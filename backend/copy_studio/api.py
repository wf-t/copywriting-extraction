from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .commercial import AdminSessionManager, CDK_TIERS, CommercialStore
from .extraction_history import ExtractionHistoryStore
from .transcript_extractor import TranscriptTaskManager

ROOT_DIR = Path(os.environ.get("COPY_STUDIO_ROOT", Path(__file__).resolve().parents[2])).resolve()
commercial_store = CommercialStore(ROOT_DIR)
admin_sessions = AdminSessionManager()
history_store = ExtractionHistoryStore(ROOT_DIR)
transcript_tasks = TranscriptTaskManager(
    ROOT_DIR,
    settings_provider=commercial_store.get_settings,
    history_writer=history_store.save,
)

USER_COOKIE = "copy_studio_cdk"
ADMIN_COOKIE = "copy_studio_admin"


@asynccontextmanager
async def lifespan(app: FastAPI):
    history_store.start_cleanup_scheduler()
    try:
        yield
    finally:
        history_store.stop_cleanup_scheduler()
        transcript_tasks.shutdown(wait=False)


app = FastAPI(title="Copy Studio", version="0.1.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/copy-studio")


class TranscriptExtractCreate(BaseModel):
    share_text: str = Field(min_length=5, max_length=5000)
    platform: Literal["douyin", "kuaishou", "xiaohongshu", "bilibili"] = "douyin"


class CDKAccessCreate(BaseModel):
    code: str = Field(min_length=4, max_length=100)


class AdminLoginCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class CDKCreate(BaseModel):
    duration_limit_minutes: Literal[5, 10, 15, 20]
    total_uses: int = Field(ge=1, le=100000)
    quantity: int = Field(default=1, ge=1, le=100)


class CDKStatusUpdate(BaseModel):
    enabled: bool


class CommercialSettingsUpdate(BaseModel):
    asr_provider: Literal["local", "dashscope"]


def _cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes", "on"}


def _require_user(
    request: Request,
    *,
    allow_exhausted: bool = True,
    allow_disabled: bool = False,
) -> dict[str, Any]:
    cdk = commercial_store.get_user_session(
        request.cookies.get(USER_COOKIE, ""),
        allow_exhausted=allow_exhausted,
        allow_disabled=allow_disabled,
    )
    if not cdk:
        raise HTTPException(401, "请先输入有效兑换码")
    return cdk


def _require_admin(request: Request) -> None:
    if not admin_sessions.valid(request.cookies.get(ADMIN_COOKIE, "")):
        raise HTTPException(401, "管理员登录已失效，请重新登录")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "copy-studio"}


@app.get("/api/commercial/access")
def commercial_access(request: Request, response: Response) -> dict[str, Any]:
    cdk = commercial_store.get_user_session(request.cookies.get(USER_COOKIE, ""))
    if not cdk:
        response.delete_cookie(USER_COOKIE, path="/")
        return {"authorized": False}
    return {"authorized": True, "cdk": cdk}


@app.post("/api/commercial/access")
def activate_commercial_access(req: CDKAccessCreate, response: Response) -> dict[str, Any]:
    try:
        token, cdk = commercial_store.create_user_session(req.code)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(403, str(exc)) from exc
    response.set_cookie(
        USER_COOKIE,
        token,
        max_age=7 * 24 * 60 * 60,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        path="/",
    )
    return {"authorized": True, "cdk": cdk}


@app.delete("/api/commercial/access")
def deactivate_commercial_access(request: Request, response: Response) -> dict[str, bool]:
    commercial_store.delete_user_session(request.cookies.get(USER_COOKIE, ""))
    response.delete_cookie(USER_COOKIE, path="/")
    return {"logged_out": True}


@app.post("/api/commercial/admin/login")
def commercial_admin_login(req: AdminLoginCreate, response: Response) -> dict[str, Any]:
    try:
        token = admin_sessions.login(req.username, req.password)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=8 * 60 * 60,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        path="/",
    )
    return {"authorized": True, "using_default_password": admin_sessions.using_default_password}


