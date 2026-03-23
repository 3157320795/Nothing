from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

from src.common import BiqugeError, http_get
from src.common import DEFAULT_UA


@dataclass(frozen=True)
class SearchItem:
    url_list: str
    url_img: str
    articlename: str
    author: str
    intro: str

    @property
    def abs_url_list(self) -> str:
        return urllib.parse.urljoin("https://m.bqgl.cc", self.url_list)

    def guess_book_id(self) -> str | None:
        # 示例: https://www.bqgl.cc/bookimg/29/29041.jpg  -> 29041
        m = re.search(r"/(\d+)\.(?:jpg|png|webp)(?:\?|$)", self.url_img, re.I)
        return m.group(1) if m else None


@dataclass(frozen=True)
class ApiBookItem:
    id: str
    title: str
    author: str
    intro: str


@dataclass(frozen=True)
class XiunewsSearchItem:
    title: str
    book_url: str
    latest_chapter: str
    latest_chapter_url: str
    author: str
    words: str
    update_time: str
    status: str


@dataclass(frozen=True)
class XiunewsChapterRef:
    idx: int
    title: str
    url: str


def search_novel(name: str) -> list[SearchItem]:
    """
    调用: https://m.bqgl.cc/user/search.html?q=<urlencoded>
    返回: 搜索结果列表（与站点返回 JSON 对齐）
    """

    q = urllib.parse.quote(name, safe="")
    url = f"https://m.bqgl.cc/user/search.html?q={q}"
    raw = http_get(url)

    try:
        payload = json.loads(raw.decode("utf-8", "strict"))
    except Exception as e:  # noqa: BLE001
        text = raw.decode("utf-8", "ignore")
        raise BiqugeError(f"搜索接口未返回 JSON，响应前200字符: {text[:200]!r}") from e

    if not isinstance(payload, list):
        raise BiqugeError(f"搜索接口返回非 list: {type(payload).__name__}")

    items: list[SearchItem] = []
    for obj in payload:
        if not isinstance(obj, dict):
            continue
        try:
            items.append(
                SearchItem(
                    url_list=str(obj.get("url_list", "")),
                    url_img=str(obj.get("url_img", "")),
                    articlename=str(obj.get("articlename", "")),
                    author=str(obj.get("author", "")),
                    intro=str(obj.get("intro", "")),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return items


def _decode_html_bytes(raw: bytes) -> str:
    # xiunews 页面主要是 gbk，兜底 utf-8，避免硬崩。
    for enc in ("gbk", "gb18030", "utf-8"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def _strip_tags(s: str) -> str:
    s = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.I | re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>", "", s, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def _html_to_text(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<p\b[^>]*>", "", s, flags=re.I)
    s = _strip_tags(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _extract_div_by_id(html_text: str, target_id: str) -> str:
    """
    提取指定 id 的 <div> 内部 HTML，支持嵌套 div，避免正则在第一个 </div> 提前截断。
    """

    start_m = re.search(
        rf"<div\b[^>]*\bid\s*=\s*(['\"])"
        rf"{re.escape(target_id)}\1[^>]*>",
        html_text,
        flags=re.I,
    )
    if not start_m:
        return ""

    i = start_m.end()
    n = len(html_text)
    depth = 1
    tag_pat = re.compile(r"<(/?)div\b[^>]*>", flags=re.I)
    while i < n:
        m = tag_pat.search(html_text, i)
        if not m:
            break
        if m.group(1):
            depth -= 1
            if depth == 0:
                return html_text[start_m.end() : m.start()]
        else:
            depth += 1
        i = m.end()
    return ""


def xiunews_search(name: str) -> list[XiunewsSearchItem]:
    q = urllib.parse.quote(name, safe="")
    url = f"http://www.xiunews.com/modules/article/search.php?searchkey={q}"
    raw = http_get(url, headers={"Referer": "http://www.xiunews.com/"})
    text = _decode_html_bytes(raw)

    m_grid = re.search(r"<table[^>]+class=[\"']grid[\"'][^>]*>(.*?)</table>", text, flags=re.I | re.S)
    if not m_grid:
        return []

    table_html = m_grid.group(1)
    row_matches = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.I | re.S)

    out: list[XiunewsSearchItem] = []
    for row in row_matches:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.I | re.S)
        if len(tds) < 6:
            continue

        m_title = re.search(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", tds[0], flags=re.I | re.S)
        if not m_title:
            continue
        book_url = urllib.parse.urljoin("http://www.xiunews.com", m_title.group(1).strip())
        title = _strip_tags(m_title.group(2))

        m_latest = re.search(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", tds[1], flags=re.I | re.S)
        latest_url = urllib.parse.urljoin("http://www.xiunews.com", m_latest.group(1).strip()) if m_latest else ""
        latest_title = _strip_tags(m_latest.group(2)) if m_latest else ""

        out.append(
            XiunewsSearchItem(
                title=title,
                book_url=book_url,
                latest_chapter=latest_title,
                latest_chapter_url=latest_url,
                author=_strip_tags(tds[2]),
                words=_strip_tags(tds[3]),
                update_time=_strip_tags(tds[4]),
                status=_strip_tags(tds[5]),
            )
        )
    return out


def xiunews_parse_toc(book_url: str) -> list[XiunewsChapterRef]:
    raw = http_get(book_url, headers={"Referer": "http://www.xiunews.com/"})
    text = _decode_html_bytes(raw)

    m_list = re.search(r"<div[^>]+id=[\"']list[\"'][^>]*>(.*?)</div>\s*</div>", text, flags=re.I | re.S)
    if not m_list:
        return []
    list_html = m_list.group(1)

    m_dl = re.search(r"<dl[^>]*>(.*?)</dl>", list_html, flags=re.I | re.S)
    if not m_dl:
        return []
    dl_html = m_dl.group(1)

    blocks = re.findall(r"<(dt|dd)\b[^>]*>(.*?)</\1>", dl_html, flags=re.I | re.S)
    in_body = False
    idx = 0
    chapters: list[XiunewsChapterRef] = []
    for tag, inner in blocks:
        tag_lower = tag.lower()
        if tag_lower == "dt":
            dt_text = _strip_tags(inner)
            if "正文" in dt_text:
                in_body = True
            elif "最新章节" in dt_text:
                in_body = False
            continue
        if not in_body:
            continue

        m_a = re.search(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", inner, flags=re.I | re.S)
        if not m_a:
            continue
        href = urllib.parse.urljoin(book_url, m_a.group(1).strip())
        title = _strip_tags(m_a.group(2))
        if not title:
            continue
        idx += 1
        chapters.append(XiunewsChapterRef(idx=idx, title=title, url=href))
    return chapters


def xiunews_fetch_chapter(chapter_url: str) -> dict[str, Any]:
    raw = http_get(chapter_url, headers={"Referer": chapter_url})
    text = _decode_html_bytes(raw)

    m_title = re.search(r"<h1[^>]*>(.*?)</h1>", text, flags=re.I | re.S)
    title = _strip_tags(m_title.group(1)) if m_title else ""

    content_html = _extract_div_by_id(text, "content")
    txt = _html_to_text(content_html) if content_html else ""
    txt = txt.replace("笔趣阁", "").strip()

    return {
        "title": title,
        "chaptername": title,
        "txt": txt,
        "url": chapter_url,
    }


def apibi_search(name: str) -> list[ApiBookItem]:
    """
    调用: https://apibi.cc/api/search?q=...
    返回: {"data":[{id,title,author,intro},...], "title":"搜索结果"}
    """

    q = urllib.parse.quote(name, safe="")
    url = f"https://apibi.cc/api/search?q={q}"
    raw = http_get(url, headers={"Accept": "application/json"})
    try:
        payload = json.loads(raw.decode("utf-8", "strict"))
    except Exception as e:  # noqa: BLE001
        text = raw.decode("utf-8", "ignore")
        raise BiqugeError(f"apibi 搜索未返回 JSON，响应前200字符: {text[:200]!r}") from e

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []

    out: list[ApiBookItem] = []
    for obj in data:
        if not isinstance(obj, dict):
            continue
        bid = str(obj.get("id", "")).strip()
        title = str(obj.get("title", "")).strip()
        if not bid or not title:
            continue
        out.append(
            ApiBookItem(
                id=bid,
                title=title,
                author=str(obj.get("author", "")).strip(),
                intro=str(obj.get("intro", "")).strip(),
            )
        )
    return out


def fetch_chapter_bqg(book_id: str, chapterid: int = 1, *, host: str = "https://b31b6920f096faf37.bqg810.cc") -> dict[str, Any]:
    """
    通过 bqg810 的 /api/chapter 拉取内容（/book/{id}/{chapter}.html 实际由 JS 调用该接口渲染）。
    """

    url = f"{host}/api/chapter?id={urllib.parse.quote(str(book_id))}&chapterid={chapterid}"
    raw = http_get(url, headers={"Accept": "application/json"})
    try:
        payload = json.loads(raw.decode("utf-8", "strict"))
    except Exception as e:  # noqa: BLE001
        text = raw.decode("utf-8", "ignore")
        raise BiqugeError(f"bqg 章节接口未返回 JSON，响应前200字符: {text[:200]!r}") from e
    if not isinstance(payload, dict):
        raise BiqugeError(f"bqg 章节接口返回非 dict: {type(payload).__name__}")
    return payload


def resolve_book_id(item: SearchItem, *, prefer_redirect: bool = True) -> str:
    """
    目标: 获取 apibi 的 id（例如 29041）

    站点 `.../list.html` 可能触发验证页，因此这里做两段式:
    - 优先尝试 list.html 重定向后 URL 中解析 `#/book/<id>/`
    - 失败则从 `url_img` 兜底提取 `<id>.jpg`
    """

    if prefer_redirect:
        list_url = urllib.parse.urljoin(item.abs_url_list.rstrip("/") + "/", "list.html")
        try:
            # 不读完整内容也会跟随重定向到最终 URL
            req = urllib.request.Request(list_url, headers={"User-Agent": DEFAULT_UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                final_url = resp.geturl()
            m = re.search(r"#/book/(\d+)/", final_url)
            if m:
                return m.group(1)
        except Exception:
            pass

    guessed = item.guess_book_id()
    if guessed:
        return guessed
    raise BiqugeError("无法解析 book id：list.html 被拦截且 url_img 不含可解析 id")


def fetch_chapter(book_id: str, chapterid: int = 1) -> dict[str, Any]:
    url = f"https://apibi.cc/api/chapter?id={urllib.parse.quote(str(book_id))}&chapterid={chapterid}"
    raw = http_get(url, headers={"Accept": "application/json"})
    try:
        payload = json.loads(raw.decode("utf-8", "strict"))
    except Exception as e:  # noqa: BLE001
        text = raw.decode("utf-8", "ignore")
        raise BiqugeError(f"章节接口未返回 JSON，响应前200字符: {text[:200]!r}") from e
    if not isinstance(payload, dict):
        raise BiqugeError(f"章节接口返回非 dict: {type(payload).__name__}")
    return payload


def format_search_results(items: Iterable[SearchItem]) -> str:
    lines: list[str] = []
    for i, it in enumerate(items, start=1):
        title = it.articlename.strip() or "(无标题)"
        author = it.author.strip() or "(未知作者)"
        lines.append(f"{i}. {title} / {author}  {it.url_list}")
    return "\n".join(lines)


__all__ = [
    "BiqugeError",
    "DEFAULT_UA",
    "SearchItem",
    "ApiBookItem",
    "XiunewsSearchItem",
    "XiunewsChapterRef",
    "format_search_results",
    "search_novel",
    "xiunews_search",
    "xiunews_parse_toc",
    "xiunews_fetch_chapter",
    "apibi_search",
    "fetch_chapter_bqg",
    "resolve_book_id",
    "fetch_chapter",
]

