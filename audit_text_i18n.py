"""巡检用户可见文案中的明显乱码。

重点检查：
- Unicode 替换符号 U+FFFD
- 连续 3 个以上问号，通常来自写入编码损坏
- 常见 UTF-8/GBK mojibake 片段，例如“锛”“鍗”“鈥”
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCAN_DIRS = ["static", "src", "templates", "prompts"]
SCAN_FILES = ["web_server.py"]
TEXT_SUFFIXES = {".html", ".css", ".js", ".py", ".json", ".md", ".txt"}

MOJIBAKE_TOKENS = [
    "\u951b",  # 锛
    "\u9347",  # 鍇/鍊 等“鍌”族附近
    "\u934d",  # 鍍
    "\u9357",  # 鍗
    "\u6d93",  # 涓
    "\u7efe",  # 绾
    "\u93c3",  # 鏃
    "\u7470",  # 瑰
    "\u9366",  # 鍦
    "\u9352",  # 鍒
    "\u9435",  # 鐵/鐜附近
    "\u9225",  # 鈥
    "\u99c3",  # 馃
]

QUESTION_RE = re.compile(r"\?{3,}")


def iter_files() -> list[Path]:
    files: list[Path] = []
    for name in SCAN_FILES:
        path = ROOT / name
        if path.exists():
            files.append(path)
    for dirname in SCAN_DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                files.append(path)
    return sorted(set(files))


def find_issues(path: Path) -> list[tuple[int, str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    issues: list[tuple[int, str, str]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if "\ufffd" in line:
            issues.append((line_no, "replacement-char", line.strip()))
            continue
        if QUESTION_RE.search(line):
            issues.append((line_no, "question-run", line.strip()))
            continue
        token = next((t for t in MOJIBAKE_TOKENS if t in line), "")
        if token:
            issues.append((line_no, f"mojibake-token {token}", line.strip()))
    return issues


def main() -> int:
    all_issues: list[tuple[Path, int, str, str]] = []
    for path in iter_files():
        for line_no, kind, sample in find_issues(path):
            all_issues.append((path, line_no, kind, sample))

    print("Text mojibake audit:")
    if not all_issues:
        print("  - none")
        return 0

    for path, line_no, kind, sample in all_issues[:200]:
        rel = path.relative_to(ROOT)
        print(f"  - {rel}:{line_no} [{kind}] {sample[:160]}")
    if len(all_issues) > 200:
        print(f"  - ... plus {len(all_issues) - 200} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
