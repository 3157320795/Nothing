"""
使用系统 ffmpeg 将视频解码为 raw RGB，在 Tkinter Canvas 上逐帧显示。
不依赖 mpv --wid，在 macOS / Windows / Linux 上均为「同一窗口内」真内嵌。

说明：
- 需要 PATH 中可执行 `ffmpeg`。
- 需要 `pillow`（PIL）用于 RGB → PhotoImage。
- 同一 ffmpeg 进程双路输出：视频 → stdout rawvideo；音轨 → 系统音频设备（muxer 随平台/构建而定，
  如 macOS 常见 audiotoolbox，Linux 常见 pulse/alsa，Windows 常见 wasapi）。无音轨或设备打开失败时
  自动回退为仅画面。
- 帧队列满时丢弃最旧帧并立刻 put 新帧：若用阻塞 put 背压 ffmpeg，单进程里会同时卡住音轨输出，
  表现为播一段时间后声音消失。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from queue import Empty, Full, Queue
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

__all__ = ["FfmpegTkEmbedPlayer"]


def _probe_video_fps(*, url: str, headers: str, timeout: float = 25.0) -> float:
    """用 ffprobe 取视频流帧率；失败则返回 30.0。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 30.0
    try:
        p = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-headers",
                headers,
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        line = (p.stdout or "").strip().splitlines()
        if not line:
            return 30.0
        s = line[0].strip()
        if "/" in s:
            a, b = s.split("/", 1)
            fps = float(a) / float(b)
        else:
            fps = float(s)
        if fps < 1.0 or fps > 240.0:
            return 30.0
        return fps
    except Exception as e:
        logger.debug("ffprobe fps 失败，使用默认 30: %s", e)
        return 30.0


_OUTPUT_MUXERS: Optional[set[str]] = None


def _parse_output_muxer_names(text: str) -> set[str]:
    """解析 `ffmpeg -muxers` 输出中带 muxing(E) 的格式名。"""
    names: set[str] = set()
    for line in text.splitlines():
        if not line.startswith(" ") or "---" in line[:20]:
            continue
        if "D.. = " in line or line.strip() == "Formats:":
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        flags, name = parts[0], parts[1]
        if "E" in flags:
            names.add(name)
    return names


