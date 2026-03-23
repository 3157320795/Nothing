from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import aiohttp

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

B23_HOST = "b23.tv"
BV_RE = re.compile(r"BV[0-9A-Za-z]{10,}")
EP_PATH_RE = re.compile(r"/bangumi/play/ep(\d+)")
EP_QS_RE = re.compile(r"(?:^|[?&])ep_id=(\d+)")


async def expand_b23(url: str, session: aiohttp.ClientSession) -> str:
    if urlparse(url).netloc.lower() == B23_HOST:
        async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return str(r.url)
    return url


def extract_p(url: str) -> int:
    try:
        return int(parse_qs(urlparse(url).query).get("p", ["1"])[0])
    except Exception:
        return 1


def detect_target(url: str) -> Tuple[Optional[str], Dict[str, str]]:
    m = EP_PATH_RE.search(url) or EP_QS_RE.search(url)
    if m:
        return "pgc", {"ep_id": m.group(1)}
    m = BV_RE.search(url)
    if m:
        return "ugc", {"bvid": m.group(0)}
    return None, {}


# ------- 基础信息（标题/简介/作者） -------
async def get_ugc_info(bvid: str, session: aiohttp.ClientSession) -> Dict[str, str]:
    api = "https://api.bilibili.com/x/web-interface/view"
    async with session.get(api, params={"bvid": bvid}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        j = await resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"view error: {j.get('code')} {j.get('message')}")
    data = j["data"]
    title = data.get("title") or ""
    desc = data.get("desc") or ""
    owner = data.get("owner") or {}
    name = owner.get("name") or ""
    mid = owner.get("mid")
    author = f"{name}(uid:{mid})" if name else ""
    return {"title": title, "desc": desc, "author": author}


async def get_pgc_info_by_ep(ep_id: str, session: aiohttp.ClientSession) -> Dict[str, str]:
    api = "https://api.bilibili.com/pgc/view/web/season"
    async with session.get(api, params={"ep_id": ep_id}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        j = await resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"pgc season view error: {j.get('code')} {j.get('message')}")
    result = j.get("result") or j.get("data") or {}
    episodes = result.get("episodes") or []
    ep_obj = None
    for e in episodes:
        if str(e.get("ep_id")) == str(ep_id):
            ep_obj = e
            break
    title = ""
    if ep_obj:
        title = ep_obj.get("share_copy") or ep_obj.get("long_title") or ep_obj.get("title") or ""
    if not title:
        title = result.get("season_title") or result.get("title") or ""
    desc = result.get("evaluate") or result.get("summary") or ""

    name, mid = "", None
    up_info = result.get("up_info") or result.get("upInfo") or {}
    if isinstance(up_info, dict):
        name = up_info.get("name") or ""
        mid = up_info.get("mid") or up_info.get("uid")
    if not name:
        pub = result.get("publisher") or {}
        name = pub.get("name") or ""
        mid = pub.get("mid") or mid

    author = f"{name}({mid})" if name else (result.get("season_title") or result.get("title") or "")
    return {"title": title, "desc": desc, "author": author}


# ------- 分P与取流 -------
async def get_pagelist(bvid: str, session: aiohttp.ClientSession):
    api = "https://api.bilibili.com/x/player/pagelist"
    async with session.get(api, params={"bvid": bvid, "jsonp": "json"}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        j = await resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"pagelist error: {j.get('code')} {j.get('message')}")
    return j["data"]


async def ugc_playurl(
    bvid: str,
    cid: int,
    qn: int,
    fnval: int,
    referer: str,
    session: aiohttp.ClientSession,
):
    api = "https://api.bilibili.com/x/player/playurl"
    params = {
        "bvid": bvid,
        "cid": cid,
        "qn": qn,
        "fnver": 0,
        "fnval": fnval,
        "fourk": 1,
        "otype": "json",
        "platform": "html5",
        "high_quality": 1,
    }
    headers = {"User-Agent": UA, "Referer": referer, "Origin": "https://www.bilibili.com"}
    async with session.get(api, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        j = await resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"playurl error: {j.get('code')} {j.get('message')}")
    return j["data"]


