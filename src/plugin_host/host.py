"""Manifest-driven child-process plugin host."""

from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .marketplace import PluginMarketplace
from .mirrors import MirrorManager
from .package_limits import (
    MAX_PLUGIN_ARCHIVE_FILES,
    MAX_PLUGIN_FILE_BYTES,
    MAX_PLUGIN_PACKAGE_BYTES,
    MAX_PLUGIN_PATH_CHARS,
    MAX_PLUGIN_UNPACKED_BYTES,
)
from .policy import PERMISSION_DETAILS, effective_plugin_permissions
from .registry import ContributionRegistry, validate_contributes
from .runtime_protocol import (
    DEFAULT_RPC_TIMEOUT,
    MAX_RPC_MESSAGE_BYTES,
    PLUGIN_PROTOCOL_VERSION,
    JsonRpcStdioClient,
    PluginProtocolError,
    validate_tool_arguments,
)
from .support import PLUGIN_TYPE_SUPPORT, plugin_type_support

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ALLOWED_CONTROLS = {"switch", "text", "secret", "number", "select", "string-list"}
_PLUGIN_TYPES = set(PLUGIN_TYPE_SUPPORT)
_STATIC_PLUGIN_TYPES = {"content-pack", "theme", "map-pack"}
_RPC_PLUGIN_TYPES = {"tool"}
_ALLOWED_PERMISSIONS = PERMISSION_DETAILS

_SAFE_PARENT_ENV = {
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TZ",
    "WINDIR",
}


@dataclass
class PluginRuntime:
    manifest: dict[str, Any]
    schema: dict[str, Any]
    directory: Path
    config: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    process: asyncio.subprocess.Process | None = None
    monitor_task: asyncio.Task | None = None
    rpc_client: JsonRpcStdioClient | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    status: str = "disabled"
    error: str = ""


