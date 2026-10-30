from __future__ import annotations

from .base import PlatformExtractor, PlatformMedia, extract_first_url
from .bilibili import BilibiliExtractor, parse_bilibili_share
from .douyin import DouyinExtractor, parse_douyin_share
from .kuaishou import KuaishouExtractor, parse_kuaishou_share
from .xiaohongshu import XiaohongshuExtractor, parse_xiaohongshu_share


PLATFORM_EXTRACTORS: dict[str, PlatformExtractor] = {
    extractor.platform: extractor
    for extractor in (
        DouyinExtractor(),
        XiaohongshuExtractor(),
        KuaishouExtractor(),
        BilibiliExtractor(),
    )
}


def get_platform_extractor(platform: str) -> PlatformExtractor:
    try:
        return PLATFORM_EXTRACTORS[platform]
    except KeyError as exc:
        raise ValueError(f"不支持的视频平台：{platform}") from exc


__all__ = [
    "PLATFORM_EXTRACTORS",
    "PlatformExtractor",
    "PlatformMedia",
    "extract_first_url",
    "get_platform_extractor",
    "parse_bilibili_share",
    "parse_douyin_share",
    "parse_kuaishou_share",
    "parse_xiaohongshu_share",
]
