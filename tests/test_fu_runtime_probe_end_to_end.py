"""FU 形态的外部规则 runtime 端到端验收（探针包，非 FU 本体）。

目的不是验证 FU 规则正确，而是验证**平台接缝**能承载一个 FU 形态的第三方
runtime。探针包刻意照抄 FU 的真实形状：

- ``ruleset_runtime.capabilities`` 同时声明 ``versioned_state`` 与
  ``authoritative_intents``（FU ``runtime.py`` 的声明）；
- 规则模板用 ``runtime: {id: "fu:core"}`` 绑定，而不是把规则并进核心；
- ``dice_system`` 只能是 ``none``——核心白名单仅 d20/d100/none，FU 的双属性
  骰必须由 runtime 在 ``resolve_intent`` 内自行裁决（这就是"骰制接缝"缺口
  在插件侧的绕行形态）；
- 属性为 FU 的灵巧/洞察/力量/意志，HP/MP 用 FU 公式（等级 + 骰面 × 5）。

覆盖两条此前缺失的协议义务：

- ``game_binding``：缺它时 ``bind_ruleset_runtime`` 抛 ``RULESET_BINDING_FAILED``
  并回滚，任何 ``versioned_state`` runtime 都建不了局；
- ``next_automatic_intent``：缺它时敌人与 AI 席位静默不动，不报错。

全部走真实 zip 安装 + 真实 stdio 子进程，不用进程内假 transport。
"""

from __future__ import annotations

import json
import random
import secrets
import zipfile
from pathlib import Path

import pytest

from src.commands.dice_resolver import DiceResolver
from src.commands.round_actions import build_dice_constraint_block
from src.engine.game_instance import GameInstance
from src.engine.modules import checks as checks_module
from src.engine.modules import ruleset_runtime
from src.plugin_host.host import PluginHost
from src.rules.rule_system import RuleSystem
from src.rulesets.narrative_checks import resolve_planned_checks, resolver_for
from src.rulesets.plugin_runtime_source import PluginRulesetRuntimeSource
from src.rulesets.registry import RulesetRuntimeRegistry

FU_RUNTIME_ID = "fu:core"
FU_RULE_ID = "fabula_ultima"

# FU 的四个属性：灵巧 / 洞察 / 力量 / 意志。
FU_ATTRIBUTES = ("dex", "ins", "mig", "wlp")


# ---------------------------------------------------------------- 探针包

