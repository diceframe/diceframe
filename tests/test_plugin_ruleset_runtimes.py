"""T3：第三方 ruleset-runtime 插件 → RulesetRuntimeRegistry 全链路回归。

按 docs/ENGINEERING_RULES.md §15.1，覆盖两个高风险契约：

- Plugin / imported content / path safety：安装校验 fail closed、注册/注销同步、
  与内置 runtime 的重名保护；
- Persisted data / save：外部 runtime 的版本化状态桥 + 重启后存档恢复闭环。

所有用例都走**真实安装**（zip → PluginHost.install_from_zip）与**真实 stdio
子进程**（RuntimeProbe 协议 JSON-lines server），不用进程内假 transport 冒充。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.plugin_host.host import PluginHost
from src.plugin_host.requirements import parse_ruleset_runtime_declaration
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.plugin_runtime_source import PluginRulesetRuntimeSource
from src.rulesets.registry import (
    RulesetRuntimeRegistry,
    UnknownRulesetRuntimeError,
)

RUNTIME_ID = "external:t3-sample"


# ---------------------------------------------------------------- fixtures


def _runtime_server_script(*, broken: bool = False) -> str:
    """A minimal RuntimeProbe JSON-lines server shipped inside the package.

    stateless：所有以 payload 提供的 instance 状态为准，子进程不持有跨请求
    状态——这正是重启恢复依赖的运行时契约。broken=True 时进程立刻退出，
    用于探活 fail closed 用例。
    """

    if broken:
        return "import sys\nsys.exit(3)\n"
    return """
import json
import sys


