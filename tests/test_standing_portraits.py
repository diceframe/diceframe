"""GAL 轨道 P8：立绘资产服务（standing_portraits）。

核心契约：
1) 透明通道必须保留（立绘的本质属性，区别于强制 256×256 不透明的头像）；
2) 尺寸与大小 fail closed（上限 2048×4096 / 8MB，下限 64×128）；
3) content-addressed 存储，同内容幂等同 id；
4) 引用校验（asset 必须真实存在）fail closed。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from src.webui.services.standing_portraits import (
    StandingPortraitService,
    save_standing_portrait_upload,
    validate_standing_portrait_ref,
)


def _png_bytes(size: tuple[int, int], with_alpha: bool = True) -> bytes:
    mode = "RGBA" if with_alpha else "RGB"
    color = (200, 100, 50, 0) if with_alpha else (200, 100, 50)
    image = Image.new(mode, size, color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_upload_preserves_alpha_channel(tmp_path: Path):
    result = save_standing_portrait_upload(
        tmp_path, _b64(_png_bytes((512, 1024))), "hero.png",
    )
    assert result["ok"] is True
    asset_id = result["standing_portrait"]["asset_id"]
    stored = tmp_path / f"{asset_id}.webp"
    assert stored.is_file()
    with Image.open(stored) as image:
        assert image.mode == "RGBA"  # 透明保留：不是头像的不透明方形
        assert image.size == (512, 1024)  # 不裁切、不缩放


def test_upload_is_content_addressed_and_idempotent(tmp_path: Path):
    payload = _b64(_png_bytes((300, 800)))
    first = save_standing_portrait_upload(tmp_path, payload)
    second = save_standing_portrait_upload(tmp_path, payload)
    assert first["standing_portrait"]["asset_id"] == second["standing_portrait"]["asset_id"]
    assert len(list(tmp_path.glob("*.webp"))) == 1


def test_upload_rejects_oversize_undersize_and_opaque_formats(tmp_path: Path):
    too_wide = save_standing_portrait_upload(tmp_path, _b64(_png_bytes((3000, 800))))
    assert too_wide["ok"] is False and "尺寸" in too_wide["error"]

    too_small = save_standing_portrait_upload(tmp_path, _b64(_png_bytes((32, 64))))
    assert too_small["ok"] is False and "尺寸" in too_small["error"]

    jpeg = io.BytesIO()
    Image.new("RGB", (512, 1024), (1, 2, 3)).save(jpeg, format="JPEG")
    rejected = save_standing_portrait_upload(tmp_path, _b64(jpeg.getvalue()), "x.jpg")
    assert rejected["ok"] is False and "PNG 或 WebP" in rejected["error"]


def test_reference_validation_fails_closed(tmp_path: Path):
    service = StandingPortraitService(tmp_path)
    saved = service.save_upload(_b64(_png_bytes((200, 500))))
    asset_id = saved["standing_portrait"]["asset_id"]

    assert validate_standing_portrait_ref(None, service.file) is None
    assert validate_standing_portrait_ref({}, service.file) is None
    assert validate_standing_portrait_ref(
        {"kind": "upload", "asset_id": asset_id}, service.file,
    ) == {"kind": "upload", "asset_id": asset_id}
    with pytest.raises(ValueError, match="不存在"):
        validate_standing_portrait_ref(
            {"kind": "upload", "asset_id": "0" * 64}, service.file,
        )
    with pytest.raises(ValueError, match="类型无效"):
        validate_standing_portrait_ref({"kind": "builtin", "id": "x"}, service.file)
    with pytest.raises(ValueError, match="立绘数据无效"):
        validate_standing_portrait_ref("upload:abc", service.file)


def test_service_delete_roundtrip(tmp_path: Path):
    service = StandingPortraitService(tmp_path)
    saved = service.save_upload(_b64(_png_bytes((200, 500))))
    asset_id = saved["standing_portrait"]["asset_id"]
    assert service.file(asset_id) is not None
    assert service.delete(asset_id)["ok"] is True
    assert service.file(asset_id) is None
    assert service.delete(asset_id)["ok"] is False
