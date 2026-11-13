"""Verified local ONNX embedding backend.

The backend only loads manifests already present below the application's
controlled ``data/models`` directory.  It never executes a manifest command
or follows a URL; downloads/imports are a separate, administrator-only step.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import numpy as np

from src.memory.embedding_profile import PROFILE_ALGORITHM_VERSION


class LocalModelError(ValueError):
    """Raised when a local model is absent, invalid, or cannot be loaded."""


def _safe_relative(value: object) -> Path:
    raw = str(value or "").strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise LocalModelError("local model manifest contains an unsafe file path")
    return path


def _read_manifest(models_dir: Path, model_id: str) -> tuple[Path, dict[str, Any]]:
    if not model_id or Path(model_id).name != model_id or ".." in Path(model_id).parts:
        raise LocalModelError("local model id is invalid")
    root = (models_dir / model_id).resolve()
    base = models_dir.resolve()
    if base not in root.parents:
        raise LocalModelError("local model path escapes the model directory")
    manifests = sorted(root.glob("*/manifest.json")) + ([root / "manifest.json"] if (root / "manifest.json").exists() else [])
    if not manifests:
        raise LocalModelError(f"local model {model_id!r} is not installed")
    manifest_path = manifests[-1].resolve()
    if base not in manifest_path.parents:
        raise LocalModelError("local model manifest path escapes the model directory")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalModelError("local model manifest cannot be read") from exc
    if not isinstance(data, dict) or data.get("kind") != "embedding":
        raise LocalModelError("local model manifest kind must be embedding")
    if str(data.get("id") or "") != model_id or str(data.get("backend") or "") != "onnxruntime":
        raise LocalModelError("local model manifest identity or backend is unsupported")
    return manifest_path.parent, data


def verify_local_model(models_dir: Path, model_id: str) -> dict[str, Any]:
    root, manifest = _read_manifest(models_dir, model_id)
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise LocalModelError("local model manifest must list its files")
    for item in files:
        if not isinstance(item, dict):
            raise LocalModelError("local model file entry is invalid")
        rel = _safe_relative(item.get("path"))
        path = (root / rel).resolve()
        if root not in path.parents:
            raise LocalModelError("local model file escapes its install directory")
        if not path.is_file():
            raise LocalModelError(f"local model file is missing: {rel}")
        expected_size = item.get("size")
        if isinstance(expected_size, int) and path.stat().st_size != expected_size:
            raise LocalModelError(f"local model file size mismatch: {rel}")
        expected_hash = str(item.get("sha256") or "").lower()
        if len(expected_hash) != 64:
            raise LocalModelError(f"local model file hash is missing: {rel}")
        digest = sha256(path.read_bytes()).hexdigest()
        if digest != expected_hash:
            raise LocalModelError(f"local model file hash mismatch: {rel}")
    return {"root": root, "manifest": manifest}


def list_installed_models(models_dir: Path) -> list[dict[str, Any]]:
    """List only verified local manifests; invalid entries are diagnostic-only."""

    if not models_dir.exists():
        return []
    result: list[dict[str, Any]] = []
    for candidate in sorted(models_dir.iterdir()):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        try:
            verified = verify_local_model(models_dir, candidate.name)
            manifest = verified["manifest"]
            result.append({
                "id": candidate.name,
                "version": str(manifest.get("version") or ""),
                "display_name": str(manifest.get("display_name") or candidate.name),
                "dimension": int(manifest.get("dimension") or 0),
                "license": str(manifest.get("license") or ""),
                "status": "installed",
            })
        except LocalModelError as exc:
            result.append({"id": candidate.name, "display_name": candidate.name, "status": "invalid", "error": str(exc)})
    return result


class LocalOnnxEmbeddingBackend:
    """ONNX sentence embedding backend with bounded off-loop CPU work."""

    source = "local"

    def __init__(self, models_dir: Path, model_id: str):
        verified = verify_local_model(models_dir, model_id)
        self.model_id = model_id
        self.root = Path(verified["root"])
        self.manifest = dict(verified["manifest"])
        self.model = str(self.manifest.get("model") or model_id)
        self.max_input_chars = max(1, int(self.manifest.get("max_input_chars") or 2048))
        self.dimension = int(self.manifest.get("dimension") or 0)
        self._session = None
        self._tokenizer = None

        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_path = self.root / _safe_relative(self.manifest.get("onnx_file") or "model.onnx")
        tokenizer_path = self.root / _safe_relative(self.manifest.get("tokenizer_file") or "tokenizer.json")
        if not onnx_path.is_file() or not tokenizer_path.is_file():
            raise LocalModelError("local model must provide onnx_file and tokenizer_file")
        try:
            self._session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        except Exception as exc:
            raise LocalModelError(f"local model failed to load: {exc}") from exc

    def fingerprint(self) -> str:
        payload = {
            "algorithm": PROFILE_ALGORITHM_VERSION,
            "source": "local",
            "id": self.model_id,
            "version": str(self.manifest.get("version") or ""),
            "revision": str(self.manifest.get("revision") or ""),
            "variant": str(self.manifest.get("variant") or ""),
            "max_input_chars": self.max_input_chars,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return f"{PROFILE_ALGORITHM_VERSION}:{sha256(encoded.encode('utf-8')).hexdigest()[:32]}"

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        if self._session is None or self._tokenizer is None:
            raise LocalModelError("local embedding model is closed")
        clipped = [str(text or "")[: self.max_input_chars] for text in texts]
        max_tokens = max(1, int(self.manifest.get("max_input_tokens") or 256))
        self._tokenizer.enable_truncation(max_length=max_tokens)
        self._tokenizer.enable_padding()
        encodings = self._tokenizer.encode_batch(clipped)
        ids = np.asarray([item.ids for item in encodings], dtype=np.int64)
        masks = np.asarray([item.attention_mask for item in encodings], dtype=np.int64)
        type_ids = np.asarray([item.type_ids for item in encodings], dtype=np.int64)
        feeds: dict[str, Any] = {}
        for item in self._session.get_inputs():
            name = item.name
            if name == "input_ids": feeds[name] = ids
            elif name == "attention_mask": feeds[name] = masks
            elif name in {"token_type_ids", "segment_ids"}: feeds[name] = type_ids
        if "input_ids" not in feeds:
            raise LocalModelError("local model ONNX graph has no input_ids")
        output = np.asarray(self._session.run(None, feeds)[0])
        if output.ndim == 3:
            weights = masks[..., None].astype(np.float32)
            output = (output * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1.0)
        if output.ndim != 2:
            raise LocalModelError("local model output must be [batch, dimension]")
        norms = np.linalg.norm(output, axis=1, keepdims=True)
        output = output / np.maximum(norms, 1e-12)
        vectors = output.astype(np.float32).tolist()
        if self.dimension and any(len(vector) != self.dimension for vector in vectors):
            raise LocalModelError("local model output dimension does not match manifest")
        return vectors

    async def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, texts)

    async def embed(self, text: str) -> list[float] | None:
        result = await self.embed_batch([text])
        return result[0] if result else None

    async def close(self) -> None:
        self._session = None
        self._tokenizer = None
