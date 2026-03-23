from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from typing import Any

from src.common import BiqugeError, http_get


DEFAULT_BILIBILI_POPULAR_URL = (
    "https://api.bilibili.com/x/web-interface/popular"
    "?ps=20&pn=1&web_location=333.934"
    "&w_rid=4ae31c690e07def2dfc2133cfbe7cf2b"
    "&wts=1773993397"
)


@dataclass(frozen=True)
class VideoItem:
    bvid: str
    title: str
    pic: str
    url: str


def _normalize_pic_url(pic: str) -> str:
    pic = (pic or "").strip()
    if not pic:
        return ""
    if pic.startswith("//"):
        return "https:" + pic
    return pic


def _extract_bvid_url(*, bvid: str, short_link_v2: str | None = None) -> str:
    if bvid:
        return f"https://www.bilibili.com/video/{bvid}"
    if short_link_v2:
        return short_link_v2
    return ""


def _print_full_debug(prefix: str, raw: bytes, *, max_chars: int = 100) -> None:
    """
    把单次请求的响应打印出来（用于定位接口返回异常）。
    默认只输出前 `max_chars` 个字符，避免控制台刷屏。
    """

    try:
        text = raw.decode("utf-8", "ignore")
    except Exception:
        text = str(raw)
    print(f"[Nothing/video] {prefix} full raw response begin")
    if max_chars is None or max_chars <= 0:
        print(text)
    else:
        preview = text[:max_chars]
        if len(text) > max_chars:
            print(preview + f"...[truncated total={len(text)}]")
        else:
            print(preview)
    print(f"[Nothing/video] {prefix} full raw response end")


def _robust_json_loads(raw: bytes, *, context: str) -> Any:
    """
    尽量容错的 JSON 解析：
    - 先尝试 UTF-8 strict 解码
    - 解码失败则 fallback 到 ignore
    - JSON 解析失败则尝试截取第一个 '{' 到最后一个 '}' 子串再解析
    """

    last_exc: Exception | None = None
    # 1) normal decode
    try:
        decoded = raw.decode("utf-8", "strict")
        return json.loads(decoded)
    except Exception as e:  # noqa: BLE001
        last_exc = e

    # 2) decode fallback
    try:
        decoded = raw.decode("utf-8", "ignore")
        return json.loads(decoded)
    except Exception as e:  # noqa: BLE001
        last_exc = e

    # 3) substring extraction
    try:
        decoded = raw.decode("utf-8", "ignore")
        start = decoded.find("{")
        end = decoded.rfind("}")
        if start >= 0 and end > start:
            sub = decoded[start : end + 1]
            return json.loads(sub)
    except Exception as e:  # noqa: BLE001
        last_exc = e

    if last_exc is not None:
        _print_full_debug(context, raw)
        print(f"[Nothing/video] json parse failed ({context}): {last_exc!r}")
    raise BiqugeError(f"JSON 解析失败: {context}: {last_exc!r}")


def bilibili_hot_videos_fetch(
    full_url: str = DEFAULT_BILIBILI_POPULAR_URL,
    *,
    debug_print_payload_on_error: bool = True,
) -> list[VideoItem]:
    """
    拉取 B 站热门视频列表（你给的 popular 接口）。

    解析重点：
    - title：视频标题
    - pic：封面图（popular 返回的 pic）
    - bvid：视频 BV 号（用于生成 url）
    """

    raw = http_get(full_url, headers={"Accept": "application/json", "Referer": "https://www.bilibili.com/"})
    payload = _robust_json_loads(raw, context="hot")

    if int(payload.get("code", -1)) != 0:
        if debug_print_payload_on_error:
            _print_full_debug("hot", raw)
        raise BiqugeError(f"热门接口错误 code={payload.get('code')}, message={payload.get('message')}")

    items = ((payload.get("data") or {}).get("list")) or []
    if not isinstance(items, list):
        if debug_print_payload_on_error:
            _print_full_debug("hot", raw)
        return []

    out: list[VideoItem] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        bvid = str(it.get("bvid") or "").strip()
        pic = _normalize_pic_url(str(it.get("pic") or "") or str(it.get("cover43") or ""))
        url = _extract_bvid_url(bvid=bvid, short_link_v2=it.get("short_link_v2"))
        if title and pic and url:
            out.append(VideoItem(bvid=bvid, title=title, pic=pic, url=url))
    return out


def bilibili_search_videos(
    keyword: str,
    *,
    page: int = 1,
    page_size: int = 20,
    order: str = "general",
    debug_print_payload_on_error: bool = True,
) -> list[VideoItem]:
    """
    视频搜索列表（web/search/all/v2）。

    解析策略：
    - 顶层 data.result 是多个模块
    - 找到 result_type == "video" 的模块，取其 data 数组
    - 从每条结果里提取 title / pic / bvid 并生成 url
    """

    keyword = (keyword or "").strip()
    if not keyword:
        return []

    query = {
        "keyword": keyword,
        "page": page,
        "page_size": page_size,
        "order": order,
    }
    qs = urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    url = f"https://api.bilibili.com/x/web-interface/search/all/v2?{qs}"

    raw = http_get(url, headers={"Accept": "application/json", "Referer": "https://search.bilibili.com/"})
    payload = _robust_json_loads(raw, context="search")

    if int(payload.get("code", -1)) != 0:
        if debug_print_payload_on_error:
            _print_full_debug("search", raw)
        raise BiqugeError(f"搜索接口错误 code={payload.get('code')}, message={payload.get('message')}")

    data = payload.get("data") or {}
    result = data.get("result") or []
    if not isinstance(result, list):
        if debug_print_payload_on_error:
            _print_full_debug("search", raw)
        return []

    video_results: list[dict[str, Any]] = []
    for block in result:
        if not isinstance(block, dict):
            continue
        if block.get("result_type") == "video":
            video_results = block.get("data") or []
            break

    if not isinstance(video_results, list):
        if debug_print_payload_on_error:
            _print_full_debug("search", raw)
        return []

    out: list[VideoItem] = []
    for it in video_results:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        bvid = str(it.get("bvid") or "").strip()
        pic = _normalize_pic_url(str(it.get("pic") or ""))
        url2 = _extract_bvid_url(bvid=bvid, short_link_v2=it.get("short_link_v2")) or str(it.get("arcurl") or "").strip()
        if title and pic and url2:
            out.append(VideoItem(bvid=bvid, title=title, pic=pic, url=url2))
    return out


__all__ = ["VideoItem", "bilibili_hot_videos_fetch", "bilibili_search_videos", "DEFAULT_BILIBILI_POPULAR_URL"]

