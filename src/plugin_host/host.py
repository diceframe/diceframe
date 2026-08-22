"""Manifest-driven child-process plugin host."""

from __future__ import annotations

import asyncio
import contextlib
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
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .content import PluginContentCatalog, safe_id_part
from .descriptors import (
    BRIDGE_EXTENSION_STAGES,
    normalize_bridge_outputs,
    validate_bridge_extension_descriptors,
    validate_provider_capabilities,
    validate_tool_descriptors,
)
from .marketplace import PluginMarketplace
from .mirrors import MirrorManager
from src.version import needs_core_update
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
    PluginInvocationError,
    PluginProtocolError,
    validate_tool_arguments,
)
from .support import (
    PLUGIN_TYPE_SUPPORT,
    plugin_type_support,
    plugin_type_descriptor,
    STATIC_PLUGIN_TYPES as _STATIC_PLUGIN_TYPES,
    RPC_PLUGIN_TYPES as _RPC_PLUGIN_TYPES,
)

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BRIDGE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MAX_BRIDGE_IMAGE_BYTES = 10 * 1024 * 1024
_ALLOWED_CONTROLS = {"switch", "text", "secret", "number", "select", "string-list"}
_RESTART_BASE_DELAY = 3.0
_RESTART_MAX_DELAY = 300.0


_RESTART_STABLE_SECONDS = 10.0
# 插件自动更新总开关。默认关闭：商店只提醒有新版，用户手动点更新。
# 保留自动更新的实现骨架，将来要恢复时把此值改为 True 即可。
_PLUGIN_AUTO_UPDATE_ENABLED = False
_PLUGIN_TYPES = set(PLUGIN_TYPE_SUPPORT)
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
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_ARCHITEW6432",
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
    bridge_extensions: list[dict[str, Any]] = field(default_factory=list)
    provider_capabilities: list[dict[str, Any]] = field(default_factory=list)
    status: str = "disabled"
    error: str = ""
    started_at: float = 0.0
    restart_delay_sec: float = 3.0
    source: str = "user"


async def _rename_dir_with_retry(src: Path, dst: Path, *, attempts: int = 3, delay: float = 0.3) -> None:
    """重命名目录；Windows 下杀毒软件实时扫描可能短暂锁定目录，失败时小间隔重试。"""
    for attempt in range(1, attempts + 1):
        try:
            src.rename(dst)
            return
        except OSError:
            if attempt >= attempts:
                raise
            await asyncio.sleep(delay)


