"""Action Surge (Fighter 2): a separate pool for one extra non-magical action.

Every assertion goes through the same authoritative chain the web layer uses:
``available_intents`` -> ``validate_intent`` -> ``resolve_intent`` ->
``apply_batch``.
"""

from __future__ import annotations

import pytest

from src.engine.modules import ruleset_runtime
from src.engine.player_control import set_control
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.events import EventBatchError

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    capability_action,
    capability_intent,
    monk_instance,
    start_combat,
)


def _advance(runtime: Dnd2024Runtime, sheet: dict, target_level: int) -> dict:
    """Advance to ``target_level`` without pushing any ability past 20.

    本测试只关心行动经济，因此每次 ASI 都投给当前最低、仍可提升的属性。
    """

    while int(sheet["ruleset_character"]["build"]["level"]) < target_level:
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        for requirement in preview.get("requirements") or []:
            if requirement["id"] != "ability_score_increases":
                continue
            allowed = requirement.get("allowed", "any")
            pool = (
                ["str", "dex", "con", "int", "wis", "cha"]
                if allowed == "any" else [str(item) for item in allowed]
            )
            scores = sheet["ruleset_character"].get("abilities") or {}
            ability = min(pool, key=lambda key: int(scores.get(key, 0) or 0))
            choices["ability_score_increases"] = {
                ability: int(requirement.get("total", 2)),
            }
        if choices:
            preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def _fighter_sheet(runtime: Dnd2024Runtime, *, level: int = 2) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Fighter"},
    )
    return _advance(runtime, sheet, level) if level > 1 else sheet


def _instance(
    *, level: int = 2, surges: int | None = None, mutate=None,
) -> tuple[Dnd2024CombatEngine, object]:
    runtime = Dnd2024Runtime()
    sheet = _fighter_sheet(runtime, level=level)
    canonical = sheet["ruleset_character"]
    if surges is not None:
        canonical["resources"]["class"]["action_surge"]["current"] = surges
    if mutate is not None:
        mutate(canonical)
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance)
    return engine, instance


def _economy(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]["economy"]


def _player(instance) -> dict:
    return instance.get_character_sheet("gm")["ruleset_character"]