class PluginHost:
    def __init__(self, plugins_dir: Path, data_dir: Path, *, base_env: dict[str, str] | None = None) -> None:
        self.plugins_dir = plugins_dir
        self.data_dir = data_dir
        self.base_env = base_env or {}
        self.plugins: dict[str, PluginRuntime] = {}
        self.logger = logging.getLogger("trpg.plugins")
        self.mirrors = MirrorManager(self.data_dir / "_marketplace" / "mirrors.json")
        self.marketplace = PluginMarketplace(self.mirrors)
        self.contributions = ContributionRegistry()
        self._api_tokens: dict[str, str] = {}

    def discover(self) -> list[dict[str, Any]]:
        self.plugins.clear()
        self.contributions.clear()
        if not self.plugins_dir.exists():
            return []
        for manifest_path in sorted(self.plugins_dir.glob("*/plugin.json")):
            try:
                plugin_id, runtime = self._load_runtime(manifest_path.parent)
                runtime.config, runtime.secrets = self._load_config(plugin_id, runtime.schema)
                runtime.status = self._status_for_enabled(runtime)
                self.plugins[plugin_id] = runtime
                if runtime.status == "active":
                    self._register_contributions(plugin_id, runtime)
            except Exception as exc:
                self.logger.exception("插件加载失败: %s", manifest_path)
                fallback_id = manifest_path.parent.name
                self.plugins[fallback_id] = PluginRuntime(
                    {"id": fallback_id, "name": fallback_id, "version": "?", "description": "插件清单无效"},
                    {"type": "object", "properties": {}}, manifest_path.parent,
                    status="failed", error=str(exc),
                )
        return self.list_public()

    def list_public(self) -> list[dict[str, Any]]:
        return [self.public_detail(plugin_id) for plugin_id in self.plugins]

    def public_detail(self, plugin_id: str) -> dict[str, Any]:
        runtime = self._require(plugin_id)
        if runtime.process and runtime.process.returncode is not None and runtime.status == "running":
            runtime.status = "failed"
            runtime.error = f"插件进程已退出，code={runtime.process.returncode}"
        public_config = dict(runtime.config)
        for key, field_schema in runtime.schema.get("properties", {}).items():
            if self._sensitive(field_schema):
                value = runtime.secrets.get(key, "")
                public_config[key] = {"configured": bool(value), "masked": f"***{value[-4:]}" if value else ""}
        return {
            "id": plugin_id,
            "name": runtime.manifest.get("name", plugin_id),
            "version": runtime.manifest.get("version", ""),
            "description": runtime.manifest.get("description", ""),
            "plugin_type": self._plugin_type(runtime.manifest),
            "support": plugin_type_support(self._plugin_type(runtime.manifest)),
            "has_entrypoint": self._has_entrypoint(runtime.manifest),
            "enabled": bool(runtime.config.get("enabled")),
            "running": bool(runtime.process and runtime.process.returncode is None),
            "status": runtime.status,
            "error": runtime.error,
            "schema": runtime.schema,
            "config": public_config,
            "capabilities": runtime.manifest.get("capabilities", []),
            "permissions": self._plugin_permissions(runtime),
            "permission_details": self._plugin_permission_details(runtime),
            "tools": [dict(tool) for tool in runtime.tools],
            "contributions": [item.to_dict() for item in self.contributions.list() if item.plugin_id == plugin_id],
            "docs": runtime.manifest.get("docs", ""),
        }

    def list_contributions(self, kind: str = "") -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.contributions.list(kind)]

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for plugin_id, runtime in self.plugins.items():
            if self._plugin_type(runtime.manifest) != "tool" or runtime.status != "running":
                continue
            for descriptor in runtime.tools:
                tools.append({
                    **descriptor,
                    "plugin_id": plugin_id,
                    "plugin_name": str(runtime.manifest.get("name") or plugin_id),
                })
        return tools

    async def call_tool(
        self,
        plugin_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime = self._require(plugin_id)
        if self._plugin_type(runtime.manifest) != "tool":
            raise ValueError("该插件不是 tool 类型")
        if runtime.status != "running" or not runtime.rpc_client:
            raise ValueError("工具插件尚未运行或初始化失败")
        if not isinstance(arguments, dict):
            raise ValueError("工具 arguments 必须是对象")
        if context is not None and not isinstance(context, dict):
            raise ValueError("工具 context 必须是对象")
        descriptor = next((item for item in runtime.tools if item.get("name") == tool_name), None)
        if not descriptor:
            raise KeyError(f"工具不存在：{tool_name}")
        validate_tool_arguments(descriptor["input_schema"], arguments)
        try:
            result = await runtime.rpc_client.request(
                "tool.call",
                {"name": tool_name, "arguments": arguments, "context": context or {}},
                timeout=DEFAULT_RPC_TIMEOUT,
            )
            if not isinstance(result, dict):
                raise PluginProtocolError("工具必须返回 JSON 对象")
        except PluginProtocolError as exc:
            await self._fail_rpc_runtime(runtime, str(exc))
            raise
        return result

    def contribution_path(self, kind: str, key: str) -> Path | None:
        item = self.contributions.find(kind, key)
        return item.path if item else None

    def load_world_template(self, world_id: str) -> dict[str, Any] | None:
        path = self.contribution_path("world_template", world_id)
        if not path or not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        rule_id = str(data.get("default_rule") or "")
        rule_path = self.contribution_path("rule", rule_id) if rule_id else None
        if rule_path:
            data = dict(data)
            data["_diceframe_rule_path"] = str(rule_path)
        return data

    def list_themes(self) -> list[dict[str, Any]]:
        themes = []
        for item in self.contributions.list("theme"):
            try:
                data = json.loads(item.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                theme_id = str(data.get("id") or item.key).strip()
                themes.append({
                    "id": theme_id,
                    "name": str(data.get("name") or item.title or theme_id),
                    "description": str(data.get("description") or item.description or ""),
                    "plugin_id": item.plugin_id,
                    "plugin_name": item.plugin_name,
                    "tokens": self._sanitize_theme_tokens(data),
                })
            except Exception:
                self.logger.warning("插件主题读取失败: %s", item.path, exc_info=True)
        return themes

    def list_map_assets(self, world_id: str = "") -> dict[str, list[dict[str, Any]]]:
        locations = [item for item in self._map_json_items("map_location", world_id)]
        return {
            "locations": locations,
            "icons": [self._asset_item(item) for item in self.contributions.list("map_icon")],
            "scenes": [self._asset_item(item) for item in self.contributions.list("map_scene")],
            "grids": [self._asset_item(item) for item in self.contributions.list("map_grid")],
        }

    def list_content_resources(
        self,
        kind: str = "",
        *,
        world_id: str = "",
        rule_id: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        allowed = {
            "character_template",
            "npc",
            "item",
            "spell",
            "class",
        }
        kinds = [kind] if kind in allowed else sorted(allowed)
        return {
            name: self._content_json_items(name, world_id=world_id, rule_id=rule_id)
            for name in kinds
        }

    def get_content_resource(self, kind: str, key: str, *, plugin_id: str = "") -> dict[str, Any] | None:
        allowed = {
            "character_template",
            "npc",
            "item",
            "spell",
            "class",
        }
        kind = (kind or "").strip()
        key = (key or "").strip()
        plugin_id = (plugin_id or "").strip()
        if kind not in allowed or not key:
            return None
        item = self.contributions.find(kind, key)
        if not item or (plugin_id and item.plugin_id != plugin_id):
            return None
        resources = self._content_json_items(kind)
        return next(
            (
                resource for resource in resources
                if str(resource.get("id") or "") == key
                and (not plugin_id or str(resource.get("plugin_id") or "") == plugin_id)
            ),
            None,
        )

    def public_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/").strip("/")
        target = (self.plugins_dir / plugin_id / normalized).resolve()
        self._ensure_inside(self.plugins_dir / plugin_id, target)
        if not target.exists() or not target.is_file() or target.is_symlink():
            raise KeyError("插件资源不存在")
        for item in self.contributions.list():
            if item.plugin_id == plugin_id and item.path == target:
                return target
        raise KeyError("插件资源未声明为可访问贡献")

    async def install_from_zip(self, payload: bytes, *, overwrite: bool = False, allow_any_root: bool = False) -> dict[str, Any]:
        if not payload:
            raise ValueError("插件包为空")
        if len(payload) > MAX_PLUGIN_PACKAGE_BYTES:
            raise ValueError("插件包不能超过 20 MB")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="plugin-install-", dir=str(self.data_dir)) as temp_name:
            temp_dir = Path(temp_name)
            self._extract_zip(payload, temp_dir)
            source_dir = self._find_install_root(temp_dir)
            plugin_id, _runtime = self._load_runtime(source_dir, require_directory_match=False)
            if not allow_any_root and source_dir != temp_dir and source_dir.name != plugin_id:
                raise ValueError("插件包顶层目录名必须与插件 ID 一致")
            target_dir = (self.plugins_dir / plugin_id).resolve()
            self._ensure_inside(self.plugins_dir, target_dir)
            if target_dir.exists() and not overwrite:
                raise ValueError(f"插件 {plugin_id} 已存在；如需更新请启用覆盖安装")

            staging_dir = (self.plugins_dir / f".{plugin_id}.installing-{secrets.token_hex(6)}").resolve()
            backup_dir = (self.plugins_dir / f".{plugin_id}.backup-{secrets.token_hex(6)}").resolve()
            self._ensure_inside(self.plugins_dir, staging_dir)
            self._ensure_inside(self.plugins_dir, backup_dir)
            shutil.copytree(source_dir, staging_dir)
            try:
                if target_dir.exists():
                    if plugin_id in self.plugins:
                        await self.stop(plugin_id)
                    target_dir.rename(backup_dir)
                staging_dir.rename(target_dir)
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
            except Exception:
                if target_dir.exists() and not (target_dir / "plugin.json").exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                if backup_dir.exists() and not target_dir.exists():
                    backup_dir.rename(target_dir)
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)
                raise

        self.discover()
        return self.public_detail(plugin_id)

    async def marketplace_plugins(self) -> dict[str, Any]:
        listing = await self.marketplace.list_plugins()
        if listing.get("ok"):
            installed = set(self.plugins)
            for item in listing.get("plugins", []):
                item["installed"] = item.get("id") in installed
                if item["installed"]:
                    current = self.plugins[item["id"]].manifest
                    item["installed_version"] = current.get("version", "")
                    metadata = self._load_marketplace_metadata(item["id"])
                    item["installed_commit_sha"] = metadata.get("commit_sha", "")
                    item["installed_update_policy"] = metadata.get("update_policy", "")
        return listing

    async def install_from_marketplace(self, plugin_id: str, *, overwrite: bool = False) -> dict[str, Any]:
        package = await self.marketplace.package_for_plugin(plugin_id)
        if not package.get("ok"):
            raise ValueError(str(package.get("error") or "插件市场安装失败"))
        package_plugin_id, package_manifest = self._inspect_zip_manifest(package["payload"])
        market_item = package.get("plugin") if isinstance(package.get("plugin"), dict) else {}
        expected_version = str(market_item.get("version") or "")
        package_version = str(package_manifest.get("version") or "")
        if package_plugin_id != plugin_id:
            raise ValueError("插件包 ID 与商店索引不一致，已拒绝安装")
        if not expected_version or package_version != expected_version:
            raise ValueError("插件包版本与商店索引不一致，已拒绝安装")
        existing_metadata = self._load_marketplace_metadata(plugin_id)
        commit_sha = str(market_item.get("commit_sha") or "")
        if overwrite and commit_sha and existing_metadata.get("commit_sha") == commit_sha:
            return {
                "source": package.get("source", {}),
                "marketplace": market_item,
                "up_to_date": True,
                **self.public_detail(plugin_id),
            }
        detail = await self.install_from_zip(package["payload"], overwrite=overwrite, allow_any_root=True)
        self._save_marketplace_metadata(plugin_id, {
            "repository_url": market_item.get("repository_url", ""),
            "release_tag": market_item.get("release_tag", ""),
            "commit_sha": commit_sha,
            "risk_level": market_item.get("risk_level", ""),
            "update_policy": market_item.get("update_policy", "notify"),
            "approved_permissions": market_item.get("approved_permissions", []),
            "installed_version": package_version,
        })
        return {"source": package.get("source", {}), "marketplace": package.get("plugin", {}), **detail}

    async def update_from_marketplace(self, plugin_id: str) -> dict[str, Any]:
        if plugin_id not in self.plugins:
            raise KeyError(f"插件不存在：{plugin_id}")
        return await self.install_from_marketplace(plugin_id, overwrite=True)

    async def auto_update_safe_plugins(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for plugin_id in list(self.plugins):
            metadata = self._load_marketplace_metadata(plugin_id)
            if metadata.get("update_policy") != "automatic":
                continue
            try:
                detail = await self.install_from_marketplace(plugin_id, overwrite=True)
                results.append({
                    "id": plugin_id,
                    "ok": True,
                    "updated": not bool(detail.get("up_to_date")),
                    "version": detail.get("version", ""),
                })
            except Exception as exc:
                self.logger.warning("声明型插件自动更新失败：%s: %s", plugin_id, exc)
                results.append({"id": plugin_id, "ok": False, "error": str(exc)})
        return results

    def list_mirrors(self) -> dict[str, Any]:
        return {"mirrors": self.mirrors.list()}

    def add_mirror(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.mirrors.add(data)

    def update_mirror(self, mirror_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self.mirrors.update(mirror_id, patch)

    def delete_mirror(self, mirror_id: str) -> dict[str, Any]:
        return self.mirrors.delete(mirror_id)

    async def test_mirror(self, mirror_id: str = "") -> dict[str, Any]:
        return await self.mirrors.test(mirror_id)

    async def uninstall(self, plugin_id: str, *, delete_data: bool = False) -> dict[str, Any]:
        runtime = self._require(plugin_id)
        await self.stop(plugin_id)
        plugin_dir = runtime.directory.resolve()
        self._ensure_inside(self.plugins_dir, plugin_dir)
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        self.contributions.clear_plugin(plugin_id)
        if delete_data:
            data_dir = (self.data_dir / plugin_id).resolve()
            self._ensure_inside(self.data_dir, data_dir)
            if data_dir.exists():
                shutil.rmtree(data_dir)
        self.plugins.pop(plugin_id, None)
        self._api_tokens.pop(plugin_id, None)
        return {"id": plugin_id, "uninstalled": True, "data_deleted": bool(delete_data)}

    async def start_enabled(self) -> None:
        await self.auto_update_safe_plugins()
        for plugin_id, runtime in self.plugins.items():
            if runtime.config.get("enabled") and runtime.status != "failed":
                await self.start(plugin_id)

    async def update_config(self, plugin_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        runtime = self._require(plugin_id)
        properties = runtime.schema.get("properties", {})
        new_config = dict(runtime.config)
        new_secrets = dict(runtime.secrets)
        for key, value in changes.items():
            if key not in properties:
                continue
            if self._sensitive(properties[key]):
                if isinstance(value, dict):
                    # Public plugin details expose secrets as
                    # {"configured": true, "masked": "***xxxx"}.  If the UI
                    # saves an unchanged form, do not persist that mask object
                    # as the real secret.
                    continue
                normalized = self._normalize_value(properties[key], value)
                if normalized:
                    new_secrets[key] = normalized
            else:
                normalized = self._normalize_value(properties[key], value)
                new_config[key] = normalized
        self._validate_required(runtime.schema, new_config, new_secrets)
        runtime.config, runtime.secrets = new_config, new_secrets
        self._save_config(plugin_id, runtime)
        await self.restart(plugin_id)
        return self.public_detail(plugin_id)

    async def start(self, plugin_id: str) -> None:
        runtime = self._require(plugin_id)
        if not runtime.config.get("enabled"):
            runtime.status = "disabled"
            return
        if runtime.process and runtime.process.returncode is None:
            runtime.status = "running"
            return
        generated = False
        for key, field_schema in runtime.schema.get("properties", {}).items():
            if self._sensitive(field_schema) and (field_schema.get("ui") or {}).get("generate") and not runtime.secrets.get(key):
                runtime.secrets[key] = secrets.token_urlsafe(24)
                generated = True
        if generated:
            self._save_config(plugin_id, runtime)
        if not self._has_entrypoint(runtime.manifest):
            self._register_contributions(plugin_id, runtime)
            runtime.status, runtime.error = "active", ""
            return
        runtime.status, runtime.error = "starting", ""
        env = self._build_process_env(plugin_id, runtime)
        command = runtime.manifest.get("entrypoint")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            runtime.status, runtime.error = "failed", "entrypoint 必须是非空字符串数组"
            return
        expanded = self._expand_entrypoint(plugin_id, runtime, command)
        executable = sys.executable if expanded[0] == "{python}" else expanded[0]
        args = expanded[1:]
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        uses_rpc = self._plugin_type(runtime.manifest) in _RPC_PLUGIN_TYPES
        if uses_rpc:
            kwargs.update({
                "stdin": asyncio.subprocess.PIPE,
                "stdout": asyncio.subprocess.PIPE,
                "limit": MAX_RPC_MESSAGE_BYTES,
            })
        try:
            runtime.process = await asyncio.create_subprocess_exec(executable, *args, cwd=str(runtime.directory.parent.parent), env=env, **kwargs)
            if uses_rpc:
                runtime.rpc_client = JsonRpcStdioClient(runtime.process)
                initialized = await runtime.rpc_client.request(
                    "initialize",
                    {
                        "protocol_version": PLUGIN_PROTOCOL_VERSION,
                        "plugin_id": plugin_id,
                        "plugin_type": self._plugin_type(runtime.manifest),
                    },
                    timeout=5,
                )
                runtime.tools = self._validate_tool_descriptors(initialized)
            runtime.status = "running"
            self.logger.info("插件 %s 已启动，PID=%s", plugin_id, runtime.process.pid)
            runtime.monitor_task = asyncio.create_task(self._monitor_process(plugin_id, runtime.process))
        except Exception as exc:
            process = runtime.process
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            runtime.process = None
            runtime.rpc_client = None
            runtime.tools = []
            runtime.status, runtime.error = "failed", str(exc)
            self.logger.exception("插件 %s 启动失败", plugin_id)

    def _build_process_env(self, plugin_id: str, runtime: PluginRuntime) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_PARENT_ENV}
        permissions = set(self._plugin_permissions(runtime))
        if "diceframe.http" in permissions:
            api_base = self.base_env.get("TRPG_API_BASE")
            if api_base:
                env["TRPG_API_BASE"] = api_base
            env["TRPG_BOT_TOKEN"] = self._plugin_api_token(plugin_id)
        plugin_data_dir = (self.data_dir / plugin_id / "runtime").resolve()
        self._ensure_inside(self.data_dir, plugin_data_dir)
        plugin_data_dir.mkdir(parents=True, exist_ok=True)
        env.update({
            "DICEFRAME_PLUGIN_ID": plugin_id,
            "DICEFRAME_PLUGIN_DIR": str(runtime.directory.resolve()),
            "DICEFRAME_PLUGIN_DATA_DIR": str(plugin_data_dir),
            "TRPG_PARENT_PID": str(os.getpid()),
            "DICEFRAME_PLUGIN_PROTOCOL": str(PLUGIN_PROTOCOL_VERSION),
        })
        if self._plugin_type(runtime.manifest) in _RPC_PLUGIN_TYPES:
            env["PYTHONPATH"] = str(runtime.directory.parent.parent.resolve())
        for key, field_schema in runtime.schema.get("properties", {}).items():
            env_name = str((field_schema.get("ui") or {}).get("env") or "")
            if not env_name:
                continue
            value = runtime.secrets.get(key, "") if self._sensitive(field_schema) else runtime.config.get(key, field_schema.get("default"))
            env[env_name] = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value).lower() if isinstance(value, bool) else str(value or "")
        return env

    def authenticate_api_token(self, token: str) -> dict[str, Any] | None:
        candidate = str(token or "").strip()
        if not candidate:
            return None
        for plugin_id, expected in self._api_tokens.items():
            if hmac.compare_digest(candidate, expected):
                runtime = self.plugins.get(plugin_id)
                if runtime and "diceframe.http" in self._plugin_permissions(runtime):
                    return {"plugin_id": plugin_id, "permissions": self._plugin_permissions(runtime)}
        return None

    async def stop(self, plugin_id: str) -> None:
        runtime = self._require(plugin_id)
        monitor = runtime.monitor_task
        runtime.monitor_task = None
        if monitor and not monitor.done():
            monitor.cancel()
        process = runtime.process
        if process and process.returncode is None:
            runtime.status = "stopping"
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        runtime.process = None
        runtime.rpc_client = None
        runtime.tools = []
        runtime.status = self._status_for_enabled(runtime)
        if runtime.status != "active":
            self.contributions.clear_plugin(plugin_id)

    async def _fail_rpc_runtime(self, runtime: PluginRuntime, error: str) -> None:
        monitor = runtime.monitor_task
        runtime.monitor_task = None
        if monitor and not monitor.done():
            monitor.cancel()
        process = runtime.process
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        runtime.process = None
        runtime.rpc_client = None
        runtime.tools = []
        runtime.status = "failed"
        runtime.error = error

    async def restart(self, plugin_id: str) -> None:
        await self.stop(plugin_id)
        await self.start(plugin_id)

    async def cleanup(self) -> None:
        for plugin_id in list(self.plugins):
            await self.stop(plugin_id)

    async def rescan(self) -> list[dict[str, Any]]:
        await self.cleanup()
        discovered = self.discover()
        await self.start_enabled()
        return discovered

    async def _monitor_process(self, plugin_id: str, process: asyncio.subprocess.Process) -> None:
        try:
            code = await process.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("插件 %s 进程监控失败", plugin_id)
            return
        runtime = self.plugins.get(plugin_id)
        if not runtime or runtime.process is not process:
            return
        if runtime.status == "stopping" or not runtime.config.get("enabled"):
            return
        runtime.status = "failed"
        runtime.error = f"插件进程已退出，code={code}"
        runtime.process = None
        runtime.rpc_client = None
        runtime.tools = []
        self.logger.warning("插件 %s 意外退出，3 秒后尝试自动重启，code=%s", plugin_id, code)
        await asyncio.sleep(3)
        if self.plugins.get(plugin_id) is runtime and runtime.config.get("enabled") and runtime.status == "failed":
            await self.start(plugin_id)

    def migrate_config(self, plugin_id: str, legacy: dict[str, Any]) -> None:
        runtime = self._require(plugin_id)
        marker = self.data_dir / plugin_id / ".migrated-v1"
        if marker.exists():
            return
        for key, value in legacy.items():
            field_schema = runtime.schema.get("properties", {}).get(key)
            if not field_schema or value in (None, ""):
                continue
            if self._sensitive(field_schema):
                runtime.secrets[key] = str(value)
            else:
                runtime.config[key] = self._normalize_value(field_schema, value)
        self._save_config(plugin_id, runtime)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1\n", encoding="ascii")

    def _load_config(self, plugin_id: str, schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        folder = self.data_dir / plugin_id
        config = {key: field.get("default") for key, field in schema.get("properties", {}).items() if "default" in field and not self._sensitive(field)}
        secrets_data: dict[str, str] = {}
        for filename, target in (("config.json", config), ("secrets.json", secrets_data)):
            path = folder / filename
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    target.update(loaded)
        return config, secrets_data

    def _save_config(self, plugin_id: str, runtime: PluginRuntime) -> None:
        folder = self.data_dir / plugin_id
        folder.mkdir(parents=True, exist_ok=True)
        self._atomic_json(folder / "config.json", runtime.config)
        self._atomic_json(folder / "secrets.json", runtime.secrets)

    def _load_marketplace_metadata(self, plugin_id: str) -> dict[str, Any]:
        path = self.data_dir / plugin_id / "marketplace.json"
        if not path.exists():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def _save_marketplace_metadata(self, plugin_id: str, metadata: dict[str, Any]) -> None:
        folder = self.data_dir / plugin_id
        folder.mkdir(parents=True, exist_ok=True)
        self._atomic_json(folder / "marketplace.json", metadata)

    def _plugin_api_token(self, plugin_id: str) -> str:
        if plugin_id in self._api_tokens:
            return self._api_tokens[plugin_id]
        path = self.data_dir / plugin_id / "auth.json"
        token = ""
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                token = str(loaded.get("api_token") or "").strip()
        if not token:
            token = secrets.token_urlsafe(32)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_json(path, {"api_token": token})
        self._api_tokens[plugin_id] = token
        return token

    def _load_runtime(self, plugin_dir: Path, *, require_directory_match: bool = True) -> tuple[str, PluginRuntime]:
        plugin_dir = plugin_dir.resolve()
        manifest_path = plugin_dir / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plugin_id = str(manifest.get("id") or "")
        if not _ID_RE.fullmatch(plugin_id):
            raise ValueError("插件 ID 非法")
        if require_directory_match and plugin_dir.name != plugin_id:
            raise ValueError("插件 ID 与目录名不一致")
        if int(manifest.get("schema_version", 0)) != 1:
            raise ValueError("不支持的 manifest schema_version")
        plugin_type = self._plugin_type(manifest)
        if plugin_type not in _PLUGIN_TYPES:
            raise ValueError(f"不支持的 plugin_type：{plugin_type}")
        schema_path = (plugin_dir / str(manifest.get("config_schema") or "config.schema.json")).resolve()
        self._ensure_inside(plugin_dir, schema_path)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self._validate_schema(schema)
        self._validate_manifest_permissions(manifest)
        self._validate_runtime_permissions(manifest, schema)
        self._validate_entrypoint(manifest, plugin_type)
        validate_contributes(manifest, plugin_dir)
        return plugin_id, PluginRuntime(manifest, schema, plugin_dir)

    def _expand_entrypoint(self, plugin_id: str, runtime: PluginRuntime, command: list[str]) -> list[str]:
        data_dir = (self.data_dir / plugin_id / "runtime").resolve()
        replacements = {
            "{plugin_dir}": str(runtime.directory.resolve()),
            "{data_dir}": str(data_dir),
        }
        expanded: list[str] = []
        for item in command:
            value = item
            for marker, replacement in replacements.items():
                value = value.replace(marker, replacement)
            expanded.append(value)
        return expanded

    @staticmethod
    def _validate_tool_descriptors(initialized: Any) -> list[dict[str, Any]]:
        if not isinstance(initialized, dict) or int(initialized.get("protocol_version") or 0) != PLUGIN_PROTOCOL_VERSION:
            raise PluginProtocolError("工具插件协议版本不匹配")
        raw_tools = initialized.get("tools")
        if not isinstance(raw_tools, list) or not raw_tools:
            raise PluginProtocolError("工具插件必须注册至少一个工具")
        if len(raw_tools) > 64:
            raise PluginProtocolError("单个插件最多注册 64 个工具")
        tools: list[dict[str, Any]] = []
        names: set[str] = set()
        for raw in raw_tools:
            if not isinstance(raw, dict):
                raise PluginProtocolError("工具描述必须是对象")
            name = str(raw.get("name") or "").strip()
            if not _TOOL_NAME_RE.fullmatch(name):
                raise PluginProtocolError(f"工具名称非法：{name}")
            if name in names:
                raise PluginProtocolError(f"工具名称重复：{name}")
            names.add(name)
            title = str(raw.get("title") or name).strip()[:120]
            description = str(raw.get("description") or "").strip()[:1000]
            input_schema = raw.get("input_schema")
            if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
                raise PluginProtocolError(f"工具 {name} 的 input_schema 必须是 object")
            try:
                json.dumps(input_schema, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise PluginProtocolError(f"工具 {name} 的 input_schema 不是有效 JSON") from exc
            tools.append({
                "name": name,
                "title": title,
                "description": description,
                "input_schema": input_schema,
            })
        return tools

    def _inspect_zip_manifest(self, payload: bytes) -> tuple[str, dict[str, Any]]:
        if not payload:
            raise ValueError("插件包为空")
        if len(payload) > MAX_PLUGIN_PACKAGE_BYTES:
            raise ValueError("插件包不能超过 20 MB")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="plugin-inspect-", dir=str(self.data_dir)) as temp_name:
            temp_dir = Path(temp_name)
            self._extract_zip(payload, temp_dir)
            source_dir = self._find_install_root(temp_dir)
            plugin_id, runtime = self._load_runtime(source_dir, require_directory_match=False)
            return plugin_id, dict(runtime.manifest)

    @staticmethod
    def _extract_zip(payload: bytes, target_dir: Path) -> None:
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise ValueError("插件包不是有效 zip 文件") from exc
        with archive:
            items = archive.infolist()
            if len(items) > MAX_PLUGIN_ARCHIVE_FILES:
                raise ValueError(f"插件包文件数量不能超过 {MAX_PLUGIN_ARCHIVE_FILES}")
            total_unpacked = sum(info.file_size for info in items if not info.is_dir())
            if total_unpacked > MAX_PLUGIN_UNPACKED_BYTES:
                raise ValueError("插件包解压后不能超过 100 MB")
            seen_paths: set[str] = set()
            for info in items:
                name = info.filename.replace("\\", "/")
                parts = Path(name).parts
                if not name or Path(name).is_absolute() or any(part == ".." for part in parts):
                    raise ValueError("插件包包含非法路径")
                if len(name) > MAX_PLUGIN_PATH_CHARS:
                    raise ValueError("插件包包含过长路径")
                normalized = "/".join(parts).casefold()
                if normalized in seen_paths:
                    raise ValueError("插件包包含重复路径")
                seen_paths.add(normalized)
                if info.flag_bits & 0x1:
                    raise ValueError("插件包不能包含加密文件")
                if info.file_size > MAX_PLUGIN_FILE_BYTES:
                    raise ValueError("插件包单个文件不能超过 25 MB")
                file_type = (info.external_attr >> 16) & 0o170000
                if file_type == 0o120000:
                    raise ValueError("插件包不能包含符号链接")
                resolved = (target_dir / name).resolve()
                PluginHost._ensure_inside(target_dir, resolved)
            archive.extractall(target_dir)

    @staticmethod
    def _find_install_root(temp_dir: Path) -> Path:
        if (temp_dir / "plugin.json").exists():
            return temp_dir
        candidates = [path.parent for path in temp_dir.glob("**/plugin.json")]
        if not candidates:
            raise ValueError("插件包缺少 plugin.json")
        if len(candidates) > 1:
            raise ValueError("插件包包含多个 plugin.json，请只打包一个插件")
        return candidates[0]

    @staticmethod
    def _ensure_inside(root: Path, target: Path) -> None:
        root = root.resolve()
        target = target.resolve()
        if target != root and root not in target.parents:
            raise ValueError("路径越界")

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    @staticmethod
    def _validate_schema(schema: dict[str, Any]) -> None:
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise ValueError("配置 Schema 必须是 object")
        for key, field_schema in schema["properties"].items():
            control = (field_schema.get("ui") or {}).get("control")
            if control and control not in _ALLOWED_CONTROLS:
                raise ValueError(f"字段 {key} 使用不支持的控件 {control}")

    def _status_for_enabled(self, runtime: PluginRuntime) -> str:
        if not runtime.config.get("enabled"):
            return "disabled"
        return "stopped" if self._has_entrypoint(runtime.manifest) else "active"

    @staticmethod
    def _plugin_type(manifest: dict[str, Any]) -> str:
        return str(manifest.get("plugin_type") or "").strip()

    @staticmethod
    def _has_entrypoint(manifest: dict[str, Any]) -> bool:
        command = manifest.get("entrypoint")
        return isinstance(command, list) and bool(command)

    @staticmethod
    def _validate_entrypoint(manifest: dict[str, Any], plugin_type: str) -> None:
        command = manifest.get("entrypoint")
        if command is None and plugin_type in _STATIC_PLUGIN_TYPES:
            return
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise ValueError(f"{plugin_type} 插件必须提供非空字符串数组 entrypoint")

    @staticmethod
    def _validate_manifest_permissions(manifest: dict[str, Any]) -> None:
        permissions = manifest.get("permissions", [])
        if permissions is None:
            permissions = []
        if not isinstance(permissions, list) or not all(isinstance(item, str) and item.strip() for item in permissions):
            raise ValueError("permissions 必须是字符串数组")
        unknown = sorted({item.strip() for item in permissions} - set(_ALLOWED_PERMISSIONS))
        if unknown:
            raise ValueError(f"未知插件权限：{', '.join(unknown)}")

    @staticmethod
    def _validate_runtime_permissions(manifest: dict[str, Any], schema: dict[str, Any]) -> None:
        plugin_type = str(manifest.get("plugin_type") or "").strip()
        permissions = set(effective_plugin_permissions(manifest, schema))
        if plugin_type == "tool" and "tool.execute" not in permissions:
            raise ValueError("tool 插件必须声明 tool.execute 权限")

    def _plugin_permissions(self, runtime: PluginRuntime) -> list[str]:
        return effective_plugin_permissions(runtime.manifest, runtime.schema)

    def _plugin_permission_details(self, runtime: PluginRuntime) -> list[dict[str, str]]:
        return [
            {"id": permission, "description": _ALLOWED_PERMISSIONS.get(permission, permission)}
            for permission in self._plugin_permissions(runtime)
        ]

    @staticmethod
    def _sensitive(field_schema: dict[str, Any]) -> bool:
        ui = field_schema.get("ui") or {}
        return bool(ui.get("sensitive") or ui.get("control") == "secret")

    @staticmethod
    def _normalize_value(field_schema: dict[str, Any], value: Any) -> Any:
        field_type = field_schema.get("type")
        if field_type == "boolean": return bool(value)
        if field_type == "number":
            number = float(value)
            if "exclusiveMinimum" in field_schema and number <= float(field_schema["exclusiveMinimum"]): raise ValueError("数值必须大于最小值")
            return number
        if field_type == "integer": return int(value)
        if field_type == "array": return list(dict.fromkeys(str(item).strip() for item in (value if isinstance(value, list) else []) if str(item).strip()))
        text = str(value or "").strip()
        if field_schema.get("enum") and text not in field_schema["enum"]: raise ValueError("选项无效")
        return text

    @staticmethod
    def _validate_required(schema: dict[str, Any], config: dict[str, Any], secrets_data: dict[str, str]) -> None:
        for key in schema.get("required", []):
            if not config.get(key) and not secrets_data.get(key):
                raise ValueError(f"缺少必填配置：{key}")

    def _require(self, plugin_id: str) -> PluginRuntime:
        if plugin_id not in self.plugins:
            raise KeyError(f"插件不存在：{plugin_id}")
        return self.plugins[plugin_id]

    def _register_contributions(self, plugin_id: str, runtime: PluginRuntime) -> None:
        self.contributions.clear_plugin(plugin_id)
        self.contributions.register_static_plugin(runtime.manifest, runtime.directory)

    @staticmethod
    def _sanitize_theme_tokens(data: dict[str, Any]) -> dict[str, dict[str, str]]:
        raw = data.get("tokens") if isinstance(data.get("tokens"), dict) else data.get("variables")
        if not isinstance(raw, dict):
            raw = {}
        if any(key.startswith("--") for key in raw):
            raw = {"base": raw}
        result = {"base": {}, "dark": {}, "light": {}}
        for mode in result:
            values = raw.get(mode)
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                name = str(key).strip()
                text = str(value).strip()
                lowered = text.lower()
                if not name.startswith("--"):
                    continue
                if len(text) > 160 or any(ch in text for ch in "{};") or "url(" in lowered or "expression(" in lowered:
                    continue
                result[mode][name] = text
        return result

    def _map_json_items(self, kind: str, world_id: str) -> list[dict[str, Any]]:
        result = []
        for item in self.contributions.list(kind):
            try:
                data = json.loads(item.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not self._matches_world(data, world_id):
                    continue
                data = dict(data)
                data.setdefault("id", item.key)
                data.setdefault("name", item.title or item.key)
                data["plugin_id"] = item.plugin_id
                data["plugin_name"] = item.plugin_name
                data["source"] = "plugin"
                result.append(data)
            except Exception:
                self.logger.warning("插件地图资源读取失败: %s", item.path, exc_info=True)
        return result

    def _content_json_items(self, kind: str, *, world_id: str = "", rule_id: str = "") -> list[dict[str, Any]]:
        result = []
        for item in self.contributions.list(kind):
            try:
                data = json.loads(item.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                if not self._matches_world(data, world_id) or not self._matches_rule(data, rule_id):
                    continue
                data = dict(data)
                data.setdefault("id", item.key)
                if kind == "character_template":
                    data.setdefault("character_name", item.title or item.key)
                else:
                    data.setdefault("name", item.title or item.key)
                data["plugin_id"] = item.plugin_id
                data["plugin_name"] = item.plugin_name
                data["source"] = "plugin"
                data["readonly"] = True
                result.append(data)
            except Exception:
                self.logger.warning("插件内容资源读取失败: %s", item.path, exc_info=True)
        return result

    @staticmethod
    def _matches_world(data: dict[str, Any], world_id: str) -> bool:
        target = str(world_id or "")
        if not target:
            return True
        declared = data.get("world_id")
        worlds = data.get("worlds")
        if declared:
            return str(declared) == target
        if isinstance(worlds, list) and worlds:
            return target in {str(item) for item in worlds}
        return True

    @staticmethod
    def _matches_rule(data: dict[str, Any], rule_id: str) -> bool:
        target = str(rule_id or "")
        if not target:
            return True
        declared = data.get("rule_id")
        rules = data.get("rules")
        if declared:
            return str(declared) == target
        if isinstance(rules, list) and rules:
            return target in {str(item) for item in rules}
        return True

    def _asset_item(self, item) -> dict[str, Any]:
        rel = item.relative_path
        return {
            "id": item.key,
            "name": item.title or item.key,
            "description": item.description,
            "plugin_id": item.plugin_id,
            "plugin_name": item.plugin_name,
            "path": rel,
            "url": f"/api/plugins/assets/{quote(item.plugin_id)}/{quote(rel, safe='/')}",
        }
