from __future__ import annotations

import base64
import io

from PIL import Image

from src.webui.services.avatars import AvatarService


def _encoded_image(size: tuple[int, int] = (80, 120), fmt: str = "PNG",
                   color: tuple[int, int, int] = (71, 93, 118)) -> str:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=fmt)
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_avatar_upload_is_cropped_reencoded_and_content_addressed(tmp_path):
    avatars_dir = tmp_path / "avatars"
    service = AvatarService(avatars_dir)

    first = service.save_upload(_encoded_image(), "../portrait.png")
    second = service.save_upload(_encoded_image(), "portrait.png")

    assert first["ok"] is True
    assert first["portrait"] == second["portrait"]
    assert first["file_name"] == "portrait.png"
    asset_id = first["portrait"]["asset_id"]
    path = service.file(asset_id)
    assert path is not None
    assert path.parent == avatars_dir
    with Image.open(path) as stored:
        assert stored.format == "WEBP"
        assert stored.size == (256, 256)


def test_avatar_upload_rejects_invalid_or_tiny_images(tmp_path):
    service = AvatarService(tmp_path / "avatars")

    invalid = service.save_upload(base64.b64encode(b"not an image").decode("ascii"))
    tiny = service.save_upload(_encoded_image((16, 16)))

    assert invalid == {"ok": False, "error": "无法读取该头像图片", "error_code": "avatars_unreadable_image"}
    assert tiny == {"ok": False, "error": "头像尺寸不能小于 32×32", "error_code": "avatars_too_small"}
    assert service.file("../escape") is None


def test_list_and_delete_user_avatars(tmp_path):
    service = AvatarService(tmp_path / "avatars")
    first = service.save_upload(_encoded_image())["portrait"]["asset_id"]
    second = service.save_upload(_encoded_image((90, 90), color=(120, 40, 40)))["portrait"]["asset_id"]
    assert first != second

    listed = service.list_user_avatars()
    assert listed["total"] == 2
    assert {a["asset_id"] for a in listed["avatars"]} == {first, second}
    assert all("size_kb" in a for a in listed["avatars"])

    assert service.delete(first) == {"ok": True}
    assert service.file(first) is None
    assert service.list_user_avatars()["total"] == 1

    assert service.delete(first) == {"ok": False, "error": "头像不存在", "error_code": "avatars_not_found"}
    assert service.delete("../escape") == {"ok": False, "error": "无效的头像 ID", "error_code": "avatars_invalid_id"}