def handle(op, payload):
    if op == "describe_experience":
        return {"id": "external:t3-sample", "name": "T3 样例规则", "locale": payload.get("locale", "")}
    if op == "builder_choices":
        return {"fields": [
            {"id": "name", "type": "string", "label": "姓名"},
            {"id": "attributes", "type": "object",
             "label": "属性骰", "options": [6, 8, 10, 12]},
        ]}
    if op == "validate_character":
        draft = payload.get("draft") or {}
        errors = []
        if not str(draft.get("name") or "").strip():
            errors.append("需要姓名")
        return errors
    if op == "derive_character":
        draft = payload.get("draft") or {}
        attrs = (draft.get("attributes") or {})
        mig = int(attrs.get("mig", 8))
        wlp = int(attrs.get("wlp", 8))
        level = 5
        # FU 基础公式（T2 修复后语义）：总等级 + 骰面 × 5。
        hp = level + mig * 5
        mp = level + wlp * 5
        return {
            "name": draft.get("name") or "", "level": level,
            "attributes": dict(attrs),
            "hp": hp, "max_hp": hp, "mp": mp, "max_mp": mp, "fabula": 3,
        }
    if op == "finalize_character":
        return {**handle("derive_character", payload), "finalized": True}
    if op == "normalize_character_submission":
        return payload.get("character") or {}
    if op == "available_intents":
        return [{"type": "t3.check", "label": "检定"}]
    if op == "prepare_intent_submission":
        return payload.get("intent") or {}
    if op == "validate_intent":
        return {"ok": True}
    if op == "resolve_intent":
        instance = payload.get("instance") or {}
        state = (instance.get("modules") or {}).get("ruleset_runtime", {}).get("state") or {}
        version = int(state.get("version", 0))
        intent = payload.get("intent") or {}
        return {"ok": True, "event_batch": {
            "batch_id": "t3-batch-%d" % (version + 1),
            "intent_id": intent.get("intent_id"),
            "expected_version": version,
            "result_version": version + 1,
            "events": [{"type": "t3.check_resolved", "version": version + 1}],
        }}
    if op == "apply_event_batch":
        instance = payload.get("instance") or {}
        batch = payload.get("batch") or {}
        module = (instance.get("modules") or {}).get("ruleset_runtime", {})
        state = module.get("state") or {}
        ledger = module.get("event_ledger") or []
        if any(row.get("batch_id") == batch.get("batch_id") for row in ledger):
            return {"ok": True, "applied": False, "replayed": True,
                    "state": state, "event_ledger": ledger}
        new_state = {**state, "version": batch.get("result_version", state.get("version", 0))}
        return {"ok": True, "applied": True,
                "state": new_state, "event_ledger": [*ledger, batch]}
    if op == "memory_deltas_from_event_batch":
        return []
    if op == "gameplay_view":
        instance = payload.get("instance") or {}
        state = (instance.get("modules") or {}).get("ruleset_runtime", {}).get("state") or {}
        view = {"public": {"version": state.get("version", 0)}}
        if payload.get("viewer_is_gm"):
            view["private"] = "gm-only"
        return view
    if op == "build_llm_view":
        return {"summary": "t3 sample state"}
    if op == "project_legacy_character":
        return payload.get("character") or {}
    if op == "migrate_state":
        return payload.get("payload") or {}
    raise ValueError("unsupported operation: %s" % op)


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except Exception:
            continue
        try:
            result = handle(str(request.get("operation")), request.get("payload") or {})
            response = {"request_id": request.get("request_id"), "ok": True, "result": result}
        except Exception as exc:
            response = {"request_id": request.get("request_id"), "ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
"""


def _write_runtime_package(root: Path, plugin_id: str, *, broken: bool = False,
                           runtime_id: str = RUNTIME_ID, entrypoint: list[str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    package_dir = root / plugin_id
    package_dir.mkdir(exist_ok=True)
    manifest = {
        "schema_version": 1,
        "id": plugin_id,
        "name": "T3 runtime pack",
        "version": "1",
        "description": "external ruleset runtime acceptance package",
        "plugin_type": "ruleset-runtime",
        "entrypoint": entrypoint or ["{python}", "runtime/server.py"],
        "permissions": ["ruleset.execute"],
        "ruleset_runtime": {
            "runtime_id": runtime_id,
            "runtime_version": 1,
            "capabilities": {
                "character_builder": "professional",
                "authoritative_intents": True,
                "versioned_state": True,
            },
            "timeout_seconds": 10,
        },
        "contributes": {"rules": ["rules/*.json"]},
        "config_schema": "config.schema.json",
    }
    (package_dir / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (package_dir / "config.schema.json").write_text(json.dumps({
        "type": "object",
        "properties": {"enabled": {"type": "boolean", "default": False, "ui": {"control": "switch"}}},
    }), encoding="utf-8")
    runtime_dir = package_dir / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    (runtime_dir / "server.py").write_text(_runtime_server_script(broken=broken), encoding="utf-8")
    rules_dir = package_dir / "rules"
    rules_dir.mkdir(exist_ok=True)
    (rules_dir / "t3sample.json").write_text(json.dumps({
        "rule_id": "t3sample",
        "rule_name": "T3 样例规则",
        "description": "rights-clean sample",
        "runtime": {"id": runtime_id, "minimum_version": 1},
        "dice_system": "d20",
        "combat_model": "hp_based",
        "attributes": [{"key": "mig", "name": "力量"}, {"key": "wlp", "name": "意志"}],
        "attribute_points": 32,
    }, ensure_ascii=False), encoding="utf-8")
    zip_path = root / f"{plugin_id}.dfplugin"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in package_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(root))
    return zip_path


def _make_source(registry: RulesetRuntimeRegistry, host: PluginHost) -> PluginRulesetRuntimeSource:
    """构建同步桥并挂上宿主生命周期通知（与组合根 attach 同一语义）。"""

    source = PluginRulesetRuntimeSource(registry)
    host.set_ruleset_runtime_listener(lambda: source.sync(host))
    return source


# ---------------------------------------------------------------- 单元契约


def test_registry_unregister_is_idempotent():
    registry = RulesetRuntimeRegistry([])
    assert registry.unregister("missing") is False
    assert registry.unregister("") is False


def test_ruleset_runtime_declaration_parser_fails_closed():
    good = parse_ruleset_runtime_declaration({
        "ruleset_runtime": {"runtime_id": RUNTIME_ID, "runtime_version": 2,
                            "capabilities": {"authoritative_intents": True}, "timeout_seconds": 8},
    })
    assert good["runtime_id"] == RUNTIME_ID and good["runtime_version"] == 2
    assert good["timeout_seconds"] == 8.0
    with pytest.raises(ValueError, match="必须提供 ruleset_runtime"):
        parse_ruleset_runtime_declaration({})
    with pytest.raises(ValueError, match="unknown field"):
        parse_ruleset_runtime_declaration({"ruleset_runtime": {"runtime_id": "x", "command": "rm"}})
    with pytest.raises(ValueError, match="runtime_id"):
        parse_ruleset_runtime_declaration({"ruleset_runtime": {"runtime_id": "  "}})
    with pytest.raises(ValueError, match="runtime_version"):
        parse_ruleset_runtime_declaration({"ruleset_runtime": {"runtime_id": "x", "runtime_version": 0}})
    with pytest.raises(ValueError, match="timeout_seconds"):
        parse_ruleset_runtime_declaration({"ruleset_runtime": {"runtime_id": "x", "timeout_seconds": 99}})


# ---------------------------------------------------------------- 安装/注册闭环


@pytest.mark.asyncio
async def test_install_registers_runtime_and_serves_character_flow(tmp_path):
    """真实 zip 安装 → 启用 → stdio 探活 → 注册 → 建卡走外部 runtime。"""

    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)

    package = _write_runtime_package(tmp_path, "t3-pack")
    detail = await host.install_from_zip(package.read_bytes())
    assert detail["id"] == "t3-pack"
    # 新装默认未启用：不注册（fail closed）。
    with pytest.raises(UnknownRulesetRuntimeError):
        registry.get(RUNTIME_ID)
    await host.start("t3-pack", require_enabled=False)
    # 启用触发宿主通知 → 同步桥注册（真实 stdio 子进程探活通过）。
    runtime = registry.get(RUNTIME_ID, minimum_version=1)
    assert runtime.runtime_id == RUNTIME_ID

    sheet = runtime.finalize_character({}, {
        "name": "英雄", "attributes": {"mig": 10, "wlp": 8},
    })
    assert sheet["max_hp"] == 55 and sheet["max_mp"] == 45  # 5 + 10*5 / 5 + 8*5
    assert sheet["finalized"] is True

    source.close()
    with pytest.raises(UnknownRulesetRuntimeError):
        registry.get(RUNTIME_ID)


@pytest.mark.asyncio
async def test_disabled_runtime_is_not_registered(tmp_path):
    """未启用的 ruleset-runtime 插件不得注册（fail closed）。"""

    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    report = source.sync(host)
    assert report["registered"] == []  # 安装后默认未启用
    with pytest.raises(UnknownRulesetRuntimeError):
        registry.get(RUNTIME_ID)

    await host.start("t3-pack", require_enabled=False)  # UI 语义：启动即启用，进程归注册表
    report = source.sync(host)
    assert report["registered"] == [RUNTIME_ID]
    assert host.public_detail("t3-pack")["status"] == "active"

    await host.stop("t3-pack")  # 停用 → 宿主通知已撤销注册（幂等）
    report = source.sync(host)
    assert report["registered"] == []  # 重复同步保持收敛
    with pytest.raises(UnknownRulesetRuntimeError):
        registry.get(RUNTIME_ID)


@pytest.mark.asyncio
async def test_uninstall_revokes_runtime_registration(tmp_path):
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    assert registry.get(RUNTIME_ID).runtime_id == RUNTIME_ID

    await host.uninstall("t3-pack")  # 宿主通知已撤销注册
    with pytest.raises(UnknownRulesetRuntimeError):
        registry.get(RUNTIME_ID)
    report = source.sync(host)
    assert report["registered"] == []
    assert not (plugins / "t3-pack").exists()


@pytest.mark.asyncio
async def test_broken_runtime_fails_probe_but_install_succeeds(tmp_path):
    """探活失败 → 不注册（fail closed），安装本身不被阻断。"""

    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "broken-pack", broken=True)
    result = await host.install_from_zip(package.read_bytes())
    assert result["id"] == "broken-pack"  # 安装成功：runtime 可用性不阻断安装事务
    await host.start("broken-pack", require_enabled=False)  # 启用 → 通知 → 同步桥探活
    report = source.sync(host)
    assert report["registered"] == []
    assert "external:t3-sample" in report["failed"]
    with pytest.raises(UnknownRulesetRuntimeError):
        registry.get(RUNTIME_ID)


@pytest.mark.asyncio
async def test_builtin_runtime_id_cannot_be_shadowed_by_plugin(tmp_path):
    """与已注册 runtime 重名 → 注册失败，内置实现保持原样。"""

    class BuiltinLike:
        runtime_id = RUNTIME_ID
        runtime_version = 99
        capabilities = RulesetCapabilities()

    registry = RulesetRuntimeRegistry([BuiltinLike()])
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    report = source.sync(host)
    assert report["registered"] == []
    assert registry.get(RUNTIME_ID).runtime_version == 99  # 内置未被替换


@pytest.mark.asyncio
async def test_install_rejects_ruleset_runtime_without_declaration(tmp_path):
    """缺 ruleset_runtime 声明的包安装即被拒（安装期 fail closed）。"""

    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    package = _write_runtime_package(tmp_path, "bad-pack")
    # 改写 zip 内 manifest：去掉 ruleset_runtime 声明。
    stripped = tmp_path / "stripped.dfplugin"
    with zipfile.ZipFile(package) as src, zipfile.ZipFile(stripped, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.endswith("plugin.json"):
                manifest = json.loads(data)
                manifest.pop("ruleset_runtime")
                data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            dst.writestr(info, data)
    with pytest.raises(ValueError, match="ruleset_runtime"):
        await host.install_from_zip(stripped.read_bytes())


# ---------------------------------------------------------------- 权威结算 + 存档恢复


@pytest.mark.asyncio
async def test_authoritative_intent_and_restart_recovery(tmp_path):
    """端到端：安装 → 建卡 → 权威结算（状态桥）→ 重启恢复。"""

    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)

    rule_template = {"runtime": {"id": RUNTIME_ID, "minimum_version": 1}}
    runtime = registry.resolve(rule_template)  # 版本绑定解析（fail closed 语义）
    metadata = registry.describe(rule_template)
    assert metadata.runtime_id == RUNTIME_ID and metadata.requested_minimum_version == 1

    instance = GameInstance(game_key=("test", "t3", "sample"), run_id="run-1")
    ruleset_runtime.bind(instance, {
        "id": RUNTIME_ID, "version": 1, "rule_id": "t3sample",
        "content_version": "1", "state_schema_version": 1,
    })
    sheet = runtime.finalize_character({}, {"name": "英雄", "attributes": {"mig": 10, "wlp": 8}})

    rng = type("Rng", (), {"getstate": lambda self: ("s", 1)})()
    settled = runtime.resolve_intent(instance, {"intent_id": "intent-1", "type": "t3.check"}, rng)
    batch = settled["event_batch"]
    assert batch["expected_version"] == 0 and batch["result_version"] == 1
    applied = runtime.apply_event_batch(instance, batch)
    assert applied["applied"] is True
    assert ruleset_runtime.state(instance)["version"] == 1

    # ---- 模拟重启：全新 host/registry/子进程，注入持久化的角色卡与规则状态。
    saved_state = json.loads(json.dumps(ruleset_runtime.state(instance)))
    saved_ledger = json.loads(json.dumps(ruleset_runtime.event_ledger(instance)))
    saved_sheet = json.loads(json.dumps(sheet))
    source.close()

    restarted_host = PluginHost(plugins, data_dir)
    restarted_host.discover()  # 恢复路径：安装收据/启用状态原样恢复
    await restarted_host.start("t3-pack")
    restarted_registry = RulesetRuntimeRegistry([])
    restarted_source = _make_source(restarted_registry, restarted_host)
    report = restarted_source.sync(restarted_host)
    assert report["registered"] == [RUNTIME_ID]

    restarted_instance = GameInstance(game_key=("test", "t3", "sample"), run_id="run-1")
    ruleset_runtime.bind(restarted_instance, {
        "id": RUNTIME_ID, "version": 1, "rule_id": "t3sample",
        "content_version": "1", "state_schema_version": 1,
    })
    ruleset_runtime.replace_state(restarted_instance, saved_state)
    ruleset_runtime.replace_event_ledger(restarted_instance, saved_ledger)

    runtime2 = restarted_registry.resolve(rule_template)
    view = runtime2.gameplay_view(restarted_instance, "player", False)
    assert view["public"]["version"] == 1  # 恢复后的版本化状态可见
    assert "private" not in view  # 玩家投影不泄露 GM 私有视图

    continued = runtime2.resolve_intent(
        restarted_instance, {"intent_id": "intent-2", "type": "t3.check"}, rng,
    )
    assert continued["event_batch"]["expected_version"] == 1
    reapplied = runtime2.apply_event_batch(restarted_instance, continued["event_batch"])
    assert reapplied["applied"] is True
    assert ruleset_runtime.state(restarted_instance)["version"] == 2

    # 停电重放保护：确定性 request_id（batch:<id>）命中持久化收据缓存，
    # 逐字返回首次响应、不二次执行——若真重执行，服务端 ledger 判重会
    # 返回 replayed=True/applied=False，与首次响应不同。
    replayed = runtime2.apply_event_batch(restarted_instance, continued["event_batch"])
    assert replayed == reapplied
    assert ruleset_runtime.state(restarted_instance)["version"] == 2
    restarted_source.close()
