"""S0-SP3: a spell can leave something behind even when the save succeeds.

`ray_of_enfeeblement` is the case: on a failed save the target is enfeebled for
a minute, and on a *successful* save it still has disadvantage on its next
attack roll until the start of your next turn. Before this slice a successful
save meant the spell did nothing at all.
"""

from __future__ import annotations

import copy

import pytest

from src.rulesets.dnd2024.combat.catalog import (
    CombatCatalogError,
    Dnd2024CombatCatalog,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from test_dnd2024_sp2_condition_depth import _cast, _combat, _enemy_conditions


FOCUS = "spell:ray_of_enfeeblement"


def test_a_successful_save_still_imposes_the_next_attack_penalty() -> None:
    _, engine, instance = _combat(focus=FOCUS)

    events = _cast(engine, instance, FOCUS, 2, ["enemy:goblin-1"], rolls=[20])

    applied = [e for e in events if e["type"] == "condition.applied"]
    assert [e["condition"] for e in applied] == ["next_attack_disadvantage"]
    assert applied[0]["duration"] == "caster_next_turn_end"
    assert "enfeebled" not in _enemy_conditions(instance)


def test_a_failed_save_imposes_enfeebled_and_not_the_penalty() -> None:
    _, engine, instance = _combat(focus=FOCUS)

    _cast(engine, instance, FOCUS, 2, ["enemy:goblin-1"], rolls=[1])

    conditions = _enemy_conditions(instance)
    assert "enfeebled" in conditions
    assert "next_attack_disadvantage" not in conditions


def test_the_success_condition_keeps_the_caster_as_its_source() -> None:
    """专注归属与解除魔法的来源快照都走同一条路，不能因为分支不同就丢。"""

    _, engine, instance = _combat(focus=FOCUS)

    events = _cast(engine, instance, FOCUS, 2, ["enemy:goblin-1"], rolls=[20])

    applied = next(e for e in events if e["type"] == "condition.applied")
    assert applied["source_actor_id"] == "player:gm"
    assert applied["source_spell_ref"] == FOCUS
    assert applied["concentration_owner"] == "player:gm"


def _patched_bundle(base, effect_override: dict):
    """Same shape `test_dnd2024_invisibility` uses to test load-time rejection."""

    class _Bundle:
        def get(self, kind, ident=None):
            if kind == "combat_catalog":
                raw = copy.deepcopy(base.get(kind, ident))
                effects = dict(raw["spell_effects"])
                effects["ray_of_enfeeblement"] = {
                    **effects["ray_of_enfeeblement"], **effect_override,
                }
                raw["spell_effects"] = effects
                return raw
            return base.get(kind, ident)

        def list(self, kind):
            return base.list(kind)

    return _Bundle()


@pytest.mark.parametrize("bad", [
    {"condition": "next_attack_disadvantage"},                      # 没有时长
    {"condition": "", "duration": "caster_next_turn_end"},           # 没有条件
    {"condition": "next_attack_disadvantage", "duration": "x", "extra": 1},  # 多键
    "next_attack_disadvantage",                                       # 裸字符串
])
def test_a_malformed_success_branch_is_rejected_at_load(bad) -> None:
    """成功分支的声明必须是 {condition, duration} 两键齐备——否则作者在猜。"""

    bundle = Dnd2024Runtime().load_bundle("en")
    patched = _patched_bundle(
        bundle, {"condition_on_success": bad},
    )

    with pytest.raises(CombatCatalogError):
        Dnd2024CombatCatalog.from_bundle(patched)
