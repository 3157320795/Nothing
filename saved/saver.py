from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.common import safe_path_part


def save_chapter_to_disk(
    *,
    base_dir: Path,
    novel_name: str,
    chapterid: int,
    chapter: dict[str, Any],
) -> Path:
    base_dir = base_dir.resolve()
    novel_dir = base_dir / safe_path_part(novel_name, fallback="novel")

    chapter_name = str(chapter.get("chaptername") or chapter.get("title") or "").strip()
    chapter_dir_name = f"{chapterid:04d}_{safe_path_part(chapter_name, fallback=f'chapter_{chapterid}')}"
    chapter_dir = novel_dir / chapter_dir_name
    chapter_dir.mkdir(parents=True, exist_ok=True)

    (chapter_dir / "meta.json").write_text(
        json.dumps(chapter, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    txt = str(chapter.get("txt") or "")
    (chapter_dir / "content.txt").write_text(txt.replace("\r\n", "\n"), encoding="utf-8")
    return chapter_dir


__all__ = ["save_chapter_to_disk"]

