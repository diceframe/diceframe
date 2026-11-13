"""Plugin-owned external ruleset runtimes → RulesetRuntimeRegistry 同步桥（T3）。

职责边界：

- ``PluginHost`` 只负责插件生命周期，并在插件集合可能影响外部 runtime 可用性时
  调用 :meth:`PluginHost._notify_ruleset_runtime_changed`；宿主不感知注册表；
- 本模块把「已安装且启用的 ``ruleset-runtime`` 插件」翻译成
  :class:`~src.rulesets.remote_adapter.RemoteRulesetRuntimeAdapter`，并同步进
  组合根唯一的 :class:`~src.rulesets.registry.RulesetRuntimeRegistry`；
- 每个插件 runtime 的 stdio 子进程由注入 transport 持有：注册即拉起并探活
  （fail closed：探活失败不注册，resolve 时抛 ``UnknownRulesetRuntimeError``）；
  注销/停用/卸载时关闭子进程；
- 同步是**幂等全量重放**：以 (plugin_id, 声明, entrypoint) 指纹复用已注册的
  adapter，重复通知不会重启子进程。

fail closed 语义（全部保持 resolve 侧既有异常行为）：

- 声明非法 / 探活失败 / 与内置 runtime 重名 → 该 runtime 不注册；
- 插件停用 / 卸载 / 安装校验失败 → 对应 runtime 撤销注册；
- 版本不足 → 既有 ``IncompatibleRulesetRuntimeError``。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

from src.plugin_host.requirements import parse_ruleset_runtime_declaration
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.registry import RulesetRuntimeRegistry
from src.rulesets.remote_adapter import RemoteRulesetRuntimeAdapter
from src.rulesets.stdio_transport import StdioRuntimeTransport

logger = logging.getLogger("trpg")

_RULESET_RUNTIME_PLUGIN_TYPE = "ruleset-runtime"


class PluginRulesetRuntimeSource:
    """Keep the registry in sync with installed ruleset-runtime plugins."""

    def __init__(self, registry: RulesetRuntimeRegistry) -> None:
        self._registry = registry
        # runtime_id -> (fingerprint, adapter)；指纹相同的重复同步直接复用。
        self._owned: dict[str, tuple[str, RemoteRulesetRuntimeAdapter]] = {}

    # ---------------------------------------------------------------- 同步

    def sync(self, plugin_host: Any) -> dict[str, Any]:
        """Idempotent full resync; returns a report for logs and tests."""

        desired: dict[str, dict[str, Any]] = {}
        for plugin_id, runtime in plugin_host.plugins.items():
            manifest = getattr(runtime, "manifest", None) or {}
            if str(manifest.get("plugin_type") or "") != _RULESET_RUNTIME_PLUGIN_TYPE:
                continue
            if not (getattr(runtime, "config", {}) or {}).get("enabled"):
                continue
            if getattr(runtime, "status", "") == "failed":
                continue
            try:
                declaration = parse_ruleset_runtime_declaration(manifest)
            except ValueError as exc:
                logger.warning("插件 %s 的 ruleset_runtime 声明无效，保持未注册: %s", plugin_id, exc)
                continue
            desired[declaration["runtime_id"]] = {
                "plugin_id": plugin_id,
                "runtime": runtime,
                "declaration": declaration,
            }

        removed = sorted(self._revoke_stale(desired))
        registered: list[str] = []
        failed: dict[str, str] = {}
        host_data_dir = Path(getattr(plugin_host, "data_dir", "") or "").resolve() if getattr(plugin_host, "data_dir", None) else None
        for runtime_id in sorted(desired):
            entry = desired[runtime_id]
            fingerprint = self._fingerprint(entry["plugin_id"], entry["declaration"], entry["runtime"])
            existing = self._owned.get(runtime_id)
            if existing and existing[0] == fingerprint:
                registered.append(runtime_id)
                continue
            if existing is not None:
                self._drop(runtime_id)
            try:
                adapter = self._build_adapter(
                    entry["plugin_id"], entry["runtime"], entry["declaration"], host_data_dir,
                )
                # 注册前探活：起不来的 runtime 不暴露给 resolve（fail closed）。
                adapter.describe_experience({}, "zh-CN")
                self._registry.register(adapter)
                self._owned[runtime_id] = (fingerprint, adapter)
                registered.append(runtime_id)
            except Exception as exc:  # noqa: BLE001 - 单个坏 runtime 不拖垮其它注册
                failed[runtime_id] = str(exc)
                logger.warning("外部规则 runtime %s 注册失败（fail closed）: %s", runtime_id, exc)
        if removed:
            logger.info("外部规则 runtime 已撤销注册: %s", ", ".join(removed))
        if registered:
            logger.info("外部规则 runtime 已注册: %s", ", ".join(registered))
        return {"registered": registered, "removed": removed, "failed": failed}

    def close(self) -> None:
        """Shut down every adapter owned by this source and revoke registrations.

        组合根 teardown 与显式撤销共用：close 后本源注册过的 runtime 一律不可
        再解析（fail closed），子进程由 adapter.close() 终止。
        """

        for runtime_id in list(self._owned):
            self._drop(runtime_id)
            self._registry.unregister(runtime_id)

    # ---------------------------------------------------------------- 内部

    def _revoke_stale(self, desired: dict[str, dict[str, Any]]) -> list[str]:
        removed: list[str] = []
        for runtime_id in sorted(self._owned):
            if runtime_id in desired:
                continue
            self._drop(runtime_id)
            self._registry.unregister(runtime_id)
            removed.append(runtime_id)
        return removed

    def _drop(self, runtime_id: str) -> None:
        entry = self._owned.pop(runtime_id, None)
        if entry is not None:
            entry[1].close()

    def _fingerprint(self, plugin_id: str, declaration: dict[str, Any], runtime: Any) -> str:
        payload = json.dumps(
            {
                "plugin_id": plugin_id,
                "declaration": declaration,
                "entrypoint": list((getattr(runtime, "manifest", {}) or {}).get("entrypoint") or []),
                "plugin_dir": str(getattr(getattr(runtime, "directory", None), "resolve", lambda: "")() or ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _build_adapter(
        self, plugin_id: str, runtime: Any, declaration: dict[str, Any],
        host_data_dir: Path | None,
    ) -> RemoteRulesetRuntimeAdapter:
        plugin_dir = Path(getattr(runtime, "directory")).resolve()
        command = self._expand_command(
            list((getattr(runtime, "manifest", {}) or {}).get("entrypoint") or []),
            plugin_dir,
        )
        ledger_path: Path | None = None
        if host_data_dir is not None:
            ledger_path = host_data_dir / plugin_id / "runtime" / "ruleset-receipts.json"
            try:
                ledger_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                ledger_path = None
        transport = StdioRuntimeTransport(
            command,
            cwd=plugin_dir,
            timeout_seconds=declaration["timeout_seconds"],
        )
        return RemoteRulesetRuntimeAdapter(
            declaration["runtime_id"],
            declaration["runtime_version"],
            RulesetCapabilities(**declaration["capabilities"]),
            transport,
            timeout_seconds=declaration["timeout_seconds"],
            ledger_path=ledger_path,
        )

    @staticmethod
    def _expand_command(command: list[str], plugin_dir: Path) -> list[str]:
        expanded: list[str] = []
        for index, item in enumerate(command):
            value = str(item).replace("{plugin_dir}", str(plugin_dir))
            if index == 0 and value == "{python}":
                value = sys.executable
            elif not Path(value).is_absolute() and Path(value).suffix in {".py", ".exe", ".cmd", ".bat", ".ps1"}:
                # 相对脚本路径一律相对插件包目录解析，杜绝依赖调用方 cwd。
                value = str(plugin_dir / value)
            expanded.append(value)
        if not expanded:
            raise ValueError("ruleset-runtime entrypoint is empty")
        return expanded
