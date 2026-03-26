"""
cartoon: 漫画模块（当前实现腾讯动漫 ac.qq.com）。
"""

from cartoon.api import (
    CartoonSourceAnalysis,
    TencentChapter,
    TencentComicDetail,
    TencentComicItem,
    analyze_cartoon_source,
    source_chapter_image_urls,
    source_comic_detail,
    source_cover_headers,
    source_hot_comics,
    source_search_comics,
    tencent_chapter_image_urls,
    tencent_comic_detail,
    tencent_cover_headers,
    tencent_hot_comics,
    tencent_search_comics,
)

__all__ = [
    "CartoonSourceAnalysis",
    "TencentChapter",
    "TencentComicDetail",
    "TencentComicItem",
    "analyze_cartoon_source",
    "source_chapter_image_urls",
    "source_comic_detail",
    "source_cover_headers",
    "source_hot_comics",
    "source_search_comics",
    "tencent_chapter_image_urls",
    "tencent_comic_detail",
    "tencent_cover_headers",
    "tencent_hot_comics",
    "tencent_search_comics",
]

