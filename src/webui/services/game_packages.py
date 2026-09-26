"""Portable save-package and offline-save access operations."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from src.engine.module_state import ModuleStateError
from src.engine.modules import media

logger = logging.getLogger("trpg")

GameKey = tuple[str, str, str]
AssetImporter = Callable[[bytes], dict[str, Any]]


class GamePackageInstance(Protocol):
    gm_uid: Any
    world_name: str


class SaveZipImporter(Protocol):
    def __call__(
        self,
        payload: bytes,
        *,
        scene_image_importer: AssetImporter,
        map_background_importer: AssetImporter,
    ) -> Awaitable[dict[str, Any]]: ...


@dataclass(frozen=True)
class GamePackageDependencies:
    parse_game_key: Callable[[str], GameKey]
    get_instance: Callable[[GameKey], GamePackageInstance | None]
    state_path_for: Callable[[GameKey], Path]
    import_save_zip: SaveZipImporter
    resolve_scene_image_file: Callable[[Any], Path | None]
    resolve_map_background_file: Callable[[Any], Path | None]
    save_scene_image_upload: Callable[[str], dict[str, Any]]
    save_map_background_upload: Callable[[str], dict[str, Any]]


class GamePackageService:
    """Portable save operations with an explicit, independently fakeable context."""

    def __init__(self, dependencies: GamePackageDependencies) -> None:
        self._dependencies = dependencies

    def _save_path(self, game_key: str) -> tuple[GameKey, Path]:
        parsed = self._dependencies.parse_game_key(game_key)
        return parsed, self._dependencies.state_path_for(parsed)

    def saved_game_access(self, game_key: str) -> dict[str, Any]:
        """Return only the metadata needed for route-level delete authorization."""

        parsed, save_path = self._save_path(game_key)
        instance = self._dependencies.get_instance(parsed)
        exists = instance is not None or save_path.parent.exists()
        if instance is not None:
            return {"exists": True, "gm_uid": str(instance.gm_uid or "")}
        if not exists:
            return {"exists": False, "gm_uid": ""}
        for path in (save_path, save_path.with_name("state.backup.json")):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                return {"exists": True, "gm_uid": str(data.get("gm_uid") or "")}
            except (OSError, ValueError, json.JSONDecodeError):
                logger.warning("读取存档 GM 身份失败: %s", path, exc_info=True)
        return {"exists": True, "gm_uid": ""}

    def export_game_package(self, game_key: str) -> dict[str, Any]:
        """Build a portable ZIP after the transport layer has authorized the GM."""

        parsed, save_path = self._save_path(game_key)
        instance = self._dependencies.get_instance(parsed)
        if instance is None:
            return {"ok": False, "error": "not found", "status": 404}
        state_path = save_path if save_path.exists() else save_path.with_name("state.backup.json")
        if not state_path.exists():
            return {"ok": False, "error": "存档文件不存在", "status": 404}

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8-sig"))
                container = media.payload_container(state_data)
                reference = container.get("scene_image")
                image_path = self._dependencies.resolve_scene_image_file(reference)
                if image_path is not None and isinstance(reference, dict) and reference.get("kind") != "builtin":
                    archive.writestr("scene-image.asset", image_path.read_bytes())
                    container["scene_image"] = {"kind": "save_asset", "path": "scene-image.asset"}
                map_reference = container.get("map_background")
                map_image_path = self._dependencies.resolve_map_background_file(map_reference)
                if map_image_path is not None:
                    archive.writestr("map-background.asset", map_image_path.read_bytes())
                    container["map_background"] = {"kind": "save_asset", "path": "map-background.asset"}
                archive.writestr("state.json", json.dumps(state_data, ensure_ascii=False, indent=2))
                chatlog = save_path.with_name("chatlog.jsonl")
                if chatlog.exists():
                    archive.writestr("chatlog.jsonl", chatlog.read_bytes())
            except ModuleStateError as exc:
                return {"ok": False, "error_code": "UNSUPPORTED_MEDIA_SCHEMA", "error": str(exc), "status": 400}
            except Exception:
                logger.exception("读取存档失败: %s", state_path)
                return {"ok": False, "error": "读取失败，请查看服务器日志", "status": 500}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r"[^\w一-鿿-]+", "_", instance.world_name or "save").strip("_") or "save"
        filename = f"save_{safe_name}_{timestamp}.zip"
        ascii_filename = re.sub(r"[^\x21-\x7e]", "_", filename) or f"save_{timestamp}.zip"
        return {
            "ok": True,
            "filename": filename,
            "ascii_filename": ascii_filename,
            "payload": buffer.getvalue(),
        }

    async def import_game_package(self, payload: bytes) -> dict[str, Any]:
        result = await self._dependencies.import_save_zip(
            payload,
            scene_image_importer=lambda raw: self._dependencies.save_scene_image_upload(
                base64.b64encode(raw).decode("ascii"),
            ),
            map_background_importer=lambda raw: self._dependencies.save_map_background_upload(
                base64.b64encode(raw).decode("ascii"),
            ),
        )
        if result.get("ok") and result.get("game_key"):
            result["game_key"] = "|".join(str(part) for part in result["game_key"])
        return result
