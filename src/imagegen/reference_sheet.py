"""Ephemeral, numbered portrait sheets; never a layout for the output image."""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw, ImageOps

from .contracts import ImageReference


class ReferenceSheetError(ValueError):
    pass


def combine_portrait_references(references: tuple[ImageReference, ...]) -> ImageReference:
    """Fit portraits without cropping; Ref N maps to the original input order."""
    if not 2 <= len(references) <= 8:
        raise ReferenceSheetError("头像拼接需要 2–8 张参考图")
    columns = min(3, len(references))
    rows = math.ceil(len(references) / columns)
    tile, padding, label_height = 256, 12, 28
    cell_width, cell_height = tile + padding * 2, tile + padding * 2 + label_height
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    for index, reference in enumerate(references):
        try:
            with Image.open(io.BytesIO(reference.content)) as source:
                if source.width * source.height > 16_000_000:
                    raise ReferenceSheetError(f"参考头像 {index + 1} 的像素尺寸过大")
                portrait = ImageOps.exif_transpose(source).convert("RGBA")
                portrait.thumbnail((tile, tile), Image.Resampling.LANCZOS)
                x = (index % columns) * cell_width + padding
                y = (index // columns) * cell_height + padding
                sheet.paste(
                    portrait,
                    (x + (tile - portrait.width) // 2, y + (tile - portrait.height) // 2),
                    portrait,
                )
                # Scale the bundled bitmap font so labels stay readable
                # without depending on OS fonts or Chinese font availability.
                label = Image.new("RGB", (72, 14), "white")
                ImageDraw.Draw(label).text((0, 0), f"Ref {index + 1}", fill="black")
                sheet.paste(label.resize((144, label_height)), (x, y + tile + 4))
        except ReferenceSheetError:
            raise
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ReferenceSheetError(f"参考头像 {index + 1} 无法读取，请重新上传") from exc
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=90)
    body = buffer.getvalue()
    if len(body) > 3 * 1024 * 1024:
        raise ReferenceSheetError("拼接后的头像参考图超过 3 MB")
    return ImageReference(
        character_id="", content=body, content_type="image/jpeg", file_name="portrait-references.jpg",
    )
