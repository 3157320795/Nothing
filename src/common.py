from __future__ import annotations

import re
import signal
import urllib.request
from typing import Any

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


# 避免 `| head` 时刷 stdout 触发 BrokenPipe 噪音
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except Exception:
    pass


class BiqugeError(RuntimeError):
    """抓取/解析失败的统一异常类型。"""


_INVALID_PATH_CHARS = re.compile(r"[\\/:*?\"<>|\n\r\t]+")


def safe_path_part(name: str, *, fallback: str = "unknown") -> str:
    """把任意字符串清洗为文件夹名片段。"""

    s = _INVALID_PATH_CHARS.sub("_", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s or fallback


def http_get(url: str, *, timeout: float = 20, headers: dict[str, str] | None = None) -> bytes:
    hdrs: dict[str, str] = {"User-Agent": DEFAULT_UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


__all__ = ["DEFAULT_UA", "BiqugeError", "safe_path_part", "http_get"]