@app.get("/api/commercial/admin/session")
def commercial_admin_session(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return {"authorized": True, "using_default_password": admin_sessions.using_default_password}


@app.delete("/api/commercial/admin/session")
def commercial_admin_logout(request: Request, response: Response) -> dict[str, bool]:
    admin_sessions.logout(request.cookies.get(ADMIN_COOKIE, ""))
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"logged_out": True}


@app.get("/api/commercial/admin/dashboard")
def commercial_admin_dashboard(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return {"statistics": commercial_store.statistics()}


@app.get("/api/commercial/admin/cdks")
def commercial_admin_list_cdks(
    request: Request,
    q: str = Query("", max_length=100),
    status: Literal["all", "active", "disabled", "exhausted"] = "all",
    duration_limit_minutes: Optional[int] = Query(None),
    total_uses: Optional[int] = Query(None, ge=1, le=100000),
) -> dict[str, Any]:
    _require_admin(request)
    try:
        cdks = commercial_store.list_cdks(
            query=q,
            status=status,
            duration_limit_minutes=duration_limit_minutes,
            total_uses=total_uses,
        )
        history_counts = history_store.counts_for_cdks([cdk["id"] for cdk in cdks])
        for cdk in cdks:
            history_count = history_counts.get(cdk["id"], 0)
            if history_count:
                cdk["history_count"] = history_count
        return {
            "cdks": cdks,
            "matched_count": commercial_store.count_cdks(
                query=q,
                status=status,
                duration_limit_minutes=duration_limit_minutes,
                total_uses=total_uses,
            ),
            "tiers": list(CDK_TIERS),
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/commercial/admin/cdks/export")
def commercial_admin_export_cdks(
    request: Request,
    q: str = Query("", max_length=100),
    status: Literal["all", "active", "disabled", "exhausted"] = "all",
    duration_limit_minutes: Optional[int] = Query(None),
    total_uses: Optional[int] = Query(None, ge=1, le=100000),
) -> PlainTextResponse:
    _require_admin(request)
    try:
        codes = commercial_store.export_cdk_codes(
            query=q,
            status=status,
            duration_limit_minutes=duration_limit_minutes,
            total_uses=total_uses,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not codes:
        raise HTTPException(400, "当前查询条件没有可导出的兑换码")
    filename = f"cdks-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    return PlainTextResponse(
        "\n".join(codes) + "\n",
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/commercial/admin/cdks/{cdk_id}/history")
def commercial_admin_cdk_history(cdk_id: int, request: Request) -> dict[str, Any]:
    _require_admin(request)
    try:
        commercial_store.get_cdk(cdk_id)
    except KeyError as exc:
        raise HTTPException(404, "兑换码不存在") from exc
    return {"records": history_store.list_for_cdk(cdk_id)}


@app.delete("/api/commercial/admin/cdks")
def commercial_admin_delete_cdks(
    request: Request,
    expected_count: int = Query(..., ge=1),
    q: str = Query("", max_length=100),
    status: Literal["all", "active", "disabled", "exhausted"] = "all",
    duration_limit_minutes: Optional[int] = Query(None),
    total_uses: Optional[int] = Query(None, ge=1, le=100000),
) -> dict[str, Any]:
    _require_admin(request)
    try:
        deleted_ids = commercial_store.delete_cdks(
            query=q,
            status=status,
            duration_limit_minutes=duration_limit_minutes,
            total_uses=total_uses,
            expected_count=expected_count,
        )
        history_store.clear_cdks(deleted_ids)
        return {"deleted": len(deleted_ids), "statistics": commercial_store.statistics()}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/commercial/admin/cdks")
def commercial_admin_create_cdks(request: Request, req: CDKCreate) -> dict[str, Any]:
    _require_admin(request)
    try:
        cdks = commercial_store.create_cdks(
            duration_limit_minutes=req.duration_limit_minutes,
            total_uses=req.total_uses,
            quantity=req.quantity,
        )
        return {"cdks": cdks, "statistics": commercial_store.statistics()}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/api/commercial/admin/cdks/{cdk_id}")
def commercial_admin_update_cdk(cdk_id: int, req: CDKStatusUpdate, request: Request) -> dict[str, Any]:
    _require_admin(request)
    try:
        return commercial_store.set_cdk_enabled(cdk_id, req.enabled)
    except KeyError as exc:
        raise HTTPException(404, "兑换码不存在") from exc


@app.get("/api/commercial/admin/settings")
def commercial_admin_get_settings(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return {
        "settings": commercial_store.get_settings(),
        "capabilities": transcript_tasks.capabilities(),
        "key_source": "environment" if os.environ.get("DASHSCOPE_API_KEY") else "model_config",
    }


@app.put("/api/commercial/admin/settings")
def commercial_admin_update_settings(req: CommercialSettingsUpdate, request: Request) -> dict[str, Any]:
    _require_admin(request)
    try:
        settings = commercial_store.update_settings(asr_provider=req.asr_provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"settings": settings, "capabilities": transcript_tasks.capabilities()}


@app.get("/api/transcript-extractor/health")
def transcript_extractor_health(request: Request) -> dict[str, Any]:
    _require_user(request)
    return transcript_tasks.capabilities()


@app.post("/api/transcript-extractor/info")
def transcript_extractor_info(req: TranscriptExtractCreate, request: Request) -> dict[str, Any]:
    _require_user(request)
    try:
        return transcript_tasks.resolve_info(req.share_text, platform=req.platform)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/transcript-extractor/tasks")
def create_transcript_task(req: TranscriptExtractCreate, request: Request) -> dict[str, Any]:
    cdk = _require_user(request)
    try:
        video_info = transcript_tasks.prepare_video(req.share_text, platform=req.platform)
        updated_cdk = commercial_store.consume(cdk["id"], duration_seconds=float(video_info.get("duration") or 0))
        task = transcript_tasks.create_prepared(
            video_info,
            platform=req.platform,
            owner_id=cdk["id"],
            request_base_url=str(request.base_url).rstrip("/"),
        )
        task["quota"] = {
            "duration_limit_minutes": updated_cdk["duration_limit_minutes"],
            "remaining_uses": updated_cdk["remaining_uses"],
        }
        return task
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/transcript-extractor/tasks/{task_id}")
def get_transcript_task(task_id: str, request: Request) -> dict[str, Any]:
    cdk = _require_user(request, allow_exhausted=True)
    try:
        return transcript_tasks.get(task_id, owner_id=cdk["id"])
    except KeyError as exc:
        raise HTTPException(404, "临时任务不存在或已经释放") from exc


@app.delete("/api/transcript-extractor/tasks/{task_id}")
def forget_transcript_task(task_id: str, request: Request) -> dict[str, bool]:
    cdk = _require_user(request, allow_exhausted=True)
    return {"deleted": transcript_tasks.forget(task_id, owner_id=cdk["id"])}


FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


@app.get("/copy-studio", include_in_schema=False)
@app.get("/copy-studio/", include_in_schema=False)
def transcript_extractor_page() -> FileResponse:
    page = FRONTEND_DIST / "transcript.html"
    if not page.exists():
        raise HTTPException(503, "文案提取界面尚未构建")
    return FileResponse(page)


@app.get("/copy-studio/records", include_in_schema=False)
@app.get("/copy-studio/records/", include_in_schema=False)
def transcript_extractor_records_page() -> FileResponse:
    page = FRONTEND_DIST / "transcript-records.html"
    if not page.exists():
        raise HTTPException(503, "提取记录界面尚未构建")
    return FileResponse(page)


@app.get("/copy-studio/ft", include_in_schema=False)
@app.get("/copy-studio/ft/", include_in_schema=False)
def transcript_extractor_admin_page() -> FileResponse:
    page = FRONTEND_DIST / "transcript-admin.html"
    if not page.exists():
        raise HTTPException(503, "CDK 管理界面尚未构建")
    return FileResponse(page)


app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False),
    name="frontend-assets",
)
