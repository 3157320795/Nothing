from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Iterable

from src.common import BiqugeError, http_get

_BASE_PC = "https://ac.qq.com/"
_BASE_M = "https://m.ac.qq.com/"


@dataclass(frozen=True)
class CartoonSourceAnalysis:
    source_url: str
    source_name: str
    source_type: str  # tencent | mkzhan | bilmanga | unknown
    has_hot: bool
    has_search: bool
    note: str = ""


@dataclass(frozen=True)
class TencentComicItem:
    comic_id: str
    title: str
    url: str
    source: str  # hot / search


@dataclass(frozen=True)
class TencentChapter:
    title: str
    url: str


@dataclass(frozen=True)
class TencentComicDetail:
    comic_id: str
    title: str
    intro: str
    cover_url: str
    comic_url: str
    chapters: list[TencentChapter]


def _decode_html(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def _strip_tags(s: str) -> str:
    s = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.I | re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>", "", s, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _normalize_url(url: str, *, mobile: bool = False) -> str:
    base = _BASE_M if mobile else _BASE_PC
    return urllib.parse.urljoin(base, url or "")


def _comic_id_from_url(url: str) -> str:
    m = re.search(r"/id/(\d+)", url, flags=re.I)
    return m.group(1) if m else ""


def _extract_items_from_links(text: str, *, source: str, mobile: bool) -> list[TencentComicItem]:
    """
    从 HTML 中抽取漫画详情链接：
    - pc: /Comic/ComicInfo/id/<id> 或 /Comic/comicInfo/id/<id>
    - m:  /comic/index/id/<id>
    """
    if mobile:
        pat = re.compile(r"<a[^>]+href=[\"']([^\"']*/comic/index/id/\d+[^\"']*)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    else:
        pat = re.compile(r"<a[^>]+href=[\"']([^\"']*/Comic/(?:comicInfo|ComicInfo)/id/\d+[^\"']*)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    seen: set[str] = set()
    out: list[TencentComicItem] = []
    for href, label_html in pat.findall(text):
        url = _normalize_url(href, mobile=mobile)
        cid = _comic_id_from_url(url)
        if not cid or cid in seen:
            continue
        title = _strip_tags(label_html)
        if not title:
            continue
        seen.add(cid)
        out.append(TencentComicItem(comic_id=cid, title=title, url=url, source=source))
    return out


def tencent_hot_comics(*, limit: int = 80) -> list[TencentComicItem]:
    raw = http_get(_BASE_PC, timeout=20, headers={"Referer": _BASE_PC})
    text = _decode_html(raw)
    rows = _extract_items_from_links(text, source="hot", mobile=False)
    if not rows:
        raise BiqugeError("热门漫画抓取为空")
    return rows[: max(1, int(limit))]


def tencent_search_comics(keyword: str, *, limit: int = 80) -> list[TencentComicItem]:
    kw = (keyword or "").strip()
    if not kw:
        return []
    q = urllib.parse.quote(kw, safe="")
    url = f"{_BASE_M}search/result?word={q}"
    raw = http_get(url, timeout=20, headers={"Referer": _BASE_M})
    text = _decode_html(raw)
    rows = _extract_items_from_links(text, source="search", mobile=True)
    return rows[: max(1, int(limit))]


def _first_match(text: str, patterns: Iterable[str]) -> str:
    for p in patterns:
        m = re.search(p, text, flags=re.I | re.S)
        if m:
            return _strip_tags(m.group(1))
    return ""


def _extract_cover_url(text: str) -> str:
    # 优先 OpenGraph
    for p in (
        r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']",
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image[\"']",
    ):
        m = re.search(p, text, flags=re.I | re.S)
        if m:
            return _normalize_url(m.group(1), mobile=True)
    # 兜底：常见封面图路径
    m2 = re.search(r"(https?://[^\"'>\s]+(?:cover|comic)[^\"'>\s]+\.(?:jpg|jpeg|png|webp))", text, flags=re.I)
    if m2:
        return m2.group(1)
    return ""


def _extract_chapters(text: str) -> list[TencentChapter]:
    pat = re.compile(
        r"<a[^>]+href=[\"']([^\"']*/chapter/index/id/\d+/(?:cid|seqno)/\d+[^\"']*)[\"'][^>]*>(.*?)</a>",
        flags=re.I | re.S,
    )
    seen: set[str] = set()
    out: list[TencentChapter] = []
    for href, label_html in pat.findall(text):
        u = _normalize_url(href, mobile=True)
        if u in seen:
            continue
        seen.add(u)
        t = _strip_tags(label_html)
        if not t:
            continue
        out.append(TencentChapter(title=t, url=u))
    return out


def tencent_comic_detail(comic_id: str) -> TencentComicDetail:
    cid = re.sub(r"\D", "", str(comic_id or ""))
    if not cid:
        raise BiqugeError("comic_id 为空")
    url = f"{_BASE_M}comic/index/id/{cid}"
    raw = http_get(url, timeout=20, headers={"Referer": _BASE_M})
    text = _decode_html(raw)

    title = _first_match(
        text,
        [
            r"<h1[^>]*>(.*?)</h1>",
            r"<title[^>]*>(.*?)</title>",
        ],
    )
    intro = _first_match(
        text,
        [
            r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)[\"']",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']description[\"']",
            r"<p[^>]+class=[\"'][^\"']*works-intro-short[^\"']*[\"'][^>]*>(.*?)</p>",
        ],
    )
    cover_url = _extract_cover_url(text)
    chapters = _extract_chapters(text)
    return TencentComicDetail(
        comic_id=cid,
        title=title or f"漫画 {cid}",
        intro=intro,
        cover_url=cover_url,
        comic_url=url,
        chapters=chapters,
    )


def tencent_chapter_image_urls(chapter_url: str) -> list[str]:
    """
    解析章节页面中的可公开图片地址（主要用于免费试读章节直渲染）。
    """
    url = str(chapter_url or "").strip()
    if not url:
        return []
    raw = http_get(url, timeout=20, headers={"Referer": _BASE_M})
    text = _decode_html(raw)
    # 章节图通常在 manhua.acimg.cn 下，先抓这一批，避免把无关资源全拿进来
    candidates = re.findall(r"https?://manhua\.acimg\.cn/[^\"'\s>]+\.(?:jpg|jpeg|png|webp)", text, flags=re.I)
    if not candidates:
        # 兜底：抓页面里所有图片链接
        candidates = re.findall(r"https?://[^\"'\s>]+\.(?:jpg|jpeg|png|webp)", text, flags=re.I)
    seen: set[str] = set()
    out: list[str] = []
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    # 经验上 vertical 更可能是正文图，给它优先级
    out.sort(key=lambda x: (0 if "/vertical/" in x else 1, x))
    return out


def tencent_cover_headers(*, referer: str) -> dict[str, str]:
    return {
        "Referer": referer or _BASE_M,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }


__all__ = [
    "TencentComicItem",
    "TencentChapter",
    "TencentComicDetail",
    "tencent_hot_comics",
    "tencent_search_comics",
    "tencent_comic_detail",
    "tencent_chapter_image_urls",
    "tencent_cover_headers",
    "CartoonSourceAnalysis",
    "analyze_cartoon_source",
    "source_hot_comics",
    "source_search_comics",
    "source_comic_detail",
    "source_chapter_image_urls",
    "source_cover_headers",
]


def _source_type_from_url(source_url: str) -> str:
    u = str(source_url or "").strip().lower()
    if "mkzhan.com" in u:
        return "mkzhan"
    return "unknown"


def analyze_cartoon_source(source_url: str) -> CartoonSourceAnalysis:
    st = _source_type_from_url(source_url)
    if st == "mkzhan":
        return CartoonSourceAnalysis(source_url=source_url, source_name="漫客栈", source_type=st, has_hot=True, has_search=True, note="使用站内开放接口与首页榜单进行自动分析")
    return CartoonSourceAnalysis(source_url=source_url, source_name="未知站点", source_type=st, has_hot=False, has_search=False, note="当前版本仅支持 https://www.mkzhan.com/")


def _mkzhan_hot_comics(*, limit: int = 120) -> list[TencentComicItem]:
    raw = http_get("https://www.mkzhan.com/", timeout=20, headers={"Referer": "https://www.mkzhan.com/"})
    text = _decode_html(raw)
    # 漫客栈漫画详情一般是 /<comic_id>/ 结构（支持相对/绝对链接）
    pat = re.compile(r"<a[^>]+href=[\"']([^\"']*/\d+/?)['\"][^>]*>(.*?)</a>", re.I | re.S)
    seen: set[str] = set()
    out: list[TencentComicItem] = []
    for href, label in pat.findall(text):
        url = urllib.parse.urljoin("https://www.mkzhan.com/", href)
        m = re.search(r"https?://www\.mkzhan\.com/(\d+)/?$", url, flags=re.I)
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue
        seen.add(cid)
        title = _strip_tags(label)
        if not title:
            continue
        out.append(TencentComicItem(comic_id=cid, title=title, url=url, source="hot"))
        if len(out) >= limit:
            break
    return out


def _mkzhan_search_comics(keyword: str, *, limit: int = 120) -> list[TencentComicItem]:
    kw = (keyword or "").strip()
    if not kw:
        return []
    q = urllib.parse.quote(kw, safe="")
    url = f"https://comic.mkzcdn.com/search/keyword/?keyword={q}&page_num=1&page_size={max(1, min(limit, 50))}"
    raw = http_get(url, timeout=20, headers={"Referer": "https://www.mkzhan.com/"})
    text = _decode_html(raw)
    try:
        import json

        payload = json.loads(text)
    except Exception as e:  # noqa: BLE001
        raise BiqugeError(f"mkzhan 搜索解析失败: {e}") from e
    data = payload.get("data") or {}
    rows = data.get("list") or []
    out: list[TencentComicItem] = []
    seen: set[str] = set()
    for it in rows:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("comic_id") or "").strip()
        title = str(it.get("title") or "").strip()
        if not cid or not title or cid in seen:
            continue
        seen.add(cid)
        out.append(TencentComicItem(comic_id=cid, title=title, url=f"https://www.mkzhan.com/{cid}/", source="search"))
        if len(out) >= limit:
            break
    return out


def _mkzhan_comic_detail(comic_id_or_url: str) -> TencentComicDetail:
    s = str(comic_id_or_url or "").strip()
    if s.startswith("http://") or s.startswith("https://"):
        u = s
    else:
        cid = re.sub(r"\D", "", s)
        if not cid:
            raise BiqugeError("mkzhan comic_id 为空")
        u = f"https://www.mkzhan.com/{cid}/"
    comic_id = _comic_id_from_url(u) or re.sub(r"\D", "", u)
    if not comic_id:
        raise BiqugeError("mkzhan comic_id 无效")
    info_raw = http_get(f"https://comic.mkzcdn.com/comic/info/?comic_id={comic_id}", timeout=20, headers={"Referer": u})
    info_text = _decode_html(info_raw)
    chap_raw = http_get(f"https://comic.mkzcdn.com/chapter/v1/?comic_id={comic_id}", timeout=20, headers={"Referer": u})
    chap_text = _decode_html(chap_raw)
    try:
        import json

        info_payload = json.loads(info_text)
        chap_payload = json.loads(chap_text)
    except Exception as e:  # noqa: BLE001
        raise BiqugeError(f"mkzhan 详情解析失败: {e}") from e
    info = (info_payload.get("data") or {}) if isinstance(info_payload, dict) else {}
    ch_rows = (chap_payload.get("data") or []) if isinstance(chap_payload, dict) else []
    title = str(info.get("title") or "漫画").strip()
    intro = str(info.get("content") or "").strip()
    cover = str(info.get("cover") or "").replace("http://", "https://").strip()
    chs: list[TencentChapter] = []
    for it in ch_rows:
        if not isinstance(it, dict):
            continue
        chid = str(it.get("chapter_id") or "").strip()
        ct = str(it.get("title") or "").strip()
        if not chid or not ct:
            continue
        chs.append(TencentChapter(title=ct, url=f"https://www.mkzhan.com/{comic_id}/{chid}/"))
    return TencentComicDetail(comic_id=comic_id, title=title, intro=intro, cover_url=cover, comic_url=f"https://www.mkzhan.com/{comic_id}/", chapters=chs)


def source_hot_comics(source_url: str, *, limit: int = 120) -> list[TencentComicItem]:
    st = _source_type_from_url(source_url)
    if st == "mkzhan":
        return _mkzhan_hot_comics(limit=limit)
    raise BiqugeError("当前源不支持热门列表")


def source_search_comics(source_url: str, keyword: str, *, limit: int = 120) -> list[TencentComicItem]:
    st = _source_type_from_url(source_url)
    kw = (keyword or "").strip()
    if not kw:
        return []
    if st == "mkzhan":
        rows = _mkzhan_search_comics(kw, limit=limit)
        if rows:
            return rows
        # 搜索无结果时回退热门本地过滤
        hot = source_hot_comics(source_url, limit=300)
        q = kw.lower()
        return [x for x in hot if q in x.title.lower()][:limit]
    raise BiqugeError("当前源不支持搜索")


def source_comic_detail(source_url: str, item: TencentComicItem) -> TencentComicDetail:
    st = _source_type_from_url(source_url)
    if st == "mkzhan":
        return _mkzhan_comic_detail(item.url or item.comic_id)
    raise BiqugeError("当前源不支持详情解析")


def source_chapter_image_urls(source_url: str, chapter_url: str) -> list[str]:
    st = _source_type_from_url(source_url)
    if st != "mkzhan":
        raise BiqugeError("当前源不支持章节图片解析")
    u = str(chapter_url or "").strip()
    m = re.search(r"mkzhan\.com/(\d+)/(\d+)/?", u, flags=re.I)
    if not m:
        # fallback: 纯链接正则解析
        raw = http_get(u, timeout=20, headers={"Referer": u})
        text = _decode_html(raw)
        urls = re.findall(r"https?://[^\"'\s>]+\.(?:jpg|jpeg|png|webp)", text, flags=re.I)
    else:
        cid, chid = m.group(1), m.group(2)
        api = f"https://comic.mkzcdn.com/chapter/content/?comic_id={cid}&chapter_id={chid}"
        raw = http_get(api, timeout=20, headers={"Referer": f"https://www.mkzhan.com/{cid}/{chid}/"})
        text = _decode_html(raw)
        try:
            import json

            payload = json.loads(text)
            rows = payload.get("data") or []
            urls = [str(x.get("image") or "").replace("http://", "https://") for x in rows if isinstance(x, dict)]
        except Exception:
            urls = re.findall(r"https?://[^\"'\s>]+\.(?:jpg|jpeg|png|webp)", text, flags=re.I)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def source_cover_headers(source_url: str, *, referer: str) -> dict[str, str]:
    return {"Referer": referer or "https://www.mkzhan.com/"}

