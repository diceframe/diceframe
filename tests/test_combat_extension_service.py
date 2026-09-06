"""战斗扩展服务层契约测试（Issue 212 phase 2 Slice 2.3）。"""

from __future__ import annotations

import pytest

from src.engine.game_instance import GameInstance
from src.webui.services import combat_extension as svc


class _FakeRule:
    def __init__(self, template: dict) -> None:
        self.template = template


def _template() -> dict:
    return {
        "rule_id": "freeform_wuxia",
        "combat": {
            "scheduler": {"kind": "threshold", "gauge": "action_gauge",
                          "speed": "action_speed", "threshold": 100},
            "resources": [
                {"id": "hp", "source": "hp"},
                {"id": "qi", "source": "special_stat", "stat": "qi", "maximum": 100},
                {"id": "barrier", "source": "combat_state", "maximum": 30,
                 "damage_priority": "before_hp"},
            ],
            "actions": [
                {"id": "item:healing_pill.use", "kind": "consumable", "name": "回春丹",
                 "effects": [{"kind": "resource_change", "resource": "hp",
                              "amount": {"op": "constant", "value": 10}}]},
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
        "item:healing_pill.use", "ability:qi_palm",
    ]
    assert set(projection["pools"]) == {"player:p1"}
    assert projection["pools"]["player:p1"]["qi"] == {"current": 50, "maximum": 100}
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
    damage = [e for e in result["events"] if e["type"] == "combat.damage_applied"]
    assert damage and damage[0]["applied"] == 10


def test_healing_pill_restores_hp_with_maximum_clamp() -> None:
    instance = _instance()
    instance.get_character_sheet("p1")["hp"] = 25
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
    result = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-5", "action_id": "item:healing_pill.use",
         "target_ids": ["player:p1"], "actor_id": "npc:old_monk"},
        actor_uid="gm", viewer_is_gm=True,
    )
    assert result["ok"] is True
    assert instance.get_character_sheet("p1")["hp"] == 30  # 22 + 10 → clamp

    # NPC 的内力池播种为 0：需要内力的动作会被资源校验拒绝（fail closed）。
    broke = svc.resolve_combat_action(
        instance, _rule(),
        {"intent_id": "i-5b", "action_id": "ability:qi_palm", "target_ids": ["player:p1"],
         "actor_id": "npc:old_monk"},
        actor_uid="gm", viewer_is_gm=True,
    )
    assert broke["code"] == "ACTION_REJECTED"


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


@pytest.mark.parametrize("intent", [
    {"intent_id": "x"},
    {"intent_id": "x", "action_id": ""},
])
def test_malformed_intents_fail(intent: dict) -> None:
    result = svc.resolve_combat_action(
        _instance(), _rule(), intent, actor_uid="p1", viewer_is_gm=False,
    )
    assert result["ok"] is False
