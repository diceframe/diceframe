"""fu-core 规则运行时包的端到端验收。

这里跑的不是探针：安装的是真实产出的 ``.dfplugin``，进程里跑的是 Fabula Ultima
规则引擎本体。包体本身不进仓库（三千多行第三方规则，归属 FU 自己的仓库），所以
用环境变量指路：

    FU_CORE_PLUGIN=/path/to/fu-core-1.0.5.dfplugin \\
        py -3.11 -m pytest tests/test_fu_core_package_end_to_end.py -q

没设置时整组跳过，CI 不受影响。

覆盖的是**接缝**，不是规则正确性：安装 → 启用 → 注册 → 绑定 → 建卡 → 自由叙事
检定 → 权威意图 → 敌方自动行动 → 声明式面板 → 角色卡回写。任何一条断了，说明
外部规则运行时这条路走不通。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

PACKAGE = os.environ.get("FU_CORE_PLUGIN", "")
pytestmark = pytest.mark.skipif(
    not PACKAGE or not Path(PACKAGE).is_file(),
    reason="set FU_CORE_PLUGIN to a built fu-core .dfplugin to run this acceptance suite",
)

PLUGIN_ID = "fu-core"
RUNTIME_ID = "fu:core"
RULE_ID = "fabula_ultima"


async def _install(tmp_path: Path):
    """Install the package the way the app does, then enable it."""

    from src.plugin_host.host import PluginHost
    from src.rulesets.plugin_runtime_source import PluginRulesetRuntimeSource
    from src.rulesets.registry import RulesetRuntimeRegistry

    plugins, data_dir = tmp_path / "plugins", tmp_path / "data"
    host = PluginHost(plugins, data_dir)
    registry = RulesetRuntimeRegistry([])
    source = PluginRulesetRuntimeSource(registry)
    host.set_ruleset_runtime_listener(lambda: source.sync(host))

    detail = await host.install_from_zip(Path(PACKAGE).read_bytes())
    assert detail["id"] == PLUGIN_ID
    await host.update_config(PLUGIN_ID, {"enabled": True})
    await host.start(PLUGIN_ID, require_enabled=True)
    source.sync(host)
    return host, registry, source


def _template(plugins: Path) -> dict:
    raw = (plugins / PLUGIN_ID / "rules" / f"{RULE_ID}.json").read_text(encoding="utf-8")
    return json.loads(raw)


def _sheet(**attributes: int) -> dict:
    attrs = {"dex": 8, "ins": 8, "mig": 8, "wlp": 8, **attributes}
    return {
        "attributes": attrs,
        "hp": 60, "max_hp": 60, "mp": 40, "max_mp": 40,
        "fabula": 3, "ip": 6, "gold": 500,
        "skills": [], "equipment": [], "inventory": [],
        "class": "奥灵使 3 / 拟态者 2",
        "class_levels": {"奥灵使": 3, "拟态者": 2},
        "level": 5,
    }


def _bound_instance(*seats: tuple[str, str]):
    from src.engine.game_instance import GameInstance
    from src.engine.modules import ruleset_runtime

    instance = GameInstance(game_key=("test", "fu", "table"), run_id="run-fu")
    ruleset_runtime.bind(instance, {
        "id": RUNTIME_ID, "version": 1, "rule_id": RULE_ID,
        "content_version": "1.0.5", "state_schema_version": 1,
    })
    for uid, mode in seats:
        instance.put_player(uid, {
            "character_name": uid.title(),
            "character_sheet": _sheet(),
            "control": {"mode": mode, "revision": 1},
        })
    return instance


@pytest.mark.asyncio
async def test_package_installs_registers_and_ships_a_none_dice_rule(tmp_path):
    host, registry, source = await _install(tmp_path)
    try:
        runtime = registry.get(RUNTIME_ID, minimum_version=1)
        assert runtime.capabilities.versioned_state is True
        assert runtime.capabilities.authoritative_intents is True
        assert runtime.capabilities.narrative_checks is True

        from src.rules.rule_system import RuleSystem

        template = _template(tmp_path / "plugins")
        rule = RuleSystem(template)
        # 宿主的骰制白名单只有 d20/d100/none；FU 的双属性骰由运行时自己掷。
        assert rule.dice_system == "none"
        assert template["runtime"]["id"] == RUNTIME_ID
        assert template["check_mechanic"]["dice"] == "2d_attributes"
        assert template["source_dice_system"] == "fu"
        assert runtime.describe_experience(rule, "zh-CN")["profile"] == "fabula_ultima"
    finally:
        source.close()


@pytest.mark.asyncio
async def test_game_creation_binds_the_packaged_runtime(tmp_path):
    from src.engine.game_instance import GameInstance
    from src.engine.modules import ruleset_runtime
    from src.rules.rule_system import RuleSystem
    from src.webui.services.game_creation_phases import bind_ruleset_runtime

    host, registry, source = await _install(tmp_path)
    try:
        template = _template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        rule = RuleSystem(template)
        instance = GameInstance(game_key=("test", "fu", "creation"), run_id="run-create")

        rollbacks: list[bool] = []

        class Transaction:
            game_key = ("test", "fu", "creation")

            def rollback(self):
                rollbacks.append(True)
                raise AssertionError("creation must not roll back")

        failure = bind_ruleset_runtime(Transaction(), instance, runtime, rule, "zh-CN")

        assert failure is None, failure
        assert rollbacks == []
        assert ruleset_runtime.binding(instance)["id"] == RUNTIME_ID
        # content_version 说的是规则内容的版本（模板里的 rule_version），不是插件包版本。
        assert ruleset_runtime.binding(instance)["content_version"] == template["rule_version"]
    finally:
        source.close()


@pytest.mark.asyncio
async def test_character_creation_uses_fu_formulas(tmp_path):
    host, registry, source = await _install(tmp_path)
    try:
        template = _template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        draft = {
            "name": "Livia",
            "attributes": {"dex": 8, "ins": 8, "mig": 10, "wlp": 6},
            "class_levels": {"奥灵使": 3, "拟兽使": 2},
            "skills": [{"name": "奥灵回响", "value": 2}],
        }
        sheet = runtime.finalize_character(template, draft)
        # HP = 力量骰面 × 5 + 20；MP = 意志骰面 × 5。
        assert sheet["max_hp"] == 70
        assert sheet["max_mp"] == 30
        assert sheet["class"] == "奥灵使 3 / 拟兽使 2"

        broken = dict(draft, attributes={"dex": 8, "ins": 8, "mig": 8, "wlp": 4})
        errors = runtime.validate_character(template, broken)
        assert any("d6/d8/d10/d12" in item for item in errors)
    finally:
        source.close()


@pytest.mark.asyncio
async def test_free_text_checks_are_rolled_inside_the_package(tmp_path):
    from src.rulesets.narrative_checks import normalize_resolved_checks

    host, registry, source = await _install(tmp_path)
    try:
        template = _template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        instance = _bound_instance(("alice", "human"))

        requests = [{
            "check_id": "c1", "actor_uid": "alice", "actor_ref": "player:alice",
            "attribute": "mig", "dc": 10, "label": "力量检定", "kind": "check",
        }]
        results = runtime.resolve_narrative_checks(instance, requests)

        assert len(results) == 1
        answer = results[0]
        assert answer["check_id"] == "c1"
        assert answer["dice"] == "d8+d8"
        assert len(answer["rolls"]) == 2
        assert answer["roll"] == sum(answer["rolls"])
        assert 2 <= answer["roll"] <= 16
        assert answer["dc"] == 10
        assert answer["verdict"]

        paired = normalize_resolved_checks(requests, results)
        assert len(paired) == 1
        _, check = paired[0]
        assert check["planner_source"] == "runtime"
        assert check["roll"] == answer["roll"]
    finally:
        source.close()


@pytest.mark.asyncio
async def test_conflict_intent_round_trips_state_and_sheets(tmp_path):
    from src.engine.modules import ruleset_runtime

    host, registry, source = await _install(tmp_path)
    try:
        template = _template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        instance = _bound_instance(("alice", "human"), ("bob", "ai"))

        started = runtime.resolve_intent(instance, {
            "intent_id": "i1", "type": "conflict.start",
            "enemies": [{"template_id": "goblin"}],
        }, __import__("random").Random(7))
        batch = started["event_batch"]
        applied = runtime.apply_event_batch(instance, batch)

        assert applied["applied"] is True
        assert applied["summary"]
        conflict = ruleset_runtime.state(instance)[RULE_ID]["conflict"]
        assert conflict["status"] == "active"
        assert set(conflict["participants"]) == {"player:alice", "player:bob"}
        assert ruleset_runtime.event_ledger(instance)[-1]["batch_id"] == batch["batch_id"]

        # 步骤制：选谁就轮到谁，AI 席位随后要交出一手。
        selected = runtime.resolve_intent(instance, {
            "intent_id": "i2", "type": "conflict.select_actor", "actor_id": "player:bob",
        }, __import__("random").Random(7))
        runtime.apply_event_batch(instance, selected["event_batch"])
        automatic = runtime.next_automatic_intent(instance)
        assert automatic and automatic["actor_id"] == "player:bob"

        # 换成真人席位，运行时不再替他出招。
        selected = runtime.resolve_intent(instance, {
            "intent_id": "i3", "type": "conflict.select_actor", "actor_id": "player:alice",
        }, __import__("random").Random(7))
        runtime.apply_event_batch(instance, selected["event_batch"])
        assert runtime.next_automatic_intent(instance) is None
    finally:
        source.close()


@pytest.mark.asyncio
async def test_rest_writes_the_healed_sheet_back_to_the_host(tmp_path):
    host, registry, source = await _install(tmp_path)
    try:
        template = _template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        instance = _bound_instance(("alice", "human"))
        hurt = dict(instance.get_character_sheet("alice"), hp=7, mp=1)
        instance.set_character_sheet("alice", hurt)

        resolved = runtime.resolve_intent(instance, {"intent_id": "r1", "type": "rest.take"}, None)
        pending = instance.get_character_sheet("alice")
        # resolve 只改运行时自己的副本：宿主这张卡在 apply 之前必须一字未动。
        assert pending["hp"] == 7

        runtime.apply_event_batch(instance, resolved["event_batch"])

        healed = instance.get_character_sheet("alice")
        assert healed["hp"] == healed["max_hp"] == 60
        assert healed["mp"] == healed["max_mp"] == 40
    finally:
        source.close()


@pytest.mark.asyncio
async def test_gameplay_view_speaks_the_declarative_vocabulary(tmp_path):
    host, registry, source = await _install(tmp_path)
    try:
        template = _template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        instance = _bound_instance(("alice", "human"), ("bob", "human"))
        started = runtime.resolve_intent(instance, {
            "intent_id": "v1", "type": "conflict.start",
            "enemies": [{"template_id": "goblin"}, {"template_id": "slime"}],
        }, __import__("random").Random(3))
        runtime.apply_event_batch(instance, started["event_batch"])

        view = runtime.gameplay_view(instance, "alice", viewer_is_gm=True)

        panels = view["panels"]
        assert panels and all(isinstance(node, dict) and node.get("component") for node in panels)
        kinds = {
            child["component"]
            for panel in panels
            for child in ([panel] + list(panel.get("children") or []))
        }
        assert {"group", "progress"} <= kinds
        assert "catalog" not in json.dumps(view)
        assert "item_catalog" not in json.dumps(view)
    finally:
        source.close()


@pytest.mark.asyncio
async def test_the_package_stays_inside_the_transport_budget(tmp_path):
    """每次请求/回包都要塞进宿主 256 KiB 的协议上限。"""

    from src.rulesets.runtime_probe import MAX_PROBE_BYTES

    host, registry, source = await _install(tmp_path)
    try:
        template = _template(tmp_path / "plugins")
        runtime = registry.resolve(template)
        instance = _bound_instance(("alice", "human"), ("bob", "human"), ("carol", "ai"))
        started = runtime.resolve_intent(instance, {
            "intent_id": "b1", "type": "conflict.start",
            "enemies": [{"template_id": "orc"}],
        }, __import__("random").Random(11))
        applied = runtime.apply_event_batch(instance, started["event_batch"])
        view = runtime.gameplay_view(instance, "alice", viewer_is_gm=True)

        for payload in (started, applied, view):
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            assert len(encoded) < MAX_PROBE_BYTES
    finally:
        source.close()
