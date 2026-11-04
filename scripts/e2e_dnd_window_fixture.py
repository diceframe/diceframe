"""Seed persistent post-roll windows through the real D&D combat engine.

The browser checks must exercise a real pending ``check_window`` / ``damage_window``
without waiting for a random attack to qualify.  These fixtures therefore run the
authoritative engine with a deterministic RNG and save the *resulting* instance,
so the browser only performs the human decision (spend/decline) afterwards.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.engine.game_instance import GameInstance, GameState
from src.engine.modules import room_access, ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection


LOCALE = "zh-CN"
VIEWPORTS = ("desktop", "mobile")
E2E_DND_LUCKY_GAME_KEYS = tuple(
    ("web", f"e2e-dnd2024-lucky-{viewport}", "web_bot") for viewport in VIEWPORTS
)
E2E_DND_CUTTING_GAME_KEYS = tuple(
    ("web", f"e2e-dnd2024-cutting-{viewport}", "web_bot") for viewport in VIEWPORTS
)
E2E_DND_WINDOW_GAME_KEYS = (*E2E_DND_LUCKY_GAME_KEYS, *E2E_DND_CUTTING_GAME_KEYS)


@dataclass
class _SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum, (minimum, value, maximum)
        return value


def _goblin() -> dict[str, Any]:
    return {
        "id": "goblin-1", "name": "Goblin", "hp": 40, "armor_class": 12,
        "speed": 30, "position": 5, "initiative_modifier": 2,
        "creature_type": "humanoid",
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _grant_lucky(runtime: Dnd2024Runtime, sheet: dict[str, Any]) -> dict[str, Any]:
    """Give the fixture hero the origin feat through the real resource sync."""

    granted = deepcopy(sheet)
    character = granted["ruleset_character"]
    refs = character.setdefault("features", {}).setdefault("feat_refs", [])
    if "feat:lucky" not in refs:
        refs.append("feat:lucky")
    granted["ruleset_character"] = Dnd2024RestEngine(
        runtime.load_bundle(LOCALE),
    ).sync_resources(character)
    return granted


def _spell_choices(runtime: Dnd2024Runtime, class_ref: str, level: int) -> dict[str, Any]:
    options = Dnd2024SpellSelection(runtime.load_bundle(LOCALE)).options(class_ref, level)
    requirements = options["requirements"]
    cantrips = [row["ref"] for row in options["cantrips"]]
    leveled = [row["ref"] for row in options["leveled_spells"]]
    prepared = leveled[: int(requirements["prepared_spell_count"])]
    spellbook: list[str] = []
    minimum = int(requirements.get("spellbook_minimum") or 0)
    if minimum:
        spellbook = leveled[:minimum]
        prepared = spellbook[: int(requirements["prepared_spell_count"])]
    return {
        "cantrip_refs": cantrips[: int(requirements["cantrip_count"])],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }


def _advancement_choices(preview: dict[str, Any]) -> dict[str, Any]:
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
    return choices


def _bard_sheet(runtime: Dnd2024Runtime) -> dict[str, Any]:
    """A level-3 College of Lore bard, built through the real builder path."""

    draft: dict[str, Any] = {
        "locale": LOCALE,
        "name": "刻薄言语诗人",
        "level": 1,
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15,
        },
        "class_ref": "class:bard",
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "class_skill_refs": ["skill:performance", "skill:persuasion", "skill:stealth"],
        "equipment_package_ref": "equipment_package:bard_a",
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
    draft["class_spell_choices"] = _spell_choices(runtime, "class:bard", 1)
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < 3:
        level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        picks = _advancement_choices(runtime.preview_advancement(None, sheet, {}))
        picks["class_spell_choices"] = _spell_choices(runtime, "class:bard", level)
        preview = runtime.preview_advancement(None, sheet, picks)
        if preview["ok"] is not True:
            raise RuntimeError(f"failed to advance the E2E bard: {preview['errors']}")
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _instance(
    game_key: tuple[str, str, str], world_name: str, players: dict[str, dict[str, Any]],
) -> GameInstance:
    instance = GameInstance(
        game_key=game_key,
        world_id="default_fantasy",
        world_name=world_name,
        group_name="Post-roll window browser tests",
        state=GameState.ACTIVE_ACTION,
        gm_uid="e2e-gm",
        rule_id="dnd2024_srd",
        language=LOCALE,
    )
    instance.set_scene("灰沼村议事厅")
    room_access.replace_max_players(instance, 2)
    instance.players = deepcopy(players)
    if not instance.bind_ruleset_runtime(
        players["e2e-gm"]["character_sheet"]["ruleset_character"]["rule_binding"],
    ):
        raise RuntimeError("failed to bind the D&D 2024 runtime for the window fixture")
    return instance


def _apply(
    engine: Dnd2024CombatEngine, instance: GameInstance, intent: dict[str, Any],
    rng: _SequenceRng,
) -> list[dict[str, Any]]:
    resolved = engine.resolve_intent(instance, intent, rng)
    if resolved.get("ok") is not True:
        raise RuntimeError(f"fixture intent failed: {resolved}")
    if engine.apply_batch(instance, resolved["event_batch"])["applied"] is not True:
        raise RuntimeError("fixture event batch was not applied")
    return resolved["event_batch"]["events"]


def _decision_intent(instance: GameInstance, decision: dict[str, Any], option: str) -> dict[str, Any]:
    return {
        "intent_id": f"fixture-decide-{decision['decision_id']}",
        "type": "decision.resolve",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": decision["assigned_to"],
        "decision_id": decision["decision_id"],
        "option": option,
    }


def _pending(instance: GameInstance) -> list[dict[str, Any]]:
    return list(ruleset_runtime.state(instance)["combat"].get("pending_decisions") or [])


def _start_and_hit(
    instance: GameInstance, engine: Dnd2024CombatEngine, initiative: list[int],
) -> None:
    """Goblin acts first and lands one scimitar hit on the GM's character."""

    _apply(engine, instance, {
        "intent_id": "fixture-start", "type": "combat.start", "expected_version": 0,
        "submitted_by": "e2e-gm", "enemies": [_goblin()],
    }, _SequenceRng(initiative))
    _apply(engine, instance, {
        "intent_id": "fixture-goblin-attack", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "e2e-gm", "actor_id": "enemy:goblin-1",
        "target_id": "player:e2e-gm", "attack_id": "scimitar",
    }, _SequenceRng([15]))


