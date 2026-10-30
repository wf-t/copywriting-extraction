from __future__ import annotations

import re
import urllib.parse
from typing import Any

import requests

from .base import (
    DESKTOP_USER_AGENT,
    PlatformExtractor,
    PlatformMedia,
    extract_assignment_json,
    extract_first_url,
    host_matches,
    milliseconds_to_seconds,
)


XIAOHONGSHU_DOMAINS = ("xiaohongshu.com", "xhslink.com", "xhslink.cn", "rednote.com")
XIAOHONGSHU_HEADERS = {
    "User-Agent": DESKTOP_USER_AGENT,
    "Referer": "https://www.xiaohongshu.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _get_page(shared_url: str) -> tuple[str, str]:
    try:
        response = requests.get(
            shared_url,
            headers=XIAOHONGSHU_HEADERS,
            timeout=20,
            allow_redirects=True,
        )
        response.raise_for_status()
        return str(response.url), response.text
    except requests.RequestException as exc:
        raise RuntimeError(f"小红书分享链接请求失败：{exc}") from exc


def _find_note(state: dict[str, Any], note_id: str) -> dict[str, Any]:
    note_root = state.get("note") or {}
    note_map = note_root.get("noteDetailMap") or {}
    exact = note_map.get(note_id)
    if isinstance(exact, dict) and isinstance(exact.get("note"), dict):
        return exact["note"]
    for value in note_map.values() if isinstance(note_map, dict) else []:
        if isinstance(value, dict) and isinstance(value.get("note"), dict):
            return value["note"]
    phone_note = (((state.get("noteData") or {}).get("data") or {}).get("noteData") or {})
    return phone_note if isinstance(phone_note, dict) else {}


def _candidate_streams(video: dict[str, Any]) -> list[dict[str, Any]]:
    stream = ((video.get("media") or {}).get("stream") or {})
    items: list[dict[str, Any]] = []
    if isinstance(stream, dict):
        for codec in ("h264", "avc", "hevc", "h265"):
            values = stream.get(codec) or []
            if isinstance(values, list):
                items.extend(value for value in values if isinstance(value, dict))
        for key, values in stream.items():
            if key in {"h264", "avc", "hevc", "h265"} or not isinstance(values, list):
                continue
            items.extend(value for value in values if isinstance(value, dict))
    return items


def _pick_video(video: dict[str, Any]) -> tuple[str, float]:
    items = _candidate_streams(video)
    items.sort(
        key=lambda item: (
            float(item.get("videoBitrate") or item.get("avgBitrate") or 0),
            float(item.get("width") or 0) * float(item.get("height") or 0),
        ),
        reverse=True,
    )
    for item in items:
        candidates = [item.get("masterUrl"), item.get("master_url")]
        candidates.extend(item.get("backupUrls") or item.get("backup_urls") or [])
        media_url = next((str(url) for url in candidates if isinstance(url, str) and url.startswith("http")), "")
        if media_url:
            return media_url, milliseconds_to_seconds(item.get("duration"))

    origin_key = ((video.get("consumer") or {}).get("originVideoKey") or "")
    if origin_key:
        origin_key = str(origin_key).encode("utf-8").decode("unicode_escape")
        return f"https://sns-video-bd.xhscdn.com/{origin_key.lstrip('/')}", 0
    source = video.get("src")
    return (str(source), 0) if isinstance(source, str) and source.startswith("http") else ("", 0)


class XiaohongshuExtractor(PlatformExtractor):
    platform = "xiaohongshu"

    def parse(self, share_text: str) -> PlatformMedia:
        shared_url = extract_first_url(share_text)
        if not host_matches(shared_url, XIAOHONGSHU_DOMAINS):
            raise ValueError("请输入有效的小红书分享链接")
        final_url, html = _get_page(shared_url)
        if not host_matches(final_url, XIAOHONGSHU_DOMAINS):
            raise ValueError("小红书短链接跳转到了不受支持的地址")
        match = re.search(r"/(?:explore|discovery/item|item)/([0-9a-zA-Z]+)", final_url)
        if not match:
            # Some failed share redirects keep the note ID in the query.
            query = urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query)
            note_id = str((query.get("noteId") or query.get("note_id") or [""])[0])
        else:
            note_id = match.group(1)
        if not note_id:
            raise ValueError("无法从分享链接中识别小红书笔记ID")

        state = extract_assignment_json(html, "window.__INITIAL_STATE__")
        note = _find_note(state, note_id)
        if not note:
            raise ValueError("小红书页面没有返回笔记详情，链接可能已失效、需要登录或触发验证")
        video = note.get("video") or {}
        if not isinstance(video, dict) or not video:
            raise ValueError("该小红书笔记不是视频笔记，暂时没有可识别的语音")
        media_url, duration = _pick_video(video)
        if not media_url:
            raise ValueError("小红书视频笔记没有返回公开播放地址")
        if not duration:
            duration = milliseconds_to_seconds(video.get("duration"))
        user = note.get("user") or {}
        return PlatformMedia(
            platform=self.platform,
            video_id=note_id,
            title=str(note.get("title") or note.get("desc") or f"小红书视频 {note_id}").strip(),
            author=str(user.get("nickname") or user.get("nickName") or "").strip(),
            duration=duration,
            source_url=shared_url,
            media_url=media_url,
            media_headers={**XIAOHONGSHU_HEADERS, "Referer": final_url},
        )


def parse_xiaohongshu_share(share_text: str) -> dict[str, Any]:
    return XiaohongshuExtractor().parse(share_text).as_dict()
