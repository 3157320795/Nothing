from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Iterable

from src.common import BiqugeError, http_get

_BASE_PC = "https://ac.qq.com/"
_BASE_M = "https://m.ac.qq.com/"
_BILIMANGA_BASE = "https://www.bilimanga.net/"
_MANGACOPY_BASE = "https://www.mangacopy.com/"


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
    if "bilimanga.net" in u or "bilimicro.top" in u:
        return "bilimanga"
    if "mangacopy.com" in u or "2026copy.com" in u:
        return "mangacopy"
    return "unknown"


def analyze_cartoon_source(source_url: str) -> CartoonSourceAnalysis:
    st = _source_type_from_url(source_url)
    if st == "mkzhan":
        return CartoonSourceAnalysis(source_url=source_url, source_name="漫客栈", source_type=st, has_hot=True, has_search=True, note="使用站内开放接口与首页榜单进行自动分析")
    if st == "bilimanga":
        return CartoonSourceAnalysis(
            source_url=source_url,
            source_name="哔哩漫画",
            source_type=st,
            has_hot=True,
            has_search=True,
            note="通过公开页面抓取；若触发 Cloudflare 风控，详情/章节可能失败",
        )
    if st == "mangacopy":
        return CartoonSourceAnalysis(
            source_url=source_url,
            source_name="拷贝漫画",
            source_type=st,
            has_hot=True,
            has_search=True,
            note="通过首页/榜单公开页面抓取，详情与章节解析为通用规则",
        )
    return CartoonSourceAnalysis(
        source_url=source_url,
        source_name="未知站点",
        source_type=st,
        has_hot=False,
        has_search=False,
        note="当前版本支持 https://www.mkzhan.com/、https://www.bilimanga.net/ 与 https://www.mangacopy.com/",
    )