def _write_lucky(data_dir, runtime: Dnd2024Runtime, hero: dict[str, Any], write_save) -> None:
    engine = Dnd2024CombatEngine(runtime.load_bundle(LOCALE), EncounterAccess.sandbox())
    for game_key in E2E_DND_LUCKY_GAME_KEYS:
        instance = _instance(game_key, "D&D 2024 幸运窗口桌", {
            "e2e-gm": {"character_name": "新手守护者", "character_sheet": hero},
        })
        _start_and_hit(instance, engine, [1, 20])
        pending = _pending(instance)
        if len(pending) != 1 or pending[0]["window_code"] != "lucky_disadvantage":
            raise RuntimeError(f"lucky fixture did not leave a Lucky window: {pending}")
        write_save(data_dir, instance)


def _write_cutting(
    data_dir, runtime: Dnd2024Runtime, hero: dict[str, Any], bard: dict[str, Any],
    write_save,
) -> None:
    engine = Dnd2024CombatEngine(runtime.load_bundle(LOCALE), EncounterAccess.sandbox())
    for game_key in E2E_DND_CUTTING_GAME_KEYS:
        instance = _instance(game_key, "D&D 2024 刻薄言语桌", {
            "e2e-gm": {"character_name": "新手守护者", "character_sheet": hero},
            "e2e-player": {"character_name": "刻薄言语诗人", "character_sheet": bard},
        })
        _start_and_hit(instance, engine, [1, 1, 20])
        # Both faces are declined up front so the saved state is the bard's
        # damage window; the browser then performs the real spend decision.
        lucky = _pending(instance)[0]
        _apply(engine, instance, _decision_intent(instance, lucky, "decline"), _SequenceRng([]))
        check = _pending(instance)[0]
        _apply(engine, instance, _decision_intent(instance, check, "decline"), _SequenceRng([4]))
        pending = _pending(instance)
        if len(pending) != 1 or pending[0]["window_code"] != "cutting_words_damage":
            raise RuntimeError(f"cutting fixture did not leave a damage window: {pending}")
        write_save(data_dir, instance)


def write_window_fixtures(
    data_dir, runtime: Dnd2024Runtime, base_hero: dict[str, Any], write_save,
) -> None:
    hero = _grant_lucky(runtime, base_hero)
    bard = _bard_sheet(runtime)
    _write_lucky(data_dir, runtime, hero, write_save)
    _write_cutting(data_dir, runtime, hero, bard, write_save)
