"""从官方 SRD 5.2.1 PDF 提取条目基线，用于内容核对与批次冻结。

用法：
    python scripts/srd_extract_baseline.py --pdf <SRD_CC_v5.2.1.pdf> [--out DIR]

子命令式行为：一次运行输出三份 JSON
    toc.json      书签树（章节 / 职业 / 子职，含印刷页码）
    items.json    魔法物品条目（名称 / 类别 / 稀有度 / 是否需同调 / 页码）
    feats.json    专长条目（名称 / 类别 / 前提）

为什么不用默认的 extract_text()：官方 PDF 正文为双栏，默认按行提取会把左右栏
交错，直接解析会产生大量假条目。这里用 visitor_text 取每个文本块的 (x, y)，
按 x 分栏、按 y 拼行还原阅读顺序。

基准：SRD 5.2.1，364 页，SHA-256
8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确提示
    raise SystemExit("需要 pypdf：pip install 'pypdf>=6.19,<7'") from exc

EXPECTED_SHA256 = "8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87"
EXPECTED_PAGES = 364
COLUMN_SPLIT = 260.0
LINE_Y_TOLERANCE = 2.0

HEADER_RE = re.compile(r"^(System Reference Document 5\.2\.1|\d{1,3})(\s+\d{1,3})?$")
ITEM_TYPE_RE = re.compile(
    r"^(Armor|Weapon|Wondrous Item|Potion|Ring|Rod|Scroll|Staff|Wand)\b"
)
RARITY_RE = re.compile(
    r"\b(Artifact|Legendary|Very Rare|Rare|Uncommon|Common|Varies)\b"
)
FEAT_CATEGORY_RE = re.compile(
    r"^(Origin Feat|General Feat|Fighting Style Feat|Epic Boon Feat)\b"
)
CONTINUATION_RE = re.compile(r"^(and|of|the|in|for|or|with|against|from|to)\b", re.I)
PREREQUISITE_RE = re.compile(r"\(Prerequisite:[^)]*\)")

# 魔法物品章节内的表格数据行，不是独立条目
TABLE_ROW_RE = re.compile(
    r"^(Potion Str\. Rarity|Potion HP Regained Rarity|Potion of Giant Strength \(|Potion of Healing \d)"
)

SUBCLASS_TITLES = {
    "Barbarian",
    "Bard",
    "Cleric",
    "Druid",
    "Fighter",
    "Monk",
    "Paladin",
    "Ranger",
    "Rogue",
    "Sorcerer",
    "Warlock",
    "Wizard",
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def page_lines(reader: PdfReader, page_number: int) -> list[str]:
    """按左右栏还原一页的阅读顺序。page_number 为印刷页码（1 起）。"""
    parts: list[tuple[float, float, str]] = []

    def visit(text, cm, tm, font_dict, font_size):  # noqa: ANN001 - pypdf 回调签名
        if text and text.strip():
            parts.append((round(tm[4], 1), round(tm[5], 1), text.strip()))

    reader.pages[page_number - 1].extract_text(visitor_text=visit)
    columns: list[list[tuple[float, float, str]]] = [[], []]
    for x, y, text in parts:
        columns[0 if x < COLUMN_SPLIT else 1].append((y, x, text))

    ordered: list[str] = []
    for column in columns:
        column.sort(key=lambda v: (-v[0], v[1]))
        lines: list[str] = []
        buffer: list[str] = []
        current_y: float | None = None
        for y, _x, text in column:
            if current_y is None or abs(current_y - y) <= LINE_Y_TOLERANCE:
                buffer.append(text)
                current_y = y if current_y is None else current_y
            else:
                lines.append(" ".join(buffer).strip())
                buffer, current_y = [text], y
        if buffer:
            lines.append(" ".join(buffer).strip())
        ordered.extend(line for line in lines if line and not HEADER_RE.match(line))
    return ordered


def extract_toc(reader: PdfReader) -> list[dict]:
    rows: list[dict] = []

    def walk(node, depth: int = 0) -> None:
        if node is None:
            return
        for item in node:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = getattr(item, "title", None)
            if title is None:
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:
                page = None
            rows.append({"depth": depth, "title": str(title), "page": page})
            try:
                children = item.children()
            except Exception:
                children = None
            if children:
                walk(children, depth + 1)

    walk(reader.outline)
    return rows


def looks_like_item_name(line: str) -> bool:
    if not 3 <= len(line) <= 58:
        return False
    if line.endswith((".", ":", ";")):
        return False
    if not line[0].isupper():
        return False
    lowered = line.lower()
    for marker in (
        " you ",
        " your ",
        " the weapon ",
        " this ",
        " if you",
        " when you",
        " while you",
    ):
        if marker in lowered:
            return False
    return True


def extract_items(reader: PdfReader, first_page: int, last_page: int) -> list[dict]:
    seq: list[tuple[int, str]] = []
    for page in range(first_page, last_page + 1):
        seq.extend((page, line) for line in page_lines(reader, page))

    found: list[dict] = []
    index = 0
    while index < len(seq):
        page, line = seq[index]
        if (
            ITEM_TYPE_RE.match(line)
            and index > 0
            and looks_like_item_name(seq[index - 1][1])
        ):
            type_line = line
            cursor = index + 1
            while (
                not RARITY_RE.search(type_line)
                and cursor < len(seq)
                and cursor < index + 3
            ):
                type_line += " " + seq[cursor][1]
                cursor += 1
            if not RARITY_RE.search(type_line):
                index += 1
                continue

            name = seq[index - 1][1]
            back = index - 2
            while (
                back >= 0
                and CONTINUATION_RE.match(seq[back][1])
                and len(seq[back][1]) <= 22
                and not seq[back][1].endswith((".", "\u201d", "\u2019"))
            ):
                name = seq[back][1] + " " + name
                back -= 1

            found.append(
                {
                    "name": name,
                    "page": page,
                    "category": ITEM_TYPE_RE.match(line).group(1),
                    "rarity": RARITY_RE.search(type_line).group(1),
                    "attunement": "Requires Attunement" in type_line,
                }
            )
            index = cursor
            continue
        index += 1

    seen: set[str] = set()
    unique: list[dict] = []
    for item in found:
        if TABLE_ROW_RE.match(item["name"]):
            continue
        key = item["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def extract_feats(reader: PdfReader, first_page: int, last_page: int) -> list[dict]:
    seq: list[str] = []
    for page in range(first_page, last_page + 1):
        seq.extend(page_lines(reader, page))

    found: list[dict] = []
    index = 0
    while index < len(seq):
        if FEAT_CATEGORY_RE.match(seq[index]) and index > 0:
            name = seq[index - 1]
            if len(name) <= 45 and name[:1].isupper():
                header = seq[index]
                cursor = index + 1
                while (
                    cursor < len(seq)
                    and not header.rstrip().endswith(")")
                    and cursor < index + 3
                ):
                    header += " " + seq[cursor]
                    cursor += 1
                match = PREREQUISITE_RE.search(header)
                found.append(
                    {
                        "name": name,
                        "category": FEAT_CATEGORY_RE.match(seq[index]).group(1),
                        "prerequisite": match.group(0) if match else None,
                    }
                )
                index = cursor
                continue
        index += 1

    seen: set[str] = set()
    unique: list[dict] = []
    for item in found:
        key = item["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def chapter_pages(toc: list[dict]) -> dict[str, tuple[int, int]]:
    """返回 {章节标题: (起始页, 结束页)}，只看一级书签。

    结束页取「下一个一级章节的起始页」本身而非前一页：书签页码指向章节标题行，
    而内容常从该页右栏开始、延伸到下一章节标题所在页的左栏。少算一页会截断末尾条目。
    """
    top = [(r["title"], r["page"]) for r in toc if r["depth"] == 1 and r["page"]]
    spans: dict[str, tuple[int, int]] = {}
    for pos, (title, page) in enumerate(top):
        end = top[pos + 1][1] if pos + 1 < len(top) else EXPECTED_PAGES
        spans[title] = (page, end)
    return spans


def main() -> int:
    parser = argparse.ArgumentParser(description="提取官方 SRD 5.2.1 条目基线")
    parser.add_argument(
        "--pdf", required=True, type=Path, help="SRD_CC_v5.2.1.pdf 路径"
    )
    parser.add_argument("--out", default=Path("."), type=Path, help="输出目录")
    parser.add_argument(
        "--skip-hash", action="store_true", help="跳过 SHA-256 校验（仅排障用）"
    )
    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"找不到文件：{args.pdf}")

    if not args.skip_hash:
        actual = digest(args.pdf)
        if actual != EXPECTED_SHA256:
            raise SystemExit(
                "SHA-256 与冻结基准不符，停止提取。\n"
                f"  预期 {EXPECTED_SHA256}\n  实际 {actual}\n"
                "若官方已发布新版，请先更新 SRD_SOURCE_AUDIT 文档再迁移。"
            )

    reader = PdfReader(str(args.pdf))
    if len(reader.pages) != EXPECTED_PAGES:
        raise SystemExit(f"页数不符：预期 {EXPECTED_PAGES}，实际 {len(reader.pages)}")

    toc = extract_toc(reader)
    spans = chapter_pages(toc)
    items_span = spans.get("Magic Items")
    feats_span = spans.get("Feats")
    if not items_span or not feats_span:
        raise SystemExit("书签中找不到 Magic Items / Feats 章节，PDF 结构可能已变")

    items = extract_items(reader, *items_span)
    feats = extract_feats(reader, *feats_span)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("toc.json", toc),
        ("items.json", items),
        ("feats.json", feats),
    ):
        (args.out / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    attuned = sum(1 for item in items if item["attunement"])
    print(f"pages      {len(reader.pages)}")
    print(f"bookmarks  {len(toc)}")
    print(f"items      {len(items)}  (需同调 {attuned})")
    print(f"feats      {len(feats)}")
    print(
        f"magic span p{items_span[0]}-p{items_span[1]} / feats span p{feats_span[0]}-p{feats_span[1]}"
    )
    print(f"output     {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
