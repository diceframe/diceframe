"""Trusted catalog and atomic installer for optional local models."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from src.memory.local_embedding import LocalModelError, list_installed_models


class ModelCatalogError(ValueError):
    """Raised when a catalog or model asset is unsafe or malformed."""


def _safe_id(value: object) -> str:
    model_id = str(value or "").strip()
    path = Path(model_id)
    if not model_id or path.name != model_id or ".." in path.parts:
        raise ModelCatalogError("model id is invalid")
    return model_id


def load_catalog(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelCatalogError("model catalog cannot be read") from exc
    if not isinstance(raw, dict) or int(raw.get("catalog_version", 0)) != 1:
        raise ModelCatalogError("unsupported model catalog version")
    models = raw.get("models")
    if not isinstance(models, list):
        raise ModelCatalogError("model catalog models must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise ModelCatalogError("model catalog entry is invalid")
        model_id = _safe_id(model.get("id"))
        if model_id in seen:
            raise ModelCatalogError(f"duplicate model id: {model_id}")
        seen.add(model_id)
        if model.get("kind") != "embedding" or model.get("backend") != "onnxruntime":
            raise ModelCatalogError(f"unsupported backend for model {model_id}")
        files = model.get("files")
        if not isinstance(files, list) or not files:
            raise ModelCatalogError(f"model {model_id} has no files")
        normalized_files: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                raise ModelCatalogError(f"model {model_id} has an invalid file entry")
            rel = Path(str(item.get("path") or ""))
            url = str(item.get("url") or "").strip()
            parsed = urlsplit(url)
            if not str(item.get("sha256") or "").lower() or len(str(item.get("sha256"))) != 64:
                raise ModelCatalogError(f"model {model_id} file hash is invalid")
            if rel.is_absolute() or ".." in rel.parts or not url.startswith("https://") or not parsed.hostname:
                raise ModelCatalogError(f"model {model_id} file source is unsafe")
            mirrors = item.get("mirrors") or []
            if not isinstance(mirrors, list):
                raise ModelCatalogError(f"model {model_id} mirrors must be a list")
            normalized_mirrors: list[str] = []
            for mirror in mirrors:
                mirror_url = str(mirror or "").strip()
                mirror_host = str(urlsplit(mirror_url).hostname or "").lower()
                if not mirror_url.startswith("https://") or not _trusted_download_host(mirror_host):
                    raise ModelCatalogError(f"model {model_id} mirror source is unsafe")
                normalized_mirrors.append(mirror_url)
            size = item.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ModelCatalogError(f"model {model_id} file size is invalid")
            normalized_files.append({
                "path": rel.as_posix(), "url": url, "mirrors": normalized_mirrors,
                "sha256": str(item["sha256"]).lower(), "size": size,
            })
        result.append({**model, "id": model_id, "files": normalized_files})
    return result


class LocalModelManager:
    """Download only catalog-approved files into the controlled model directory."""

    def __init__(self, models_dir: Path, *, catalog_path: Path | None = None,
                 proxy_url: str = "", mirror_manager: Any | None = None):
        self.models_dir = Path(models_dir)
        self.catalog_path = catalog_path or self.models_dir / "catalog.json"
        self.default_catalog_path = Path(__file__).with_name("default_model_catalog.json")
        self.proxy_url = str(proxy_url or "").strip() or None
        self.mirror_manager = mirror_manager
        self._locks: dict[str, asyncio.Lock] = {}

    def _catalog_entries(self) -> list[dict[str, Any]]:
        """内置/本地目录 + 远端目录缓存合并（W5-2）。

        合并策略：用户自己的 data/models/catalog.json 优先；内置兜底目录允许
        被管理员刷新得到的远端同 id 条目替换，以便模型版本和状态能更新。远端
        新 id 追加到列表末尾。候选条目照常列出，但 install() 拒绝安装。
        """

        custom_catalog = self.catalog_path.exists()
        path = self.catalog_path if custom_catalog else self.default_catalog_path
        entries = load_catalog(path)
        positions = {entry["id"]: index for index, entry in enumerate(entries)}
        remote_path = self.models_dir / "remote-catalog.json"
        if remote_path.exists():
            try:
                for entry in load_catalog(remote_path):
                    model_id = entry["id"]
                    if model_id not in positions:
                        entries.append(entry)
                        positions[model_id] = len(entries) - 1
                    elif not custom_catalog:
                        entries[positions[model_id]] = entry
            except ModelCatalogError:
                # 远端缓存损坏不能拖垮目录：忽略并保留原样（下次刷新覆盖）。
                pass
        return entries

    async def refresh_remote_catalog(self, url: str) -> dict[str, Any]:
        """W5-2：管理员触发拉取远端目录（index 或 diceframe-models 分片）。

        fail closed：仅 https、单响应 ≤ 1MB、必须通过 load_catalog 同一校验；
        通过后原子写入 remote-catalog.json 缓存，读取路径（_catalog_entries）
        永不发起网络请求。
        """

        url = str(url or "").strip()
        parsed = urlsplit(url)
        if not url.startswith("https://") or not parsed.hostname:
            raise ModelCatalogError("remote catalog url must be https")
        async with aiohttp.ClientSession() as session:
            raw = await self._fetch_remote_json(session, url)
            raw = await self._expand_catalog_index(session, url, raw)
        # 与 load_catalog 同一校验（临时落盘复用既有契约，避免第二套规则）。
        staging = self.models_dir / ".remote-catalog.staging.json"
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        try:
            entries = load_catalog(staging)
        finally:
            staging.unlink(missing_ok=True)
        destination = self.models_dir / "remote-catalog.json"
        destination.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"ok": True, "url": url, "models": [entry["id"] for entry in entries]}

    async def _fetch_remote_json(self, session: aiohttp.ClientSession, url: str) -> dict[str, Any]:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ModelCatalogError("remote catalog url must be https")
        payload: bytes
        mirror = self.mirror_manager
        if mirror is not None and parsed.hostname.lower() in {"raw.githubusercontent.com", "github.com"}:
            result = await mirror.fetch_github_url(url, binary=True, max_bytes=1024 * 1024, resolve=False)
            if not result.ok or result.data is None:
                raise ModelCatalogError(f"remote catalog fetch failed: {result.error or 'all mirrors failed'}")
            payload = result.data if isinstance(result.data, bytes) else str(result.data).encode("utf-8")
        else:
            async with session.get(
                url, allow_redirects=True, proxy=self.proxy_url,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise ModelCatalogError(f"remote catalog fetch failed: HTTP {response.status}")
                final = urlsplit(str(response.url))
                if final.scheme != "https" or not final.hostname:
                    raise ModelCatalogError("remote catalog redirected to an unsafe URL")
                payload = await response.content.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ModelCatalogError("remote catalog exceeds 1MB")
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelCatalogError("remote catalog cannot be parsed") from exc
        if not isinstance(raw, dict):
            raise ModelCatalogError("remote catalog root must be an object")
        return raw

    async def _expand_catalog_index(
        self,
        session: aiohttp.ClientSession,
        index_url: str,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve diceframe-models ``catalog/index.json`` into loader shards.

        A direct shard already has ``files`` and is returned unchanged.  The
        repository index instead points at ``models/<id>/<version>.json``;
        those paths are relative to the repository root, not the ``catalog/``
        directory containing the index.
        """

        models = raw.get("models")
        if not isinstance(models, list) or not any(
            isinstance(item, dict) and item.get("file") and "files" not in item
            for item in models
        ):
            return raw
        if int(raw.get("catalog_version", 0)) != 1:
            raise ModelCatalogError("unsupported model catalog version")
        if len(models) > 64:
            raise ModelCatalogError("remote catalog contains too many shards")
        resolved: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pointer in models:
            if not isinstance(pointer, dict):
                raise ModelCatalogError("remote catalog index entry is invalid")
            model_id = _safe_id(pointer.get("id"))
            file_ref = str(pointer.get("file") or "").strip()
            if not file_ref or file_ref.startswith("/") or ".." in Path(file_ref).parts:
                raise ModelCatalogError(f"model {model_id} shard path is invalid")
            if file_ref.startswith("models/"):
                shard_url = urljoin(index_url, "../" + file_ref)
            else:
                shard_url = urljoin(index_url, file_ref)
            shard = await self._fetch_remote_json(session, shard_url)
            shard_models = shard.get("models")
            if not isinstance(shard_models, list):
                raise ModelCatalogError(f"model {model_id} shard is invalid")
            candidates = [
                item for item in shard_models
                if isinstance(item, dict) and str(item.get("id") or "") == model_id
            ]
            if len(candidates) != 1:
                raise ModelCatalogError(f"model {model_id} is missing from its shard")
            if model_id in seen:
                raise ModelCatalogError(f"duplicate model id: {model_id}")
            seen.add(model_id)
            selected = dict(candidates[0])
            for key in ("status", "recommended", "summary"):
                if key in pointer:
                    selected[key] = pointer[key]
            resolved.append(selected)
        return {"catalog_version": 1, "models": resolved}

    def catalog(self) -> list[dict[str, Any]]:
        entries = self._catalog_entries()
        installed = {item["id"]: item for item in list_installed_models(self.models_dir)}
        return [
            {
                "id": entry["id"],
                "display_name": entry.get("display_name") or entry["id"],
                "version": str(entry.get("version") or ""),
                "dimension": int(entry.get("dimension") or 0),
                "license": str(entry.get("license") or ""),
                # W5：卡片展示需要预估下载体积（catalog 固定清单的 size 之和）。
                "size_bytes": sum(int(item.get("size") or 0) for item in entry.get("files") or []),
                "status": installed.get(entry["id"], {}).get("status", "not_installed"),
            }
            for entry in entries
        ]

    def uninstall(self, model_id: str, *, active_model_id: str = "") -> dict[str, Any]:
        """删除已安装的本地模型目录（W5）。

        fail closed：路径越界/未安装拒绝；当前启用的模型必须先切换来源或
        模型再删，避免运行中的 embedding client 指向被删除的权重。
        """

        model_id = _safe_id(model_id)
        root = (self.models_dir / model_id).resolve()
        if root == self.models_dir.resolve() or self.models_dir.resolve() not in root.parents:
            raise ModelCatalogError("model directory escapes the model storage")
        if not root.is_dir():
            raise ModelCatalogError("model is not installed")
        if active_model_id and model_id == _safe_id(active_model_id):
            raise ModelCatalogError("model is currently enabled; switch the source or model first")
        shutil.rmtree(root)
        return {"ok": True, "model_id": model_id}

    async def install(self, model_id: str) -> dict[str, Any]:
        entries = {entry["id"]: entry for entry in self._catalog_entries()}
        model_id = _safe_id(model_id)
        entry = entries.get(model_id)
        if entry is None:
            raise ModelCatalogError("model is not present in the trusted catalog")
        if str(entry.get("status") or "released") == "candidate":
            raise ModelCatalogError("model is a candidate; not installable until benchmarks pass")
        lock = self._locks.setdefault(model_id, asyncio.Lock())
        async with lock:
            version = str(entry.get("version") or "")
            if not version or Path(version).name != version or ".." in Path(version).parts:
                raise ModelCatalogError("model version is invalid")
            destination = (self.models_dir / model_id / version).resolve()
            if self.models_dir.resolve() not in destination.parents:
                raise ModelCatalogError("model destination escapes the model directory")
            destination.mkdir(parents=True, exist_ok=True)
            async with aiohttp.ClientSession() as session:
                for item in entry["files"]:
                    target = (destination / item["path"]).resolve()
                    if destination not in target.parents:
                        raise ModelCatalogError("model file destination escapes install directory")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    part = target.with_name(target.name + ".part")
                    digest = sha256()
                    count = 0
                    try:
                        sources = [item["url"], *item.get("mirrors", [])]
                        last_error = "model download failed"
                        for source in sources:
                            try:
                                async with session.get(
                                    source, allow_redirects=True,
                                    proxy=self.proxy_url,
                                    timeout=aiohttp.ClientTimeout(total=900),
                                ) as response:
                                    final_host = str(urlsplit(str(response.url)).hostname or "").lower()
                                    if not _trusted_download_host(final_host):
                                        raise ModelCatalogError("model download redirected to an untrusted host")
                                    if response.status != 200:
                                        raise ModelCatalogError(f"model download failed: HTTP {response.status}")
                                    with part.open("wb") as handle:
                                        async for chunk in response.content.iter_chunked(1024 * 1024):
                                            count += len(chunk)
                                            if count > int(item["size"]):
                                                raise ModelCatalogError("model download exceeded catalog size")
                                            digest.update(chunk); handle.write(chunk)
                                break
                            except (aiohttp.ClientError, asyncio.TimeoutError, ModelCatalogError) as exc:
                                last_error = str(exc)
                                part.unlink(missing_ok=True)
                                count = 0
                                digest = sha256()
                        else:
                            raise ModelCatalogError(last_error)
                        if count != int(item["size"]) or digest.hexdigest() != item["sha256"]:
                            raise ModelCatalogError(f"model asset verification failed: {item['path']}")
                        part.replace(target)
                    finally:
                        part.unlink(missing_ok=True)
            manifest = {key: value for key, value in entry.items() if key != "files"}
            manifest.update({"files": [{key: value for key, value in item.items() if key != "url"} for item in entry["files"]]})
            manifest.setdefault("onnx_file", "model.onnx")
            manifest.setdefault("tokenizer_file", "tokenizer.json")
            (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                verified = list_installed_models(self.models_dir)
            except LocalModelError as exc:
                raise ModelCatalogError(str(exc)) from exc
            return next(item for item in verified if item["id"] == model_id)


def _trusted_download_host(hostname: str) -> bool:
    return (
        hostname in {"huggingface.co", "hf-mirror.com"}
        or hostname.endswith(".huggingface.co")
        or hostname.endswith(".hf-mirror.com")
        or hostname.endswith(".hf.co")
    )
