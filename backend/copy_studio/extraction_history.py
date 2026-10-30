from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any


SEVEN_DAYS_SECONDS = 7 * 24 * 60 * 60
DAILY_CLEANUP_SECONDS = 24 * 60 * 60


class ExtractionHistoryStore:
    """Seven-day JSON history for administrator lookup only.

    The JSON body intentionally contains only the source video URL and the final
    organized copy. File modification time is the retention clock, so no extra
    user metadata is written into the record.
    """

    def __init__(self, root: str | Path, *, retention_seconds: int = SEVEN_DAYS_SECONDS):
        self.root = Path(root).resolve()
        self.path = self.root / "data" / "cdk-history"
        self.path.mkdir(parents=True, exist_ok=True)
        self.retention_seconds = retention_seconds
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _cdk_dir(self, cdk_id: int) -> Path:
        if not isinstance(cdk_id, int) or cdk_id < 1:
            raise ValueError("CDK ID 无效")
        return self.path / str(cdk_id)

    def save(self, cdk_id: int, *, video_url: str, final_text: str) -> None:
        video_url = (video_url or "").strip()
        final_text = (final_text or "").strip()
        parsed = urllib.parse.urlparse(video_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("视频链接无效")
        if not final_text:
            raise ValueError("最终文案为空")

        payload = {"video_url": video_url, "final_text": final_text}
        with self._lock:
            target_dir = self._cdk_dir(cdk_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            record_id = f"hx_{time.time_ns()}_{uuid.uuid4().hex}.json"
            target = target_dir / record_id
            temporary = target.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

    def list_for_cdk(self, cdk_id: int) -> list[dict[str, str]]:
        self.cleanup_expired()
        target_dir = self._cdk_dir(cdk_id)
        if not target_dir.exists():
            return []
        records: list[dict[str, str]] = []
        with self._lock:
            paths = sorted(target_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
            for path in paths:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    video_url = str(payload.get("video_url") or "").strip()
                    final_text = str(payload.get("final_text") or "").strip()
                except (OSError, json.JSONDecodeError):
                    continue
                if video_url and final_text:
                    records.append({"video_url": video_url, "final_text": final_text})
        return records

    def count_for_cdk(self, cdk_id: int) -> int:
        return self.counts_for_cdks([cdk_id]).get(cdk_id, 0)

    def counts_for_cdks(self, cdk_ids: list[int]) -> dict[int, int]:
        self.cleanup_expired()
        counts: dict[int, int] = {}
        with self._lock:
            for cdk_id in cdk_ids:
                target_dir = self._cdk_dir(cdk_id)
                if not target_dir.exists():
                    continue
                count = sum(1 for path in target_dir.glob("*.json") if path.is_file())
                if count:
                    counts[cdk_id] = count
        return counts

    def clear_cdk(self, cdk_id: int) -> None:
        target_dir = self._cdk_dir(cdk_id)
        with self._lock:
            if target_dir.exists():
                shutil.rmtree(target_dir)

    def clear_cdks(self, cdk_ids: list[int]) -> None:
        for cdk_id in cdk_ids:
            self.clear_cdk(cdk_id)

    def cleanup_expired(self, *, now: float | None = None) -> int:
        cutoff = (now if now is not None else time.time()) - self.retention_seconds
        deleted = 0
        with self._lock:
            for cdk_dir in self.path.iterdir():
                if not cdk_dir.is_dir() or not cdk_dir.name.isdigit():
                    continue
                for path in cdk_dir.iterdir():
                    if not path.is_file() or path.suffix not in {".json", ".tmp"}:
                        continue
                    try:
                        expired = path.stat().st_mtime < cutoff
                    except FileNotFoundError:
                        continue
                    if expired:
                        path.unlink(missing_ok=True)
                        deleted += 1
                try:
                    cdk_dir.rmdir()
                except OSError:
                    pass
        return deleted

    def start_cleanup_scheduler(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self.cleanup_expired()
            self._stop.clear()
            self._thread = threading.Thread(target=self._cleanup_loop, name="cdk-history-cleanup", daemon=True)
            self._thread.start()

    def stop_cleanup_scheduler(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)

    def _cleanup_loop(self) -> None:
        while not self._stop.wait(DAILY_CLEANUP_SECONDS):
            self.cleanup_expired()
