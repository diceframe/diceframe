"""Shared fixtures for the D&D 2024 class feature runtime v1 tests.

Kept next to the tests (rather than in ``src``) because every helper here is
test scaffolding: a deterministic RNG, a level-1 Monk draft, the real
advancement path to higher levels, and a minimal combat instance.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.engine.game_instance import GameInstance
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.engine.modules import ruleset_runtime


@dataclass
class SequenceRng:
    """Deterministic ``randint`` source for authoritative resolution."""

    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum, (minimum, value, maximum)
        return value


def monk_draft(runtime: Dnd2024Runtime, locale: str = "en") -> dict[str, Any]:
    """A level 1 Monk draft whose guided choices follow the bundle."""

    draft: dict[str, Any] = {
        "locale": locale,
        "name": "Monk",
        "level": 1,
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 12, "dex": 14, "con": 13, "int": 8, "wis": 15, "cha": 10,
        },
        "class_ref": "class:monk",
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "class_skill_refs": ["skill:acrobatics", "skill:stealth"],
        "equipment_package_ref": "equipment_package:monk_b",
        "background_equipment_package_ref": "equipment_package:acolyte_b",
        "alignment": "neutral",
    }
    choices = runtime.builder_choices(None, draft)
    ability_refs = choices["background_ability_refs"]
    draft["background_ability_bonuses"] = {
        ability_refs[0].split(":", 1)[1]: 2,
        ability_refs[1].split(":", 1)[1]: 1,
    }
    draft["species_size"] = (choices["species_sizes"] or [""])[0]
    draft["species_skill_refs"] = [
        item["ref"] for item in choices["species_skills"]
    ][: choices["species_skill_count"]]
    draft["species_feat_refs"] = [
        item["ref"] for item in choices["species_feats"]
    ][: choices["species_feat_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    draft["language_refs"] = ["language:common", "language:dwarvish", "language:elvish"]
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [option["value"] for option in spec["options"]][: int(spec["count"])]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    errors = runtime.validate_character(None, draft)
    assert errors == [], errors
    return draft


def advancement_choices(preview: dict[str, Any]) -> dict[str, Any]:
    """Pick the first legal answer for every requirement of one level-up."""

    choices: dict[str, Any] = {}
    for requirement in preview.get("requirements") or []:
        requirement_id = str(requirement["id"])
        if requirement_id == "subclass_ref":
            choices["subclass_ref"] = str(requirement["options"][0]["value"])
        elif requirement_id in {"feat_ref", "epic_boon_ref"}:
            available = [
                option for option in requirement["options"] if option.get("available")
            ]
            choices[requirement_id] = str(available[0]["value"])
        elif requirement_id == "ability_score_increases":
            allowed = requirement.get("allowed", "any")
            ability = "str" if allowed == "any" else str(allowed[0])
            choices["ability_score_increases"] = {ability: int(requirement.get("total", 2))}
    return choices


def advance(runtime: Dnd2024Runtime, sheet: dict[str, Any], target_level: int) -> dict[str, Any]:
    """Advance a professional character to ``target_level`` through the real path."""

    while int(sheet["ruleset_character"]["build"]["level"]) < target_level:
        preview = runtime.preview_advancement(None, sheet, {})
        choices = advancement_choices(preview)
        if choices:
            preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return sheet


def monk_sheet(
    runtime: Dnd2024Runtime, *, level: int = 1, locale: str = "en",
) -> dict[str, Any]:
    sheet = runtime.finalize_character(None, monk_draft(runtime, locale))
    if level > 1:
        sheet = advance(runtime, sheet, level)
    return sheet


def with_equipment(
    runtime: Dnd2024Runtime, sheet: dict[str, Any], *item_refs: str, locale: str = "en",
) -> dict[str, Any]:
    """Return the sheet wearing/wielding exactly ``item_refs``.

    ``ruleset_character.equipment.item_refs`` is the canonical authority for
    what is worn and wielded -- the live equipment path writes that same field.
    The user-visible projection fields are refreshed through the feature
    boundary (the same call the runtime's reconcile uses), so a test never
    hand-rolls a projection of its own.
    """

    updated = deepcopy(sheet)
    canonical = updated["ruleset_character"]
    canonical["equipment"]["item_refs"] = [str(ref) for ref in item_refs]
    updated.update(
        Dnd2024ClassFeatureResolver(runtime.load_bundle(locale)).projection_fields(canonical)
    )
    return updated


def goblin(*, position: int = 5, hp: int = 40) -> dict[str, Any]:
    return {
        "id": "goblin-1", "name": "Goblin", "hp": hp, "armor_class": 12,
        "speed": 30, "position": position, "initiative_modifier": 2,
        "creature_type": "humanoid",
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def monk_instance(
    runtime: Dnd2024Runtime, sheet: dict[str, Any], *, locale: str = "en",
    uid: str = "gm",
) -> tuple[Dnd2024CombatEngine, GameInstance]:
    instance = GameInstance(
        game_key=("test", "dnd2024-monk", "bot"),
        rule_id="dnd2024_srd", gm_uid=uid, language=locale,
    )
    instance.players[uid] = {
        "character_name": str(sheet.get("character_name") or "Hero"),
        "character_sheet": sheet,
    }
    assert instance.bind_ruleset_runtime(sheet["ruleset_character"]["rule_binding"])
    return Dnd2024CombatEngine(runtime.load_bundle(locale), EncounterAccess.sandbox()), instance


def start_combat(
    engine: Dnd2024CombatEngine, instance: GameInstance, *, position: int = 5,
    hp: int = 40, uid: str = "gm",
) -> None:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": uid, "enemies": [goblin(position=position, hp=hp)],
    }, SequenceRng([20, 1]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    assert ruleset_runtime.state(instance)["combat"]["initiative"][0] == f"player:{uid}"


def capability_actions(
    engine: Dnd2024CombatEngine, instance: GameInstance, uid: str = "gm",
) -> list[dict[str, Any]]:
    return [
        item for item in engine.available_intents(instance, uid)
        if item["type"] == "class_capability"
    ]


def capability_action(
    engine: Dnd2024CombatEngine, instance: GameInstance, capability_id: str,
    uid: str = "gm",
) -> dict[str, Any] | None:
    return next(
        (
            item for item in capability_actions(engine, instance, uid)
            if item["capability_id"] == capability_id
        ),
        None,
    )


def capability_intent(
    engine: Dnd2024CombatEngine, instance: GameInstance, capability_id: str,
    *, intent_id: str, uid: str = "gm", target_id: str = "enemy:goblin-1",
    **extra: Any,
) -> dict[str, Any]:
    intent: dict[str, Any] = {
        "intent_id": intent_id,
        "type": "class_capability",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": uid,
        "actor_id": f"player:{uid}",
        "capability_id": capability_id,
    }
    if target_id:
        intent["target_id"] = target_id
    intent.update(extra)
    return intent


def focus_state(instance: GameInstance, uid: str = "gm") -> dict[str, Any]:
    canonical = instance.get_character_sheet(uid)["ruleset_character"]
    return dict(canonical.get("resources", {}).get("class", {}).get("focus_points") or {})
