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
