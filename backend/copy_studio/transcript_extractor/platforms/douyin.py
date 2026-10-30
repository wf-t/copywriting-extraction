from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import requests

from .base import PlatformExtractor, PlatformMedia, extract_first_url, host_matches


DOUYIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://www.douyin.com/",
}
DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")


def _request(url: str, *, timeout: int = 30) -> urllib.request.addinfourl:
    request = urllib.request.Request(url, headers=DOUYIN_HEADERS)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"网络请求失败：{last_error}") from last_error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _head_redirect_location(url: str, *, timeout: int = 12) -> str:
    request = urllib.request.Request(url, headers=DOUYIN_HEADERS, method="HEAD")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            location = response.headers.get("Location") or ""
    except urllib.error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise
        location = exc.headers.get("Location") or ""
    return urllib.parse.urljoin(url, location.strip()) if location else ""


def _resolve_share_url(shared_url: str) -> str:
    parsed = urllib.parse.urlparse(shared_url)
    if (parsed.hostname or "").lower() == "v.douyin.com":
        head_url = urllib.parse.urlunparse(parsed._replace(scheme="http"))
        try:
            location = _head_redirect_location(head_url)
            if location and host_matches(location, DOUYIN_DOMAINS):
                return location
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
    with _request(shared_url) as response:
        return response.geturl()


def _request_share_html(url: str, *, timeout: int = 20) -> str:
    session = requests.Session()
    last_error: Exception | None = None
    last_html = ""
    for attempt in range(3):
        try:
            response = session.get(url, headers=DOUYIN_HEADERS, timeout=timeout)
            response.raise_for_status()
            last_html = response.text
            if "window._ROUTER_DATA" in last_html and "videoInfoRes" in last_html and "item_list" in last_html:
                return last_html
            last_error = ValueError("抖音分享页暂未返回视频详情")
        except requests.RequestException as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(0.4 * (attempt + 1))
    if last_html:
        return last_html
    raise RuntimeError(f"抖音分享页请求失败：{last_error}") from last_error


class DouyinExtractor(PlatformExtractor):
    platform = "douyin"

    def parse(self, share_text: str) -> PlatformMedia:
        shared_url = extract_first_url(share_text)
        if not host_matches(shared_url, DOUYIN_DOMAINS):
            raise ValueError("请输入有效的抖音分享链接")
        final_url = _resolve_share_url(shared_url)

        video_match = re.search(r"/(?:video|note)/(\d+)", final_url)
        if not video_match:
            path_parts = [part for part in urllib.parse.urlparse(final_url).path.split("/") if part]
            candidate = next((part for part in reversed(path_parts) if part.isdigit()), "")
            if not candidate:
                raise ValueError("无法从分享链接中识别抖音视频 ID")
            video_id = candidate
        else:
            video_id = video_match.group(1)

        page_url = f"https://www.iesdouyin.com/share/video/{video_id}/?from_ssr=1"
        html = _request_share_html(page_url)
        match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", html, flags=re.DOTALL)
        if not match:
            raise ValueError("抖音页面没有返回可解析的视频信息，链接可能已失效或需要验证")

        router_data: dict[str, Any] = json.loads(match.group(1).strip())
        loader_data = router_data.get("loaderData") or {}
        page_data = loader_data.get("video_(id)/page") or loader_data.get("note_(id)/page")
        items = (((page_data or {}).get("videoInfoRes") or {}).get("item_list") or [])
        if not items:
            raise ValueError("抖音页面没有返回视频详情")

        item = items[0]
        video = item.get("video") or {}
        play_urls = ((video.get("play_addr") or {}).get("url_list") or [])
        if not play_urls:
            raise ValueError("没有找到视频音频来源")
        author = item.get("author") or {}
        duration_ms = video.get("duration") or item.get("duration") or 0
        return PlatformMedia(
            platform=self.platform,
            video_id=video_id,
            title=str(item.get("desc") or f"抖音视频 {video_id}").strip(),
            author=str(author.get("nickname") or "").strip(),
            duration=round(float(duration_ms) / 1000, 1) if duration_ms else 0,
            source_url=shared_url,
            media_url=str(play_urls[0]).replace("playwm", "play"),
            media_headers=dict(DOUYIN_HEADERS),
        )


def parse_douyin_share(share_text: str) -> dict[str, Any]:
    return DouyinExtractor().parse(share_text).as_dict()