def _apply(engine: Dnd2024CombatEngine, instance, intent: dict, rng=None):
    resolved = engine.resolve_intent(instance, intent, rng or SequenceRng([]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _intent_types(engine: Dnd2024CombatEngine, instance) -> list[str]:
    return [item["type"] for item in engine.available_intents(instance, "gm")]


def _attack_intent(engine, instance, *, intent_id: str = "atk-1") -> dict:
    return {
        "intent_id": intent_id, "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:greatsword",
    }


def _surge(engine, instance, *, intent_id: str = "surge-1") -> list[dict]:
    return _apply(
        engine, instance,
        capability_intent(engine, instance, "action_surge", intent_id=intent_id),
    )


def _end_turn(engine, instance, *, actor_id: str = "player:gm", intent_id: str = "end-1"):
    return _apply(engine, instance, {
        "intent_id": intent_id, "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": actor_id,
    })


def _give_cantrip(canonical: dict) -> None:
    canonical["spellcasting"] = {"class": {
        "ability": "int", "slots_current": {}, "concentration": None,
        "prepared_spell_refs": [], "cantrip_refs": ["spell:fire_bolt"],
    }}


def test_using_action_surge_grants_a_separate_action_pool() -> None:
    engine, instance = _instance()

    events = _surge(engine, instance)
    applied = [event for event in events if event["type"] != "intent.submitted"]

    assert [event["type"] for event in applied] == [
        "dnd2024.class_resource.spent", "dnd2024.economy.granted",
    ]
    assert (applied[0]["resource_id"], applied[0]["amount"]) == ("action_surge", 1)
    assert applied[1]["grants"] == {"surge_action": 1}
    assert applied[1]["flags"] == {"action_surge_used": True}

    economy = _economy(instance)
    assert economy["surge_action"] == 1
    assert economy["action_surge_used"] is True
    # Action Surge 本身不消耗动作：普通动作与附赠动作都保持原值。
    assert economy["action"] == 1
    assert economy["bonus_action"] == 1
    assert _player(instance)["resources"]["class"]["action_surge"]["current"] == 0


def test_surged_attack_reopens_attack_and_pays_from_the_surge_pool() -> None:
    engine, instance = _instance()
    _apply(engine, instance, _attack_intent(engine, instance), SequenceRng([15, 3, 4]))

    assert _economy(instance)["action"] == 0
    assert "attack" not in _intent_types(engine, instance)

    _surge(engine, instance)
    assert "attack" in _intent_types(engine, instance)

    _apply(
        engine, instance, _attack_intent(engine, instance, intent_id="atk-2"),
        SequenceRng([15, 3, 4]),
    )
    economy = _economy(instance)
    assert economy["surge_action"] == 0
    assert economy["action"] == 0


def test_the_surge_pool_pays_first_so_the_ordinary_action_stays_free_for_magic() -> None:
    engine, instance = _instance(mutate=_give_cantrip)
    _surge(engine, instance)
    assert (_economy(instance)["action"], _economy(instance)["surge_action"]) == (1, 1)

    _apply(engine, instance, _attack_intent(engine, instance), SequenceRng([15, 3, 4]))

    economy = _economy(instance)
    assert economy["surge_action"] == 0
    assert economy["action"] == 1
    assert "cast_spell" in _intent_types(engine, instance)
    _apply(engine, instance, {
        "intent_id": "cast-1", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:fire_bolt", "target_ids": ["enemy:goblin-1"], "slot_level": 0,
    }, SequenceRng([15, 5]))
    assert _economy(instance)["action"] == 0


def test_basic_action_after_surge_spends_the_surge_pool() -> None:
    engine, instance = _instance()
    _apply(engine, instance, _attack_intent(engine, instance), SequenceRng([15, 3, 4]))
    _surge(engine, instance)
    assert "dash" in _intent_types(engine, instance)

    events = _apply(engine, instance, {
        "intent_id": "dash-1", "type": "dash",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    })
    spent = next(
        event for event in events if event["type"] == "dnd2024.action.spent"
    )

    assert (spent["resource"], spent["amount"]) == ("surge_action", 1)
    economy = _economy(instance)
    assert economy["surge_action"] == 0
    assert economy["action"] == 0


def test_the_surge_action_can_never_be_spent_on_a_spell() -> None:
    engine, instance = _instance(mutate=_give_cantrip)
    _apply(engine, instance, _attack_intent(engine, instance), SequenceRng([15, 3, 4]))
    _surge(engine, instance)

    economy = _economy(instance)
    assert (economy["action"], economy["surge_action"]) == (0, 1)
    assert "cast_spell" not in _intent_types(engine, instance)

    forged = engine.validate_intent(instance, {
        "intent_id": "cast-1", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:fire_bolt", "target_id": "enemy:goblin-1",
    })

    assert forged["ok"] is False
    assert "action" in forged["error"]


def test_action_surge_is_limited_to_once_per_turn() -> None:
    engine, instance = _instance(level=17)
    assert _player(instance)["resources"]["class"]["action_surge"]["current"] == 2

    _surge(engine, instance)
    assert _economy(instance)["action_surge_used"] is True
    assert capability_action(engine, instance, "action_surge") is None

    forged = engine.validate_intent(
        instance,
        capability_intent(
            engine, instance, "action_surge", intent_id="surge-2", target_id="",
        ),
    )
    assert forged["ok"] is False
    assert _player(instance)["resources"]["class"]["action_surge"]["current"] == 1

    _end_turn(engine, instance)
    _end_turn(engine, instance, actor_id="enemy:goblin-1", intent_id="end-2")
    assert capability_action(engine, instance, "action_surge") is not None


def test_an_exhausted_surge_resource_hides_the_capability() -> None:
    engine, instance = _instance(surges=0)

    assert capability_action(engine, instance, "action_surge") is None


def test_ai_hosted_fighter_keeps_attacking_after_using_the_surge() -> None:
    engine, instance = _instance()
    _apply(engine, instance, _attack_intent(engine, instance), SequenceRng([15, 3, 4]))
    _surge(engine, instance)
    set_control(instance, "gm", "ai")

    intent = engine.next_automatic_intent(instance)

    assert intent is not None
    assert intent["type"] == "attack"
    assert intent["target_id"] == "enemy:goblin-1"


def test_economy_grant_flags_fail_closed_on_unknown_keys() -> None:
    """``flags`` is engine bookkeeping, not a channel to write arbitrary economy."""

    engine, instance = _instance()
    version = ruleset_runtime.state(instance)["version"]

    with pytest.raises(EventBatchError, match="unknown"):
        engine.apply_batch(instance, {
            "batch_id": "batch_forged_economy_flags",
            "intent_id": "forged-flags",
            "intent_type": "class_capability",
            "expected_version": version,
            "result_version": version + 1,
            "events": [
                {"type": "dnd2024.economy.granted", "actor_id": "player:gm",
                 "grants": {}, "flags": {"not_a_real_flag": True}},
            ],
            "source_ref": "test",
        })

    assert "not_a_real_flag" not in _economy(instance)
    assert ruleset_runtime.state(instance)["version"] == version