_FU_SERVER = '''
import json
import sys

RUNTIME_ID = "fu:core"
CONTENT_VERSION = "1.0.5"

# FU 检定：两个属性各掷一个骰面，取和对抗难度。rng 由宿主注入，
# 探针内不得自建随机源（FU 原实现在 _resolve_conflict_reroll 里自建
# random.Random()，插件化后必须改掉）。
DICE_FACES = 8


def _state(payload):
    instance = payload.get("instance") or {}
    module = (instance.get("modules") or {}).get("ruleset_runtime") or {}
    return module.get("state") or {}


def _derive(payload):
    draft = payload.get("draft") or {}
    attrs = dict(draft.get("attributes") or {})
    level = int(draft.get("level") or 5)
    mig = int(attrs.get("mig") or 8)
    wlp = int(attrs.get("wlp") or 8)
    hp = level + mig * 5
    mp = level + wlp * 5
    return {
        "name": draft.get("name") or "",
        "level": level,
        "attributes": attrs,
        "hp": hp, "max_hp": hp,
        "mp": mp, "max_mp": mp,
        "fabula": 3,
    }


def handle(op, payload):
    if op == "describe_experience":
        return {"id": RUNTIME_ID, "name": "Fabula Ultima", "locale": payload.get("locale", "")}
    if op == "game_binding":
        rule = payload.get("rule") or {}
        return {
            "rule_id": rule.get("rule_id") or "",
            "runtime_id": RUNTIME_ID,
            "runtime_version": 1,
            "content_version": CONTENT_VERSION,
            "state_schema_version": 1,
        }
    if op == "builder_choices":
        return {"fields": [
            {"id": "name", "type": "string", "label": "Name"},
            {"id": "attributes", "type": "object", "label": "Attributes",
             "options": list(DICE_FACES for _ in range(1))},
        ]}
    if op == "validate_character":
        draft = payload.get("draft") or {}
        attrs = draft.get("attributes") or {}
        errors = []
        if not str(draft.get("name") or "").strip():
            errors.append("name is required")
        for key in ("dex", "ins", "mig", "wlp"):
            if int(attrs.get(key) or 0) < 6:
                errors.append("attribute %s must be at least d6" % key)
        return errors
    if op == "derive_character":
        return _derive(payload)
    if op == "finalize_character":
        return {**_derive(payload), "finalized": True}
    if op == "normalize_character_submission":
        return payload.get("character") or {}
    if op == "available_intents":
        state = _state(payload)
        phase = state.get("phase") or "exploration"
        intents = [
            {"type": "fu.attribute_check", "label": "Attribute check"},
            {"type": "fu.attack", "label": "Attack"},
        ]
        if phase == "conflict":
            intents.append({"type": "fu.conflict_action", "label": "Conflict action"})
        return intents
    if op == "prepare_intent_submission":
        return payload.get("intent") or {}
    if op == "validate_intent":
        intent = payload.get("intent") or {}
        return {"ok": bool(intent.get("type"))}
    if op == "resolve_narrative_checks":
        # 自由叙事的检定：核心只提出「谁、用哪项属性、多难」，骰子留在包里。
        characters = _state(payload).get("characters") or {}
        results = []
        for request in payload.get("requests") or []:
            attributes = (characters.get(str(request.get("actor_uid") or "")) or {}).get("attributes") or {}
            key_a = str(request.get("attribute") or "")
            faces_a = max(1, min(DICE_FACES, int(attributes.get(key_a, DICE_FACES) or DICE_FACES)))
            others = [int(value or DICE_FACES) for key, value in attributes.items() if key != key_a]
            faces_b = max(1, min(DICE_FACES, max(others or [DICE_FACES])))
            rolls = [min(4, faces_a), min(3, faces_b)]
            total = sum(rolls)
            target = int(request.get("target") or 10)
            results.append({
                "check_id": request.get("check_id"),
                "dice": "d%d+d%d" % (faces_a, faces_b),
                "rolls": rolls,
                "roll": total,
                "total": total,
                "dc": target,
                "verdict": "成功" if total >= target else "失败",
            })
        return results
    if op == "resolve_intent":
        # 假想 FU 检定：双属性 d8 之和 + 等级，对抗固定难度。
        intent = payload.get("intent") or {}
        rng_state = payload.get("rng") or {}
        dice = intent.get("dice") or {}
        a = int(dice.get("primary") or 8)
        b = int(dice.get("secondary") or 8)
        # 探针不消费真实 RNG 序列，只证明宿主注入了可序列化随机源。
        injected = bool(rng_state)
        total = min(a, DICE_FACES) + min(b, DICE_FACES)
        state = _state(payload)
        version = int(state.get("version") or 0)
        return {"ok": True, "event_batch": {
            "batch_id": "fu-batch-%d" % (version + 1),
            "intent_id": intent.get("intent_id"),
            "expected_version": version,
            "result_version": version + 1,
            "events": [{
                "type": "fu.check_resolved",
                "total": total,
                "success": total >= 10,
                "rng_injected": injected,
            }],
        }}
    if op == "apply_event_batch":
        instance = payload.get("instance") or {}
        module = (instance.get("modules") or {}).get("ruleset_runtime") or {}
        state = module.get("state") or {}
        ledger = module.get("event_ledger") or []
        batch = payload.get("batch") or {}
        if any(row.get("batch_id") == batch.get("batch_id") for row in ledger):
            return {"ok": True, "applied": False, "replayed": True,
                    "state": state, "event_ledger": ledger}
        new_state = dict(state)
        new_state["version"] = batch.get("result_version", state.get("version", 0))
        # 结算掉一条敌方行动后，待办队列出队。
        pending = list(new_state.get("pending_enemies") or [])
        if pending:
            pending.pop(0)
        new_state["pending_enemies"] = pending
        return {"ok": True, "applied": True, "state": new_state,
                "event_ledger": [*ledger, batch]}
    if op == "next_automatic_intent":
        state = _state(payload)
        pending = list(state.get("pending_enemies") or [])
        if not pending:
            return None
        actor = pending[0]
        return {
            "intent_id": "auto-%s-%d" % (actor, int(state.get("version") or 0) + 1),
            "type": "fu.attack",
            "actor_id": actor,
            "automatic": True,
        }
    if op == "memory_deltas_from_event_batch":
        return []
    if op == "gameplay_view":
        state = _state(payload)
        pending = list(state.get("pending_enemies") or [])
        version = int(state.get("version") or 0)
        # 插件不能携带 Vue 组件，FU 的面板只能以声明式节点下发。
        panels = [
            {"component": "group", "label": "Clock", "children": [
                {"component": "text", "label": "Doom", "value": "%d / 6" % min(version, 6)},
                {"component": "progress", "label": "Doom", "value": min(version, 6),
                 "min": 0, "max": 6},
            ]},
            {"component": "list", "label": "Opportunities",
             "value": ["A merchant offers a map"]},
        ]
        if pending:
            panels.append({
                "component": "button", "label": "Enemy turn",
                "action": "fu.attack", "arguments": {"actor_id": pending[0]},
            })
        view = {
            "public": {"version": version, "pending_enemies": pending},
            "panels": panels,
        }
        if payload.get("viewer_is_gm"):
            view["private"] = {"notes": "gm-only"}
        return view
    if op == "build_llm_view":
        state = _state(payload)
        return {"summary": "FU state v%s" % state.get("version", 0)}
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
'''


