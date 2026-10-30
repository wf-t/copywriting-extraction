from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..model_config import ModelConfigStore
from ..model_runtime import call_openai_compatible_chat, resolve_model
from .platforms import extract_first_url, get_platform_extractor, parse_douyin_share

STAGES = {
    "queued": ("等待处理", 5),
    "resolving": ("正在解析链接", 15),
    "downloading": ("正在获取视频", 32),
    "extracting_audio": ("正在提取音频", 50),
    "transcribing": ("正在识别语音", 65),
    "organizing": ("DeepSeek 正在语义整理", 84),
    "cleaning": ("正在清理临时文件", 96),
    "completed": ("提取完成", 100),
    "failed": ("提取失败", 100),
}


def _now() -> float:
    return time.time()


def _task_id() -> str:
    return f"tx_{uuid.uuid4().hex}"


def _worker_count(name: str, default: int, maximum: int) -> int:
    raw_value = str(os.environ.get(name, default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if value < 1 or value > maximum:
        raise ValueError(f"{name} 必须在 1 到 {maximum} 之间")
    return value


@dataclass
class TaskState:
    id: str
    platform: str
    asr_provider: str = "local"
    owner_id: int = 0
    status: str = "queued"
    stage: str = "queued"
    stage_label: str = "等待处理"
    progress: int = 5
    result: dict[str, Any] | None = None
    error: str = ""
    created_at: float = field(default_factory=_now)
    touched_at: float = field(default_factory=_now)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("created_at", None)
        data.pop("touched_at", None)
        data.pop("owner_id", None)
        return data


@dataclass(frozen=True)
class TranscriptJob:
    """An immutable ownership envelope passed to exactly one ASR queue."""

    task_id: str
    owner_id: int
    platform: str
    asr_provider: str
    video_info: dict[str, Any]
    request_base_url: str = ""


class TranscriptTaskManager:
    """Provider-isolated, memory-only transcription queues.

    Task metadata and results never touch SQLite. Each job uses an operating-system
    temporary directory that is removed even if parsing or transcription fails.
    Local Whisper and Fun-ASR have separate executors so switching providers cannot
    move an already queued job to another recognition path.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        ttl_seconds: int = 1800,
        settings_provider: Callable[[], dict[str, str]] | None = None,
        history_writer: Callable[..., None] | None = None,
    ):
        self.root = Path(root).resolve()
        self.model_config_store = ModelConfigStore(self.root)
        self.ttl_seconds = ttl_seconds
        self.settings_provider = settings_provider or (lambda: {"asr_provider": "local"})
        self.history_writer = history_writer
        self._tasks: dict[str, TaskState] = {}
        self._lock = threading.RLock()
        self.local_workers = _worker_count("TRANSCRIPT_LOCAL_WORKERS", 1, 4)
        self.fun_asr_workers = _worker_count("TRANSCRIPT_FUN_ASR_WORKERS", 5, 32)
        self._executors = {
            "local": ThreadPoolExecutor(
                max_workers=self.local_workers,
                thread_name_prefix="transcript-local",
            ),
            "dashscope": ThreadPoolExecutor(
                max_workers=self.fun_asr_workers,
                thread_name_prefix="transcript-fun-asr",
            ),
        }

    def capabilities(self) -> dict[str, Any]:
        whisper = self._whisper_command()
        config = self.model_config_store.load()
        settings = self.settings_provider()
        deepseek_config = (config.get("api_providers") or {}).get("deepseek") or {}
        deepseek_ready = bool(str(deepseek_config.get("api_key") or "").strip())
        dashscope_config = (config.get("api_providers") or {}).get("dashscope") or {}
        dashscope_ready = bool(os.environ.get("DASHSCOPE_API_KEY") or str(dashscope_config.get("api_key") or "").strip())
        asr_provider = settings.get("asr_provider") or "local"
        local_ready = bool(shutil.which("ffmpeg") and whisper)
        provider_ready = local_ready if asr_provider == "local" else dashscope_ready
        return {
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "whisper": bool(whisper),
            "model": "large-v3-turbo",
            "ready": provider_ready,
            "local_ready": local_ready,
            "asr_provider": asr_provider,
            "asr_label": "本地 Whisper" if asr_provider == "local" else "阿里云 Fun-ASR",
            "dashscope_ready": dashscope_ready,
            "semantic_model": str((config.get("models") or {}).get("llm") or "deepseek-chat"),
            "deepseek_ready": deepseek_ready,
            "concurrency": {
                "local": self.local_workers,
                "dashscope": self.fun_asr_workers,
                "active": self.local_workers if asr_provider == "local" else self.fun_asr_workers,
            },
            "persistence": False,
        }

    def shutdown(self, *, wait: bool = True) -> None:
        for executor in self._executors.values():
            executor.shutdown(wait=wait, cancel_futures=not wait)

    def _current_asr_provider(self) -> str:
        provider = str(self.settings_provider().get("asr_provider") or "local").strip().lower()
        if provider not in self._executors:
            raise ValueError(f"不支持的语音识别方式：{provider}")
        return provider

    def prepare_video(self, share_text: str, *, platform: str = "douyin") -> dict[str, Any]:
        return get_platform_extractor(platform).parse(share_text).as_dict()

    def create_prepared(
        self,
        video_info: dict[str, Any],
        *,
        platform: str = "douyin",
        owner_id: int = 0,
        request_base_url: str = "",
    ) -> dict[str, Any]:
        get_platform_extractor(platform)
        if video_info.get("platform") != platform:
            raise ValueError("视频平台与解析结果不一致")
        if not video_info.get("media_url"):
            raise ValueError("视频信息不完整，无法创建识别任务")
        asr_provider = self._current_asr_provider()
        self._remove_expired()
        task = TaskState(
            id=_task_id(),
            platform=platform,
            asr_provider=asr_provider,
            owner_id=owner_id,
        )
        job = TranscriptJob(
            task_id=task.id,
            owner_id=owner_id,
            platform=platform,
            asr_provider=asr_provider,
            video_info=copy.deepcopy(video_info),
            request_base_url=request_base_url,
        )
        with self._lock:
            self._tasks[task.id] = task
        self._executors[asr_provider].submit(self._run_prepared, job)
        return task.public()

    def create(
        self,
        share_text: str,
        *,
        platform: str = "douyin",
        owner_id: int = 0,
        request_base_url: str = "",
    ) -> dict[str, Any]:
        video_info = self.prepare_video(share_text, platform=platform)
        return self.create_prepared(
            video_info,
            platform=platform,
            owner_id=owner_id,
            request_base_url=request_base_url,
        )

    def get(self, task_id: str, *, owner_id: int | None = None) -> dict[str, Any]:
        self._remove_expired()
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            if owner_id is not None and task.owner_id != owner_id:
                raise KeyError(task_id)
            task.touched_at = _now()
            return task.public()

    def forget(self, task_id: str, *, owner_id: int | None = None) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None and owner_id is not None and task.owner_id != owner_id:
                return False
            return self._tasks.pop(task_id, None) is not None

    def resolve_info(self, share_text: str, *, platform: str = "douyin") -> dict[str, Any]:
        info = self.prepare_video(share_text, platform=platform)
        return self._public_video_info(info)

    @staticmethod
    def _public_video_info(video_info: dict[str, Any]) -> dict[str, Any]:
        private_keys = {"media_url", "media_headers"}
        return {key: value for key, value in video_info.items() if key not in private_keys}

    def _update(self, task_id: str, stage: str, **changes: Any) -> None:
        label, progress = STAGES[stage]
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.stage = stage
            task.stage_label = label
            task.progress = progress
            task.touched_at = _now()
            for key, value in changes.items():
                setattr(task, key, value)

    def _job_matches_task(self, job: TranscriptJob) -> bool:
        with self._lock:
            task = self._tasks.get(job.task_id)
            return bool(
                task
                and task.owner_id == job.owner_id
                and task.platform == job.platform
                and task.asr_provider == job.asr_provider
            )

    def _run_prepared(self, job: TranscriptJob) -> None:
        if not self._job_matches_task(job):
            return
        task_id = job.task_id
        video_info = job.video_info
        try:
            self._update(task_id, "downloading", status="processing")
            with tempfile.TemporaryDirectory(prefix=f"{task_id}-") as temp_value:
                temp_dir = Path(temp_value)
                video_path = temp_dir / "video.mp4"
                audio_path = temp_dir / "audio.wav"
                if job.asr_provider == "dashscope":
                    # Every adapter returns a public, time-limited media URL.
                    # Alibaba fetches it directly; the server stores no media.
                    self._update(task_id, "transcribing")
                    transcript = self._transcribe_dashscope_url(str(video_info["media_url"]))
                else:
                    self._update(task_id, "downloading")
                    self._download(
                        str(video_info["media_url"]),
                        video_path,
                        headers=video_info.get("media_headers") or {},
                    )

                    self._update(task_id, "extracting_audio")
                    self._extract_audio(video_path, audio_path)

                    self._update(task_id, "transcribing")
                    transcript = self._transcribe_local(audio_path, temp_dir)

                self._update(task_id, "organizing")
                semantic_text = ""
                semantic_error = ""
                try:
                    semantic_text = self._organize_transcript(
                        transcript["text"],
                        title=str(video_info.get("title") or ""),
                        author=str(video_info.get("author") or ""),
                    )
                except Exception as exc:
                    semantic_error = str(exc)

                if (
                    semantic_text
                    and self.history_writer
                    and job.owner_id > 0
                    and self._job_matches_task(job)
                ):
                    try:
                        self.history_writer(
                            job.owner_id,
                            video_url=str(video_info.get("source_url") or ""),
                            final_text=semantic_text,
                        )
                    except Exception:
                        pass

                self._update(task_id, "cleaning")
                public_info = self._public_video_info(video_info)
                result = {
                    "video": public_info,
                    "text": transcript["text"],
                    "segments": transcript["segments"],
                    "language": transcript.get("language") or "zh",
                    "model": transcript.get("model") or "large-v3-turbo",
                    "semantic_text": semantic_text,
                    "semantic_model": str((self.model_config_store.load().get("models") or {}).get("llm") or "deepseek-chat"),
                    "semantic_error": semantic_error,
                }
            self._update(task_id, "completed", status="completed", result=result)
        except Exception as exc:
            self._update(task_id, "failed", status="failed", error=str(exc))

    def _download(self, url: str, target: Path, *, headers: dict[str, str] | None = None) -> None:
        request_headers = {str(key): str(value) for key, value in (headers or {}).items() if value}
        curl = shutil.which("curl")
        if curl:
            command = [
                curl,
                "-fL",
                "--retry", "3",
                "--retry-all-errors",
                "--connect-timeout", "20",
                "--max-time", "180",
            ]
            for key, value in request_headers.items():
                command.extend(["-H", f"{key}: {value}"])
            command.extend(["-o", str(target), url])
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"视频下载失败：{(result.stderr or '网络错误')[-500:]}")
        else:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        if not target.exists() or target.stat().st_size == 0:
            raise RuntimeError("视频下载失败：没有获得媒体内容")

    def _extract_audio(self, video_path: Path, audio_path: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("本机没有找到 FFmpeg")
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", str(audio_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not audio_path.exists():
            raise RuntimeError(f"音频提取失败：{(result.stderr or '未知错误')[-500:]}")

    def _transcribe_local(self, audio_path: Path, output_dir: Path) -> dict[str, Any]:
        whisper = self._whisper_command()
        if not whisper:
            raise RuntimeError("没有找到本地 Whisper 环境")
        model_dir = self.root / "data" / "models" / "whisper"
        result = subprocess.run(
            [
                whisper,
                str(audio_path),
                "--model", "turbo",
                "--model_dir", str(model_dir),
                "--output_dir", str(output_dir),
                "--output_format", "json",
                "--language", "zh",
                "--fp16", "False",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        transcript_path = output_dir / "audio.json"
        if result.returncode != 0 or not transcript_path.exists():
            message = (result.stderr or result.stdout or "Whisper 没有生成结果")[-1000:]
            raise RuntimeError(f"语音识别失败：{message}")
        payload = json.loads(transcript_path.read_text(encoding="utf-8"))
        segments = [
            {
                "start": round(float(item.get("start") or 0), 2),
                "end": round(float(item.get("end") or 0), 2),
                "text": str(item.get("text") or "").strip(),
            }
            for item in payload.get("segments") or []
            if str(item.get("text") or "").strip()
        ]
        return {
            "text": str(payload.get("text") or "").strip(),
            "segments": segments,
            "language": payload.get("language") or "zh",
            "model": "large-v3-turbo",
        }

    def _transcribe_dashscope_url(self, media_url: str) -> dict[str, Any]:
        media_url = (media_url or "").strip()
        parsed_media = urllib.parse.urlparse(media_url)
        if parsed_media.scheme not in {"http", "https"} or not parsed_media.netloc:
            raise RuntimeError("阿里云语音识别的媒体地址无效")

        config = self.model_config_store.load()
        provider_config = (config.get("api_providers") or {}).get("dashscope") or {}
        api_key = str(os.environ.get("DASHSCOPE_API_KEY") or provider_config.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("阿里云语音识别尚未配置 DASHSCOPE_API_KEY")

        try:
            import dashscope
            from dashscope.audio.asr import Transcription
        except ImportError as exc:
            raise RuntimeError("缺少 DashScope SDK，请先安装 backend/requirements.txt") from exc

        dashscope.api_key = api_key
        task_response = Transcription.async_call(
            model="fun-asr",
            file_urls=[media_url],
            language_hints=["zh"],
        )
        if getattr(task_response, "status_code", 0) != 200:
            message = getattr(task_response, "message", "阿里云未接受识别任务")
            raise RuntimeError(f"阿里云语音识别提交失败：{message}")
        task_id = getattr(getattr(task_response, "output", None), "task_id", "")
        if not task_id:
            raise RuntimeError("阿里云语音识别没有返回任务 ID")
        response = Transcription.wait(task=task_id)
        if getattr(response, "status_code", 0) != 200:
            message = getattr(response, "message", "异步识别失败")
            raise RuntimeError(f"阿里云语音识别失败：{message}")
        output = getattr(response, "output", None)
        results = getattr(output, "results", None) or (output.get("results") if isinstance(output, dict) else None) or []
        if not results:
            raise RuntimeError("阿里云语音识别没有返回结果文件")
        first = results[0]
        transcription_url = (
            first.get("transcription_url") if isinstance(first, dict) else getattr(first, "transcription_url", "")
        )
        if not transcription_url:
            raise RuntimeError("阿里云语音识别结果中缺少 transcription_url")
        request = urllib.request.Request(str(transcription_url), headers={"User-Agent": "Copy-Studio/1.0"})
        with urllib.request.urlopen(request, timeout=60) as result_response:
            payload = json.loads(result_response.read().decode("utf-8"))
        return self._parse_dashscope_transcript(payload)

    @staticmethod
    def _parse_dashscope_transcript(payload: dict[str, Any]) -> dict[str, Any]:
        transcripts = payload.get("transcripts") or []
        texts: list[str] = []
        segments: list[dict[str, Any]] = []
        for transcript in transcripts:
            text = str(transcript.get("text") or "").strip()
            if text:
                texts.append(text)
            for sentence in transcript.get("sentences") or []:
                sentence_text = str(sentence.get("text") or "").strip()
                if not sentence_text:
                    continue
                segments.append({
                    "start": round(float(sentence.get("begin_time") or 0) / 1000, 2),
                    "end": round(float(sentence.get("end_time") or 0) / 1000, 2),
                    "text": sentence_text,
                })
        if not texts and payload.get("text"):
            texts.append(str(payload["text"]).strip())
        if not texts and segments:
            texts.append("".join(segment["text"] for segment in segments))
        text = "\n".join(item for item in texts if item).strip()
        if not text:
            raise RuntimeError("阿里云 Fun-ASR 没有识别到语音文字")
        return {"text": text, "segments": segments, "language": "zh", "model": "fun-asr"}

    def _organize_transcript(self, transcript: str, *, title: str = "", author: str = "") -> str:
        if not transcript.strip():
            raise RuntimeError("语音识别没有得到可供整理的文字")
        config = self.model_config_store.load()
        resolved = resolve_model(config, "llm")
        system_prompt = """你是一名严谨的中文短视频文案编辑。请只依据用户提供的语音转写进行语义整理，不得补充视频中没有的信息。

任务要求：
1. 修正明显的同音错字、断句和标点，删除无意义的重复口头语，但保留全部有效信息。
2. 将内容整理成自然、易读、信息完整的中文文案；不要为了简短而丢失细节。
3. 提炼核心精华和可执行要点。没有明确操作建议时，不要强行生成建议。
4. 遇到无法确定的专业词，不要擅自替换，可以保留原词并标注“可能识别有误”。
5. 不要解释你的工作过程，直接输出 Markdown 正文。

必须使用以下结构：
## 整理后的完整文案
按语义分段的完整文案

## 核心精华
- 3至8条要点

## 可执行要点
- 视频明确提到的可执行事项；若没有则写“视频未提供明确的操作步骤”。"""
        user_prompt = (
            f"视频标题：{title or '未提供'}\n"
            f"作者：{author or '未提供'}\n\n"
            "以下是原始语音转写：\n"
            f"{transcript.strip()}"
        )
        return call_openai_compatible_chat(
            resolved=resolved,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            input_data={"temperature": 0.2},
            config=config,
        )

    def _whisper_command(self) -> str:
        configured = os.environ.get("WHISPER_COMMAND", "").strip()
        if configured:
            return shutil.which(configured) or ""
        local = self.root / ".venv" / "bin" / "whisper"
        if local.is_file() and os.access(local, os.X_OK):
            return str(local)
        return shutil.which("whisper") or ""

    def _remove_expired(self) -> None:
        cutoff = _now() - self.ttl_seconds
        with self._lock:
            expired = [
                task_id
                for task_id, task in self._tasks.items()
                if task.touched_at < cutoff and task.status in {"completed", "failed"}
            ]
            for task_id in expired:
                self._tasks.pop(task_id, None)
