"""Issue 212 core acceptance: ATB action gauge + attribute-driven action damage.

`#212` (a cultivation-style ruleset author) reported two blockers:

1. there is no action-speed model, so "speed accumulates to 100 and then you
   act" (ATB) and "escape techniques raise action speed" were impossible;
2. damage could only come from ``weapon.damage`` — no action/spell could
   declare a damage formula tied to the caster's own attributes.

Both were answered by the generic combat extension (ADR 0004). These tests walk
the whole chain with a real rule file instead of a hand-built config dict:

    rule definition -> RuleSystem.load -> extension config
        -> action executable -> scheduler advance -> formula settlement
        -> authoritative state (character sheet + pools + action gauge)
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.rules.rule_system import RuleSystem
from src.webui.services import combat_extension as svc
from src.engine.modules import combat_extension_state

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "rules" / "atb_cultivation.json"


@pytest.fixture
def rule() -> RuleSystem:
    """The #212 scenario as a real, loadable rule file (fixture, not product)."""

    return RuleSystem.load(FIXTURE)


def make_instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "atb", "bot"), gm_uid="gm")
    instance.state = GameState.ACTIVE_ACTION
    instance.players = {
        "p1": {"character_name": "李逍遥", "character_sheet": {
            "hp": 30, "max_hp": 30, "ling_li": 40, "max_ling_li": 100,
            # 身法 25 → 行动速度 100（一 tick 满条）；悟性 4 → 火球 12 伤害。
            "attributes": {"wu_xing": 4, "shen_fa": 25, "str": 3, "con": 2},
        }},
        "p2": {"character_name": "赵灵儿", "character_sheet": {
            "hp": 20, "max_hp": 20, "ling_li": 30, "max_ling_li": 100,
            # 身法 10 → 行动速度 40；悟性 7 → 火球 21 伤害。
            "attributes": {"wu_xing": 7, "shen_fa": 10, "str": 2, "con": 2},
        }},
    }
    instance.npcs["old_monk"] = {
        "name": "老僧", "hp": 30, "max_hp": 30,
        "attributes": {"wu_xing": 1, "shen_fa": 5, "str": 1, "con": 1},
    }
    return instance


def advance(instance: GameInstance, rule: RuleSystem) -> dict:
    result = svc.scheduler_advance(instance, rule)
    assert result["ok"] is True, result
    return result


def advance_until_ready(
    instance: GameInstance, rule: RuleSystem, actor_id: str, *, limit: int = 12,
) -> dict:
    for _ in range(limit):
        result = advance(instance, rule)
        if actor_id in result["ready"]:
            return result
    raise AssertionError(f"{actor_id} never became ready")


_INTENT_COUNTER = itertools.count(1)


def cast(
    instance: GameInstance, rule: RuleSystem, actor_uid: str, action_id: str,
    *, target_ids: list[str] | None = None,
) -> dict:
    intent: dict = {"intent_id": f"intent-{next(_INTENT_COUNTER)}", "action_id": action_id}
    if target_ids is not None:
        intent["target_ids"] = target_ids
    return svc.resolve_combat_action(
        instance, rule, intent, actor_uid=actor_uid, viewer_is_gm=False,
    )


def test_rule_declaration_reaches_the_runtime_projection(
    rule: RuleSystem,
) -> None:
    instance = make_instance()

    projection = svc.combat_extension_projection(
        instance, rule, viewer_uid="p1", viewer_is_gm=False,
    )

    assert projection is not None
    assert projection["scheduler"]["kind"] == "threshold"
    assert [action["id"] for action in projection["actions"]] == [
        "spell:fireball", "technique:escape_light",
    ]
    assert projection["actions"][0]["costs"] == [
        {"resource": "ling_li", "amount": {"op": "constant", "value": 10}},
    ]
    # 资源池只对本人可见；调度器（行动条）是桌面公共信息。
    assert set(projection["pools"]) == {"player:p1"}
    assert projection["pools"]["player:p1"]["ling_li"] == {
        "current": 40, "maximum": 100,
    }


def test_action_gauge_accumulates_by_the_actor_attribute(rule: RuleSystem) -> None:
    instance = make_instance()

    first = advance(instance, rule)

    # 行动速度 = 身法 × 4：李逍遥 100/一 tick，赵灵儿 40/一 tick。
    assert first["gauges"] == {"player:p1": 100, "player:p2": 40}
    # 只有攒满 100 的人就绪；慢的一方还在条上。
    assert first["ready"] == ["player:p1"]

    second = advance(instance, rule)

    # 时间推进到"下一个就绪事件"：赵灵儿按 40/tick 再积两 tick 到 120。
    assert second["gauges"] == {"player:p1": 100, "player:p2": 120}
    assert second["ready"] == ["player:p1", "player:p2"]


def test_scheduler_gates_actions_until_the_gauge_is_full(rule: RuleSystem) -> None:
    instance = make_instance()
    advance(instance, rule)

    blocked = cast(instance, rule, "p2", "spell:fireball", target_ids=["npc:old_monk"])

    assert blocked["ok"] is False
    assert blocked["error_code"] == "SCHEDULER_NOT_READY"
    assert instance.get_character_sheet("p2")["ling_li"] == 30
    assert instance.npcs["old_monk"]["hp"] == 30

    advance_until_ready(instance, rule, "player:p2")
    allowed = cast(instance, rule, "p2", "spell:fireball", target_ids=["npc:old_monk"])

    assert allowed["ok"] is True


