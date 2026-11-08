"""S0-F1: one D20 skill formula, Expertise, Jack of All Trades, Reliable Talent.

Everything here runs through the real chain the web layer uses, because "the
catalog declares ``expertise``" and "the sheet actually doubles the Proficiency
Bonus" are two very different claims:

* sheets are built with ``builder_choices`` -> ``finalize_character`` and grown
  with ``preview_advancement`` -> ``apply_advancement``, so a selection is always
  validated by the path that will own it;
* checks are resolved by ``resolve_intent`` -> ``apply_batch``, so a floored d20
  or an Advantage source is read from an event the engine produced, never from a
  value the test wrote;
* the only hand-written state is a *persisted* slot or condition (a legacy save,
  a forged record, a grappled-but-not-paralyzed body), which is exactly what the
  task's replay and fail-closed items are about.

The module follows the task's numbered matrix; each test names the item it
covers.  Deviations the matrix asks for but the engine cannot express yet (tool
proficiency checks, a Speed-0 creature that can still attack) are documented in
the execution report rather than faked here.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.derivation import derive_skill_check
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import advancement_choices, goblin, monk_instance

LOCALE = "en"
# 标准数组必须恰好用满 15/14/13/12/10/8；背景加值在此之上叠加。
BASE_ABILITIES = {"str": 8, "dex": 15, "con": 14, "int": 10, "wis": 12, "cha": 13}
ROGUE_SKILLS = [
    "skill:stealth", "skill:sleight_of_hand", "skill:perception", "skill:acrobatics",
]
BARD_SKILLS = ["skill:performance", "skill:persuasion", "skill:stealth"]
WIZARD_SKILLS = ["skill:history", "skill:investigation"]
RANGER_SKILLS = ["skill:stealth", "skill:perception", "skill:survival"]
FIGHTER_SKILLS = ["skill:athletics", "skill:perception"]
EXPERTISE_SLOT = ("class:rogue", "expertise", "expertise_skills")
SCHOLAR_OPTIONS = (
    "skill:arcana", "skill:history", "skill:investigation",
    "skill:medicine", "skill:nature", "skill:religion",
)


# --------------------------------------------------------------------------
# sheets: real builder + real advancement path
# --------------------------------------------------------------------------

def _spell_choices(runtime: Dnd2024Runtime, class_ref: str, level: int) -> dict:
    options = Dnd2024SpellSelection(runtime.load_bundle(LOCALE)).options(
        class_ref, level
    )
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


def _draft(
    runtime: Dnd2024Runtime,
    class_ref: str,
    package: str,
    skills: list[str],
    extra: dict | None = None,
) -> dict:
    """A level-1 draft whose guided answers follow this bundle."""

    draft: dict = {
        "locale": LOCALE,
        "name": "D20 Hero",
        "level": 1,
        "ability_method": "standard_array",
        "base_abilities": dict(BASE_ABILITIES),
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "background_equipment_package_ref": "equipment_package:acolyte_b",
        "alignment": "neutral",
        "class_ref": class_ref,
        "class_skill_refs": list(skills),
        "equipment_package_ref": package,
    }
    if extra:
        draft.update(extra)
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
    draft["language_refs"] = [
        "language:common", "language:dwarvish", "language:elvish",
    ]
    draft["feat_choice_answers"] = {
        feat["feat_ref"]: {
            spec["id"]: [option["value"] for option in spec["options"]][
                : int(spec["count"])
            ]
            for spec in feat["specs"]
        }
        for feat in choices["feat_choices"]
    }
    draft["class_spell_choices"] = _spell_choices(runtime, class_ref, 1)
    return draft


def _advance(
    runtime: Dnd2024Runtime,
    sheet: dict,
    target: int,
    class_ref: str,
    *,
    skill_picks: dict[tuple[str, str, str], list[str]] | None = None,
) -> dict:
    """Grow a sheet through the real level-up path to ``target``.

    ``skill_picks`` overrides the default "first available option" answer for a
    named slot, because several items are about *which* skill was chosen and in
    which order the two answers are consumed.
    """

    overrides = dict(skill_picks or {})
    while int(sheet["ruleset_character"]["build"]["level"]) < target:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        picks["class_spell_choices"] = _spell_choices(runtime, class_ref, next_level)
        for record in picks.get("feature_choices") or []:
            key = (record["owner"], record["feature_id"], record["choice_id"])
            if key in overrides:
                record["selected_refs"] = list(overrides[key])
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _creation_choices(
    picks: list[str] | None, *, extra: dict | None = None,
) -> dict | None:
    if picks is None:
        return extra
    record = {
        "owner": EXPERTISE_SLOT[0],
        "feature_id": EXPERTISE_SLOT[1],
        "choice_id": EXPERTISE_SLOT[2],
        "selected_refs": list(picks),
    }
    return {**(extra or {}), "feature_choices": [record]}


def _rogue(
    runtime: Dnd2024Runtime,
    level: int = 1,
    *,
    picks: list[str] | None = ("skill:stealth", "skill:sleight_of_hand"),
    skills: list[str] | None = None,
    skill_picks: dict | None = None,
) -> dict:
    draft = _draft(
        runtime, "class:rogue", "equipment_package:rogue_a",
        ROGUE_SKILLS if skills is None else skills,
        _creation_choices(list(picks) if picks else None),
    )
    sheet = runtime.finalize_character(None, draft)
    if level > 1:
        sheet = _advance(
            runtime, sheet, level, "class:rogue", skill_picks=skill_picks,
        )
    return sheet


def _bard(runtime: Dnd2024Runtime, level: int = 1, **kwargs) -> dict:
    sheet = runtime.finalize_character(None, _draft(
        runtime, "class:bard", "equipment_package:bard_a", BARD_SKILLS,
    ))
    return _advance(runtime, sheet, level, "class:bard", **kwargs) if level > 1 else sheet


def _wizard(runtime: Dnd2024Runtime, level: int = 1, **kwargs) -> dict:
    sheet = runtime.finalize_character(None, _draft(
        runtime, "class:wizard", "equipment_package:wizard_a", WIZARD_SKILLS,
    ))
    return _advance(runtime, sheet, level, "class:wizard", **kwargs) if level > 1 else sheet


def _ranger(runtime: Dnd2024Runtime, level: int = 1, **kwargs) -> dict:
    sheet = runtime.finalize_character(None, _draft(
        runtime, "class:ranger", "equipment_package:ranger_a", RANGER_SKILLS,
    ))
    return _advance(runtime, sheet, level, "class:ranger", **kwargs) if level > 1 else sheet


def _champion(runtime: Dnd2024Runtime, level: int = 3) -> dict:
    """A Champion in studded leather: Speed 30, no armor Speed penalty.

    ``fighter_a`` wears chain mail a Strength 8 character cannot carry, and the
    armor penalty would make "half your current Speed" read a number this file
    is not about.  ``fighter_b`` keeps the fixture on the plain 30 ft.
    """

    sheet = runtime.finalize_character(None, _draft(
        runtime, "class:fighter", "equipment_package:fighter_b", FIGHTER_SKILLS,
        {"class_feature_feat_choices": {"fighting_style": "feat:archery"}},
    ))
    return _advance(runtime, sheet, level, "class:fighter") if level > 1 else sheet


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _canonical(sheet: dict) -> dict:
    return sheet["ruleset_character"]


def _resolver(runtime: Dnd2024Runtime) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))


def _skill(value: dict, skill_id: str) -> int:
    return int(_canonical(value)["proficiencies"]["skill_values"][skill_id])


def _slots(preview: dict, kind: str = "feature_choice") -> list[dict]:
    return [
        requirement for requirement in preview.get("requirements") or []
        if requirement.get("kind") == kind
    ]


def _slot(preview: dict, feature_id: str) -> dict:
    return next(
        requirement for requirement in _slots(preview)
        if requirement["feature_id"] == feature_id
    )


class _CountingRng:
    """Deterministic d20 source that also counts how many dice were asked for."""

    def __init__(self, values=()) -> None:
        self.values = list(values)
        self.calls = 0

    def randint(self, minimum: int, maximum: int) -> int:
        self.calls += 1
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum, (minimum, value, maximum)
        return value


def _state(instance) -> dict:
    return ruleset_runtime.state(instance)


def _apply(engine, instance, intent: dict, rng: _CountingRng | None = None) -> list[dict]:
    rng = rng or _CountingRng()
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _combat(
    runtime: Dnd2024Runtime,
    sheet: dict,
    *,
    rolls=(20, 3),
    enemy_hp: int = 60,
    extra_players: dict[str, dict] | None = None,
):
    """Start a real combat with an enemy 5 ft away."""

    engine, instance = monk_instance(runtime, deepcopy(sheet))
    for uid, other in (extra_players or {}).items():
        instance.players[uid] = {
            "character_name": str(other.get("character_name") or "Ally"),
            "character_sheet": deepcopy(other),
        }
    intent = {
        "intent_id": "start",
        "type": "combat.start",
        "expected_version": 0,
        "submitted_by": "gm",
        "enemies": [goblin(position=5, hp=enemy_hp)],
    }
    rng = _CountingRng(rolls)
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return engine, instance


def _started(events: list[dict]) -> dict:
    return next(e for e in events if e.get("type") == "dnd2024.combat.started")


def _check(events: list[dict], kind: str) -> dict:
    return next(
        event for event in events
        if event.get("type") == "check.resolved" and event.get("kind") == kind
    )


def _attack_intent(
    instance, *, intent_id: str = "atk-1", weapon: str = "item:dagger",
):
    return {
        "intent_id": intent_id, "type": "attack",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": weapon,
    }


def _move_intent(instance, distance: int, *, intent_id: str = "mv-1") -> dict:
    return {
        "intent_id": intent_id, "type": "move",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "distance": distance,
    }


def _hide_intent(instance, *, intent_id: str = "hide-1") -> dict:
    return {
        "intent_id": intent_id, "type": "hide",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


# --------------------------------------------------------------------------
# 1-3. Expertise and Scholar: where the slot appears and what it accepts
# --------------------------------------------------------------------------

def test_expertise_is_projected_only_at_its_own_grant_level(
    runtime: Dnd2024Runtime,
) -> None:
    """1. Rogue 1 offers its slot; Bard/Wizard 1 do not offer theirs yet."""

    rogue = runtime.builder_choices(None, _draft(
        runtime, "class:rogue", "equipment_package:rogue_a", ROGUE_SKILLS,
    ))
    slots = rogue["skill_choices"]
    assert len(slots) == 1
    slot = slots[0]
    assert (slot["owner"], slot["feature_id"], slot["choice_id"]) == EXPERTISE_SLOT
    assert (slot["count"], slot["grants"]) == (2, "expertise")
    # 选项不隐藏，只标不可选原因：玩家要能区分"还不能选"和"不存在"。
    reasons = {
        option["value"]: option["unavailable_reasons"] for option in slot["options"]
    }
    assert reasons["skill:stealth"] == []
    assert reasons["skill:arcana"] == ["not_proficient"]
    assert len(slot["options"]) == len(runtime.load_bundle(LOCALE).list("skill"))

    for class_ref, package, skills in (
        ("class:bard", "equipment_package:bard_a", BARD_SKILLS),
        ("class:wizard", "equipment_package:wizard_a", WIZARD_SKILLS),
        ("class:ranger", "equipment_package:ranger_a", RANGER_SKILLS),
    ):
        choices = runtime.builder_choices(None, _draft(
            runtime, class_ref, package, skills,
        ))
        assert choices["skill_choices"] == [], class_ref


def test_a_second_pair_arrives_with_the_next_granted_level(
    runtime: Dnd2024Runtime,
) -> None:
    """1. Rogue 6, Bard 2/9 and Ranger 9 each add one further pair.

    The preview describes the *next* level, so "not yet" and "now" are read one
    step apart: Rogue 4 -> 5 has no slot, Rogue 5 -> 6 does, Rogue 6 -> 7 does
    not.
    """

    assert _slots(runtime.preview_advancement(None, _rogue(runtime, 4), {})) == []
    assert _slot(
        runtime.preview_advancement(None, _rogue(runtime, 5), {}), "expertise",
    )["count"] == 2
    assert _slots(runtime.preview_advancement(None, _rogue(runtime, 6), {})) == []
    assert len(_canonical(_rogue(runtime, 6))["features"]["feature_choices"]) == 2

    # Bard 的槽在 2 级：1 级建卡时没有，1 -> 2 的预览里才出现。
    assert _slots(runtime.preview_advancement(None, _bard(runtime, 1), {})) != []
    assert _slot(
        runtime.preview_advancement(None, _bard(runtime, 1), {}), "expertise",
    )["count"] == 2
    bard2 = _bard(runtime, 2)
    assert [record["granted_level"] for record in
            _canonical(bard2)["features"]["feature_choices"]] == [2]
    # 2 级交出这一对之后，2 -> 3 的预览里没有第二个槽（下一次在 9 级）。
    assert _slots(runtime.preview_advancement(None, bard2, {})) == []
    bard8 = _bard(runtime, 8)
    assert _slot(runtime.preview_advancement(None, bard8, {}), "expertise")["count"] == 2
    bard9 = _bard(runtime, 9)
    assert [record["granted_level"] for record in
            _canonical(bard9)["features"]["feature_choices"]] == [2, 9]

    # Ranger 7 -> 8 还没有这个槽；8 -> 9 才出现，且必须回答。
    assert _slots(runtime.preview_advancement(None, _ranger(runtime, 7), {})) == []
    ranger8 = _ranger(runtime, 8)
    preview = runtime.preview_advancement(None, ranger8, {})
    assert _slot(preview, "expertise")["count"] == 2
    unanswered = runtime.preview_advancement(None, ranger8, {
        "class_spell_choices": _spell_choices(runtime, "class:ranger", 9),
    })
    assert unanswered["ok"] is False
    assert any("expertise" in error for error in unanswered["errors"])


_CREATION_REJECTIONS: dict[str, tuple[list[str], str]] = {
    "too-few": (["skill:stealth"], "must select exactly 2 options"),
    "too-many": (
        ["skill:stealth", "skill:sleight_of_hand", "skill:perception"],
        "must select exactly 2 options",
    ),
    "repeats": (
        ["skill:stealth", "skill:stealth"], "selected_refs repeats an option",
    ),
    "not-proficient": (["skill:stealth", "skill:arcana"], "is not a skill proficiency"),
    "not-a-skill": (["skill:stealth", "tool:thieves_tools"], "is not a declared skill"),
    "unknown-skill": (["skill:stealth", "skill:nope"], "is not a declared skill"),
}


@pytest.mark.parametrize("case", sorted(_CREATION_REJECTIONS))
def test_creation_refuses_an_illegal_expertise_selection(
    runtime: Dnd2024Runtime, case: str,
) -> None:
    """1+21. Count, uniqueness and "you must already be proficient" are gated."""

    picks, message = _CREATION_REJECTIONS[case]
    draft = _draft(
        runtime, "class:rogue", "equipment_package:rogue_a", ROGUE_SKILLS,
        _creation_choices(picks),
    )

    errors = runtime.validate_character(None, draft)
    assert any(message in error for error in errors), errors
    with pytest.raises(ValueError):
        runtime.finalize_character(None, draft)


def test_a_slot_this_character_does_not_own_is_rejected(
    runtime: Dnd2024Runtime,
) -> None:
    """2+21. A record for a foreign owner, or an extra field, never lands."""

    foreign = _draft(
        runtime, "class:rogue", "equipment_package:rogue_a", ROGUE_SKILLS,
        {"feature_choices": [{
            "owner": "class:bard", "feature_id": "expertise",
            "choice_id": "expertise_skills",
            "selected_refs": ["skill:stealth", "skill:athletics"],
        }]},
    )
    errors = runtime.validate_character(None, foreign)
    assert any("does not have" in error for error in errors), errors

    smuggled = _draft(
        runtime, "class:rogue", "equipment_package:rogue_a", ROGUE_SKILLS,
        {"feature_choices": [{
            "owner": EXPERTISE_SLOT[0], "feature_id": EXPERTISE_SLOT[1],
            "choice_id": EXPERTISE_SLOT[2],
            "selected_refs": ["skill:stealth", "skill:sleight_of_hand"],
            "granted_level": 9,
        }]},
    )
    errors = runtime.validate_character(None, smuggled)
    assert any("unknown field" in error for error in errors), errors


def test_the_same_named_slots_never_cross_owners(runtime: Dnd2024Runtime) -> None:
    """2. ``expertise`` is identity-scoped: a Bard record never pays a Rogue."""

    resolver = _resolver(runtime)
    rogue = _canonical(_rogue(runtime, 6))
    owned = resolver.expertise_skills(rogue)
    # Rogue 6：1 级两项 + 6 级两项，四个技能，来源都只写 Rogue 自己的槽。
    assert len(owned) == 4
    assert {row["owner"] for row in owned} == {"class:rogue"}
    assert {"skill:stealth", "skill:sleight_of_hand"} <= {
        row["skill_ref"] for row in owned
    }

    # 伪造：把 Bard 的槽写进 Rogue 卡（历史存档/恶意提交都可能这样）。
    forged = deepcopy(rogue)
    forged["features"]["feature_choices"].append({
        "owner": "class:bard", "feature_id": "expertise",
        "choice_id": "expertise_skills",
        "selected_refs": ["skill:perception", "skill:athletics"],
        "granted_level": 2,
    })
    sources = {row["skill_ref"]: row["owner"] for row in resolver.expertise_skills(forged)}
    assert "skill:perception" not in sources
    assert sources["skill:stealth"] == "class:rogue"

    # 反方向同样成立：Bard 卡上的 Rogue 槽不生效。
    bard = deepcopy(_canonical(_bard(runtime, 2)))
    bard["features"]["feature_choices"].append({
        "owner": "class:rogue", "feature_id": "expertise",
        "choice_id": "expertise_skills",
        "selected_refs": ["skill:stealth", "skill:sleight_of_hand"],
        "granted_level": 1,
    })
    owners = {row["owner"] for row in resolver.expertise_skills(bard)}
    assert owners == {"class:bard"}


def test_two_records_for_one_granted_level_void_the_selection(
    runtime: Dnd2024Runtime,
) -> None:
    """2. Duplicated answers inside one slot are ambiguous, not "first wins"."""

    resolver = _resolver(runtime)
    sheet = _canonical(_rogue(runtime, 1))
    duplicate = deepcopy(sheet)
    duplicate["features"]["feature_choices"].append(deepcopy(
        duplicate["features"]["feature_choices"][0]
    ))

    definition = next(
        item for item in resolver.features_for(duplicate)
        if item.id == "expertise"
    )
    assert resolver.feature_selection(duplicate, definition)["selected_refs"] == []
    assert resolver.expertise_skills(duplicate) == []


def test_scholar_offers_exactly_the_six_declared_skills(
    runtime: Dnd2024Runtime,
) -> None:
    """3. The whitelist is the six named skills, one choice, no widening."""

    wizard2 = _wizard(runtime, 2)
    preview = runtime.preview_advancement(None, _wizard(runtime, 1), {})
    assert _slot(preview, "scholar")["count"] == 1
    slot = next(
        requirement for requirement in
        runtime.preview_advancement(None, _wizard(runtime, 1), {})["requirements"]
        if requirement.get("feature_id") == "scholar"
    )
    assert [option["value"] for option in slot["options"]] == list(SCHOLAR_OPTIONS)

    recorded = _canonical(wizard2)["features"]["feature_choices"]
    assert len(recorded) == 1
    assert recorded[0]["feature_id"] == "scholar"
    assert recorded[0]["selected_refs"][0] in SCHOLAR_OPTIONS
    assert _skill(wizard2, recorded[0]["selected_refs"][0][6:]) == 5


@pytest.mark.parametrize(
    ("picked", "message"),
    (
        ("skill:arcana", "not available: not_proficient"),
        ("skill:athletics", "is not a declared option"),
    ),
)
def test_scholar_refuses_untrained_and_out_of_list_skills(
    runtime: Dnd2024Runtime, picked: str, message: str,
) -> None:
    """3. Wizard 2 cannot take Expertise in a skill it lacks or was not offered."""

    wizard1 = _wizard(runtime, 1)
    preview = runtime.preview_advancement(None, wizard1, {})
    picks = advancement_choices(preview)
    picks["class_spell_choices"] = _spell_choices(runtime, "class:wizard", 2)
    for record in picks["feature_choices"]:
        if record["feature_id"] == "scholar":
            record["selected_refs"] = [picked]

    refused = runtime.preview_advancement(None, wizard1, picks)
    assert refused["ok"] is False
    assert any(message in error for error in refused["errors"]), refused["errors"]


def test_the_second_pair_cannot_repeat_an_expertise_skill(
    runtime: Dnd2024Runtime,
) -> None:
    """3+1. "You can't have Expertise in the same skill more than once"."""

    rogue5 = _rogue(runtime, 5)
    preview = runtime.preview_advancement(None, rogue5, {})
    picks = advancement_choices(preview)
    picks["class_spell_choices"] = _spell_choices(runtime, "class:rogue", 6)
    for record in picks["feature_choices"]:
        if record["feature_id"] == "expertise":
            record["selected_refs"] = ["skill:stealth", "skill:perception"]

    refused = runtime.preview_advancement(None, rogue5, picks)
    assert refused["ok"] is False
    assert any(
        "not available: already_expertise" in error for error in refused["errors"]
    ), refused["errors"]


# --------------------------------------------------------------------------
# 4-8. The single bonus formula: 0 / PB / 2xPB / floor(PB/2)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bonus", (2, 3, 4, 5, 6))
def test_one_formula_covers_zero_proficiency_and_expertise(bonus: int) -> None:
    """4. Contributions are exactly 0, PB and 2xPB at every Proficiency Bonus."""

    plain = derive_skill_check(14, proficiency_bonus=bonus, proficient=False)
    trained = derive_skill_check(14, proficiency_bonus=bonus, proficient=True)
    expert = derive_skill_check(
        14, proficiency_bonus=bonus, proficient=True, expertise=True,
    )

    assert (plain["contribution"], plain["total"]) == (0, 2)
    assert (trained["contribution"], trained["total"]) == (bonus, 2 + bonus)
    assert (expert["contribution"], expert["total"]) == (2 * bonus, 2 + 2 * bonus)
    assert [row["code"] for row in expert["components"]] == ["ability", "expertise"]
    assert expert["expertise"] is True and expert["half_proficiency"] is False


@pytest.mark.parametrize(("bonus", "expected"), ((2, 1), (3, 1), (4, 2), (5, 2), (6, 3)))
def test_jack_of_all_trades_adds_floor_of_pb_over_two(
    bonus: int, expected: int,
) -> None:
    """6. Half the Proficiency Bonus, rounded down, at PB 2..6."""

    result = derive_skill_check(
        14, proficiency_bonus=bonus, proficient=False, half_proficiency=True,
    )
    assert result["contribution"] == expected
    assert result["half_proficiency"] is True
    assert [row["code"] for row in result["components"]] == [
        "ability", "half_proficiency",
    ]


def test_half_proficiency_never_stacks_with_proficiency(
    runtime: Dnd2024Runtime,
) -> None:
    """7. Jack never rides on top of proficiency, Expertise or another half."""

    trained = derive_skill_check(
        14, proficiency_bonus=4, proficient=True, half_proficiency=True,
    )
    expert = derive_skill_check(
        14, proficiency_bonus=4, proficient=True, expertise=True,
        half_proficiency=True,
    )
    spent = derive_skill_check(
        14, proficiency_bonus=4, proficient=False, half_proficiency=True,
        pb_used_elsewhere=True,
    )
    assert trained["contribution"] == 4
    assert expert["contribution"] == 8
    assert spent["contribution"] == 0

    # 端到端：Bard 2 同时拥有 Jack 与 Expertise，两条渠道各付一次。
    bard2 = _bard(runtime, 2)
    assert _skill(bard2, "acrobatics") == 2 + 2 * 2      # Dexterity 精通 + 专精
    assert _skill(bard2, "stealth") == 2 + 2             # 熟练，不加半熟练
    assert _skill(bard2, "athletics") == -1 + 1          # 无熟练，加 floor(PB/2)


def test_jack_is_not_used_for_initiative_saves_attacks_or_tools(
    runtime: Dnd2024Runtime,
) -> None:
    """8. Jack is a skill-check bonus and nothing else."""

    bard1 = _canonical(_bard(runtime, 1))
    bard2 = _canonical(_bard(runtime, 2))
    assert bard2["derived"]["initiative"] == bard1["derived"]["initiative"]
    # 豁免不吃半熟练：Bard 未熟练的力量豁免就是属性调整值本身。
    assert bard2["derived"]["saving_throws"]["str"] == -1
    assert bard2["derived"]["saving_throws"] == bard1["derived"]["saving_throws"]
    # 工具检定没有派生加值面（§7 非目标），因此也不可能被半熟练污染。
    assert "tool_values" not in bard2["proficiencies"]

    engine = Dnd2024CombatEngine(runtime.load_bundle(LOCALE))
    attacks1 = engine._available_attacks(engine._player_view("gm", bard1))
    attacks2 = engine._available_attacks(engine._player_view("gm", bard2))
    assert attacks2 == attacks1

    # 攻击检定本身：Dexterity 15 (+2) 加熟练 PB 2 = +4，Jack 不能变成 +5。
    engine, instance = _combat(runtime, _bard(runtime, 2))
    events = _apply(
        engine, instance, _attack_intent(instance), _CountingRng([15, 1]),
    )
    attack = _check(events, "attack")
    assert attack["modifier"] == 4
    assert "half_proficiency" not in attack


def test_a_second_expertise_source_still_doubles_once(
    runtime: Dnd2024Runtime,
) -> None:
    """5. Two records for one skill are one Expertise: PB is never tripled."""

    resolver = _resolver(runtime)
    sheet = _rogue(runtime, 6)
    assert _skill(sheet, "stealth") == 2 + 2 * 3

    forged = deepcopy(_canonical(sheet))
    forged["features"]["feature_choices"].append({
        "owner": "class:rogue", "feature_id": "expertise",
        "choice_id": "expertise_skills",
        "selected_refs": ["skill:stealth", "skill:perception"],
        "granted_level": 9,
    })
    entries = [row for row in resolver.expertise_skills(forged)
               if row["skill_ref"] == "skill:stealth"]
    assert len(entries) == 1

    # 同一结论只走一个入口：重复来源不会让 PB 变成三倍。
    assert derive_skill_check(
        15, proficiency_bonus=3, proficient=True, expertise=True,
    )["total"] == 2 + 2 * 3


def test_equipment_changes_do_not_touch_the_skill_formula(
    runtime: Dnd2024Runtime,
) -> None:
    """5+16. Re-projection after a gear change keeps the same doubling."""

    resolver = _resolver(runtime)
    sheet = _rogue(runtime, 6)
    canonical = _canonical(sheet)
    stripped = deepcopy(sheet)
    stripped["ruleset_character"]["equipment"]["item_refs"] = []
    stripped.update(resolver.projection_fields(stripped["ruleset_character"]))

    assert _skill(stripped, "stealth") == _skill(sheet, "stealth") == 8
    assert canonical["proficiencies"]["skill_refs"].count("skill:stealth") == 1


def test_every_stored_skill_value_matches_the_one_formula(
    runtime: Dnd2024Runtime,
) -> None:
    """4+16. A split formula would show up as a stored value it cannot rebuild."""

    resolver = _resolver(runtime)
    for sheet in (_rogue(runtime, 6), _bard(runtime, 2), _wizard(runtime, 2)):
        canonical = _canonical(sheet)
        expertise = {
            row["skill_ref"] for row in resolver.expertise_skills(canonical)
        }
        half = resolver.half_proficiency_bonus(canonical)
        proficient = set(canonical["proficiencies"]["skill_refs"])
        bonus = int(canonical["derived"]["proficiency_bonus"])
        abilities = canonical["abilities"]
        bundle = runtime.load_bundle(LOCALE)
        for skill in bundle.list("skill"):
            ref = f"skill:{skill['id']}"
            ability = str(skill["ability_ref"]).split(":", 1)[-1]
            expected = derive_skill_check(
                int(abilities[ability]),
                proficiency_bonus=bonus,
                proficient=ref in proficient,
                expertise=ref in expertise,
                half_proficiency=half,
            )["total"]
            assert canonical["proficiencies"]["skill_values"][skill["id"]] == expected


def test_passive_perception_reuses_the_same_aggregate(
    runtime: Dnd2024Runtime,
) -> None:
    """4. Passive Perception is 10 + the derived skill, never a second formula."""

    bard2 = _canonical(_bard(runtime, 2))
    assert bard2["derived"]["passive_perception"] == 10 + _skill(
        {"ruleset_character": bard2}, "perception",
    )


# --------------------------------------------------------------------------
# 9-12. Reliable Talent: a substituted face, not a reroll
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "effective"), ((1, 10), (9, 10), (10, 10), (20, 20)),
)
def test_reliable_talent_treats_low_faces_as_ten(
    runtime: Dnd2024Runtime, raw: int, effective: int,
) -> None:
    """9+12. 1-9 become 10, 10+ are untouched, and 20 is never broken."""

    engine, instance = _combat(runtime, _rogue(runtime, 7), rolls=(20, 3))
    rng = _CountingRng([raw])
    events = _apply(engine, instance, _hide_intent(instance), rng)
    check = _check(events, "hide")

    assert rng.calls == 1
    assert check["rolls"] == [raw]
    assert check["raw_d20"] == raw
    assert check["effective_d20"] == effective
    assert check["natural"] == effective
    assert check["d20_floor"] == 10
    assert check["d20_floor_sources"] == [
        {"code": "reliable_talent", "owner": "class:rogue"},
    ]
    assert check["modifier"] == _skill(_rogue(runtime, 7), "stealth") == 8
    assert check["total"] == effective + 8
    # 被替换的天然 1 不再按天然 1 结算：成功的判定只用替换后的面值。
    assert check["success"] is (effective + 8 >= check["target"])


def test_reliable_talent_needs_a_real_proficiency(
    runtime: Dnd2024Runtime,
) -> None:
    """10. An untrained skill is not floored, even with Reliable Talent owned."""

    sheet = _rogue(runtime, 7)
    engine, instance = _combat(runtime, sheet)
    combat = _state(instance)["combat"]
    actor = engine._actor_view(instance, combat, "player:gm")
    assert engine._skill_check_floor(actor, "stealth") == (10, [
        {"code": "reliable_talent", "owner": "class:rogue"},
    ])
    assert engine._skill_check_floor(actor, "medicine") == (0, [])

    # 端到端：倒地同伴的稳定检定用的是未熟练的 Medicine，天然 1 仍是 1。
    downed = deepcopy(_rogue(runtime, 1))
    downed["character_name"] = "Fallen"
    downed["ruleset_character"]["resources"]["hp"] = 0
    engine, instance = _combat(
        runtime, sheet, rolls=(20, 5, 3), extra_players={"p2": downed},
    )
    events = _apply(engine, instance, {
        "intent_id": "stab-1", "type": "stabilize",
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "target_id": "player:p2",
    }, _CountingRng([1]))
    check = _check(events, "medicine")
    assert check["rolls"] == [1]
    assert "d20_floor" not in check
    assert check["total"] == 1 + 1
    assert check["success"] is False


def test_reliable_talent_is_not_used_for_initiative_attacks_or_saves(
    runtime: Dnd2024Runtime,
) -> None:
    """10. Initiative, attack rolls and saving throws never take the floor."""

    engine, instance = _combat(runtime, _rogue(runtime, 7), rolls=(1, 3))
    rolls = _state(instance)["combat"]["initiative_rolls"]
    assert rolls[0]["roll"] == 1 and "advantage_sources" not in rolls[0]

    events = _apply(engine, instance, _attack_intent(instance), _CountingRng([1, 1]))
    attack = _check(events, "attack")
    assert attack["natural"] == 1
    assert "d20_floor" not in attack


def test_the_floored_face_still_takes_the_full_skill_bonus(
    runtime: Dnd2024Runtime,
) -> None:
    """11. Order is floor -> modifier -> total, and the batch replays to the same."""

    sheet = _rogue(runtime, 7)
    engine, instance = _combat(runtime, sheet)
    intent = _hide_intent(instance)
    rng = _CountingRng([2])
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    check = _check(resolved["event_batch"]["events"], "hide")

    assert (check["raw_d20"], check["effective_d20"], check["total"]) == (2, 10, 18)
    # 同一请求重复提交是重放（不改版本、不再掷骰），骰迹逐字节相同。
    again = engine.resolve_intent(instance, intent, _CountingRng([2]))
    assert again["replayed"] is True
    assert _check(again["event_batch"]["events"], "hide") == check


# --------------------------------------------------------------------------
# 13-15. Remarkable Athlete: Initiative and Strength (Athletics) only
# --------------------------------------------------------------------------

def test_remarkable_athlete_arrives_with_the_champion_at_level_three(
    runtime: Dnd2024Runtime,
) -> None:
    """13. The declaration is a Champion 3 feature, not a Fighter one."""

    resolver = _resolver(runtime)
    definition = resolver.catalog.features_by_key[("subclass:champion", "remarkable_athlete")]
    assert (definition.owner, definition.minimum_level) == ("subclass:champion", 3)
    assert definition.parameters == {
        "initiative_advantage": True,
        "skill_advantage_refs": ["skill:athletics"],
        "critical_hit_move_speed_divisor": 2,
    }

    assert resolver.initiative_advantage_sources(_canonical(_champion(runtime, 3))) == [
        {"code": "remarkable_athlete", "owner": "subclass:champion"},
    ]
    assert resolver.initiative_advantage_sources(_canonical(_champion(runtime, 2))) == []


def test_remarkable_athlete_covers_initiative_and_athletics_only(
    runtime: Dnd2024Runtime,
) -> None:
    """14. Every other skill, save and attack roll is untouched."""

    resolver = _resolver(runtime)
    champion = _canonical(_champion(runtime, 3))
    assert resolver.skill_advantage_sources(champion, "athletics") == [
        {"code": "remarkable_athlete", "owner": "subclass:champion"},
    ]
    for skill in ("stealth", "perception", "acrobatics", "intimidation", "survival"):
        assert resolver.skill_advantage_sources(champion, skill) == [], skill
    # 力量豁免不是技能检定，也不吃"运动"这条。
    assert champion["derived"]["saving_throws"]["str"] == (
        _canonical(_champion(runtime, 2))["derived"]["saving_throws"]["str"]
    )

    # 端到端：攻击检定仍是一枚 d20，事件里没有任何优势来源。
    engine, instance = _combat(runtime, _champion(runtime, 3))
    events = _apply(
        engine, instance,
        _attack_intent(instance, weapon="item:scimitar"),
        _CountingRng([15, 4]),
    )
    attack = _check(events, "attack")
    assert attack["rolls"] == [15]
    assert attack["advantage_sources"] == []
    assert attack["advantage_mode"] == ""


def test_advantage_sources_never_add_dice(runtime: Dnd2024Runtime) -> None:
    """15. Remarkable Athlete plus Invisible is still two d20s, both sources kept."""

    sheet = _champion(runtime, 3)
    sheet["ruleset_character"]["conditions"] = {"invisible": True}
    engine, instance = _combat(runtime, sheet, rolls=(20, 15, 3))
    rolls = _state(instance)["combat"]["initiative_rolls"]
    player = rolls[0]

    assert sorted(player["rolls"]) == [15, 20]
    assert player["roll"] == 20
    assert player["advantage_mode"] == "advantage"
    assert [row["code"] for row in player["advantage_sources"]] == [
        "remarkable_athlete", "invisible",
    ]
    # 先攻加值仍是 Dexterity + Alert 的 PB，没有第三枚骰，也没有额外加值。
    assert player["total"] == 20 + player["modifier"]


# --------------------------------------------------------------------------
# 16. Save, load and rebuild: one authority, no stale copies
# --------------------------------------------------------------------------

def test_a_saved_sheet_keeps_its_choices_values_and_sources(
    runtime: Dnd2024Runtime,
) -> None:
    """16. A JSON round trip then a level-up rebuild the same numbers."""

    sheet = _rogue(runtime, 6)
    reloaded = json.loads(json.dumps(sheet))
    assert _canonical(reloaded)["features"]["feature_choices"] == (
        _canonical(sheet)["features"]["feature_choices"]
    )

    engine, instance = monk_instance(runtime, reloaded)
    loaded = instance.get_character_sheet("gm")
    assert _skill(loaded, "stealth") == 8

    advanced = _advance(runtime, loaded, 7, "class:rogue")
    assert _skill(advanced, "stealth") == 8
    assert [
        record["granted_level"]
        for record in _canonical(advanced)["features"]["feature_choices"]
    ] == [1, 6]
    # 7 级只多出 Reliable Talent 的声明，不会顺手改写入侵过的技能值。
    assert _resolver(runtime).expertise_skills(_canonical(advanced)) == (
        _resolver(runtime).expertise_skills(_canonical(loaded))
    )


def test_a_reloaded_champion_still_rolls_initiative_with_advantage(
    runtime: Dnd2024Runtime,
) -> None:
    """16. The Advantage mode is rebuilt from declarations, not stored state."""

    sheet = json.loads(json.dumps(_champion(runtime, 3)))
    engine, instance = _combat(runtime, sheet, rolls=(3, 19, 1))
    player = _state(instance)["combat"]["initiative_rolls"][0]

    assert player["rolls"] == [3, 19]
    assert player["roll"] == 19
    assert [row["code"] for row in player["advantage_sources"]] == [
        "remarkable_athlete",
    ]


def test_a_legacy_sheet_without_choices_is_not_silently_upgraded(
    runtime: Dnd2024Runtime,
) -> None:
    """16. A Rogue 1 save from before this slice keeps an un-doubled Stealth."""

    draft = _draft(
        runtime, "class:rogue", "equipment_package:rogue_a", ROGUE_SKILLS,
    )
    sheet = runtime.finalize_character(None, draft)
    assert _canonical(sheet)["features"]["feature_choices"] == []
    # 未作答 = 尚未选择，不是"默认专精两项"：加值仍是 熟练 PB 一次。
    assert _skill(sheet, "stealth") == 2 + _canonical(sheet)["derived"]["proficiency_bonus"]
    assert _resolver(runtime).expertise_skills(_canonical(sheet)) == []


# --------------------------------------------------------------------------
# 17-19. The move a Critical Hit pays for
# --------------------------------------------------------------------------

def _granted(events: list[dict]) -> list[dict]:
    return [event for event in events if event.get("type") == "dnd2024.movement.granted"]


def test_a_critical_hit_grants_half_the_current_speed(
    runtime: Dnd2024Runtime,
) -> None:
    """17. Champion 3 crits: half of Speed 30 in the pool, no Opportunity Attack."""

    engine, instance = _combat(runtime, _champion(runtime, 3))
    economy = _state(instance)["combat"]["economy"]
    assert economy["speed"] == 30

    events = _apply(
        engine, instance,
        _attack_intent(instance, weapon="item:scimitar"),
        _CountingRng([20, 4, 5]),
    )
    assert _check(events, "attack")["critical"] is True
    granted = _granted(events)
    assert len(granted) == 1
    assert granted[0]["amount"] == 15
    assert granted[0]["reason"] == "critical_hit"
    assert (granted[0]["source_feature"], granted[0]["source_owner"]) == (
        "remarkable_athlete", "subclass:champion",
    )
    state = _state(instance)["combat"]
    assert state["economy"]["movement"] == 45
    assert state["economy"]["crit_move"] == 15

    # 这一次移动在额度之内：借机攻击不开窗，事件自己带着抑制标记。
    moved = _apply(engine, instance, _move_intent(instance, 15))
    changed = next(e for e in moved if e.get("type") == "dnd2024.position.changed")
    assert changed["crit_move"] is True
    assert _state(instance)["combat"]["pending_decisions"] == []
    assert _state(instance)["combat"]["economy"]["crit_move"] == 0


def test_a_normal_hit_grants_nothing(runtime: Dnd2024Runtime) -> None:
    """17. A hit that is not a Critical Hit pays no movement."""

    engine, instance = _combat(runtime, _champion(runtime, 3))
    events = _apply(
        engine, instance,
        _attack_intent(instance, weapon="item:scimitar"),
        _CountingRng([15, 4]),
    )
    attack = _check(events, "attack")
    assert attack["natural"] == 15 and attack["critical"] is False
    assert attack["success"] is True
    assert _granted(events) == []
    assert _state(instance)["combat"]["economy"].get("crit_move") is None


def test_moving_past_the_budget_provokes_again(runtime: Dnd2024Runtime) -> None:
    """18. Over-budget and over-pool movement are refused / not covered."""

    engine, instance = _combat(runtime, _champion(runtime, 3))
    _apply(
        engine, instance,
        _attack_intent(instance, weapon="item:scimitar"),
        _CountingRng([20, 4, 5]),
    )

    # 超出剩余移动池：整条意图被拒绝，位置与配额都不动。
    refused = engine.validate_intent(instance, _move_intent(instance, 100))
    assert refused["ok"] is False
    assert "exceeds the remaining speed" in refused["error"]
    assert _state(instance)["combat"]["positions"]["player:gm"] == 0

    # 超出额度但仍合法：借机攻击照常开窗，且这一动不带抑制标记。
    events = _apply(engine, instance, _move_intent(instance, 20))
    pending = next(
        event for event in events if event.get("type") == "dnd2024.decision.pending"
    )
    assert pending["kind"] == "opportunity_attack"
    assert pending["movement"] == {"actor_id": "player:gm", "distance": 20}


def test_a_zero_speed_creature_gains_nothing_and_cannot_move(
    runtime: Dnd2024Runtime,
) -> None:
    """18. Speed 0 means 0: no allowance, and the move itself is refused."""

    sheet = _champion(runtime, 3)
    # 2024 的伤害性麻痹会同时把速度压到 0；这里只借它表达"当前 Speed = 0"
    # 这一个事实（该状态下角色本来也无法出手，因此额度守卫直接断言在结算层）。
    sheet["ruleset_character"]["conditions"] = {"paralyzed": True}
    engine, instance = _combat(runtime, sheet)
    state = _state(instance)["combat"]
    assert state["economy"]["speed"] == 0
    assert state["economy"]["movement"] == 0

    assert engine._critical_hit_move_events(instance, state, "player:gm") == []
    refused = engine.validate_intent(instance, _move_intent(instance, 5))
    assert refused["ok"] is False
    assert "speed zero" in refused["error"]


def test_only_the_owning_class_grants_the_critical_move(
    runtime: Dnd2024Runtime,
) -> None:
    """19. A Fighter 2, or a forged subclass on a Rogue, gets no allowance."""

    resolver = _resolver(runtime)
    assert resolver.critical_hit_move(_canonical(_champion(runtime, 2))) is None

    engine, instance = _combat(runtime, _champion(runtime, 2))
    events = _apply(
        engine, instance,
        _attack_intent(instance, weapon="item:scimitar"),
        _CountingRng([20, 4, 5]),
    )
    assert _check(events, "attack")["critical"] is True
    assert _granted(events) == []

    rogue = deepcopy(_canonical(_rogue(runtime, 6)))
    rogue["build"] = {"class_levels": [{"class_ref": "class:rogue", "level": 6}]}
    rogue["features"]["subclass_ref"] = "subclass:champion"
    assert resolver.critical_hit_move(rogue) is None
    assert resolver.initiative_advantage_sources(rogue) == []


# --------------------------------------------------------------------------
# 20-21. Ordering and fail-closed boundaries
# --------------------------------------------------------------------------

def test_the_attack_settles_before_the_move_is_granted(
    runtime: Dnd2024Runtime,
) -> None:
    """20. Check -> damage -> allowance, and a duplicate submit stays a replay."""

    engine, instance = _combat(runtime, _champion(runtime, 3))
    intent = _attack_intent(instance, weapon="item:scimitar")
    resolved = engine.resolve_intent(instance, intent, _CountingRng([20, 4, 5]))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True

    kinds = [event["type"] for event in resolved["event_batch"]["events"]]
    assert kinds.index("check.resolved") < kinds.index("resource.changed")
    assert kinds.index("resource.changed") < kinds.index("dnd2024.movement.granted")
    # 同一请求重放不会重复发放额度。
    again = engine.resolve_intent(instance, intent, _CountingRng([20, 4, 5]))
    assert again["replayed"] is True
    assert _state(instance)["combat"]["economy"]["movement"] == 45


def test_a_client_supplied_face_never_replaces_the_server_roll(
    runtime: Dnd2024Runtime,
) -> None:
    """21. Dice the caller attaches are inert; only the server's d20 counts."""

    engine, instance = _combat(runtime, _rogue(runtime, 7))
    forgery = {
        **_hide_intent(instance),
        "natural": 20, "effective_d20": 20, "total": 99, "rolls": [20],
    }
    events = _apply(engine, instance, forgery, _CountingRng([1]))
    check = _check(events, "hide")

    assert check["rolls"] == [1]
    assert (check["raw_d20"], check["effective_d20"], check["total"]) == (1, 10, 18)
    assert check["success"] is (18 >= check["target"])


def test_an_illegal_advancement_leaves_the_sheet_untouched(
    runtime: Dnd2024Runtime,
) -> None:
    """21. A rejected selection is a transaction that never happened."""

    rogue5 = _rogue(runtime, 5)
    before = deepcopy(rogue5)
    preview = runtime.preview_advancement(None, rogue5, {})
    picks = advancement_choices(preview)
    picks["class_spell_choices"] = _spell_choices(runtime, "class:rogue", 6)
    for record in picks["feature_choices"]:
        record["selected_refs"] = ["skill:stealth", "skill:arcana"]

    refused = runtime.preview_advancement(None, rogue5, picks)
    assert refused["ok"] is False
    assert any("skill:arcana" in error for error in refused["errors"])
    with pytest.raises(ValueError):
        runtime.apply_advancement(None, rogue5, picks)
    assert rogue5 == before == json.loads(json.dumps(before))
