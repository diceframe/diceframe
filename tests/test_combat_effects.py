"""通用战斗效果引擎的契约测试（Issue 212 PR 2）。"""

from __future__ import annotations

import json

import pytest

from src.engine.combat_contracts import CombatAction, EffectSpec, ResourceCost
from src.engine.combat_effects import (
    CombatActionError,
    CombatState,
    apply_combat_action,
    apply_effects,
    validate_effect_spec,
)
from src.engine.combat_formulas import FormulaContext
from src.engine.combat_resources import ResourcePool


def _state() -> CombatState:
    return CombatState(
        entities={
            "player:x": {
                "hp": ResourcePool("hp", 30, 30),
                "mana": ResourcePool("mana", 12, 20),
            },
            "player:y": {
                "hp": ResourcePool("hp", 20, 20),
                "mana": ResourcePool("mana", 0, 10),
                "barrier": ResourcePool("barrier", 15, 15),
            },
            "enemy:goblin": {
                "hp": ResourcePool("hp", 18, 18),
                "barrier": ResourcePool("barrier", 10, 10),
            },
        },
        hp_resource="hp",
        barriers={"barrier": None},
    )


def _context(target_id: str | None = "enemy:goblin") -> FormulaContext:
    return FormulaContext(
        attributes={"strength": 3, "intelligence": 4, "wisdom": 3},
        derived_stats={},
        resources={"mana": 12},
        equipment_stats={},
        actor_id="player:x",
        target_id=target_id,
    )


def _entity(state: CombatState, entity_id: str, resource: str) -> ResourcePool:
    return state.entities[entity_id][resource]


def _action(**overrides) -> CombatAction:
    values = {
        "action_id": "spell:fireball",
        "actor_id": "player:x",
        "target_ids": ("enemy:goblin",),
        "kind": "ability",
        "costs": (ResourceCost("mana", {"op": "constant", "value": 12}),),
        "effects": (
            EffectSpec(kind="damage", amount={"op": "constant", "value": 5},
                       damage_type="fire"),
        ),
    }
    values.update(overrides)
    return CombatAction(**values)


def test_plain_constant_damage_consumes_barrier_first() -> None:
    outcome = apply_effects(_state(), [
        EffectSpec(kind="damage", amount={"op": "constant", "value": 5},
                   damage_type="slashing"),
    ], _context(), source_action_id="attack:1")

    # 护盾先扣：barrier 10 -> 5，HP 不动。
    assert _entity(outcome.state, "enemy:goblin", "barrier").current == 5
    assert _entity(outcome.state, "enemy:goblin", "hp").current == 18
    events = outcome.events
    assert events[0]["type"] == "combat.resource_changed"
    assert events[0]["resource"] == "barrier"
    assert events[0]["delta"] == -5
    assert events[-1]["type"] == "combat.damage_applied"
    assert events[-1]["applied"] == 5
    json.dumps(list(events))  # 事件必须可序列化


def test_barrier_partial_absorb_then_hp() -> None:
    outcome = apply_effects(_state(), [
        EffectSpec(kind="damage", amount={"op": "constant", "value": 15},
                   damage_type="fire"),
    ], _context())

    assert _entity(outcome.state, "enemy:goblin", "barrier").current == 0
    assert _entity(outcome.state, "enemy:goblin", "hp").current == 13
    damage_event = outcome.events[-1]
    assert damage_event["applied"] == 15


def test_barrier_zero_goes_straight_to_hp() -> None:
    state = _state()
    drained = CombatState(
        entities={
            **state.entities,
            "enemy:goblin": {
                "hp": ResourcePool("hp", 18, 18),
                "barrier": ResourcePool("barrier", 0, 10),
            },
        },
        hp_resource="hp",
        barriers={"barrier": None},
    )
    outcome = apply_effects(drained, [
        EffectSpec(kind="damage", amount={"op": "constant", "value": 4}),
    ], _context())
    assert _entity(outcome.state, "enemy:goblin", "barrier").current == 0
    assert _entity(outcome.state, "enemy:goblin", "hp").current == 14


def test_barrier_respects_damage_type_filter() -> None:
    state = CombatState(
        entities=_state().entities,
        hp_resource="hp",
        barriers={"barrier": frozenset({"fire"})},
    )
    outcome = apply_effects(state, [
        EffectSpec(kind="damage", amount={"op": "constant", "value": 4},
                   damage_type="slashing"),
    ], _context())
    # 物理伤害绕过只吸收火焰的护盾。
    assert _entity(outcome.state, "enemy:goblin", "barrier").current == 10
    assert _entity(outcome.state, "enemy:goblin", "hp").current == 14


