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


def _manager_with_installed_model(tmp_path) -> tuple["LocalModelManager", str]:
    """构造一个「已安装」的模型目录（manifest + 权重占位，走真实校验器）。"""
    import hashlib
    from src.memory.model_catalog import LocalModelManager

    models_dir = tmp_path / "models"
    version_dir = models_dir / "demo-model" / "1.0.0"
    version_dir.mkdir(parents=True)
    payload = b"weights"
    (version_dir / "model.onnx").write_bytes(payload)
    manifest = {
        "id": "demo-model", "kind": "embedding", "backend": "onnxruntime",
        "display_name": "Demo", "version": "1.0.0", "dimension": 8,
        "max_input_chars": 2048,
        "onnx_file": "model.onnx", "tokenizer_file": "tokenizer.json",
        "files": [{
            "path": "model.onnx", "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }
    (version_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return LocalModelManager(models_dir), "demo-model"


def test_uninstall_removes_installed_model_dir(tmp_path):
    manager, model_id = _manager_with_installed_model(tmp_path)
    result = manager.uninstall(model_id)
    assert result == {"ok": True, "model_id": model_id}
    assert not (manager.models_dir / model_id).exists()
    with pytest.raises(ModelCatalogError, match="not installed"):
        manager.uninstall(model_id)


def test_uninstall_refuses_active_model_and_path_escapes(tmp_path):
    manager, model_id = _manager_with_installed_model(tmp_path)
    with pytest.raises(ModelCatalogError, match="currently enabled"):
        manager.uninstall(model_id, active_model_id=model_id)
    with pytest.raises(ModelCatalogError, match="escapes|invalid"):
        manager.uninstall("..")
    with pytest.raises(ModelCatalogError, match="escapes|not installed"):
        manager.uninstall("missing-model")


def test_catalog_projection_includes_download_size(tmp_path):
    import io as _io
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "catalog_version": 1,
        "models": [{
            "id": "sized", "kind": "embedding", "backend": "onnxruntime",
            "version": "1.0.0", "dimension": 512, "license": "mit",
            "files": [{"path": "model.onnx", "url": "https://huggingface.co/m.onnx",
                       "sha256": "a" * 64, "size": 24010842}],
        }],
    }), encoding="utf-8")
    entries = load_catalog(catalog)
    assert entries[0]["files"][0]["size"] == 24010842


def _write_remote_shard(path, model_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "catalog_version": 1,
        "models": [{
            "id": model_id, "kind": "embedding", "backend": "onnxruntime",
            "version": "0.1.0", "dimension": 384, "license": "apache-2.0",
            "status": "released",
            "files": [{"path": "model.onnx",
                       "url": "https://huggingface.co/demo/model.onnx",
                       "sha256": "b" * 64, "size": 10}],
        }],
    }), encoding="utf-8")


def test_remote_catalog_cache_merges_new_ids_only(tmp_path):
    """远端缓存只补充本地没有的 id，不覆盖本地声明（W5-2 合并策略）。"""
    from src.memory.model_catalog import LocalModelManager

    models_dir = tmp_path / "models"
    _write_remote_shard(models_dir / "remote-catalog.json", "remote-model")
    manager = LocalModelManager(models_dir)
    ids = [entry["id"] for entry in manager._catalog_entries()]
    # 内置默认 catalog 的 bge 在前，远端补充在后。
    assert ids[0] == "bge-small-zh-v1.5"
    assert "remote-model" in ids
    # 同 id 不重复。
    assert len(ids) == len(set(ids))


def test_candidate_entries_are_not_installable(tmp_path):
    from src.memory.model_catalog import LocalModelManager, ModelCatalogError

    models_dir = tmp_path / "models"
    shard = models_dir / "remote-catalog.json"
    _write_remote_shard(shard, "candidate-model")
    raw = json.loads(shard.read_text(encoding="utf-8"))
    raw["models"][0]["status"] = "candidate"
    shard.write_text(json.dumps(raw), encoding="utf-8")
    manager = LocalModelManager(models_dir)
    import asyncio
    with pytest.raises(ModelCatalogError, match="candidate"):
        asyncio.run(manager.install("candidate-model"))


def test_refresh_rejects_non_https_url(tmp_path):
    from src.memory.model_catalog import LocalModelManager, ModelCatalogError

    manager = LocalModelManager(tmp_path / "models")
    import asyncio
    with pytest.raises(ModelCatalogError, match="https"):
        asyncio.run(manager.refresh_remote_catalog("http://evil.example/catalog.json"))


def test_catalog_index_expands_repository_shards(tmp_path, monkeypatch):
    from src.memory.model_catalog import LocalModelManager

    manager = LocalModelManager(tmp_path / "models")
    index_url = "https://raw.githubusercontent.com/diceframe/diceframe-models/main/catalog/index.json"
    index = {
        "catalog_version": 1,
        "models": [{"id": "demo-model", "file": "models/demo-model/1.0.0.json", "status": "released"}],
    }
    shard = {
        "catalog_version": 1,
        "models": [{
            "id": "demo-model", "kind": "embedding", "backend": "onnxruntime",
            "version": "1.0.0", "files": [{
                "path": "model.onnx", "url": "https://huggingface.co/demo/model.onnx",
                "sha256": "a" * 64, "size": 1,
            }],
        }],
    }

    async def fake_fetch(_session, url):
        return index if url == index_url else shard

    monkeypatch.setattr(manager, "_fetch_remote_json", fake_fetch)
    import asyncio
    resolved = asyncio.run(manager._expand_catalog_index(None, index_url, index))
    assert resolved["models"][0]["id"] == "demo-model"
    assert resolved["models"][0]["files"][0]["path"] == "model.onnx"
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
