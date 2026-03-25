"""音乐：网易云热门/搜索与播放直链解析。"""

from .music import (
    MusicItem,
    NETEASE_HOT_PLAYLIST_ID,
    music_hot_fetch,
    music_search_songs,
    netease_song_play_url,
    netease_song_page_url,
)

__all__ = [
    "MusicItem",
    "NETEASE_HOT_PLAYLIST_ID",
    "music_hot_fetch",
    "music_search_songs",
    "netease_song_play_url",
    "netease_song_page_url",
]
