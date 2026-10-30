from __future__ import annotations

import json
import re
import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

import requests


DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class PlatformMedia:
    platform: str
    video_id: str
    title: str
    author: str
    duration: float
    source_url: str
    media_url: str
    media_headers: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlatformExtractor(ABC):
    platform: str

    @abstractmethod
    def parse(self, share_text: str) -> PlatformMedia:
        raise NotImplementedError


def extract_first_url(text: str) -> str:
    source = text or ""
    match = re.search(r"https?://[^\s，。；、]+", source, flags=re.IGNORECASE)
    if match:
        value = match.group(0)
    else:
        # New Xiaohongshu shares sometimes omit the scheme. Supporting a plain
        # host here is safe because every platform adapter validates its hosts.
        match = re.search(
            r"(?<![@\w])((?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s，。；、]+)",
            source,
            flags=re.IGNORECASE,
        )
        if not match:
            raise ValueError("没有找到有效的分享链接")
        value = f"https://{match.group(1)}"
    return value.rstrip(").,!?;:，。；！？」』】")


def host_matches(url: str, domains: tuple[str, ...]) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def require_host(url: str, domains: tuple[str, ...], platform_label: str) -> None:
    if not host_matches(url, domains):
        raise ValueError(f"请输入有效的{platform_label}分享链接")


def request_text(
    url: str,
    *,
    headers: dict[str, str],
    timeout: int = 20,
    session: requests.Session | None = None,
) -> str:
    client = session or requests.Session()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"网络请求失败：{last_error}") from last_error


def request_json(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    client = session or requests.Session()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.get(
                url,
                headers=headers,
                params=params,
                timeout=timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("接口没有返回JSON对象")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"网络请求失败：{last_error}") from last_error


def resolve_url(
    url: str,
    *,
    headers: dict[str, str],
    timeout: int = 20,
    session: requests.Session | None = None,
) -> str:
    client = session or requests.Session()
    try:
        response = client.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
        response.raise_for_status()
        return str(response.url)
    except requests.RequestException as exc:
        raise RuntimeError(f"分享短链接解析失败：{exc}") from exc


def _replace_javascript_literals(source: str) -> str:
    """Turn JavaScript-only primitive literals into JSON outside strings."""

    output: list[str] = []
    quote = ""
    escaped = False
    index = 0
    replacements = {"undefined": "null", "NaN": "null", "Infinity": "null"}
    while index < len(source):
        char = source[index]
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            output.append(char)
            index += 1
            continue
        replaced = False
        for token, replacement in replacements.items():
            if source.startswith(token, index):
                before = source[index - 1] if index else ""
                after_index = index + len(token)
                after = source[after_index] if after_index < len(source) else ""
                if not (before.isalnum() or before in "_$") and not (after.isalnum() or after in "_$"):
                    output.append(replacement)
                    index = after_index
                    replaced = True
                    break
        if not replaced:
            output.append(char)
            index += 1
    return "".join(output)


def extract_assignment_json(html: str, marker: str) -> dict[str, Any]:
    marker_index = html.find(marker)
    if marker_index < 0:
        raise ValueError("页面没有返回可解析的视频数据")
    start = html.find("{", marker_index + len(marker))
    if start < 0:
        raise ValueError("页面视频数据格式不完整")

    depth = 0
    quote = ""
    escaped = False
    end = -1
    for index in range(start, len(html)):
        char = html[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end < 0:
        raise ValueError("页面视频数据没有完整结束")

    source = _replace_javascript_literals(html[start:end])
    try:
        payload = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ValueError("页面视频数据无法解析") from exc
    if not isinstance(payload, dict):
        raise ValueError("页面视频数据格式错误")
    return payload


def milliseconds_to_seconds(value: Any) -> float:
    try:
        return round(float(value or 0) / 1000, 3)
    except (TypeError, ValueError):
        return 0.0
