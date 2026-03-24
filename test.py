from __future__ import annotations

import argparse
import html
import json
import re
import urllib.parse
import urllib.request


API_URL = "https://api.bilibili.com/x/web-interface/search/all/v2"


def _normalize_image_url(pic: str) -> str:
    pic = (pic or "").strip()
    if not pic:
        return ""
    if pic.startswith("//"):
        return "https:" + pic
    return pic


def _clean_title(raw_title: str) -> str:
    # 返回里标题可能包含 <em class="keyword">，这里去掉标签并做 HTML 反转义。
    text = re.sub(r"<[^>]+>", "", raw_title or "")
    return html.unescape(text).strip()


def fetch_bilibili_videos(keyword: str, page: int = 1, page_size: int = 42) -> list[dict[str, str]]:
    params = {
        "__refresh__": "true",
        "_extra": "",
        "context": "",
        "page": max(1, page),
        "page_size": max(1, page_size),
        "order": "",
        "pubtime_begin_s": 0,
        "pubtime_end_s": 0,
        "duration": "",
        "from_source": "",
        "from_spmid": "333.337",
        "platform": "pc",
        "highlight": 1,
        "single_column": 0,
        "keyword": keyword,
        "ad_resource": "5646",
        "source_tag": "3",
        "web_roll_page": 1,
    }
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{API_URL}?{query}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Referer": "https://search.bilibili.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8", "ignore"))

    if int(payload.get("code", -1)) != 0:
        raise RuntimeError(f"接口返回异常: code={payload.get('code')} message={payload.get('message')}")

    result_blocks = ((payload.get("data") or {}).get("result")) or []
    videos: list[dict[str, str]] = []

    for block in result_blocks:
        if not isinstance(block, dict):
            continue
        if block.get("result_type") != "video":
            continue

        for item in block.get("data") or []:
            if not isinstance(item, dict):
                continue

            title = _clean_title(str(item.get("title") or ""))
            image_url = _normalize_image_url(str(item.get("pic") or ""))
            url = str(item.get("arcurl") or "").strip()
            if not url:
                bvid = str(item.get("bvid") or "").strip()
                if bvid:
                    url = f"https://www.bilibili.com/video/{bvid}"

            videos.append(
                {
                    "image_url": image_url,
                    "title": title,
                    "url": url,
                }
            )
        break

    return videos


def main() -> None:
    parser = argparse.ArgumentParser(description="B站搜索抓取：打印 video 结果的 image_url/title/url")
    parser.add_argument("keyword", nargs="?", default="大哥", help="搜索关键词，默认：大哥")
    parser.add_argument("--page", type=int, default=1, help="页码，默认 1")
    parser.add_argument("--page-size", type=int, default=42, help="每页数量，默认 42")
    args = parser.parse_args()

    rows = fetch_bilibili_videos(keyword=args.keyword, page=args.page, page_size=args.page_size)
    print(f"keyword={args.keyword} 命中 video 条数: {len(rows)}")
    for idx, row in enumerate(rows, start=1):
        print(f"{idx:02d}. image_url: {row['image_url']}")
        print(f"    title: {row['title']}")
        print(f"    url: {row['url']}")


if __name__ == "__main__":
    main()
