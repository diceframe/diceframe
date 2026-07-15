"""群聊 Bot 图片卡片渲染。

优先用 Pillow 生成 PNG；如果运行环境缺少字体，仍会尽量用系统字体。
调用方应把图片发送失败视为可降级事件，继续发送纯文本。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Iterable


BRAND_NAME = "DiceFrame"
BRAND_SLOGAN = "把一句话，掷成冒险"
BRAND_FOOTER = f"{BRAND_NAME} · {BRAND_SLOGAN}"


def _font_paths() -> list[str]:
    return [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]


def _load_font(size: int):
    from PIL import ImageFont

    for path in _font_paths():
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_card_png(
    out_dir: Path,
    *,
    title: str,
    subtitle: str = "",
    lines: Iterable[str] = (),
    footer: str = "",
    hint: list[tuple[str, str]] | None = None,
) -> Path:
    """渲染一张适合 QQ 发送的 PNG 卡片，返回文件路径。"""
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    title = str(title or BRAND_NAME)
    subtitle = str(subtitle or "")
    # 保留空串作为段落分隔标记（渲染时段间留空行）；丢弃只含空白的行
    # 元素可为 str（正文）或 (text, True)（小字号辅助行，如建议项）
    raw_lines: list[tuple[str, bool]] = []
    for line in lines:
        if isinstance(line, tuple):
            text, small = line
        else:
            text, small = line, False
        text = str(text)
        if text == "" or text.strip():
            raw_lines.append((text, bool(small)))
    footer = str(footer or "")
    # 可选行动两列：每对 (左, 右)，渲染在正文最下方、footer 前
    hint_pairs: list[tuple[str, str]] = []
    for pair in hint or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            left = str(pair[0] or "").strip()
            right = str(pair[1] or "").strip()
            if left or right:
                hint_pairs.append((left, right))

    width = 660
    pad = 30
    title_font = _load_font(29)
    sub_font = _load_font(19)
    body_font = _load_font(21)
    small_font = _load_font(17)
    foot_font = _load_font(16)
    line_gap = 10
    paragraph_gap = 18

    measure = Image.new("RGB", (10, 10))
    measure_draw = ImageDraw.Draw(measure)
    content_width = width - pad * 2
    indent_px = measure_draw.textbbox((0, 0), "　　", font=body_font)[2]
    wrapped: list[tuple[str, bool]] = []
    for text, small in raw_lines:
        if text == "":
            wrapped.append(("", False))  # 段落分隔
        else:
            font = small_font if small else body_font
            wrapped.extend(
                (w, small)
                for w in _wrap_by_pixel(
                    measure_draw,
                    text,
                    font,
                    content_width,
                    first_line_max_width=content_width - indent_px,
                )
            )

    title_height = 40
    subtitle_height = 28 if subtitle else 0
    body_height = sum(
        paragraph_gap if ln == "" else ((19 if sm else 23) + line_gap)
        for ln, sm in wrapped
    ) or (23 + line_gap)
    hint_line_height = 19 + line_gap
    hint_block_height = 0
    if hint_pairs:
        hint_block_height = paragraph_gap + hint_line_height + len(hint_pairs) * hint_line_height
    footer_height = 40 if footer else 14
    height = pad * 2 + title_height + subtitle_height + 22 + body_height + hint_block_height + footer_height
    height = max(240, min(6000, height))

    accent = _accent_for(title + subtitle)
    image = Image.new("RGB", (width, height), accent["bg"])
    draw = ImageDraw.Draw(image)

    _paint_background(draw, width, height, accent)

    card_margin = 12
    card_box = (card_margin, card_margin, width - card_margin, height - card_margin)
    draw.rounded_rectangle(card_box, radius=18, fill=accent["panel"], outline=accent["outline"], width=1)
    draw.line((pad, pad + 42, width - pad, pad + 42), fill=accent["line"], width=1)
    draw.text((pad, pad - 4), _fit_by_pixel(measure_draw, title, title_font, content_width), font=title_font, fill=accent["title"])
    y = pad + title_height
    if subtitle:
        draw.text((pad, y), _fit_by_pixel(measure_draw, subtitle, sub_font, content_width), font=sub_font, fill=accent["muted"])
        y += subtitle_height
    y += 16

    paragraph_start = True
    for index, (line, is_small) in enumerate(wrapped[:120]):
        if line == "":
            y += paragraph_gap  # 段落分隔：段间留空行
            paragraph_start = True
            continue
        font = small_font if is_small else body_font
        if index and line.lstrip().startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "- ", "· ")):
            y += 2
        draw.text((pad + (indent_px if paragraph_start else 0), y), line, font=font, fill=accent["body"])
        paragraph_start = False
        y += (19 if is_small else 23) + line_gap
    # 可选行动两列（最下面，footer 前）：一行两个选项
    if hint_pairs:
        y += paragraph_gap
        draw.text((pad, y), "不知道写什么？可参考", font=small_font, fill=accent["muted"])
        y += hint_line_height
        col_gap = 24
        col_w = (width - pad * 2 - col_gap) // 2
        right_x = pad + col_w + col_gap
        for left, right in hint_pairs:
            if left:
                draw.text((pad, y), _fit_by_pixel(measure_draw, left, small_font, col_w), font=small_font, fill=accent["body"])
            if right:
                draw.text((right_x, y), _fit_by_pixel(measure_draw, right, small_font, col_w), font=small_font, fill=accent["body"])
            y += hint_line_height
    if footer:
        draw.text((pad, height - 36), _fit_by_pixel(measure_draw, footer, foot_font, content_width), font=foot_font, fill=accent["muted"])

    digest = hashlib.sha1((title + subtitle + "\n".join(t for t, _ in raw_lines) + footer + str(time.time())).encode("utf-8")).hexdigest()[:12]
    path = out_dir / f"card_{digest}.png"
    image.save(path, "PNG")
    return path


def cleanup_card_cache(
    out_dir: Path,
    *,
    max_age_hours: float = 24,
    max_files: int = 200,
    delete_all: bool = False,
) -> dict[str, int]:
    """清理 QQ 卡片 PNG 缓存。

    只处理当前卡片目录下的 ``card_*.png``，不递归，不碰用户上传或其他资源。
    ``max_age_hours <= 0`` 表示不按时间清理；``max_files <= 0`` 表示不按数量清理。
    """
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return {"scanned": 0, "deleted": 0, "kept": 0, "bytes_deleted": 0}

    files = [path for path in out_dir.glob("card_*.png") if path.is_file()]
    scanned = len(files)
    deleted = 0
    bytes_deleted = 0
    now = time.time()
    cutoff = now - max_age_hours * 3600 if max_age_hours > 0 else None
    by_mtime_desc = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    keep_by_count = set(by_mtime_desc[:max_files]) if max_files > 0 else set(files)

    for path in files:
        should_delete = delete_all
        if not should_delete and cutoff is not None:
            try:
                should_delete = path.stat().st_mtime < cutoff
            except FileNotFoundError:
                continue
        if not should_delete and max_files > 0:
            should_delete = path not in keep_by_count
        if not should_delete:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            deleted += 1
            bytes_deleted += size
        except FileNotFoundError:
            continue

    return {
        "scanned": scanned,
        "deleted": deleted,
        "kept": max(0, scanned - deleted),
        "bytes_deleted": bytes_deleted,
    }


def _wrap_by_pixel(draw, text: str, font, max_width: int, first_line_max_width: int | None = None) -> list[str]:
    """按像素宽度换行，比固定字数更适合中英文混排。"""
    text = str(text or "").strip()
    if not text:
        return []
    max_width = max(1, int(max_width))
    first_line_max_width = max_width if first_line_max_width is None else max(1, int(first_line_max_width))
    result: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        bbox = draw.textbbox((0, 0), trial, font=font)
        line_max_width = first_line_max_width if not result else max_width
        if bbox[2] - bbox[0] <= line_max_width or not current:
            current = trial
            continue
        result.append(current)
        current = char
    if current:
        result.append(current)
    return result


def _fit_by_pixel(draw, text: str, font, max_width: int) -> str:
    """把单行文本压进指定像素宽度，超出时加省略号。"""
    text = str(text or "").strip()
    max_width = max(1, int(max_width))
    if not text:
        return ""
    if _text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "…"
    ellipsis_width = _text_width(draw, ellipsis, font)
    current = ""
    for char in text:
        trial = current + char
        if _text_width(draw, trial, font) + ellipsis_width > max_width:
            break
        current = trial
    return (current.rstrip() + ellipsis) if current else ellipsis


def _text_width(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), str(text or ""), font=font)
    return bbox[2] - bbox[0]


def _accent_for(seed: str) -> dict[str, tuple[int, int, int]]:
    # 保持克制：背景和文字固定，只让细边框有一点点轻微色差。
    palettes = [
        {
            "bg": (246, 244, 239),
            "panel": (255, 254, 251),
            "outline": (221, 215, 205),
            "title": (39, 35, 31),
            "body": (58, 53, 48),
            "muted": (126, 116, 104),
            "line": (232, 226, 216),
        },
        {
            "bg": (244, 247, 250),
            "panel": (255, 255, 255),
            "outline": (210, 219, 229),
            "title": (34, 39, 46),
            "body": (53, 59, 67),
            "muted": (112, 124, 138),
            "line": (225, 231, 238),
        },
        {
            "bg": (244, 248, 244),
            "panel": (255, 255, 253),
            "outline": (211, 224, 211),
            "title": (34, 43, 35),
            "body": (53, 64, 54),
            "muted": (111, 130, 111),
            "line": (225, 235, 225),
        },
        {
            "bg": (249, 246, 241),
            "panel": (255, 254, 252),
            "outline": (226, 215, 199),
            "title": (45, 38, 31),
            "body": (65, 56, 48),
            "muted": (136, 119, 100),
            "line": (235, 227, 216),
        },
    ]
    value = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:2], 16)
    return palettes[value % len(palettes)]


def _paint_background(draw, width: int, height: int, accent: dict[str, tuple[int, int, int]]) -> None:
    bg = accent["bg"]
    draw.rectangle((0, 0, width, height), fill=bg)
