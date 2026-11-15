"""T3：第三方 ruleset-runtime 插件 → RulesetRuntimeRegistry 全链路回归。

按 docs/ENGINEERING_RULES.md §15.1，覆盖两个高风险契约：

- Plugin / imported content / path safety：安装校验 fail closed、注册/注销同步、
  与内置 runtime 的重名保护；
- Persisted data / save：外部 runtime 的版本化状态桥 + 重启后存档恢复闭环。

所有用例都走**真实安装**（zip → PluginHost.install_from_zip）与**真实 stdio
子进程**（RuntimeProbe 协议 JSON-lines server），不用进程内假 transport 冒充。
"""

from __future__ import annotations

import asyncio
import json
import random
import secrets
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


def _runtime_server_script(*, broken: bool = False, slow_resolve: float = 0.0,
                           sequential_ops: bool = True, forge_history: bool = False) -> str:
    """A minimal RuntimeProbe JSON-lines server shipped inside the package.

    stateless：所有以 payload 提供的 instance 状态为准，子进程不持有跨请求
    状态——这正是重启恢复依赖的运行时契约。broken=True 时进程立刻退出，
    用于探活 fail closed 用例。slow_resolve>0 时 resolve_intent 故意睡眠，
    用于验证慢子进程不阻塞宿主事件循环（P1-04）。
    """

    if broken:
        return "import sys\nsys.exit(3)\n"
    slow_prefix = (
        f"import time\nSLOW_RESOLVE = {slow_resolve!r}\nSEQ_OPS = {sequential_ops!r}\n"
        f"FORGE_HISTORY = {forge_history!r}\n\n"
    )
    return slow_prefix + """
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
    if SEQ_OPS and op == "validate_sequential_plan":
        plan = payload.get("plan") or {}
        return {"ok": True, "intent_id": plan.get("intent_id"), "validated": True,
                "steps": plan.get("steps") or []}
    if SEQ_OPS and op == "resolve_sequential_step":
        step = payload.get("step") or {}
        instance = payload.get("instance") or {}
        state = (instance.get("modules") or {}).get("ruleset_runtime", {}).get("state") or {}
        version = int(state.get("version", 0))
        return {"ok": True, "event_batch": {
            "batch_id": "t3-step-%s-%d" % (step.get("step_id"), version + 1),
            "intent_id": step.get("intent_id"),
            "expected_version": version,
            "result_version": version + 1,
            "events": [{"type": "t3.step_resolved", "step_id": step.get("step_id")}],
        }}
    if op == "resolve_intent":
        if SLOW_RESOLVE:
            time.sleep(SLOW_RESOLVE)
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
        out_ledger = [*ledger, batch]
        # 仅当存在既有历史时改写：保证第一次 apply 干净落账，
        # 第二次 apply（历史非空）才触发"内容改写"的篡改场景。
        if FORGE_HISTORY and ledger:
            out_ledger = [{**out_ledger[0], "events": [{"type": "forged"}]}, *out_ledger[1:]]
        return {"ok": True, "applied": True,
                "state": new_state, "event_ledger": out_ledger}
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
                           runtime_id: str = RUNTIME_ID, entrypoint: list[str] | None = None,
                           slow_resolve: float = 0.0, rule_id: str = "t3sample",
                           sequential_checks: bool = False,
                           sequential_ops: bool = True, forge_history: bool = False) -> Path:
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
                **({"sequential_checks": True, "composite_action": True}
                   if sequential_checks else {}),
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
    (runtime_dir / "server.py").write_text(
        _runtime_server_script(broken=broken, slow_resolve=slow_resolve,
                               sequential_ops=sequential_ops, forge_history=forge_history),
        encoding="utf-8")
    rules_dir = package_dir / "rules"
    rules_dir.mkdir(exist_ok=True)
    (rules_dir / f"{rule_id}.json").write_text(json.dumps({
        "rule_id": rule_id,
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
async def test_resolve_intent_accepts_default_serializable_rng(tmp_path):
    """P0 回归（审查 P0-01）：默认服务 RNG 必须可序列化并可过真实 stdio 结算。

    旧默认 random.SystemRandom 的 getstate() 抛 NotImplementedError，外部
    规则的权威动作在送到子进程之前就 500。现默认为每意图 OS 熵种子的
    random.Random：可序列化、跨意图不可预测；同 intent 的重试命中幂等收据。
    """
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    runtime = registry.get(RUNTIME_ID, minimum_version=1)

    instance = {"game_key": ["web", "room-a", "alice"], "run_id": "run-a",
                "modules": {"ruleset_runtime": {"state": {"version": 0}, "event_ledger": []}}}
    intent = {"intent_id": "t3-intent-1", "type": "t3.check"}
    rng = random.Random(secrets.randbits(128))  # 与 ruleset_gameplay._default_rng 同形
    resolved = runtime.resolve_intent(instance, intent, rng)
    assert resolved.get("ok") is True
    assert resolved["event_batch"]["intent_id"] == "t3-intent-1"

    # 同 intent 换一个新 rng 重试：收据身份只含规范化 payload，命中同一收据。
    replayed = runtime.resolve_intent(instance, intent, random.Random(secrets.randbits(128)))
    assert replayed == resolved
    source.close()


@pytest.mark.asyncio
async def test_receipts_are_isolated_per_game_and_run(tmp_path):
    """P1-03 回归：两场游戏用同 intent_id 同 payload，不得互相命中收据。

    request_id 现在携带服务器权威的 (game_key, run_id) 隔离段：游戏 A 的
    收据不能替游戏 B 回答（旧实现会返回 A 的 event_batch）。
    """
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    runtime = registry.get(RUNTIME_ID, minimum_version=1)

    intent = {"intent_id": "t3-shared", "type": "t3.check"}
    game_a = {"game_key": ["web", "room-a", "alice"], "run_id": "run_aaa",
              "modules": {"ruleset_runtime": {"state": {"version": 0}, "event_ledger": []}}}
    game_b = {"game_key": ["web", "room-b", "bob"], "run_id": "run_bbb",
              "modules": {"ruleset_runtime": {"state": {"version": 4}, "event_ledger": []}}}

    resolved_a = runtime.resolve_intent(game_a, dict(intent), random.Random(secrets.randbits(128)))
    resolved_b = runtime.resolve_intent(game_b, dict(intent), random.Random(secrets.randbits(128)))
    # B 按自己的状态版本结算（t3-batch-5），而不是复用 A 的 t3-batch-1。
    assert resolved_a["event_batch"]["batch_id"] == "t3-batch-1"
    assert resolved_b["event_batch"]["batch_id"] == "t3-batch-5"
    source.close()


@pytest.mark.asyncio
async def test_prepare_receipts_are_isolated_per_game(tmp_path):
    """P1-03 回归：prepare 钩子的收据同样按 (game_key, run_id) 隔离。"""
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    runtime = registry.get(RUNTIME_ID, minimum_version=1)

    intent = {"intent_id": "t3-prepare-1", "type": "t3.check"}
    game_a = {"game_key": ["web", "room-a", "alice"], "run_id": "run_aaa"}
    game_b = {"game_key": ["web", "room-b", "bob"], "run_id": "run_bbb"}
    prepared_a = runtime.prepare_intent_submission(dict(intent), "alice", False, game_a)
    prepared_b = runtime.prepare_intent_submission(dict(intent), "bob", False, game_b)
    # fixture 的 prepare 返回 intent 本身；隔离意味着两条独立收据，互不覆盖。
    assert prepared_a["intent_id"] == "t3-prepare-1"
    assert prepared_b["intent_id"] == "t3-prepare-1"
    source.close()


@pytest.mark.asyncio
async def test_declared_sequential_capability_survives_and_serves(tmp_path):
    """P1-01：真实提供顺序动作协议的 runtime，宣称的能力位注册后保留并可用。"""
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack", sequential_checks=True)
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    runtime = registry.get(RUNTIME_ID, minimum_version=1)
    assert runtime.capabilities.sequential_checks is True
    assert runtime.capabilities.composite_action is True

    validated = runtime.validate_sequential_plan(
        {"game_key": ["web", "room-a", "alice"], "run_id": "run-a"},
        {"intent_id": "seq-1", "steps": [{"step_id": "s1"}]}, "p1", False,
    )
    assert validated.get("validated") is True
    resolved = runtime.resolve_sequential_step(
        {"game_key": ["web", "room-a", "alice"], "run_id": "run-a"},
        {"step_id": "s1", "intent_id": "seq-1"}, random.Random(secrets.randbits(128)),
    )
    assert resolved["event_batch"]["intent_id"] == "seq-1"
    source.close()


@pytest.mark.asyncio
async def test_unimplemented_sequential_capability_is_stripped_at_registration(tmp_path):
    """P1-01：宣称顺序动作能力但子进程没有协议操作 → 注册时剥离能力位。"""
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack", sequential_checks=True,
                                     sequential_ops=False)
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    report = source.sync(host)
    # runtime 本身可用（registered），但能力位被剥离：UI/服务端不再按多技能执行。
    assert report["registered"] == [RUNTIME_ID]
    assert report["failed"] == {}
    capabilities = registry.get(RUNTIME_ID, minimum_version=1).capabilities
    assert capabilities.sequential_checks is False
    assert capabilities.composite_action is False
    source.close()


@pytest.mark.asyncio
async def test_slow_remote_resolve_keeps_event_loop_responsive(tmp_path):
    """P1-04 回归：慢的外部 runtime 不拖住宿主事件循环。

    服务层把外部 adapter 的同步 RPC 卸到工作线程（asyncio.to_thread）。
    这里用真实子进程（resolve 睡 1.5s）证明：结算进行期间事件循环仍以
    ~10ms 的节奏持续调度其它协程（ticks 计数远大于 0）；若有人把 RPC
    拉回事件循环上执行，ticks 会跌到接近 0，测试失败。
    """
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack", slow_resolve=1.5)
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    runtime = registry.get(RUNTIME_ID, minimum_version=1)
    instance = {"game_key": ["web", "room-slow", "alice"], "run_id": "run-slow",
                "modules": {"ruleset_runtime": {"state": {"version": 0}, "event_ledger": []}}}
    intent = {"intent_id": "t3-slow-1", "type": "t3.check"}

    slow_task = asyncio.create_task(asyncio.to_thread(
        runtime.resolve_intent, instance, intent, random.Random(secrets.randbits(128)),
    ))
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while not slow_task.done():
            ticks += 1
            await asyncio.sleep(0.01)

    await asyncio.wait_for(tick(), timeout=10)
    resolved = await asyncio.wait_for(slow_task, timeout=10)
    assert resolved.get("ok") is True
    # 1.5s 里按 10ms 心跳应累计 ~150 次；被阻塞的话接近 0。留出 CI 抖动余量。
    assert ticks >= 30, ticks
    source.close()


def _installed_runtime_dir(plugins: Path, plugin_id: str = "t3-pack") -> Path:
    return plugins / plugin_id / "runtime"


@pytest.mark.asyncio
async def test_overwrite_update_swaps_adapter_and_closes_old_process(tmp_path):
    """P1-02 回归：原地覆盖代码后必须换新子进程，且不允许重复 ID 注册失败。"""
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    assert source.sync(host)["registered"] == [RUNTIME_ID]

    # 原地覆盖：manifest 关键字段不变，只改服务器行为（describe 名称带 v2）。
    server = _installed_runtime_dir(plugins) / "server.py"
    server.write_text(_runtime_server_script().replace("T3 样例规则", "T3 样例规则 v2"), encoding="utf-8")

    report = source.sync(host)
    assert report["failed"] == {}
    assert report["registered"] == [RUNTIME_ID]
    # 注册表服务的是新工件：describe 名称带 v2（旧子进程已关闭并被替换）。
    served = registry.get(RUNTIME_ID, minimum_version=1).describe_experience({}, "zh-CN")
    assert served["name"] == "T3 样例规则 v2"
    source.close()


@pytest.mark.asyncio
async def test_probe_failure_keeps_previous_runtime_registered(tmp_path):
    """P1-02 回归：新版本探活失败时，旧可用实现保持注册（不得留下已关闭 adapter）。"""
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    assert source.sync(host)["registered"] == [RUNTIME_ID]

    # 覆盖成启动即退出的坏实现（manifest 不变 → 走更新路径）。
    (_installed_runtime_dir(plugins) / "server.py").write_text(
        _runtime_server_script(broken=True), encoding="utf-8")

    report = source.sync(host)
    assert report["registered"] == []
    assert RUNTIME_ID in report["failed"]
    # 旧实现仍然可用：resolve 探针照常工作，不会拿到已关闭的子进程。
    served = registry.get(RUNTIME_ID, minimum_version=1).describe_experience({}, "zh-CN")
    assert served["name"] == "T3 样例规则"
    source.close()


@pytest.mark.asyncio
async def test_ambiguous_runtime_id_rejected_across_plugins(tmp_path):
    """P1-02 回归：多个启用插件声明同一 runtime_id 时按歧义拒绝，不靠遍历顺序。"""
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    await host.install_from_zip(_write_runtime_package(tmp_path, "t3-pack").read_bytes())
    await host.install_from_zip(_write_runtime_package(tmp_path, "t3-pack-b", rule_id="t3-pack-b").read_bytes())
    await host.start("t3-pack", require_enabled=False)
    await host.start("t3-pack-b", require_enabled=False)

    report = source.sync(host)
    assert RUNTIME_ID not in report["registered"]
    assert "runtime_id 被多个启用的插件声明" in report["failed"][RUNTIME_ID]
    with pytest.raises(UnknownRulesetRuntimeError):
        registry.get(RUNTIME_ID)
    source.close()


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


@pytest.mark.asyncio
async def test_real_gameinstance_receipts_isolated_across_games_and_restarts(tmp_path):
    """复审 P1-01：真实 GameInstance 生产入口 + 跨重启恢复。

    - 两局真实聚合用同 intent_id：各按自己的状态版本结算，不串收据；
    - 同局同 intent 重试命中收据（幂等）；
    - 关闭并重建同步桥（模拟重启）后，同局重试经由持久账本恢复同一结果。
    """
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    runtime = registry.get(RUNTIME_ID, minimum_version=1)

    game_a = GameInstance(game_key=("web", "room-a", "alice"), world_id="w",
                          rule_id="r", gm_uid="gm", language="en")
    game_b = GameInstance(game_key=("web", "room-b", "bob"), world_id="w",
                          rule_id="r", gm_uid="gm", language="en")
    for game in (game_a, game_b):
        game.modules["ruleset_runtime"] = ruleset_runtime.fresh()
        ruleset_runtime.replace_binding(game, {"id": RUNTIME_ID, "runtime_version": 1})
    # A 推进一个版本：同 intent 在 A 上结算 batch-2；B（v0）应是 batch-1，
    # 若收据按旧实现落到共享 unknown 桶，B 会错误拿到 A 的 batch-2。
    ruleset_runtime.replace_state(game_a, {"version": 1})

    intent = {"intent_id": "shared-intent", "type": "t3.check"}
    resolved_a = runtime.resolve_intent(game_a, dict(intent), random.Random(secrets.randbits(128)))
    assert resolved_a["event_batch"]["batch_id"] == "t3-batch-2"

    applied = runtime.apply_event_batch(game_a, resolved_a["event_batch"])
    assert applied.get("applied") is True

    # 重启：关闭同步桥并重建（同一持久账本路径）。
    source.close()
    registry_restart = RulesetRuntimeRegistry([])
    source_restart = _make_source(registry_restart, host)
    source_restart.sync(host)
    restarted = registry_restart.get(RUNTIME_ID, minimum_version=1)
    replayed = restarted.resolve_intent(game_a, dict(intent), random.Random(secrets.randbits(128)))
    assert replayed == resolved_a  # 同局重试跨重启命中持久收据

    resolved_b = restarted.resolve_intent(game_b, dict(intent), random.Random(secrets.randbits(128)))
    assert resolved_b["event_batch"]["batch_id"] == "t3-batch-1"  # B 按 v0 结算，没撞 A 的收据
    source_restart.close()


@pytest.mark.asyncio
async def test_ledger_history_tampering_is_rejected(tmp_path):
    """复审 P1-02：子进程回写的历史行 ID 不变但内容被改写 → 拒绝且不写入状态。"""
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack", forge_history=True)
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    runtime = registry.get(RUNTIME_ID, minimum_version=1)

    game = GameInstance(game_key=("web", "room-a", "alice"), world_id="w",
                        rule_id="r", gm_uid="gm", language="en")
    game.modules["ruleset_runtime"] = ruleset_runtime.fresh()
    ruleset_runtime.replace_binding(game, {"id": RUNTIME_ID, "runtime_version": 1})
    resolved = runtime.resolve_intent(
        game, {"intent_id": "tamper-1", "type": "t3.check"}, random.Random(secrets.randbits(128)),
    )
    applied = runtime.apply_event_batch(game, resolved["event_batch"])
    assert applied.get("applied") is True
    assert len(ruleset_runtime.event_ledger(game)) == 1

    resolved_next = runtime.resolve_intent(
        game, {"intent_id": "tamper-2", "type": "t3.check"}, random.Random(secrets.randbits(128)),
    )
    from src.rulesets.runtime_probe import RuntimeProbeError

    with pytest.raises(RuntimeProbeError, match="rewrote persisted history"):
        runtime.apply_event_batch(game, resolved_next["event_batch"])
    # 拒绝时状态与账本都保持原样：不写入追加行。
    assert len(ruleset_runtime.event_ledger(game)) == 1
    assert ruleset_runtime.state(game).get("version") == 1
    source.close()


@pytest.mark.asyncio
async def test_sequential_step_receipts_scoped_by_plan(tmp_path):
    """复审 P1-01/TEST-01：同一局、同一 step_id、两个不同计划必须独立结算。

    收据身份 = 游戏/Run + 计划 intent_id + step_id。两个计划都在同一
    GameInstance 上提交 step-1：若收据只按 step_id（旧实现）第二次会
    重放第一次的 batch 或直接冲突；plan_id 生效时第二次按推进后的版本
    独立结算。同计划重试、跨重启恢复与跨游戏隔离同时覆盖。
    """
    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    package = _write_runtime_package(tmp_path, "t3-pack")
    await host.install_from_zip(package.read_bytes())
    await host.start("t3-pack", require_enabled=False)
    runtime = registry.get(RUNTIME_ID, minimum_version=1)

    game = GameInstance(game_key=("web", "room-a", "alice"), world_id="w",
                        rule_id="r", gm_uid="gm", language="en")
    other_game = GameInstance(game_key=("web", "room-b", "bob"), world_id="w",
                              rule_id="r", gm_uid="gm", language="en")
    for item in (game, other_game):
        item.modules["ruleset_runtime"] = ruleset_runtime.fresh()
        ruleset_runtime.replace_binding(item, {"id": RUNTIME_ID, "runtime_version": 1})
    # 同局从 v1 开始：第一个计划结算出 batch-2。
    ruleset_runtime.replace_state(game, {"version": 1})
    step = {"step_id": "step-1", "order": 1}

    resolved_a = runtime.resolve_sequential_step(
        game, dict(step), random.Random(secrets.randbits(128)), "plan-a",
    )
    assert resolved_a["event_batch"]["batch_id"] == "t3-step-step-1-2"
    applied = runtime.apply_event_batch(game, resolved_a["event_batch"])
    assert applied.get("applied") is True  # 版本推进到 2

    # 同局第二个计划、同样 step-1：独立结算（version 2 → batch-3）。
    # 旧实现会在这里复用 plan-a 的收据：相同 payload 重放 batch-2，
    # 不同 payload 则抛 "same receipt id with different payload"。
    resolved_b = runtime.resolve_sequential_step(
        game, dict(step), random.Random(secrets.randbits(128)), "plan-b",
    )
    assert resolved_b["event_batch"]["batch_id"] == "t3-step-step-1-3"

    # 两个计划的各自重试：分别命中自己的收据，互不污染。
    retry_a = runtime.resolve_sequential_step(
        game, dict(step), random.Random(secrets.randbits(128)), "plan-a",
    )
    retry_b = runtime.resolve_sequential_step(
        game, dict(step), random.Random(secrets.randbits(128)), "plan-b",
    )
    assert retry_a == resolved_a
    assert retry_b == resolved_b

    # 重启：关闭并重建同步桥（同一持久账本），两个计划的收据都仍可恢复。
    source.close()
    registry_restart = RulesetRuntimeRegistry([])
    source_restart = _make_source(registry_restart, host)
    source_restart.sync(host)
    restarted = registry_restart.get(RUNTIME_ID, minimum_version=1)
    assert restarted.resolve_sequential_step(
        game, dict(step), random.Random(secrets.randbits(128)), "plan-a",
    ) == resolved_a
    assert restarted.resolve_sequential_step(
        game, dict(step), random.Random(secrets.randbits(128)), "plan-b",
    ) == resolved_b
    # 跨游戏使用相同计划/步骤 ID：按游戏隔离，各自按自己的版本结算。
    cross_game = restarted.resolve_sequential_step(
        other_game, dict(step), random.Random(secrets.randbits(128)), "plan-a",
    )
    assert cross_game["event_batch"]["batch_id"] == "t3-step-step-1-1"
    source_restart.close()
