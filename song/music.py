from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from typing import Any

from src.common import BiqugeError, http_get

# 网易云官方「热歌榜」歌单 id（api/toplist 可查）
NETEASE_HOT_PLAYLIST_ID = 3778678

NETEASE_HEADERS = {
    "Referer": "https://music.163.com/",
    "Accept": "application/json, text/plain, */*",
}


@dataclass(frozen=True)
class MusicItem:
    """一首可展示/可尝试播放的歌曲。"""

    song_id: int
    title: str
    artist: str
    pic_url: str
    album: str
    # 搜索接口常省略专辑封面 URL，可用专辑 id 走 api/v1/album 补图
    album_id: int = 0


def _normalize_pic_url(pic: str) -> str:
    pic = (pic or "").strip()
    if not pic:
        return ""
    if pic.startswith("//"):
        return "https:" + pic
    if pic.startswith("http://"):
        return "https://" + pic[len("http://") :]
    return pic


def _robust_json_loads(raw: bytes, *, context: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "strict"))
    except Exception as e:
        raise BiqugeError(f"JSON 解析失败: {context}: {e!r}") from e


def _item_from_v6_track(t: dict[str, Any]) -> MusicItem | None:
    try:
        sid = int(t.get("id") or 0)
    except Exception:
        return None
    if sid <= 0:
        return None
    title = str(t.get("name") or "").strip()
    ar_list = t.get("ar") or []
    artist = str(ar_list[0].get("name") or "").strip() if ar_list else ""
    al = t.get("al") or {}
    album = str(al.get("name") or "").strip()
    pic = _normalize_pic_url(str(al.get("picUrl") or ""))
    if not title:
        return None
    try:
        al_id = int(al.get("id") or 0)
    except Exception:
        al_id = 0
    return MusicItem(
        song_id=sid,
        title=title,
        artist=artist or "未知歌手",
        pic_url=pic,
        album=album,
        album_id=al_id,
    )


def _item_from_search_song(s: dict[str, Any]) -> MusicItem | None:
    try:
        sid = int(s.get("id") or 0)
    except Exception:
        return None
    if sid <= 0:
        return None
    title = str(s.get("name") or "").strip()
    artists = s.get("artists") or []
    artist = str(artists[0].get("name") or "").strip() if artists else ""
    album_obj = s.get("album") or {}
    album = str(album_obj.get("name") or "").strip()
    pic = _normalize_pic_url(
        str(album_obj.get("picUrl") or album_obj.get("blurPicUrl") or "")
    )
    try:
        al_id = int(album_obj.get("id") or 0)
    except Exception:
        al_id = 0
    if not title:
        return None
    return MusicItem(
        song_id=sid,
        title=title,
        artist=artist or "未知歌手",
        pic_url=pic,
        album=album,
        album_id=al_id,
    )


def _fetch_album_cover_urls(album_ids: set[int]) -> dict[int, str]:
    """通过 api/v1/album 批量补全专辑封面（搜索单曲里 album 往往不带 picUrl）。"""
    out: dict[int, str] = {}
    for aid in album_ids:
        if aid <= 0:
            continue
        try:
            url = f"https://music.163.com/api/v1/album/{int(aid)}"
            raw = http_get(url, headers=NETEASE_HEADERS, timeout=18)
            payload = _robust_json_loads(raw, context="album_cover")
            if int(payload.get("code", -1)) != 200:
                continue
            al = payload.get("album") or {}
            p = _normalize_pic_url(str(al.get("picUrl") or al.get("blurPicUrl") or ""))
            if p:
                out[int(aid)] = p
        except Exception:
            continue
    return out


def _enrich_search_items_with_covers(items: list[MusicItem]) -> list[MusicItem]:
    missing = {it.album_id for it in items if it.album_id > 0 and not (it.pic_url or "").strip()}
    if not missing:
        return items
    pic_map = _fetch_album_cover_urls(missing)
    if not pic_map:
        return items
    rebuilt: list[MusicItem] = []
    for it in items:
        pu = (it.pic_url or "").strip()
        if not pu and it.album_id and it.album_id in pic_map:
            rebuilt.append(
                MusicItem(
                    song_id=it.song_id,
                    title=it.title,
                    artist=it.artist,
                    pic_url=pic_map[it.album_id],
                    album=it.album,
                    album_id=it.album_id,
                )
            )
        else:
            rebuilt.append(it)
    return rebuilt


