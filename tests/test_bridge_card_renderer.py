from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from src.bots.bridge_core import card_renderer
from src.bots.bridge_core.card_renderer import (
    _fit_by_pixel,
    _font_paths,
    _font_supports_cjk,
    _load_font,
    _text_width,
    _wrap_by_pixel,
    cleanup_card_cache,
    render_card_png,
)

# 这些断言要求宿主机存在 CJK 字体（Windows 自带；Docker 镜像装了 fonts-noto-cjk）；
# 纯开发容器没有字体时跳过，而不是让整套测试失败。
CJK_FONT_AVAILABLE = any(Path(path).exists() for path in _font_paths())
requires_cjk_font = pytest.mark.skipif(
    not CJK_FONT_AVAILABLE, reason="no CJK font installed on this host",
)


def test_wrapping_accounts_for_first_line_indent():
    font = _load_font(21)
    image = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(image)
    content_width = 600
    indent_px = _text_width(draw, "　　", font)
    text = "尤洛沿着白马寺藏经阁外墙一路疾行，银针上残留的冷光忽明忽暗，像是在催促她立刻做出决定。"

    lines = _wrap_by_pixel(draw, text, font, content_width, first_line_max_width=content_width - indent_px)

    assert lines
    assert _text_width(draw, lines[0], font) + indent_px <= content_width
    for line in lines[1:]:
        assert _text_width(draw, line, font) <= content_width


def test_fit_by_pixel_adds_ellipsis_within_width():
    # 宽度拟合与字体来源无关：这里直接用 Pillow 默认字体，避免依赖机器字体。
    from PIL import ImageFont

    font = ImageFont.load_default(size=19)
    image = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(image)

    fitted = _fit_by_pixel(draw, "这是一个很长很长很长的卡片副标题", font, 120)

    assert fitted.endswith("…")
    assert _text_width(draw, fitted, font) <= 120


def test_cleanup_card_cache_deletes_old_and_excess_files(tmp_path):
    card_dir = tmp_path / "cards"
    card_dir.mkdir()
    old = card_dir / "card_old.png"
    keep = card_dir / "card_keep.png"
    extra = card_dir / "card_extra.png"
    other = card_dir / "avatar.png"
    for path in (old, keep, extra, other):
        path.write_bytes(b"png")
    now = time.time()
    os.utime(old, (now - 48 * 3600, now - 48 * 3600))
    os.utime(keep, (now, now))
    os.utime(extra, (now - 60, now - 60))

    result = cleanup_card_cache(card_dir, max_age_hours=24, max_files=1)

    assert result["deleted"] == 2
    assert not old.exists()
    assert keep.exists()
    assert not extra.exists()
    assert other.exists()


def test_cleanup_card_cache_delete_all_only_removes_generated_cards(tmp_path):
    card_dir = tmp_path / "cards"
    card_dir.mkdir()
    generated = card_dir / "card_abc.png"
    unrelated = card_dir / "manual.png"
    generated.write_bytes(b"png")
    unrelated.write_bytes(b"do not touch")

    result = cleanup_card_cache(card_dir, delete_all=True)

    assert result["deleted"] == 1
    assert not generated.exists()
    assert unrelated.exists()


# ---------- CJK 字体加载：有字体就用，没有就明确失败（绝不静默方框） ----------

def test_pillow_default_font_is_not_treated_as_cjk() -> None:
    """Pillow 默认字体为中文画 .notdef 方框，必须被识别为"不含 CJK"。"""

    default_font = ImageFont.load_default(size=20)
    assert default_font.getmask("中文测试").getbbox() is not None  # 方框 bbox 非空
    assert _font_supports_cjk(default_font) is False


@requires_cjk_font
def test_load_font_returns_cjk_capable_font() -> None:
    font = _load_font(20)
    assert _font_supports_cjk(font)
    # 中文字形与私用区占位字形不同：确实是 CJK 字体而不是 .notdef。
    assert bytes(font.getmask("中文测试")) != bytes(font.getmask(""))


def test_load_font_without_cjk_font_raises(monkeypatch) -> None:
    monkeypatch.setattr(card_renderer, "_font_paths", lambda: [])
    with pytest.raises(RuntimeError, match="No usable CJK font"):
        _load_font(20)


def test_load_font_rejects_latin_only_font_file(tmp_path, monkeypatch) -> None:
    """候选路径命中只含拉丁字形的字体时也必须拒绝。"""

    latin_only = ImageFont.load_default(size=20)
    candidate = tmp_path / "latin-only.ttf"
    candidate.write_bytes(b"placeholder")
    monkeypatch.setattr(card_renderer, "_font_paths", lambda: [str(candidate)])
    monkeypatch.setattr(ImageFont, "truetype", lambda path, size=20: latin_only)
    with pytest.raises(RuntimeError, match="No usable CJK font"):
        _load_font(20)


def test_render_card_png_propagates_missing_font_error(tmp_path, monkeypatch) -> None:
    """渲染器不得吞掉字体错误：上层据此降级为纯文本，而不是发方框图。"""

    monkeypatch.setattr(card_renderer, "_font_paths", lambda: [])
    with pytest.raises(RuntimeError, match="No usable CJK font"):
        render_card_png(tmp_path, title="DiceFrame 测试", subtitle="机器人帮助")


@requires_cjk_font
def test_render_card_png_chinese_smoke(tmp_path) -> None:
    path = render_card_png(
        tmp_path,
        title="DiceFrame 测试",
        subtitle="机器人帮助",
        lines=["GM 可以使用以下命令", "把一句话，掷成冒险"],
        footer="DiceFrame · 把一句话，掷成冒险",
    )
    assert path.exists()
    assert path.stat().st_size > 0
    with Image.open(path) as image:
        assert image.size[0] > 0 and image.size[1] > 0
