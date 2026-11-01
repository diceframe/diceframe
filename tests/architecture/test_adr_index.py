"""ADR 目录契约：编号唯一、标题与文件名一致、索引不重复且链接可解析。

两个分支各自新增 ADR 时容易撞号（曾出现两篇 ADR-0012），
入站链接按文件名引用，撞号后只能靠人工发现。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs" / "adr"
_FILE_NUMBER = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
_TITLE_NUMBER = re.compile(r"^# ADR[ -](\d{4})\s*[:：]")
_LINK = re.compile(r"\]\(([^)#\s]+)")


def _adr_files() -> list[Path]:
    return sorted(
        path for path in ADR_DIR.glob("*.md")
        if _FILE_NUMBER.match(path.name) and not path.name.startswith("0000-")
    )


def test_adr_numbers_are_unique() -> None:
    numbers = Counter(_FILE_NUMBER.match(path.name).group(1) for path in _adr_files())
    assert [number for number, count in numbers.items() if count > 1] == []


def test_adr_title_matches_file_number() -> None:
    mismatched = []
    for path in _adr_files():
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        title = _TITLE_NUMBER.match(first_line)
        if title is None or title.group(1) != path.name[:4]:
            mismatched.append(f"{path.name}: {first_line}")
    assert mismatched == []


def test_adr_index_has_no_duplicate_entries_and_links_resolve() -> None:
    readme = ADR_DIR / "README.md"
    lines = [line for line in readme.read_text(encoding="utf-8").splitlines() if "](" in line]
    assert [line for line, count in Counter(lines).items() if count > 1] == []
    missing = [
        target for line in lines for target in _LINK.findall(line)
        if not target.startswith(("http://", "https://"))
        and not (readme.parent / target).resolve().exists()
    ]
    assert missing == []
