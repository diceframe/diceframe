from __future__ import annotations

import base64
import io
from types import SimpleNamespace

from PIL import Image

from src.webui.services.avatars import avatar_file, save_avatar_upload


def _encoded_image(size: tuple[int, int] = (80, 120), fmt: str = "PNG") -> str:
    output = io.BytesIO()
    Image.new("RGB", size, (71, 93, 118)).save(output, format=fmt)
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_avatar_upload_is_cropped_reencoded_and_content_addressed(tmp_path):
    api = SimpleNamespace(_avatars_dir=tmp_path / "avatars")

    first = save_avatar_upload(api, _encoded_image(), "../portrait.png")
    second = save_avatar_upload(api, _encoded_image(), "portrait.png")

    assert first["ok"] is True
    assert first["portrait"] == second["portrait"]
    assert first["file_name"] == "portrait.png"
    asset_id = first["portrait"]["asset_id"]
    path = avatar_file(api, asset_id)
    assert path is not None
    assert path.parent == api._avatars_dir
    with Image.open(path) as stored:
        assert stored.format == "WEBP"
        assert stored.size == (256, 256)


def test_avatar_upload_rejects_invalid_or_tiny_images(tmp_path):
    api = SimpleNamespace(_avatars_dir=tmp_path / "avatars")

    invalid = save_avatar_upload(api, base64.b64encode(b"not an image").decode("ascii"))
    tiny = save_avatar_upload(api, _encoded_image((16, 16)))

    assert invalid == {"ok": False, "error": "无法读取该头像图片"}
    assert tiny == {"ok": False, "error": "头像尺寸不能小于 32×32"}
    assert avatar_file(api, "../escape") is None
