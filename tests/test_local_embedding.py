from __future__ import annotations

from hashlib import sha256
import json

import pytest

from src.memory.local_embedding import LocalModelError, list_installed_models, verify_local_model


def _manifest(root, *, model_id: str = "demo"):
    model_dir = root / model_id / "1.0.0"
    model_dir.mkdir(parents=True)
    onnx = model_dir / "model.onnx"
    tokenizer = model_dir / "tokenizer.json"
    onnx.write_bytes(b"onnx-placeholder")
    tokenizer.write_text("{}", encoding="utf-8")
    files = []
    for path in (onnx, tokenizer):
        files.append({
            "path": path.name,
            "size": path.stat().st_size,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        })
    (model_dir / "manifest.json").write_text(json.dumps({
        "id": model_id,
        "kind": "embedding",
        "backend": "onnxruntime",
        "version": "1.0.0",
        "files": files,
        "onnx_file": "model.onnx",
        "tokenizer_file": "tokenizer.json",
        "dimension": 2,
    }), encoding="utf-8")


def test_verified_local_model_is_listed(tmp_path):
    _manifest(tmp_path / "models")
    listed = list_installed_models(tmp_path / "models")
    assert listed == [{
        "id": "demo",
        "version": "1.0.0",
        "display_name": "demo",
        "dimension": 2,
        "license": "",
        "status": "installed",
    }]
    verified = verify_local_model(tmp_path / "models", "demo")
    assert verified["manifest"]["id"] == "demo"


def test_invalid_hash_is_visible_but_cannot_be_loaded(tmp_path):
    _manifest(tmp_path / "models")
    manifest = tmp_path / "models" / "demo" / "1.0.0" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")
    listed = list_installed_models(tmp_path / "models")
    assert listed[0]["status"] == "invalid"
    with pytest.raises(LocalModelError, match="hash mismatch"):
        verify_local_model(tmp_path / "models", "demo")


def test_manifest_path_traversal_is_rejected(tmp_path):
    _manifest(tmp_path / "models")
    manifest = tmp_path / "models" / "demo" / "1.0.0" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"][0]["path"] = "../outside.onnx"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LocalModelError, match="unsafe file path"):
        verify_local_model(tmp_path / "models", "demo")