def write_fu_package(root: Path, plugin_id: str = "fu-core") -> Path:
    """Build a FU-shaped ``.dfplugin`` and return its path."""

    root.mkdir(parents=True, exist_ok=True)
    package_dir = root / plugin_id
    (package_dir / "runtime").mkdir(parents=True, exist_ok=True)
    (package_dir / "rules").mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": 1,
        "id": plugin_id,
        "name": "Fabula Ultima runtime",
        "version": "1.0.5",
        "description": "FU-shaped external ruleset runtime (probe package)",
        "plugin_type": "ruleset-runtime",
        "entrypoint": ["{python}", "runtime/server.py"],
        "permissions": ["ruleset.execute"],
        "ruleset_runtime": {
            "runtime_id": FU_RUNTIME_ID,
            "runtime_version": 1,
            "capabilities": {
                "character_builder": "professional",
                "authoritative_intents": True,
                "versioned_state": True,
                "narrative_checks": True,
            },
            "timeout_seconds": 10,
        },
        "contributes": {"rules": ["rules/*.json"]},
        "config_schema": "config.schema.json",
    }
    (package_dir / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (package_dir / "config.schema.json").write_text(json.dumps({
        "type": "object",
        "properties": {"enabled": {"type": "boolean", "default": False,
                                   "ui": {"control": "switch"}}},
    }), encoding="utf-8")
    (package_dir / "runtime" / "server.py").write_text(_FU_SERVER, encoding="utf-8")
    (package_dir / "rules" / f"{FU_RULE_ID}.json").write_text(json.dumps({
        "rule_id": FU_RULE_ID,
        "rule_name": "Fabula Ultima",
        "description": "probe rule template bound to an external runtime",
        "runtime": {"id": FU_RUNTIME_ID, "minimum_version": 1},
        "dice_system": "none",
        "combat_model": "hp_based",
        "attributes": [{"key": key, "name": key.upper()} for key in FU_ATTRIBUTES],
        "attribute_points": 32,
    }, ensure_ascii=False), encoding="utf-8")

    zip_path = root / f"{plugin_id}.dfplugin"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in package_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(root))
    return zip_path