async def pgc_playurl_v2(
    ep_id: str,
    qn: int,
    fnval: int,
    referer: str,
    session: aiohttp.ClientSession,
):
    api = "https://api.bilibili.com/pgc/player/web/v2/playurl"
    params = {"ep_id": ep_id, "qn": qn, "fnver": 0, "fnval": fnval, "fourk": 1, "otype": "json"}
    headers = {"User-Agent": UA, "Referer": referer, "Origin": "https://www.bilibili.com"}
    async with session.get(api, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        j = await resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"pgc playurl v2 error: {j.get('code')} {j.get('message')}")
    return j.get("result") or j.get("data") or j


def best_qn_from_data(data: Dict[str, Any]) -> Optional[int]:
    aq = data.get("accept_quality") or []
    if isinstance(aq, list) and aq:
        try:
            return max(int(x) for x in aq)
        except Exception:
            pass
    dash = data.get("dash") or {}
    if dash.get("video"):
        try:
            return max(int(v.get("id", 0)) for v in dash["video"])
        except Exception:
            pass
    return None


def pick_best_video(dash_obj: Dict[str, Any]):
    vids = dash_obj.get("video") or []
    if not vids:
        return None
    return sorted(vids, key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)), reverse=True)[0]


# ------- 入口：输出固定字段 -------
async def parse_bilibili_minimal(url: str, p: Optional[int] = None) -> Dict[str, str]:
    """
    解析 B 站链接，尽量拿到直链（`direct_url`）。

    返回字段：
    - video_url：原始/重定向后的页面链接
    - author / title / desc
    - direct_url：直链（可能是 m3u8 或 mp4/m4s 等）
    """

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(headers={"User-Agent": UA}, timeout=timeout) as session:
        page_url = await expand_b23(url, session)
        p_index = max(1, int(p or extract_p(page_url)))
        vtype, ident = detect_target(page_url)
        if not vtype:
            raise ValueError("无法从链接中识别 BV 或 ep_id")

        FNVAL_MAX = 4048
        if vtype == "ugc":
            bvid = ident["bvid"]
            info = await get_ugc_info(bvid, session)
            pages = await get_pagelist(bvid, session)
            if p_index > len(pages):
                raise IndexError(f"P 超出范围(总 P={len(pages)})")
            cid = pages[p_index - 1]["cid"]

            probe = await ugc_playurl(bvid, cid, qn=120, fnval=FNVAL_MAX, referer=page_url, session=session)
            target_qn = best_qn_from_data(probe) or probe.get("quality") or 80

            merged_try = await ugc_playurl(bvid, cid, qn=target_qn, fnval=0, referer=page_url, session=session)
            if merged_try.get("durl"):
                direct_url = merged_try["durl"][0].get("url")
            else:
                dash_try = await ugc_playurl(
                    bvid, cid, qn=target_qn, fnval=FNVAL_MAX, referer=page_url, session=session
                )
                v = pick_best_video(dash_try.get("dash") or {})
                direct_url = (v.get("baseUrl") or v.get("base_url")) if v else ""
        else:
            ep_id = ident["ep_id"]
            info = await get_pgc_info_by_ep(ep_id, session)
            probe = await pgc_playurl_v2(ep_id, qn=120, fnval=FNVAL_MAX, referer=page_url, session=session)
            target_qn = best_qn_from_data(probe) or probe.get("quality") or 80

            merged_try = await pgc_playurl_v2(ep_id, qn=target_qn, fnval=0, referer=page_url, session=session)
            if merged_try.get("durl"):
                direct_url = merged_try["durl"][0].get("url")
            else:
                dash_try = await pgc_playurl_v2(
                    ep_id, qn=target_qn, fnval=FNVAL_MAX, referer=page_url, session=session
                )
                v = pick_best_video(dash_try.get("dash") or {})
                direct_url = (v.get("baseUrl") or v.get("base_url")) if v else ""

        return {
            "video_url": page_url,
            "author": info["author"],
            "title": info["title"],
            "desc": info["desc"],
            "direct_url": direct_url or "",
        }


__all__ = ["parse_bilibili_minimal", "UA"]

