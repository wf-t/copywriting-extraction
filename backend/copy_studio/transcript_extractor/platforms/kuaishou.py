from __future__ import annotations

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


KUAISHOU_DOMAINS = (
    "kuaishou.com",
    "kuaishou.cn",
    "chenzhongtech.com",
    "chenzhongtech.cn",
    "gifshow.com",
)
KUAISHOU_HEADERS = {
    "User-Agent": DESKTOP_USER_AGENT,
    "Referer": "https://www.kuaishou.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
KUAISHOU_MOBILE_HEADERS = {
    **KUAISHOU_HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
    ),
}


def _get_page(shared_url: str) -> tuple[str, str]:
    try:
        response = requests.get(
            shared_url,
            headers=KUAISHOU_HEADERS,
            timeout=20,
            allow_redirects=True,
        )
        response.raise_for_status()
        return str(response.url), response.text
    except requests.RequestException as exc:
        raise RuntimeError(f"快手分享链接请求失败：{exc}") from exc


def _get_mobile_page(video_id: str) -> tuple[str, str]:
    mobile_url = f"https://m.gifshow.com/fw/photo/{urllib.parse.quote(video_id, safe='')}"
    try:
        response = requests.get(
            mobile_url,
            headers=KUAISHOU_MOBILE_HEADERS,
            timeout=20,
            allow_redirects=True,
        )
        response.raise_for_status()
        return str(response.url), response.text
    except requests.RequestException as exc:
        raise RuntimeError(f"快手移动分享页请求失败：{exc}") from exc


def _pick_photo(client: dict[str, Any], video_id: str) -> dict[str, Any]:
    exact = client.get(f"VisionVideoDetailPhoto:{video_id}")
    if isinstance(exact, dict):
        return exact
    for key, value in client.items():
        if isinstance(value, dict) and (
            key.startswith("VisionVideoDetailPhoto:") or value.get("__typename") == "VisionVideoDetailPhoto"
        ):
            return value
    return {}


def _pick_author(client: dict[str, Any], photo: dict[str, Any]) -> str:
    author = photo.get("author") or photo.get("user") or {}
    if isinstance(author, dict) and (author.get("name") or author.get("userName")):
        return str(author.get("name") or author.get("userName") or "").strip()
    for key, value in client.items():
        if isinstance(value, dict) and (
            key.startswith("VisionVideoDetailAuthor:") or value.get("__typename") == "VisionVideoDetailAuthor"
        ):
            return str(value.get("name") or value.get("userName") or "").strip()
    return ""


def _pick_media_url(photo: dict[str, Any]) -> str:
    # Prefer H.264 because it has the broadest compatibility with file ASR.
    direct = photo.get("photoUrl")
    if direct:
        return str(direct)
    resource = photo.get("videoResource") or {}
    resource_json = resource.get("json") or {}
    for codec in ("h264", "hevc"):
        representations: list[dict[str, Any]] = []
        for adaptation in ((resource_json.get(codec) or {}).get("adaptationSet") or []):
            representations.extend(adaptation.get("representation") or [])
        representations = [item for item in representations if item.get("url")]
        if representations:
            representations.sort(
                key=lambda item: (
                    float(item.get("maxBitrate") or 0),
                    float(item.get("width") or 0) * float(item.get("height") or 0),
                ),
                reverse=True,
            )
            return str(representations[0]["url"])
    return ""


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _pick_mobile_detail(state: dict[str, Any], video_id: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for item in _walk_dicts(state):
        photo = item.get("photo")
        if not isinstance(photo, dict):
            continue
        share_info = str(photo.get("share_info") or "")
        main_urls = photo.get("mainMvUrls") or []
        if photo.get("photoId") or photo.get("caption") or main_urls:
            candidates.append(item)
        if video_id and (video_id in share_info or any(video_id in str(url) for url in main_urls)):
            return item
    return candidates[0] if candidates else {}


def _pick_mobile_media_url(detail: dict[str, Any]) -> str:
    direct = detail.get("mp4Url")
    if direct:
        return str(direct)
    photo = detail.get("photo") or {}
    for item in photo.get("mainMvUrls") or []:
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
        if isinstance(item, str) and item:
            return item
    return ""


def _parse_mobile_state(
    html: str,
    *,
    video_id: str,
    shared_url: str,
    referer: str,
) -> PlatformMedia | None:
    try:
        state = extract_assignment_json(html, "window.INIT_STATE")
    except ValueError:
        return None
    detail = _pick_mobile_detail(state, video_id)
    photo = detail.get("photo") or {}
    if not isinstance(photo, dict) or not photo:
        return None
    media_url = _pick_mobile_media_url(detail)
    if not media_url:
        photo_type = str(photo.get("photoType") or "").upper()
        if photo_type in {"SINGLE_PICTURE", "MULTI_PICTURE", "PICTURE"} or photo.get("singlePicture"):
            raise ValueError("该快手作品是图片作品，暂不支持语音文案提取")
        raise ValueError("该快手作品没有返回公开播放地址，可能已失效或需要验证")
    return PlatformMedia(
        platform="kuaishou",
        video_id=video_id,
        title=str(photo.get("caption") or f"快手视频 {video_id}").strip(),
        author=str(photo.get("userName") or "").strip(),
        duration=milliseconds_to_seconds(photo.get("duration")),
        source_url=shared_url,
        media_url=media_url,
        media_headers={**KUAISHOU_HEADERS, "Referer": referer},
    )


class KuaishouExtractor(PlatformExtractor):
    platform = "kuaishou"

    def parse(self, share_text: str) -> PlatformMedia:
        shared_url = extract_first_url(share_text)
        if not host_matches(shared_url, KUAISHOU_DOMAINS):
            raise ValueError("请输入有效的快手分享链接")
        final_url, html = _get_page(shared_url)
        if not host_matches(final_url, KUAISHOU_DOMAINS):
            raise ValueError("快手短链接跳转到了不受支持的地址")

        path_parts = [part for part in urllib.parse.urlparse(final_url).path.split("/") if part]
        video_id = path_parts[-1] if path_parts else ""
        if not video_id:
            raise ValueError("无法从分享链接中识别快手作品ID")
        mobile_result = _parse_mobile_state(
            html,
            video_id=video_id,
            shared_url=shared_url,
            referer=final_url,
        )
        if mobile_result:
            return mobile_result

        try:
            state = extract_assignment_json(html, "window.__APOLLO_STATE__")
        except ValueError:
            state = {}
        client = state.get("defaultClient") or {}
        photo = _pick_photo(client, video_id) if isinstance(client, dict) else {}
        media_url = _pick_media_url(photo) if photo else ""
        if not media_url:
            mobile_url, mobile_html = _get_mobile_page(video_id)
            mobile_result = _parse_mobile_state(
                mobile_html,
                video_id=video_id,
                shared_url=shared_url,
                referer=mobile_url,
            )
            if mobile_result:
                return mobile_result
            raise ValueError("快手页面没有返回作品详情，链接可能已失效或需要验证")

        return PlatformMedia(
            platform=self.platform,
            video_id=video_id,
            title=str(photo.get("caption") or f"快手视频 {video_id}").strip(),
            author=_pick_author(client, photo),
            duration=milliseconds_to_seconds(photo.get("duration")),
            source_url=shared_url,
            media_url=media_url,
            media_headers={**KUAISHOU_HEADERS, "Referer": final_url},
        )


def parse_kuaishou_share(share_text: str) -> dict[str, Any]:
    return KuaishouExtractor().parse(share_text).as_dict()