# ---------------------------------------------------------------- 夹具


async def _install_and_enable(tmp_path: Path):
    """Real install → enable → register; returns (host, registry, source)."""

    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = PluginRulesetRuntimeSource(registry)
    host.set_ruleset_runtime_listener(lambda: source.sync(host))

    package = write_fu_package(tmp_path)
    detail = await host.install_from_zip(package.read_bytes())
    assert detail["id"] == "fu-core"
    await host.start("fu-core", require_enabled=False)
    return host, registry, source


def _packaged_rule_template(plugins: Path) -> dict:
    """Read the rule template that actually shipped inside the installed package."""

    path = plugins / "fu-core" / "rules" / f"{FU_RULE_ID}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 验收


@pytest.mark.asyncio
async def test_fu_shaped_package_registers_and_answers_probe(tmp_path):
    """安装 → 启用 → 探活 → 注册；runtime 身份来自包声明，不来自核心。"""

    host, registry, source = await _install_and_enable(tmp_path)
    try:
        runtime = registry.get(FU_RUNTIME_ID, minimum_version=1)
        assert runtime.runtime_id == FU_RUNTIME_ID
        assert runtime.capabilities.versioned_state is True
        assert runtime.capabilities.authoritative_intents is True
        assert runtime.describe_experience({}, "zh-CN")["name"] == "Fabula Ultima"
    finally:
        source.close()


@pytest.mark.asyncio
async def test_fu_game_creation_binds_versioned_state(tmp_path):
    """建局不再回滚：真实 bind_ruleset_runtime 通过 game_binding 完成绑定。

    这是 ``51798c83`` 之前必然失败的路径——缺 ``game_binding`` 时
    ``bind_ruleset_runtime`` 抛 ``RULESET_BINDING_FAILED`` 并回滚事务。
    """

    from src.rules.rule_system import RuleSystem
    from src.webui.services.game_creation_phases import bind_ruleset_runtime

    host, registry, source = await _install_and_enable(tmp_path)
    try:
        template = _packaged_rule_template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        rule = RuleSystem(template)

        rollbacks = []

        class Transaction:
            game_key = ("probe", "fu", "creation")

            def rollback(self):
                rollbacks.append(True)
                raise AssertionError("creation must not roll back")

        instance = GameInstance(
            game_key=("probe", "fu", "creation"), run_id="run-fu")
        failure = bind_ruleset_runtime(Transaction(), instance, runtime, rule, "zh-CN")

        assert failure is None, failure
        assert rollbacks == []
        binding = ruleset_runtime.binding(instance)
        assert binding == {
            "id": FU_RUNTIME_ID,
            "version": 1,
            "content_version": "1.0.5",
            "state_schema_version": 1,
        }
    finally:
        source.close()


@pytest.mark.asyncio
async def test_fu_runtime_serves_character_sheet(tmp_path):
    """建卡走外部进程：FU 公式 HP = 等级 + 力量 × 5。"""

    host, registry, source = await _install_and_enable(tmp_path)
    try:
        template = _packaged_rule_template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        sheet = runtime.finalize_character({}, {
            "name": "Livia",
            "level": 5,
            "attributes": {"dex": 8, "ins": 10, "mig": 12, "wlp": 6},
        })
        assert sheet["max_hp"] == 65  # 5 + 12 * 5
        assert sheet["max_mp"] == 35  # 5 + 6 * 5
        assert sheet["fabula"] == 3
        assert sheet["finalized"] is True
    finally:
        source.close()