def _fetch_playlist_chunk(
    *,
    playlist_id: int,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    """单次 v6 playlist/detail；实测单次最多返回约 10 条，limit 再大也不会多给。"""
    qs = urllib.parse.urlencode(
        {
            "id": playlist_id,
            "limit": max(1, min(limit, 50)),
            "offset": max(0, offset),
        }
    )
    url = f"https://music.163.com/api/v6/playlist/detail?{qs}"
    raw = http_get(url, headers=NETEASE_HEADERS, timeout=25)
    payload = _robust_json_loads(raw, context="hot_playlist")

    if int(payload.get("code", -1)) != 200:
        raise BiqugeError(f"热歌接口错误 code={payload.get('code')}, message={payload.get('message')}")

    pl = payload.get("playlist") or {}
    tracks = pl.get("tracks") or []
    if not isinstance(tracks, list):
        return []
    return [t for t in tracks if isinstance(t, dict)]


def music_hot_fetch(
    *,
    page: int = 1,
    page_size: int = 20,
    playlist_id: int = NETEASE_HOT_PLAYLIST_ID,
) -> list[MusicItem]:
    """
    热歌榜分页（网易云 v6 playlist/detail）。
    接口单次约返回 10 条，本函数在 page_size 较大时会自动连续请求拼接。
    """

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20

    base_off = (page - 1) * page_size
    out: list[MusicItem] = []
    cur_offset = base_off
    # 单次最多约 10 条有效数据，按步进 10 拉取直到凑满 page_size 或无更多
    while len(out) < page_size:
        chunk = _fetch_playlist_chunk(playlist_id=playlist_id, offset=cur_offset, limit=10)
        if not chunk:
            break
        for t in chunk:
            it = _item_from_v6_track(t)
            if it is not None:
                out.append(it)
            if len(out) >= page_size:
                break
        if len(chunk) < 10:
            break
        cur_offset += len(chunk)

    return out[:page_size]


def music_search_songs(
    *,
    keyword: str,
    page: int = 1,
    page_size: int = 20,
) -> list[MusicItem]:
    """单曲搜索（web search）。"""

    keyword = (keyword or "").strip()
    if not keyword:
        return []

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    offset = (page - 1) * page_size

    qs = urllib.parse.urlencode(
        {
            "s": keyword,
            "type": 1,
            "limit": page_size,
            "offset": offset,
        }
    )
    url = f"https://music.163.com/api/search/get/web?{qs}"
    raw = http_get(url, headers=NETEASE_HEADERS, timeout=25)
    payload = _robust_json_loads(raw, context="search")

    if int(payload.get("code", -1)) != 200:
        raise BiqugeError(f"搜索接口错误 code={payload.get('code')}, message={payload.get('message')}")

    result = payload.get("result") or {}
    songs = result.get("songs") or []
    if not isinstance(songs, list):
        return []

    out: list[MusicItem] = []
    for s in songs:
        if not isinstance(s, dict):
            continue
        it = _item_from_search_song(s)
        if it is not None:
            out.append(it)
    return _enrich_search_items_with_covers(out)


def netease_song_play_url(song_id: int, *, br: int = 999000) -> str | None:
    """
    解析网易云可播放直链（mp3）。部分版权/VIP 曲目可能返回 None。
    """

    if song_id <= 0:
        return None
    q = urllib.parse.urlencode({"id": song_id, "ids": f"[{song_id}]", "br": int(br)})
    url = f"https://music.163.com/api/song/enhance/player/url?{q}"
    raw = http_get(url, headers=NETEASE_HEADERS, timeout=25)
    payload = _robust_json_loads(raw, context="player_url")
    if int(payload.get("code", -1)) != 200:
        return None
    data = payload.get("data") or []
    if not data or not isinstance(data, list):
        return None
    first = data[0] if isinstance(data[0], dict) else {}
    u = str(first.get("url") or "").strip()
    return u or None


def netease_song_page_url(song_id: int) -> str:
    """网页试听（直链不可用时兜底）。"""

    return f"https://music.163.com/#/song?id={int(song_id)}"


__all__ = [
    "MusicItem",
    "NETEASE_HOT_PLAYLIST_ID",
    "music_hot_fetch",
    "music_search_songs",
    "netease_song_play_url",
    "netease_song_page_url",
]
