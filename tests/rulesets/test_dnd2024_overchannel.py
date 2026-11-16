"""S0-D2: Overchannel -- maximize declaration plus long-rest usage counter.

The feature is subclass-granted, so every sheet here is built through the real
1 -> 13/14 advancement path with a real `subclass:evoker` choice; only the
counter is primed directly when a test needs "this is not the first use today".
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.rulesets.dnd2024.features import (
    Dnd2024ClassFeatureCatalog,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.features.resources import (
    feature_usage_count,
    set_feature_usage_count,
)
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from test_dnd2024_die_manipulation import (  # noqa: F401 - fixtures by import
    WIZARD_ABILITIES,
    WIZARD_SKILLS,
    _build_sheet,
    cast,
    cast_result,
    damage_to,
    enemy,
    events_of,
    grant,
    grant_cantrip,
    new_combat,
    overchannel_cost,
    reject,
    runtime,
    set_hp,
    set_turn,
    wizard13,
    wizard14,
)
from test_dnd2024_die_manipulation import canonical, check_of
from test_dnd2024_counterspell import pending_of, resolve_decision

OWNER = "subclass:evoker"
FEATURE = "overchannel"


# --- helpers ---------------------------------------------------------------

def prime(instance, count: int = 1, uid: str = "gm") -> None:
    """Make the next cast the (count + 1)-th of this long rest.

    This writes the same canonical ledger the reducer writes; it never fakes
    ownership, spells, slots or the feature declaration.
    """

    set_feature_usage_count(canonical(instance, uid), OWNER, FEATURE, "long_rest", count)


def usage_count(instance, uid: str = "gm") -> int:
    return feature_usage_count(canonical(instance, uid), OWNER, FEATURE)


def usage_events(result: dict) -> list[dict]:
    return [
        event for event in events_of(result)
        if event["type"] == "dnd2024.feature_usage.changed"
    ]


def event_index(result: dict, predicate) -> int:
    return next(index for index, event in enumerate(events_of(result)) if predicate(event))


def big_enemy(enemy_id: str = "goblin-1", hp: int = 300) -> dict:
    return enemy(enemy_id, hp=hp)


# --- 6.1 content, ownership and eligibility --------------------------------

def test_catalog_declares_the_usage_contract(runtime: Dnd2024Runtime) -> None:
    english = Dnd2024ClassFeatureCatalog.from_bundle(runtime.load_bundle("en"))
    chinese = Dnd2024ClassFeatureCatalog.from_bundle(runtime.load_bundle("zh-CN"))
    definition = english.features[FEATURE]

    assert definition.class_id == "wizard"
    assert definition.owner == OWNER
    assert definition.minimum_level == 14
    assert definition.source_ref == "srd-5.2.1:p82:overchannel"
    assert dict(definition.parameters) == {
        "maximum_slot_level": 5,
        "usage_scope": "long_rest",
        "self_damage_die": 12,
        "initial_self_damage_dice": 2,
    }
    assert english.labels[FEATURE] == "Overchannel"
    assert chinese.labels[FEATURE] == "超限导能"
    # The declared scope is the only scope the usage ledger accepts.
    assert definition.parameters["usage_scope"] == "long_rest"


def test_only_the_evoker_subclass_owns_overchannel(
    wizard14, wizard13, runtime: Dnd2024Runtime,
) -> None:
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle("en"))

    assert resolver.has_feature(wizard13["ruleset_character"], FEATURE) is False
    assert resolver.has_feature(
        wizard14["ruleset_character"], FEATURE, owner=OWNER,
    ) is True
    # Owner is part of the identity: a different subclass cannot share the id.
    assert resolver.has_feature(
        wizard14["ruleset_character"], FEATURE, owner="subclass:abjurer",
    ) is False


def test_declaring_overchannel_is_strictly_optional(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")

    # Omitted: baseline dice, no usage event, no counter movement.
    baseline = cast(engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"], rolls=[1, 1, 2])
    assert damage_to(baseline, "enemy:goblin-1")["rolls"] == [1, 1, 2]
    assert usage_events(baseline) == []
    assert usage_count(instance) == 0

    # Explicit false is the same no-op, including without the feature at all.
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")
    explicit = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[1, 1, 2], overchannel=False,
    )
    assert damage_to(explicit, "enemy:goblin-1")["rolls"] == [1, 1, 2]
    assert usage_events(explicit) == []
    assert usage_count(instance) == 0

    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")
    assert reject(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        overchannel="yes",
    ) == "overchannel must be a boolean"


def test_overchannel_gating_matrix(wizard13, wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard13)
    grant(instance, "spell:magic_missile")
    assert reject(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"], overchannel=True,
    ) == "overchannel is required to maximize damage"

    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:false_life", "spell:thunderwave", "spell:guiding_bolt")
    grant_cantrip(instance, "spell:fire_bolt")

    # An off-list spell is refused even though the actor can cast it.
    assert reject(
        engine, instance, "spell:guiding_bolt", 1, ["enemy:goblin-1"], overchannel=True,
    ) == "overchannel only applies to Wizard spells"
    # A Wizard-list spell with no damage effect is refused next.
    assert reject(
        engine, instance, "spell:false_life", 1, ["player:gm"], overchannel=True,
    ) == "this spell does not deal damage"
    # Cantrip: no slot, never maximizable.
    assert reject(
        engine, instance, "spell:fire_bolt", 0, ["enemy:goblin-1"], overchannel=True,
    ) == "maximized casting requires a spell slot of level 1-5"
    # 6th-level slot is outside the declared cap.
    assert reject(
        engine, instance, "spell:thunderwave", 6, ["enemy:goblin-1"], overchannel=True,
    ) == "maximized casting requires a spell slot of level 1-5"
    assert usage_count(instance) == 0


def test_the_class_list_decides_eligibility_not_the_school(
    wizard14, runtime: Dnd2024Runtime,
) -> None:
    """`class_refs` is the gate; `school` never is.

    No automated Wizard-list damage spell below level 6 is a non-evocation
    spell, so the positive half is witnessed by `false_life` (Wizard list,
    necromancy) reaching the damage check instead of being refused for its
    school, and the negative half by `guiding_bolt` (evocation, Cleric only)
    being refused.
    """

    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:false_life", "spell:guiding_bolt")

    assert reject(
        engine, instance, "spell:false_life", 1, ["player:gm"], overchannel=True,
    ) == "this spell does not deal damage"
    assert reject(
        engine, instance, "spell:guiding_bolt", 1, ["enemy:goblin-1"], overchannel=True,
    ) == "overchannel only applies to Wizard spells"


# --- 6.2 three damage modes and dice order ---------------------------------

def test_first_use_maximizes_and_is_free(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")

    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[1, 1, 1], overchannel=True,
    )
    damage = damage_to(result, "enemy:goblin-1")
    assert damage["amount"] == 15, "3d4 + 3 maximized keeps the flat +3"
    assert damage["rolls"] == []
    assert damage["overchannel"] is True
    assert not any(event.get("overchannel_cost") for event in events_of(result))
    usage = usage_events(result)
    assert len(usage) == 1
    assert (usage[0]["before_count"], usage[0]["after_count"]) == (0, 1)
    assert usage[0]["use_index"] == 1
    assert usage[0]["owner"] == OWNER
    assert usage[0]["scope"] == "long_rest"
    assert usage_count(instance) == 1


def test_upcast_dice_are_maximized(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")

    result = cast(
        engine, instance, "spell:magic_missile", 2, ["enemy:goblin-1"],
        rolls=[1, 1, 1, 1], overchannel=True,
    )
    damage = damage_to(result, "enemy:goblin-1")
    # 3d4 + 3 base plus one upcast die of 1d4 + 1, every die maximized.
    assert damage["amount"] == 4 * 4 + 4
    assert damage["rolls"] == []
    assert damage["overchannel"] is True


def test_saving_throw_maximizes_before_halving(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:burning_hands")

    failed = cast(
        engine, instance, "spell:burning_hands", 1, ["enemy:goblin-1"],
        rolls=[1], overchannel=True,
    )
    assert damage_to(failed, "enemy:goblin-1")["amount"] == 18  # 3d6 maximized

    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:burning_hands")
    saved = cast(
        engine, instance, "spell:burning_hands", 1, ["enemy:goblin-1"],
        rolls=[20], overchannel=True,
    )
    assert damage_to(saved, "enemy:goblin-1")["amount"] == 9  # 18 halved


def test_every_ray_is_maximized_and_charged_once(wizard14, runtime: Dnd2024Runtime) -> None:
    target = enemy("goblin-1", hp=300)
    engine, instance = new_combat(runtime, wizard14, enemies=[target])
    grant(instance, "spell:scorching_ray")

    # Scorching Ray is a 2nd-level spell; the slot level also sets the cost.
    result = cast(
        engine, instance, "spell:scorching_ray", 2,
        ["enemy:goblin-1", "enemy:goblin-1", "enemy:goblin-1"],
        rolls=[20, 10, 10], overchannel=True,
    )
    rays = [
        event for event in events_of(result)
        if event["type"] == "resource.changed" and event.get("target_id") == "enemy:goblin-1"
    ]
    # The first ray is a natural 20: 2d6 doubles to 4d6, then maximizes to 24.
    assert [ray["amount"] for ray in rays] == [24, 12, 12]
    assert all(ray["rolls"] == [] for ray in rays)
    assert all(ray["overchannel"] is True for ray in rays)
    assert len(usage_events(result)) == 1, "one cast, three rays, one use"


def test_spell_attack_still_rolls_and_a_miss_still_counts(
    wizard14, runtime: Dnd2024Runtime,
) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:chromatic_orb")
    prime(instance, 1)

    hit = cast(
        engine, instance, "spell:chromatic_orb", 1, ["enemy:goblin-1"],
        rolls=[15, 12, 12], damage_type="fire", overchannel=True,
    )
    assert damage_to(hit, "enemy:goblin-1")["amount"] == 24  # 3d8 maximized
    assert overchannel_cost(hit)["amount"] == 24  # 2d12, one slot level

    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:chromatic_orb")
    miss = cast(
        engine, instance, "spell:chromatic_orb", 1, ["enemy:goblin-1"],
        rolls=[1], damage_type="fire", overchannel=True,
    )
    assert not any(
        event["type"] == "resource.changed" and event.get("target_id") == "enemy:goblin-1"
        for event in events_of(miss)
    )
    assert len(usage_events(miss)) == 1, "a miss still spends the use"
    assert usage_count(instance) == 1
    assert not any(event.get("overchannel_cost") for event in events_of(miss))


def test_undeclared_cast_keeps_the_baseline(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:burning_hands")

    result = cast(engine, instance, "spell:burning_hands", 1, ["enemy:goblin-1"], rolls=[1, 3, 4, 5])
    damage = damage_to(result, "enemy:goblin-1")
    assert damage["amount"] == 12
    assert damage["rolls"] == [3, 4, 5]
    assert "overchannel" not in damage
    assert usage_events(result) == []
    assert usage_count(instance) == 0


def test_first_use_consumes_no_self_damage_dice(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:thunderwave")

    _result, rng = cast_result(
        engine, instance, "spell:thunderwave", 1, ["enemy:goblin-1"],
        rolls=[1, 99, 98], overchannel=True,
    )
    # Only the target's save d20 was consumed; the sentinel d12s survived.
    assert rng.values == [99, 98]


# --- 6.3 escalation, state and the reducer ---------------------------------

def test_self_damage_escalates_within_one_long_rest(
    wizard14, runtime: Dnd2024Runtime,
) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")
    expected = [(1, 0), (2, 2), (3, 3), (4, 4)]

    for use_index, dice in expected:
        result = cast(
            engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
            rolls=[1] * max(dice, 1), overchannel=True,
        )
        usage = usage_events(result)[0]
        assert usage["use_index"] == use_index
        assert usage["before_count"] == use_index - 1
        costs = [event for event in events_of(result) if event.get("overchannel_cost")]
        if dice == 0:
            assert costs == []
        else:
            assert len(costs) == 1
            assert len(costs[0]["rolls"]) == dice
            assert costs[0]["amount"] == dice
        assert usage_count(instance) == use_index


def test_self_damage_uses_the_actual_slot_and_use_index(
    wizard14, runtime: Dnd2024Runtime,
) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")

    # Second use in a 2nd-level slot: 2 d12 per slot level => 4 d12.
    prime(instance, 1)
    result = cast(
        engine, instance, "spell:magic_missile", 2, ["enemy:goblin-1"],
        rolls=[1, 1, 1, 1], overchannel=True,
    )
    assert len(overchannel_cost(result)["rolls"]) == 4

    # Third use in a 5th-level slot: 3 d12 per slot level => 15 d12.
    prime(instance, 2)
    canonical(instance)["spellcasting"]["class"]["slots_current"] = {"5": 1}
    result = cast(
        engine, instance, "spell:magic_missile", 5, ["enemy:goblin-1"],
        rolls=[1] * 15, overchannel=True,
    )
    assert len(overchannel_cost(result)["rolls"]) == 15
    assert overchannel_cost(result)["amount"] == 15


def test_self_damage_targets_the_caster_before_victory(
    wizard14, runtime: Dnd2024Runtime,
) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[enemy("goblin-1", hp=5)])
    grant(instance, "spell:magic_missile")
    prime(instance, 1)

    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[3, 4], overchannel=True,
    )
    events = events_of(result)
    cost = overchannel_cost(result)
    assert cost["target_id"] == "player:gm"
    assert cost["damage_type"] == "necrotic"
    assert cost["ignore_resistance"] is True
    assert cost["ignore_immunity"] is True
    assert cost["amount"] == 7
    assert len(cost["rolls"]) == 2
    victory = next(
        index for index, event in enumerate(events) if event["type"] == "dnd2024.combat.ended"
    )
    cost_index = next(
        index for index, event in enumerate(events) if event.get("overchannel_cost")
    )
    usage_index = next(
        index for index, event in enumerate(events)
        if event["type"] == "dnd2024.feature_usage.changed"
    )
    assert usage_index < cost_index < victory


def test_self_damage_ignores_resistance_and_temp_hp_absorbs_first(
    wizard14, runtime: Dnd2024Runtime,
) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    character = canonical(instance)
    character.setdefault("conditions", {})["necrotic_resistant"] = {
        "source": "test", "resist_damage_types": ["necrotic"],
    }
    character["resources"]["temp_hp"] = 10
    grant(instance, "spell:magic_missile")
    prime(instance, 1)

    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[12, 12], overchannel=True,
    )
    cost = overchannel_cost(result)
    assert cost["amount"] == 24, "resistance must not reduce the self damage"
    assert "resisted" not in cost
    resources = canonical(instance)["resources"]
    assert resources["temp_hp"] == 0
    assert resources["hp"] == resources["max_hp"] - 14


def test_self_damage_drops_the_caster_and_forces_a_concentration_check(
    wizard14, runtime: Dnd2024Runtime,
) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:stoneskin", "spell:thunderwave", "spell:magic_missile")
    cast(engine, instance, "spell:stoneskin", 4, ["player:gm"])
    prime(instance, 1)

    result = cast(
        engine, instance, "spell:thunderwave", 1, ["enemy:goblin-1"],
        rolls=[1, 10, 10, 10], overchannel=True,
    )
    assert overchannel_cost(result)["amount"] == 20
    check = check_of(result, "concentration")
    assert check["target"] == max(10, 20 // 2)
    assert check["rolls"] == [10]

    # 第三次使用：每环阶 3d12。
    set_hp(instance, 1)
    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[12, 12, 12], overchannel=True,
    )
    assert overchannel_cost(result)["amount"] == 36
    canonical_state = canonical(instance)
    assert int(canonical_state["resources"]["hp"]) == 0
    assert "unconscious" in canonical_state["conditions"]


def test_a_zero_damage_result_still_spends_the_use(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:thunderwave")

    # Damage is halved, not zeroed; the use is recorded regardless.
    result = cast(
        engine, instance, "spell:thunderwave", 1, ["enemy:goblin-1"],
        rolls=[20], overchannel=True,
    )
    assert damage_to(result, "enemy:goblin-1")["amount"] == 8
    assert usage_count(instance) == 1


def test_applying_the_same_batch_twice_does_not_double_charge(
    wizard14, runtime: Dnd2024Runtime,
) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")
    prime(instance, 1)

    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[3, 4], overchannel=True,
    )
    hp = int(canonical(instance)["resources"]["hp"])
    assert usage_count(instance) == 2

    engine.apply_batch(instance, result["event_batch"])

    assert int(canonical(instance)["resources"]["hp"]) == hp
    assert usage_count(instance) == 2


def test_a_stale_usage_event_fails_closed(wizard14, runtime: Dnd2024Runtime) -> None:
    """The reducer guards the counter with `before_count`, not with trust."""

    from src.rulesets.events import EventBatchError

    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")
    prime(instance, 3)

    version = ruleset_runtime.state(instance)["version"]
    batch = {
        "batch_id": "batch_staleusage0000000000000000000",
        "intent_id": "stale-usage",
        "expected_version": version,
        "result_version": version + 1,
        "events": [{
            "type": "dnd2024.feature_usage.changed", "actor_id": "player:gm",
            "owner": OWNER, "feature_id": FEATURE, "scope": "long_rest",
            "spell_ref": "spell:magic_missile", "slot_level": 1,
            "before_count": 0, "after_count": 1, "use_index": 1,
        }],
    }

    with pytest.raises(EventBatchError, match="feature usage"):
        engine.apply_batch(instance, batch)
    assert usage_count(instance) == 3


def test_a_corrupt_usage_ledger_fails_closed(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")
    for broken in (
        {"owner": OWNER, "feature_id": FEATURE, "scope": "long_rest", "count": -1},
        {"owner": OWNER, "feature_id": FEATURE, "scope": "short_rest", "count": 1},
        {"owner": OWNER, "feature_id": FEATURE, "scope": "long_rest", "count": True},
    ):
        canonical(instance)["features"]["usage"] = [broken]
        assert reject(
            engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
            overchannel=True,
        ).startswith("features.usage")

    canonical(instance)["features"]["usage"] = [
        {"owner": OWNER, "feature_id": FEATURE, "scope": "long_rest", "count": 1},
        {"owner": OWNER, "feature_id": FEATURE, "scope": "long_rest", "count": 2},
    ]
    assert reject(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"], overchannel=True,
    ).startswith("features.usage")


# --- 6.4 counterspell, rest and the exclusion surface ----------------------

def _caster_with_counter_dc(runtime: Dnd2024Runtime, sheet: dict, dc: int = 15):
    goblin_enemy = enemy("goblin-1", hp=300)
    goblin_enemy["counterspell_dc"] = dc
    return new_combat(runtime, sheet, enemies=[goblin_enemy])


def test_a_countered_cast_spends_nothing(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = _caster_with_counter_dc(runtime, wizard14)
    grant(instance, "spell:magic_missile")
    prime(instance, 1)

    opened = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        overchannel=True,
    )
    decision = pending_of(opened)[0]
    countered = resolve_decision(engine, instance, decision, "counter", rolls=[3])

    types = [event["type"] for event in events_of(countered)]
    assert "dnd2024.spell.countered" in types
    assert "dnd2024.feature_usage.changed" not in types
    assert not any(event.get("overchannel_cost") for event in events_of(countered))
    assert usage_count(instance) == 1, "a countered cast is not a use"


def test_a_failed_counter_keeps_the_declaration(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = _caster_with_counter_dc(runtime, wizard14)
    grant(instance, "spell:magic_missile")
    prime(instance, 1)

    opened = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        overchannel=True,
    )
    decision = pending_of(opened)[0]
    resolved = resolve_decision(engine, instance, decision, "counter", rolls=[20, 1, 1])

    events = events_of(resolved)
    assert "dnd2024.feature_usage.changed" in [event["type"] for event in events]
    damage = damage_to({"event_batch": {"events": events}}, "enemy:goblin-1")
    assert damage["amount"] == 15
    assert overchannel_cost({"event_batch": {"events": events}})["amount"] == 2
    assert usage_count(instance) == 2


def test_short_rest_keeps_the_counter_and_long_rest_resets_it(
    wizard14, runtime: Dnd2024Runtime,
) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")
    prime(instance, 2)

    rest = Dnd2024RestEngine(runtime.load_bundle("en"))
    character = deepcopy(canonical(instance))
    short = rest.complete_short_rest(character)
    assert [event["type"] for event in short["events"]].count("reset_feature_usage") == 0
    assert feature_usage_count(short["character"], OWNER, FEATURE) == 2

    long = rest.complete_long_rest(deepcopy(canonical(instance)))
    resets = [event for event in long["events"] if event["type"] == "reset_feature_usage"]
    assert len(resets) == 1
    assert resets[0]["owner"] == OWNER
    assert resets[0]["feature_id"] == FEATURE
    assert resets[0]["scope"] == "long_rest"
    assert (resets[0]["before"], resets[0]["after"]) == (2, 0)
    assert feature_usage_count(long["character"], OWNER, FEATURE) == 0


def test_a_long_rest_without_uses_emits_no_reset(wizard14, runtime: Dnd2024Runtime) -> None:
    rest = Dnd2024RestEngine(runtime.load_bundle("en"))
    character = deepcopy(wizard14["ruleset_character"])
    character["resources"]["hp"] = int(character["resources"]["max_hp"])

    long = rest.complete_long_rest(character)

    assert [event["type"] for event in long["events"]].count("reset_feature_usage") == 0
    assert "usage" not in (long["character"].get("features") or {})


def test_unrelated_paths_leave_the_counter_alone(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:cure_wounds", "spell:false_life")

    cast(engine, instance, "spell:cure_wounds", 1, ["player:gm"], rolls=[1, 2])
    cast(engine, instance, "spell:false_life", 1, ["player:gm"], rolls=[1, 2])

    assert usage_count(instance) == 0
    assert canonical(instance).get("features", {}).get("usage") in (None, [])


def test_the_public_feed_does_not_grow(wizard14, runtime: Dnd2024Runtime) -> None:
    engine, instance = new_combat(runtime, wizard14, enemies=[big_enemy()])
    grant(instance, "spell:magic_missile")
    prime(instance, 1)

    result = cast(
        engine, instance, "spell:magic_missile", 1, ["enemy:goblin-1"],
        rolls=[3, 4], overchannel=True,
    )
    assert len(usage_events(result)) == 1

    feed = runtime._recent_combat_events(instance)
    assert all(row["type"] != "dnd2024.feature_usage.changed" for row in feed)
    cost_rows = [
        row for row in feed
        if row["type"] == "resource.changed" and row.get("damage_type") == "necrotic"
    ]
    assert len(cost_rows) == 1, "the necrotic cost still shows through the public fields"
    assert "overchannel" not in cost_rows[0]