@pytest.mark.asyncio
async def test_fu_runtime_settles_authoritative_intent(tmp_path):
    """权威动作闭环：列可用动作 → 结算 → 落账，版本推进且宿主 rng 可序列化。"""

    host, registry, source = await _install_and_enable(tmp_path)
    try:
        template = _packaged_rule_template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        instance = GameInstance(game_key=("probe", "fu", "intent"), run_id="run-intent")
        instance.modules["ruleset_runtime"] = ruleset_runtime.fresh()
        ruleset_runtime.replace_binding(
            instance, {"id": FU_RUNTIME_ID, "runtime_version": 1})

        intents = runtime.available_intents(instance, "alice")
        assert [item["type"] for item in intents][:2] == [
            "fu.attribute_check", "fu.attack"]

        settled = runtime.resolve_intent(
            instance,
            {"intent_id": "fu-intent-1", "type": "fu.attribute_check",
             "dice": {"primary": 8, "secondary": 10}},
            random.Random(secrets.randbits(128)),
        )
        batch = settled["event_batch"]
        assert batch["expected_version"] == 0 and batch["result_version"] == 1
        assert batch["events"][0]["rng_injected"] is True
        assert batch["events"][0]["total"] == 16  # min(8, 8) + min(10, 8)

        applied = runtime.apply_event_batch(instance, batch)
        assert applied["applied"] is True
        assert ruleset_runtime.state(instance)["version"] == 1
    finally:
        source.close()


@pytest.mark.asyncio
async def test_fu_runtime_declares_automatic_enemy_turns(tmp_path):
    """``next_automatic_intent``：有敌方待办时出一手，出完返回 None 而不是报错。

    缺这个 op 时敌人与 AI 席位静默不动、不报错——这条用例把静默失效变成
    可观测行为。
    """

    host, registry, source = await _install_and_enable(tmp_path)
    try:
        template = _packaged_rule_template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        instance = GameInstance(game_key=("probe", "fu", "auto"), run_id="run-auto")
        instance.modules["ruleset_runtime"] = ruleset_runtime.fresh()
        ruleset_runtime.replace_binding(
            instance, {"id": FU_RUNTIME_ID, "runtime_version": 1})

        # 空队列：正常终止值是 None。
        assert runtime.next_automatic_intent(instance) is None

        ruleset_runtime.replace_state(
            instance, {"version": 0, "pending_enemies": ["goblin-a", "goblin-b"]})

        first = runtime.next_automatic_intent(instance)
        assert first["actor_id"] == "goblin-a"
        assert first["type"] == "fu.attack"
        assert first["automatic"] is True

        # 结算这一手：队列出队，下一手换人。
        settled = runtime.resolve_intent(instance, first,
                                         random.Random(secrets.randbits(128)))
        applied = runtime.apply_event_batch(instance, settled["event_batch"])
        assert applied["applied"] is True
        second = runtime.next_automatic_intent(instance)
        assert second["actor_id"] == "goblin-b"

        settled2 = runtime.resolve_intent(instance, second,
                                          random.Random(secrets.randbits(128)))
        runtime.apply_event_batch(instance, settled2["event_batch"])
        assert runtime.next_automatic_intent(instance) is None
    finally:
        source.close()


