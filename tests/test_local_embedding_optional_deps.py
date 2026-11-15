"""本地向量推理的可选依赖契约：默认安装（无 numpy/onnxruntime/tokenizers）时
应用必须能正常导入启动；真正走本地推理时给出带安装提示的错误。"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.memory.local_embedding import LocalModelError, LocalOnnxEmbeddingBackend

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_web_app_imports_without_optional_ml_dependencies():
    """P0 回归：普通 Web 启动路径不得在导入期触发 numpy/onnxruntime/tokenizers。"""
    code = (
        "import sys\n"
        "for name in ('numpy', 'onnxruntime', 'tokenizers'):\n"
        "    sys.modules[name] = None\n"
        "import src.memory.local_embedding\n"
        "import src.memory.model_catalog\n"
        "import src.webui.application\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, result.stderr


def test_embed_reports_missing_numpy_with_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)
    backend = object.__new__(LocalOnnxEmbeddingBackend)
    backend._session = object()
    backend._tokenizer = object()
    backend.max_input_chars = 64
    backend.manifest = {}
    backend.dimension = 0
    with pytest.raises(LocalModelError, match=r"pip install numpy"):
        backend._embed_sync(["hello"])


def _write_verified_model(models_dir: Path, model_id: str = "tiny-embed") -> None:
    root = models_dir / model_id
    root.mkdir(parents=True)
    manifest = {
        "kind": "embedding", "id": model_id, "backend": "onnxruntime",
        "model": "tiny", "version": "1.0.0", "dimension": 2,
        "onnx_file": "model.onnx", "tokenizer_file": "tokenizer.json",
        "files": [],
    }
    for name in ("model.onnx", "tokenizer.json"):
        payload = f"stub-{name}".encode("utf-8")
        (root / name).write_bytes(payload)
        manifest["files"].append({
            "path": name,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_init_missing_tokenizer_runtime_raises_actionable_error(monkeypatch, tmp_path):
    _write_verified_model(tmp_path)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setitem(sys.modules, "tokenizers", None)
    with pytest.raises(LocalModelError, match=r"pip install onnxruntime tokenizers"):
        LocalOnnxEmbeddingBackend(tmp_path, "tiny-embed")