def _common_html_headers(*, referer: str) -> dict[str, str]:
    return {
        "Referer": referer,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _bilimanga_extract_items(text: str, *, source: str) -> list[TencentComicItem]:
    pat = re.compile(r"<a[^>]+href=[\"']([^\"']*/detail/(\d+)\.html)['\"][^>]*>(.*?)</a>", re.I | re.S)
    seen: set[str] = set()
    out: list[TencentComicItem] = []
    for href, cid, label in pat.findall(text):
        if not cid or cid in seen:
            continue
        title = _strip_tags(label)
        if not title:
            continue
        seen.add(cid)
        url = urllib.parse.urljoin(_BILIMANGA_BASE, href)
        out.append(TencentComicItem(comic_id=cid, title=title, url=url, source=source))
    return out


def _bilimanga_hot_comics(*, limit: int = 240) -> list[TencentComicItem]:
    seeds = [
        _BILIMANGA_BASE,
        f"{_BILIMANGA_BASE}sort/",
        f"{_BILIMANGA_BASE}top/monthvisit/1.html",
        f"{_BILIMANGA_BASE}top/weekvisit/1.html",
    ]
    max_n = max(1, int(limit))
    seen: set[str] = set()
    out: list[TencentComicItem] = []
    for seed in seeds:
        try:
            raw = http_get(seed, timeout=20, headers=_common_html_headers(referer=_BILIMANGA_BASE))
            text = _decode_html(raw)
        except Exception:
            continue
        for it in _bilimanga_extract_items(text, source="hot"):
            if it.comic_id in seen:
                continue
            seen.add(it.comic_id)
            out.append(it)
            if len(out) >= max_n:
                return out
    if not out:
        raise BiqugeError("bilimanga 热门抓取为空，可能触发了 Cloudflare 风控")
    return out


def _bilimanga_search_comics(keyword: str, *, limit: int = 120) -> list[TencentComicItem]:
    kw = (keyword or "").strip()
    if not kw:
        return []
    q = urllib.parse.quote(kw, safe="")
    # 该站检索页参数兼容性不稳定，先尝试 query，再回退到热门本地过滤
    seeds = [
        f"{_BILIMANGA_BASE}search.html?keyword={q}",
        f"{_BILIMANGA_BASE}search.html?wd={q}",
        f"{_BILIMANGA_BASE}search.html?key={q}",
    ]
    seen: set[str] = set()
    out: list[TencentComicItem] = []
    for seed in seeds:
        try:
            raw = http_get(seed, timeout=20, headers=_common_html_headers(referer=_BILIMANGA_BASE))
            text = _decode_html(raw)
        except Exception:
            continue
        for it in _bilimanga_extract_items(text, source="search"):
            if it.comic_id in seen:
                continue
            seen.add(it.comic_id)
            out.append(it)
            if len(out) >= limit:
                return out
    if out:
        return out[:limit]
    hot = _bilimanga_hot_comics(limit=400)
    q2 = kw.lower()
    return [x for x in hot if q2 in x.title.lower()][:limit]


def _bilimanga_comic_detail(comic_id_or_url: str) -> TencentComicDetail:
    s = str(comic_id_or_url or "").strip()
    if s.startswith("http://") or s.startswith("https://"):
        u = s
    else:
        cid = re.sub(r"\D", "", s)
        if not cid:
            raise BiqugeError("bilimanga comic_id 为空")
        u = f"{_BILIMANGA_BASE}detail/{cid}.html"
    m = re.search(r"/detail/(\d+)\.html", u, flags=re.I)
    comic_id = m.group(1) if m else re.sub(r"\D", "", u)
    raw = http_get(u, timeout=20, headers=_common_html_headers(referer=_BILIMANGA_BASE))
    text = _decode_html(raw)
    low = text.lower()
    if "attention required! | cloudflare" in low or "sorry, you have been blocked" in low:
        raise BiqugeError("bilimanga 触发 Cloudflare 风控，请稍后重试或切换漫客栈")
    title = _first_match(
        text,
        [
            r"<h1[^>]*>(.*?)</h1>",
            r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)[\"']",
            r"<title[^>]*>(.*?)</title>",
        ],
    )
    intro = _first_match(
        text,
        [
            r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)[\"']",
            r"<div[^>]+class=[\"'][^\"']*(?:intro|desc|summary|content)[^\"']*[\"'][^>]*>(.*?)</div>",
            r"<p[^>]+class=[\"'][^\"']*(?:intro|desc|summary|content)[^\"']*[\"'][^>]*>(.*?)</p>",
        ],
    )
    cover = _first_match(
        text,
        [
            r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']",
            r"<img[^>]+class=[\"'][^\"']*(?:cover|thumb)[^\"']*[\"'][^>]+src=[\"']([^\"']+)[\"']",
        ],
    )
    cover_url = urllib.parse.urljoin(_BILIMANGA_BASE, cover) if cover else ""
    ch_pat = re.compile(
        r"<a[^>]+href=[\"']([^\"']*(?:chapter|read|viewer|play)[^\"']*)[\"'][^>]*>(.*?)</a>",
        re.I | re.S,
    )
    seen: set[str] = set()
    chs: list[TencentChapter] = []
    for href, label in ch_pat.findall(text):
        cu = urllib.parse.urljoin(_BILIMANGA_BASE, href)
        if "/detail/" in cu or "/search" in cu:
            continue
        if cu in seen:
            continue
        seen.add(cu)
        ct = _strip_tags(label)
        if not ct:
            continue
        chs.append(TencentChapter(title=ct, url=cu))
    return TencentComicDetail(
        comic_id=comic_id or "",
        title=title or f"漫画 {comic_id}",
        intro=intro,
        cover_url=cover_url,
        comic_url=u,
        chapters=chs,
    )


def _mangacopy_extract_items(text: str, *, source: str) -> list[TencentComicItem]:
    # 兼容常见详情页路径：/comic/<slug>、/comic/<id>、/detail/<id>
    pat = re.compile(
        r"<a[^>]+href=[\"']([^\"']*(?:/comic/[^\"'/\s?#]+|/detail/\d+)[^\"']*)[\"'][^>]*>(.*?)</a>",
        re.I | re.S,
    )
    seen: set[str] = set()
    out: list[TencentComicItem] = []
    for href, label in pat.findall(text):
        url = urllib.parse.urljoin(_MANGACOPY_BASE, href)
        m_slug = re.search(r"/comic/([^/?#]+)", url, flags=re.I)
        m_detail = re.search(r"/detail/(\d+)", url, flags=re.I)
        cid = (m_slug.group(1) if m_slug else "") or (m_detail.group(1) if m_detail else "")
        if not cid or cid in seen:
            continue
        title = _strip_tags(label)
        if not title:
            continue
        seen.add(cid)
        out.append(TencentComicItem(comic_id=cid, title=title, url=url, source=source))
    return out


def _mangacopy_hot_comics(*, limit: int = 240) -> list[TencentComicItem]:
    seeds = [
        _MANGACOPY_BASE,
        f"{_MANGACOPY_BASE}rank",
        f"{_MANGACOPY_BASE}comics",
        f"{_MANGACOPY_BASE}newest",
        f"{_MANGACOPY_BASE}discover",
        f"{_MANGACOPY_BASE}recommend",
    ]
    max_n = max(1, int(limit))
    seen: set[str] = set()
    out: list[TencentComicItem] = []
    for seed in seeds:
        try:
            raw = http_get(seed, timeout=20, headers=_common_html_headers(referer=_MANGACOPY_BASE))
            text = _decode_html(raw)
        except Exception:
            continue
        for it in _mangacopy_extract_items(text, source="hot"):
            if it.comic_id in seen:
                continue
            seen.add(it.comic_id)
            out.append(it)
            if len(out) >= max_n:
                return out
    if not out:
        raise BiqugeError("mangacopy 热门抓取为空")
    return out


def _mangacopy_search_comics(keyword: str, *, limit: int = 120) -> list[TencentComicItem]:
    kw = (keyword or "").strip()
    if not kw:
        return []
    q = urllib.parse.quote(kw, safe="")
    seeds = [
        f"{_MANGACOPY_BASE}search?keyword={q}",
        f"{_MANGACOPY_BASE}search?q={q}",
        f"{_MANGACOPY_BASE}search/{q}",
    ]
    seen: set[str] = set()
    out: list[TencentComicItem] = []
    for seed in seeds:
        try:
            raw = http_get(seed, timeout=20, headers=_common_html_headers(referer=_MANGACOPY_BASE))
            text = _decode_html(raw)
        except Exception:
            continue
        for it in _mangacopy_extract_items(text, source="search"):
            if it.comic_id in seen:
                continue
            seen.add(it.comic_id)
            out.append(it)
            if len(out) >= limit:
                return out
    if out:
        return out[:limit]
    # 搜索页参数可能受前端路由影响，回退到多页面本地索引过滤
    local_pool: list[TencentComicItem] = []
    for seed in (f"{_MANGACOPY_BASE}comics", f"{_MANGACOPY_BASE}newest", f"{_MANGACOPY_BASE}recommend", f"{_MANGACOPY_BASE}rank"):
        try:
            raw = http_get(seed, timeout=20, headers=_common_html_headers(referer=_MANGACOPY_BASE))
            text = _decode_html(raw)
            local_pool.extend(_mangacopy_extract_items(text, source="search"))
        except Exception:
            continue
    if not local_pool:
        local_pool = _mangacopy_hot_comics(limit=500)
    seen: set[str] = set()
    uniq_pool: list[TencentComicItem] = []
    for it in local_pool:
        if it.comic_id in seen:
            continue
        seen.add(it.comic_id)
        uniq_pool.append(it)
    q2 = kw.lower()
    return [x for x in uniq_pool if q2 in x.title.lower()][:limit]


def _mangacopy_comic_detail(comic_id_or_url: str) -> TencentComicDetail:
    s = str(comic_id_or_url or "").strip()
    if s.startswith("http://") or s.startswith("https://"):
        u = s
    else:
        sid = str(s).strip("/")
        u = f"{_MANGACOPY_BASE}comic/{sid}"
    raw = http_get(u, timeout=20, headers=_common_html_headers(referer=_MANGACOPY_BASE))
    text = _decode_html(raw)
    title = _first_match(
        text,
        [
            r"<h1[^>]*>(.*?)</h1>",
            r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)[\"']",
            r"<title[^>]*>(.*?)</title>",
        ],
    )
    intro = _first_match(
        text,
        [
            r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)[\"']",
            r"<p[^>]+class=[\"'][^\"']*(?:intro|desc|summary|content)[^\"']*[\"'][^>]*>(.*?)</p>",
            r"<div[^>]+class=[\"'][^\"']*(?:intro|desc|summary|content)[^\"']*[\"'][^>]*>(.*?)</div>",
        ],
    )
    cover = _first_match(
        text,
        [
            r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']",
            r"<img[^>]+src=[\"']([^\"']+)[\"'][^>]+class=[\"'][^\"']*(?:cover|thumb)[^\"']*[\"']",
        ],
    )
    cover_url = urllib.parse.urljoin(_MANGACOPY_BASE, cover) if cover else ""
    cid_m = re.search(r"/comic/([^/?#]+)", u, flags=re.I)
    comic_id = cid_m.group(1) if cid_m else re.sub(r"\W+", "", u)[-24:]
    ch_pat = re.compile(
        r"<a[^>]+href=[\"']([^\"']*(?:/chapter/|/comic/[^\"']*/chapter/|/reader/)[^\"']*)[\"'][^>]*>(.*?)</a>",
        re.I | re.S,
    )
    seen: set[str] = set()
    chs: list[TencentChapter] = []
    for href, label in ch_pat.findall(text):
        cu = urllib.parse.urljoin(_MANGACOPY_BASE, href)
        if cu in seen:
            continue
        seen.add(cu)
        ct = _strip_tags(label)
        if not ct:
            continue
        chs.append(TencentChapter(title=ct, url=cu))
    return TencentComicDetail(
        comic_id=comic_id,
        title=title or "漫画",
        intro=intro,
        cover_url=cover_url,
        comic_url=u,
        chapters=chs,
    )


def _mkzhan_hot_comics(*, limit: int = 240) -> list[TencentComicItem]:
    # 多入口聚合热门，尽可能多抓取并去重
    seeds = [
        "https://www.mkzhan.com/",
        "https://www.mkzhan.com/update/",
        "https://www.mkzhan.com/rank/",
        "https://www.mkzhan.com/top/",
    ]
    # 漫客栈漫画详情一般是 /<comic_id>/ 结构（支持相对/绝对链接）
    pat = re.compile(r"<a[^>]+href=[\"']([^\"']*/\d+/?)['\"][^>]*>(.*?)</a>", re.I | re.S)
    seen: set[str] = set()
    out: list[TencentComicItem] = []
    max_n = max(1, int(limit))
    for seed in seeds:
        try:
            raw = http_get(seed, timeout=20, headers={"Referer": "https://www.mkzhan.com/"})
            text = _decode_html(raw)
        except Exception:
            continue
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
            if len(out) >= max_n:
                return out
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


def source_hot_comics(source_url: str, *, limit: int = 240) -> list[TencentComicItem]:
    st = _source_type_from_url(source_url)
    if st == "mkzhan":
        return _mkzhan_hot_comics(limit=limit)
    if st == "bilimanga":
        return _bilimanga_hot_comics(limit=limit)
    if st == "mangacopy":
        return _mangacopy_hot_comics(limit=limit)
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
    if st == "bilimanga":
        return _bilimanga_search_comics(kw, limit=limit)
    if st == "mangacopy":
        return _mangacopy_search_comics(kw, limit=limit)
    raise BiqugeError("当前源不支持搜索")


def source_comic_detail(source_url: str, item: TencentComicItem) -> TencentComicDetail:
    st = _source_type_from_url(source_url)
    if st == "mkzhan":
        return _mkzhan_comic_detail(item.url or item.comic_id)
    if st == "bilimanga":
        return _bilimanga_comic_detail(item.url or item.comic_id)
    if st == "mangacopy":
        return _mangacopy_comic_detail(item.url or item.comic_id)
    raise BiqugeError("当前源不支持详情解析")


def source_chapter_image_urls(source_url: str, chapter_url: str) -> list[str]:
    st = _source_type_from_url(source_url)
    if st not in {"mkzhan", "bilimanga", "mangacopy"}:
        raise BiqugeError("当前源不支持章节图片解析")
    u = str(chapter_url or "").strip()
    m = re.search(r"mkzhan\.com/(\d+)/(\d+)/?", u, flags=re.I)
    if st == "mkzhan" and m:
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
    else:
        # bilimanga 及 mkzhan fallback：直接从章节页抽图
        referer = _BILIMANGA_BASE if st == "bilimanga" else (_MANGACOPY_BASE if st == "mangacopy" else u)
        raw = http_get(u, timeout=20, headers=_common_html_headers(referer=referer))
        text = _decode_html(raw)
        low = text.lower()
        if st == "bilimanga" and ("attention required! | cloudflare" in low or "sorry, you have been blocked" in low):
            raise BiqugeError("bilimanga 触发 Cloudflare 风控，暂无法直渲染章节")
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
    st = _source_type_from_url(source_url)
    if st == "bilimanga":
        return {
            "Referer": referer or _BILIMANGA_BASE,
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
        }
    if st == "mangacopy":
        return {
            "Referer": referer or _MANGACOPY_BASE,
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
        }
    return {"Referer": referer or "https://www.mkzhan.com/"}

