from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable

from gui.gui_app import gui_app
from novel.api import (
    BiqugeError,
    SearchItem,
    fetch_chapter,
    format_search_results,
    resolve_book_id,
    search_novel,
)
from saved.saver import save_chapter_to_disk


def _prompt_non_empty(prompt: str) -> str:
    while True:
        s = input(prompt).strip()
        if s:
            return s
        print("输入不能为空，请重试。")


def _prompt_int_in_range(prompt: str, *, min_v: int, max_v: int) -> int:
    while True:
        s = input(prompt).strip()
        try:
            v = int(s)
        except Exception:
            print("请输入数字。")
            continue
        if v < min_v or v > max_v:
            print(f"请输入范围内的数字: {min_v}..{max_v}")
            continue
        return v


def interactive_flow(*, out_dir: Path, prefer_redirect: bool = True, save: bool = True) -> int:
    """
    交互模式：输入小说名 -> 选择结果 -> 输入章节号 -> 抓取并可选落盘。
    """
    novel_name = _prompt_non_empty("请输入小说名称: ")
    items = search_novel(novel_name)
    if not items:
        print("未搜索到结果。")
        return 2

    print("\n搜索结果:")
    print(format_search_results(items))

    idx = _prompt_int_in_range(f"\n请选择序号 (1..{len(items)}): ", min_v=1, max_v=len(items))
    chosen = items[idx - 1]

    chapterid = _prompt_int_in_range("\n请输入要爬取的章节号 chapterid (>=1): ", min_v=1, max_v=10**9)
    book_id = resolve_book_id(chosen, prefer_redirect=prefer_redirect)

    chapter = fetch_chapter(book_id, chapterid=chapterid)

    title = str(chapter.get("chaptername") or chapter.get("title") or "").strip()
    txt = str(chapter.get("txt") or "")

    if save:
        out_path = save_chapter_to_disk(
            base_dir=out_dir,
            novel_name=chosen.articlename or novel_name,
            chapterid=chapterid,
            chapter=chapter,
        )
        print(f"\n已保存到: {out_path}")

    if title:
        print("\n" + title)
    if txt:
        print("\n" + txt)
    else:
        print("\n(该章节无 txt 字段或为空)")
    return 0


def run_cli(argv: list[str] | None = None) -> int:
    log = logging.getLogger(__name__)
    parser = argparse.ArgumentParser(description="bqgl.cc 小说搜索与章节抓取")
    parser.add_argument("name", nargs="?", help="小说名称（用于 q=... 搜索）。不填则进入交互模式。")
    parser.add_argument("--index", type=int, default=1, help="选择搜索结果序号（从 1 开始，仅非交互模式使用）")
    parser.add_argument("--chapterid", type=int, default=1, help="章节 chapterid（仅非交互模式使用）")
    parser.add_argument("--no-redirect", action="store_true", help="不尝试 list.html 重定向解析 id")
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parents[1] / "out" / "novel"),
        help="保存目录（默认 Nothing/out/novel）",
    )
    parser.add_argument("--no-save", action="store_true", help="仅打印，不落盘保存")
    gui_group = parser.add_mutually_exclusive_group()
    gui_group.add_argument("--gui", dest="gui", action="store_true", help="启动 GUI 界面交互（默认）")
    gui_group.add_argument("--no-gui", dest="gui", action="store_false", help="关闭 GUI，使用命令行模式")
    parser.set_defaults(gui=True)
    args = parser.parse_args(argv)
    log.info("CLI argv=%r", argv if argv is not None else sys.argv[1:])

    out_dir = Path(args.out_dir)
    prefer_redirect = not args.no_redirect
    save = not args.no_save

    try:
        if args.gui:
            return gui_app(out_dir=out_dir, prefer_redirect=prefer_redirect, save=save)

        # 交互模式：不传 name 时启用
        if not args.name:
            return interactive_flow(out_dir=out_dir, prefer_redirect=prefer_redirect, save=save)

        items = search_novel(args.name)
        if not items:
            print("未搜索到结果")
            return 2

        if args.index < 1 or args.index > len(items):
            print(f"index 超出范围: 1..{len(items)}")
            return 2

        chosen = items[args.index - 1]
        book_id = resolve_book_id(chosen, prefer_redirect=prefer_redirect)
        chapter = fetch_chapter(book_id, chapterid=args.chapterid)

        title = str(chapter.get("chaptername") or chapter.get("title") or "")
        txt = str(chapter.get("txt") or "")

        if save:
            out_path = save_chapter_to_disk(
                base_dir=out_dir,
                novel_name=chosen.articlename or args.name,
                chapterid=args.chapterid,
                chapter=chapter,
            )
            print(f"\n已保存到: {out_path}")

        if title:
            print("\n" + title)
        if txt:
            print("\n" + txt)
        else:
            print("\n(该章节无 txt 字段或为空)")
        return 0
    except BrokenPipeError:
        # 兼容 `... | head` 之类的管道截断
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except BiqugeError as e:
        # 保持与原脚本相近：用户侧可读的错误信息
        log.warning("BiqugeError: %s", e)
        print(str(e))
        return 2


def main(argv: list[str] | None = None) -> int:
    from log import setup_runtime_logging

    log_path = setup_runtime_logging()
    log = logging.getLogger("app")
    log.info("启动 log_file=%s", log_path)
    try:
        code = run_cli(argv)
        log.info("退出 code=%s", code)
        return code
    except SystemExit as e:
        # argparse --help / --version 等会 sys.exit，需在此记录并转为返回码
        c = e.code
        if c is None:
            out = 0
        elif isinstance(c, int):
            out = c
        else:
            out = 1
        log.info("退出 SystemExit code=%s", out)
        return out
    except Exception:
        log.exception("未捕获异常")
        raise
    finally:
        logging.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

