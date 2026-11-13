"""Manifest-driven child-process plugin host."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
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
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .capabilities import (
    clear_runtime_capabilities,
    find_provider_plugin,
    initialize_runtime_capabilities,
    list_runtime_bridge_extensions,
    list_runtime_tools,
    provider_capability,
)
from .content import PluginContentCatalog, safe_id_part
from .contracts import (
    AIProviderResolver,
    ListedBridgeExtensionDescriptor,
    ListedToolDescriptor,
    PluginContributionView,
    PluginPublicDetail,
    PluginStoppedCallback,
)
from .descriptors import (
    BRIDGE_EXTENSION_STAGES,
    normalize_bridge_outputs,
)
from .document_reader import DocumentReadError, read_document
from .marketplace import PluginMarketplace
from .mirrors import MirrorManager
from .models import PluginRuntime
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
from .requirements import (
    dependency_rows,
    parse_plugin_requirements,
    parse_ruleset_requirements,
    plugin_version_satisfies,
)
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
    validate_content_module_profile,
    STATIC_PLUGIN_TYPES as _STATIC_PLUGIN_TYPES,
    RPC_PLUGIN_TYPES as _RPC_PLUGIN_TYPES,
)

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RULEBOOK_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BRIDGE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MAX_BRIDGE_IMAGE_BYTES = 10 * 1024 * 1024
_ALLOWED_CONTROLS = {"switch", "text", "secret", "number", "select", "string-list"}
_RESTART_BASE_DELAY = 3.0
_RESTART_MAX_DELAY = 300.0


_RESTART_STABLE_SECONDS = 10.0
# 插件自动更新总开关。默认关闭：商店只提醒有新版，用户手动点更新。
# 保留自动更新的实现骨架，将来要恢复时把此值改为 True 即可。
_PLUGIN_AUTO_UPDATE_ENABLED = False

# FIX-01 §3.3/§3.4：安装事务内的包校验钩子。签名 ``(plugin_dir, manifest) -> None``，
# 有 blocker 时抛 ValueError；宿主在**任何 mutation 之前**调用它（依赖倒置）。
PackageValidator = Callable[[Path, dict[str, Any]], None]
# FIX-01 §3.5：破坏性 package 操作 guard。签名 ``(plugin_id, action) -> None``，
# 被绑定的存档引用时抛 ValueError（实现方决定哪些 plugin_type 受保护）。
ContentModuleGuard = Callable[[str, str], None]
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
        on_plugin_stopped: PluginStoppedCallback | None = None,
        hub_client: Any | None = None,
        ai_provider_resolver: AIProviderResolver | None = None,
        package_validator: PackageValidator | None = None,
        content_module_guard: ContentModuleGuard | None = None,
    ) -> None:
        self.builtin_dir = builtin_dir
        self.plugins_dir = plugins_dir
        self.data_dir = data_dir
        self.base_env = base_env or {}
        # 接线层注入：插件被真正停止/卸载时（keep_enabled=False）回调，用于释放隧道发布。
        self._on_plugin_stopped = on_plugin_stopped
        # 接线层注入（FIX-01 §3.3/§3.5）：安装事务内的包校验与破坏性操作 guard。
        # 宿主只按契约调用，不认识 game/module 语义（依赖倒置，不是第二 authority）。
        self._package_validator = package_validator
        self._content_module_guard = content_module_guard
        self.plugins: dict[str, PluginRuntime] = {}
        self.logger = logging.getLogger("trpg.plugins")
        self.mirrors = MirrorManager(self.data_dir / "_marketplace" / "mirrors.json")
        self.hub_client = hub_client
        self._ai_provider_resolver = ai_provider_resolver
        self.marketplace = PluginMarketplace(
            self.mirrors,
            hub_client=hub_client,
            cache_path=self.data_dir / "_marketplace" / "catalog-cache.json",
        )
        self.contributions = ContributionRegistry()
        self.content = PluginContentCatalog(self.contributions, self.logger)
        self._api_tokens: dict[str, str] = {}
        self._install_locks: dict[str, asyncio.Lock] = {}
        self._auto_update_task: asyncio.Task[Any] | None = None
        # 宿主世代标识：每个主进程实例一个随机值。spawn 插件前写入插件 runtime 目录，
        # 插件进程启动时捕获并轮询；主进程被重启（含 os.execv 保 PID 重启）后重新生成，
        # 旧插件进程读到世代变化/缺失即退出，避免孤儿进程残留导致开关状态与真实进程不一致。
        self._host_generation = secrets.token_hex(8)

    def discover(self) -> list[PluginPublicDetail]:
        self._recover_group_install_journal()
        self._recover_install_journal()
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
                    receipt = self._load_install_receipt(plugin_id)
                    expected_digest = str(receipt.get("manifest_digest") or "").strip().lower()
                    if expected_digest and expected_digest != self._manifest_digest(runtime.manifest):
                        runtime.status = "failed"
                        runtime.error = "installed manifest digest mismatch"
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
        self._validate_discovered_plugin_dependencies()
        # Dependency failures are checked before contributions are exposed.
        self.contributions.clear()
        for plugin_id, runtime in self.plugins.items():
            if runtime.status == "active":
                self._register_contributions(plugin_id, runtime)
        return self.list_public()

    def _validate_discovered_plugin_dependencies(self) -> None:
        """Fail closed on missing, incompatible, or cyclic hard package edges."""

        graph: dict[str, list[str]] = {}
        for plugin_id, runtime in self.plugins.items():
            try:
                requirements = parse_plugin_requirements(runtime.manifest)
            except ValueError as exc:
                runtime.status, runtime.error = "failed", str(exc)
                graph[plugin_id] = []
                continue
            graph[plugin_id] = [item["id"] for item in requirements]

        visiting: set[str] = set()
        visited: set[str] = set()
        cyclic: set[str] = set()

        def walk(plugin_id: str, trail: tuple[str, ...] = ()) -> None:
            if plugin_id in visiting:
                cyclic.update(trail[trail.index(plugin_id):])
                return
            if plugin_id in visited:
                return
            visiting.add(plugin_id)
            for dependency in graph.get(plugin_id, []):
                if dependency in self.plugins:
                    walk(dependency, (*trail, plugin_id))
            visiting.remove(plugin_id)
            visited.add(plugin_id)

        for plugin_id in graph:
            walk(plugin_id)
        for plugin_id in cyclic:
            runtime = self.plugins.get(plugin_id)
            if runtime is not None:
                runtime.status, runtime.error = "failed", "plugin dependency cycle detected"

        for plugin_id, runtime in self.plugins.items():
            if runtime.status == "failed":
                continue
            try:
                requirements = parse_plugin_requirements(runtime.manifest)
            except ValueError as exc:
                runtime.status, runtime.error = "failed", str(exc)
                continue
            for requirement in requirements:
                dependency_id = requirement["id"]
                dependency = self.plugins.get(dependency_id)
                if dependency is None:
                    runtime.status, runtime.error = "failed", f"missing plugin dependency: {dependency_id}"
                    break
                if dependency.status == "failed":
                    runtime.status, runtime.error = "failed", f"plugin dependency unavailable: {dependency_id}"
                    break
                if not plugin_version_satisfies(
                    requirement["minimum_version"], str(dependency.manifest.get("version") or ""),
                ):
                    runtime.status, runtime.error = "failed", (
                        f"plugin dependency version mismatch: {dependency_id}"
                    )
                    break

    def _validate_plugin_requirements_for_manifest(
        self, manifest: Mapping[str, Any],
    ) -> None:
        """Preflight a candidate package before staging or directory mutation."""

        for requirement in parse_plugin_requirements(manifest):
            dependency_id = requirement["id"]
            if dependency_id == str(manifest.get("id") or ""):
                raise ValueError("plugin dependency cycle detected")
            dependency = self.plugins.get(dependency_id)
            if dependency is None or dependency.status == "failed":
                raise ValueError(f"missing plugin dependency: {dependency_id}")
            if not plugin_version_satisfies(
                requirement["minimum_version"], str(dependency.manifest.get("version") or ""),
            ):
                raise ValueError(f"plugin dependency version mismatch: {dependency_id}")

    def _dependents_requiring(self, plugin_id: str) -> list[dict[str, Any]]:
        """Return installed packages that would be broken by removing ``plugin_id``."""

        result: list[dict[str, Any]] = []
        for dependent_id in sorted(self.plugins):
            if dependent_id == plugin_id:
                continue
            runtime = self.plugins[dependent_id]
            try:
                requirements = parse_plugin_requirements(runtime.manifest)
            except ValueError:
                # The dependent is already invalid; it still must not make a
                # destructive operation appear safe.  Keep the reason visible.
                result.append({"id": dependent_id, "minimum_version": "", "invalid": True})
                continue
            for requirement in requirements:
                if requirement["id"] == plugin_id:
                    result.append({
                        "id": dependent_id,
                        "minimum_version": requirement["minimum_version"],
                    })
        return result

    def _validate_plugin_replacement(self, plugin_id: str, manifest: Mapping[str, Any]) -> None:
        """Reject a candidate version that would invalidate installed dependents."""

        candidate_version = str(manifest.get("version") or "")
        for dependent in self._dependents_requiring(plugin_id):
            minimum = str(dependent.get("minimum_version") or "")
            if dependent.get("invalid") or not plugin_version_satisfies(minimum, candidate_version):
                raise ValueError(
                    f"plugin update would break dependency: {dependent['id']} requires "
                    f"{plugin_id}>={minimum or '?'}"
                )

    def dependency_plan(self) -> list[dict[str, Any]]:
        """Expose the installed package DAG and exact unsatisfied blockers."""

        manifests = {
            plugin_id: runtime.manifest
            for plugin_id, runtime in self.plugins.items()
        }
        rows = dependency_rows(manifests)
        reverse: dict[str, list[str]] = {plugin_id: [] for plugin_id in manifests}
        for row in rows:
            for dependency in row["dependencies"]:
                dependency_id = dependency["id"]
                if dependency_id in reverse:
                    reverse[dependency_id].append(row["id"])
        for row in rows:
            row["dependents"] = sorted(reverse.get(row["id"], []))
            row["status"] = self.plugins[row["id"]].status
        return rows

    def plan_install_group(
        self, payloads: list[bytes], *, overwrite: bool = False,
    ) -> dict[str, Any]:
        """Validate a bounded package group without touching live state.

        The planner resolves hard edges against the candidate group first and
        the installed host second.  It returns a topological order and exact
        blockers, but deliberately does not download, start, or publish any
        package; group atomic commit remains an explicit later lifecycle step.
        """

        if not isinstance(payloads, list) or not payloads or len(payloads) > 32:
            raise ValueError("插件组必须是 1 到 32 个安装包")
        candidate: dict[str, tuple[dict[str, Any], Path]] = {}
        temp = tempfile.TemporaryDirectory(
            prefix="plugin-group-plan-",
            dir=str(self.data_dir) if self.data_dir.exists() else None,
        )
        try:
            root = Path(temp.name)
            for index, payload in enumerate(payloads):
                if not isinstance(payload, (bytes, bytearray)) or not payload:
                    raise ValueError(f"插件组第 {index + 1} 个包为空")
                package_dir = root / str(index)
                package_dir.mkdir()
                self._extract_zip(bytes(payload), package_dir)
                source_dir = self._find_install_root(package_dir)
                plugin_id, runtime = self._load_runtime(source_dir, require_directory_match=False)
                if source_dir != package_dir and source_dir.name != plugin_id:
                    raise ValueError("插件包顶层目录名必须与插件 ID 一致")
                if plugin_id in candidate:
                    raise ValueError(f"插件组包含重复 ID：{plugin_id}")
                self._validate_package_before_install(
                    source_dir, dict(runtime.manifest), "",
                )
                candidate[plugin_id] = (dict(runtime.manifest), source_dir)

            manifests: dict[str, Mapping[str, Any]] = {
                plugin_id: runtime.manifest
                for plugin_id, runtime in self.plugins.items()
            }
            if not overwrite:
                duplicates = sorted(plugin_id for plugin_id in candidate if plugin_id in manifests)
                if duplicates:
                    raise ValueError(
                        "插件已存在；如需更新请启用覆盖安装：" + ", ".join(duplicates)
                    )
            manifests.update({plugin_id: manifest for plugin_id, (manifest, _path) in candidate.items()})
            graph: dict[str, list[str]] = {}
            blockers: list[dict[str, str]] = []
            candidate_registry = ContributionRegistry()
            for item in self.contributions.list():
                candidate_registry._add(item)
            for plugin_id, (_manifest, source_dir) in candidate.items():
                candidate_registry.clear_plugin(plugin_id)
                try:
                    candidate_registry.register_static_plugin(_manifest, source_dir)
                except ValueError as exc:
                    blockers.append({"plugin_id": plugin_id, "reason": str(exc)})
            for plugin_id, manifest in manifests.items():
                try:
                    requirements = parse_plugin_requirements(manifest)
                except ValueError as exc:
                    blockers.append({"plugin_id": plugin_id, "reason": str(exc)})
                    graph[plugin_id] = []
                    continue
                graph[plugin_id] = [item["id"] for item in requirements]
                for requirement in requirements:
                    dependency_id = requirement["id"]
                    dependency = manifests.get(dependency_id)
                    if dependency is None:
                        blockers.append({
                            "plugin_id": plugin_id,
                            "reason": f"missing plugin dependency: {dependency_id}",
                        })
                    elif not plugin_version_satisfies(
                        requirement["minimum_version"],
                        str(dependency.get("version") or ""),
                    ):
                        blockers.append({
                            "plugin_id": plugin_id,
                            "reason": f"plugin dependency version mismatch: {dependency_id}",
                        })
            visiting: set[str] = set()
            visited: set[str] = set()
            cycle_nodes: set[str] = set()

            def visit(node: str, trail: tuple[str, ...] = ()) -> None:
                if node in visiting:
                    cycle_nodes.update(trail[trail.index(node):])
                    return
                if node in visited:
                    return
                visiting.add(node)
                for dependency_id in graph.get(node, []):
                    if dependency_id in graph:
                        visit(dependency_id, (*trail, node))
                visiting.remove(node)
                visited.add(node)

            for plugin_id in graph:
                visit(plugin_id)
            for plugin_id in sorted(cycle_nodes):
                blockers.append({"plugin_id": plugin_id, "reason": "plugin dependency cycle detected"})

            for plugin_id, (manifest, _path) in candidate.items():
                if overwrite and plugin_id in self.plugins:
                    try:
                        self._validate_plugin_replacement(plugin_id, manifest)
                    except ValueError as exc:
                        blockers.append({"plugin_id": plugin_id, "reason": str(exc)})

            # Kahn order is useful to a later atomic publisher and remains a
            # read-only review artifact even when blockers exist.
            indegree = {plugin_id: 0 for plugin_id in graph}
            for plugin_id, dependencies in graph.items():
                for dependency_id in dependencies:
                    if dependency_id in indegree:
                        indegree[plugin_id] += 1
            ready = sorted(plugin_id for plugin_id, degree in indegree.items() if degree == 0)
            order: list[str] = []
            while ready:
                current = ready.pop(0)
                order.append(current)
                for dependent, dependencies in graph.items():
                    if current in dependencies:
                        indegree[dependent] -= 1
                        if indegree[dependent] == 0:
                            ready.append(dependent)
                            ready.sort()
            if len(order) != len(graph):
                order = []
            return {
                "ok": not blockers,
                "atomic_commit": False,
                "plugins": [
                    {
                        "id": plugin_id,
                        "version": str(manifest.get("version") or ""),
                        "candidate": plugin_id in candidate,
                    }
                    for plugin_id, manifest in sorted(manifests.items())
                ],
                "order": order,
                "blockers": blockers,
            }
        finally:
            temp.cleanup()

    def list_public(self) -> list[PluginPublicDetail]:
        return [self.public_detail(plugin_id) for plugin_id in self.plugins]

    def plugin_type_of(self, plugin_id: str) -> str:
        """返回插件类型（卸载清理等按类型 descriptor 派发用）；未知插件返回空串。"""
        runtime = self.plugins.get(plugin_id)
        return self._plugin_type(runtime.manifest) if runtime else ""

    def public_detail(self, plugin_id: str) -> PluginPublicDetail:
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
        dependency_projection = next(
            (row for row in self.dependency_plan() if row["id"] == plugin_id),
            {"dependencies": [], "dependents": []},
        )
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
            "dependencies": dependency_projection["dependencies"],
            "dependents": dependency_projection["dependents"],
        }

    def list_contributions(self, kind: str = "") -> list[PluginContributionView]:
        return [item.to_dict() for item in self.contributions.list(kind)]

    def list_tools(self) -> list[ListedToolDescriptor]:
        return list_runtime_tools(self.plugins)

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
        return find_provider_plugin(self.plugins, capability)

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
        descriptor = provider_capability(runtime, capability)
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

    def list_bridge_extensions(self) -> list[ListedBridgeExtensionDescriptor]:
        return list_runtime_bridge_extensions(self.plugins)

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

    def load_world_template(self, world_id: str, language: str = "") -> dict[str, Any] | None:
        return self.content.load_world_template(world_id, language)

    def load_rule_template(self, rule_id: str, language: str = "", *, plugin_id: str = "") -> dict[str, Any] | None:
        return self.content.load_rule_template(rule_id, language, plugin_id=plugin_id)

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
        language: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        return self.content.list_content_resources(kind, world_id=world_id, rule_id=rule_id, language=language)

    def get_content_resource(self, kind: str, key: str, *, plugin_id: str = "", language: str = "") -> dict[str, Any] | None:
        return self.content.get_content_resource(kind, key, plugin_id=plugin_id, language=language)

    def public_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        runtime = self._require(plugin_id)
        return self.content.public_asset_path(plugin_id, relative_path, runtime.directory)

    def prebuilt_ui_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        """Resolve only an active plugin's explicitly declared U02 UI files."""

        runtime = self._require(plugin_id)
        if runtime.status not in {"active", "running"} or not runtime.prebuilt_ui:
            raise KeyError("插件未提供可用的预构建 UI")
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        allowed = {
            runtime.prebuilt_ui["entrypoint"],
            *runtime.prebuilt_ui.get("assets", []),
        }
        if normalized not in allowed:
            raise ValueError("UI 资源未在 prebuilt_ui 中声明")
        target = (runtime.directory / normalized).resolve()
        self._ensure_inside(runtime.directory, target)
        if target.is_symlink() or not target.is_file():
            raise KeyError("UI 资源不存在")
        return target

    def prebuilt_ui_descriptor(self, plugin_id: str) -> dict[str, Any]:
        runtime = self._require(plugin_id)
        if runtime.status not in {"active", "running"} or not runtime.prebuilt_ui:
            raise KeyError("插件未提供可用的预构建 UI")
        return {
            "plugin_id": plugin_id,
            "entrypoint": runtime.prebuilt_ui["entrypoint"],
            "assets": list(runtime.prebuilt_ui.get("assets", [])),
        }

    def read_docs(self, plugin_id: str) -> dict[str, Any]:
        """读取插件 README/说明文档内容（manifest docs 指向的 .md 文件）。

        只允许读取插件目录内的文件，拒绝路径越界。
        """
        runtime = self._require(plugin_id)
        docs_rel = str(runtime.manifest.get("docs") or "").strip()
        if not docs_rel:
            return {"ok": False, "error": "该插件未提供说明文档", "found": False}
        kind = "pdf" if docs_rel.lower().endswith(".pdf") else "markdown"
        try:
            content = read_document(runtime.directory, docs_rel, kind=kind)
        except DocumentReadError as exc:
            return {"ok": False, "error": str(exc), "found": False}
        if isinstance(content, bytes):
            return {
                "ok": True, "found": True, "name": docs_rel,
                "kind": kind, "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
            }
        return {"ok": True, "found": True, "name": docs_rel, "kind": kind, "content": content}

    def list_rulebooks(self, plugin_id: str, *, viewer_is_gm: bool = False) -> dict[str, Any]:
        """Project declared rulebook metadata without exposing GM entries."""

        runtime = self._require(plugin_id)
        books = []
        for entry in runtime.rulebooks:
            if entry["audience"] == "gm" and not viewer_is_gm:
                continue
            books.append({key: value for key, value in entry.items() if key != "path"})
        return {"ok": True, "plugin_id": plugin_id, "rulebooks": books}

    def read_rulebook(
        self, plugin_id: str, book_id: str, *, viewer_is_gm: bool = False,
    ) -> dict[str, Any]:
        """Read one declared Markdown/PDF rulebook through D01 bounds."""

        runtime = self._require(plugin_id)
        entry = next((item for item in runtime.rulebooks if item["id"] == str(book_id or "")), None)
        if entry is None:
            return {"ok": False, "error": "规则书不存在", "found": False}
        if entry["audience"] == "gm" and not viewer_is_gm:
            return {"ok": False, "error": "规则书需要 GM 权限", "found": False}
        if entry.get("kind") == "external":
            return {
                "ok": True,
                "found": True,
                "external": True,
                "plugin_id": plugin_id,
                "book": {key: value for key, value in entry.items() if key != "path"},
                "url": entry["url"],
                "encoding": "external",
                "content": None,
                "offline_hint": "该规则书由外部地址提供，离线时不可读取",
            }
        try:
            content = read_document(runtime.directory, entry["path"], kind=entry["kind"])
        except DocumentReadError as exc:
            return {"ok": False, "error": str(exc), "found": False}
        if isinstance(content, bytes):
            content_value: str = base64.b64encode(content).decode("ascii")
            encoding = "base64"
        else:
            content_value = content
            encoding = "utf-8"
        return {
            "ok": True,
            "found": True,
            "plugin_id": plugin_id,
            "book": {key: value for key, value in entry.items() if key != "path"},
            "encoding": encoding,
            "content": content_value,
        }

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
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError(f"插件世界模板读取失败：{item.path}: {exc}") from exc
            if not isinstance(data, dict) or not data.get("world_id"):
                raise ValueError(f"插件世界模板缺少 world_id：{item.path}")
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

    @staticmethod
    def _validate_ruleset_catalogs(
        manifest: dict[str, Any], plugin_dir: Path,
    ) -> tuple[Path, tuple[str, ...]] | None:
        """Validate manifest ``ruleset_catalogs``（DNDMOD-04，母方案 §7/§107）。

        与 adventure_packages 同规则：相对/不越界/真实目录/共享父目录；目录内
        是 catalog JSON（装载时经 DNDMOD-01 契约校验）。仅 content-pack。
        """

        declared = manifest.get("ruleset_catalogs")
        if declared is None:
            return None
        if str(manifest.get("plugin_type") or "") != "content-pack":
            raise ValueError("ruleset_catalogs 仅支持 content-pack")
        if not isinstance(declared, list) or not declared:
            raise ValueError("ruleset_catalogs 必须是非空字符串数组")
        base = plugin_dir.resolve()
        catalogs: list[Path] = []
        for pattern in declared:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError("ruleset_catalogs 路径必须是非空字符串")
            normalized = pattern.strip().replace("\\", "/")
            candidate = Path(normalized)
            if candidate.is_absolute() or any(part in ("..", "") for part in candidate.parts):
                raise ValueError("ruleset_catalogs 路径不能是绝对路径或包含 ..")
            resolved = (base / candidate).resolve()
            if resolved != base and base not in resolved.parents:
                raise ValueError("ruleset_catalogs 路径越界")
            if resolved.is_symlink() or not resolved.is_dir():
                raise ValueError(f"ruleset_catalogs 必须是已存在的目录：{normalized}")
            catalogs.append(resolved)
        parents = {catalog.parent for catalog in catalogs}
        if len(parents) != 1:
            raise ValueError("ruleset_catalogs 必须共享同一父目录")
        parent = catalogs[0].parent
        directories = tuple(sorted(catalog.name for catalog in catalogs))
        return parent, directories

    def set_package_hooks(
        self,
        *,
        package_validator: PackageValidator | None = None,
        content_module_guard: ContentModuleGuard | None = None,
    ) -> None:
        """Attach the composition root's install-transaction hooks (FIX-01 §3.3/§3.5).

        单一接线点：组合根（WebAPI）与测试都走这里，避免"某条安装路径没接线"。
        """

        self._package_validator = package_validator
        self._content_module_guard = content_module_guard

    def _guard_package_mutation(self, plugin_id: str, action: str) -> None:
        """Ask the injected guard before changing/removing installed package bytes."""

        guard = self._content_module_guard
        if guard is None:
            return
        guard(str(plugin_id or ""), str(action))

    def _validate_package_before_install(
        self, plugin_dir: Path, manifest: dict[str, Any], expected_plugin_type: str,
    ) -> None:
        """Run the injected package validator inside the install transaction."""

        plugin_type = str(manifest.get("plugin_type") or "")
        if expected_plugin_type and plugin_type != expected_plugin_type:
            raise ValueError(
                f"该接口只接受 {expected_plugin_type} 包，实际为 {plugin_type or 'missing'}"
            )
        validator = self._package_validator
        if validator is None:
            return
        validator(plugin_dir, dict(manifest))

    async def install_from_zip(
        self,
        payload: bytes,
        *,
        overwrite: bool = False,
        allow_any_root: bool = False,
        expected_plugin_type: str = "",
    ) -> PluginPublicDetail:
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
            plugin_id, runtime = self._load_runtime(source_dir, require_directory_match=False)
            if not allow_any_root and source_dir != temp_dir and source_dir.name != plugin_id:
                raise ValueError("插件包顶层目录名必须与插件 ID 一致")
            target_dir = (self.plugins_dir / plugin_id).resolve()
            self._ensure_inside(self.plugins_dir, target_dir)
            if target_dir.exists() and not overwrite:
                raise ValueError(f"插件 {plugin_id} 已存在；如需更新请启用覆盖安装")

            # FIX-01 §3.4/§3.5：包校验与 bound-save guard 必须在任何 mutation
            # （staging/rename/元数据写入）之前完成。
            self._validate_package_before_install(
                source_dir, dict(runtime.manifest), expected_plugin_type,
            )
            self._validate_plugin_requirements_for_manifest(dict(runtime.manifest))
            if target_dir.exists():
                self._validate_plugin_replacement(plugin_id, runtime.manifest)
            self.contributions.validate_static_plugin(
                runtime.manifest,
                source_dir,
                replacing_plugin_id=plugin_id if target_dir.exists() else "",
            )
            if target_dir.exists():
                self._guard_package_mutation(plugin_id, "overwrite")

            staging_dir = (self.plugins_dir / f".{plugin_id}.installing-{secrets.token_hex(6)}").resolve()
            backup_dir = (self.plugins_dir / f".{plugin_id}.backup-{secrets.token_hex(6)}").resolve()
            self._ensure_inside(self.plugins_dir, staging_dir)
            self._ensure_inside(self.plugins_dir, backup_dir)
            journal = {
                "plugin_id": plugin_id,
                "target": str(target_dir),
                "staging": str(staging_dir),
                "backup": str(backup_dir),
                "phase": "prepared",
                "was_running": was_running,
                # receipt 重建数据：commit 后中断时恢复逻辑据此补写 install.json。
                "manifest": dict(runtime.manifest),
                "artifact_hash": hashlib.sha256(payload).hexdigest(),
                "source": "zip",
            }
            self._write_install_journal(journal, append=True)
            shutil.copytree(source_dir, staging_dir)
            try:
                journal["phase"] = "staged"
                self._write_install_journal(journal)
                if target_dir.exists():
                    if plugin_id in self.plugins:
                        current = self.plugins[plugin_id]
                        was_running = bool(current.process and current.process.returncode is None)
                        await self.stop(plugin_id, keep_enabled=True)
                    await _rename_dir_with_retry(target_dir, backup_dir)
                    journal["phase"] = "backup_moved"
                    self._write_install_journal(journal)
                await _rename_dir_with_retry(staging_dir, target_dir)
                journal["phase"] = "published"
                self._write_install_journal(journal)
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                # journal 在 receipt 持久化之后才删除（见下方 with 块外），
                # 避免 commit 后中断留下 published-but-no-receipt 状态。
            except Exception:
                if target_dir.exists() and not (target_dir / "plugin.json").exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                if backup_dir.exists() and not target_dir.exists():
                    await _rename_dir_with_retry(backup_dir, target_dir)
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)
                self.discover()
                self._remove_install_journal(plugin_id)
                if was_running and plugin_id in self.plugins:
                    await self.start(plugin_id)
                raise

        self.discover()
        self._save_install_receipt(
            plugin_id,
            self.plugins[plugin_id].manifest,
            artifact_hash=hashlib.sha256(payload).hexdigest(),
            source="zip",
        )
        # receipt 已持久化，事务 journal 才能安全移除；恢复路径
        # (_recover_install_journal) 会在 commit 后中断时补写缺失 receipt。
        self._remove_install_journal(plugin_id)
        # A replacement can legitimately have a different manifest digest.
        # The first discover happens before the new receipt is persisted, so
        # it conservatively marks the live runtime failed.  Re-discover after
        # the receipt write to clear that transient mismatch and re-register
        # the new contributions before returning to callers.
        self.discover()
        if was_running:
            await self.start(plugin_id)
        return self.public_detail(plugin_id)

    async def install_group_from_zips(
        self, payloads: list[bytes], *, overwrite: bool = False,
    ) -> list[PluginPublicDetail]:
        """Publish a preflighted package group as one recoverable transaction."""

        plan = self.plan_install_group(payloads, overwrite=overwrite)
        if not plan.get("ok"):
            reasons = "; ".join(
                f"{item.get('plugin_id')}: {item.get('reason')}"
                for item in plan.get("blockers", [])
            )
            raise ValueError(f"插件组安装计划被拒绝：{reasons or '未知阻塞'}")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        group_id = secrets.token_hex(8)
        entries: list[dict[str, Any]] = []
        was_running: dict[str, bool] = {}
        staged_sources: list[tuple[str, Path, dict[str, Any], bytes]] = []
        try:
            with tempfile.TemporaryDirectory(prefix="plugin-group-install-", dir=str(self.data_dir)) as temp_name:
                temp_root = Path(temp_name)
                for index, payload in enumerate(payloads):
                    package_dir = temp_root / str(index)
                    package_dir.mkdir()
                    self._extract_zip(bytes(payload), package_dir)
                    source_dir = self._find_install_root(package_dir)
                    plugin_id, runtime = self._load_runtime(source_dir, require_directory_match=False)
                    staged_sources.append((plugin_id, source_dir, dict(runtime.manifest), bytes(payload)))

                for plugin_id, source_dir, manifest, _payload in staged_sources:
                    staging = (self.plugins_dir / f".{plugin_id}.group-{group_id}.installing").resolve()
                    backup = (self.plugins_dir / f".{plugin_id}.group-{group_id}.backup").resolve()
                    target = (self.plugins_dir / plugin_id).resolve()
                    self._ensure_inside(self.plugins_dir, staging)
                    self._ensure_inside(self.plugins_dir, backup)
                    self._ensure_inside(self.plugins_dir, target)
                    if target.exists() and not overwrite:
                        raise ValueError(f"插件 {plugin_id} 已存在；如需更新请启用覆盖安装")
                    if staging.exists() or backup.exists():
                        raise ValueError(f"插件 {plugin_id} 存在未清理的组安装现场")
                    if target.exists():
                        self._guard_package_mutation(plugin_id, "overwrite")
                    shutil.copytree(source_dir, staging)
                    was_running[plugin_id] = bool(
                        self.plugins.get(plugin_id)
                        and self.plugins[plugin_id].process
                        and self.plugins[plugin_id].process.returncode is None
                    )
                    entries.append({
                        "plugin_id": plugin_id,
                        "target": str(target),
                        "staging": str(staging),
                        "backup": str(backup),
                    })
                receipt_records: list[dict[str, Any]] = [
                    {
                        "plugin_id": plugin_id,
                        "manifest": manifest,
                        "artifact_hash": hashlib.sha256(payload).hexdigest(),
                        "source": "group-zip",
                    }
                    for plugin_id, _source_dir, manifest, payload in staged_sources
                ]
                journal_record: dict[str, Any] = {
                    "schema_version": 1, "group_id": group_id,
                    "entries": entries, "published": [],
                    "receipts": receipt_records,
                }
                # journal 携带 receipt 重建数据：commit 后中断时恢复逻辑可补写。
                self._write_group_install_journal({**journal_record, "phase": "staged"})
                for entry in entries:
                    plugin_id = str(entry["plugin_id"])
                    target = Path(entry["target"])
                    backup = Path(entry["backup"])
                    if target.exists():
                        if was_running.get(plugin_id):
                            await self.stop(plugin_id, keep_enabled=True)
                        await _rename_dir_with_retry(target, backup)
                self._write_group_install_journal({**journal_record, "phase": "backup_moved"})
                published: list[str] = []
                for entry in entries:
                    await _rename_dir_with_retry(Path(entry["staging"]), Path(entry["target"]))
                    published.append(str(entry["plugin_id"]))
                    self._write_group_install_journal({
                        **journal_record, "phase": "publishing", "published": published,
                    })
                self._write_group_install_journal({
                    **journal_record, "phase": "published", "published": published,
                })
                for entry in entries:
                    backup = Path(entry["backup"])
                    if backup.exists():
                        shutil.rmtree(backup, ignore_errors=True)
                # receipt 必须在事务 journal 删除前持久化；进程在 commit 后
                # 中断时，_recover_group_install_journal 会按 journal 补写缺失
                # 的 receipt，避免出现 published-but-no-receipt 的不可自愈状态。
                self.discover()
                for record in receipt_records:
                    self._save_install_receipt(
                        str(record["plugin_id"]), record["manifest"],
                        artifact_hash=str(record["artifact_hash"]),
                        source=str(record["source"]),
                    )
                self._group_install_journal_path.unlink(missing_ok=True)
        except Exception:
            # Leave the journal for the next discover to perform the same
            # rollback after a process interruption.  Do an immediate best
            # effort recovery as well so the current caller sees old packages.
            self._recover_group_install_journal()
            self.discover()
            raise

        self.discover()
        for plugin_id, running in was_running.items():
            if running:
                await self.start(plugin_id)
        return [self.public_detail(plugin_id) for plugin_id, *_ in staged_sources]

    @property
    def _install_journal_path(self) -> Path:
        return self.data_dir / "_plugin_install_journal.json"

    def _read_install_journal(self) -> list[dict[str, Any]]:
        path = self._install_journal_path
        if not path.exists():
            return []
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.logger.error("插件安装 journal 无法读取，保留现场等待人工处理: %s", path)
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict) and str(item.get("plugin_id") or "")]

    def _write_install_journal(self, record: dict[str, Any], *, append: bool = False) -> None:
        records = self._read_install_journal() if append else self._read_install_journal()
        plugin_id = str(record.get("plugin_id") or "")
        records = [item for item in records if str(item.get("plugin_id") or "") != plugin_id]
        records.append(dict(record))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_json(self._install_journal_path, records)

    def _remove_install_journal(self, plugin_id: str) -> None:
        records = [item for item in self._read_install_journal()
                   if str(item.get("plugin_id") or "") != str(plugin_id or "")]
        path = self._install_journal_path
        if records:
            self._atomic_json(path, records)
        elif path.exists():
            try:
                path.unlink()
            except OSError:
                self.logger.warning("清理插件安装 journal 失败: %s", path, exc_info=True)

    def _recover_install_journal(self) -> None:
        records = self._read_install_journal()
        if not records:
            return
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        for record in records:
            plugin_id = str(record.get("plugin_id") or "")
            try:
                target = Path(str(record.get("target") or "")).resolve()
                staging = Path(str(record.get("staging") or "")).resolve()
                backup = Path(str(record.get("backup") or "")).resolve()
                self._ensure_inside(self.plugins_dir, target)
                self._ensure_inside(self.plugins_dir, staging)
                self._ensure_inside(self.plugins_dir, backup)
                # A valid published target is the committed side of the
                # transaction. Any leftover staging/backup is garbage.
                if target.is_dir() and (target / "plugin.json").is_file():
                    if staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
                    if backup.exists():
                        shutil.rmtree(backup, ignore_errors=True)
                    # Commit 后中断：补写缺失的安装 receipt（journal 记录
                    # 自身携带 manifest/artifact_hash/source 重建数据）。
                    self._backfill_install_receipt(plugin_id, record, target)
                    continue
                # Otherwise restore the previous package if the process died
                # after moving it aside. Never promote an uncommitted staging
                # directory into a live plugin.
                if backup.exists() and not target.exists():
                    _rename = target
                    backup.rename(_rename)
                elif target.exists() and not (target / "plugin.json").is_file():
                    shutil.rmtree(target, ignore_errors=True)
                    if backup.exists():
                        backup.rename(target)
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
            except Exception:
                self.logger.error("插件安装 journal 恢复失败，已保留现场: %s", plugin_id, exc_info=True)
        try:
            self._install_journal_path.unlink()
        except OSError:
            self.logger.warning("清理插件安装 journal 失败: %s", self._install_journal_path, exc_info=True)

    @property
    def _group_install_journal_path(self) -> Path:
        return self.data_dir / "_plugin_group_install_journal.json"

    def _write_group_install_journal(self, record: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_json(self._group_install_journal_path, record)

    def _recover_group_install_journal(self) -> None:
        """Roll back an incomplete multi-package publish before discovery."""

        path = self._group_install_journal_path
        if not path.exists():
            return
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            entries = record.get("entries") if isinstance(record, dict) else None
            phase = str(record.get("phase") or "") if isinstance(record, dict) else ""
            published = {
                str(item) for item in (record.get("published") or [])
            } if isinstance(record, dict) else set()
            if not isinstance(entries, list):
                raise ValueError("group journal entries are invalid")
            receipts_by_id: dict[str, dict[str, Any]] = {}
            journal_receipts = record.get("receipts")
            if isinstance(journal_receipts, list):
                for journal_receipt in journal_receipts:
                    if isinstance(journal_receipt, dict):
                        receipts_by_id[str(journal_receipt.get("plugin_id") or "")] = journal_receipt
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("group journal entry is invalid")
                target = Path(str(entry.get("target") or "")).resolve()
                staging = Path(str(entry.get("staging") or "")).resolve()
                backup = Path(str(entry.get("backup") or "")).resolve()
                self._ensure_inside(self.plugins_dir, target)
                self._ensure_inside(self.plugins_dir, staging)
                self._ensure_inside(self.plugins_dir, backup)
                if phase == "published":
                    if staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
                    if backup.exists():
                        shutil.rmtree(backup, ignore_errors=True)
                    # Commit 后中断：补写缺失的安装 receipt，保证重启后
                    # marketplace 身份校验与覆盖安装仍有依据。
                    self._backfill_install_receipt(
                        str(entry.get("plugin_id") or ""),
                        receipts_by_id.get(str(entry.get("plugin_id") or "")),
                        target,
                    )
                    continue
                # No commit marker means the group is not atomic. Restore every
                # old target and discard all candidate staging directories.
                if str(entry.get("plugin_id") or "") in published and target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                if target.exists() and backup.exists():
                    shutil.rmtree(target, ignore_errors=True)
                if backup.exists() and not target.exists():
                    backup.rename(target)
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            self.logger.error("插件组安装 journal 恢复失败，保留现场等待人工处理", exc_info=True)
            return
        try:
            path.unlink()
        except OSError:
            self.logger.warning("清理插件组安装 journal 失败: %s", path, exc_info=True)

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
                    receipt = self._load_install_receipt(item["id"])
                    item["installed_manifest_digest"] = str(receipt.get("manifest_digest") or "")
        if _PLUGIN_AUTO_UPDATE_ENABLED and (self._auto_update_task is None or self._auto_update_task.done()):
            self._auto_update_task = asyncio.create_task(self._auto_update_in_background())
        return listing

    def _install_lock(self, plugin_id: str) -> asyncio.Lock:
        lock = self._install_locks.get(plugin_id)
        if lock is None:
            lock = self._install_locks[plugin_id] = asyncio.Lock()
        return lock

    async def install_from_marketplace(
        self, plugin_id: str, *, overwrite: bool = False, expected_plugin_type: str = "",
    ) -> dict[str, Any]:
        async with self._install_lock(plugin_id):
            return await self._install_from_marketplace_unlocked(
                plugin_id, overwrite=overwrite,
                expected_plugin_type=expected_plugin_type,
            )

    async def _install_from_marketplace_unlocked(
        self, plugin_id: str, *, overwrite: bool = False, expected_plugin_type: str = "",
    ) -> dict[str, Any]:
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
        detail = await self.install_from_zip(
            package["payload"], overwrite=overwrite, allow_any_root=True,
            expected_plugin_type=expected_plugin_type,
        )
        self._save_marketplace_metadata(plugin_id, {
            "repository_url": market_item.get("repository_url", ""),
            "release_tag": market_item.get("release_tag", ""),
            "commit_sha": commit_sha,
            "risk_level": market_item.get("risk_level", ""),
            "update_policy": market_item.get("update_policy", "notify"),
            "approved_permissions": market_item.get("approved_permissions", []),
            "approved_release_tag": market_item.get("approved_release_tag", ""),
            "approved_commit": market_item.get("approved_commit", ""),
            "approved_artifact_sha256": market_item.get("approved_artifact_sha256", ""),
            "artifact_hash": package.get("artifact_hash", ""),
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
        dependents = self._dependents_requiring(plugin_id)
        if dependents:
            names = ", ".join(str(item["id"]) for item in dependents)
            raise ValueError(
                f"cannot uninstall plugin {plugin_id}; required by installed plugin(s): {names}"
            )
        # FIX-01 §3.5：删除 package bytes 前必须过 bound-save guard（与覆盖安装
        # 同一条 choke point，任何入口都绕不过）。
        self._guard_package_mutation(plugin_id, "uninstall")
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

    async def update_config(
        self,
        plugin_id: str,
        changes: dict[str, Any],
    ) -> PluginPublicDetail:
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
            # 声明型插件没有进程，"启动"就是"启用"：必须持久化，否则主程序重启后
            # 又回到 disabled，内容包/模块每次开机都要用户重新点一次启用。
            if not runtime.config.get("enabled"):
                runtime.config["enabled"] = True
                self._save_config(plugin_id, runtime)
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
            kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
                initialize_runtime_capabilities(
                    self._plugin_type(runtime.manifest),
                    runtime,
                    initialized,
                )
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
            clear_runtime_capabilities(runtime)
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

    async def restart_api_consumers(self) -> int:
        """重启正在运行且依赖 diceframe.http 的插件，返回重启数量。

        插件进程在 spawn 时从 base_env 快照 TRPG_API_BASE，之后不再感知变化；
        内部 API 基址在监听器启动后被修正（回退到实际成功的监听器）时，只有
        重启进程才能拿到新地址。还没启动的插件下次 start 时自然读到新值。
        """

        restarted = 0
        for plugin_id, runtime in self.plugins.items():
            permissions = set(self._plugin_permissions(runtime))
            process_running = bool(runtime.process and runtime.process.returncode is None)
            if "diceframe.http" in permissions and process_running:
                await self.restart(plugin_id, require_enabled=False)
                restarted += 1
        return restarted

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

    def api_token_active(self, plugin_id: str, token_digest: str) -> bool:
        """Whether an open stream admitted with ``plugin_id``'s API token may continue.

        ``token_digest`` is the SHA-256 hex digest of the token the stream was
        admitted with; the plaintext is not kept. The plugin must still be
        installed, still hold a token with that digest and still have
        ``diceframe.http`` (as :meth:`authenticate_api_token` requires).

        This is stricter than admission: a plugin that is disabled and has no
        running process is treated as turned off, even though its token would
        still authenticate a new request. An enabled plugin between restarts,
        or one started from the UI switch without being enabled, stays active.
        """
        expected = self._api_tokens.get(plugin_id)
        runtime = self.plugins.get(plugin_id)
        if not expected or not token_digest or runtime is None:
            return False
        expected_digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected_digest, str(token_digest)):
            return False
        if "diceframe.http" not in self._plugin_permissions(runtime):
            return False
        process_running = bool(runtime.process and runtime.process.returncode is None)
        return bool(runtime.config.get("enabled") or process_running)

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
        clear_runtime_capabilities(runtime)
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
        clear_runtime_capabilities(runtime)
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

    async def rescan(self) -> list[PluginPublicDetail]:
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
        clear_runtime_capabilities(runtime)
        if alive_sec >= _RESTART_STABLE_SECONDS:
            runtime.restart_delay_sec = _RESTART_BASE_DELAY
        delay = runtime.restart_delay_sec
        if alive_sec < _RESTART_STABLE_SECONDS:
            runtime.restart_delay_sec = min(runtime.restart_delay_sec * 2, _RESTART_MAX_DELAY)
        self.logger.warning("插件 %s 意外退出，%.0f 秒后尝试自动重启，code=%s", plugin_id, delay, code)
        await asyncio.sleep(delay)
        if (
            runtime is not None
            and self.plugins.get(plugin_id) is runtime
            and runtime.config.get("enabled")
            and runtime.status == "failed"
        ):
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

    @staticmethod
    def _manifest_digest(manifest: dict[str, Any]) -> str:
        encoded = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _load_install_receipt(self, plugin_id: str) -> dict[str, Any]:
        path = self.data_dir / plugin_id / "install.json"
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _backfill_install_receipt(
        self,
        plugin_id: str,
        receipt: dict[str, Any] | None,
        target: Path,
    ) -> None:
        """事务 commit 后中断时，按 journal 数据补写缺失的安装 receipt。

        仅在 receipt 缺失且目标包确实存在时写入；写入失败会向上抛出，
        由恢复逻辑保留 journal 以便下次重启重试（fail closed）。
        """
        if not plugin_id or receipt is None:
            return
        receipt_path = self.data_dir / plugin_id / "install.json"
        if receipt_path.exists() or not target.is_dir():
            return
        manifest = receipt.get("manifest")
        if not isinstance(manifest, dict):
            return
        self._save_install_receipt(
            plugin_id, manifest,
            artifact_hash=str(receipt.get("artifact_hash") or ""),
            source=str(receipt.get("source") or "group-zip"),
        )

    def _save_install_receipt(
        self,
        plugin_id: str,
        manifest: dict[str, Any],
        *,
        artifact_hash: str = "",
        source: str = "zip",
    ) -> None:
        folder = self.data_dir / plugin_id
        folder.mkdir(parents=True, exist_ok=True)
        resolved_dependencies: list[dict[str, str]] = []
        try:
            for requirement in parse_plugin_requirements(manifest):
                dependency = self.plugins.get(requirement["id"])
                if dependency is not None:
                    resolved_dependencies.append({
                        "id": requirement["id"],
                        "minimum_version": requirement["minimum_version"],
                        "version": str(dependency.manifest.get("version") or ""),
                    })
        except ValueError:
            # The manifest was already validated before install.  Keep receipt
            # writing fail closed if a future schema reaches this boundary.
            resolved_dependencies = []
        self._atomic_json(folder / "install.json", {
            "schema_version": 1,
            "plugin_id": plugin_id,
            "version": str(manifest.get("version") or ""),
            "manifest_digest": self._manifest_digest(manifest),
            "artifact_hash": str(artifact_hash or ""),
            "source": source,
            "resolved_dependencies": resolved_dependencies,
        })

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
        supported_versions = {
            "content_schema_version": {1, 2},
            "locale_schema_version": {1},
        }
        for field in ("content_schema_version", "locale_schema_version"):
            if field in manifest:
                try:
                    version = int(manifest[field])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{field} 必须是正整数") from exc
                if version not in supported_versions[field]:
                    raise ValueError(f"不支持的 {field}: {version}")
        if "default_locale" in manifest and not str(manifest["default_locale"] or "").strip():
            raise ValueError("default_locale 不能为空")
        plugin_type = self._plugin_type(manifest)
        if plugin_type not in _PLUGIN_TYPES:
            raise ValueError(f"不支持的 plugin_type：{plugin_type}")
        schema_path = (plugin_dir / str(manifest.get("config_schema") or "config.schema.json")).resolve()
        self._ensure_inside(plugin_dir, schema_path)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self._validate_schema(schema)
        validate_content_module_profile(manifest)
        # M01: validate the established hard runtime requirement shape before
        # any plugin config or contribution is exposed.  Resolution remains
        # with the ruleset binding owner; the host does not guess or download.
        parse_ruleset_requirements(manifest)
        self._validate_manifest_permissions(manifest)
        self._validate_runtime_permissions(manifest, schema)
        self._validate_entrypoint(manifest, plugin_type)
        validate_contributes(manifest, plugin_dir)
        adventure_packages = self._validate_adventure_packages(manifest, plugin_dir)
        adventure_root, adventure_dirs = (
            adventure_packages if adventure_packages is not None else (None, ())
        )
        ruleset_catalogs = self._validate_ruleset_catalogs(manifest, plugin_dir)
        catalog_root, catalog_dirs = (
            ruleset_catalogs if ruleset_catalogs is not None else (None, ())
        )
        rulebooks = self._validate_rulebooks(manifest, plugin_dir)
        prebuilt_ui = self._validate_prebuilt_ui(manifest, plugin_dir)
        return plugin_id, PluginRuntime(
            manifest, schema, plugin_dir,
            adventure_packages_root=adventure_root,
            adventure_package_directories=adventure_dirs,
            ruleset_catalogs_root=catalog_root,
            ruleset_catalog_directories=catalog_dirs,
            rulebooks=rulebooks,
            prebuilt_ui=prebuilt_ui,
        )

    @staticmethod
    def _validate_prebuilt_ui(
        manifest: dict[str, Any], plugin_dir: Path,
    ) -> dict[str, Any] | None:
        """Validate an optional package-owned U02 prebuilt UI surface."""

        declared = manifest.get("prebuilt_ui")
        if declared is None:
            return None
        if not isinstance(declared, dict) or set(declared) != {"entrypoint", "assets"}:
            raise ValueError("prebuilt_ui must contain only entrypoint and assets")
        entrypoint = str(declared.get("entrypoint") or "").replace("\\", "/").strip("/")
        assets = declared.get("assets")
        if (
            not entrypoint.endswith((".html", ".htm"))
            or not isinstance(assets, list)
            or len(assets) > 256
        ):
            raise ValueError("prebuilt_ui entrypoint/assets are invalid")
        paths = [entrypoint, *[str(item or "").replace("\\", "/").strip("/") for item in assets]]
        if len(set(paths)) != len(paths):
            raise ValueError("prebuilt_ui contains duplicate paths")
        normalized: list[str] = []
        for relative in paths:
            candidate = Path(relative)
            if (
                not relative
                or len(relative) > 240
                or candidate.is_absolute()
                or any(part in {"", ".", ".."} for part in candidate.parts)
            ):
                raise ValueError("prebuilt_ui path is invalid")
            target = (plugin_dir / relative).resolve()
            PluginHost._ensure_inside(plugin_dir, target)
            if target.is_symlink() or not target.is_file():
                raise ValueError(f"prebuilt_ui file is missing: {relative}")
            normalized.append(relative)
        return {"entrypoint": normalized[0], "assets": normalized[1:]}

    @staticmethod
    def _validate_rulebooks(
        manifest: dict[str, Any], plugin_dir: Path,
    ) -> tuple[dict[str, Any], ...]:
        """Validate the D01 ``rulebooks`` declaration without exposing files."""

        declared = manifest.get("rulebooks")
        if declared is None:
            return ()
        if not isinstance(declared, list) or len(declared) > 32:
            raise ValueError("rulebooks 必须是最多 32 项的数组")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in declared:
            if not isinstance(entry, dict):
                raise ValueError("rulebooks entry 必须是对象")
            fields = set(entry)
            if fields not in (
                {"id", "title", "path", "audience"},
                {"id", "title", "url", "audience"},
            ):
                raise ValueError("rulebooks entry fields are invalid")
            book_id = str(entry.get("id") or "").strip()
            title = str(entry.get("title") or "").strip()
            relative = str(entry.get("path") or "").replace("\\", "/").strip("/")
            external_url = str(entry.get("url") or "").strip()
            audience = str(entry.get("audience") or "").strip().lower()
            if not _RULEBOOK_ID_RE.fullmatch(book_id) or book_id in seen:
                raise ValueError("rulebooks id is invalid or duplicated")
            if not title or len(title) > 200:
                raise ValueError("rulebooks title is invalid")
            if audience not in {"player", "gm"}:
                raise ValueError("rulebooks audience must be player or gm")
            if "url" in fields:
                parsed = urlsplit(external_url)
                if (
                    len(external_url) > 2048
                    or parsed.scheme.lower() != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                ):
                    raise ValueError("rulebooks external URL must be a public HTTPS URL")
                normalized.append({
                    "id": book_id,
                    "title": title,
                    "url": external_url,
                    "audience": audience,
                    "kind": "external",
                })
                seen.add(book_id)
                continue
            candidate = Path(relative)
            if (
                not relative
                or candidate.is_absolute()
                or any(part in {"", ".", ".."} for part in candidate.parts)
                or len(relative) > 240
            ):
                raise ValueError("rulebooks path is invalid")
            target = (plugin_dir / relative).resolve()
            PluginHost._ensure_inside(plugin_dir, target)
            if target.is_symlink() or not target.is_file():
                raise ValueError(f"rulebook file is missing: {relative}")
            suffix = target.suffix.lower()
            if suffix not in {".md", ".markdown", ".pdf"}:
                raise ValueError("rulebooks only support Markdown or PDF files")
            seen.add(book_id)
            normalized.append({
                "id": book_id,
                "title": title,
                "path": relative,
                "audience": audience,
                "kind": "pdf" if suffix == ".pdf" else "markdown",
            })
        return tuple(normalized)

    @staticmethod
    def _validate_adventure_packages(
        manifest: dict[str, Any], plugin_dir: Path,
    ) -> tuple[Path, tuple[str, ...]] | None:
        """Validate manifest ``adventure_packages``（MOD-03，母方案 §107）。

        声明式冒险包：路径相对、不越界、真实目录（非 symlink）、含
        manifest.json；全部包必须共享同一父目录（v1 约束：一个 plugin =
        一个 declared-only loader）。仅 content-pack 允许声明。返回
        (公共父目录, 包目录名集合)，未声明返回 None。
        """

        declared = manifest.get("adventure_packages")
        if declared is None:
            return None
        if str(manifest.get("plugin_type") or "") != "content-pack":
            raise ValueError("adventure_packages 仅支持 content-pack")
        if not isinstance(declared, list) or not declared:
            raise ValueError("adventure_packages 必须是非空字符串数组")
        base = plugin_dir.resolve()
        packages: list[Path] = []
        for pattern in declared:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError("adventure_packages 路径必须是非空字符串")
            normalized = pattern.strip().replace("\\", "/")
            candidate = Path(normalized)
            if candidate.is_absolute() or any(part in ("..", "") for part in candidate.parts):
                raise ValueError("adventure_packages 路径不能是绝对路径或包含 ..")
            resolved = (base / candidate).resolve()
            if resolved != base and base not in resolved.parents:
                raise ValueError("adventure_packages 路径越界")
            if resolved.is_symlink() or not resolved.is_dir():
                raise ValueError(f"adventure_packages 必须是已存在的目录：{normalized}")
            if not (resolved / "manifest.json").is_file():
                raise ValueError(f"adventure_packages 缺少 manifest.json：{normalized}")
            packages.append(resolved)
        parents = {package.parent for package in packages}
        if len(parents) != 1:
            raise ValueError("adventure_packages 必须共享同一父目录")
        parent = packages[0].parent
        directories = tuple(sorted(package.name for package in packages))
        return parent, directories

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

    def inspect_package_manifest(self, payload: bytes) -> tuple[str, dict[str, Any]]:
        """Validate a package archive and return its declared plugin identity."""

        return self._inspect_zip_manifest(payload)

    def validate_package(self, payload: bytes, validate: Callable[[Path, dict[str, Any]], Any]) -> Any:
        """Extract a package and run ``validate(directory, manifest)`` inside it.

        FIX-01 §3.3：预览必须能对**已解压的真实包**做同一套深度校验，而不是只看
        manifest；临时目录的生命周期由宿主管理，校验结果原样返回。
        """

        if not payload:
            raise ValueError("插件包为空")
        if len(payload) > MAX_PLUGIN_PACKAGE_BYTES:
            raise ValueError("插件包不能超过 20 MB")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="plugin-validate-", dir=str(self.data_dir)) as temp_name:
            temp_dir = Path(temp_name)
            self._extract_zip(payload, temp_dir)
            source_dir = self._find_install_root(temp_dir)
            _plugin_id, runtime = self._load_runtime(source_dir, require_directory_match=False)
            return validate(source_dir, dict(runtime.manifest))

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
    def _plugin_type(manifest: Mapping[str, object]) -> str:
        return str(manifest.get("plugin_type") or "").strip()

    @staticmethod
    def _has_entrypoint(manifest: Mapping[str, object]) -> bool:
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