def test_damage_clamps_at_hp_minimum() -> None:
    outcome = apply_effects(_state(), [
        EffectSpec(kind="damage", amount={"op": "constant", "value": 999}),
    ], _context())
    assert _entity(outcome.state, "enemy:goblin", "hp").current == 0
    # 总生效伤害 = 护盾吸收 10 + HP 实扣 18。
    assert outcome.events[-1]["applied"] == 28


def test_heal_does_not_restore_barrier() -> None:
    state = _state()
    damaged = apply_effects(state, [
        EffectSpec(kind="damage", amount={"op": "constant", "value": 6}),
    ], _context()).state
    # 6 点伤害被 10 点护盾全吸收：barrier 10->4，HP 仍是满的 18。
    assert _entity(damaged, "enemy:goblin", "barrier").current == 4
    healed = apply_effects(damaged, [
        EffectSpec(kind="resource_change", resource="hp",
                   amount={"op": "constant", "value": 4}),
    ], _context(target_id="enemy:goblin"))
    # HP 已满：治疗被 clamp 在 maximum，绝不溢出恢复护盾。
    assert _entity(healed.state, "enemy:goblin", "hp").current == 18
    assert _entity(healed.state, "enemy:goblin", "barrier").current == 4
    assert healed.state.entities["enemy:goblin"]["hp"].maximum == 18


def test_resource_change_on_self_clamps_and_drains_to_minimum() -> None:
    outcome = apply_effects(_state(), [
        EffectSpec(kind="resource_change", resource="mana",
                   amount={"op": "constant", "value": 99}),
    ], _context(target_id="player:x"))
    assert _entity(outcome.state, "player:x", "mana").current == 20  # clamp 到 maximum

    drained = apply_effects(_state(), [
        EffectSpec(kind="resource_change", resource="mana",
                   amount={"op": "constant", "value": -999}),
    ], _context(target_id="player:x"))
    assert _entity(drained.state, "player:x", "mana").current == 0


def test_action_costs_and_damage_commit_together() -> None:
    outcome = apply_combat_action(_state(), _action(), _context())
    assert _entity(outcome.state, "player:x", "mana").current == 0
    assert _entity(outcome.state, "enemy:goblin", "barrier").current == 5
    kinds = [event["type"] for event in outcome.events]
    assert kinds[0] == "combat.resource_changed"  # 先是施法者的 mana 消耗
    assert "combat.damage_applied" in kinds


def test_insufficient_cost_rejects_whole_action_without_partial_state() -> None:
    state = _state()
    broke = CombatState(
        entities={
            **state.entities,
            "player:x": {"hp": ResourcePool("hp", 30, 30),
                          "mana": ResourcePool("mana", 3, 20)},
        },
        hp_resource="hp",
        barriers={"barrier": None},
    )
    with pytest.raises(CombatActionError):
        apply_combat_action(broke, _action(), _context())
    # 原状态对象完全未变（调用方保留旧状态即为回滚）。
    assert broke.entities["player:x"]["mana"].current == 3
    assert broke.entities["enemy:goblin"]["barrier"].current == 10


def test_effect_formula_failure_rejects_before_costs_commit() -> None:
    bad_action = _action(effects=(
        EffectSpec(kind="damage", amount={"op": "attribute", "id": "charisma"}),
    ))
    with pytest.raises(CombatActionError):
        apply_combat_action(_state(), bad_action, _context())
    # 公式求值发生在资源提交之前：mana 分文未扣。
    assert _entity(_state(), "player:x", "mana").current == 12


def test_unsupported_effect_kinds_are_rejected_explicitly() -> None:
    for kind in ("apply_status", "remove_status", "barrier",
                 "advance_scheduler"):
        with pytest.raises(CombatActionError, match="not supported"):
            apply_combat_action(_state(), _action(effects=(
                EffectSpec(kind=kind, amount={"op": "constant", "value": 1}),
            )), _context())


def test_capability_bounds_reject_undeclared_resources_and_damage_types() -> None:
    with pytest.raises(CombatActionError, match="undeclared resource"):
        apply_combat_action(_state(), _action(costs=(
            ResourceCost("qigong", {"op": "constant", "value": 1}),
        )), _context(), allowed_resources=frozenset({"mana"}))
    with pytest.raises(CombatActionError, match="undeclared damage type"):
        apply_combat_action(_state(), _action(effects=(
            EffectSpec(kind="damage", amount={"op": "constant", "value": 1},
                       damage_type="psychic"),
        )), _context(), allowed_damage_types=frozenset({"fire"}))


