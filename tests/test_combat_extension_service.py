"""战斗扩展服务层契约测试（Issue 212 phase 2 Slice 2.3）。"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from webapi_harness import web_api  # noqa: F401

from src.engine.game_instance import GameInstance, GameState
from src.engine import combat_narrative
from src.webui.services import combat_extension as svc


class _FakeRule:
    def __init__(self, template: dict) -> None:
        self.template = template


def _template() -> dict:
    return {
        "rule_id": "freeform_wuxia",
        "combat": {
            "scheduler": {"kind": "threshold", "gauge": "action_gauge",
                          "speed": "action_speed", "threshold": 100,
                      "speed_formula": {"op": "constant", "value": 50}},
            "resources": [
                {"id": "hp", "source": "hp"},
                {"id": "qi", "source": "special_stat", "stat": "qi", "maximum": 100},
                {"id": "barrier", "source": "combat_state", "maximum": 30,
                 "damage_priority": "before_hp"},
            ],
            "actions": [
                {"id": "item:healing_pill.use", "kind": "consumable", "name": "回春丹",
                 "consume_item": {"item": "回春丹", "qty": 1},
                 "effects": [{"kind": "resource_change", "resource": "hp",
                              "amount": {"op": "constant", "value": 10}}]},
                {"id": "ability:escape_step", "kind": "ability", "name": "遁术",
             "costs": [{"resource": "qi", "amount": {"op": "constant", "value": 5}}],
             "effects": [{"kind": "modify_stat", "resource": "action_speed",
                          "amount": {"op": "constant", "value": 50}, "duration": 1}]},
            {"id": "ability:qi_palm", "kind": "ability", "name": "内力掌",
                 "costs": [{"resource": "qi", "amount": {"op": "constant", "value": 8}}],
                 "effects": [{"kind": "damage", "amount": {
                     "op": "multiply", "args": [
                         {"op": "attribute", "id": "str"}, {"op": "constant", "value": 2}]},
                     "damage_type": "bludgeoning"}]},
            ],
        },
    }


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "wuxia", "bot"), gm_uid="gm")
    instance.state = GameState.ACTIVE_ACTION
    instance.players = {
        "p1": {"character_name": "李逍遥", "character_sheet": {
            "hp": 30, "max_hp": 30, "qi": 50, "max_qi": 100,
            "attributes": {"str": 5, "wis": 3},
        }},
        "p2": {"character_name": "赵灵儿", "character_sheet": {
            "hp": 20, "max_hp": 20, "qi": 30, "max_qi": 80,
            "attributes": {"str": 2, "wis": 6},
        }},
    }
    instance.npcs["old_monk"] = {"name": "老僧", "hp": 14}
    return instance


def _rule() -> _FakeRule:
    return _FakeRule(template=_template())


def test_projection_hides_other_players_pools() -> None:
    instance = _instance()
    projection = svc.combat_extension_projection(instance, _rule(), viewer_uid="p1", viewer_is_gm=False)
    assert projection is not None
    assert [action["id"] for action in projection["actions"]] == [
        "item:healing_pill.use", "ability:escape_step", "ability:qi_palm",
    ]
    assert set(projection["pools"]) == {"player:p1"}
    assert projection["pools"]["player:p1"]["qi"] == {"current": 50, "maximum": 100}
    assert projection["actions"][0]["consume_item"] == {"item": "回春丹", "qty": 1}
    # 调度器是桌面公共信息，但尚未初始化时为空。
    assert projection["scheduler"]["ready"] == []

    gm_projection = svc.combat_extension_projection(instance, _rule(), viewer_uid="gm", viewer_is_gm=True)
    # NPC 按需参与：未被交战过的 NPC 不占状态与面板。
    assert set(gm_projection["pools"]) == {"player:p1", "player:p2"}
    svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "engage", "action_id": "ability:qi_palm", "target_ids": ["npc:old_monk"]},
        actor_uid="p1", viewer_is_gm=False,
    )
    engaged = svc.combat_extension_projection(instance, _rule(), viewer_uid="gm", viewer_is_gm=True)
    assert "npc:old_monk" in engaged["pools"]


def test_projection_is_none_without_combat_block() -> None:
    instance = _instance()

    class _PlainRule:
        template = {"rule_id": "plain"}

    assert svc.combat_extension_projection(instance, _PlainRule(), viewer_uid="p1", viewer_is_gm=False) is None


def test_malformed_combat_block_disables_optional_projection() -> None:
    instance = _instance()

    class _MalformedRule:
        template = {"rule_id": "bad", "combat": {"scheduler": {}}}

    assert svc.combat_extension_projection(
        instance, _MalformedRule(), viewer_uid="p1", viewer_is_gm=False,
    ) is None
    result = svc.resolve_combat_action(
        instance,
        _MalformedRule(),
        {"intent_id": "bad-config", "action_id": "strike"},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    assert result["code"] == "COMBAT_EXTENSION_NOT_CONFIGURED"


@pytest.mark.parametrize("scheduler_kind", ["round_robin", "initiative"])
def test_projection_ready_uses_scheduler_available_actors(scheduler_kind: str) -> None:
    instance = _instance()
    template = _template()
    template["combat"]["scheduler"] = {"kind": scheduler_kind}
    rule = _FakeRule(template=template)

    # Seed the scheduler state without advancing it. Both non-threshold
    # schedulers expose the current actor through available_actors(), while
    # SchedulerState.ready remains empty for these implementations.
    result = svc.scheduler_advance(instance, rule)
    assert result["ok"] is True
    current = "player:p1"
    projection = svc.combat_extension_projection(
        instance, rule, viewer_uid="p1", viewer_is_gm=False,
    )

    assert projection is not None
    assert projection["scheduler"]["ready"] == [current]


@pytest.mark.parametrize("scheduler_kind", ["round_robin", "initiative"])
def test_first_non_threshold_scheduler_advance_keeps_first_actor(
    scheduler_kind: str,
) -> None:
    instance = _instance()
    template = _template()
    template["combat"]["scheduler"] = {"kind": scheduler_kind}
    rule = _FakeRule(template=template)

    result = svc.scheduler_advance(instance, rule)

    assert result["ok"] is True
    assert result["ready"] == ["player:p1"]


def test_player_action_costs_qi_and_damages_npc() -> None:
    instance = _instance()
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-1", "action_id": "ability:qi_palm", "target_ids": ["npc:old_monk"]},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["ok"] is True
    # 内力 50 - 8 = 42，并写回角色卡（单一权威）。
    assert instance.get_character_sheet("p1")["qi"] == 42
    pools = instance.combat_extension["pools"]
    assert pools["player:p1"]["qi"]["current"] == 42
    # 老僧无护盾：str(5)*2=10 伤害直接进 HP（14 -> 4），并落进战斗扩展池。
    assert pools["npc:old_monk"]["hp"]["current"] == 4
    assert instance.npcs["old_monk"]["hp"] == 4
    damage = [e for e in result["events"] if e["type"] == "combat.damage_applied"]
    assert damage and damage[0]["applied"] == 10


def test_healing_pill_restores_hp_with_maximum_clamp() -> None:
    instance = _instance()
    instance.get_character_sheet("p1")["hp"] = 25
    instance.get_character_sheet("p1")["inventory"] = [{"name": "回春丹", "qty": 1}]
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-2", "action_id": "item:healing_pill.use"},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["ok"] is True
    # 25 + 10 → clamp 到 max_hp 30，写回角色卡。
    assert instance.get_character_sheet("p1")["hp"] == 30


def test_player_cannot_drive_other_entities() -> None:
    instance = _instance()
    before = instance.get_character_sheet("p2")["qi"]
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-3", "action_id": "ability:qi_palm", "target_ids": ["npc:old_monk"],
         "actor_id": "player:p2"},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["code"] == "ACTOR_FORBIDDEN"
    assert instance.get_character_sheet("p2")["qi"] == before
    npc_forbidden = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-4", "action_id": "ability:qi_palm", "target_ids": ["player:p2"],
         "actor_id": "npc:old_monk"},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert npc_forbidden["code"] == "ACTOR_FORBIDDEN"


def test_gm_can_drive_npc_entity() -> None:
    instance = _instance()
    instance.get_character_sheet("p1")["hp"] = 22
    instance.npcs["old_monk"].update({
        "qi": 8,
        "max_qi": 20,
        "attributes": {"str": 3},
        "inventory": [{"name": "回春丹", "qty": 1}],
    })
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-5", "action_id": "item:healing_pill.use",
         "target_ids": ["player:p1"], "actor_id": "npc:old_monk"},
        actor_uid="gm", viewer_is_gm=True,
    )
    assert result["ok"] is True
    assert instance.get_character_sheet("p1")["hp"] == 30  # 22 + 10 → clamp
    assert instance.npcs["old_monk"]["inventory"] == []

    # NPC 使用同一套权威属性/资源公式，并把 special_stat 写回 NPC 记录。
    strike = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-5b", "action_id": "ability:qi_palm", "target_ids": ["player:p1"],
         "actor_id": "npc:old_monk"},
        actor_uid="gm", viewer_is_gm=True,
    )
    assert strike["ok"] is True
    assert instance.npcs["old_monk"]["qi"] == 0
    assert instance.get_character_sheet("p1")["hp"] == 24


def test_unknown_action_and_target_fail_closed() -> None:
    instance = _instance()
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-6", "action_id": "ability:fly"},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["code"] == "ACTION_NOT_FOUND"
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-7", "action_id": "ability:qi_palm", "target_ids": ["npc:ghost"]},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["code"] == "TARGET_NOT_FOUND"


def test_insufficient_qi_rejects_without_state_change() -> None:
    instance = _instance()
    instance.get_character_sheet("p1")["qi"] = 3
    before_pools = instance.combat_extension
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-8", "action_id": "ability:qi_palm", "target_ids": ["npc:old_monk"]},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["code"] == "ACTION_REJECTED"
    assert instance.combat_extension == before_pools
    assert instance.get_character_sheet("p1")["qi"] == 3


def test_unconfigured_rule_is_rejected() -> None:
    class _PlainRule:
        template = {"rule_id": "plain"}

    result = svc.resolve_combat_action(
        _instance(), _PlainRule(),
        {"intent_id": "i-9", "action_id": "anything"},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["code"] == "COMBAT_EXTENSION_NOT_CONFIGURED"


def test_state_survives_round_trip() -> None:
    from src.engine.game_instance import GameInstance as GI

    instance = _instance()
    svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-10", "action_id": "ability:qi_palm", "target_ids": ["npc:old_monk"]},
        actor_uid="p1", viewer_is_gm=False,
    )
    recovered = GI.from_dict(instance.to_dict())
    assert recovered.combat_extension == instance.combat_extension
    assert recovered.get_character_sheet("p1")["qi"] == 42


def test_resolved_action_is_queued_for_narration_until_consumed() -> None:
    instance = _instance()
    result = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-narrative", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )

    assert result["ok"] is True
    pending = combat_narrative.pending_events(instance)
    assert len(pending) == 1
    assert pending[0]["intent_id"] == "i-narrative"
    assert pending[0]["events"][-1]["type"] == "combat.damage_applied"

    prompt_block = combat_narrative.format_pending_events(instance)
    assert "已结算战斗事实" in prompt_block
    assert "npc:old_monk" in prompt_block
    assert '"after":4' in prompt_block

    combat_narrative.consume_pending_events(instance, ["i-narrative"])
    assert combat_narrative.pending_events(instance) == []
    assert "pending_narrative_events" not in instance.combat_extension


@pytest.mark.parametrize("intent", [
    {"intent_id": "x"},
    {"intent_id": "x", "action_id": ""},
])
def test_malformed_intents_fail(intent: dict) -> None:
    result = svc.resolve_combat_action(
        _instance(), _rule(), intent, actor_uid="p1", viewer_is_gm=False,
    )
    assert result["ok"] is False


def test_action_summary_enters_public_timeline() -> None:
    """多人可见性：动作结算摘要写进当前回合的公共状态变化。"""
    instance = _instance()
    instance.round_number = 4
    instance.log.append({"round": 4, "actions": [], "gm_response": "交战",
                         "state_changes": []})
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-t", "action_id": "ability:qi_palm", "target_ids": ["npc:old_monk"]},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["ok"] is True
    changes = instance.log[-1]["state_changes"]
    assert any("战斗扩展" in item and "李逍遥" in item and "内力掌" in item
               for item in changes)
    assert any("老僧 -10" in item for item in changes)


def test_scheduler_advance_accumulates_and_buff_speeds_next_tick() -> None:
    """ATB：GM 推进累积 gauge；遁术 buff 提升下一次推进速度。"""
    instance = _instance()
    result = svc.scheduler_advance(instance, _rule())
    assert result["ok"] is True
    # 按需参与：未交战过的 NPC 不在调度集合里。
    assert result["ready"] == ["player:p1", "player:p2"]
    assert result["gauges"]["player:p1"] == 100
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-s1", "action_id": "ability:escape_step"},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["ok"] is True
    assert instance.combat_extension["buffs"][0]["delta"] == 50
    result = svc.scheduler_advance(instance, _rule())
    assert "player:p1" in result["ready"]
    # 时长 1 的 buff 在推进后到期移除。
    assert not any(b["entity_id"] == "player:p1"
                   for b in instance.combat_extension["buffs"])


def test_scheduler_noop_does_not_tick_buffs_when_ready_queue_is_nonempty() -> None:
    instance = _instance()
    first = svc.scheduler_advance(instance, _rule())
    assert first["ok"] is True
    instance.combat_extension["buffs"] = [{
        "entity_id": "player:p1",
        "stat": "action_speed",
        "delta": 10,
        "remaining": 2,
    }]

    second = svc.scheduler_advance(instance, _rule())

    assert second["ok"] is True
    assert instance.combat_extension["buffs"][0]["remaining"] == 2


def test_scheduler_gates_actions_before_ready() -> None:
    """调度器激活后，未就绪实体不能行动（ATB 纪律）。"""
    instance = _instance()
    svc.scheduler_advance(instance, _rule())
    ready = instance.combat_extension["scheduler"]["ready"]
    other = next(entity for entity in ("player:p1", "player:p2", "npc:old_monk")
                 if entity not in ready)
    actor_uid = other.removeprefix("player:") or "gm"
    viewer_is_gm = not other.startswith("player:")
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-g1", "action_id": "item:healing_pill.use",
         "actor_id": other},
        actor_uid=actor_uid, viewer_is_gm=viewer_is_gm,
    )
    assert result["code"] == "SCHEDULER_NOT_READY"


def test_ready_actor_action_consumes_turn() -> None:
    instance = _instance()
    svc.scheduler_advance(instance, _rule())
    ready = list(instance.combat_extension["scheduler"]["ready"])
    actor = ready[0]
    uid = actor.removeprefix("player:") if actor.startswith("player:") else actor
    if actor.startswith("player:"):
        target_sheet = instance.get_character_sheet(uid)
        target_sheet["inventory"] = [{"name": "回春丹", "qty": 3}]
        instance.set_character_sheet(uid, target_sheet)
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-c1", "action_id": "item:healing_pill.use", "actor_id": actor},
        actor_uid=uid, viewer_is_gm=True,
    )
    assert result["ok"] is True
    assert actor not in instance.combat_extension["scheduler"]["ready"]


def test_rapid_action_submissions_never_double_charge() -> None:
    """快速连发提交（模拟并发请求）：结算在事件循环内原子执行，
    内力扣减无重复、无丢失。真正的多请求竞态由 aiohttp 串行化 +
    integration 并发测试覆盖。"""
    instance = _instance()
    results = [
        svc.resolve_combat_action(
            instance, _rule(),
            {"intent_id": f"i-r{i}", "action_id": "ability:qi_palm",
             "target_ids": ["npc:old_monk"]},
            actor_uid="p1", viewer_is_gm=False,
        )
        for i in range(5)
    ]
    assert all(r["ok"] for r in results)
    assert instance.get_character_sheet("p1")["qi"] == 10
    assert instance.combat_extension["pools"]["player:p1"]["qi"]["current"] == 10


def test_consume_item_deducts_inventory_atomically() -> None:
    """库存联动：动作执行扣除背包物品；不足则整单拒绝。"""
    instance = _instance()
    sheet = instance.get_character_sheet("p1")
    sheet["inventory"] = [
        {"name": "回春丹", "qty": 2},
        {"name": "长剑"},
    ]
    instance.set_character_sheet("p1", sheet)
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-inv", "action_id": "item:healing_pill.use"},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["ok"] is True
    rows = instance.get_character_sheet("p1")["inventory"]
    assert rows == [{"name": "回春丹", "qty": 1}, {"name": "长剑"}]

    # 只剩 1 颗时再吃 1 颗可以，但模板动作若声明 qty=2 就必须拒绝。
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-inv2", "action_id": "item:healing_pill.use"},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["ok"] is True
    rows = instance.get_character_sheet("p1")["inventory"]
    assert [row for row in rows if row.get("name") == "回春丹"] == []


def test_repeated_intent_replays_without_double_spending() -> None:
    instance = _instance()
    intent = {
        "intent_id": "i-retry",
        "action_id": "ability:qi_palm",
        "target_ids": ["npc:old_monk"],
    }

    first = svc.resolve_combat_action(
        instance, _rule(), intent, actor_uid="p1", viewer_is_gm=False,
    )
    second = svc.resolve_combat_action(
        instance, _rule(), intent, actor_uid="p1", viewer_is_gm=False,
    )

    assert first["ok"] is True
    assert second == {**first, "replayed": True}
    assert instance.get_character_sheet("p1")["qi"] == 42
    assert instance.npcs["old_monk"]["hp"] == 4

    conflict = svc.resolve_combat_action(
        instance,
        _rule(),
        {**intent, "target_ids": ["player:p2"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    assert conflict["code"] == "INTENT_ID_CONFLICT"
    assert instance.get_character_sheet("p1")["qi"] == 42


def test_multi_target_or_malformed_target_intent_is_rejected() -> None:
    instance = _instance()

    repeated = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-many", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk", "npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    malformed = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-shape", "action_id": "ability:qi_palm",
         "target_ids": "npc:old_monk"},
        actor_uid="p1",
        viewer_is_gm=False,
    )

    assert repeated["code"] == "INVALID_TARGETS"
    assert malformed["code"] == "INVALID_TARGETS"
    assert instance.get_character_sheet("p1")["qi"] == 50
    assert instance.npcs["old_monk"]["hp"] == 14


def test_combat_writes_are_rejected_outside_action_phase() -> None:
    instance = _instance()
    instance.state = GameState.ACTIVE_JUDGMENT

    action = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-phase", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    advance = svc.scheduler_advance(instance, _rule())

    assert action["code"] == "ROUND_NOT_ACCEPTING_ACTIONS"
    assert advance["code"] == "ROUND_NOT_ACCEPTING_ACTIONS"
    assert instance.combat_extension == {}


def test_corrupt_idempotency_record_fails_closed_without_reexecuting() -> None:
    instance = _instance()
    intent = {
        "intent_id": "i-corrupt",
        "action_id": "ability:qi_palm",
        "target_ids": ["npc:old_monk"],
    }
    first = svc.resolve_combat_action(
        instance, _rule(), intent, actor_uid="p1", viewer_is_gm=False,
    )
    assert first["ok"] is True
    instance.combat_extension["intents"]["i-corrupt"]["result"] = {"ok": True}

    replay = svc.resolve_combat_action(
        instance, _rule(), intent, actor_uid="p1", viewer_is_gm=False,
    )

    assert replay["code"] == "INTENT_RECORD_INVALID"
    assert instance.get_character_sheet("p1")["qi"] == 42
    assert instance.npcs["old_monk"]["hp"] == 4


@pytest.mark.parametrize("schema_version", [True, "1", 2])
def test_unsupported_combat_state_schema_rejects_writes(
    schema_version: object,
) -> None:
    instance = _instance()
    instance.combat_extension = {
        "schema_version": schema_version,
        "pools": {},
    }

    result = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-version", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )

    assert result["code"] == "COMBAT_STATE_UNSUPPORTED"
    assert instance.get_character_sheet("p1")["qi"] == 50


def test_engaged_npc_joins_active_threshold_scheduler() -> None:
    instance = _instance()
    advanced = svc.scheduler_advance(instance, _rule())
    assert advanced["ok"] is True
    assert "npc:old_monk" not in instance.combat_extension["scheduler"]["gauges"]

    acted = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-enroll", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )

    assert acted["ok"] is True
    scheduler = instance.combat_extension["scheduler"]
    assert scheduler["gauges"]["npc:old_monk"] == 0
    assert "npc:old_monk" in scheduler["order"]


def test_engaged_npc_joins_active_round_robin_scheduler() -> None:
    instance = _instance()
    template = _template()
    template["combat"]["scheduler"] = {"kind": "round_robin"}
    rule = _FakeRule(template)
    assert svc.scheduler_advance(instance, rule)["ready"] == ["player:p1"]

    joined = svc.resolve_combat_action(
        instance,
        rule,
        {"intent_id": "i-rr-join", "action_id": "ability:escape_step",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    assert joined["ok"] is True
    assert "npc:old_monk" in instance.combat_extension["scheduler"]["order"]
    assert svc.combat_extension_projection(
        instance, rule, viewer_uid="p2", viewer_is_gm=False,
    )["scheduler"]["ready"] == ["player:p2"]

    next_turn = svc.resolve_combat_action(
        instance,
        rule,
        {"intent_id": "i-rr-next", "action_id": "ability:escape_step"},
        actor_uid="p2",
        viewer_is_gm=False,
    )
    assert next_turn["ok"] is True
    assert svc.combat_extension_projection(
        instance, rule, viewer_uid="gm", viewer_is_gm=True,
    )["scheduler"]["ready"] == ["npc:old_monk"]


def test_inventory_is_not_consumed_when_action_is_rejected() -> None:
    instance = _instance()
    sheet = instance.get_character_sheet("p1")
    sheet["inventory"] = [{"name": "回春丹", "qty": 1}]
    instance.set_character_sheet("p1", sheet)
    template = deepcopy(_template())
    template["combat"]["actions"].append({
        "id": "item:too_expensive",
        "kind": "consumable",
        "name": "昂贵丹药",
        "consume_item": {"item": "回春丹", "qty": 1},
        "costs": [{
            "resource": "qi",
            "amount": {"op": "constant", "value": 999},
        }],
        "effects": [{
            "kind": "resource_change",
            "resource": "hp",
            "amount": {"op": "constant", "value": 10},
        }],
    })

    result = svc.resolve_combat_action(
        instance,
        _FakeRule(template),
        {"intent_id": "i-atomic", "action_id": "item:too_expensive"},
        actor_uid="p1",
        viewer_is_gm=False,
    )

    assert result["code"] == "ACTION_REJECTED"
    assert instance.get_character_sheet("p1")["inventory"] == [
        {"name": "回春丹", "qty": 1},
    ]
    assert instance.get_character_sheet("p1")["qi"] == 50
    assert instance.combat_extension == {}
    assert instance.combat_extension_round_snapshots == {}


def test_character_sheet_update_reseeds_source_backed_pool() -> None:
    instance = _instance()
    first = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-sync-1", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    assert first["ok"] is True
    instance.get_character_sheet("p1")["qi"] = 50

    second = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-sync-2", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )

    assert second["ok"] is True
    assert instance.get_character_sheet("p1")["qi"] == 42
    assert instance.combat_extension["pools"]["player:p1"]["qi"]["current"] == 42


@pytest.mark.parametrize("payload", [
    {"pools": "bad", "scheduler": None},
    {"pools": {"player:p1": "bad"}, "scheduler": None},
    {"pools": {"player:p1": {"qi": "bad"}}, "scheduler": None},
    {"pools": {}, "scheduler": {"kind": "threshold", "gauges": "bad"}},
    {"pools": {}, "buffs": "bad", "scheduler": None},
])
def test_malformed_nested_state_fails_closed(payload: dict) -> None:
    instance = _instance()
    instance.combat_extension = payload

    projection = svc.combat_extension_projection(
        instance, _rule(), viewer_uid="p1", viewer_is_gm=False,
    )

    assert projection is not None
    assert projection["pools"]["player:p1"]["qi"]["current"] == 50


def test_malformed_scheduler_state_cannot_bypass_turn_gating() -> None:
    instance = _instance()
    instance.combat_extension = {
        "schema_version": 1,
        "scheduler": {"kind": "threshold", "gauges": "bad"},
    }

    result = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-bad-scheduler", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )

    assert result["code"] == "SCHEDULER_STATE_INVALID"
    assert instance.get_character_sheet("p1")["qi"] == 50


def test_malformed_character_records_do_not_crash_projection() -> None:
    instance = _instance()
    instance.players["p1"]["character_sheet"] = "malformed"
    instance.npcs["old_monk"] = "malformed"

    projection = svc.combat_extension_projection(
        instance, _rule(), viewer_uid="p1", viewer_is_gm=True,
    )

    assert projection is not None
    assert projection["pools"]["player:p1"]["hp"]["current"] == 0
    assert projection["entity_names"]["player:p1"] == "李逍遥"
    assert projection["entity_names"]["npc:old_monk"] == "old_monk"


@pytest.mark.asyncio
async def test_round_rollback_restores_combat_pools_source_fields_and_npc_hp() -> None:
    instance = _instance()
    instance.round_number = 3
    instance.log.append({
        "round": 3,
        "round_start_snapshot": {
            "p1": {"hp": 30, "max_hp": 30, "inventory": []},
            "p2": {"hp": 20, "max_hp": 20, "inventory": []},
        },
    })
    result = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-rollback", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    assert result["ok"] is True
    assert instance.get_character_sheet("p1")["qi"] == 42
    assert instance.npcs["old_monk"]["hp"] == 4

    assert await instance.rollback_last_round() == 3

    assert instance.combat_extension == {}
    assert instance.get_character_sheet("p1")["qi"] == 50
    assert instance.npcs["old_monk"]["hp"] == 14


@pytest.mark.asyncio
async def test_finished_round_rollback_restores_pre_combat_inventory_and_hp() -> None:
    instance = _instance()
    instance.round_number = 3
    instance.state = GameState.ACTIVE_ACTION
    sheet = instance.get_character_sheet("p1")
    sheet["hp"] = 25
    sheet["inventory"] = [{"name": "回春丹", "qty": 1}]
    instance.set_character_sheet("p1", sheet)

    healed = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-round-heal", "action_id": "item:healing_pill.use"},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    struck = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-round-hit", "action_id": "ability:qi_palm",
         "target_ids": ["npc:old_monk"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    assert healed["ok"] is True and struck["ok"] is True
    assert instance._do_advance_locked() is True
    await instance.finish_judgment("本轮结算")
    assert any("内力掌" in item for item in instance.log[-1]["state_changes"])
    assert "pending_summaries" not in instance.combat_extension

    # A user can already have acted in the newly opened round when the GM
    # rolls history back. Those unlogged writes must be undone as part of the
    # same branch cut before the finished round snapshot is restored.
    instance.npcs["bandit"] = {"name": "山贼", "hp": 20}
    current_round = svc.resolve_combat_action(
        instance,
        _rule(),
        {"intent_id": "i-current-round", "action_id": "ability:qi_palm",
         "target_ids": ["npc:bandit"]},
        actor_uid="p1",
        viewer_is_gm=False,
    )
    assert current_round["ok"] is True
    assert instance.npcs["bandit"]["hp"] == 10

    assert await instance.rollback_last_round() == 3
    restored = instance.get_character_sheet("p1")
    assert restored["hp"] == 25
    assert restored["qi"] == 50
    assert restored["inventory"] == [{"name": "回春丹", "qty": 1}]
    assert instance.npcs["old_monk"]["hp"] == 14
    assert instance.npcs["bandit"]["hp"] == 20
    assert "max_hp" not in instance.npcs["old_monk"]
    assert instance.combat_extension == {}


@pytest.mark.asyncio
async def test_facade_rejects_combat_write_during_historical_rewrite(
    web_api, monkeypatch,
) -> None:
    api, _lorebook, registry, _llm, _worlds = web_api
    instance = _instance()
    registry.register(instance)
    monkeypatch.setattr(api, "_load_rule_for_game", lambda _instance: _rule())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_rewrite() -> None:
        async with instance.historical_rewrite() as acquired:
            assert acquired is True
            entered.set()
            await release.wait()

    rewrite = asyncio.create_task(hold_rewrite())
    await entered.wait()
    try:
        result = await api.combat_extension_action(
            "web|wuxia|bot",
            {"intent_id": "i-locked", "action_id": "ability:qi_palm",
             "target_ids": ["npc:old_monk"]},
            session_uid="p1",
            viewer_is_gm=False,
        )
    finally:
        release.set()
        await rewrite

    assert result["code"] == "REWRITE_IN_PROGRESS"
    assert instance.get_character_sheet("p1")["qi"] == 50
    assert instance.combat_extension == {}


def test_unengaged_npc_still_targetable_and_named() -> None:
    """按需参与不能把未交战 NPC 从目标列表里删掉——否则永远无法开战。"""
    instance = _instance()
    projection = svc.combat_extension_projection(instance, _rule(), viewer_uid="p1", viewer_is_gm=False)
    assert "npc:old_monk" in projection["entities"]
    # 显示名映射：玩家显示角色名，NPC 显示名称。
    assert projection["entity_names"]["player:p1"] == "李逍遥"
    assert projection["entity_names"]["npc:old_monk"] == "老僧"
    # 但池子仍是按需的：GM 看到的池不含未交战 NPC。
    gm = svc.combat_extension_projection(instance, _rule(), viewer_uid="gm", viewer_is_gm=True)
    assert "npc:old_monk" not in gm["pools"]


@pytest.mark.asyncio
async def test_facade_action_path_is_usable(web_api) -> None:
    """门面层回归（真实 500 事故）：sync 服务被 await 会导致 TypeError。"""
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Facade Regression",
        players=[{"character_name": "Hero", "attributes": {"str": 10}}],
    )
    result = await api.combat_extension_action(
        created["game_key"],
        {"intent_id": "x", "action_id": "anything"},
        session_uid="gm",
        viewer_is_gm=True,
    )
    # template_world 未声明 combat：应得到显式"未配置"，而不是 500。
    assert result["ok"] is False
    assert result["code"] == "COMBAT_EXTENSION_NOT_CONFIGURED"