class PluginHost:
    def __init__(
        self,
        plugins_dir: Path,
        data_dir: Path,
        *,
        builtin_dir: Path | None = None,
        base_env: dict[str, str] | None = None,
        on_plugin_stopped=None,
        hub_client=None,
        ai_provider_resolver=None,
    ) -> None:
        self.builtin_dir = builtin_dir
        self.plugins_dir = plugins_dir
        self.data_dir = data_dir
        self.base_env = base_env or {}
        # 接线层注入：插件被真正停止/卸载时（keep_enabled=False）回调，用于释放隧道发布。
        self._on_plugin_stopped = on_plugin_stopped
        self.plugins: dict[str, PluginRuntime] = {}
        self.logger = logging.getLogger("trpg.plugins")
        self.mirrors = MirrorManager(self.data_dir / "_marketplace" / "mirrors.json")
        self.hub_client = hub_client
        self._ai_provider_resolver = ai_provider_resolver
        self.marketplace = PluginMarketplace(self.mirrors, hub_client=hub_client)
        self.contributions = ContributionRegistry()
        self.content = PluginContentCatalog(self.contributions, self.logger)
        self._api_tokens: dict[str, str] = {}
        self._install_locks: dict[str, asyncio.Lock] = {}
        self._auto_update_task: asyncio.Task[Any] | None = None
        # 宿主世代标识：每个主进程实例一个随机值。spawn 插件前写入插件 runtime 目录，
        # 插件进程启动时捕获并轮询；主进程被重启（含 os.execv 保 PID 重启）后重新生成，
        # 旧插件进程读到世代变化/缺失即退出，避免孤儿进程残留导致开关状态与真实进程不一致。
        self._host_generation = secrets.token_hex(8)

    def discover(self) -> list[dict[str, Any]]:
        self.plugins.clear()
        # 先内置再用户目录，用户目录同名覆盖内置；runtime.source 记录来源。
        for source, base_dir in (("builtin", self.builtin_dir), ("user", self.plugins_dir)):
            if base_dir is None or not base_dir.exists():
                continue
            for manifest_path in sorted(base_dir.glob("*/plugin.json")):
                try:
                    plugin_id, runtime = self._load_runtime(manifest_path.parent)
                    runtime.config, runtime.secrets = self._load_config(plugin_id, runtime.schema)
                    runtime.source = source
                    runtime.status = self._status_for_enabled(runtime)
                    self.plugins[plugin_id] = runtime
                except Exception as exc:
                    self.logger.exception("插件加载失败: %s", manifest_path)
                    fallback_id = manifest_path.parent.name
                    if fallback_id not in self.plugins:
                        self.plugins[fallback_id] = PluginRuntime(
                            {"id": fallback_id, "name": fallback_id, "version": "?", "description": "插件清单无效"},
                            {"type": "object", "properties": {}}, manifest_path.parent,
                            status="failed", error=str(exc), source=source,
                        )
        # 统一按最终 runtime 状态注册贡献，避免内置/用户同名时贡献不一致。
        self.contributions.clear()
        for plugin_id, runtime in self.plugins.items():
            if runtime.status == "active":
                self._register_contributions(plugin_id, runtime)
        return self.list_public()

    def list_public(self) -> list[dict[str, Any]]:
        return [self.public_detail(plugin_id) for plugin_id in self.plugins]

    def plugin_type_of(self, plugin_id: str) -> str:
        """返回插件类型（卸载清理等按类型 descriptor 派发用）；未知插件返回空串。"""
        runtime = self.plugins.get(plugin_id)
        return self._plugin_type(runtime.manifest) if runtime else ""

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
        min_app_version = str(runtime.manifest.get("min_app_version") or "").strip()
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
            "min_app_version": min_app_version,
            "needs_core_update": needs_core_update(min_app_version),
            "tool_ui": str(runtime.manifest.get("tool_ui") or "").strip(),
            "tools": [dict(tool) for tool in runtime.tools],
            "bridge_extensions": [dict(extension) for extension in runtime.bridge_extensions],
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
                    "tool_ui": str(runtime.manifest.get("tool_ui") or "").strip(),
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

    def find_provider(self, capability: str) -> str | None:
        """返回当前运行中、声明了指定 capability 的 provider 插件 id。"""
        capability = str(capability or "").strip()
        if not capability:
            return None
        for plugin_id, runtime in self.plugins.items():
            if self._plugin_type(runtime.manifest) != "provider" or runtime.status != "running":
                continue
            if any(item.get("kind") == capability for item in runtime.provider_capabilities):
                return plugin_id
        return None

    async def call_provider(
        self,
        plugin_id: str,
        capability: str,
        method_alias: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """按 capability 别名调用 provider 插件；默认超时长于普通 RPC。"""
        runtime = self._require(plugin_id)
        if self._plugin_type(runtime.manifest) != "provider":
            raise ValueError("该插件不是 provider 类型")
        if runtime.status != "running" or not runtime.rpc_client:
            raise ValueError("Provider 插件尚未运行或初始化失败")
        descriptor = next(
            (item for item in runtime.provider_capabilities if item.get("kind") == capability),
            None,
        )
        if not descriptor:
            raise KeyError(f"插件 {plugin_id} 未声明 capability：{capability}")
        method_name = str(descriptor.get("methods", {}).get(method_alias) or "")
        if not method_name:
            raise KeyError(f"capability {capability} 不支持方法别名：{method_alias}")
        if not isinstance(arguments, dict):
            raise ValueError("Provider 调用 arguments 必须是对象")
        try:
            result = await runtime.rpc_client.request(
                "provider.request",
                {"capability": capability, "method": method_alias, "arguments": arguments},
                timeout=max(float(timeout), DEFAULT_RPC_TIMEOUT),
            )
            if not isinstance(result, dict):
                raise PluginProtocolError("Provider 必须返回 JSON 对象")
        except PluginProtocolError as exc:
            await self._fail_rpc_runtime(runtime, str(exc))
            raise
        return result

    def list_bridge_extensions(self) -> list[dict[str, Any]]:
        extensions: list[dict[str, Any]] = []
        for plugin_id, runtime in self.plugins.items():
            if self._plugin_type(runtime.manifest) != "bot-extension" or runtime.status != "running":
                continue
            for descriptor in runtime.bridge_extensions:
                extensions.append({
                    **descriptor,
                    "plugin_id": plugin_id,
                    "plugin_name": str(runtime.manifest.get("name") or plugin_id),
                })
        return sorted(
            extensions,
            key=lambda item: (-int(item.get("priority", 0)), str(item.get("plugin_id")), str(item.get("name"))),
        )

    async def apply_bridge_extensions(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        stage = str(stage or "").strip()
        if stage not in BRIDGE_EXTENSION_STAGES:
            raise ValueError(f"不支持的 Bot Bridge 扩展阶段：{stage}")
        if not isinstance(payload, dict):
            raise ValueError("Bot Bridge 扩展 payload 必须是对象")
        current = dict(payload)
        outputs: list[dict[str, Any]] = []
        applied: list[dict[str, str]] = []
        handled = False
        platform = str(current.get("platform") or "").strip().lower()
        kind = str(current.get("kind") or "").strip().lower()

        for descriptor in self.list_bridge_extensions():
            if stage not in descriptor.get("stages", []):
                continue
            platforms = descriptor.get("platforms", [])
            kinds = descriptor.get("kinds", [])
            if platforms and platform not in platforms:
                continue
            if kinds and kind not in kinds:
                continue
            plugin_id = str(descriptor["plugin_id"])
            runtime = self._require(plugin_id)
            if not runtime.rpc_client:
                continue
            try:
                result = await runtime.rpc_client.request(
                    "bridge.apply",
                    {
                        "name": descriptor["name"],
                        "stage": stage,
                        "payload": current,
                    },
                    timeout=min(30.0, max(1.0, float(descriptor.get("timeout_sec", 5)))),
                )
                if not isinstance(result, dict):
                    raise PluginProtocolError("Bot Bridge 扩展必须返回 JSON 对象")
                next_payload = result.get("payload")
                if next_payload is not None:
                    if not isinstance(next_payload, dict):
                        raise PluginProtocolError("Bot Bridge 扩展返回的 payload 必须是对象")
                    current = next_payload
                normalized_outputs = normalize_bridge_outputs(
                    runtime,
                    result.get("outputs"),
                    self.bridge_asset_path,
                )
                applied.append({"plugin_id": plugin_id, "name": str(descriptor["name"])})
                if bool(result.get("handled")):
                    handled = True
                    outputs = normalized_outputs
                    break
                if stage != "render" and normalized_outputs:
                    outputs.extend(normalized_outputs)
            except PluginInvocationError as exc:
                self.logger.warning(
                    "Bot Bridge 扩展调用失败，已跳过: plugin=%s extension=%s stage=%s error=%s",
                    plugin_id,
                    descriptor["name"],
                    stage,
                    exc,
                )
            except PluginProtocolError as exc:
                self.logger.error(
                    "Bot Bridge 扩展协议错误，已停止插件: plugin=%s extension=%s error=%s",
                    plugin_id,
                    descriptor["name"],
                    exc,
                )
                await self._fail_rpc_runtime(runtime, str(exc))

        return {
            "handled": handled,
            "payload": current,
            "outputs": outputs[:16],
            "applied": applied,
        }

    def bridge_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        runtime = self._require(plugin_id)
        if self._plugin_type(runtime.manifest) != "bot-extension" or runtime.status != "running":
            raise KeyError("Bot Bridge 扩展未运行")
        root = (self.data_dir / plugin_id / "runtime").resolve()
        target = (root / str(relative_path or "")).resolve()
        self._ensure_inside(root, target)
        if not target.is_file() or target.suffix.lower() not in _BRIDGE_IMAGE_SUFFIXES:
            raise KeyError("Bot Bridge 图片不存在或格式不受支持")
        if target.stat().st_size > _MAX_BRIDGE_IMAGE_BYTES:
            raise ValueError("Bot Bridge 图片不能超过 10 MB")
        return target

    def contribution_path(self, kind: str, key: str) -> Path | None:
        return self.content.contribution_path(kind, key)

    def load_world_template(self, world_id: str) -> dict[str, Any] | None:
        return self.content.load_world_template(world_id)

    def expose_scene_image(self, data: dict[str, Any], plugin_id: str) -> dict[str, Any]:
        return self.content.expose_scene_image(data, plugin_id)

    def list_themes(self) -> list[dict[str, Any]]:
        return self.content.list_themes()

    def list_map_assets(self, world_id: str = "") -> dict[str, list[dict[str, Any]]]:
        return self.content.list_map_assets(world_id)

    def list_voice_profiles(self) -> list[dict[str, Any]]:
        return self.content.list_voice_profiles()

    def list_content_resources(
        self,
        kind: str = "",
        *,
        world_id: str = "",
        rule_id: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        return self.content.list_content_resources(kind, world_id=world_id, rule_id=rule_id)

    def get_content_resource(self, kind: str, key: str, *, plugin_id: str = "") -> dict[str, Any] | None:
        return self.content.get_content_resource(kind, key, plugin_id=plugin_id)

    def public_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        runtime = self._require(plugin_id)
        return self.content.public_asset_path(plugin_id, relative_path, runtime.directory)

    def read_docs(self, plugin_id: str) -> dict[str, Any]:
        """读取插件 README/说明文档内容（manifest docs 指向的 .md 文件）。

        只允许读取插件目录内的文件，拒绝路径越界。
        """
        runtime = self._require(plugin_id)
        docs_rel = str(runtime.manifest.get("docs") or "").strip()
        if not docs_rel:
            return {"ok": False, "error": "该插件未提供说明文档", "found": False}
        plugin_dir = runtime.directory.resolve()
        try:
            docs_path = (plugin_dir / docs_rel).resolve()
            self._ensure_inside(plugin_dir, docs_path)
        except ValueError:
            return {"ok": False, "error": "说明文档路径越界", "found": False}
        if not docs_path.is_file() or docs_path.is_symlink():
            return {"ok": False, "error": "插件说明文档不存在", "found": False}
        try:
            content = docs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return {"ok": False, "error": f"读取说明文档失败：{exc}", "found": False}
        return {"ok": True, "found": True, "name": docs_rel, "content": content}

    def sync_lorebooks(self, lorebook_store: Any) -> int:
        """把已启用插件的世界模板 starter_lorebook 同步到世界书库（幂等）。

        插件启用后或建插件世界游戏前调用，使世界书无需先开一把游戏即可出现。
        条目 id 用 ``{world}_plugin_{plugin_id}_{entry_id}`` 标记，便于卸载时
        精确清理；已存在的条目跳过，可安全反复调用。
        """
        if not lorebook_store:
            return 0
        total = 0
        for item in self.contributions.list("world_template"):
            plugin_id = str(item.plugin_id or "")
            runtime = self.plugins.get(plugin_id)
            if not runtime or not runtime.config.get("enabled"):
                continue
            try:
                data = json.loads(item.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(data, dict) or not data.get("world_id"):
                continue
            world_id = str(data["world_id"])
            if not lorebook_store.get_world(world_id):
                lorebook_store.create_world(
                    world_id,
                    data.get("world_name", world_id),
                    description=data.get("description", ""),
                    language=data.get("language", "zh-CN"),
                )
            for raw in data.get("starter_lorebook", []):
                if not isinstance(raw, dict) or not raw.get("id"):
                    continue
                entry_id = f"{safe_id_part(world_id)}_plugin_{safe_id_part(plugin_id)}_{safe_id_part(str(raw['id']))}"
                if lorebook_store.get_entry(entry_id):
                    continue
                entry = dict(raw)
                self.content._expose_packaged_portrait(entry, plugin_id)
                entry["id"] = entry_id
                entry["world_id"] = world_id
                entry["source_plugin"] = plugin_id
                lorebook_store.add_entry(entry)
                total += 1
        return total

    async def install_from_zip(self, payload: bytes, *, overwrite: bool = False, allow_any_root: bool = False) -> dict[str, Any]:
        if not payload:
            raise ValueError("插件包为空")
        if len(payload) > MAX_PLUGIN_PACKAGE_BYTES:
            limit_mb = (MAX_PLUGIN_PACKAGE_BYTES + 1024 * 1024 - 1) // (1024 * 1024)
            raise ValueError(f"插件包不能超过 {limit_mb} MB")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        was_running = False
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
                        current = self.plugins[plugin_id]
                        was_running = bool(current.process and current.process.returncode is None)
                        await self.stop(plugin_id, keep_enabled=True)
                    await _rename_dir_with_retry(target_dir, backup_dir)
                await _rename_dir_with_retry(staging_dir, target_dir)
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
            except Exception:
                if target_dir.exists() and not (target_dir / "plugin.json").exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                if backup_dir.exists() and not target_dir.exists():
                    await _rename_dir_with_retry(backup_dir, target_dir)
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)
                self.discover()
                if was_running and plugin_id in self.plugins:
                    await self.start(plugin_id)
                raise

        self.discover()
        if was_running:
            await self.start(plugin_id)
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
        if _PLUGIN_AUTO_UPDATE_ENABLED and (self._auto_update_task is None or self._auto_update_task.done()):
            self._auto_update_task = asyncio.create_task(self._auto_update_in_background())
        return listing

    def _install_lock(self, plugin_id: str) -> asyncio.Lock:
        lock = self._install_locks.get(plugin_id)
        if lock is None:
            lock = self._install_locks[plugin_id] = asyncio.Lock()
        return lock

    async def install_from_marketplace(self, plugin_id: str, *, overwrite: bool = False) -> dict[str, Any]:
        async with self._install_lock(plugin_id):
            return await self._install_from_marketplace_unlocked(plugin_id, overwrite=overwrite)

    async def _install_from_marketplace_unlocked(self, plugin_id: str, *, overwrite: bool = False) -> dict[str, Any]:
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
        if self.hub_client is not None:
            self.hub_client.queue_download_event(
                plugin_id,
                event_id=str(package.get("hub_event_id") or secrets.token_urlsafe(18)),
                kind="install_succeeded",
                plugin_version=package_version,
                artifact_hash=str(package.get("artifact_hash") or ""),
            )
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
        if runtime.source == "builtin":
            raise ValueError("内置插件不可卸载")
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
        for plugin_id, runtime in self.plugins.items():
            if runtime.config.get("enabled") and runtime.status != "failed":
                await self.start(plugin_id)

    async def _auto_update_in_background(self) -> None:
        try:
            results = await self.auto_update_safe_plugins()
            updated = [item for item in results if item.get("updated")]
            self.logger.info(
                "插件商店自动更新完成：%d 个已更新，%d 个已最新",
                len(updated),
                len(results) - len(updated),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("插件商店自动更新失败")

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

    async def start(self, plugin_id: str, *, reset_backoff: bool = True, require_enabled: bool = True) -> None:
        runtime = self._require(plugin_id)
        if reset_backoff:
            runtime.restart_delay_sec = _RESTART_BASE_DELAY
        # 启动时只拉起 enabled 的插件；前端开关用 require_enabled=False 强制启动进程。
        if require_enabled and not runtime.config.get("enabled"):
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
        self._write_host_generation(plugin_id)
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
                plugin_type = self._plugin_type(runtime.manifest)
                if plugin_type == "tool":
                    runtime.tools = validate_tool_descriptors(initialized)
                elif plugin_type == "provider":
                    runtime.provider_capabilities = validate_provider_capabilities(initialized)
                else:
                    runtime.bridge_extensions = validate_bridge_extension_descriptors(initialized)
            runtime.started_at = time.monotonic()
            runtime.status = "running"
            self.logger.info("插件 %s 已启动，PID=%s", plugin_id, runtime.process.pid)
            runtime.monitor_task = asyncio.create_task(self._monitor_process(plugin_id, runtime.process))
            # 启动成功 = 用户要开；持久化 enabled，重启后保持开启状态。
            if not runtime.config.get("enabled"):
                runtime.config["enabled"] = True
                self._save_config(plugin_id, runtime)
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
            runtime.bridge_extensions = []
            runtime.provider_capabilities = []
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
            "DICEFRAME_APP_ROOT": str(Path(__file__).resolve().parents[2]),
            "DICEFRAME_PLUGIN_DATA_DIR": str(plugin_data_dir),
            "TRPG_PARENT_PID": str(os.getpid()),
            "DICEFRAME_PLUGIN_PROTOCOL": str(PLUGIN_PROTOCOL_VERSION),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        })
        if self._plugin_type(runtime.manifest) in _RPC_PLUGIN_TYPES:
            host_root = Path(__file__).resolve().parents[2]
            env["PYTHONPATH"] = os.pathsep.join((
                str(host_root),
                str(runtime.directory.parent.parent.resolve()),
            ))
        else:
            # 非 RPC 插件（如 channel-adapter）通过 python -m 运行，cwd 是主程序根，
            # 能 import 主程序的 src/。拆离成独立仓库后，插件代码在 plugins/<id>/src/，
            # 需要把插件目录加进 PYTHONPATH 才能被入口找到；保留主程序根以访问 bridge_core。
            env["PYTHONPATH"] = os.pathsep.join((
                str(Path(__file__).resolve().parents[2]),
                str(runtime.directory.resolve()),
            ))
        for key, field_schema in runtime.schema.get("properties", {}).items():
            env_name = str((field_schema.get("ui") or {}).get("env") or "")
            if not env_name:
                continue
            value = runtime.secrets.get(key, "") if self._sensitive(field_schema) else runtime.config.get(key, field_schema.get("default"))
            env[env_name] = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value).lower() if isinstance(value, bool) else str(value or "")
        self._inject_ai_provider_env(runtime, env, permissions)
        return env

    def _inject_ai_provider_env(
        self,
        runtime: PluginRuntime,
        env: dict[str, str],
        permissions: set[str],
    ) -> None:
        if "ai.providers" not in permissions or self._ai_provider_resolver is None:
            return
        for key, field_schema in runtime.schema.get("properties", {}).items():
            ui = field_schema.get("ui") or {}
            if ui.get("options_source") != "ai_providers":
                continue
            provider_ref = str(runtime.config.get(key) or "").strip()
            if not provider_ref:
                continue
            try:
                provider = self._ai_provider_resolver(provider_ref)
            except Exception:
                self.logger.exception("解析插件 AI 服务商失败: %s", provider_ref)
                continue
            if not isinstance(provider, dict):
                continue
            required_format = str(ui.get("api_format") or "").strip().lower()
            provider_format = str(provider.get("api_format") or "openai").strip().lower()
            if required_format and provider_format != required_format:
                continue
            for ui_key, provider_key in (
                ("provider_base_url_env", "base_url"),
                ("provider_api_key_env", "api_key"),
                ("provider_api_format_env", "api_format"),
            ):
                env_name = str(ui.get(ui_key) or "").strip()
                if env_name:
                    env[env_name] = str(provider.get(provider_key) or "")

    async def restart_ai_provider_consumers(self) -> None:
        for plugin_id, runtime in self.plugins.items():
            permissions = set(self._plugin_permissions(runtime))
            uses_ai_provider = any(
                (field_schema.get("ui") or {}).get("options_source") == "ai_providers"
                for field_schema in runtime.schema.get("properties", {}).values()
            )
            process_running = bool(runtime.process and runtime.process.returncode is None)
            if "ai.providers" in permissions and uses_ai_provider and process_running:
                await self.restart(plugin_id, require_enabled=False)

    def _host_generation_path(self, plugin_id: str) -> Path:
        return (self.data_dir / plugin_id / "runtime" / ".host-generation").resolve()

    def _write_host_generation(self, plugin_id: str) -> None:
        """把本宿主进程的世代标识原子写入插件 runtime 目录。

        插件进程启动时捕获该值并周期性轮询；值变化或文件缺失 = 宿主已换代
        （主程序被重启，可能是 PID 复用或 os.execv 保 PID 重启），插件应立即
        退出并释放单实例锁，避免旧进程残留导致 UI 开关与真实进程不一致、
        新实例被锁拒绝。
        """
        path = self._host_generation_path(plugin_id)
        self._ensure_inside(self.data_dir, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".host-generation.{os.getpid()}.tmp")
        tmp.write_text(self._host_generation + "\n", encoding="ascii")
        os.replace(tmp, path)

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

    async def stop(self, plugin_id: str, *, keep_enabled: bool = False) -> None:
        runtime = self._require(plugin_id)
        # 用户主动关闭插件时，把 enabled 置 false 并保存，重启后不再自动拉起。
        # restart 用 keep_enabled=True 保留用户"开着"的意图，仅重启进程。
        if not keep_enabled and runtime.config.get("enabled"):
            runtime.config["enabled"] = False
            self._save_config(plugin_id, runtime)
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
        runtime.bridge_extensions = []
        runtime.provider_capabilities = []
        runtime.status = self._status_for_enabled(runtime)
        if runtime.status != "active":
            self.contributions.clear_plugin(plugin_id)
        # 用户主动停止/卸载（keep_enabled=False）时通知接线层；
        # restart/cleanup/更新走 keep_enabled=True 不触发，插件会重新拉起并重新发布。
        if not keep_enabled and self._on_plugin_stopped is not None:
            try:
                await self._on_plugin_stopped(plugin_id)
            except Exception:
                self.logger.exception("插件停止回调失败: %s", plugin_id)

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
        runtime.bridge_extensions = []
        runtime.provider_capabilities = []
        runtime.status = "failed"
        runtime.error = error

    async def restart(self, plugin_id: str, *, require_enabled: bool = True) -> None:
        await self.stop(plugin_id, keep_enabled=True)
        await self.start(plugin_id, require_enabled=require_enabled)

    async def cleanup(self) -> None:
        if self._auto_update_task and not self._auto_update_task.done():
            self._auto_update_task.cancel()
        # 宿主关闭不改变插件 enabled 状态，重启后按用户意图恢复。
        for plugin_id in list(self.plugins):
            await self.stop(plugin_id, keep_enabled=True)
        # 删除世代文件：若某插件 stop 失败残留，也能感知宿主换代立即退出并释放锁。
        for plugin_id in list(self.plugins):
            with contextlib.suppress(OSError):
                self._host_generation_path(plugin_id).unlink()

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
        alive_sec = time.monotonic() - runtime.started_at if runtime.started_at else 0.0
        if runtime.status == "stopping" or not runtime.config.get("enabled"):
            return
        runtime.status = "failed"
        runtime.error = f"插件进程已退出，code={code}"
        runtime.process = None
        runtime.rpc_client = None
        runtime.tools = []
        runtime.bridge_extensions = []
        runtime.provider_capabilities = []
        if alive_sec >= _RESTART_STABLE_SECONDS:
            runtime.restart_delay_sec = _RESTART_BASE_DELAY
        delay = runtime.restart_delay_sec
        if alive_sec < _RESTART_STABLE_SECONDS:
            runtime.restart_delay_sec = min(runtime.restart_delay_sec * 2, _RESTART_MAX_DELAY)
        self.logger.warning("插件 %s 意外退出，%.0f 秒后尝试自动重启，code=%s", plugin_id, delay, code)
        await asyncio.sleep(delay)
        if self.plugins.get(plugin_id) is runtime and runtime.config.get("enabled") and runtime.status == "failed":
            await self.start(plugin_id, reset_backoff=False)

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
    def package_files(plugin_id: str, files: dict[str, str | bytes], *, flat: bool = False) -> bytes:
        """把内存中的插件文件打包成 zip 字节，镜像 _extract_zip 的约束。

        files 的 key 是相对路径。``flat=False``（默认，.dfplugin 用）会自动加
        ``<plugin_id>/`` 前缀；``flat=True``（仓库源码用）不加前缀，plugin.json
        落到根目录，解压即可推到 GitHub。
        """
        if not _ID_RE.fullmatch(plugin_id):
            raise ValueError("插件 ID 非法")
        buffer = io.BytesIO()
        seen: set[str] = set()
        total_unpacked = 0
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for rel, content in files.items():
                name = str(rel or "").replace("\\", "/").strip("/")
                parts = Path(name).parts
                if not name or Path(name).is_absolute() or any(part == ".." for part in parts):
                    raise ValueError(f"导出路径非法：{rel}")
                if not flat and parts[0] != plugin_id:
                    name = f"{plugin_id}/{name}"
                    parts = Path(name).parts
                if len(name) > MAX_PLUGIN_PATH_CHARS:
                    raise ValueError(f"导出路径过长：{rel}")
                normalized = "/".join(parts).casefold()
                if normalized in seen:
                    raise ValueError(f"导出路径重复：{rel}")
                seen.add(normalized)
                data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
                if len(data) > MAX_PLUGIN_FILE_BYTES:
                    raise ValueError(f"导出单文件过大：{rel}")
                total_unpacked += len(data)
                if total_unpacked > MAX_PLUGIN_UNPACKED_BYTES:
                    raise ValueError("导出内容总体积超限")
                if len(seen) > MAX_PLUGIN_ARCHIVE_FILES:
                    raise ValueError("导出文件数量超限")
                archive.writestr(name, data)
        payload = buffer.getvalue()
        if len(payload) > MAX_PLUGIN_PACKAGE_BYTES:
            raise ValueError("导出包体积超限")
        return payload

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
            raise ValueError(
                f"未知插件权限：{', '.join(unknown)}"
                "（该权限可能来自更新版本的 DiceFrame，请升级后重试）"
            )

    @staticmethod
    def _validate_runtime_permissions(manifest: dict[str, Any], schema: dict[str, Any]) -> None:
        plugin_type = str(manifest.get("plugin_type") or "").strip()
        permissions = set(effective_plugin_permissions(manifest, schema))
        required = plugin_type_descriptor(plugin_type).get("required_permission")
        if required and required not in permissions:
            raise ValueError(f"{plugin_type} 插件必须声明 {required} 权限")

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