@pytest.mark.asyncio
async def test_fu_view_carries_declarative_panels(tmp_path):
    """面板能穿过子进程边界：插件发声明式节点，宿主原样透传、不裁剪。

    插件包不能带 Vue 组件，命刻/机会/冲突步骤只能这样表达；这条用例守住
    「节点不被宿主改写或丢弃」，前端渲染由 `RulesetPanels.test.ts` 覆盖。
    """

    host, registry, source = await _install_and_enable(tmp_path)
    try:
        template = _packaged_rule_template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        instance = GameInstance(game_key=("probe", "fu", "panel"), run_id="run-panel")
        instance.modules["ruleset_runtime"] = ruleset_runtime.fresh()
        ruleset_runtime.replace_binding(
            instance, {"id": FU_RUNTIME_ID, "runtime_version": 1})
        ruleset_runtime.replace_state(
            instance, {"version": 2, "pending_enemies": ["goblin-a"]})

        view = runtime.gameplay_view(instance, "alice", False)
        panels = view["panels"]
        assert panels[0]["component"] == "group"
        assert panels[0]["children"][1]["component"] == "progress"
        assert panels[0]["children"][1]["max"] == 6
        assert panels[1]["component"] == "list"
        assert panels[2]["action"] == "fu.attack"

        # 玩家投影不带 GM 私有段，但面板对所有人一致（面板内容由 runtime 自己裁剪）。
        assert "private" not in view
        gm_view = runtime.gameplay_view(instance, "gm", True)
        assert gm_view["panels"] == panels
    finally:
        source.close()


@pytest.mark.asyncio
async def test_fu_rule_template_cannot_declare_custom_dice_system(tmp_path):
    """骰制接缝缺口：规则模板自带骰制无处可写，只能声明 ``none`` 后走 intent。

    这是探针包用 ``dice_system: "none"`` 的原因，也是 ``#739`` 想解决、但
    必须与"把 FU 并进 main"切开的那一半。
    """

    from src.rules.rule_system import SUPPORTED_DICE_SYSTEMS, RuleSystem

    assert SUPPORTED_DICE_SYSTEMS == frozenset({"d20", "d100", "none"})
    write_fu_package(tmp_path / "plugins")
    template = _packaged_rule_template(tmp_path / "plugins")
    assert template["dice_system"] == "none"
    with pytest.raises(ValueError, match="不支持的检定骰制"):
        RuleSystem({**template, "dice_system": "fu_dual"})


@pytest.mark.asyncio
async def test_fu_runtime_resolves_free_text_checks(tmp_path):
    """探索阶段的自由描述也能掷骰：核心只规划，骰子留在包里。

    闭环是「规划出的 CheckRequest → 跨进程 resolve_narrative_checks →
    check_result 落到行动上 → 生成必须遵循的约束块 → 记录本轮检定」。
    """

    host, registry, source = await _install_and_enable(tmp_path)
    try:
        template = _packaged_rule_template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        # 能力位来自包声明，适配器据此才允许宿主把骰子交出去。
        assert runtime.capabilities.narrative_checks is True

        instance = GameInstance(game_key=("probe", "fu", "check"), run_id="run-check")
        instance.modules["ruleset_runtime"] = ruleset_runtime.fresh()
        ruleset_runtime.replace_binding(
            instance, {"id": FU_RUNTIME_ID, "runtime_version": 1})
        ruleset_runtime.state(instance)["characters"] = {
            "alice": {"attributes": {"dex": 10, "ins": 8, "mig": 12, "wlp": 6}},
        }
        instance.players["alice"] = {"user_id": "alice", "character_name": "Livia"}
        action = {"user_id": "alice", "text": "我翻过塌了一半的墙"}
        instance.action_queue = [action]
        planned = [(action, {
            "check_id": "check-1",
            "actor_uid": "alice",
            "actor_name": "Livia",
            "attribute": "dex",
            "target": 6,
            "label": "灵巧检定",
        })]

        surviving = resolve_planned_checks(instance, resolver_for(runtime), planned)

        assert [request["check_id"] for _, request in surviving] == ["check-1"]
        check = action["check_result"]
        assert check["planner_source"] == "runtime"
        assert check["dice"] == "d8+d8"
        assert check["roll"] == 7 and check["total"] == 7
        assert check["dc"] == 6 and check["verdict"] == "成功"
        assert action["dice_value"] == 7

        block = build_dice_constraint_block(
            instance, action["text"], RuleSystem(template), "none", DiceResolver(),
            planned_only=True,
        )
        assert "必须遵循" in block
        assert "成功" in block
        assert checks_module.last_check(instance)["roll"] == 7
    finally:
        source.close()
