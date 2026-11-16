from __future__ import annotations

import json

import pytest

from src.memory.model_catalog import ModelCatalogError, load_catalog


def test_catalog_rejects_non_https_or_path_traversal(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "catalog_version": 1,
        "models": [{
            "id": "demo", "kind": "embedding", "backend": "onnxruntime",
            "version": "1.0.0", "files": [{
                "path": "model.onnx", "url": "http://example.invalid/model.onnx",
                "sha256": "0" * 64, "size": 1,
            }],
        }],
    }), encoding="utf-8")
    with pytest.raises(ModelCatalogError, match="unsafe"):
        load_catalog(catalog)


def test_catalog_normalizes_fixed_asset_metadata(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "catalog_version": 1,
        "models": [{
            "id": "demo", "kind": "embedding", "backend": "onnxruntime",
            "version": "1.0.0", "files": [{
                "path": "model.onnx", "url": "https://huggingface.co/model.onnx",
                "mirrors": ["https://hf-mirror.com/model.onnx"],
                "sha256": "A" * 64, "size": 1,
            }],
        }],
    }), encoding="utf-8")
    file = load_catalog(catalog)[0]["files"][0]
    assert file["sha256"] == "a" * 64
    assert file["mirrors"] == ["https://hf-mirror.com/model.onnx"]


def test_catalog_rejects_untrusted_mirror(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "catalog_version": 1,
        "models": [{
            "id": "demo", "kind": "embedding", "backend": "onnxruntime",
            "version": "1.0.0", "files": [{
                "path": "model.onnx", "url": "https://huggingface.co/model.onnx",
                "mirrors": ["https://evil.example/model.onnx"],
                "sha256": "A" * 64, "size": 1,
            }],
        }],
    }), encoding="utf-8")
    with pytest.raises(ModelCatalogError, match="mirror source"):
        load_catalog(catalog)


# ---- 模型安装路由鉴权回归 ----

def _install_request(*, owner: bool):
    class FakeRequest:
        def __init__(self):
            self.headers = {"X-TRPG-Confirm": "true"}
            self.match_info = {"model_id": "demo"}
            self.app = {}  # 无 subsystems：若鉴权放行，_manager 会抛 ModelCatalogError → 422

        def get(self, key, default=None):
            return owner if key == "owner_authenticated" else default

    return FakeRequest()


@pytest.mark.asyncio
async def test_embedding_model_install_requires_owner():
    from src.webui.routes import embedding_models

    # 未通过 owner 鉴权时必须 403，且不能继续触达模型存储（否则会是 422）
    response = await embedding_models.api_embedding_model_install(_install_request(owner=False))
    assert response.status == 403
    assert json.loads(response.text)["error"] == "owner authentication required"


@pytest.mark.asyncio
async def test_embedding_model_install_proceeds_after_owner_check():
    from src.webui.routes import embedding_models

    # owner 鉴权通过后进入安装流程：存储未就绪 → 422（证明鉴权不是永久挡板）
    response = await embedding_models.api_embedding_model_install(_install_request(owner=True))
    assert response.status == 422
