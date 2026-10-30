from __future__ import annotations

import re
import urllib.parse
from typing import Any

import requests

from .base import (
    DESKTOP_USER_AGENT,
    PlatformExtractor,
    PlatformMedia,
    extract_first_url,
    host_matches,
    request_json,
    resolve_url,
)


BILIBILI_DOMAINS = ("bilibili.com", "b23.tv")
BILIBILI_HEADERS = {
    "User-Agent": DESKTOP_USER_AGENT,
    "Referer": "https://www.bilibili.com/",
}


def _api_data(payload: dict[str, Any], label: str) -> dict[str, Any]:
    if int(payload.get("code") or 0) != 0:
        raise ValueError(f"B站{label}失败：{payload.get('message') or '未知错误'}")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError(f"B站没有返回{label}")
    return data


class BilibiliExtractor(PlatformExtractor):
    platform = "bilibili"

    def parse(self, share_text: str) -> PlatformMedia:
        shared_url = extract_first_url(share_text)
        if not host_matches(shared_url, BILIBILI_DOMAINS):
            raise ValueError("请输入有效的B站分享链接")

        final_url = resolve_url(shared_url, headers=BILIBILI_HEADERS)
        if not host_matches(final_url, BILIBILI_DOMAINS):
            raise ValueError("B站短链接跳转到了不受支持的地址")
        match = re.search(r"/video/(BV[0-9A-Za-z]+|av\d+)", final_url, flags=re.IGNORECASE)
        if not match:
            raise ValueError("无法从分享链接中识别B站视频ID")
        identifier = match.group(1)
        params: dict[str, Any]
        if identifier.lower().startswith("av"):
            params = {"aid": identifier[2:]}
        else:
            params = {"bvid": f"BV{identifier[2:]}"}

        view = _api_data(
            request_json(
                "https://api.bilibili.com/x/web-interface/view",
                headers=BILIBILI_HEADERS,
                params=params,
            ),
            "视频信息获取",
        )
        bvid = str(view.get("bvid") or params.get("bvid") or identifier)
        pages = view.get("pages") or []
        query = urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query)
        try:
            page_number = max(1, int((query.get("p") or ["1"])[0]))
        except ValueError:
            page_number = 1
        if not pages:
            pages = [{"cid": view.get("cid"), "duration": view.get("duration"), "part": ""}]
        if page_number > len(pages):
            raise ValueError(f"B站视频只有 {len(pages)} 个分P，找不到P{page_number}")
        page = pages[page_number - 1]
        cid = page.get("cid")
        if not cid:
            raise ValueError("B站视频没有返回CID")

        play = _api_data(
            request_json(
                "https://api.bilibili.com/x/player/playurl",
                headers={**BILIBILI_HEADERS, "Referer": final_url},
                params={
                    "bvid": bvid,
                    "cid": cid,
                    "qn": 16,
                    "fnver": 0,
                    "fnval": 0,
                    "fourk": 0,
                    "platform": "html5",
                },
            ),
            "播放地址获取",
        )
        media_urls = [str(item.get("url") or "") for item in play.get("durl") or [] if item.get("url")]
        if not media_urls:
            raise ValueError("该B站视频没有可直接用于语音识别的公开MP4地址")

        title = str(view.get("title") or f"B站视频 {bvid}").strip()
        part_title = str(page.get("part") or "").strip()
        if len(pages) > 1:
            title = f"{title} · P{page_number}{f' {part_title}' if part_title else ''}"
        duration_ms = play.get("timelength") or 0
        duration = round(float(duration_ms) / 1000, 3) if duration_ms else float(page.get("duration") or 0)
        owner = view.get("owner") or {}
        return PlatformMedia(
            platform=self.platform,
            video_id=f"{bvid}-P{page_number}" if len(pages) > 1 else bvid,
            title=title,
            author=str(owner.get("name") or "").strip(),
            duration=duration,
            source_url=shared_url,
            media_url=media_urls[0],
            media_headers={**BILIBILI_HEADERS, "Referer": final_url},
        )


def parse_bilibili_share(share_text: str) -> dict[str, Any]:
    return BilibiliExtractor().parse(share_text).as_dict()
