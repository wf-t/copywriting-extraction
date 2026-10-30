from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


CDK_TIERS = (5, 10, 15, 20)
USER_SESSION_SECONDS = 7 * 24 * 60 * 60
ADMIN_SESSION_SECONDS = 8 * 60 * 60


def _now() -> int:
    return int(time.time())


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_code(code: str) -> str:
    return "".join((code or "").upper().split())


class CommercialStore:
    """Persistent CDK quotas and settings, without user content or media metadata."""

    def __init__(self, root: str | Path, *, database_path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.path = Path(database_path) if database_path else self.root / "data" / "commercial.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 20000")
        return connection

    def _initialize(self) -> None:
        with self._init_lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cdks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    duration_limit_minutes INTEGER NOT NULL CHECK (duration_limit_minutes IN (5, 10, 15, 20)),
                    total_uses INTEGER NOT NULL CHECK (total_uses > 0),
                    used_uses INTEGER NOT NULL DEFAULT 0 CHECK (used_uses >= 0),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_used_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_cdks_duration_limit ON cdks(duration_limit_minutes);
                CREATE INDEX IF NOT EXISTS idx_cdks_total_uses ON cdks(total_uses);
                CREATE INDEX IF NOT EXISTS idx_cdks_enabled ON cdks(enabled);

                CREATE TABLE IF NOT EXISTS cdk_sessions (
                    token_hash TEXT PRIMARY KEY,
                    cdk_id INTEGER NOT NULL REFERENCES cdks(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cdk_sessions_cdk_id ON cdk_sessions(cdk_id);
                CREATE INDEX IF NOT EXISTS idx_cdk_sessions_expires_at ON cdk_sessions(expires_at);

                CREATE TABLE IF NOT EXISTS cdk_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cdk_id INTEGER NOT NULL REFERENCES cdks(id) ON DELETE CASCADE,
                    duration_seconds REAL NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cdk_usage_cdk_id ON cdk_usage(cdk_id);

                CREATE TABLE IF NOT EXISTS commercial_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            db.execute("PRAGMA optimize")
            now = _now()
            db.execute(
                "INSERT OR IGNORE INTO commercial_settings(key, value, updated_at) VALUES ('asr_provider', 'local', ?)",
                (now,),
            )

    @staticmethod
    def _public_cdk(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        total = int(row["total_uses"])
        used = int(row["used_uses"])
        return {
            "id": int(row["id"]),
            "code": str(row["code"]),
            "duration_limit_minutes": int(row["duration_limit_minutes"]),
            "total_uses": total,
            "used_uses": used,
            "remaining_uses": max(0, total - used),
            "enabled": bool(row["enabled"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "last_used_at": int(row["last_used_at"]) if row["last_used_at"] else None,
        }

    def create_cdks(self, *, duration_limit_minutes: int, total_uses: int, quantity: int = 1) -> list[dict[str, Any]]:
        if duration_limit_minutes not in CDK_TIERS:
            raise ValueError("CDK 时长档次只能是 5、10、15 或 20 分钟")
        if total_uses < 1 or total_uses > 100000:
            raise ValueError("使用次数必须在 1 到 100000 之间")
        if quantity < 1 or quantity > 100:
            raise ValueError("单次生成数量必须在 1 到 100 之间")

        now = _now()
        created: list[dict[str, Any]] = []
        with self._connect() as db:
            for _ in range(quantity):
                while True:
                    parts = [secrets.token_hex(2).upper() for _ in range(3)]
                    code = f"TX-{duration_limit_minutes:02d}M-{'-'.join(parts)}"
                    try:
                        cursor = db.execute(
                            """
                            INSERT INTO cdks(code, duration_limit_minutes, total_uses, used_uses, enabled, created_at, updated_at)
                            VALUES (?, ?, ?, 0, 1, ?, ?)
                            """,
                            (code, duration_limit_minutes, total_uses, now, now),
                        )
                        row = db.execute("SELECT * FROM cdks WHERE id = ?", (cursor.lastrowid,)).fetchone()
                        created.append(self._public_cdk(row) or {})
                        break
                    except sqlite3.IntegrityError:
                        continue
        return created

    @staticmethod
    def _cdk_filter_sql(
        *,
        query: str = "",
        status: str = "all",
        duration_limit_minutes: int | None = None,
        total_uses: int | None = None,
    ) -> tuple[str, list[Any]]:
        normalized = _normalize_code(query)
        if status not in {"all", "active", "disabled", "exhausted"}:
            raise ValueError("兑换码状态筛选条件无效")
        if duration_limit_minutes is not None and duration_limit_minutes not in CDK_TIERS:
            raise ValueError("CDK 时长档次只能是 5、10、15 或 20 分钟")
        if total_uses is not None and total_uses < 1:
            raise ValueError("使用次数必须大于 0")

        clauses: list[str] = []
        params: list[Any] = []
        if normalized:
            clauses.append("code LIKE ?")
            params.append(f"%{normalized}%")
        if status == "active":
            clauses.append("enabled = 1 AND used_uses < total_uses")
        elif status == "disabled":
            clauses.append("enabled = 0")
        elif status == "exhausted":
            clauses.append("enabled = 1 AND used_uses >= total_uses")
        if duration_limit_minutes is not None:
            clauses.append("duration_limit_minutes = ?")
            params.append(duration_limit_minutes)
        if total_uses is not None:
            clauses.append("total_uses = ?")
            params.append(total_uses)
        return (f" WHERE {' AND '.join(f'({clause})' for clause in clauses)}" if clauses else ""), params

    def list_cdks(
        self,
        *,
        query: str = "",
        status: str = "all",
        duration_limit_minutes: int | None = None,
        total_uses: int | None = None,
        limit: int | None = 200,
    ) -> list[dict[str, Any]]:
        where_sql, params = self._cdk_filter_sql(
            query=query,
            status=status,
            duration_limit_minutes=duration_limit_minutes,
            total_uses=total_uses,
        )
        sql = f"SELECT * FROM cdks{where_sql} ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, min(limit, 500)))
        with self._connect() as db:
            return [self._public_cdk(row) or {} for row in db.execute(sql, params).fetchall()]

    def get_cdk(self, cdk_id: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cdks WHERE id = ?", (cdk_id,)).fetchone()
        cdk = self._public_cdk(row)
        if not cdk:
            raise KeyError(cdk_id)
        return cdk

    def export_cdk_codes(
        self,
        *,
        query: str = "",
        status: str = "all",
        duration_limit_minutes: int | None = None,
        total_uses: int | None = None,
    ) -> list[str]:
        return [
            str(cdk["code"])
            for cdk in self.list_cdks(
                query=query,
                status=status,
                duration_limit_minutes=duration_limit_minutes,
                total_uses=total_uses,
                limit=None,
            )
        ]

    def count_cdks(
        self,
        *,
        query: str = "",
        status: str = "all",
        duration_limit_minutes: int | None = None,
        total_uses: int | None = None,
    ) -> int:
        where_sql, params = self._cdk_filter_sql(
            query=query,
            status=status,
            duration_limit_minutes=duration_limit_minutes,
            total_uses=total_uses,
        )
        with self._connect() as db:
            row = db.execute(f"SELECT COUNT(*) AS count FROM cdks{where_sql}", params).fetchone()
        return int(row["count"])

    def delete_cdks(
        self,
        *,
        query: str = "",
        status: str = "all",
        duration_limit_minutes: int | None = None,
        total_uses: int | None = None,
        expected_count: int,
    ) -> list[int]:
        if expected_count < 1:
            raise ValueError("没有可删除的兑换码")
        where_sql, params = self._cdk_filter_sql(
            query=query,
            status=status,
            duration_limit_minutes=duration_limit_minutes,
            total_uses=total_uses,
        )
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(f"SELECT id FROM cdks{where_sql}", params).fetchall()
            cdk_ids = [int(row["id"]) for row in rows]
            actual_count = len(cdk_ids)
            if actual_count != expected_count:
                raise RuntimeError(f"匹配数量已从 {expected_count} 个变为 {actual_count} 个，请重新查询后再删除")
            db.execute(f"DELETE FROM cdks{where_sql}", params)
            db.commit()
            return cdk_ids
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def set_cdk_enabled(self, cdk_id: int, enabled: bool) -> dict[str, Any]:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE cdks SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), _now(), cdk_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(cdk_id)
            row = db.execute("SELECT * FROM cdks WHERE id = ?", (cdk_id,)).fetchone()
            if not enabled:
                db.execute("DELETE FROM cdk_sessions WHERE cdk_id = ?", (cdk_id,))
            return self._public_cdk(row) or {}

    def create_user_session(self, code: str) -> tuple[str, dict[str, Any]]:
        normalized = _normalize_code(code)
        if not normalized:
            raise ValueError("请输入兑换码")
        now = _now()
        token = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("DELETE FROM cdk_sessions WHERE expires_at <= ?", (now,))
            row = db.execute("SELECT * FROM cdks WHERE code = ?", (normalized,)).fetchone()
            cdk = self._public_cdk(row)
            if cdk is None or not cdk["enabled"]:
                raise PermissionError("兑换码无效或已停用")
            db.execute(
                "INSERT INTO cdk_sessions(token_hash, cdk_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_token_hash(token), cdk["id"], now, now + USER_SESSION_SECONDS),
            )
        return token, cdk

    def get_user_session(
        self,
        token: str,
        *,
        allow_exhausted: bool = True,
        allow_disabled: bool = False,
    ) -> dict[str, Any] | None:
        if not token:
            return None
        now = _now()
        with self._connect() as db:
            row = db.execute(
                """
                SELECT c.* FROM cdk_sessions s
                JOIN cdks c ON c.id = s.cdk_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                """,
                (_token_hash(token), now),
            ).fetchone()
        cdk = self._public_cdk(row)
        if not cdk:
            return None
        if not allow_disabled and not cdk["enabled"]:
            return None
        if not allow_exhausted and cdk["remaining_uses"] <= 0:
            return None
        return cdk

    def delete_user_session(self, token: str) -> None:
        if not token:
            return
        with self._connect() as db:
            db.execute("DELETE FROM cdk_sessions WHERE token_hash = ?", (_token_hash(token),))

    def consume(self, cdk_id: int, *, duration_seconds: float) -> dict[str, Any]:
        if duration_seconds <= 0:
            raise ValueError("无法获取视频时长，未扣除次数，请更换视频后重试")
        now = _now()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM cdks WHERE id = ?", (cdk_id,)).fetchone()
            cdk = self._public_cdk(row)
            if not cdk or not cdk["enabled"]:
                raise PermissionError("兑换码无效或已停用")
            limit_seconds = cdk["duration_limit_minutes"] * 60
            if duration_seconds > limit_seconds:
                raise ValueError(
                    f"视频时长为 {duration_seconds / 60:.1f} 分钟，超过该兑换码的 {cdk['duration_limit_minutes']} 分钟档次，未扣除次数"
                )
            cursor = db.execute(
                """
                UPDATE cdks
                SET used_uses = used_uses + 1, updated_at = ?, last_used_at = ?
                WHERE id = ? AND enabled = 1 AND used_uses < total_uses
                """,
                (now, now, cdk_id),
            )
            if cursor.rowcount != 1:
                raise PermissionError("该兑换码的使用次数已用完")
            db.execute(
                "INSERT INTO cdk_usage(cdk_id, duration_seconds, created_at) VALUES (?, ?, ?)",
                (cdk_id, round(float(duration_seconds), 3), now),
            )
            updated_row = db.execute("SELECT * FROM cdks WHERE id = ?", (cdk_id,)).fetchone()
            db.commit()
            return self._public_cdk(updated_row) or {}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_settings(self) -> dict[str, str]:
        with self._connect() as db:
            rows = db.execute("SELECT key, value FROM commercial_settings").fetchall()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        return {"asr_provider": values.get("asr_provider", "local")}

    def update_settings(self, *, asr_provider: str) -> dict[str, str]:
        if asr_provider not in {"local", "dashscope"}:
            raise ValueError("语音识别方式只能选择本地 Whisper 或阿里云 Fun-ASR")
        now = _now()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO commercial_settings(key, value, updated_at) VALUES ('asr_provider', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (asr_provider, now),
            )
        return self.get_settings()

    def statistics(self) -> dict[str, int | float]:
        local_now = datetime.now().astimezone()
        today_start = int(local_now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        with self._connect() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total_codes,
                       COALESCE(SUM(CASE WHEN enabled = 1 AND used_uses < total_uses THEN 1 ELSE 0 END), 0) AS active_codes,
                       COALESCE(SUM(total_uses), 0) AS total_uses,
                       COALESCE(SUM(used_uses), 0) AS used_uses
                FROM cdks
                """
            ).fetchone()
            today = db.execute(
                """
                SELECT COUNT(*) AS today_uses,
                       COALESCE(SUM(duration_seconds), 0) AS today_duration_seconds
                FROM cdk_usage
                WHERE created_at >= ?
                """,
                (today_start,),
            ).fetchone()
        return {
            **{key: int(row[key]) for key in ("total_codes", "active_codes", "total_uses", "used_uses")},
            "today_uses": int(today["today_uses"]),
            "today_duration_seconds": round(float(today["today_duration_seconds"]), 1),
        }


class AdminSessionManager:
    """One configured administrator with short-lived, in-memory browser sessions."""

    def __init__(self):
        self.username = os.environ.get("CDK_ADMIN_USERNAME", "admin")
        self.password = os.environ.get("CDK_ADMIN_PASSWORD", "ChangeMe-8765")
        self.using_default_password = "CDK_ADMIN_PASSWORD" not in os.environ
        self._sessions: dict[str, int] = {}
        self._lock = threading.Lock()

    def login(self, username: str, password: str) -> str:
        valid_user = hmac.compare_digest(username.encode("utf-8"), self.username.encode("utf-8"))
        valid_password = hmac.compare_digest(password.encode("utf-8"), self.password.encode("utf-8"))
        if not (valid_user and valid_password):
            raise PermissionError("管理员账号或密码错误")
        token = secrets.token_urlsafe(36)
        now = _now()
        with self._lock:
            self._sessions = {key: expiry for key, expiry in self._sessions.items() if expiry > now}
            self._sessions[_token_hash(token)] = now + ADMIN_SESSION_SECONDS
        return token

    def valid(self, token: str) -> bool:
        if not token:
            return False
        now = _now()
        key = _token_hash(token)
        with self._lock:
            expiry = self._sessions.get(key, 0)
            if expiry <= now:
                self._sessions.pop(key, None)
                return False
            return True

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(_token_hash(token), None)
