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
- 同步是**幂等全量重放**：以 (plugin_id, 声明, entrypoint, 工件内容) 指纹复用
  已注册的 adapter，重复通知不会重启子进程；指纹变化时先构建并探活新实现，
  成功后一致切换，失败保留旧可用实现。

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
from dataclasses import replace as _dataclass_replace
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
        """Idempotent full resync; returns a report for logs and tests.

        切换协议（P1-02）：先构建并探活新 adapter，成功后才以「注销旧 → 注册新 →
        更新所有权 → 关闭旧子进程」的顺序切换；构建/探活失败时**保留旧可用实现**，
        不产生"注册表挂着已关闭 adapter"的空洞。跨插件重名 runtime_id 按歧义
        显式拒绝（fail closed），不依赖遍历顺序。
        """

        desired, collisions = self._collect_desired(plugin_host)

        removed = sorted(self._revoke_stale(desired))
        registered: list[str] = []
        failed: dict[str, str] = dict(collisions)
        host_data_dir = Path(getattr(plugin_host, "data_dir", "") or "").resolve() if getattr(plugin_host, "data_dir", None) else None
        for runtime_id in sorted(desired):
            entry = desired[runtime_id]
            fingerprint = self._fingerprint(entry["plugin_id"], entry["declaration"], entry["runtime"])
            existing = self._owned.get(runtime_id)
            if existing and existing[0] == fingerprint:
                registered.append(runtime_id)
                continue
            new_adapter = None
            try:
                new_adapter = self._build_adapter(
                    entry["plugin_id"], entry["runtime"], entry["declaration"], host_data_dir,
                )
                # 注册前探活：起不来的 runtime 不暴露给 resolve（fail closed）。
                new_adapter.describe_experience({}, "zh-CN")
                self._verify_declared_sequential_capability(new_adapter)
            except Exception as exc:  # noqa: BLE001 - 单个坏 runtime 不拖垮其它注册
                # 新实现不可用：旧 adapter（若有）保持注册且子进程仍然存活，
                # 玩家继续拿到可用规则；新构建的 adapter 必须关闭避免泄漏。
                if new_adapter is not None:
                    try:
                        new_adapter.close()
                    except Exception:  # noqa: BLE001 - 清理失败不影响报告
                        logger.warning("外部规则 runtime %s 探活失败后清理子进程出错", runtime_id, exc_info=True)
                failed[runtime_id] = str(exc)
                logger.warning("外部规则 runtime %s 注册失败（fail closed）: %s", runtime_id, exc)
                continue
            # 一致切换：只撤**本源拥有**的旧注册（幂等）。注册表里的外部条目
            # （内置实现或其它来源）不得由本源注销——直接尝试注册，重名失败
            # 会落入 failed 并保持原实现（P1-02 回归：内置实现不被插件顶掉）。
            if existing is not None:
                self._registry.unregister(runtime_id)
            try:
                self._registry.register(new_adapter)
            except Exception as exc:  # noqa: BLE001
                failed[runtime_id] = str(exc)
                logger.warning("外部规则 runtime %s 注册进注册表失败: %s", runtime_id, exc)
                try:
                    new_adapter.close()
                except Exception:  # noqa: BLE001
                    logger.warning("外部规则 runtime %s 注册失败后清理子进程出错", runtime_id, exc_info=True)
                if existing is not None:
                    try:
                        self._registry.register(existing[1])
                        self._owned[runtime_id] = existing
                        registered.append(runtime_id)
                    except Exception:  # noqa: BLE001
                        logger.exception("外部规则 runtime %s 回退旧实现失败，进入不可用状态", runtime_id)
                continue
            self._owned[runtime_id] = (fingerprint, new_adapter)
            if existing is not None:
                existing[1].close()
            registered.append(runtime_id)
        if removed:
            logger.info("外部规则 runtime 已撤销注册: %s", ", ".join(removed))
        if registered:
            logger.info("外部规则 runtime 已注册: %s", ", ".join(registered))
        if failed:
            logger.warning("外部规则 runtime 同步失败项: %s", failed)
        return {"registered": registered, "removed": removed, "failed": failed}

    def _collect_desired(
        self, plugin_host: Any,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        desired: dict[str, dict[str, Any]] = {}
        owners: dict[str, list[str]] = {}
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
            runtime_id = declaration["runtime_id"]
            desired[runtime_id] = {
                "plugin_id": plugin_id,
                "runtime": runtime,
                "declaration": declaration,
            }
            owners.setdefault(runtime_id, []).append(plugin_id)
        collisions: dict[str, str] = {}
        for runtime_id, owner_ids in owners.items():
            if len(owner_ids) > 1:
                # 跨插件重名：按插件身份显式拒绝歧义，绝不靠遍历顺序取胜。
                collisions[runtime_id] = (
                    "runtime_id 被多个启用的插件声明: " + ", ".join(sorted(owner_ids))
                )
                desired.pop(runtime_id, None)
        return desired, collisions

    def close(self) -> None:
        """Shut down every adapter owned by this source and revoke registrations.

        组合根 teardown 与显式撤销共用：close 后本源注册过的 runtime 一律不可
        再解析（fail closed），子进程由 adapter.close() 终止。
        """

        for runtime_id in list(self._owned):
            self._drop(runtime_id)
            self._registry.unregister(runtime_id)

    # ---------------------------------------------------------------- 内部

    def _verify_declared_sequential_capability(self, adapter: RemoteRulesetRuntimeAdapter) -> None:
        """P1-01 合同检验：宣称 R05/SK-01 能力就必须真的提供协议操作。

        用一次空计划探针验证 validate_sequential_plan 存在且返回对象；协议层
        失败（未实现该 op / 探针错误 / 返回非对象）时把能力位剥离——UI 与
        服务端不再按"支持多技能"执行，真实调用在运行时侧继续 fail closed。
        """
        capabilities = adapter.capabilities
        if not (capabilities.sequential_checks or capabilities.composite_action):
            return
        try:
            probed = adapter.validate_sequential_plan(
                {}, {"intent_id": "capability-probe", "steps": []}, "probe", True,
            )
            supported = isinstance(probed, dict)
        except Exception as exc:  # noqa: BLE001 - 探针失败即视为能力不可用
            logger.warning(
                "外部规则 runtime %s 宣称顺序动作能力但探针失败，能力位已剥离: %s",
                adapter.runtime_id, exc,
            )
            supported = False
        if not supported:
            adapter.capabilities = _dataclass_replace(
                adapter.capabilities, sequential_checks=False, composite_action=False,
            )

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
        # 指纹必须覆盖**实际安装的工件内容**：原地覆盖代码而 manifest 关键字段
        # 不变时，纯声明式指纹会复用过期的子进程（P1-02）。
        payload = json.dumps(
            {
                "plugin_id": plugin_id,
                "declaration": declaration,
                "entrypoint": list((getattr(runtime, "manifest", {}) or {}).get("entrypoint") or []),
                "plugin_dir": str(getattr(getattr(runtime, "directory", None), "resolve", lambda: "")() or ""),
                "artifacts": self._artifact_digest(getattr(runtime, "directory", None)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _artifact_digest(directory: Any) -> str:
        """内容指纹：插件目录内全部普通文件的 (相对路径, sha256)。读不到就说读不到。"""
        if directory is None:
            return "unavailable"
        parts: list[str] = []
        try:
            resolved = Path(directory).resolve()
            for path in sorted(resolved.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(65536), b""):
                        digest.update(chunk)
                parts.append(f"{path.relative_to(resolved).as_posix()}:{digest.hexdigest()}")
        except OSError as exc:
            logger.warning("外部规则 runtime 工件指纹读取失败（沿用可用性现状）: %s", exc)
            return "unavailable"
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

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