def test_multi_target_damage_consumes_barriers_independently() -> None:
    action = _action(
        target_ids=("enemy:goblin", "player:y"),
        effects=(EffectSpec(kind="damage", amount={"op": "constant", "value": 12},
                            damage_type="fire"),),
        costs=(),
    )
    outcome = apply_combat_action(_state(), action, _context())
    # enemy:goblin：barrier 10 + hp 2；player:y：barrier 15 全吸收。
    assert _entity(outcome.state, "enemy:goblin", "barrier").current == 0
    assert _entity(outcome.state, "enemy:goblin", "hp").current == 16
    assert _entity(outcome.state, "player:y", "barrier").current == 3
    assert _entity(outcome.state, "player:y", "hp").current == 20


def test_multi_target_dice_damage_rolls_once_for_all_targets() -> None:
    action = _action(
        target_ids=("player:y", "enemy:goblin"),
        effects=(EffectSpec(kind="damage", amount={
            "op": "add", "args": [
                {"op": "dice", "formula": "1d6"},
                {"op": "constant", "value": 2},
            ]}),),
        costs=(),
    )
    outcome = apply_combat_action(_state(), action, _context())
    damage_events = [e for e in outcome.events if e["type"] == "combat.damage_applied"]
    assert [e["target_id"] for e in damage_events] == ["player:y", "enemy:goblin"]
    assert damage_events[0]["amount"] == damage_events[1]["amount"]
    assert 3 <= damage_events[0]["amount"] <= 8


def test_unknown_target_or_actor_rejects_action() -> None:
    with pytest.raises(CombatActionError, match="unknown combat entity"):
        apply_combat_action(_state(), _action(target_ids=("enemy:ghost",)), _context())
    with pytest.raises(CombatActionError, match="unknown combat entity"):
        apply_combat_action(
            _state(), _action(), _context(target_id=None).__class__(  # type: ignore[arg-type]
                attributes={}, derived_stats={}, resources={},
                equipment_stats={}, actor_id="player:ghost",
            ),
        )


def test_validate_effect_spec_bounds() -> None:
    spec = validate_effect_spec(
        {"kind": "damage", "amount": {"op": "constant", "value": 3},
         "damage_type": "fire"},
        allowed_damage_types=frozenset({"fire", "slashing"}),
    )
    assert spec.kind == "damage"
    with pytest.raises(CombatActionError, match="unknown effect kind"):
        validate_effect_spec({"kind": "fireball"})
    with pytest.raises(CombatActionError, match="must be a formula object"):
        validate_effect_spec({"kind": "damage", "amount": 5})
    with pytest.raises(CombatActionError, match="undeclared damage type"):
        validate_effect_spec(
            {"kind": "damage", "amount": {"op": "constant", "value": 1},
             "damage_type": "psychic"},
            allowed_damage_types=frozenset({"fire"}),
        )


def test_modify_stat_applies_buff_with_duration() -> None:
    """遁术类效果：modify_stat 挂 buff，时长必填、零变化拒绝。"""
    outcome = apply_effects(_state(), [
        EffectSpec(kind="modify_stat", resource="action_speed",
                   amount={"op": "constant", "value": 20}, duration=1),
    ], _context(target_id="player:x"), source_action_id="ability:escape_step")
    assert outcome.state.buffs == (
        {"entity_id": "player:x", "stat": "action_speed",
         "delta": 20, "remaining": 1},
    )
    assert outcome.events[-1]["type"] == "combat.buff_applied"

    with pytest.raises(CombatActionError, match="positive duration"):
        apply_effects(_state(), [
            EffectSpec(kind="modify_stat", resource="action_speed",
                       amount={"op": "constant", "value": 20}),
        ], _context(target_id="player:x"))
    with pytest.raises(CombatActionError, match="evaluated to zero"):
        apply_effects(_state(), [
            EffectSpec(kind="modify_stat", resource="action_speed",
                       amount={"op": "constant", "value": 0}, duration=2),
        ], _context(target_id="player:x"))


def test_action_level_modify_stat_appends_buff_per_target() -> None:
    action = CombatAction(
        action_id="ability:escape_step", actor_id="player:x",
        target_ids=("player:x",), kind="ability",
        effects=(EffectSpec(kind="modify_stat", resource="action_speed",
                            amount={"op": "constant", "value": 20}, duration=1),),
    )
    outcome = apply_combat_action(_state(), action, _context(target_id="player:x"))
    assert len(outcome.state.buffs) == 1
    assert outcome.state.buffs[0]["entity_id"] == "player:x"
