"""F38：武器基本词条（Two-Handed / Loading）、在手状态与 Light 追加攻击。

这一组测试只走权威链：真实 bundle → 角色卡 → combat.start → attack intent。
它要守住四件事：

1. 双手武器拿在盾牌旁边时**不可攻击**（数据说它是 Two-Handed，就必须真给两只手）。
2. 骑枪的骑乘例外只对"确实骑乘"的角色生效。
3. 换手是"每次攻击一次"的声明，且只改这一场的握持，不改装备栏。
4. Light 追加攻击花 Bonus Action，默认不吃属性调整值，Two-Weapon Fighting 才给。

D&D 2024 没有"副手武器"这个概念：本测试里也不会出现 off-hand 语义。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import SequenceRng, goblin, monk_instance


LOCALE = "en"


# --------------------------------------------------------------------------
# fixtures: 一个真实角色卡，装备与专长按测试需要改写
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _sheet(
    runtime: Dnd2024Runtime, *, equipment: list[str], two_weapon_fighting: bool = False,
) -> dict:
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": LOCALE, "name": "Grip"},
    )
    canonical = sheet["ruleset_character"]
    canonical["equipment"]["item_refs"] = [f"item:{item}" for item in equipment]
    if two_weapon_fighting:
        features = canonical.setdefault("features", {})
        refs = features.setdefault("feat_refs", [])
        if "feat:two_weapon_fighting" not in refs:
            refs.append("feat:two_weapon_fighting")
    return sheet


def _start(
    runtime: Dnd2024Runtime, sheet: dict, *, mounted: list[str] | None = None,
):
    engine, instance = monk_instance(runtime, sheet, uid="gm")
    intent = {
        "intent_id": "start-hands", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(hp=100)],
    }
    if mounted is not None:
        intent["mounted_player_ids"] = mounted
    resolved = engine.resolve_intent(instance, intent, SequenceRng([20, 1]))
    assert resolved["ok"], resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"]
    return engine, instance


def _combat(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _turn(engine, instance, actor_id: str = "player:gm") -> None:
    combat = _combat(instance)
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(
        engine._actor_view(instance, combat, actor_id)
    )


def _attack_intent(instance, *, weapon: str, **extra) -> dict:
    version = ruleset_runtime.state(instance)["version"]
    payload = {
        # 同一把武器可能在同一回合被攻击多次（Extra Attack / Loading 校验），
        # intent_id 必须唯一，否则会被当成本请求的重放。
        "intent_id": f"attack:{weapon}:{version}", "type": "attack", "submitted_by": "gm",
        "actor_id": "player:gm", "target_id": "enemy:goblin-1",
        "weapon_ref": f"item:{weapon}", "expected_version": version,
    }
    payload.update(extra)
    return payload


def _resolve(engine, instance, payload: dict, rolls=(15, 4)):
    return engine.resolve_intent(
        instance, payload, SequenceRng(list(rolls)),
    )


def _apply(engine, instance, result: dict) -> dict:
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"], applied
    return applied


def _damage_event(batch: dict) -> dict:
    return next(
        event for event in batch["events"]
        if event["type"] == "resource.changed" and event["resource"] == "hp"
    )


# --------------------------------------------------------------------------
# 1. Two-Handed：数据说必须双手，就必须真给两只手
# --------------------------------------------------------------------------


def test_two_handed_weapon_beside_a_shield_is_rejected(runtime) -> None:
    engine, instance = _start(runtime, _sheet(runtime, equipment=["greatsword", "shield"]))
    _turn(engine, instance)

    result = _resolve(engine, instance, _attack_intent(instance, weapon="greatsword"))

    assert result["ok"] is False
    assert "a shield in the other hand blocks two-handed use" in result["error"]


def test_mounted_lance_ignores_the_shield(runtime) -> None:
    engine, instance = _start(
        runtime, _sheet(runtime, equipment=["lance", "shield"]), mounted=["gm"],
    )
    _turn(engine, instance)

    result = _resolve(engine, instance, _attack_intent(instance, weapon="lance"))

    assert result["ok"] is True, result


def test_unmounted_lance_still_needs_both_hands(runtime) -> None:
    engine, instance = _start(runtime, _sheet(runtime, equipment=["lance", "shield"]))
    _turn(engine, instance)

    result = _resolve(engine, instance, _attack_intent(instance, weapon="lance"))

    assert result["ok"] is False
    assert "two-handed" in result["error"]


# --------------------------------------------------------------------------
# 2. 换手：一次攻击一次，且只改这一场的握持
# --------------------------------------------------------------------------


def test_stowing_a_two_handed_weapon_frees_both_hands(runtime) -> None:
    engine, instance = _start(
        runtime, _sheet(runtime, equipment=["shortsword", "greatsword"]),
    )
    _turn(engine, instance)

    result = _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="greatsword",
            weapon_swap={"stow": "item:greatsword"},
        ),
    )
    _apply(engine, instance, result)

    hands = _combat(instance)["hands"]["player:gm"]
    assert hands == {"left": None, "right": None}


def test_two_operations_in_one_swap_are_rejected(runtime) -> None:
    engine, instance = _start(
        runtime, _sheet(runtime, equipment=["shortsword", "greatsword"]),
    )
    _turn(engine, instance)

    result = _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="shortsword",
            weapon_swap={"stow": "item:greatsword", "draw": "item:shortsword"},
        ),
    )

    assert result["ok"] is False
    assert "exactly one of draw or stow" in result["error"]


def test_drawing_a_carried_weapon_takes_a_free_hand(runtime) -> None:
    engine, instance = _start(
        runtime, _sheet(runtime, equipment=["shortsword", "greatsword"]),
    )
    _turn(engine, instance)

    # 双手剑占着两只手；先把它收起，再用下一次攻击拔出短剑。
    _apply(engine, instance, _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="greatsword",
            weapon_swap={"stow": "item:greatsword"},
        ),
    ))
    _turn(engine, instance)
    _apply(engine, instance, _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="shortsword",
            weapon_swap={"draw": "item:shortsword"},
        ),
    ))

    hands = _combat(instance)["hands"]["player:gm"]
    assert hands["right"] == "item:shortsword" or hands["left"] == "item:shortsword"


def test_shield_cannot_be_stowed_as_part_of_an_attack(runtime) -> None:
    engine, instance = _start(runtime, _sheet(runtime, equipment=["greatsword", "shield"]))
    _turn(engine, instance)

    result = _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="greatsword",
            weapon_swap={"stow": "item:shield"},
        ),
    )

    assert result["ok"] is False
    assert "only a weapon can be stowed" in result["error"]


def test_equipment_is_never_rewritten_by_a_swap(runtime) -> None:
    engine, instance = _start(
        runtime, _sheet(runtime, equipment=["shortsword", "greatsword"]),
    )
    _turn(engine, instance)
    before = list(
        instance.get_character_sheet("gm")["ruleset_character"]["equipment"]["item_refs"]
    )

    _apply(engine, instance, _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="greatsword",
            weapon_swap={"stow": "item:greatsword"},
        ),
    ))

    after = instance.get_character_sheet("gm")["ruleset_character"]["equipment"]["item_refs"]
    assert after == before


# --------------------------------------------------------------------------
# 3. Light 追加攻击
# --------------------------------------------------------------------------


@dataclass
class _Inputs:
    engine: object
    instance: object


def _two_light_weapons(runtime, *, style: bool) -> _Inputs:
    engine, instance = _start(
        runtime,
        _sheet(runtime, equipment=["dagger", "shortsword"], two_weapon_fighting=style),
    )
    _turn(engine, instance)
    return _Inputs(engine, instance)


@pytest.mark.parametrize("style", [False, True])
def test_light_extra_attack_damage_follows_the_fighting_style(runtime, style: bool) -> None:
    inputs = _two_light_weapons(runtime, style=style)
    engine, instance = inputs.engine, inputs.instance

    # 先用短剑执行 Attack action：它既是追加攻击的前提，也给出"普通攻击里属性
    # 调整值是多少"的权威参照——不在这里复算伤害公式。
    normal = _resolve(engine, instance, _attack_intent(instance, weapon="shortsword"))
    _apply(engine, instance, normal)
    normal_damage = _damage_event(normal["event_batch"])
    ability_bonus = normal_damage["amount"] - sum(normal_damage["rolls"])
    assert ability_bonus > 0

    # 追加攻击必须用**另一把** Light 武器，所以换成匕首。
    _turn(engine, instance)
    result = _resolve(
        engine, instance,
        _attack_intent(instance, weapon="dagger", light_extra_attack=True),
    )
    _apply(engine, instance, result)
    damage = _damage_event(result["event_batch"])

    expected_bonus = ability_bonus if style else 0
    assert damage["amount"] == sum(damage["rolls"]) + expected_bonus
    assert _combat(instance)["economy"]["bonus_action"] == 0


def test_light_extra_attack_requires_a_prior_light_attack(runtime) -> None:
    inputs = _two_light_weapons(runtime, style=False)
    engine, instance = inputs.engine, inputs.instance

    result = _resolve(
        engine, instance,
        _attack_intent(instance, weapon="shortsword", light_extra_attack=True),
    )

    assert result["ok"] is False
    assert "requires an Attack action with a Light weapon" in result["error"]


def test_light_extra_attack_needs_a_light_weapon(runtime) -> None:
    engine, instance = _start(
        runtime, _sheet(runtime, equipment=["longsword", "shortsword"]),
    )
    _turn(engine, instance)
    _apply(engine, instance, _resolve(
        engine, instance, _attack_intent(instance, weapon="shortsword"),
    ))
    _turn(engine, instance)

    result = _resolve(
        engine, instance,
        _attack_intent(instance, weapon="longsword", light_extra_attack=True),
    )

    assert result["ok"] is False
    assert "must use a Light weapon" in result["error"]


# --------------------------------------------------------------------------
# 4. Loading：每次 action / Bonus Action / Reaction 只能射一发
# --------------------------------------------------------------------------


def test_loading_weapon_fires_once_per_action(runtime) -> None:
    engine, instance = _start(runtime, _sheet(runtime, equipment=["pistol"]))
    _turn(engine, instance)
    _apply(engine, instance, _resolve(
        engine, instance, _attack_intent(instance, weapon="pistol"),
    ))

    # 同一个行动窗口里的第二发：即使还有攻击次数也不允许。
    _turn(engine, instance)
    result = _resolve(engine, instance, _attack_intent(instance, weapon="pistol"))

    assert result["ok"] is False
    assert "Loading weapon" in result["error"]


def test_loading_window_resets_on_the_next_turn(runtime) -> None:
    engine, instance = _start(runtime, _sheet(runtime, equipment=["pistol"]))
    _turn(engine, instance)
    _apply(engine, instance, _resolve(
        engine, instance, _attack_intent(instance, weapon="pistol"),
    ))

    combat = _combat(instance)
    combat["round"] = int(combat["round"]) + 1
    _turn(engine, instance)
    result = _resolve(engine, instance, _attack_intent(instance, weapon="pistol"))

    assert result["ok"] is True, result


# --------------------------------------------------------------------------
# 5. 换手与骑乘声明的拒绝路径（审核补测：每条失败分支都要有用例）
# --------------------------------------------------------------------------


def test_swap_cannot_draw_a_weapon_that_is_not_carried(runtime) -> None:
    engine, instance = _start(runtime, _sheet(runtime, equipment=["longsword"]))
    _turn(engine, instance)

    result = _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="longsword",
            weapon_swap={"draw": "item:greataxe"},
        ),
    )

    assert result["ok"] is False
    assert "not carried" in result["error"]


def test_swap_cannot_stow_what_the_hand_no_longer_holds(runtime) -> None:
    engine, instance = _start(
        runtime, _sheet(runtime, equipment=["shortsword", "greatsword"]),
    )
    _turn(engine, instance)
    # 先收起双手剑，两只手都空了。
    _apply(engine, instance, _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="greatsword",
            weapon_swap={"stow": "item:greatsword"},
        ),
    ))
    _turn(engine, instance)

    result = _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="shortsword",
            weapon_swap={"stow": "item:greatsword"},
        ),
    )

    assert result["ok"] is False
    assert "not in hand" in result["error"]


def test_swap_cannot_draw_a_weapon_already_in_hand(runtime) -> None:
    engine, instance = _start(runtime, _sheet(runtime, equipment=["longsword"]))
    _turn(engine, instance)

    result = _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="longsword",
            weapon_swap={"draw": "item:longsword"},
        ),
    )

    assert result["ok"] is False
    assert "already in hand" in result["error"]


def test_drawing_a_two_handed_weapon_needs_both_hands_free(runtime) -> None:
    # 双手剑收起后，先用短剑占住一只手，再想拔出双手剑就没有第二只手了。
    engine, instance = _start(
        runtime, _sheet(runtime, equipment=["shortsword", "greatsword"]),
    )
    _turn(engine, instance)
    _apply(engine, instance, _resolve(
        engine, instance, _attack_intent(instance, weapon="greatsword"),
    ))
    _turn(engine, instance)
    _apply(engine, instance, _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="greatsword",
            weapon_swap={"stow": "item:greatsword"},
        ),
    ))
    _turn(engine, instance)
    _apply(engine, instance, _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="shortsword",
            weapon_swap={"draw": "item:shortsword"},
        ),
    ))
    _turn(engine, instance)

    result = _resolve(
        engine, instance,
        _attack_intent(
            instance, weapon="shortsword",
            weapon_swap={"draw": "item:greatsword"},
        ),
    )

    assert result["ok"] is False
    assert "needs both hands free" in result["error"]


def test_mounted_declaration_rejects_an_unknown_player(runtime) -> None:
    sheet = _sheet(runtime, equipment=["longsword"])
    engine, instance = monk_instance(runtime, sheet, uid="gm")
    intent = {
        "intent_id": "start-bad-mounted", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [goblin(hp=10)],
        "mounted_player_ids": ["nobody"],
    }

    result = engine.resolve_intent(instance, intent, SequenceRng([20, 1]))

    assert result["ok"] is False
    assert "mounted_player_ids" in result["error"]


def test_light_extra_attack_needs_an_unspent_bonus_action(runtime) -> None:
    inputs = _two_light_weapons(runtime, style=False)
    engine, instance = inputs.engine, inputs.instance
    _apply(engine, instance, _resolve(
        engine, instance, _attack_intent(instance, weapon="shortsword"),
    ))
    _turn(engine, instance)
    _combat(instance)["economy"]["bonus_action"] = 0

    result = _resolve(
        engine, instance,
        _attack_intent(instance, weapon="dagger", light_extra_attack=True),
    )

    assert result["ok"] is False
    assert "Bonus Action" in result["error"]
