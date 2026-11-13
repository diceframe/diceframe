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