def _get_output_muxers(*, ffmpeg: str) -> set[str]:
    global _OUTPUT_MUXERS
    if _OUTPUT_MUXERS is not None:
        return _OUTPUT_MUXERS
    try:
        p = subprocess.run(
            [ffmpeg, "-hide_banner", "-muxers"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        blob = (p.stdout or "") + (p.stderr or "")
        _OUTPUT_MUXERS = _parse_output_muxer_names(blob)
    except Exception as e:
        logger.debug("枚举 ffmpeg muxer 失败: %s", e)
        _OUTPUT_MUXERS = set()
    return _OUTPUT_MUXERS


def _pick_audio_sink(*, ffmpeg: str) -> Optional[tuple[str, str]]:
    """
    返回 (format_name, output_arg) 供第二路输出使用，例如 ("audiotoolbox", "default")。
    若当前 ffmpeg/平台无可用的音频输出 muxer，返回 None。
    """
    muxers = _get_output_muxers(ffmpeg=ffmpeg)
    plat = sys.platform
    candidates: list[tuple[str, str]] = []
    if plat == "darwin":
        for fmt in ("audiotoolbox", "coreaudio"):
            if fmt in muxers:
                candidates.append((fmt, "default"))
    elif plat == "win32":
        if "wasapi" in muxers:
            candidates.append(("wasapi", "default"))
    elif plat.startswith("linux"):
        for fmt in ("pulse", "alsa"):
            if fmt in muxers:
                candidates.append((fmt, "default"))
    return candidates[0] if candidates else None


def _probe_has_audio(*, url: str, headers: str, timeout: float = 25.0) -> bool:
    """ffprobe 检测是否存在可解码音轨。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    try:
        p = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-headers",
                headers,
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return any(ch.isdigit() for ch in (p.stdout or ""))
    except Exception as e:
        logger.debug("ffprobe 音频检测失败，按无音轨处理: %s", e)
        return False


def _build_ffmpeg_cmd(
    *,
    ffmpeg: str,
    hdr: str,
    url: str,
    vf: str,
    with_audio_sink: Optional[tuple[str, str]],
) -> list[str]:
    """构造 ffmpeg 参数列表：一路 rawvideo → pipe:1，可选第二路系统音频。"""
    base = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rw_timeout",
        "15000000",
        "-headers",
        hdr,
        "-i",
        url,
        "-map",
        "0:v:0",
        "-fflags",
        "+genpts",
        "-vf",
        vf,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    if with_audio_sink is not None:
        fmt, out = with_audio_sink
        base.extend(
            [
                "-map",
                "0:a:0",
                "-f",
                fmt,
                out,
            ]
        )
    return base


def _popen_ffmpeg(cmd: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        bufsize=0,
    )


def _try_launch_ffmpeg(
    *,
    cmd_with_audio: Optional[list[str]],
    cmd_video_only: list[str],
) -> tuple[subprocess.Popen[bytes], bool]:
    """
    优先启动带音频的命令；若进程立刻退出则再试仅视频。
    返回 (proc, audio_active)。
    """
    attempts: list[tuple[list[str], bool]] = []
    if cmd_with_audio is not None:
        attempts.append((cmd_with_audio, True))
    attempts.append((cmd_video_only, False))

    last_exc: Optional[BaseException] = None
    for cmd, audio_on in attempts:
        try:
            proc = _popen_ffmpeg(cmd)
        except Exception as e:
            last_exc = e
            continue
        time.sleep(0.2)
        if proc.poll() is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=1)
            except Exception:
                pass
            continue
        return proc, audio_on

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("ffmpeg 未能保持运行")


class FfmpegTkEmbedPlayer:
    """在 Tk Canvas 上播放网络视频（ffmpeg rawvideo → PIL → PhotoImage）。"""

    def __init__(
        self,
        *,
        root: Any,
        canvas: Any,
        set_status: Callable[[str], None],
        on_stopped: Callable[[], None],
    ) -> None:
        self.root = root
        self.canvas = canvas
        self.set_status = set_status
        self.on_stopped = on_stopped
        self._stop = threading.Event()
        self._user_halt = False
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._after_id: Optional[str] = None
        self._reader: Optional[threading.Thread] = None
        self._queue: Queue[bytes] = Queue(maxsize=2)
        self._interval_ms = 33
        self._play_fps = 30.0
        self._photo_image: Any = None
        self._width = 0
        self._height = 0
        self._ended = False
        self._playing_with_audio = False

    def stop(self) -> None:
        """用户停止或切换视频时调用：不触发 on_stopped（由外层恢复 UI）。"""
        self._user_halt = True
        self._stop.set()
        # 读线程可能在 queue.put 上阻塞；清空队列以释放槽位，便于线程退出
        try:
            while True:
                self._queue.get_nowait()
        except Empty:
            pass
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=1.5)
        self._reader = None

    def start(self, *, url: str, referer: str, user_agent: str, width: int, height: int) -> bool:
        try:
            from PIL import Image, ImageTk
        except Exception:
            self.set_status("内嵌播放需要 pillow：pip install pillow")
            return False

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.set_status("内嵌播放需要系统已安装 ffmpeg（PATH 中可执行）")
            return False

        if width < 8 or height < 8:
            return False

        self.stop()
        self._user_halt = False
        self._ended = False
        self._stop = threading.Event()
        # Tk 在布局未完成时 winfo 可能为 1，若按 8×8 解码画面几乎不可见
        if width < 32 or height < 32:
            width = max(640, width)
            height = max(360, height)
        self._width = width
        self._height = height

        # 保持比例，不足部分黑边（与常见播放器一致）
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        )
        hdr = (
            f"Referer: {referer}\r\n"
            f"User-Agent: {user_agent}\r\n"
            f"Origin: https://www.bilibili.com\r\n"
        )

        fps = _probe_video_fps(url=url, headers=hdr)
        self._play_fps = fps
        # 按源视频帧率刷新 UI；原先用固定 16ms 会远快于 24/25fps 片源，且队列丢帧 = 快进感
        self._interval_ms = max(8, min(80, int(round(1000.0 / fps))))

        sink = _pick_audio_sink(ffmpeg=ffmpeg)
        has_audio_stream = _probe_has_audio(url=url, headers=hdr)
        cmd_video_only = _build_ffmpeg_cmd(
            ffmpeg=ffmpeg, hdr=hdr, url=url, vf=vf, with_audio_sink=None
        )
        cmd_with_audio: Optional[list[str]] = None
        if has_audio_stream and sink is not None:
            cmd_with_audio = _build_ffmpeg_cmd(
                ffmpeg=ffmpeg, hdr=hdr, url=url, vf=vf, with_audio_sink=sink
            )

        try:
            # 必须丢弃 stderr 或单独线程持续读取：若 PIPE 且不读，缓冲区满后 ffmpeg
            # 会阻塞，stdout 永远不出帧 → Canvas 一直黑屏。
            self._proc, self._playing_with_audio = _try_launch_ffmpeg(
                cmd_with_audio=cmd_with_audio,
                cmd_video_only=cmd_video_only,
            )
        except Exception as e:
            self.set_status(f"启动 ffmpeg 失败：{e}")
            return False

        assert self._proc.stdout is not None
        frame_size = width * height * 3
        # 每轮新开队列，避免 stop/start 残留旧帧
        self._queue = Queue(maxsize=2)

        def _read_exact(st: Any, n: int) -> bytes:
            """管道上 read(n) 可能只返回部分字节，必须拼满一帧再解码。"""
            buf = bytearray()
            while len(buf) < n and not self._stop.is_set():
                chunk = st.read(n - len(buf))
                if not chunk:
                    break
                buf += chunk
            return bytes(buf)

        def _enqueue_frame_drop_oldest(raw: bytes) -> None:
            """
            队列满时丢最旧帧再入队。若用阻塞 put 背压读线程，ffmpeg 无法继续写 pipe:1，
            单进程双输出时整路解码停滞，音轨也无法送入设备，缓冲耗尽后声音会消失。
            """
            while True:
                try:
                    self._queue.put_nowait(raw)
                    return
                except Full:
                    try:
                        self._queue.get_nowait()
                    except Empty:
                        pass

        def _read_loop() -> None:
            """尽快从 stdout 读满帧并入队；显示侧按帧率取帧，跟不上时丢帧而非阻塞 ffmpeg。"""
            try:
                while not self._stop.is_set() and self._proc and self._proc.stdout:
                    raw = _read_exact(self._proc.stdout, frame_size)
                    if len(raw) < frame_size:
                        break
                    if self._stop.is_set():
                        break
                    _enqueue_frame_drop_oldest(raw)
            finally:
                pass

        self._reader = threading.Thread(target=_read_loop, daemon=True)
        self._reader.start()

        _au = "有声" if self._playing_with_audio else "仅画面"
        self.set_status(
            f"正在缓冲…（内嵌·ffmpeg·{_au}，约 {fps:.1f}fps / {self._interval_ms}ms 每帧）"
        )
        # 闭包内引用 ImageTk，供 _tick 使用
        self._Image = Image
        self._ImageTk = ImageTk
        self._first_frame_ok = False
        self._stall_ticks = 0
        self._stall_msged = False
        self._after_id = self.root.after(0, self._tick)
        return True

    def _tick(self) -> None:
        if self._user_halt or self._ended:
            return

        width, height = self._width, self._height
        frame_size = width * height * 3

        try:
            data = self._queue.get_nowait()
        except Empty:
            # 解码线程尚未产出帧，或已播完
            if self._proc is not None and self._proc.poll() is not None:
                self._natural_end()
                return
            self._stall_ticks += 1
            # 约 5s 仍无首帧提示一次（按 interval 折算 tick 次数）
            stall_limit = max(80, int(5000 / max(getattr(self, "_interval_ms", 16), 1)))
            if (
                not self._first_frame_ok
                and self._stall_ticks > stall_limit
                and not self._stall_msged
            ):
                self.set_status("长时间无画面：网络缓冲慢或流异常，可重试或检查 URL")
                self._stall_msged = True
            try:
                self.root.update_idletasks()
            except Exception:
                pass
            iv = getattr(self, "_interval_ms", 16)
            self._after_id = self.root.after(iv, self._tick)
            return

        self._stall_ticks = 0

        if len(data) != frame_size:
            iv = getattr(self, "_interval_ms", 16)
            self._after_id = self.root.after(iv, self._tick)
            return

        try:
            self.root.update_idletasks()
            img = self._Image.frombytes("RGB", (width, height), data)
            self._photo_image = self._ImageTk.PhotoImage(image=img)
            self.canvas.delete("all")
            # 用当前 Canvas 几何中心放置画面（布局未稳定时先 update_idletasks）
            try:
                cw = max(1, int(self.canvas.winfo_width()))
                ch = max(1, int(self.canvas.winfo_height()))
            except Exception:
                cw, ch = width, height
            self.canvas.create_image(
                cw // 2,
                ch // 2,
                image=self._photo_image,
                anchor="center",
            )
            if not self._first_frame_ok:
                self._first_frame_ok = True
                _au = "有声" if self._playing_with_audio else "仅画面"
                self.set_status(
                    f"播放中（内嵌·ffmpeg·{_au}，约 {self._play_fps:.1f} fps）"
                )
        except Exception as e:
            logger.exception("ffmpeg 内嵌渲染一帧失败: %s", e)
            self.set_status(f"画面渲染失败：{e!s}")

        iv = getattr(self, "_interval_ms", 16)
        self._after_id = self.root.after(iv, self._tick)

    def _natural_end(self) -> None:
        if self._ended:
            return
        self._ended = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

        code: int | None = None
        proc = self._proc
        if proc is not None:
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            code = getattr(proc, "returncode", None)
        self._proc = None

        if code not in (None, 0):
            self.set_status(f"ffmpeg 异常退出，code={code}（stderr 已丢弃以避免管道死锁，调试可改源码为 PIPE+读线程）")
        else:
            self.set_status("播放结束")
        try:
            self.on_stopped()
        except Exception:
            pass
