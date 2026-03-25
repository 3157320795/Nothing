from __future__ import annotations

from pathlib import Path
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import shutil
import sys
from typing import Any
import webbrowser

from src.common import http_get
from fund.api import fundgz_fetch, fund_history_fetch
from novel.api import (
    ApiBookItem,
    BiqugeError,
    SearchItem,
    XiunewsSearchItem,
    XiunewsChapterRef,
    apibi_search,
    fetch_chapter,
    fetch_chapter_bqg,
    format_search_results,
    resolve_book_id,
    search_novel,
    xiunews_fetch_chapter,
    xiunews_parse_toc,
    xiunews_search,
)
from saved.saver import save_chapter_to_disk
from video.video import VideoItem, bilibili_hot_videos_fetch, bilibili_search_videos
from video.bilibili_direct import parse_bilibili_minimal, UA
from video.ffmpeg_tk_embed import FfmpegTkEmbedPlayer
from song.music import MusicItem, music_hot_fetch, music_search_songs, netease_song_play_url, netease_song_page_url


def gui_app(*, out_dir: Path, prefer_redirect: bool = True, save: bool = True, theme: str = "night") -> int:
    try:
        import tkinter as tk
        import tkinter.colorchooser as colorchooser
        import tkinter.font as tkfont
        from tkinter import ttk
    except Exception as e:  # noqa: BLE001
        print(f"GUI 依赖 tkinter 不可用: {e}")
        return 2

    try:
        from log import setup_runtime_logging

        setup_runtime_logging()
    except Exception:
        pass

    logging.getLogger(__name__).info(
        "GUI 启动 out_dir=%s prefer_redirect=%s save=%s",
        out_dir,
        prefer_redirect,
        save,
    )

    root = tk.Tk()
    root.title("小说下载/阅读")
    root.geometry("1200x780")

    is_day_theme = str(theme or "").strip().lower() == "day"
    if is_day_theme:
        # 白天模式：在当前黑底白字的基础上做全局反色。
        UI_BG = "#FFFFFF"
        UI_FG = "#181818"
        UI_ACTIVE_BG = "#FFFFFF"
        UI_SEL_BG = "#E6E6E6"
        UI_SEL_FG = "#181818"
    else:
        UI_BG = "#181818"
        UI_FG = "#FFFFFF"
        UI_ACTIVE_BG = "#181818"
        UI_SEL_BG = "#2A2A2A"
        UI_SEL_FG = "#FFFFFF"

    style_holder: dict[str, Any] = {"style": None}

    def _set_ui_theme(mode: str) -> None:
        nonlocal UI_BG, UI_FG, UI_ACTIVE_BG, UI_SEL_BG, UI_SEL_FG
        if str(mode or "").strip().lower() == "day":
            UI_BG = "#FFFFFF"
            UI_FG = "#181818"
            UI_ACTIVE_BG = "#FFFFFF"
            UI_SEL_BG = "#E6E6E6"
            UI_SEL_FG = "#181818"
        else:
            UI_BG = "#181818"
            UI_FG = "#FFFFFF"
            UI_ACTIVE_BG = "#181818"
            UI_SEL_BG = "#2A2A2A"
            UI_SEL_FG = "#FFFFFF"

    def _apply_ttk_theme() -> None:
        style = style_holder.get("style")
        if style is None:
            return
        try:
            style.configure(".", background=UI_BG, foreground=UI_FG)
            style.configure("TFrame", background=UI_BG)
            style.configure("TLabel", background=UI_BG, foreground=UI_FG)
            style.configure("TNotebook", background=UI_BG, borderwidth=0)
            style.configure("TNotebook.Tab", background=UI_BG, foreground=UI_FG, padding=[10, 6], borderwidth=0)
            style.map(
                "TNotebook.Tab",
                background=[("selected", UI_BG), ("active", UI_BG)],
                foreground=[("selected", UI_FG), ("active", UI_FG)],
            )
            style.configure("TButton", background=UI_BG, foreground=UI_FG, borderwidth=0, focusthickness=0)
            style.map(
                "TButton",
                background=[("active", UI_BG), ("pressed", UI_BG)],
                foreground=[("active", UI_FG), ("pressed", UI_FG)],
            )
            style.configure(
                "Treeview",
                background=UI_BG,
                foreground=UI_FG,
                fieldbackground=UI_BG,
                borderwidth=0,
                rowheight=24,
            )
            style.configure("Treeview.Heading", background=UI_BG, foreground=UI_FG)
            style.map(
                "Treeview",
                background=[("selected", UI_SEL_BG)],
                foreground=[("selected", UI_SEL_FG)],
            )
        except Exception:
            pass

    def _apply_theme_to_widget_tree(widget: Any) -> None:
        cls = str(widget.winfo_class())
        try:
            if cls in {"Frame", "Toplevel", "Panedwindow", "Canvas"}:
                widget.configure(bg=UI_BG)
            elif cls == "Label":
                widget.configure(bg=UI_BG, fg=UI_FG)
            elif cls in {"Entry", "Spinbox"}:
                widget.configure(
                    bg=UI_BG,
                    fg=UI_FG,
                    insertbackground=UI_FG,
                    highlightbackground=UI_BG,
                    highlightcolor=UI_BG,
                )
            elif cls == "Text":
                widget.configure(bg=UI_BG, fg=UI_FG, insertbackground=UI_FG)
            elif cls == "Listbox":
                widget.configure(
                    bg=UI_BG,
                    fg=UI_FG,
                    selectbackground=UI_SEL_BG,
                    selectforeground=UI_SEL_FG,
                    highlightbackground=UI_BG,
                    highlightcolor=UI_BG,
                )
            elif cls == "Scale":
                widget.configure(
                    bg=UI_BG,
                    fg=UI_FG,
                    activebackground=UI_BG,
                    troughcolor=UI_BG,
                    highlightthickness=0,
                )
        except Exception:
            pass
        for child in widget.winfo_children():
            _apply_theme_to_widget_tree(child)

    def _apply_theme_to_all(theme_mode: str) -> None:
        _set_ui_theme(theme_mode)
        try:
            root.configure(bg=UI_BG)
        except Exception:
            pass
        _apply_ttk_theme()
        _apply_theme_to_widget_tree(root)

    root.configure(bg=UI_BG)
    # 普通模式也启用无边框（自绘移动/缩放）
    # macOS 下有时需要在窗口初始化后再设置一次，确保系统标题栏不会回来
    # macOS/Tk 环境下（偶发）重复 overrideredirect 时把回调参数解析错
    # 这里改为 after + lambda：避免 Tcl 把 after 返回值当成参数（macOS 偶发）。
    # macOS/Tk 偶发地把 after 返回值传入 overrideredirect 参数（导致 TclError）。
    # 这里改为同步设置：初始化后立即生效。
    try:
        root.update_idletasks()
        root.overrideredirect(True)
    except Exception:
        pass

    # ====== 无边框窗口：拖动/缩放 ======
    MOVE_AREA_H = 34
    RESIZE_PAD = 10
    # 最低尺寸（允许更小高度，避免“不能缩小窗口”）
    MIN_W, MIN_H = 520, 220
    _win_state: dict[str, Any] = {"mode": None, "start": None, "geo": None}
    try:
        root.minsize(MIN_W, MIN_H)
    except Exception:
        pass

    def _parse_geom(g: str) -> tuple[int, int, int, int]:
        # "1200x780+10+20"
        m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", g)
        if not m:
            # 兜底：取当前值
            w = max(MIN_W, root.winfo_width() or 0)
            h = max(MIN_H, root.winfo_height() or 0)
            return w, h, root.winfo_x(), root.winfo_y()
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

    def _start_action(mode: str, e: Any) -> None:
        # 在对角缩放时，必须以“按下瞬间”的窗口几何为准，否则中途读取会跳变
        root.update_idletasks()
        _win_state["mode"] = mode
        _win_state["start"] = (e.x_root, e.y_root)
        try:
            geo = root.wm_geometry()
        except Exception:
            geo = root.geometry()
        _win_state["geo"] = _parse_geom(geo)

    def _on_motion(e: Any) -> None:
        mode = _win_state.get("mode")
        if not mode:
            return
        sx, sy = _win_state["start"]
        w, h, x, y = _win_state["geo"]
        dx = e.x_root - sx
        dy = e.y_root - sy

        if mode == "move":
            root.geometry(f"{w}x{h}+{x + dx}+{y + dy}")
            return

        # resize modes: n,s,e,w,ne,nw,se,sw
        new_w, new_h, new_x, new_y = w, h, x, y
        if "e" in mode:
            new_w = max(MIN_W, w + dx)
        if "s" in mode:
            new_h = max(MIN_H, h + dy)
        if "w" in mode:
            new_w = max(MIN_W, w - dx)
            new_x = x + (w - new_w)
        if "n" in mode:
            new_h = max(MIN_H, h - dy)
            new_y = y + (h - new_h)

        root.geometry(f"{new_w}x{new_h}+{new_x}+{new_y}")

    def _end_action(_e: Any) -> None:
        _win_state["mode"] = None
        _win_state["start"] = None
        _win_state["geo"] = None

    def _set_cursor(cur: str) -> None:
        try:
            root.configure(cursor=cur)
        except Exception:
            try:
                root.configure(cursor="arrow")
            except Exception:
                pass

    def _cursor_for_mode(mode: str) -> str:
        # macOS Tk 不支持 size_nw_se/size_ne_sw，这里用更通用的 cursor 名称
        return {
            "n": "sb_v_double_arrow",
            "s": "sb_v_double_arrow",
            "e": "sb_h_double_arrow",
            "w": "sb_h_double_arrow",
            # 对角缩放游标在各平台名字不一致，统一回退为角落游标
            "nw": "top_left_corner",
            "ne": "top_right_corner",
            "sw": "bottom_left_corner",
            "se": "bottom_right_corner",
        }.get(mode, "arrow")

    def _hit_test(e: Any) -> str | None:
        # 在沉浸层也支持缩放：以窗口边缘判断
        # 注意：事件可能来自子控件，e.x/e.y 是“子控件坐标系”，会导致命中判断失真。
        # 用屏幕坐标换算到 root 窗口坐标，避免缩放时高度/宽度被算错（出现“一条线”）。
        try:
            rx = root.winfo_rootx()
            ry = root.winfo_rooty()
            x = int(e.x_root - rx)
            y = int(e.y_root - ry)
        except Exception:
            x = getattr(e, "x", 0)
            y = getattr(e, "y", 0)
        ww = max(1, root.winfo_width())
        hh = max(1, root.winfo_height())
        left = x <= RESIZE_PAD
        right = x >= ww - RESIZE_PAD
        top = y <= RESIZE_PAD
        bottom = y >= hh - RESIZE_PAD

        if top and left:
            return "nw"
        if top and right:
            return "ne"
        if bottom and left:
            return "sw"
        if bottom and right:
            return "se"
        if top:
            return "n"
        if bottom:
            return "s"
        if left:
            return "w"
        if right:
            return "e"
        return None

    def _global_press(e: Any) -> None:
        # 如果点在边缘：缩放；否则在顶部区域：移动（沉浸时也允许）
        mode = _hit_test(e)
        if mode:
            _set_cursor(_cursor_for_mode(mode))
            _start_action(mode, e)
            return
        # 同样用窗口坐标判断是否在顶部拖动区
        try:
            ry = root.winfo_rooty()
            y = int(e.y_root - ry)
        except Exception:
            y = getattr(e, "y", 0)
        if y <= MOVE_AREA_H:
            _set_cursor("fleur")
            _start_action("move", e)

    def _global_move(e: Any) -> None:
        # 若正在操作，执行；否则仅更新 cursor
        if _win_state.get("mode"):
            _on_motion(e)
            return
        mode = _hit_test(e)
        if mode:
            _set_cursor(_cursor_for_mode(mode))
        else:
            try:
                ry = root.winfo_rooty()
                y = int(e.y_root - ry)
            except Exception:
                y = getattr(e, "y", 0)
            if y <= MOVE_AREA_H:
                _set_cursor("fleur")
            else:
                _set_cursor("arrow")

    def _global_release(e: Any) -> None:
        _end_action(e)
        _set_cursor("arrow")

    root.bind("<ButtonPress-1>", _global_press, add=True)
    root.bind("<B1-Motion>", _global_move, add=True)
    root.bind("<ButtonRelease-1>", _global_release, add=True)
    root.bind("<Motion>", _global_move, add=True)

    # 退出按钮（无系统边框时需要自己提供）
    def _close_app() -> None:
        # 先停掉外部播放器子进程（mpv/ffplay/ffmpeg），再销毁 GUI。
        try:
            _stop_embedded_video(restore_cover=False)
        except Exception:
            pass
        try:
            _stop_music_audio()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    topbar = tk.Frame(root, bg=UI_BG, bd=0, highlightthickness=0, height=MOVE_AREA_H)
    topbar.place(x=0, y=0, relwidth=1.0, height=MOVE_AREA_H)
    tk.Label(topbar, text="小说下载/阅读", bg=UI_BG, fg=UI_FG).pack(side=tk.LEFT, padx=10)
    # macOS 下 tk.Button 可能忽略 bg 显示为白色，这里用 Label 自绘按钮保证背景一致
    close_btn = tk.Label(
        topbar,
        text="×",
        bg=UI_BG,
        fg=UI_FG,
        padx=12,
        pady=6,
        bd=1,
        relief="solid",
        highlightthickness=0,
    )
    close_btn.pack(side=tk.RIGHT, padx=6)
    # 主题开关固定在关闭按钮左侧，显示为滑动开关（亮/暗）
    theme_switch = tk.Canvas(
        topbar,
        width=78,
        height=24,
        bg=UI_BG,
        bd=0,
        highlightthickness=0,
    )
    theme_switch.pack(side=tk.RIGHT, padx=(0, 8))

    def _render_theme_switch(*, hover: bool = False) -> None:
        try:
            theme_switch.delete("all")
        except Exception:
            return
        mode = str(app_state.get("theme_mode") or "night").lower()
        is_night = mode == "night"
        track_bg = "#3A3A3A" if is_night else "#D9D9D9"
        if hover:
            track_bg = "#4A4A4A" if is_night else "#CFCFCF"
        knob_fill = "#FFFFFF" if is_night else "#181818"
        text_color = "#FFFFFF" if is_night else "#181818"

        theme_switch.create_rectangle(1, 1, 77, 23, outline=UI_FG, width=1, fill=track_bg)
        if is_night:
            theme_switch.create_oval(53, 3, 73, 21, fill=knob_fill, outline=knob_fill)
            theme_switch.create_text(20, 12, text="暗", fill=text_color, font=("TkDefaultFont", 10, "bold"))
        else:
            theme_switch.create_oval(5, 3, 25, 21, fill=knob_fill, outline=knob_fill)
            theme_switch.create_text(58, 12, text="亮", fill=text_color, font=("TkDefaultFont", 10, "bold"))

    def _close_hover(_e: Any) -> None:
        close_btn.configure(bg=UI_SEL_BG, fg=UI_SEL_FG)

    def _close_leave(_e: Any) -> None:
        close_btn.configure(bg=UI_BG, fg=UI_FG)

    def _theme_hover(_e: Any) -> None:
        _render_theme_switch(hover=True)

    def _theme_leave(_e: Any) -> None:
        _render_theme_switch(hover=False)

    def _close_click(_e: Any) -> None:
        _close_app()

    def _toggle_theme_click(_e: Any = None) -> None:
        cur = str(app_state.get("theme_mode") or "night").lower()
        nxt = "day" if cur == "night" else "night"
        app_state["theme_mode"] = nxt
        _apply_theme_to_all(nxt)
        try:
            _redraw_fund_history_if_loaded()
        except Exception:
            pass
        _render_theme_switch()
        try:
            set_status("已切换主题：" + ("白天" if nxt == "day" else "黑夜"))
        except Exception:
            pass

    close_btn.bind("<Enter>", _close_hover)
    close_btn.bind("<Leave>", _close_leave)
    close_btn.bind("<Button-1>", _close_click)
    theme_switch.bind("<Enter>", _theme_hover)
    theme_switch.bind("<Leave>", _theme_leave)
    theme_switch.bind("<Button-1>", _toggle_theme_click)

    def _make_flat_button(parent: Any, text: str, command: Any) -> Any:
        """
        使用 Label 自绘按钮，避免 macOS 下 tk.Button 忽略 bg 导致白底。
        """
        w = tk.Label(
            parent,
            text=text,
            bg=UI_BG,
            fg=UI_FG,
            padx=10,
            pady=6,
            bd=1,
            relief="solid",
            highlightthickness=0,
        )

        def _enter(_e: Any) -> None:
            w.configure(bg=UI_SEL_BG, fg=UI_SEL_FG)

        def _leave(_e: Any) -> None:
            w.configure(bg=UI_BG, fg=UI_FG)

        def _click(_e: Any) -> None:
            try:
                command()
            except Exception:
                pass

        w.bind("<Enter>", _enter)
        w.bind("<Leave>", _leave)
        w.bind("<Button-1>", _click)
        return w

    # 给主内容区域留出顶部拖动栏空间
    def _apply_padding_for_topbar(widget: Any) -> None:
        try:
            widget.pack_configure(pady=(MOVE_AREA_H, 0))
        except Exception:
            pass

    app_state: dict[str, Any] = {
        "search_items": [],
        "out_dir": out_dir,
        "prefer_redirect": prefer_redirect,
        "save": save,
        "crawl_cancel": False,
        "home_channel": "bqg1",
        # 基金 Tab 状态：每次新增一条，且可移除；每分钟刷新一次展示内容
        "fund_items": [],
        "fund_uid_counter": 1,
        "fund_refresh_job": None,
        "fund_refresh_running": False,
        "theme_mode": "day" if is_day_theme else "night",
    }

    # ======================
    # 基金配置本地持久化
    # ======================
    fund_store_dir = Path(__file__).resolve().parent.parent / "out" / "fund"
    fund_store_file = fund_store_dir / "fund_items.json"
    # 兼容：历史版本可能把配置落在 `Nothing/fund/fund_items.json`
    old_fund_store_file = Path(__file__).resolve().parent.parent / "fund" / "fund_items.json"

    def _fund_load_items_from_disk() -> tuple[list[dict[str, Any]], int]:
        if not fund_store_file.exists():
            # 首次启动时把旧配置迁移到新目录
            try:
                if old_fund_store_file.exists():
                    fund_store_dir.mkdir(parents=True, exist_ok=True)
                    fund_store_file.write_text(old_fund_store_file.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass

        if not fund_store_file.exists():
            return [], 1
        try:
            raw = fund_store_file.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            return [], 1

        if not isinstance(payload, list):
            return [], 1

        items: list[dict[str, Any]] = []
        max_uid = 0
        for obj in payload:
            if not isinstance(obj, dict):
                continue
            try:
                uid = int(obj.get("uid"))
                fundcode = str(obj.get("fundcode") or "").strip()
                shares = float(obj.get("shares") if obj.get("shares") is not None else 0.0)
            except Exception:
                continue
            if not fundcode:
                continue
            max_uid = max(max_uid, uid)
            items.append({"uid": uid, "fundcode": fundcode, "shares": shares, "latest": {}})
        return items, (max_uid + 1 if max_uid > 0 else 1)

    def _fund_save_items_to_disk() -> None:
        # 保存“新增/移除/清空”的配置：uid + code + shares
        # latest（净值）每分钟动态刷新，不需要落盘。
        try:
            fund_store_dir.mkdir(parents=True, exist_ok=True)
            payload: list[dict[str, Any]] = []
            for it in app_state.get("fund_items") or []:
                if not isinstance(it, dict):
                    continue
                uid = int(it.get("uid"))
                fundcode = str(it.get("fundcode") or "").strip()
                shares = float(it.get("shares") or 0.0)
                if not fundcode:
                    continue
                payload.append({"uid": uid, "fundcode": fundcode, "shares": shares})

            tmp = fund_store_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, fund_store_file)
        except Exception:
            # GUI 不因保存失败而崩
            pass

    # GUI 启动时加载本地基金配置
    loaded_items, next_uid = _fund_load_items_from_disk()
    if loaded_items:
        app_state["fund_items"] = loaded_items
        app_state["fund_uid_counter"] = next_uid

    def set_root_alpha(v: float) -> None:
        try:
            root.attributes("-alpha", float(v))
        except Exception:
            pass

    # ====== 顶部状态栏 ======
    status_var = tk.StringVar(value=f"保存目录: {out_dir}")
    status_bar = tk.Label(root, textvariable=status_var, anchor="w", bg=UI_BG, fg=UI_FG, bd=0, highlightthickness=0)
    status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def set_status(msg: str) -> None:
        status_var.set(msg)
        root.update_idletasks()

    # ====== Tab 容器： 首页 / 阅读 ======
    # macOS 的默认 ttk 主题常会强制浅色（Tab 区域/按钮变白）
    # 这里切到 clam 并把常用 ttk 样式统一覆盖为 #181818 + 白字
    try:
        style = ttk.Style()
        style_holder["style"] = style
        try:
            style.theme_use("clam")
        except Exception:
            style.theme_use(style.theme_use())

        style.configure(".", background=UI_BG, foreground=UI_FG)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabel", background=UI_BG, foreground=UI_FG)
        style.configure("TNotebook", background=UI_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=UI_BG, foreground=UI_FG, padding=[10, 6], borderwidth=0)
        style.map(
            "TNotebook.Tab",
            background=[("selected", UI_BG), ("active", UI_BG)],
            foreground=[("selected", UI_FG), ("active", UI_FG)],
        )
        style.configure("TButton", background=UI_BG, foreground=UI_FG, borderwidth=0, focusthickness=0)
        style.map(
            "TButton",
            background=[("active", UI_BG), ("pressed", UI_BG)],
            foreground=[("active", UI_FG), ("pressed", UI_FG)],
        )
    except Exception:
        pass

    notebook = ttk.Notebook(root)
    notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
    _apply_padding_for_topbar(notebook)

    home = tk.Frame(notebook, bg=UI_BG, bd=0, highlightthickness=0)
    reader_tab = tk.Frame(notebook, bg=UI_BG, bd=0, highlightthickness=0)
    fund_tab = tk.Frame(notebook, bg=UI_BG, bd=0, highlightthickness=0)
    video_tab = tk.Frame(notebook, bg=UI_BG, bd=0, highlightthickness=0)
    music_tab = tk.Frame(notebook, bg=UI_BG, bd=0, highlightthickness=0)
    notebook.add(home, text="首页")
    notebook.add(reader_tab, text="阅读")
    notebook.add(fund_tab, text="基金")
    notebook.add(video_tab, text="视频")
    notebook.add(music_tab, text="音乐")

    # ------------------------------------------------------------------
    # 基金：输入基金代码 + 持有份额，点击“新增”获取净值信息
    # ------------------------------------------------------------------
    fund_top = tk.Frame(fund_tab, bg=UI_BG, bd=0, highlightthickness=0)
    fund_top.pack(side=tk.TOP, fill=tk.X, padx=12, pady=12)

    tk.Label(fund_top, text="基金代码:", bg=UI_BG, fg=UI_FG).pack(side=tk.LEFT)
    fund_code_entry = tk.Entry(
        fund_top,
        width=12,
        bg=UI_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        bd=0,
        highlightthickness=0,
    )
    fund_code_entry.pack(side=tk.LEFT, padx=(8, 12))
    fund_code_entry.insert(0, "")

    tk.Label(fund_top, text="持有份额:", bg=UI_BG, fg=UI_FG).pack(side=tk.LEFT)
    fund_shares_entry = tk.Entry(
        fund_top,
        width=12,
        bg=UI_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        bd=0,
        highlightthickness=0,
    )
    fund_shares_entry.pack(side=tk.LEFT, padx=(8, 12))
    fund_shares_entry.insert(0, "0")

    fund_btns = tk.Frame(fund_top, bg=UI_BG, bd=0, highlightthickness=0)
    fund_btns.pack(side=tk.LEFT, padx=(12, 0))

    # 透明度控制：右侧滑块，参考首页模块设计
    fund_alpha_frame = tk.Frame(fund_top, bg=UI_BG, bd=0, highlightthickness=0)
    fund_alpha_frame.pack(side=tk.RIGHT, padx=(0, 8))
    tk.Label(fund_alpha_frame, text="透明度:", bg=UI_BG, fg=UI_FG).pack(side=tk.LEFT)
    fund_alpha = tk.DoubleVar(value=1.0)
    tk.Scale(
        fund_alpha_frame,
        from_=0.15,
        to=1.0,
        resolution=0.05,
        orient=tk.HORIZONTAL,
        variable=fund_alpha,
        command=lambda _v: set_root_alpha(fund_alpha.get()),
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        highlightthickness=0,
        troughcolor=UI_BG,
    ).pack(side=tk.LEFT, padx=(8, 0))
    fund_list_frame = tk.Frame(fund_tab, bg=UI_BG, bd=0, highlightthickness=0)
    fund_list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=False, padx=12, pady=(0, 12))

    # ----------- 基金列表（表格）-----------
    # value: 当前金额=份额*最新净值
    # profit: 预估收益=份额*实时净值-当前金额
    fund_cols = ["code", "name", "dwjz", "jzrq", "gsz", "gztime", "gszzl", "shares", "value", "profit"]
    try:
        style.configure(
            "Treeview",
            background=UI_BG,
            foreground=UI_FG,
            fieldbackground=UI_BG,
            borderwidth=0,
            rowheight=24,
        )
        style.configure("Treeview.Heading", background=UI_BG, foreground=UI_FG)
        style.map(
            "Treeview",
            background=[("selected", UI_SEL_BG)],
            foreground=[("selected", UI_SEL_FG)],
        )
    except Exception:
        pass

    fund_tree = ttk.Treeview(
        fund_list_frame,
        columns=fund_cols,
        show="headings",
        selectmode="browse",
        height=10,
    )
    fund_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    fund_tree.heading("code", text="代码")
    fund_tree.heading("name", text="名称")
    fund_tree.heading("dwjz", text="最新净值")
    fund_tree.heading("jzrq", text="净值日")
    fund_tree.heading("gsz", text="实时净值")
    fund_tree.heading("gztime", text="时间")
    fund_tree.heading("gszzl", text="涨跌幅")
    fund_tree.heading("shares", text="份额")
    fund_tree.heading("value", text="当前金额")
    fund_tree.heading("profit", text="预估收益")

    # 列宽尽量适配窗口；不追求像素级，保证信息可读
    fund_tree.column("code", width=70, anchor="center")
    fund_tree.column("name", width=220, anchor="w")
    fund_tree.column("dwjz", width=80, anchor="e")
    fund_tree.column("jzrq", width=90, anchor="center")
    fund_tree.column("gsz", width=80, anchor="e")
    fund_tree.column("gztime", width=130, anchor="center")
    fund_tree.column("gszzl", width=80, anchor="center")
    fund_tree.column("shares", width=70, anchor="e")
    fund_tree.column("value", width=120, anchor="e")
    fund_tree.column("profit", width=140, anchor="e")

    # ----------- 历史净值折线图展示（嵌入基金页下方）-----------
    fund_history_frame = tk.Frame(fund_tab, bg=UI_BG, bd=0, highlightthickness=0)
    fund_history_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

    fund_history_header = tk.Label(
        fund_history_frame,
        text="历史净值折线图：点击“历史净值”按钮加载数据（点击点位显示详细数据）",
        bg=UI_BG,
        fg=UI_FG,
    )
    fund_history_header.pack(side=tk.TOP, anchor="w", padx=0, pady=(8, 4))

    fund_history_status_var = tk.StringVar(value="等待加载...")
    fund_history_status = tk.Label(fund_history_frame, textvariable=fund_history_status_var, bg=UI_BG, fg=UI_FG)
    fund_history_status.pack(side=tk.TOP, anchor="w", padx=0, pady=(0, 6))

    fund_history_canvas = tk.Canvas(fund_history_frame, bg=UI_BG, highlightthickness=0)
    fund_history_canvas.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 6))

    fund_history_holder: dict[str, Any] = {"code": "", "points": []}  # points: (FSRQ, DWJZ, JZZZL)

    def _to_float(s: Any) -> float:
        try:
            return float(str(s).strip())
        except Exception:
            return 0.0

    def _get_fund_item_by_uid(uid: int) -> dict[str, Any] | None:
        for it in app_state.get("fund_items") or []:
            if int(it.get("uid")) == int(uid):
                return it
        return None

    def _fund_update_row(uid: int) -> None:
        item = _get_fund_item_by_uid(uid)
        if not item:
            return

        latest = item.get("latest") or {}
        fundcode = str(item.get("fundcode") or "").strip()
        shares = item.get("shares") or 0.0

        if latest.get("error"):
            fund_tree.item(
                str(uid),
                values=[fundcode, latest.get("name") or "", "—", "", "—", "", "获取失败", shares, "—", "—"],
            )
            return

        name = str(latest.get("name") or "")
        dwjz = str(latest.get("dwjz") or "")
        jzrq = str(latest.get("jzrq") or "")
        gsz = str(latest.get("gsz") or "")
        gztime = str(latest.get("gztime") or "")
        gszzl = str(latest.get("gszzl") or "")

        have_dwjz = bool(dwjz.strip())
        have_gsz = bool(gsz.strip())

        dwjz_f = _to_float(dwjz) if have_dwjz else 0.0
        gsz_f = _to_float(gsz) if have_gsz else 0.0

        # 当前金额：份额 * 最新净值
        current_amount = float(shares) * float(dwjz_f) if have_dwjz else 0.0
        current_amount_s = f"{current_amount:.4f}" if have_dwjz and shares else ""

        # 预估收益：份额*实时净值 - 当前金额
        if have_gsz and have_dwjz:
            estimated_profit = float(shares) * float(gsz_f) - current_amount
            profit_s = f"{estimated_profit:.4f}" if shares else ""
        else:
            profit_s = ""

        fund_tree.item(
            str(uid),
            values=[fundcode, name, dwjz, jzrq, gsz, gztime, gszzl, shares, current_amount_s or "—", profit_s or "—"],
        )

    def _fund_add_row(item: dict[str, Any]) -> None:
        uid = int(item.get("uid"))
        fundcode = str(item.get("fundcode") or "").strip()
        shares = item.get("shares") or 0.0
        fund_tree.insert(
            "",
            tk.END,
            iid=str(uid),
            values=[fundcode, "", "—", "", "—", "", "", shares, "—", "—"],
        )

    def _fund_render_all() -> None:
        fund_tree.delete(*fund_tree.get_children())
        for it in app_state.get("fund_items") or []:
            _fund_add_row(it)

    def fund_add_button() -> None:
        fc = fund_code_entry.get().strip()
        shares_s = fund_shares_entry.get().strip()

        if not fc:
            set_status("请输入基金代码（6位数字）")
            return

        try:
            shares = float(shares_s) if shares_s else 0.0
        except Exception:
            set_status("持有份额请输入数字")
            return

        uid = int(app_state.get("fund_uid_counter") or 1)
        app_state["fund_uid_counter"] = uid + 1
        item = {"uid": uid, "fundcode": fc, "shares": shares, "latest": {}}
        app_state.setdefault("fund_items", []).append(item)

        _fund_add_row(item)
        _fund_update_row(uid)  # 显示“等待/空”

        _fund_save_items_to_disk()

        set_status(f"已新增基金 {fc}，开始获取...")

        # 若列表此前为空，定时器可能停止；此处确保重新开始刷新
        if app_state.get("fund_refresh_job") is None:
            app_state["fund_refresh_job"] = root.after(60_000, _fund_refresh_tick)

        def worker_one() -> None:
            try:
                payload = fundgz_fetch(fc)
                latest = {
                    "name": str(payload.get("name") or ""),
                    "dwjz": str(payload.get("dwjz") or ""),
                    "gsz": str(payload.get("gsz") or ""),
                    "gszzl": str(payload.get("gszzl") or ""),
                    "jzrq": str(payload.get("jzrq") or ""),
                    "gztime": str(payload.get("gztime") or ""),
                }

                def _apply() -> None:
                    item2 = _get_fund_item_by_uid(uid)
                    if not item2:
                        return
                    item2["latest"] = latest
                    _fund_update_row(uid)
                    set_status(f"基金 {fc} 获取完成")

                root.after(0, _apply)
            except Exception as e:  # noqa: BLE001
                err_msg = str(e)

                def _apply_err() -> None:
                    item2 = _get_fund_item_by_uid(uid)
                    if not item2:
                        return
                    item2["latest"] = {"error": err_msg}
                    _fund_update_row(uid)
                    set_status(f"基金获取失败: {err_msg}")

                root.after(0, _apply_err)

        threading.Thread(target=worker_one, daemon=True).start()

    def fund_remove_selected_button() -> None:
        sel = fund_tree.selection()
        if not sel:
            return

        uids = [int(str(x)) for x in sel]
        # 从 app_state 删除
        new_list: list[dict[str, Any]] = []
        for it in app_state.get("fund_items") or []:
            if int(it.get("uid")) not in set(uids):
                new_list.append(it)
        app_state["fund_items"] = new_list

        _fund_render_all()
        _fund_save_items_to_disk()
        set_status("已移除所选基金条目")

    def fund_clear_button() -> None:
        app_state["fund_items"] = []
        _fund_render_all()
        _fund_save_items_to_disk()
        set_status("已清空基金列表")

    def _render_fund_history_chart(*, _fundcode: str, _points: list[tuple[str, float, float]], keep_status: bool = False) -> None:
        # 将图表绘制到基金页下方 Canvas 中，并根据当前主题选择高对比度配色。
        fund_history_holder["code"] = _fundcode
        fund_history_holder["points"] = list(_points)
        try:
            fund_history_canvas.delete("all")
        except Exception:
            pass

        if not keep_status:
            fund_history_status_var.set("已加载，点击图中点位查看详细数据")

        try:
            fund_history_canvas.update_idletasks()
        except Exception:
            pass

        canvas = fund_history_canvas
        points = list(_points)
        cw = max(300, canvas.winfo_width())
        ch = max(200, canvas.winfo_height())
        left, top, right, bottom = 70, 20, cw - 60, ch - 60
        pw = max(1, right - left)
        ph = max(1, bottom - top)

        is_day = str(app_state.get("theme_mode") or "night").lower() == "day"
        axis = "#8A8A8A" if is_day else "#666666"
        grid = "#E3E3E3" if is_day else "#2A2A2A"
        nav_line = "#1F6FEB" if is_day else "#7AB8FF"
        point_fill = "#1458C9" if is_day else "#A8D4FF"
        point_outline = nav_line

        canvas.create_line(left, top, left, bottom, fill=axis)
        canvas.create_line(left, bottom, right, bottom, fill=axis)

        xs = [p[0] for p in points]
        ys_nav = [p[1] for p in points]
        n = len(points)

        min_nav, max_nav = min(ys_nav), max(ys_nav)
        if max_nav == min_nav:
            max_nav += 1e-6

        def x_at(i: int) -> float:
            if n <= 1:
                return left
            return left + (pw * i / (n - 1))

        def y_nav(v: float) -> float:
            return bottom - (v - min_nav) / (max_nav - min_nav) * ph

        for k in range(5):
            yy = top + ph * k / 4
            canvas.create_line(left, yy, right, yy, fill=grid)

        nav_pts: list[float] = []
        for i, (_, nav, _pct) in enumerate(points):
            nav_pts.extend([x_at(i), y_nav(nav)])
        nav_line_id = canvas.create_line(*nav_pts, fill=nav_line, width=2, smooth=True, tags=("navline",))

        def _click_from_line(_e: Any) -> None:
            try:
                x = float(getattr(_e, "x", left))
            except Exception:
                x = left
            if pw <= 0 or n <= 1:
                idx = 0
            else:
                rel = (x - left) / pw
                idx = int(round(rel * (n - 1)))
            idx = max(0, min(n - 1, idx))
            _d, _nav, _pct = points[idx]
            fund_history_status_var.set(f"{_d}  净值={_nav:.4f}  涨跌幅={_pct:.2f}%")

        canvas.tag_bind(nav_line_id, "<Button-1>", _click_from_line)

        canvas.create_text(left - 8, top, text=f"{max_nav:.4f}", fill=UI_FG, anchor="e")
        canvas.create_text(left - 8, bottom, text=f"{min_nav:.4f}", fill=UI_FG, anchor="e")

        tick_count = 6
        for k in range(tick_count):
            i = int((n - 1) * k / (tick_count - 1))
            x = x_at(i)
            d = xs[i]
            canvas.create_line(x, bottom, x, bottom + 4, fill=axis)
            anchor = "n"
            if k == 0:
                anchor = "nw"
            elif k == tick_count - 1:
                anchor = "ne"
            canvas.create_text(x, bottom + 14, text=d[5:], fill=UI_FG, anchor=anchor)

        for i, (d, nav, pct) in enumerate(points):
            x = x_at(i)
            y = y_nav(nav)
            tag = f"pt_{i}"
            r = 3.2
            canvas.create_oval(
                x - r,
                y - r,
                x + r,
                y + r,
                fill=point_fill,
                outline=point_outline,
                tags=(tag,),
            )

            def _click(_e: Any = None, *, _i: int = i) -> None:
                _d, _nav, _pct = points[_i]
                fund_history_status_var.set(f"{_d}  净值={_nav:.4f}  涨跌幅={_pct:.2f}%")

            canvas.tag_bind(tag, "<Button-1>", _click)

        set_status(f"历史净值已展示: {_fundcode}")

    def _redraw_fund_history_if_loaded() -> None:
        points = list(fund_history_holder.get("points") or [])
        code = str(fund_history_holder.get("code") or "").strip()
        if len(points) < 2 or not code:
            return
        _render_fund_history_chart(_fundcode=code, _points=points, keep_status=True)

    def fund_show_history_chart() -> None:
        sel = fund_tree.selection()
        if not sel:
            set_status("请先在基金列表选择一项")
            return
        try:
            uid = int(str(sel[0]))
        except Exception:
            set_status("选中项无效")
            return
        item = _get_fund_item_by_uid(uid)
        if not item:
            set_status("未找到选中基金")
            return
        fundcode = str(item.get("fundcode") or "").strip()
        if not fundcode:
            set_status("选中基金代码为空")
            return

        set_status(f"加载历史净值中: {fundcode}")

        def worker() -> None:
            try:
                datas = fund_history_fetch(fundcode, range_key="y")
                if not datas:
                    raise BiqugeError("历史净值数据为空")

                points: list[tuple[str, float, float]] = []
                for row in datas:
                    d = str(row.get("FSRQ") or "").strip()
                    if not d:
                        continue
                    try:
                        dwjz = float(str(row.get("DWJZ") or "").strip())
                    except Exception:
                        continue
                    try:
                        jzzzl = float(str(row.get("JZZZL") or "").strip())
                    except Exception:
                        jzzzl = 0.0
                    points.append((d, dwjz, jzzzl))
                if len(points) < 2:
                    raise BiqugeError("可绘制点不足")

                def _render_into_fund_tab() -> None:
                    _render_fund_history_chart(_fundcode=fundcode, _points=points)

                root.after(0, _render_into_fund_tab)
            except Exception as e:  # noqa: BLE001
                err_msg = str(e)
                root.after(0, lambda err_msg=err_msg: set_status(f"历史净值加载失败: {err_msg}"))

        # 真正启动后台拉取线程
        threading.Thread(target=worker, daemon=True).start()

    _make_flat_button(fund_btns, "新增", fund_add_button).pack(side=tk.LEFT, padx=6)
    _make_flat_button(fund_btns, "移除所选", fund_remove_selected_button).pack(side=tk.LEFT, padx=6)
    _make_flat_button(fund_btns, "清空", fund_clear_button).pack(side=tk.LEFT, padx=6)
    # 按钮事件在主线程执行；函数内部再起后台线程拉接口，避免 Tk 跨线程调用问题
    _make_flat_button(fund_btns, "历史净值", fund_show_history_chart).pack(side=tk.LEFT, padx=6)

    def refresh_funds_all() -> None:
        # 防止频繁切 Tab/重复触发导致并发抓取
        if app_state.get("fund_refresh_running"):
            return
        items_snapshot = list(app_state.get("fund_items") or [])
        if not items_snapshot:
            return

        app_state["fund_refresh_running"] = True

        def worker_loop() -> None:
            results: dict[int, dict[str, Any]] = {}
            for it in items_snapshot:
                uid = int(it.get("uid"))
                fc = str(it.get("fundcode") or "")
                try:
                    payload = fundgz_fetch(fc)
                    results[uid] = {
                        "name": str(payload.get("name") or ""),
                        "dwjz": str(payload.get("dwjz") or ""),
                        "gsz": str(payload.get("gsz") or ""),
                        "gszzl": str(payload.get("gszzl") or ""),
                        "jzrq": str(payload.get("jzrq") or ""),
                        "gztime": str(payload.get("gztime") or ""),
                    }
                except Exception as e:  # noqa: BLE001
                    results[uid] = {"error": str(e)}

            def _apply() -> None:
                for uid, latest in results.items():
                    item2 = _get_fund_item_by_uid(uid)
                    if item2:
                        item2["latest"] = latest
                        _fund_update_row(uid)

                app_state["fund_refresh_running"] = False

            root.after(0, _apply)

        threading.Thread(target=worker_loop, daemon=True).start()

    # 每分钟更新一次
    def _fund_refresh_tick() -> None:
        app_state["fund_refresh_job"] = None
        try:
            refresh_funds_all()
        finally:
            if app_state.get("fund_items"):
                app_state["fund_refresh_job"] = root.after(60_000, _fund_refresh_tick)

    # 初次渲染
    _fund_render_all()

    # 启动后立刻刷新一次（如果有本地配置）
    try:
        refresh_funds_all()
    except Exception:
        pass

    # 列表为空也允许 schedule（refresh_funds_all 会 return）
    if app_state.get("fund_refresh_job") is None:
        app_state["fund_refresh_job"] = root.after(60_000, _fund_refresh_tick)

    # 切到“基金”Tab 时刷新一次
    try:
        fund_tab_idx = notebook.index(fund_tab)
    except Exception:
        fund_tab_idx = None

    if fund_tab_idx is not None:
        def _on_notebook_tab_changed(_e: Any = None) -> None:
            try:
                idx = notebook.index(notebook.select())
            except Exception:
                return
            if idx == fund_tab_idx:
                try:
                    refresh_funds_all()
                except Exception:
                    pass

        notebook.bind("<<NotebookTabChanged>>", _on_notebook_tab_changed)

    # ------------------------------------------------------------------
    # 视频：热门 / 搜索 + 左侧列表（纵向滚动）+ 右侧播放器区
    # ------------------------------------------------------------------
    video_state: dict[str, Any] = {
        "token": 0,
        "embed_player": None,
        "play_generation": 0,
        "mode": "hot",  # hot | search
        "page": 1,
        "page_size": 20,
        "keyword": "",
        "loading_more": False,
        "has_more": True,
        "videos": [],
        "auto_loading_queued": False,
    }
    video_cover_cache: dict[str, Any] = {}  # 保持 PhotoImage 引用，避免被 GC

    video_top = tk.Frame(video_tab, bg=UI_BG, bd=0, highlightthickness=0)
    video_top.pack(side=tk.TOP, fill=tk.X, padx=12, pady=12)

    video_search_label = tk.Label(video_top, text="视频搜索:", bg=UI_BG, fg=UI_FG)
    video_search_label.pack(side=tk.LEFT)
    video_keyword_var = tk.StringVar(value="")
    video_keyword_entry = tk.Entry(
        video_top,
        width=12,
        bg=UI_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        bd=0,
        highlightthickness=0,
        textvariable=video_keyword_var,
    )
    video_keyword_entry.pack(side=tk.LEFT, padx=(8, 12))

    def _open_video(url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # 左侧：视频列表（纵向滚动）；右侧：视频播放器（封面/标题/播放按钮）
    video_main = tk.PanedWindow(video_tab, orient=tk.HORIZONTAL, sashrelief=tk.FLAT, bg=UI_BG, bd=0)
    video_main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

    video_list_holder = tk.Frame(video_main, bg=UI_BG, bd=0, highlightthickness=0)
    video_main.add(video_list_holder, minsize=430)

    player_holder = tk.Frame(video_main, bg=UI_BG, bd=0, highlightthickness=0)
    video_main.add(player_holder, minsize=540)

    video_canvas = tk.Canvas(video_list_holder, bg=UI_BG, bd=0, highlightthickness=0)

    video_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    video_list_frame = tk.Frame(video_canvas, bg=UI_BG, bd=0, highlightthickness=0)
    video_window_id = video_canvas.create_window((0, 0), window=video_list_frame, anchor="nw")
    video_canvas.bind(
        "<Configure>",
        lambda e: (
            video_canvas.itemconfigure(video_window_id, width=max(1, int(getattr(e, "width", 1)))),
            _refresh_video_scrollregion(),
        ),
    )

    def _refresh_video_scrollregion() -> None:
        try:
            bbox = video_canvas.bbox("all")
            if bbox:
                video_canvas.configure(scrollregion=bbox)
        except Exception:
            pass

    video_list_frame.bind("<Configure>", lambda _e=None: _refresh_video_scrollregion())
    # 关键：canvas 内部窗口承载的 video_list_frame 也要绑定滚轮
    # 否则当鼠标位于卡片之间的空白区域时，事件落到 Frame 上不会滚动。
    video_list_frame.bind("<MouseWheel>", lambda e: _wheel_scroll(e))
    video_list_frame.bind("<Button-4>", lambda e: _btn4(e))
    video_list_frame.bind("<Button-5>", lambda e: _btn5(e))

    def _wheel_scroll(event: Any) -> str:
        try:
            # macOS: event.delta 通常为 ±120
            step = -1 if event.delta > 0 else 1
        except Exception:
            step = 0
        if step != 0:
            video_canvas.yview_scroll(step, "units")
            if step > 0:
                root.after(0, _try_auto_load_more_videos)
        return "break"

    def _btn4(_e: Any) -> str:
        video_canvas.yview_scroll(-1, "units")
        return "break"

    def _btn5(_e: Any) -> str:
        video_canvas.yview_scroll(1, "units")
        root.after(0, _try_auto_load_more_videos)
        return "break"

    video_canvas.bind("<MouseWheel>", _wheel_scroll)
    video_canvas.bind("<Button-4>", _btn4)
    video_canvas.bind("<Button-5>", _btn5)

    def _clear_video_list() -> None:
        for child in video_list_frame.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

    # -------- 播放器区（封面 + 标题 + 播放）--------
    THUMB_W, THUMB_H = 160, 90
    PLAYER_W, PLAYER_H = 700, 390

    player_title_var = tk.StringVar(value="")
    player_url_var = tk.StringVar(value="")

    tk.Label(player_holder, text="视频播放器", bg=UI_BG, fg=UI_FG).pack(anchor="w", padx=10, pady=(6, 0))

    player_cover_box = tk.Frame(player_holder, bg=UI_BG, width=PLAYER_W, height=PLAYER_H)
    player_cover_box.pack_propagate(False)
    player_cover_box.pack(side=tk.TOP, padx=10, pady=(8, 10))

    # 用 Canvas 做“真正的渲染承载面”：在 macOS/Tk 上比 Label 更有机会被 mpv --wid 正确重定向
    player_cover_canvas = tk.Canvas(
        player_cover_box,
        bg="#000000",
        highlightthickness=0,
        bd=0,
    )
    player_cover_canvas.pack(fill=tk.BOTH, expand=True)

    player_cover_label = tk.Label(
        player_cover_box,
        bg=UI_BG,
        fg=UI_FG,
        text="点击左侧列表选择视频",
        anchor="center",
        justify="center",
    )
    # 必须与 Canvas 叠放，不能用 pack(expand=True) 与 Canvas 并列——否则会垂直分栏，
    # 上半为黑底 Canvas、下半才是封面，出现“上方大块黑边挤压封面”的现象。
    def _show_player_cover_overlay() -> None:
        try:
            player_cover_label.place(relx=0, rely=0, relwidth=1, relheight=1)
        except Exception:
            pass

    def _hide_player_cover_overlay() -> None:
        try:
            player_cover_label.place_forget()
        except Exception:
            pass

    _show_player_cover_overlay()

    player_title_label = tk.Label(
        player_holder,
        bg=UI_BG,
        fg=UI_FG,
        textvariable=player_title_var,
        wraplength=PLAYER_W - 40,
        justify="center",
        anchor="center",
    )
    try:
        player_title_label.configure(font=tkfont.Font(size=15, weight="bold"))
    except Exception:
        pass
    player_title_label.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 10))

    # 右侧播放器区域不显示 URL（只保留封面/标题/播放按钮）
    player_url_label = tk.Label(
        player_holder,
        bg=UI_BG,
        fg=UI_FG,
        textvariable=player_url_var,
    )

    video_state["selected_url"] = ""
    video_state["selected_bvid"] = ""

    def _stop_embedded_video(*, restore_cover: bool = True) -> None:
        """停止内嵌 ffmpeg / mpv，并清空 Canvas 上残留的最后一帧。"""
        pl = video_state.get("embed_player")
        if pl is not None:
            try:
                pl.stop()
            except Exception:
                pass
            video_state["embed_player"] = None
        proc = video_state.get("mpv_proc")
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            video_state["mpv_proc"] = None
        try:
            player_cover_canvas.delete("all")
        except Exception:
            pass
        if restore_cover:
            try:
                root.after(
                    0,
                    lambda: (
                        _show_player_cover_overlay(),
                        player_cover_canvas.configure(bg=UI_BG),
                    ),
                )
            except Exception:
                pass

    def _load_cover_async(*, item: VideoItem, label: Any, w: int, h: int, cache_key: str) -> None:
        token = int(video_state.get("token") or 0)

        def worker() -> None:
            try:
                pic_url = item.pic
                if not pic_url:
                    return

                tmp_dir = Path(tempfile.gettempdir()) / "nothing_video_covers"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                jpg_path = tmp_dir / f"{item.bvid}.jpg"
                png_path = tmp_dir / f"{item.bvid}_{w}x{h}.png"

                if not png_path.exists():
                    img_bytes = http_get(
                        pic_url,
                        timeout=20,
                        headers={"Accept": "image/*", "Referer": "https://www.bilibili.com/"},
                    )
                    jpg_path.write_bytes(img_bytes)

                    subprocess.run(
                        [
                            "sips",
                            "-s",
                            "format",
                            "png",
                            "-z",
                            str(h),
                            str(w),
                            str(jpg_path),
                            "--out",
                            str(png_path),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True,
                    )

                def _apply() -> None:
                    if int(video_state.get("token") or 0) != token:
                        return
                    if not png_path.exists():
                        return
                    photo = tk.PhotoImage(file=str(png_path))
                    label.configure(image=photo, text="")
                    label.image = photo
                    video_cover_cache[cache_key] = photo

                root.after(0, _apply)
            except Exception:
                return

        # 加载封面时立刻更新占位文字
        try:
            label.configure(text="加载封面中...", image="")
        except Exception:
            pass
        threading.Thread(target=worker, daemon=True).start()

    def _select_video(item: VideoItem) -> None:
        # 切换条目时必须先停播并清画面，否则会叠着上一路的 ffmpeg/mpv 画面
        video_state["play_generation"] = int(video_state.get("play_generation") or 0) + 1
        _stop_embedded_video(restore_cover=True)
        video_state["selected_url"] = item.url
        video_state["selected_bvid"] = item.bvid
        player_title_var.set(item.title)
        # URL 不在右侧显示

        _load_cover_async(
            item=item,
            label=player_cover_label,
            w=PLAYER_W,
            h=PLAYER_H,
            cache_key=f"{item.bvid}_{PLAYER_W}x{PLAYER_H}",
        )

    def _play_selected() -> None:
        url = str(video_state.get("selected_url") or "").strip()
        if not url:
            set_status("请先在左侧选择视频。")
            return

        root.update_idletasks()
        try:
            player_cover_box.update_idletasks()
        except Exception:
            pass

        # mpv 回退用：Linux/X11、Windows 下 --wid；macOS 下为几何对齐
        mpv_target_wid = None
        try:
            mpv_target_wid = int(player_cover_canvas.winfo_id())
        except Exception:
            mpv_target_wid = None

        def _open_direct_url_html(*, direct_url: str, title: str) -> None:
            # 没有 mpv/ffmpeg/vlc 时，最稳妥的“在 GUI 中播放”手段是：生成一个本地 HTML，
            # 用浏览器的 <video> 去拉直链（m3u8/mp4 等）。
            # 注意：若直链需要 Referer/CORS，浏览器直链可能失败；此时回退到网页播放。
            try:
                import hashlib
                import html as html_lib

                tmp_dir = Path(tempfile.gettempdir()) / "nothing_video_direct_play"
                tmp_dir.mkdir(parents=True, exist_ok=True)

                h = hashlib.sha1(direct_url.encode("utf-8")).hexdigest()[:12]
                html_path = tmp_dir / f"direct_{h}.html"

                esc_url = html_lib.escape(direct_url, quote=True)
                esc_title = html_lib.escape(title or "video", quote=True)

                html_content = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>{esc_title}</title>
  <style>
    body {{ margin: 0; background: #000; }}
    video {{ width: 100%; height: 100vh; background: #000; object-fit: contain; }}
  </style>
</head>
<body>
  <video controls autoplay playsinline>
    <source src="{esc_url}" type="application/vnd.apple.mpegurl"/>
  </video>
</body>
</html>
"""
                html_path.write_text(html_content, encoding="utf-8")
                webbrowser.open(f"file://{html_path}")
            except Exception:
                # 兜底：直接打开直链（让用户用自己的浏览器判断）
                try:
                    webbrowser.open(direct_url)
                except Exception:
                    pass

        set_status("正在解析直链（请稍候...）")
        video_state["play_generation"] = int(video_state.get("play_generation") or 0) + 1
        play_gen = int(video_state["play_generation"])

        def _on_ffmpeg_stopped() -> None:
            video_state["embed_player"] = None
            try:
                _show_player_cover_overlay()
                player_cover_canvas.configure(bg=UI_BG)
            except Exception:
                pass

        def _begin_playback_from_direct(*, direct_url: str, referer: str, title: str) -> None:
            """主线程：优先 ffmpeg 解码到 Canvas（真内嵌），失败则线程内回退 mpv / 浏览器。"""
            if int(video_state.get("play_generation") or 0) != play_gen:
                return
            _stop_embedded_video(restore_cover=False)
            root.update_idletasks()
            try:
                player_cover_box.update_idletasks()
            except Exception:
                pass
            try:
                cw = int(player_cover_canvas.winfo_width())
                ch = int(player_cover_canvas.winfo_height())
            except Exception:
                cw, ch = PLAYER_W, PLAYER_H
            # 布局未完成时 winfo 常为 1，若仍用 8×8 解码会几乎看不见 → 退回设计尺寸
            if cw < 32 or ch < 32:
                cw, ch = PLAYER_W, PLAYER_H

            try:
                _hide_player_cover_overlay()
                player_cover_box.configure(bg="#000000")
                player_cover_canvas.configure(bg="#000000")
            except Exception:
                pass

            pl = FfmpegTkEmbedPlayer(
                root=root,
                canvas=player_cover_canvas,
                set_status=set_status,
                on_stopped=_on_ffmpeg_stopped,
            )
            if pl.start(url=direct_url, referer=referer, user_agent=UA, width=cw, height=ch):
                video_state["embed_player"] = pl
                set_status(f"内嵌播放（ffmpeg）：{title}")
                return

            def _mpv_fallback() -> None:
                if int(video_state.get("play_generation") or 0) != play_gen:
                    return
                try:
                    eg = (
                        int(player_cover_canvas.winfo_rootx()),
                        int(player_cover_canvas.winfo_rooty()),
                        max(2, player_cover_canvas.winfo_width()),
                        max(2, player_cover_canvas.winfo_height()),
                    )
                except Exception:
                    eg = (0, 0, PLAYER_W, PLAYER_H)
                ok = _try_play_with_mpv_embedded(
                    direct_url=direct_url,
                    referer=referer,
                    title=title,
                    embed_geom=eg,
                )
                if not ok:

                    def _restore_and_html() -> None:
                        try:
                            _show_player_cover_overlay()
                            player_cover_canvas.configure(bg=UI_BG)
                        except Exception:
                            pass
                        _open_direct_url_html(direct_url=direct_url, title=title)

                    root.after(0, _restore_and_html)

            threading.Thread(target=_mpv_fallback, daemon=True).start()

        def _try_play_with_mpv_embedded(
            *,
            direct_url: str,
            referer: str,
            title: str,
            embed_geom: tuple[int, int, int, int],
        ) -> bool:
            """
            返回 True 表示“已成功启动并认为在内嵌播放”。
            - Linux/X11、Windows：--wid 指向 Tk Canvas，真内嵌。
            - macOS：Tk winfo_id 与 mpv Cocoa --wid 不匹配，改用 --geometry + --no-border
              将无边框 mpv 窗口叠到播放区矩形上（视觉上内嵌）。
            """
            try:
                mpv_path = shutil.which("mpv")
            except Exception:
                mpv_path = None
            if not mpv_path:
                return False
            is_darwin = sys.platform == "darwin"
            if not is_darwin and not mpv_target_wid:
                return False
            gx, gy, gw, gh = embed_geom
            if gw < 8 or gh < 8:
                return False

            _stop_embedded_video(restore_cover=False)

            try:
                _hide_player_cover_overlay()
                player_cover_box.configure(bg="#000000")
                player_cover_canvas.configure(bg="#000000")
            except Exception:
                pass

            header_fields = f"User-Agent:{UA},Referer:{referer},Origin:https://www.bilibili.com"
            log_path = Path(tempfile.gettempdir()) / "nothing_mpv_embed.log"
            cmd = [
                "mpv",
                "--no-terminal",
                f"--http-header-fields={header_fields}",
                f"--log-file={str(log_path)}",
                "--msg-level=all=info",
            ]
            if is_darwin:
                cmd.extend(
                    [
                        "--force-window=yes",
                        "--no-border",
                        f"--geometry={gw}x{gh}+{gx}+{gy}",
                        "--title=Nothing",
                    ]
                )
            else:
                cmd.extend(
                    [
                        "--force-window",
                        f"--wid={mpv_target_wid}",
                    ]
                )
            cmd.append(direct_url)

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                video_state["mpv_proc"] = proc
                root.after(0, lambda lp=log_path: set_status(f"已启动 mpv（日志: {lp}）"))
            except Exception:
                return False

            # 等一下看看 mpv 是否立刻退出（macOS/环境不支持 wid 时有可能会这样）
            try:
                time.sleep(0.8)
            except Exception:
                pass
            try:
                if proc.poll() is not None:
                    video_state["mpv_proc"] = None
                    # 内嵌尝试失败：把封面层恢复出来
                    try:
                        root.after(0, _show_player_cover_overlay)
                    except Exception:
                        pass
                    return False
            except Exception:
                # poll 失败就当作“启动成功”；实际效果由用户验证
                pass

            if is_darwin:
                root.after(
                    0,
                    lambda: set_status(
                        f"已对齐播放区启动 mpv（macOS）：{title}；移动主窗口后若错位可停止后重播"
                    ),
                )
            else:
                root.after(0, lambda: set_status(f"已内嵌播放：{title}"))
            return True

        def worker() -> None:
            try:
                import asyncio

                data = asyncio.run(parse_bilibili_minimal(url))
                du = str(data.get("direct_url") or "").strip()
                ref = str(data.get("video_url") or "https://www.bilibili.com/").strip()
                ttl = str(data.get("title") or "").strip() or "bilibili"
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda e=e: set_status(f"直链解析失败：{e}，回退网页播放"))
                root.after(0, lambda: _open_video(url))
                return

            def _apply() -> None:
                if int(video_state.get("play_generation") or 0) != play_gen:
                    return
                if not du.strip():
                    set_status("直链为空，回退网页播放")
                    _open_video(url)
                    return
                set_status("直链解析成功，启动内嵌播放…")
                _begin_playback_from_direct(direct_url=du, referer=ref, title=ttl)

            root.after(0, _apply)

        threading.Thread(target=worker, daemon=True).start()

    play_btn = _make_flat_button(player_holder, "播放", _play_selected)
    try:
        play_btn.configure(font=tkfont.Font(size=16, weight="bold"))
    except Exception:
        pass
    play_btn.pack(side=tk.TOP, padx=10, pady=(0, 14))

    def _is_video_list_near_bottom() -> bool:
        try:
            top, bottom = video_canvas.yview()
        except Exception:
            return False
        return bottom >= 0.995

    def _render_videos(videos: list[VideoItem], *, append: bool = False) -> None:
        # 非追加模式：刷新列表时停止当前播放，避免旧画面残留
        if not append:
            _stop_embedded_video(restore_cover=True)
            _clear_video_list()
            video_state["videos"] = list(videos)
        else:
            video_state["videos"] = list(video_state.get("videos") or []) + list(videos)

        if not videos and not append:
            tk.Label(video_list_frame, text="暂无视频结果", bg=UI_BG, fg=UI_FG).pack(anchor="w", padx=6, pady=10)
            return

        COVER_W, COVER_H = THUMB_W, THUMB_H

        def _truncate_title(text: str, max_len: int = 20) -> str:
            s = str(text or "").strip()
            if len(s) <= max_len:
                return s
            return s[:max_len] + "..."

        # 纵向列表：Canvas 高度由内容+滚动条决定，不强行固定高度

        for idx, item in enumerate(videos):
            card = tk.Frame(video_list_frame, bg=UI_BG, bd=0, highlightthickness=0)
            card.pack(side=tk.TOP, fill=tk.X, padx=10, pady=6)

            # 用 Frame 固定封面像素高度（Label.height 是“行数”容易造成异常）
            cover_box = tk.Frame(card, bg=UI_BG, width=COVER_W, height=COVER_H)
            cover_box.pack_propagate(False)
            cover_box.pack(side=tk.LEFT, padx=(0, 8), pady=4)

            cover_label = tk.Label(
                cover_box,
                bg=UI_BG,
                fg=UI_FG,
                text="加载封面中...",
                anchor="center",
                justify="center",
            )
            cover_label.pack(fill=tk.BOTH, expand=True)

            # 每条视频单独提供播放按钮：播放对应 url
            _make_flat_button(
                card,
                "跳转",
                lambda _it=item: (_select_video(_it), _open_video(_it.url)),
            ).pack(side=tk.RIGHT, padx=6, pady=0)

            info_holder = tk.Frame(card, bg=UI_BG, bd=0, highlightthickness=0)
            info_holder.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

            title_label = tk.Label(
                info_holder,
                bg=UI_BG,
                fg=UI_FG,
                text=_truncate_title(item.title, 20),
                wraplength=220,
                justify="left",
                anchor="nw",
            )
            title_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            def _on_card_resize(e: Any, *, _label: Any = title_label) -> None:
                # 动态按卡片宽度给标题分配可用区域，避免横向裁剪。
                try:
                    card_w = int(getattr(e, "width", 0) or 0)
                    text_w = max(100, card_w - COVER_W - 90)
                    _label.configure(wraplength=text_w)
                except Exception:
                    pass

            try:
                card.bind("<Configure>", _on_card_resize)
            except Exception:
                pass

            def _on_click(_e: Any = None, *, _item: VideoItem = item) -> None:
                _select_video(_item)

            for w in (card, cover_label, title_label):
                try:
                    w.bind("<Button-1>", _on_click)
                except Exception:
                    pass

            # 让鼠标滚轮在卡片区域也能工作（避免光标不在 Canvas 上时不触发）
            for w in (card, cover_box, cover_label, info_holder, title_label):
                try:
                    w.bind("<MouseWheel>", _wheel_scroll)
                    w.bind("<Button-4>", _btn4)
                    w.bind("<Button-5>", _btn5)
                except Exception:
                    pass

            # 异步加载封面（避免阻塞 Tk 主线程）
            token = int(video_state.get("token") or 0)

            def _load_cover_worker(*, _item: VideoItem = item, _label: Any = cover_label, _token: int = token) -> None:
                try:
                    pic_url = _item.pic
                    if not pic_url:
                        return

                    tmp_dir = Path(tempfile.gettempdir()) / "nothing_video_covers"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    jpg_path = tmp_dir / f"{_item.bvid}.jpg"
                    png_path = tmp_dir / f"{_item.bvid}_{COVER_W}x{COVER_H}.png"

                    if not png_path.exists():
                        img_bytes = http_get(
                            pic_url,
                            timeout=20,
                            headers={"Accept": "image/*", "Referer": "https://www.bilibili.com/"},
                        )
                        jpg_path.write_bytes(img_bytes)

                        # macOS: 用 sips 把 jpg 转 png 并缩放到固定尺寸
                        subprocess.run(
                            [
                                "sips",
                                "-s",
                                "format",
                                "png",
                                "-z",
                                str(COVER_H),
                                str(COVER_W),
                                str(jpg_path),
                                "--out",
                                str(png_path),
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=True,
                        )

                    def _apply() -> None:
                        if int(video_state.get("token") or 0) != _token:
                            return
                        try:
                            if not png_path.exists():
                                return
                            photo = tk.PhotoImage(file=str(png_path))
                            _label.configure(image=photo, text="")
                            _label.image = photo
                            video_cover_cache[_item.bvid] = photo
                        except Exception:
                            return

                    root.after(0, _apply)
                except Exception:
                    # 不因封面加载失败而影响列表展示
                    pass

            threading.Thread(target=_load_cover_worker, daemon=True).start()

        # 仅在“首屏加载”时默认选中第一条，避免翻页时打断当前选择
        if videos and not append:
            try:
                _select_video(videos[0])
            except Exception:
                pass

        _refresh_video_scrollregion()

    def _fetch_and_render(fetch_fn: Any, *, append: bool = False, **kwargs: Any) -> None:
        video_state["token"] = int(video_state.get("token") or 0) + 1
        token = int(video_state["token"])
        set_status("视频加载中..." if not append else "加载下一页中...")

        def worker() -> None:
            try:
                videos: list[VideoItem] = fetch_fn(**kwargs)
                def _apply() -> None:
                    if int(video_state.get("token") or 0) != token:
                        return
                    _render_videos(videos, append=append)
                    if append:
                        video_state["loading_more"] = False
                        video_state["auto_loading_queued"] = False
                        page_size = int(video_state.get("page_size") or 20)
                        video_state["has_more"] = len(videos) >= page_size
                        set_status(
                            f"第 {int(video_state.get('page') or 1)} 页加载完成，新增 {len(videos)} 条（累计 {len(video_state.get('videos') or [])} 条）"
                        )
                    else:
                        page_size = int(video_state.get("page_size") or 20)
                        video_state["has_more"] = len(videos) >= page_size
                        set_status(f"视频加载完成（共 {len(videos)} 条）")
                root.after(0, _apply)
            except Exception as e:  # noqa: BLE001
                if int(video_state.get("token") or 0) != token:
                    return
                err_msg = str(e)
                def _apply_err(err_msg: str = err_msg) -> None:
                    if append:
                        video_state["loading_more"] = False
                        video_state["auto_loading_queued"] = False
                        # 回滚页码，避免“跳页”
                        video_state["page"] = max(1, int(video_state.get("page") or 1) - 1)
                    set_status(f"视频加载失败: {err_msg}")
                root.after(0, _apply_err)

        threading.Thread(target=worker, daemon=True).start()

    def _load_next_video_page() -> None:
        if video_state.get("loading_more"):
            return
        if not video_state.get("has_more", True):
            return
        video_state["loading_more"] = True
        video_state["page"] = int(video_state.get("page") or 1) + 1
        page = int(video_state["page"])
        page_size = int(video_state.get("page_size") or 20)
        mode = str(video_state.get("mode") or "hot")
        if mode == "search":
            kw = str(video_state.get("keyword") or "").strip()
            if not kw:
                video_state["loading_more"] = False
                return
            _fetch_and_render(
                bilibili_search_videos,
                append=True,
                keyword=kw,
                page=page,
                page_size=page_size,
                order="general",
            )
        else:
            _fetch_and_render(
                bilibili_hot_videos_fetch,
                append=True,
                page=page,
                page_size=page_size,
            )

    def _try_auto_load_more_videos() -> None:
        if video_state.get("auto_loading_queued"):
            return
        if video_state.get("loading_more"):
            return
        if not _is_video_list_near_bottom():
            return
        video_state["auto_loading_queued"] = True
        _load_next_video_page()

    def _on_hot_clicked() -> None:
        video_state["mode"] = "hot"
        video_state["page"] = 1
        video_state["keyword"] = ""
        video_state["loading_more"] = False
        video_state["auto_loading_queued"] = False
        video_state["has_more"] = True
        _fetch_and_render(bilibili_hot_videos_fetch, page=1, page_size=int(video_state.get("page_size") or 20))

    def _on_search_clicked() -> None:
        kw = (video_keyword_var.get() or "").strip()
        if not kw:
            set_status("请输入视频关键词。")
            return
        video_state["mode"] = "search"
        video_state["page"] = 1
        video_state["keyword"] = kw
        video_state["loading_more"] = False
        video_state["auto_loading_queued"] = False
        video_state["has_more"] = True
        _fetch_and_render(
            bilibili_search_videos,
            keyword=kw,
            page=1,
            page_size=int(video_state.get("page_size") or 20),
            order="general",
        )

    # 顶部按钮：用统一的 Label 自绘样式
    # 互换“热门按钮”和“搜索栏”位置：热门在左侧，搜索栏在其右侧
    hot_btn = _make_flat_button(video_top, "热门", _on_hot_clicked)
    search_btn = _make_flat_button(video_top, "搜索", _on_search_clicked)

    # 重新 pack（避免 pack 顺序导致位置不对）
    try:
        video_search_label.pack_forget()
        video_keyword_entry.pack_forget()
    except Exception:
        pass

    hot_btn.pack(side=tk.LEFT, padx=6)
    video_search_label.pack(side=tk.LEFT)
    video_keyword_entry.pack(side=tk.LEFT, padx=(8, 12))
    search_btn.pack(side=tk.LEFT, padx=6)

    try:
        video_keyword_entry.bind("<Return>", lambda _e: _on_search_clicked())
    except Exception:
        pass

    # 初始化：默认加载热门
    _on_hot_clicked()

    # ------------------------------------------------------------------
    # 音乐：网易云热歌榜 / 搜索 + 左侧列表 + 右侧试听区（mpv/ffplay）
    # ------------------------------------------------------------------
    music_state: dict[str, Any] = {
        "token": 0,
        "play_generation": 0,
        "mode": "hot",
        "page": 1,
        "page_size": 20,
        "keyword": "",
        "loading_more": False,
        "has_more": True,
        "songs": [],
        "audio_proc": None,
        "selected_id": 0,
        "auto_loading_queued": False,
        "sel_token": 0,
    }
    music_cover_cache: dict[str, Any] = {}

    MUSIC_THUMB_W, MUSIC_THUMB_H = 64, 64
    MUSIC_PANEL_W, MUSIC_PANEL_H = 360, 360

    music_top = tk.Frame(music_tab, bg=UI_BG, bd=0, highlightthickness=0)
    music_top.pack(side=tk.TOP, fill=tk.X, padx=12, pady=12)

    music_keyword_var = tk.StringVar(value="")
    music_search_label = tk.Label(music_top, text="音乐搜索:", bg=UI_BG, fg=UI_FG)
    music_keyword_entry = tk.Entry(
        music_top,
        width=18,
        bg=UI_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        bd=0,
        highlightthickness=0,
        textvariable=music_keyword_var,
    )

    music_main = tk.PanedWindow(music_tab, orient=tk.HORIZONTAL, sashrelief=tk.FLAT, bg=UI_BG, bd=0)
    music_main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

    music_list_holder = tk.Frame(music_main, bg=UI_BG, bd=0, highlightthickness=0)
    music_main.add(music_list_holder, minsize=400)
    music_player_holder = tk.Frame(music_main, bg=UI_BG, bd=0, highlightthickness=0)
    music_main.add(music_player_holder, minsize=420)

    music_canvas = tk.Canvas(music_list_holder, bg=UI_BG, bd=0, highlightthickness=0)
    music_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    music_list_frame = tk.Frame(music_canvas, bg=UI_BG, bd=0, highlightthickness=0)
    music_window_id = music_canvas.create_window((0, 0), window=music_list_frame, anchor="nw")

    def _refresh_music_scrollregion() -> None:
        try:
            bbox = music_canvas.bbox("all")
            if bbox:
                music_canvas.configure(scrollregion=bbox)
        except Exception:
            pass

    music_canvas.bind(
        "<Configure>",
        lambda e: (
            music_canvas.itemconfigure(music_window_id, width=max(1, int(getattr(e, "width", 1)))),
            _refresh_music_scrollregion(),
        ),
    )
    music_list_frame.bind("<Configure>", lambda _e=None: _refresh_music_scrollregion())

    def _music_wheel(event: Any) -> str:
        try:
            step = -1 if event.delta > 0 else 1
        except Exception:
            step = 0
        if step != 0:
            music_canvas.yview_scroll(step, "units")
            if step > 0:
                root.after(0, _try_auto_load_more_music)
        return "break"

    def _music_btn4(_e: Any) -> str:
        music_canvas.yview_scroll(-1, "units")
        return "break"

    def _music_btn5(_e: Any) -> str:
        music_canvas.yview_scroll(1, "units")
        root.after(0, _try_auto_load_more_music)
        return "break"

    music_list_frame.bind("<MouseWheel>", _music_wheel)
    music_list_frame.bind("<Button-4>", _music_btn4)
    music_list_frame.bind("<Button-5>", _music_btn5)
    music_canvas.bind("<MouseWheel>", _music_wheel)
    music_canvas.bind("<Button-4>", _music_btn4)
    music_canvas.bind("<Button-5>", _music_btn5)

    def _clear_music_list() -> None:
        for child in music_list_frame.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

    music_title_var = tk.StringVar(value="")
    music_artist_var = tk.StringVar(value="")

    tk.Label(music_player_holder, text="音乐试听", bg=UI_BG, fg=UI_FG).pack(anchor="w", padx=10, pady=(6, 0))

    music_cover_box = tk.Frame(music_player_holder, bg=UI_BG, width=MUSIC_PANEL_W, height=MUSIC_PANEL_H)
    music_cover_box.pack_propagate(False)
    music_cover_box.pack(side=tk.TOP, padx=10, pady=(8, 10))

    music_cover_label = tk.Label(
        music_cover_box,
        bg=UI_BG,
        fg=UI_FG,
        text="在左侧选择歌曲",
        anchor="center",
        justify="center",
        wraplength=MUSIC_PANEL_W - 20,
    )
    music_cover_label.pack(fill=tk.BOTH, expand=True)

    tk.Label(
        music_player_holder,
        bg=UI_BG,
        fg=UI_FG,
        textvariable=music_title_var,
        font=tkfont.Font(size=15, weight="bold"),
        wraplength=MUSIC_PANEL_W,
        justify="center",
    ).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 4))
    tk.Label(
        music_player_holder,
        bg=UI_BG,
        fg=UI_FG,
        textvariable=music_artist_var,
        wraplength=MUSIC_PANEL_W,
        justify="center",
    ).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 12))

    def _stop_music_audio() -> None:
        proc = music_state.get("audio_proc")
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
            music_state["audio_proc"] = None

    def _load_music_cover_async(*, item: MusicItem, label: Any, w: int, h: int, cache_key: str) -> None:
        sel_tok = int(music_state.get("sel_token") or 0)

        def worker() -> None:
            try:
                pic_url = item.pic_url
                if not pic_url:
                    return
                tmp_dir = Path(tempfile.gettempdir()) / "nothing_music_covers"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                jpg_path = tmp_dir / f"m_{item.song_id}.jpg"
                png_path = tmp_dir / f"m_{item.song_id}_{w}x{h}.png"
                if not png_path.exists():
                    img_bytes = http_get(
                        pic_url,
                        timeout=20,
                        headers={"Accept": "image/*", "Referer": "https://music.163.com/"},
                    )
                    jpg_path.write_bytes(img_bytes)
                    subprocess.run(
                        [
                            "sips",
                            "-s",
                            "format",
                            "png",
                            "-z",
                            str(h),
                            str(w),
                            str(jpg_path),
                            "--out",
                            str(png_path),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True,
                    )

                def _apply() -> None:
                    if int(music_state.get("sel_token") or 0) != sel_tok:
                        return
                    if not png_path.exists():
                        return
                    photo = tk.PhotoImage(file=str(png_path))
                    label.configure(image=photo, text="")
                    label.image = photo
                    music_cover_cache[cache_key] = photo

                root.after(0, _apply)
            except Exception:
                return

        try:
            label.configure(text="加载封面中...", image="")
        except Exception:
            pass
        threading.Thread(target=worker, daemon=True).start()

    def _play_music_selected() -> None:
        sid = int(music_state.get("selected_id") or 0)
        if sid <= 0:
            set_status("请先在左侧选择一首歌曲。")
            return
        music_state["play_generation"] = int(music_state.get("play_generation") or 0) + 1
        play_gen = int(music_state["play_generation"])
        _stop_music_audio()
        ttl = str(music_title_var.get() or "").strip() or "music"
        set_status("正在解析播放地址…")

        def worker() -> None:
            try:
                play_url = netease_song_play_url(sid)
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda e=e: set_status(f"解析播放地址失败：{e}"))
                return

            def _apply() -> None:
                if int(music_state.get("play_generation") or 0) != play_gen:
                    return
                if not play_url:
                    set_status("无可用直链（版权限制），正在打开网页试听…")
                    try:
                        webbrowser.open(netease_song_page_url(sid))
                    except Exception:
                        pass
                    return
                mpv_path = shutil.which("mpv")
                ffplay_path = shutil.which("ffplay")
                cmd: list[str] | None = None
                if mpv_path:
                    cmd = [mpv_path, "--no-video", "--no-terminal", play_url]
                elif ffplay_path:
                    cmd = [ffplay_path, "-nodisp", "-autoexit", "-loglevel", "quiet", play_url]
                if not cmd:
                    set_status("未找到 mpv / ffplay，无法本地播放，改为打开网页")
                    try:
                        webbrowser.open(netease_song_page_url(sid))
                    except Exception:
                        pass
                    return
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    music_state["audio_proc"] = proc
                    set_status(f"正在播放：{ttl}")
                except Exception as e:  # noqa: BLE001
                    set_status(f"启动播放器失败：{e}")

            root.after(0, _apply)

        threading.Thread(target=worker, daemon=True).start()

    def _select_music(item: MusicItem, *, autoplay: bool = True) -> None:
        music_state["sel_token"] = int(music_state.get("sel_token") or 0) + 1
        _stop_music_audio()
        music_state["selected_id"] = int(item.song_id)
        music_title_var.set(item.title)
        art = str(item.artist or "").strip()
        alb = str(item.album or "").strip()
        music_artist_var.set(f"{art}" + (f" · {alb}" if alb else ""))
        _load_music_cover_async(
            item=item,
            label=music_cover_label,
            w=MUSIC_PANEL_W,
            h=MUSIC_PANEL_H,
            cache_key=f"m_{item.song_id}_{MUSIC_PANEL_W}x{MUSIC_PANEL_H}",
        )
        if autoplay:
            _play_music_selected()

    def _truncate_m(s: str, n: int = 22) -> str:
        t = str(s or "").strip()
        return t if len(t) <= n else t[: n - 1] + "…"

    def _render_music_list(songs: list[MusicItem], *, append: bool = False) -> None:
        if not append:
            _stop_music_audio()
            _clear_music_list()
            music_state["songs"] = list(songs)
        else:
            music_state["songs"] = list(music_state.get("songs") or []) + list(songs)

        if not songs and not append:
            tk.Label(music_list_frame, text="暂无歌曲", bg=UI_BG, fg=UI_FG).pack(anchor="w", padx=6, pady=10)
            return

        tw, th = MUSIC_THUMB_W, MUSIC_THUMB_H
        for item in songs:
            card = tk.Frame(music_list_frame, bg=UI_BG, bd=0, highlightthickness=0)
            card.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)

            cover_box = tk.Frame(card, bg=UI_BG, width=tw, height=th)
            cover_box.pack_propagate(False)
            cover_box.pack(side=tk.LEFT, padx=(0, 8), pady=2)

            cover_l = tk.Label(
                cover_box,
                bg=UI_BG,
                fg=UI_FG,
                text="…",
                anchor="center",
            )
            cover_l.pack(fill=tk.BOTH, expand=True)

            info = tk.Frame(card, bg=UI_BG, bd=0, highlightthickness=0)
            info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            line1 = f"{_truncate_m(item.title, 26)}"
            line2 = f"{_truncate_m(item.artist, 20)}" + (f" · {_truncate_m(item.album, 14)}" if item.album else "")
            tk.Label(
                info,
                bg=UI_BG,
                fg=UI_FG,
                text=line1,
                anchor="w",
            ).pack(anchor="w")
            tk.Label(
                info,
                bg=UI_BG,
                fg="#AAAAAA" if not is_day_theme else "#666666",
                text=line2,
                anchor="w",
            ).pack(anchor="w")

            _make_flat_button(card, "试听", lambda it=item: _select_music(it, autoplay=True)).pack(
                side=tk.RIGHT, padx=4
            )

            def _on_click(_e: Any = None, *, _it: MusicItem = item) -> None:
                _select_music(_it, autoplay=True)

            for wdg in (card, cover_box, cover_l, info):
                try:
                    wdg.bind("<Button-1>", _on_click)
                except Exception:
                    pass

            for wdg in (card, cover_box, cover_l, info):
                try:
                    wdg.bind("<MouseWheel>", _music_wheel)
                    wdg.bind("<Button-4>", _music_btn4)
                    wdg.bind("<Button-5>", _music_btn5)
                except Exception:
                    pass

            tok = int(music_state.get("token") or 0)

            def _cover_worker(*, _item: MusicItem = item, _label: Any = cover_l, _tok: int = tok) -> None:
                try:
                    if not _item.pic_url:
                        return
                    tmp_dir = Path(tempfile.gettempdir()) / "nothing_music_covers"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    jpg_path = tmp_dir / f"m_{_item.song_id}.jpg"
                    png_path = tmp_dir / f"m_{_item.song_id}_t{tw}x{th}.png"
                    if not png_path.exists():
                        img_bytes = http_get(
                            _item.pic_url,
                            timeout=20,
                            headers={"Accept": "image/*", "Referer": "https://music.163.com/"},
                        )
                        jpg_path.write_bytes(img_bytes)
                        subprocess.run(
                            [
                                "sips",
                                "-s",
                                "format",
                                "png",
                                "-z",
                                str(th),
                                str(tw),
                                str(jpg_path),
                                "--out",
                                str(png_path),
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=True,
                        )

                    def _ap() -> None:
                        if int(music_state.get("token") or 0) != _tok:
                            return
                        if not png_path.exists():
                            return
                        photo = tk.PhotoImage(file=str(png_path))
                        _label.configure(image=photo, text="")
                        _label.image = photo
                        music_cover_cache[f"{_item.song_id}_t"] = photo

                    root.after(0, _ap)
                except Exception:
                    pass

            threading.Thread(target=_cover_worker, daemon=True).start()

        if songs and not append:
            try:
                _select_music(songs[0], autoplay=False)
            except Exception:
                pass

        _refresh_music_scrollregion()

    def _fetch_and_render_music(fetch_fn: Any, *, append: bool = False, **kwargs: Any) -> None:
        music_state["token"] = int(music_state.get("token") or 0) + 1
        token = int(music_state["token"])
        set_status("音乐加载中…" if not append else "加载下一页…")

        def worker() -> None:
            try:
                rows: list[MusicItem] = fetch_fn(**kwargs)

                def _apply() -> None:
                    if int(music_state.get("token") or 0) != token:
                        return
                    _render_music_list(rows, append=append)
                    if append:
                        music_state["loading_more"] = False
                        music_state["auto_loading_queued"] = False
                        ps = int(music_state.get("page_size") or 20)
                        music_state["has_more"] = len(rows) >= ps
                        set_status(
                            f"第 {int(music_state.get('page') or 1)} 页：新增 {len(rows)} 首（共 {len(music_state.get('songs') or [])} 首）"
                        )
                    else:
                        ps = int(music_state.get("page_size") or 20)
                        music_state["has_more"] = len(rows) >= ps
                        set_status(f"音乐加载完成（{len(rows)} 首）")

                root.after(0, _apply)
            except Exception as e:  # noqa: BLE001
                if int(music_state.get("token") or 0) != token:
                    return
                err = str(e)

                def _err() -> None:
                    if append:
                        music_state["loading_more"] = False
                        music_state["auto_loading_queued"] = False
                        music_state["page"] = max(1, int(music_state.get("page") or 1) - 1)
                    set_status(f"音乐加载失败：{err}")

                root.after(0, _err)

        threading.Thread(target=worker, daemon=True).start()

    def _is_music_list_near_bottom() -> bool:
        try:
            _top, bottom = music_canvas.yview()
        except Exception:
            return False
        return bottom >= 0.995

    def _load_next_music_page() -> None:
        if music_state.get("loading_more"):
            return
        if not music_state.get("has_more", True):
            return
        music_state["loading_more"] = True
        music_state["page"] = int(music_state.get("page") or 1) + 1
        page = int(music_state["page"])
        ps = int(music_state.get("page_size") or 20)
        mode = str(music_state.get("mode") or "hot")
        if mode == "search":
            kw = str(music_state.get("keyword") or "").strip()
            if not kw:
                music_state["loading_more"] = False
                return
            _fetch_and_render_music(
                music_search_songs,
                append=True,
                keyword=kw,
                page=page,
                page_size=ps,
            )
        else:
            _fetch_and_render_music(
                music_hot_fetch,
                append=True,
                page=page,
                page_size=ps,
            )

    def _try_auto_load_more_music() -> None:
        if music_state.get("auto_loading_queued"):
            return
        if music_state.get("loading_more"):
            return
        if not _is_music_list_near_bottom():
            return
        music_state["auto_loading_queued"] = True
        _load_next_music_page()

    def _on_music_hot_clicked() -> None:
        music_state["mode"] = "hot"
        music_state["page"] = 1
        music_state["keyword"] = ""
        music_state["loading_more"] = False
        music_state["auto_loading_queued"] = False
        music_state["has_more"] = True
        _fetch_and_render_music(music_hot_fetch, page=1, page_size=int(music_state.get("page_size") or 20))

    def _on_music_search_clicked() -> None:
        kw = (music_keyword_var.get() or "").strip()
        if not kw:
            set_status("请输入歌曲或歌手关键词。")
            return
        music_state["mode"] = "search"
        music_state["page"] = 1
        music_state["keyword"] = kw
        music_state["loading_more"] = False
        music_state["auto_loading_queued"] = False
        music_state["has_more"] = True
        _fetch_and_render_music(
            music_search_songs,
            keyword=kw,
            page=1,
            page_size=int(music_state.get("page_size") or 20),
        )

    music_hot_btn = _make_flat_button(music_top, "热门", _on_music_hot_clicked)
    music_search_btn = _make_flat_button(music_top, "搜索", _on_music_search_clicked)
    try:
        music_search_label.pack_forget()
        music_keyword_entry.pack_forget()
    except Exception:
        pass
    music_hot_btn.pack(side=tk.LEFT, padx=6)
    music_search_label.pack(side=tk.LEFT)
    music_keyword_entry.pack(side=tk.LEFT, padx=(8, 12))
    music_search_btn.pack(side=tk.LEFT, padx=6)
    try:
        music_keyword_entry.bind("<Return>", lambda _e: _on_music_search_clicked())
    except Exception:
        pass

    music_play_btn = _make_flat_button(music_player_holder, "播放", _play_music_selected)
    try:
        music_play_btn.configure(font=tkfont.Font(size=14, weight="bold"))
    except Exception:
        pass
    music_play_btn.pack(side=tk.TOP, padx=10, pady=(0, 14))

    _on_music_hot_clicked()

    # ------------------------------------------------------------------
    # 首页：搜索 + 选书后自动全量爬取（从 chapterid=1 递增直到失败）
    # ------------------------------------------------------------------
    home_top = tk.Frame(home, bg=UI_BG, bd=0, highlightthickness=0)
    home_top.pack(side=tk.TOP, fill=tk.X, padx=12, pady=12)

    tk.Label(home_top, text="小说名称:", bg=UI_BG, fg=UI_FG).pack(side=tk.LEFT)
    home_novel_entry = tk.Entry(home_top, width=34, bg=UI_BG, fg=UI_FG, insertbackground=UI_FG, bd=0, highlightthickness=0)
    home_novel_entry.pack(side=tk.LEFT, padx=(8, 12))

    # 渠道切换：笔趣阁1 / 笔趣阁2（手动）
    channel_map = {
        "bqg1": "笔趣阁1",
        "bqg2": "笔趣阁2",
        "bqg3": "笔趣阁3",
    }
    tk.Label(home_top, text="渠道:", bg=UI_BG, fg=UI_FG).pack(side=tk.LEFT)
    home_channel_var = tk.StringVar(value=channel_map["bqg1"])
    home_channel_btn = tk.Label(home_top, textvariable=home_channel_var, bg=UI_BG, fg=UI_FG, padx=10, pady=6)
    home_channel_btn.pack(side=tk.LEFT, padx=(8, 12))

    def _toggle_home_channel(_e: Any = None) -> None:
        order = ["bqg1", "bqg2", "bqg3"]
        cur = app_state.get("home_channel", "bqg1")
        try:
            i = order.index(cur)
        except ValueError:
            i = 0
        app_state["home_channel"] = order[(i + 1) % len(order)]
        home_channel_var.set(channel_map[app_state["home_channel"]])
        set_status(f"已切换渠道：{home_channel_var.get()}")

    home_channel_btn.bind("<Button-1>", _toggle_home_channel)

    tk.Label(home_top, text="起始章节:", bg=UI_BG, fg=UI_FG).pack(side=tk.LEFT)
    home_start_entry = tk.Entry(home_top, width=8, bg=UI_BG, fg=UI_FG, insertbackground=UI_FG, bd=0, highlightthickness=0)
    home_start_entry.insert(0, "1")
    home_start_entry.pack(side=tk.LEFT, padx=(8, 12))

    # 透明度控制：参考基金模块，放到最右侧
    home_alpha_frame = tk.Frame(home_top, bg=UI_BG, bd=0, highlightthickness=0)
    home_alpha_frame.pack(side=tk.RIGHT, padx=(0, 8))
    tk.Label(home_alpha_frame, text="透明度:", bg=UI_BG, fg=UI_FG).pack(side=tk.LEFT)
    home_alpha = tk.DoubleVar(value=1.0)
    tk.Scale(
        home_alpha_frame,
        from_=0.15,
        to=1.0,
        resolution=0.05,
        orient=tk.HORIZONTAL,
        variable=home_alpha,
        command=lambda _v: set_root_alpha(home_alpha.get()),
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        highlightthickness=0,
        troughcolor=UI_BG,
    ).pack(side=tk.LEFT, padx=(8, 0))

    home_mid = tk.PanedWindow(home, orient=tk.HORIZONTAL, sashrelief=tk.FLAT, bg=UI_BG, bd=0)
    home_mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

    home_left = tk.Frame(home_mid, bg=UI_BG, bd=0, highlightthickness=0)
    home_right = tk.Frame(home_mid, bg=UI_BG, bd=0, highlightthickness=0)
    home_mid.add(home_left, minsize=420)
    home_mid.add(home_right, minsize=700)

    tk.Label(home_left, text="搜索结果（选择小说后点击“开始下载”）", bg=UI_BG, fg=UI_FG).pack(anchor="w")
    home_list_frame = tk.Frame(home_left, bg=UI_BG, bd=0, highlightthickness=0)
    home_list_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

    home_listbox = tk.Listbox(
        home_list_frame,
        bg=UI_BG,
        fg=UI_FG,
        selectbackground=UI_SEL_BG,
        selectforeground=UI_SEL_FG,
        bd=0,
        highlightthickness=0,
    )
    # 不显示显式 Scrollbar：通过鼠标滚轮/键盘仍可滚动
    home_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    tk.Label(home_right, text="下载日志", bg=UI_BG, fg=UI_FG).pack(anchor="w")
    home_log = tk.Text(home_right, wrap="word", bg=UI_BG, fg=UI_FG, insertbackground=UI_FG, bd=0, highlightthickness=0)
    home_log.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

    def home_log_append(line: str) -> None:
        home_log.insert(tk.END, line + "\n")
        home_log.see(tk.END)

    def home_selected_item() -> dict[str, Any] | None:
        sel = home_listbox.curselection()
        if not sel:
            return None
        idx = int(sel[0])
        items: list[dict[str, Any]] = app_state.get("search_items") or []
        if idx < 0 or idx >= len(items):
            return None
        return items[idx]

    def home_search() -> None:
        name = home_novel_entry.get().strip()
        if not name:
            set_status("请输入小说名称。")
            return

        def worker() -> None:
            try:
                set_status("搜索中...")
                selected_channel = app_state.get("home_channel", "bqg1")
                rows: list[dict[str, Any]] = []

                if selected_channel == "bqg1":
                    # 渠道1: apibi 搜索；失败/空则回退渠道2
                    items1: list[ApiBookItem] = []
                    try:
                        items1 = apibi_search(name)
                    except Exception:
                        items1 = []
                    if items1:
                        rows = [{"channel": "bqg1", "item": it} for it in items1]
                    else:
                        legacy = search_novel(name)
                        rows = [{"channel": "bqg2", "item": it} for it in legacy]
                elif selected_channel == "bqg2":
                    # 渠道2: m.bqgl 搜索；失败/空则回退渠道1
                    items2: list[SearchItem] = []
                    try:
                        items2 = search_novel(name)
                    except Exception:
                        items2 = []
                    if items2:
                        rows = [{"channel": "bqg2", "item": it} for it in items2]
                    else:
                        try:
                            x3 = xiunews_search(name)
                            if x3:
                                rows = [{"channel": "bqg3", "item": it} for it in x3]
                            else:
                                apis = apibi_search(name)
                                rows = [{"channel": "bqg1", "item": it} for it in apis]
                        except Exception:
                            apis = apibi_search(name)
                            rows = [{"channel": "bqg1", "item": it} for it in apis]
                else:
                    # 渠道3: xiunews 搜索；失败/空则回退渠道1再渠道2
                    items3: list[XiunewsSearchItem] = []
                    try:
                        items3 = xiunews_search(name)
                    except Exception:
                        items3 = []
                    if items3:
                        rows = [{"channel": "bqg3", "item": it} for it in items3]
                    else:
                        apis = []
                        try:
                            apis = apibi_search(name)
                        except Exception:
                            apis = []
                        if apis:
                            rows = [{"channel": "bqg1", "item": it} for it in apis]
                        else:
                            legacy = search_novel(name)
                            rows = [{"channel": "bqg2", "item": it} for it in legacy]

                app_state["search_items"] = rows

                def update_ui() -> None:
                    home_listbox.delete(0, tk.END)
                    home_log.delete("1.0", tk.END)
                    if not rows:
                        set_status("未搜索到结果。")
                        return
                    for i, row in enumerate(rows, start=1):
                        ch = row.get("channel")
                        it = row.get("item")
                        if isinstance(it, ApiBookItem):
                            tag = channel_map.get(ch, "笔趣阁1")
                            home_listbox.insert(tk.END, f"{i}. {it.title} / {it.author}  (id={it.id}) [{tag}]")
                        elif isinstance(it, XiunewsSearchItem):
                            tag = channel_map.get(ch, "笔趣阁3")
                            home_listbox.insert(
                                tk.END,
                                f"{i}. {it.title} / {it.author}  ({it.update_time}, {it.status}) [{tag}]",
                            )
                        else:
                            tag = channel_map.get(ch, "笔趣阁2")
                            home_listbox.insert(tk.END, f"{i}. {getattr(it,'articlename','')} / {getattr(it,'author','')} [{tag}]")
                    set_status(f"搜索完成，共 {len(rows)} 条。请选择一本书。")

                root.after(0, update_ui)
            except Exception as e:  # noqa: BLE001
                err_msg = str(e)
                root.after(0, lambda err_msg=err_msg: set_status(f"搜索失败: {err_msg}"))

        threading.Thread(target=worker, daemon=True).start()

    def home_cancel_download() -> None:
        app_state["crawl_cancel"] = True
        set_status("已请求停止下载（将在当前章节完成后停止）")

    def home_start_download() -> None:
        chosen_row = home_selected_item()
        if not chosen_row:
            set_status("请先在左侧选择一本小说。")
            return
        chosen = chosen_row.get("item")
        chosen_channel = chosen_row.get("channel", app_state.get("home_channel", "bqg1"))

        try:
            start_ch = int(home_start_entry.get().strip() or "1")
            if start_ch < 1:
                raise ValueError
        except Exception:
            set_status("起始章节必须是 >=1 的整数。")
            return

        app_state["crawl_cancel"] = False
        if isinstance(chosen, ApiBookItem):
            home_log_append(f"开始下载: {chosen.title} / {chosen.author} (id={chosen.id}, 从第 {start_ch} 章, 渠道={channel_map.get(chosen_channel, chosen_channel)})")
        elif isinstance(chosen, XiunewsSearchItem):
            home_log_append(
                f"开始下载: {chosen.title} / {chosen.author} "
                f"(目录={chosen.book_url}, 从第 {start_ch} 章, 渠道={channel_map.get(chosen_channel, chosen_channel)})"
            )
        else:
            home_log_append(
                f"开始下载: {getattr(chosen,'articlename','')} / {getattr(chosen,'author','')} "
                f"(从第 {start_ch} 章, 渠道={channel_map.get(chosen_channel, chosen_channel)})"
            )

        def worker() -> None:
            # 根据选中条目的渠道决定解析与下载接口
            try:
                fetcher = fetch_chapter_bqg
                if isinstance(chosen, ApiBookItem):
                    book_id = chosen.id
                    fetcher = fetch_chapter_bqg if chosen_channel == "bqg1" else fetch_chapter
                elif isinstance(chosen, XiunewsSearchItem):
                    chapters = xiunews_parse_toc(chosen.book_url)
                    if not chapters:
                        raise BiqugeError("未解析到章节目录（正文）")
                    if start_ch > len(chapters):
                        raise BiqugeError(f"起始章节超范围：{start_ch} > {len(chapters)}")
                    root.after(0, lambda n=len(chapters): home_log_append(f"已解析正文目录，共 {n} 章"))
                    consecutive_fail = 0
                    for ch_ref in chapters[start_ch - 1 :]:
                        if app_state["crawl_cancel"]:
                            root.after(0, lambda: home_log_append("下载已停止。"))
                            root.after(0, lambda: set_status("下载已停止"))
                            return
                        try:
                            root.after(0, lambda c=ch_ref.idx: set_status(f"下载中：chapter={c}"))
                            chap = xiunews_fetch_chapter(ch_ref.url)
                            txt = str(chap.get("txt") or "").strip()
                            if not txt:
                                consecutive_fail += 1
                                root.after(
                                    0,
                                    lambda c=ch_ref.idx: home_log_append(f"第 {c} 章无内容，跳过（fail={consecutive_fail}）"),
                                )
                            else:
                                consecutive_fail = 0
                                if app_state["save"]:
                                    out_path = save_chapter_to_disk(
                                        base_dir=app_state["out_dir"],
                                        novel_name=chosen.title or home_novel_entry.get().strip(),
                                        chapterid=ch_ref.idx,
                                        chapter=chap,
                                    )
                                    title = str(chap.get("chaptername") or chap.get("title") or "").strip()
                                    root.after(
                                        0,
                                        lambda c=ch_ref.idx, t=title, p=out_path: home_log_append(f"已保存 第 {c} 章 {t} -> {p}"),
                                    )
                                else:
                                    title = str(chap.get("chaptername") or chap.get("title") or "").strip()
                                    root.after(0, lambda c=ch_ref.idx, t=title: home_log_append(f"已获取 第 {c} 章 {t}（未保存）"))
                            if consecutive_fail >= 3:
                                root.after(0, lambda: home_log_append("连续多章无内容，推断已到末尾，结束下载。"))
                                root.after(0, lambda: set_status("下载完成"))
                                return
                        except Exception as e:  # noqa: BLE001
                            consecutive_fail += 1
                            root.after(
                                0,
                                lambda c=ch_ref.idx, err=e: home_log_append(f"第 {c} 章抓取失败: {err}（fail={consecutive_fail}）"),
                            )
                            if consecutive_fail >= 3:
                                root.after(0, lambda: home_log_append("连续失败次数过多，结束下载。"))
                                root.after(0, lambda: set_status("下载结束（失败过多）"))
                                return
                    root.after(0, lambda: home_log_append("下载完成。"))
                    root.after(0, lambda: set_status("下载完成"))
                    return
                else:
                    set_status("解析 book id...")
                    book_id = resolve_book_id(chosen, prefer_redirect=app_state["prefer_redirect"])
                    fetcher = fetch_chapter if chosen_channel == "bqg2" else fetch_chapter_bqg
                root.after(0, lambda: home_log_append(f"book_id = {book_id}"))
            except Exception as e:  # noqa: BLE001
                err_msg = str(e)
                root.after(0, lambda err_msg=err_msg: set_status(f"解析 book id 失败: {err_msg}"))
                return

            consecutive_fail = 0
            chapterid = start_ch
            while True:
                if app_state["crawl_cancel"]:
                    root.after(0, lambda: home_log_append("下载已停止。"))
                    root.after(0, lambda: set_status("下载已停止"))
                    return

                try:
                    root.after(0, lambda c=chapterid: set_status(f"下载中：chapterid={c}"))
                    chap = fetcher(book_id, chapterid=chapterid)
                    txt = str(chap.get("txt") or "")
                    if not txt.strip():
                        consecutive_fail += 1
                        root.after(0, lambda c=chapterid: home_log_append(f"第 {c} 章无内容，跳过（fail={consecutive_fail}）"))
                    else:
                        consecutive_fail = 0
                        if app_state["save"]:
                            out_path = save_chapter_to_disk(
                                base_dir=app_state["out_dir"],
                                novel_name=(chosen.title if isinstance(chosen, ApiBookItem) else getattr(chosen, "articlename", "")) or home_novel_entry.get().strip(),
                                chapterid=chapterid,
                                chapter=chap,
                            )
                            title = str(chap.get("chaptername") or chap.get("title") or "").strip()
                            root.after(0, lambda c=chapterid, t=title, p=out_path: home_log_append(f"已保存 第 {c} 章 {t} -> {p}"))
                        else:
                            title = str(chap.get("chaptername") or chap.get("title") or "").strip()
                            root.after(0, lambda c=chapterid, t=title: home_log_append(f"已获取 第 {c} 章 {t}（未保存）"))

                    # 结束条件：连续多章失败/无内容，视为到尾
                    if consecutive_fail >= 3:
                        root.after(0, lambda: home_log_append("连续多章无内容，推断已到末尾，结束下载。"))
                        root.after(0, lambda: set_status("下载完成"))
                        return

                    chapterid += 1
                except Exception as e:  # noqa: BLE001
                    consecutive_fail += 1
                    root.after(0, lambda c=chapterid, err=e: home_log_append(f"第 {c} 章抓取失败: {err}（fail={consecutive_fail}）"))
                    if consecutive_fail >= 3:
                        root.after(0, lambda: home_log_append("连续失败次数过多，结束下载。"))
                        root.after(0, lambda: set_status("下载结束（失败过多）"))
                        return
                    chapterid += 1

        threading.Thread(target=worker, daemon=True).start()

    home_actions = tk.Frame(home_top, bg=UI_BG, bd=0, highlightthickness=0)
    home_actions.pack(side=tk.RIGHT)
    # 首页按钮也统一深色，避免白色控件区域
    _make_flat_button(home_actions, "搜索", home_search).pack(side=tk.LEFT, padx=6)
    _make_flat_button(home_actions, "开始下载", home_start_download).pack(side=tk.LEFT, padx=6)
    _make_flat_button(home_actions, "停止", home_cancel_download).pack(side=tk.LEFT, padx=6)

    # ------------------------------------------------------------------
    # 阅读：浏览本地 reader/content，黑底白字逐章阅读
    # ------------------------------------------------------------------
    reader_tab.configure(bg=UI_BG, bd=0, highlightthickness=0)
    try:
        reader_tab.master.configure(bg=UI_BG)
    except Exception:
        pass

    read_top = tk.Frame(reader_tab, bg=UI_BG, bd=0, highlightthickness=0)
    read_top.pack(side=tk.TOP, fill=tk.X, padx=12, pady=12)

    tk.Label(read_top, text="本地小说:", fg=UI_FG, bg=UI_BG).pack(side=tk.LEFT)
    novel_var = tk.StringVar(value="")
    # macOS 下 OptionMenu/Menubutton 可能不吃 bg，这里用自绘下拉弹窗保证颜色一致
    novel_dropdown_btn = tk.Label(read_top, text="选择…", bg=UI_BG, fg=UI_FG, padx=10, pady=6)
    novel_dropdown_btn.pack(side=tk.LEFT, padx=(8, 12))
    novel_popup: dict[str, Any] = {"win": None, "listbox": None, "items": []}

    read_font_label = tk.Label(read_top, text="字号:", fg=UI_FG, bg=UI_BG)
    read_font_label.pack(side=tk.LEFT)
    read_font_size = tk.IntVar(value=12)
    read_size_spin = tk.Spinbox(
        read_top,
        from_=4,
        to=48,
        textvariable=read_font_size,
        width=6,
        bg=UI_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        bd=0,
        highlightthickness=1,
        highlightbackground=UI_BG,
        highlightcolor=UI_BG,
    )
    read_size_spin.pack(side=tk.LEFT, padx=(8, 12))

    def _on_read_font_wheel(event: Any) -> str:
        # Windows/Linux: 使用 event.delta；macOS: 也通常走 <MouseWheel>
        # 兜底：部分环境走 <Button-4>/<Button-5>
        direction = 0
        try:
            if hasattr(event, "delta") and event.delta:
                direction = 1 if event.delta > 0 else -1
            elif hasattr(event, "num") and event.num in (4, 5):
                direction = 1 if event.num == 4 else -1
        except Exception:
            direction = 0

        if direction == 0:
            return "break"

        try:
            cur = int(read_font_size.get())
        except Exception:
            cur = 12

        new_val = max(4, min(48, cur + direction))
        if new_val == cur:
            return "break"

        read_font_size.set(new_val)
        # 同步刷新阅读字号和当前章节内容
        try:
            apply_read_font()
            load_selected_chapter_and_mirror()
        except Exception:
            pass
        return "break"

    # 滚轮调整字号
    read_size_spin.bind("<MouseWheel>", _on_read_font_wheel)
    read_font_label.bind("<MouseWheel>", _on_read_font_wheel)
    # X11/部分 Linux 没有 MouseWheel
    read_size_spin.bind("<Button-4>", _on_read_font_wheel)
    read_size_spin.bind("<Button-5>", _on_read_font_wheel)

    # 透明度控制：参考首页/基金模块，放到最右侧
    read_alpha_frame = tk.Frame(read_top, bg=UI_BG, bd=0, highlightthickness=0)
    read_alpha_frame.pack(side=tk.RIGHT, padx=(0, 8))
    tk.Label(read_alpha_frame, text="透明度:", fg=UI_FG, bg=UI_BG).pack(side=tk.LEFT)
    read_alpha = tk.DoubleVar(value=1.0)

    def _blend_bg(level: float) -> str:
        # level: 0..1，将 UI_BG(#181818) 向黑色靠拢
        level = max(0.0, min(1.0, float(level)))
        base = 0x18
        v = int(base * level)
        return f"#{v:02x}{v:02x}{v:02x}"

    def apply_read_bg(level: float) -> None:
        bg = _blend_bg(level)
        # 阅读相关区域统一背景（仅做备选方案：不改变窗口 alpha）
        try:
            read_top.configure(bg=bg)
        except Exception:
            pass
        try:
            read_mid.configure(bg=bg)
        except Exception:
            pass
        try:
            read_left.configure(bg=bg)
            read_right.configure(bg=bg)
        except Exception:
            pass
        try:
            chapters_list.configure(bg=bg)
        except Exception:
            pass
        try:
            read_text.configure(bg=bg)
        except Exception:
            pass
        try:
            immersive_layer.configure(bg=bg)
            immersive_text.configure(bg=bg)
        except Exception:
            pass
        try:
            novel_dropdown_btn.configure(bg=bg)
        except Exception:
            pass
        try:
            read_size_spin.configure(bg=bg, highlightbackground=bg, highlightcolor=bg)
        except Exception:
            pass
    tk.Scale(
        read_alpha_frame,
        from_=0.15,
        to=1.0,
        resolution=0.05,
        orient=tk.HORIZONTAL,
        variable=read_alpha,
        # 透明度恢复为窗口 alpha：会同时影响字体亮度
        command=lambda _v: set_root_alpha(read_alpha.get()),
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        highlightthickness=0,
        troughcolor=UI_BG,
    ).pack(side=tk.LEFT, padx=(8, 0))

    # PanedWindow 不支持 highlightthickness 参数
    read_mid = tk.PanedWindow(reader_tab, orient=tk.HORIZONTAL, sashrelief=tk.FLAT, bg=UI_BG, bd=0)
    read_mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

    read_left = tk.Frame(read_mid, bg=UI_BG, bd=0, highlightthickness=0)
    read_right = tk.Frame(read_mid, bg=UI_BG, bd=0, highlightthickness=0)
    read_mid.add(read_left, minsize=360)
    read_mid.add(read_right, minsize=760)

    tk.Label(read_left, text="章节列表", fg=UI_FG, bg=UI_BG).pack(anchor="w")
    chapters_list_frame = tk.Frame(read_left, bg=UI_BG, bd=0, highlightthickness=0)
    chapters_list_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

    chapters_list = tk.Listbox(
        chapters_list_frame,
        bg=UI_BG,
        fg=UI_FG,
        selectbackground=UI_SEL_BG,
        selectforeground=UI_SEL_FG,
        bd=0,
        highlightthickness=1,
        highlightbackground=UI_BG,
        highlightcolor=UI_BG,
    )
    chapters_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    read_text = tk.Text(read_right, wrap="word", bg=UI_BG, fg=UI_FG, insertbackground=UI_FG, bd=0, highlightthickness=0)
    read_text.pack(fill=tk.BOTH, expand=True)

    def list_local_novels() -> list[str]:
        base = Path(out_dir)
        if not base.exists():
            return []
        return sorted([p.name for p in base.iterdir() if p.is_dir()])

    def list_local_chapters(novel_name: str) -> list[Path]:
        base = Path(out_dir) / novel_name
        if not base.exists():
            return []
        # 章节目录形如 0001_xxx
        dirs = [p for p in base.iterdir() if p.is_dir()]
        return sorted(dirs, key=lambda p: p.name)

    def _close_novel_popup() -> None:
        w = novel_popup.get("win")
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
        novel_popup["win"] = None
        novel_popup["listbox"] = None

    def _open_novel_popup() -> None:
        # 切换弹窗
        if novel_popup.get("win") is not None:
            _close_novel_popup()
            return

        novels: list[str] = novel_popup.get("items") or list_local_novels()
        novel_popup["items"] = novels
        if not novels:
            set_status("本地暂无小说，请先在首页下载。")
            return

        w = tk.Toplevel(root)
        w.after(0, lambda: w.overrideredirect(True))
        w.configure(bg=UI_BG)
        w.attributes("-topmost", True)

        lb = tk.Listbox(w, bg=UI_BG, fg=UI_FG, selectbackground=UI_SEL_BG, selectforeground=UI_SEL_FG, bd=0, highlightthickness=0)
        for n in novels:
            lb.insert(tk.END, n)
        lb.pack(fill=tk.BOTH, expand=True)

        # 位置：按钮下方
        try:
            x = novel_dropdown_btn.winfo_rootx()
            y = novel_dropdown_btn.winfo_rooty() + novel_dropdown_btn.winfo_height()
            w.geometry(f"320x260+{x}+{y}")
        except Exception:
            pass

        def on_pick(_e: Any = None) -> None:
            sel = lb.curselection()
            if not sel:
                return
            v = lb.get(int(sel[0]))
            novel_var.set(v)
            novel_dropdown_btn.configure(text=v)
            _close_novel_popup()
            refresh_chapters()

        def on_escape(_e: Any = None) -> None:
            _close_novel_popup()

        lb.bind("<Double-Button-1>", on_pick)
        lb.bind("<Return>", on_pick)
        lb.bind("<Escape>", on_escape)
        w.bind("<Escape>", on_escape)
        lb.focus_set()

        novel_popup["win"] = w
        novel_popup["listbox"] = lb

    def _dropdown_hover(_e: Any) -> None:
        novel_dropdown_btn.configure(bg=UI_SEL_BG)

    def _dropdown_leave(_e: Any) -> None:
        novel_dropdown_btn.configure(bg=UI_BG)

    def _dropdown_click(_e: Any) -> None:
        _open_novel_popup()

    novel_dropdown_btn.bind("<Enter>", _dropdown_hover)
    novel_dropdown_btn.bind("<Leave>", _dropdown_leave)
    novel_dropdown_btn.bind("<Button-1>", _dropdown_click)

    def refresh_local_library() -> None:
        novels = list_local_novels()
        novel_popup["items"] = novels
        if novels and (novel_var.get() not in novels):
            novel_var.set(novels[0])
            novel_dropdown_btn.configure(text=novels[0])
        refresh_chapters()

    def refresh_chapters() -> None:
        chapters_list.delete(0, tk.END)
        novel_name = novel_var.get().strip()
        if not novel_name:
            return
        ch_dirs = list_local_chapters(novel_name)
        for d in ch_dirs:
            chapters_list.insert(tk.END, d.name)
        if ch_dirs:
            chapters_list.selection_set(0)
            load_selected_chapter()

    def load_selected_chapter() -> None:
        novel_name = novel_var.get().strip()
        if not novel_name:
            return
        sel = chapters_list.curselection()
        if not sel:
            return
        chapter_dir_name = chapters_list.get(int(sel[0]))
        chapter_dir = Path(out_dir) / novel_name / chapter_dir_name
        content_path = chapter_dir / "content.txt"
        meta_path = chapter_dir / "meta.json"

        title = chapter_dir_name
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                t = str(meta.get("chaptername") or meta.get("title") or "").strip()
                if t:
                    title = t
        except Exception:
            pass

        content = ""
        try:
            if content_path.exists():
                content = content_path.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            content = f"(读取失败: {e})"

        # 应用字号
        try:
            f = tkfont.Font(family="PingFang SC" if "PingFang SC" in tkfont.families() else "TkDefaultFont", size=int(read_font_size.get()))
            read_text.configure(font=f)
        except Exception:
            pass

        read_text.delete("1.0", tk.END)
        read_text.insert(tk.END, f"{title}\n\n{content}")
        set_status(f"阅读：{novel_name} / {chapter_dir_name}")

    def read_prev_next(delta: int) -> None:
        sel = chapters_list.curselection()
        if not sel:
            return
        i = int(sel[0]) + delta
        if i < 0 or i >= chapters_list.size():
            return
        chapters_list.selection_clear(0, tk.END)
        chapters_list.selection_set(i)
        chapters_list.see(i)
        load_selected_chapter()

    # -------- 沉浸阅读层：只显示正文，无任何边框区域 --------
    immersive_layer = tk.Frame(root, bg=UI_BG, bd=0, highlightthickness=0)
    immersive_text = tk.Text(immersive_layer, wrap="word", bg=UI_BG, fg=UI_FG, insertbackground=UI_FG, bd=0, highlightthickness=0)
    immersive_text.pack(fill=tk.BOTH, expand=True)
    immersive_state: dict[str, Any] = {"chapter_marks": [], "current_idx": 0, "novel": ""}

    def apply_read_font() -> None:
        try:
            fam = "PingFang SC" if "PingFang SC" in tkfont.families() else "TkDefaultFont"
            f = tkfont.Font(family=fam, size=int(read_font_size.get()))
            read_text.configure(font=f)
            immersive_text.configure(font=f)
        except Exception:
            pass

    def _chapter_title_from_dir(chapter_dir: Path) -> str:
        meta_path = chapter_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                t = str(meta.get("chaptername") or meta.get("title") or "").strip()
                if t:
                    return t
            except Exception:
                pass
        return chapter_dir.name

    def build_immersive_text_for_novel(novel_name: str) -> tuple[str, list[str]]:
        """
        返回：拼接后的全文 + 每章起始位置（Text index 字符串，如 '1.0'）
        """
        ch_dirs = list_local_chapters(novel_name)
        chunks: list[str] = []
        marks: list[str] = []
        # 用字符计数粗略换算 index：后续通过插入时记录真实 index 更可靠，所以这里直接在插入时写 marks
        for d in ch_dirs:
            title = _chapter_title_from_dir(d)
            content_path = d / "content.txt"
            try:
                content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
            except Exception as e:  # noqa: BLE001
                content = f"(读取失败: {e})"
            chunks.append(f"{title}\n\n{content}\n\n")
        return "".join(chunks), marks

    def load_selected_chapter_and_mirror() -> None:
        load_selected_chapter()
        if app_state.get("immersive"):
            # 沉浸模式使用全文拼接，不在这里按单章镜像
            return

    def enter_immersive() -> None:
        if app_state.get("immersive"):
            return
        app_state["immersive"] = True
        # 沉浸模式窗口置顶
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        apply_read_font()
        # 构建全文拼接
        novel_name = novel_var.get().strip()
        immersive_state["novel"] = novel_name
        immersive_text.delete("1.0", tk.END)
        immersive_state["chapter_marks"] = []
        immersive_state["current_idx"] = 0
        if novel_name:
            ch_dirs = list_local_chapters(novel_name)
            # 以当前选择章节为起点定位
            sel = chapters_list.curselection()
            cur = int(sel[0]) if sel else 0
            immersive_state["current_idx"] = max(0, min(cur, len(ch_dirs) - 1)) if ch_dirs else 0
            for i, d in enumerate(ch_dirs):
                start_index = immersive_text.index(tk.END)
                immersive_state["chapter_marks"].append(start_index)
                title = _chapter_title_from_dir(d)
                content_path = d / "content.txt"
                try:
                    content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
                except Exception as e:  # noqa: BLE001
                    content = f"(读取失败: {e})"
                immersive_text.insert(tk.END, f"{title}\n\n{content}\n\n")
            # 跳到当前章
            marks = immersive_state["chapter_marks"]
            if marks:
                immersive_text.see(marks[immersive_state["current_idx"]])
                immersive_text.mark_set("insert", marks[immersive_state["current_idx"]])
        # 隐藏所有其它区域
        notebook.pack_forget()
        status_bar.pack_forget()
        # 沉浸模式不显示自绘顶部栏
        topbar.place_forget()
        immersive_layer.pack(fill=tk.BOTH, expand=True)
        # 强制保持无边框，避免系统标题栏回弹
        root.after(0, lambda: root.overrideredirect(True))
        immersive_text.focus_set()
        set_status("沉浸模式：←/→ 跳转章节，Esc 退出")

    def exit_immersive() -> None:
        if not app_state.get("immersive"):
            return
        app_state["immersive"] = False
        # 退出沉浸后取消置顶
        try:
            root.attributes("-topmost", False)
        except Exception:
            pass
        # 普通模式也保持无边框，不切回系统装饰
        immersive_layer.pack_forget()
        notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # 退出沉浸后恢复自绘顶部栏
        topbar.place(x=0, y=0, relwidth=1.0, height=MOVE_AREA_H)
        # 退出沉浸后恢复自绘顶部栏/状态栏（不再做可选开关控制）
        if not status_bar.winfo_ismapped():
            status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        if not read_top.winfo_ismapped():
            read_top.pack(side=tk.TOP, fill=tk.X, padx=12, pady=12)
        try:
            # 确保章节列表面板仍存在
            if str(read_left) not in read_mid.panes():
                read_mid.add(read_left, before=read_right, minsize=360)
        except Exception:
            pass
        set_status("已退出沉浸模式")

    def _is_reader_tab_active() -> bool:
        try:
            return notebook.select() == str(reader_tab)
        except Exception:
            return True

    def on_key(event: Any) -> None:
        # 按 `·`（以及 ` 键）快速退出程序
        # - event.char: 实际输入字符（如 '·' 或 '`'）
        # - keysym: 不同键盘布局可能不同，这里两者都兼容
        if event.char in {"·", "`"} or event.keysym in {"grave", "quoteleft"}:
            _close_app()
            return
        # Esc：进入/退出沉浸模式
        if event.keysym == "Escape":
            if app_state.get("immersive"):
                exit_immersive()
            else:
                # 仅在“阅读”页按 Esc 才进入沉浸，避免影响首页输入
                if _is_reader_tab_active():
                    enter_immersive()
            return

        # 沉浸模式下：←/→ 跳转章节
        if not app_state.get("immersive"):
            return
        if event.keysym == "Left":
            if immersive_state["chapter_marks"]:
                immersive_state["current_idx"] = max(0, immersive_state["current_idx"] - 1)
                idx = immersive_state["chapter_marks"][immersive_state["current_idx"]]
                immersive_text.see(idx)
                immersive_text.mark_set("insert", idx)
        elif event.keysym == "Right":
            if immersive_state["chapter_marks"]:
                immersive_state["current_idx"] = min(len(immersive_state["chapter_marks"]) - 1, immersive_state["current_idx"] + 1)
                idx = immersive_state["chapter_marks"][immersive_state["current_idx"]]
                immersive_text.see(idx)
                immersive_text.mark_set("insert", idx)

    root.bind("<KeyPress-Left>", on_key)
    root.bind("<KeyPress-Right>", on_key)
    root.bind("<KeyPress-Escape>", on_key)
    root.bind("<KeyPress>", on_key)

    # （移除：阅读页 UI 显示开关逻辑）
    read_actions = tk.Frame(read_top, bg=UI_BG, bd=0, highlightthickness=0)
    read_actions.pack(side=tk.RIGHT)
    # 阅读模式按钮底色统一为黑色
    # macOS 下 tk.Button 可能忽略 bg，这里统一用自绘按钮保证颜色一致
    _make_flat_button(read_actions, "刷新本地", refresh_local_library).pack(side=tk.LEFT, padx=6)
    _make_flat_button(read_actions, "上一章", lambda: read_prev_next(-1)).pack(side=tk.LEFT, padx=6)
    _make_flat_button(read_actions, "下一章", lambda: read_prev_next(1)).pack(side=tk.LEFT, padx=6)
    _make_flat_button(read_actions, "沉浸", enter_immersive).pack(side=tk.LEFT, padx=6)

    chapters_list.bind("<<ListboxSelect>>", lambda _e: load_selected_chapter_and_mirror())
    read_size_spin.configure(command=lambda: (apply_read_font(), load_selected_chapter_and_mirror()))

    # 初始化
    set_root_alpha(1.0)
    _apply_theme_to_all(str(app_state.get("theme_mode") or "night"))
    _render_theme_switch()
    # apply_read_bg(read_alpha.get())
    # apply_read_font()
    refresh_local_library()

    root.mainloop()
    return 0