def test_spell_damage_comes_from_the_caster_attribute_formula(
    rule: RuleSystem,
) -> None:
    instance = make_instance()
    advance(instance, rule)

    result = cast(instance, rule, "p1", "spell:fireball", target_ids=["npc:old_monk"])

    # 火球 = 悟性 × 3 = 4 × 3；灵力按声明扣 10 并写回角色卡（单一权威）。
    assert result["ok"] is True
    damage = next(
        event for event in result["events"] if event["type"] == "combat.damage_applied"
    )
    assert damage["amount"] == 12 and damage["applied"] == 12
    assert damage["damage_type"] == "fire"
    assert instance.get_character_sheet("p1")["ling_li"] == 30
    assert combat_extension_state.current(instance)["pools"]["player:p1"]["ling_li"]["current"] == 30
    assert instance.npcs["old_monk"]["hp"] == 18


def test_the_same_action_damages_differently_per_caster(rule: RuleSystem) -> None:
    """公式绑定 caster 属性：同一 action_id 在不同角色身上结果不同。"""

    instance = make_instance()
    advance_until_ready(instance, rule, "player:p1")
    first = cast(instance, rule, "p1", "spell:fireball", target_ids=["npc:old_monk"])
    advance_until_ready(instance, rule, "player:p2")
    second = cast(instance, rule, "p2", "spell:fireball", target_ids=["npc:old_monk"])

    first_damage = next(
        event for event in first["events"] if event["type"] == "combat.damage_applied"
    )
    assert second["ok"] is True
    damage = next(
        event for event in second["events"] if event["type"] == "combat.damage_applied"
    )
    assert first_damage["amount"] == 12  # 李逍遥 悟性 4 × 3
    assert damage["amount"] == 21  # 赵灵儿 悟性 7 × 3
    assert instance.get_character_sheet("p2")["ling_li"] == 20


def test_action_consumes_the_action_gauge_turn(rule: RuleSystem) -> None:
    instance = make_instance()
    advance(instance, rule)

    first = cast(instance, rule, "p1", "spell:fireball", target_ids=["npc:old_monk"])
    assert first["ok"] is True
    assert combat_extension_state.current(instance)["scheduler"]["gauges"]["player:p1"] == 0
    assert combat_extension_state.current(instance)["scheduler"]["ready"] == []

    again = cast(instance, rule, "p1", "spell:fireball", target_ids=["npc:old_monk"])

    assert again["ok"] is False and again["error_code"] == "SCHEDULER_NOT_READY"


def test_speed_technique_lands_in_the_action_gauge(rule: RuleSystem) -> None:
    """遁术提升行动速度：buff 直接改变下一次 ATB 推进的 gauge。"""

    instance = make_instance()
    advance(instance, rule)
    buffed = cast(instance, rule, "p1", "technique:escape_light")

    assert buffed["ok"] is True
    assert instance.get_character_sheet("p1")["ling_li"] == 35
    buffs = combat_extension_state.current(instance)["buffs"]
    assert buffs == [{"entity_id": "player:p1", "stat": "action_speed",
                      "delta": 40, "remaining": 2}]

    # 无 buff 时李逍遥一 tick 只能积 100；遁术生效后同一 tick 积 140。
    ticked = advance(instance, rule)

    assert ticked["gauges"]["player:p1"] == 140
    assert "player:p1" in ticked["ready"]
    assert combat_extension_state.current(instance)["buffs"][0]["remaining"] == 1


def test_combat_state_survives_save_reload_round_trip(rule: RuleSystem) -> None:
    instance = make_instance()
    advance(instance, rule)
    cast(instance, rule, "p1", "spell:fireball", target_ids=["npc:old_monk"])
    advance(instance, rule)

    recovered = GameInstance.from_dict(instance.to_dict())

    assert combat_extension_state.current(recovered) == combat_extension_state.current(instance)
    assert recovered.get_character_sheet("p1")["ling_li"] == 30
    assert recovered.npcs["old_monk"]["hp"] == 18
    projection = svc.combat_extension_projection(
        recovered, rule, viewer_uid="p1", viewer_is_gm=False,
    )
    assert projection is not None
    assert projection["scheduler"]["gauges"] == {
        "player:p1": 100, "player:p2": 80, "npc:old_monk": 20,
    }
    # 重载后同一权威链继续可用（李逍遥已就绪）。
    assert "player:p1" in projection["scheduler"]["ready"]

    continued = cast(recovered, rule, "p1", "spell:fireball", target_ids=["npc:old_monk"])

    assert continued["ok"] is True
    assert recovered.npcs["old_monk"]["hp"] == 6


def test_rule_without_combat_declaration_keeps_the_legacy_turn_flow() -> None:
    """没有声明 combat 块的规则不会被猜测升级成 ATB（fail closed）。"""

    plain_rule = RuleSystem.load(ROOT / "templates" / "rules" / "base_d20.json")
    instance = make_instance()

    assert svc.combat_extension_projection(
        instance, plain_rule, viewer_uid="p1", viewer_is_gm=False,
    ) is None
    result = svc.resolve_combat_action(
        instance, plain_rule,
        {"intent_id": "legacy-1", "action_id": "spell:fireball"},
        actor_uid="p1", viewer_is_gm=False,
    )
    assert result["error_code"] == "COMBAT_EXTENSION_NOT_CONFIGURED"
    assert "combat_extension" not in instance.to_dict()["modules"] or not combat_extension_state.current(instance)
