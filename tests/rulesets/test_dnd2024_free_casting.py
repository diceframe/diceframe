"""S0-F5: free-cast slots that never become a shared "free spell" flag.

Seven declarations are checked here: Contact Patron, the four Mystic Arcana,
Spell Mastery and Signature Spells.  Each owns its own pool with its own
recharge, and every one of them still pays the ordinary casting rules -- action
economy, components, targets, concentration, and the ``not_automated`` wall.

The item numbers in the docstrings are the task spec's matrix.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine, RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
from src.rulesets.events import EventBatchError, stable_batch_id

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    monk_instance,
    start_combat,
)

LOCALE = "en"

ABILITIES = {
    "class:warlock": {"str": 8, "dex": 13, "con": 14, "int": 12, "wis": 10, "cha": 15},
    "class:wizard": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
}

# The arcanum picks, in the order the class table unlocks them.
ARCANA = {
    11: ("mystic_arcanum_6", "spell:circle_of_death"),
    13: ("mystic_arcanum_7", "spell:forcecage"),
    15: ("mystic_arcanum_8", "spell:demiplane"),
    17: ("mystic_arcanum_9", "spell:foresight"),
}
MASTERY = {"spell_mastery_1": "spell:magic_missile", "spell_mastery_2": "spell:scorching_ray"}
SIGNATURE = {"signature_1": "spell:dispel_magic", "signature_2": "spell:protection_from_energy"}
BOOK_EXTRAS = ["spell:scorching_ray", "spell:dispel_magic", "spell:protection_from_energy"]


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def selection(runtime: Dnd2024Runtime) -> Dnd2024SpellSelection:
    return Dnd2024SpellSelection(runtime.load_bundle(LOCALE))


@pytest.fixture(scope="module")
def rest(runtime: Dnd2024Runtime) -> Dnd2024RestEngine:
    return Dnd2024RestEngine(runtime.load_bundle(LOCALE))


@pytest.fixture(scope="module")
def resolver(runtime: Dnd2024Runtime) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))


# --------------------------------------------------------------------------
# sheets through the real creation and advancement paths
# --------------------------------------------------------------------------


def _draft(runtime: Dnd2024Runtime, class_ref: str) -> dict:
    class_id = class_ref.removeprefix("class:")
    draft: dict = {
        "locale": LOCALE,
        "name": "Hero",
        "level": 1,
        "ability_method": "standard_array",
        "base_abilities": dict(ABILITIES[class_ref]),
        "class_ref": class_ref,
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "equipment_package_ref": f"equipment_package:{class_id}_a",
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
    draft["class_skill_refs"] = [
        item["ref"] for item in choices["class_skills"]
    ][: choices["class_skill_count"]]
    draft["species_skill_refs"] = [
        item["ref"] for item in choices["species_skills"]
        if item["ref"] not in draft["class_skill_refs"]
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
    draft["class_spell_choices"] = _spell_choices(
        Dnd2024SpellSelection(runtime.load_bundle(LOCALE)),
        class_ref,
        1,
        {"build": {"class_levels": [{"class_ref": class_ref, "level": 1}]}},
        BOOK_EXTRAS,
    )
    errors = runtime.validate_character(None, draft)
    assert errors == [], errors
    return draft


def _probe(canonical: dict, class_ref: str, level: int, subclass_ref: str = "") -> dict:
    probe = deepcopy(canonical)
    probe["build"] = {
        **(probe.get("build") or {}),
        "class_levels": [{"class_ref": class_ref, "level": level}],
    }
    features = probe.get("features")
    features = dict(features) if isinstance(features, dict) else {}
    features["subclass_ref"] = subclass_ref
    probe["features"] = features
    return probe


def _spell_choices(
    selection: Dnd2024SpellSelection,
    class_ref: str,
    level: int,
    character: dict,
    book_extras: list[str] | None = None,
) -> dict:
    options = selection.options(class_ref, level, character)
    requirements = options["requirements"]
    leveled = [row["ref"] for row in options["leveled_spells"]]
    prepared_count = int(requirements["prepared_spell_count"])
    minimum = int(requirements.get("spellbook_minimum") or 0)
    prepared = leveled[:prepared_count]
    spellbook: list[str] = []
    if minimum:
        spellbook = leveled[:minimum]
        # 研究型法术必须真的在法术书里：从准备区之后的位置逐个替换，既保住
        # "已准备 ⊆ 法术书"，又不会凭空多出一本法术书。
        free_slots = [
            index for index in range(len(spellbook) - 1, prepared_count - 1, -1)
        ]
        for ref in book_extras or []:
            # 只收这一级真的有资格抄进书里的法术：1 级角色没有 3 环术书页。
            if ref not in leveled or ref in spellbook or not free_slots:
                continue
            spellbook[free_slots.pop(0)] = ref
        prepared = spellbook[:prepared_count]
    return {
        "cantrip_refs": [row["ref"] for row in options["cantrips"]][
            : int(requirements["cantrip_count"])
        ],
        "prepared_spell_refs": prepared,
        "spellbook_refs": spellbook,
    }


_SHEET_CACHE: dict[tuple, dict] = {}


def _sheet(
    runtime: Dnd2024Runtime,
    class_ref: str,
    level: int,
    *,
    free_cast: dict[str, str] | None = None,
) -> dict:
    """A character built and advanced through the authoritative paths."""

    key = (class_ref, level, tuple(sorted((free_cast or {}).items())))
    cached = _SHEET_CACHE.get(key)
    if cached is not None:
        return deepcopy(cached)
    selection = Dnd2024SpellSelection(runtime.load_bundle(LOCALE))
    draft = _draft(runtime, class_ref)
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        canonical = sheet["ruleset_character"]
        next_level = int(canonical["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        for requirement in preview.get("requirements") or []:
            if requirement.get("id") != "ability_score_increases":
                continue
            allowed = requirement.get("allowed", "any")
            if allowed == "any":
                abilities = canonical.get("abilities") or {}
                picks["ability_score_increases"] = {
                    min(abilities, key=lambda key: int(abilities[key])): int(
                        requirement.get("total", 2)
                    )
                }
        subclass = str(
            picks.get("subclass_ref")
            or (canonical.get("features") or {}).get("subclass_ref")
            or ""
        )
        probe = _probe(canonical, class_ref, next_level, subclass)
        picks["class_spell_choices"] = _spell_choices(
            selection, class_ref, next_level, probe, BOOK_EXTRAS,
        )
        if free_cast:
            owned = {
                row["slot_id"] for row in selection.free_cast_slots(next_level, probe)
            }
            wanted = {key: ref for key, ref in free_cast.items() if key in owned}
            if wanted:
                picks["class_spell_choices"]["free_cast"] = wanted
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    _SHEET_CACHE[key] = deepcopy(sheet)
    return sheet


def _canonical(sheet: dict) -> dict:
    return sheet["ruleset_character"]


def _warlock(runtime: Dnd2024Runtime, level: int) -> dict:
    picks = {
        slot_id: ref for unlock, (slot_id, ref) in ARCANA.items() if level >= unlock
    }
    return _canonical(_sheet(runtime, "class:warlock", level, free_cast=picks))


def _wizard(runtime: Dnd2024Runtime, level: int) -> dict:
    picks: dict[str, str] = {}
    if level >= 18:
        picks.update(MASTERY)
    if level >= 20:
        picks.update(SIGNATURE)
    return _canonical(_sheet(runtime, "class:wizard", level, free_cast=picks))


def _slot(resolver: Dnd2024ClassFeatureResolver, character: dict, slot_id: str) -> dict:
    rows = {row["slot_id"]: row for row in resolver.free_cast_slots(character)}
    assert slot_id in rows, sorted(rows)
    return rows[slot_id]


def _block(character: dict) -> dict:
    return character["spellcasting"]["class"]


def _with_records(character: dict, **records: dict) -> dict:
    updated = deepcopy(character)
    _block(updated).setdefault("free_cast_slots", {}).update(deepcopy(records))
    return updated


# --------------------------------------------------------------------------
# combat helpers
# --------------------------------------------------------------------------


def _combat(runtime: Dnd2024Runtime, sheet: dict):
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance, hp=400)
    return engine, instance


def _combat_wizard(runtime: Dnd2024Runtime, level: int):
    picks: dict[str, str] = {}
    if level >= 18:
        picks.update(MASTERY)
    if level >= 20:
        picks.update(SIGNATURE)
    return _combat(runtime, _sheet(runtime, "class:wizard", level, free_cast=picks))


def _combat_warlock(runtime: Dnd2024Runtime, level: int):
    picks = {slot: ref for unlock, (slot, ref) in ARCANA.items() if level >= unlock}
    return _combat(runtime, _sheet(runtime, "class:warlock", level, free_cast=picks))


def _intent(instance, kind: str, **extra) -> dict:
    state = ruleset_runtime.state(instance)
    return {
        "intent_id": f"{kind}:{state['version']}",
        "type": kind,
        "expected_version": state["version"],
        "submitted_by": "gm",
        "actor_id": "player:gm",
        **extra,
    }


def _cast(
    engine, instance, spell_ref: str, slot_level: int,
    *, target: str = "enemy:goblin-1", **extra,
):
    # 全部取 4：d4 是这条链上下限最窄的骰面，4 对 d4/d6/d8/d20 都合法，
    # 所以同一个序列既能算伤害、也能掷施法攻击。
    return engine.resolve_intent(
        instance,
        _intent(
            instance, "cast_spell", spell_ref=spell_ref, slot_level=slot_level,
            target_id=target, **extra,
        ),
        SequenceRng([4] * 32),
    )


def _apply(engine, instance, result) -> list[str]:
    assert result["ok"], result
    applied = engine.apply_batch(instance, result["event_batch"])
    assert applied["applied"], applied
    return [str(event["type"]) for event in result["event_batch"]["events"]]


def _refresh_action(instance) -> None:
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    economy["action"] = 1
    economy["bonus_action"] = 1
    economy["slot_spell_cast"] = False


def _player(instance) -> dict:
    return instance.get_character_sheet("gm")["ruleset_character"]


# --------------------------------------------------------------------------
# 1-2: ownership and forgery
# --------------------------------------------------------------------------


def test_matrix_01_every_carrier_appears_at_its_own_level(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    expected = {
        (9, "contact_patron"),
        (11, "mystic_arcanum_6"),
        (13, "mystic_arcanum_7"),
        (15, "mystic_arcanum_8"),
        (17, "mystic_arcanum_9"),
    }
    for level in (1, 8, 9, 10, 11, 12, 13, 15, 17):
        owned = {row["slot_id"] for row in resolver.free_cast_slots(
            _warlock(runtime, level)
        )}
        want = {slot for unlock, slot in expected if level >= unlock}
        assert owned == want, level
    wizard = {
        17: set(),
        18: {"spell_mastery_1", "spell_mastery_2"},
        19: {"spell_mastery_1", "spell_mastery_2"},
        20: {"spell_mastery_1", "spell_mastery_2", "signature_1", "signature_2"},
    }
    for level, want in wizard.items():
        assert {
            row["slot_id"] for row in resolver.free_cast_slots(_wizard(runtime, level))
        } == want, level
    # 每个槽自报固定环阶与 recharge，不是共享的"免费"布尔值。
    assert _slot(resolver, _warlock(runtime, 17), "mystic_arcanum_8")["spell_level"] == 8
    assert _slot(resolver, _wizard(runtime, 20), "signature_2")["recharge"] == "short_rest"
    assert _slot(resolver, _wizard(runtime, 18), "spell_mastery_1")["uses_max"] == 0


def test_matrix_02_a_slot_that_is_not_owned_or_whose_level_is_forged_is_refused(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    warlock = _warlock(runtime, 11)
    probe = _probe(warlock, "class:warlock", 11, "subclass:fiend_patron")
    choices = _spell_choices(selection, "class:warlock", 11, probe)
    choices["free_cast"] = {"mystic_arcanum_5": "spell:circle_of_death"}
    _parsed, errors = selection.validate(
        "class:warlock", 11, choices, character=probe,
    )
    assert any("not a slot this character owns" in error for error in errors)

    wizard = _wizard(runtime, 18)
    wizard_probe = _probe(wizard, "class:wizard", 18, "subclass:evoker")
    wizard_choices = _spell_choices(selection, "class:wizard", 18, wizard_probe)
    wizard_choices["free_cast"] = {"mystic_arcanum_6": "spell:circle_of_death"}
    _parsed, errors = selection.validate(
        "class:wizard", 18, wizard_choices, character=wizard_probe,
    )
    assert any("not a slot this character owns" in error for error in errors)

    # 被改过环阶的记录不算槽：既不能施法，也不会被当成"已选"。
    forged = _with_records(
        _warlock(runtime, 11),
        mystic_arcanum_6={
            "spell_ref": "spell:circle_of_death",
            "spell_level": 5,
            "recharge": "long_rest",
            "uses_remaining": 1,
        },
    )
    row = _slot(resolver, forged, "mystic_arcanum_6")
    assert row["valid"] is False
    assert row["selected"] is True


# --------------------------------------------------------------------------
# 3-5: Contact Patron
# --------------------------------------------------------------------------


def test_matrix_03_contact_patron_prepares_contact_other_plane_without_capacity(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    nine = _warlock(runtime, 9)
    eight = _warlock(runtime, 8)
    view = selection.prepared_view(nine)
    entry = next(
        row for row in view["prepared"] if row["spell_ref"] == "spell:contact_other_plane"
    )
    source = entry["sources"][0]
    assert source["kind"] == "class_feature"
    assert source["owner"] == "class:warlock"
    assert source["feature_id"] == "contact_patron"
    assert source["granted_level"] == 9
    # 常驻但不占容量：已准备数与普通准备列表长度一致，授予单独计数。
    assert view["capacity"]["prepared_used"] == len(
        _block(nine)["prepared_spell_refs"]
    )
    assert view["capacity"]["granted"] == len(
        {row["spell_ref"] for row in resolver.spell_grant_rows(nine)}
    )
    assert view["capacity"]["prepared_used"] <= view["capacity"]["prepared"]
    assert selection.prepared_view(eight)["capacity"]["granted"] == len(
        {row["spell_ref"] for row in resolver.spell_grant_rows(eight)}
    )


def test_matrix_04_contact_patron_is_one_long_rest_use_and_never_a_short_rest_one(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
    rest: Dnd2024RestEngine,
) -> None:
    nine = _warlock(runtime, 9)
    row = _slot(resolver, nine, "contact_patron")
    assert row["source"] == "fixed"
    assert row["spell_ref"] == "spell:contact_other_plane"
    assert row["spell_level"] == 5
    assert row["recharge"] == "long_rest"
    assert row["uses_max"] == 1
    # 自动成功只是这一条授予的属性，不能挂到法术或普通槽上。
    assert row["save_auto_succeed"] is True
    assert all(
        grant["spell_ref"] != "spell:contact_other_plane" or "save_auto_succeed" not in grant
        for grant in resolver.spell_grant_rows(nine)
    )

    spent = _with_records(nine, contact_patron={
        "spell_ref": "spell:contact_other_plane",
        "spell_level": 5,
        "recharge": "long_rest",
        "uses_remaining": 0,
    })
    short = rest.complete_short_rest(spent)["character"]
    assert _slot(resolver, short, "contact_patron")["uses_remaining"] == 0
    long = rest.complete_long_rest(short)["character"]
    assert _slot(resolver, long, "contact_patron")["uses_remaining"] == 1


def test_matrix_05_contact_other_plane_is_not_automated_and_spends_nothing(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
    selection: Dnd2024SpellSelection,
) -> None:
    nine = _warlock(runtime, 9)
    assert selection.automation_status("spell:contact_other_plane") == "not_automated"
    engine, instance = _combat_warlock(runtime, 9)
    result = _cast(engine, instance, "spell:contact_other_plane", 0)
    assert result["ok"] is False
    assert "not deterministically automated" in str(result.get("error") or result)
    assert _slot(resolver, _player(instance), "contact_patron")["uses_remaining"] == 1
    assert ruleset_runtime.state(instance)["version"] == 1
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 1


# --------------------------------------------------------------------------
# 6-10: Mystic Arcanum
# --------------------------------------------------------------------------


def test_matrix_06_each_arcanum_only_takes_a_warlock_spell_of_its_own_level(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    seventeen = _warlock(runtime, 17)
    probe = _probe(seventeen, "class:warlock", 17, "subclass:fiend_patron")
    base = _spell_choices(selection, "class:warlock", 17, probe)
    base["free_cast"] = {
        slot: record["spell_ref"]
        for slot, record in _block(seventeen)["free_cast_slots"].items()
    }

    wrong_level = deepcopy(base)
    wrong_level["free_cast"]["mystic_arcanum_6"] = "spell:forcecage"
    _parsed, errors = selection.validate(
        "class:warlock", 17, wrong_level, character=probe,
    )
    assert any("is not a level 6 spell" in error for error in errors)

    other_list = deepcopy(base)
    other_list["free_cast"]["mystic_arcanum_6"] = "spell:dispel_magic"
    _parsed, errors = selection.validate(
        "class:warlock", 17, other_list, character=probe,
    )
    assert any("is not a level 6 spell" in error for error in errors)

    # 9 环槽只收 9 环：7 环的同列表法术也不行。
    other_level = deepcopy(base)
    other_level["free_cast"]["mystic_arcanum_9"] = "spell:forcecage"
    _parsed, errors = selection.validate(
        "class:warlock", 17, other_level, character=probe,
    )
    assert any("is not a level 9 spell" in error for error in errors)

    off_list = deepcopy(base)
    off_list["free_cast"]["mystic_arcanum_6"] = "spell:chain_lightning"
    _parsed, errors = selection.validate(
        "class:warlock", 17, off_list, character=probe,
    )
    assert errors


def _spend_batch(
    engine, instance, slot_id: str, spell_ref: str, level: int,
) -> dict:
    """Submit one free-cast event as a well-formed, versioned batch.

    6-9 环法术目前一个都没有自动化效果（目录只到 4 环），所以秘奥的四格
    用真实施法永远走不到扣减这一步。这里提交与生产 reducer 相同的批次
    （稳定 batch_id + 版本推进），验证扣账走的是生产代码，而不是在测试里
    手改计数。
    """

    expected = int(engine.initialize_state(instance).get("version", 0) or 0)
    intent = {
        "intent_id": f"free-cast-{uuid4().hex}",
        "type": "free_cast.spent",
        "actor_id": "player:gm",
    }
    batch = {
        "batch_id": stable_batch_id(intent, expected),
        "intent_id": intent["intent_id"],
        "expected_version": expected,
        "result_version": expected + 1,
        "events": [{
            "type": "dnd2024.free_cast.spent",
            "actor_id": "player:gm",
            "free_slot_id": slot_id,
            "spell_ref": spell_ref,
            "spell_level": level,
        }],
    }
    applied = engine.apply_batch(instance, batch)
    return {"applied": applied, "types": [str(event["type"]) for event in batch["events"]]}


def _spend(engine, instance, slot_id: str, spell_ref: str, level: int) -> list[str]:
    result = _spend_batch(engine, instance, slot_id, spell_ref, level)
    assert result["applied"]["applied"], result["applied"]
    return result["types"]


def test_matrix_07_the_four_arcana_are_independent_pools_with_different_recharge(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
    rest: Dnd2024RestEngine,
) -> None:
    seventeen = _warlock(runtime, 17)
    engine, instance = _combat_warlock(runtime, 17)
    _spend(engine, instance, "mystic_arcanum_6", "spell:circle_of_death", 6)
    after = _player(instance)
    # 花掉 6 环那一格：其余三格必须原样是满的。
    assert _slot(resolver, after, "mystic_arcanum_6")["uses_remaining"] == 0
    for slot_id in ("mystic_arcanum_7", "mystic_arcanum_8", "mystic_arcanum_9"):
        assert _slot(resolver, after, slot_id)["uses_remaining"] == 1, slot_id
    # 同一格再花一次必须被 reducer 拒绝：批次整体回滚，扣不成负数。
    with pytest.raises(EventBatchError, match="already spent"):
        _spend_batch(engine, instance, "mystic_arcanum_6", "spell:circle_of_death", 6)
    assert _slot(resolver, _player(instance), "mystic_arcanum_6")["uses_remaining"] == 0

    spent = deepcopy(seventeen)
    records = _block(spent)["free_cast_slots"]
    for slot_id, record in records.items():
        record["uses_remaining"] = 0
    # 只花掉 6 环那一格：其余三格必须原样是满的。
    restored = deepcopy(seventeen)
    _block(restored)["free_cast_slots"]["mystic_arcanum_6"]["uses_remaining"] = 0
    short = rest.complete_short_rest(restored)["character"]
    for slot_id in ("mystic_arcanum_6", "mystic_arcanum_7", "mystic_arcanum_8", "mystic_arcanum_9"):
        assert _slot(resolver, short, slot_id)["uses_remaining"] == (
            0 if slot_id == "mystic_arcanum_6" else 1
        ), slot_id
    long = rest.complete_long_rest(short)["character"]
    for slot_id in ("mystic_arcanum_6", "mystic_arcanum_7", "mystic_arcanum_8", "mystic_arcanum_9"):
        assert _slot(resolver, long, slot_id)["uses_remaining"] == 1, slot_id
    # 短休连"全部花光"的角色也一格都不恢复。
    still_spent = rest.complete_short_rest(spent)["character"]
    assert all(
        _slot(resolver, still_spent, slot_id)["uses_remaining"] == 0
        for slot_id in (
            "mystic_arcanum_6", "mystic_arcanum_7",
            "mystic_arcanum_8", "mystic_arcanum_9",
        )
    )


def test_matrix_08_an_arcanum_never_touches_the_pact_slots(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    seventeen = _warlock(runtime, 17)
    # 邪术师最高只有 5 环契约槽，6 环法术除了免费施放没有第二条路：
    # 免费槽的环阶就是法术环阶，不存在"免费上调"。
    row = _slot(resolver, seventeen, "mystic_arcanum_9")
    assert row["spell_level"] == 9
    assert set(_block(seventeen)["slots_current"]) == {"5"}
    engine, instance = _combat_warlock(runtime, 17)
    # 9 环法术未自动化，所以这一次被挡在施法之前，槽位与次数都不动。
    result = _cast(engine, instance, "spell:foresight", 0)
    assert result["ok"] is False
    assert _slot(resolver, _player(instance), "mystic_arcanum_9")["uses_remaining"] == 1
    assert _block(_player(instance))["slots_current"] == _block(seventeen)["slots_current"]


def test_matrix_09_one_arcanum_may_be_replaced_per_level_up_only(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    thirteen = _warlock(runtime, 13)
    probe = _probe(thirteen, "class:warlock", 14, "subclass:fiend_patron")
    choices = _spell_choices(selection, "class:warlock", 14, probe)
    held = {
        slot: record["spell_ref"]
        for slot, record in _block(thirteen)["free_cast_slots"].items()
    }
    same = deepcopy(held)
    same["mystic_arcanum_6"] = "spell:true_seeing"
    choices["free_cast"] = same
    _parsed, errors = selection.validate("class:warlock", 14, choices, character=probe)
    assert errors == [], errors

    two = deepcopy(same)
    two["mystic_arcanum_7"] = "spell:plane_shift"
    choices["free_cast"] = two
    _parsed, errors = selection.validate("class:warlock", 14, choices, character=probe)
    assert any("can replace at most 1" in error for error in errors)

    cross = deepcopy(held)
    cross["mystic_arcanum_6"] = "spell:forcecage"
    choices["free_cast"] = cross
    _parsed, errors = selection.validate("class:warlock", 14, choices, character=probe)
    assert any("is not a level 6 spell" in error for error in errors)


def test_matrix_10_replacing_a_spent_arcanum_keeps_its_uses(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    thirteen = _warlock(runtime, 13)
    spent = deepcopy(thirteen)
    _block(spent)["free_cast_slots"]["mystic_arcanum_6"]["uses_remaining"] = 0
    probe = _probe(spent, "class:warlock", 14, "subclass:fiend_patron")
    choices = _spell_choices(selection, "class:warlock", 14, probe)
    choices["free_cast"] = {"mystic_arcanum_6": "spell:true_seeing"}
    parsed, errors = selection.validate("class:warlock", 14, choices, character=probe)
    assert errors == [], errors
    assert parsed["free_cast_slots"]["mystic_arcanum_6"] == {
        "spell_ref": "spell:true_seeing",
        "spell_level": 6,
        "recharge": "long_rest",
        "uses_remaining": 0,
    }
    # 另一个环的槽不因为这次替换被改写。
    assert parsed["free_cast_slots"]["mystic_arcanum_7"] == (
        _block(spent)["free_cast_slots"]["mystic_arcanum_7"]
    )


# --------------------------------------------------------------------------
# 11-15: Spell Mastery
# --------------------------------------------------------------------------


def test_matrix_11_spell_mastery_needs_one_level_one_and_one_level_two_action_spell(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    eighteen = _wizard(runtime, 18)
    probe = _probe(eighteen, "class:wizard", 18, "subclass:evoker")
    choices = _spell_choices(selection, "class:wizard", 18, probe, BOOK_EXTRAS)
    choices["free_cast"] = dict(MASTERY)
    parsed, errors = selection.validate("class:wizard", 18, choices, character=probe)
    assert errors == [], errors
    assert parsed["free_cast_slots"]["spell_mastery_1"]["spell_level"] == 1
    assert parsed["free_cast_slots"]["spell_mastery_2"]["spell_level"] == 2
    assert "uses_remaining" not in parsed["free_cast_slots"]["spell_mastery_1"]


def test_matrix_12_reaction_bonus_action_ritual_and_off_book_picks_are_refused(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    eighteen = _wizard(runtime, 18)
    probe = _probe(eighteen, "class:wizard", 18, "subclass:evoker")
    base = _spell_choices(selection, "class:wizard", 18, probe, BOOK_EXTRAS)

    def errors_for(picks: dict[str, str], choices: dict | None = None) -> list[str]:
        payload = deepcopy(base if choices is None else choices)
        payload["free_cast"] = picks
        _parsed, problems = selection.validate(
            "class:wizard", 18, payload, character=probe,
        )
        return problems

    # 反应
    assert any("casting time must be Action" in error for error in errors_for(
        {**MASTERY, "spell_mastery_1": "spell:shield"},
    ))
    # 附赠动作
    assert any("casting time must be Action" in error for error in errors_for(
        {**MASTERY, "spell_mastery_1": "spell:jump"},
    ))
    # 1 分钟（且是仪式）
    assert any("casting time must be Action" in error for error in errors_for(
        {**MASTERY, "spell_mastery_1": "spell:alarm"},
    ))
    # 环阶不符
    assert any("is not a level 1 spell" in error for error in errors_for(
        {**MASTERY, "spell_mastery_1": "spell:scorching_ray"},
    ))

    # 不在法术书：18 级法师的书里已经有全部 1 环法术，所以拿一项
    # 合法但不在这本书里的 2 环 Action 法术来试，撞的正是"书里没有"。
    outsider = next(
        row["ref"] for row in selection.options(
            "class:wizard", 18, probe,
        )["leveled_spells"]
        if row["ref"] not in base["spellbook_refs"]
        and selection.catalog.get(row["ref"])["level"] == 2
        and str(selection.catalog.get(row["ref"])["casting_time"]).startswith("Action")
    )
    problems = errors_for({**MASTERY, "spell_mastery_2": outsider})
    assert any("is not in your spellbook" in error for error in problems), problems


def test_matrix_13_spell_mastery_is_free_without_limit_and_needs_no_rest(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    engine, instance = _combat_wizard(runtime, 18)
    for _round in range(3):
        _refresh_action(instance)
        events = _apply(engine, instance, _cast(
            engine, instance, "spell:magic_missile", 0,
        ))
        assert "dnd2024.free_cast.spent" not in events
        assert "dnd2024.spell.slot_spent" not in events
    character = _player(instance)
    assert _block(character)["slots_current"]["1"] == 4
    assert _slot(resolver, character, "spell_mastery_1")["uses_remaining"] is None


def test_matrix_14_spell_mastery_at_a_higher_level_spends_a_real_slot(
    runtime: Dnd2024Runtime,
) -> None:
    engine, instance = _combat_wizard(runtime, 18)
    events = _apply(engine, instance, _cast(
        engine, instance, "spell:magic_missile", 2,
    ))
    assert "dnd2024.spell.slot_spent" in events
    assert "dnd2024.free_cast.spent" not in events
    assert _block(_player(instance))["slots_current"]["2"] == 2


def test_matrix_15_the_long_rest_study_replaces_at_most_one_mastery_spell(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
    rest: Dnd2024RestEngine,
) -> None:
    eighteen = _wizard(runtime, 18)
    # 短休不提供研究入口。
    with pytest.raises(RestError):
        rest.complete_short_rest(eighteen, free_cast={"spell_mastery_1": "spell:burning_hands"})
    # 未研究时长休不改动任何一项。
    untouched = rest.complete_long_rest(eighteen)["character"]
    assert _slot(resolver, untouched, "spell_mastery_1")["spell_ref"] == "spell:magic_missile"
    assert _slot(resolver, untouched, "spell_mastery_2")["spell_ref"] == "spell:scorching_ray"
    # 一次长休最多换一项。
    with pytest.raises(RestError):
        rest.complete_long_rest(eighteen, free_cast={
            "spell_mastery_1": "spell:burning_hands",
            "spell_mastery_2": "spell:see_invisibility",
        })
    studied = rest.complete_long_rest(eighteen, free_cast={
        "spell_mastery_1": "spell:burning_hands",
    })["character"]
    assert _slot(resolver, studied, "spell_mastery_1")["spell_ref"] == "spell:burning_hands"
    assert _slot(resolver, studied, "spell_mastery_2")["spell_ref"] == "spell:scorching_ray"
    # 非法替换不落账：整次长休一行都不改。
    with pytest.raises(RestError):
        rest.complete_long_rest(eighteen, free_cast={
            "spell_mastery_1": "spell:shield",
        })
    assert _slot(resolver, eighteen, "spell_mastery_1")["spell_ref"] == "spell:magic_missile"


# --------------------------------------------------------------------------
# 16-19: Signature Spells
# --------------------------------------------------------------------------


def test_matrix_16_signature_spells_are_two_different_level_three_spells(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
) -> None:
    twenty = _wizard(runtime, 20)
    probe = _probe(twenty, "class:wizard", 20, "subclass:evoker")
    base = _spell_choices(selection, "class:wizard", 20, probe, BOOK_EXTRAS)

    def errors_for(picks: dict[str, str]) -> list[str]:
        choices = deepcopy(base)
        choices["free_cast"] = picks
        _parsed, problems = selection.validate(
            "class:wizard", 20, choices, character=probe,
        )
        return problems

    ok = errors_for({**MASTERY, **SIGNATURE})
    assert ok == [], ok
    # 两项签名必须不同：把旧记录清掉再提交两份一样的，撞的是去重而不是替换。
    fresh = deepcopy(probe)
    _block(fresh)["free_cast_slots"] = {}
    duplicated_choices = deepcopy(base)
    _parsed, duplicated = selection.validate(
        "class:wizard", 20,
        {**duplicated_choices, "free_cast": {
            **MASTERY, **SIGNATURE, "signature_2": "spell:dispel_magic",
        }},
        character=fresh,
    )
    assert any("cannot fill both" in error for error in duplicated), duplicated
    wrong_level = errors_for({**MASTERY, **SIGNATURE, "signature_1": "spell:magic_missile"})
    assert any("is not a level 3 spell" in error for error in wrong_level)

    view = selection.prepared_view(twenty)
    for ref in SIGNATURE.values():
        entry = next(row for row in view["prepared"] if row["spell_ref"] == ref)
        assert any(row["feature_id"] == "signature_spells" for row in entry["sources"])
    # 常驻不占容量：签名两项计入 granted，不是 prepared_used。
    assert view["capacity"]["prepared_used"] == len(
        _block(twenty)["prepared_spell_refs"]
    )
    eighteen = selection.prepared_view(_wizard(runtime, 18))
    assert view["capacity"]["granted"] == eighteen["capacity"]["granted"] + 2


def test_matrix_17_the_two_signature_uses_are_independent_and_rest_apart(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
    rest: Dnd2024RestEngine,
) -> None:
    engine, instance = _combat_wizard(runtime, 20)
    _refresh_action(instance)
    first = _apply(engine, instance, _cast(
        engine, instance, "spell:dispel_magic", 0,
    ))
    assert "dnd2024.free_cast.spent" in first
    character = _player(instance)
    assert _slot(resolver, character, "signature_1")["uses_remaining"] == 0
    assert _slot(resolver, character, "signature_2")["uses_remaining"] == 1

    _refresh_action(instance)
    second = _apply(engine, instance, _cast(
        engine, instance, "spell:protection_from_energy", 0, target="player:gm",
        resist_type="fire",
    ))
    assert "dnd2024.free_cast.spent" in second
    character = _player(instance)
    assert _slot(resolver, character, "signature_1")["uses_remaining"] == 0
    assert _slot(resolver, character, "signature_2")["uses_remaining"] == 0

    # 两项都花完了，第三次连免费来源都找不到。
    _refresh_action(instance)
    assert _cast(engine, instance, "spell:dispel_magic", 0)["ok"] is False

    short = rest.complete_short_rest(character)["character"]
    assert _slot(resolver, short, "signature_1")["uses_remaining"] == 1
    assert _slot(resolver, short, "signature_2")["uses_remaining"] == 1


def test_matrix_18_a_signature_cast_at_level_four_spends_a_slot_not_a_use(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    engine, instance = _combat_wizard(runtime, 20)
    events = _apply(engine, instance, _cast(
        engine, instance, "spell:dispel_magic", 4,
    ))
    assert "dnd2024.spell.slot_spent" in events
    assert "dnd2024.free_cast.spent" not in events
    character = _player(instance)
    assert _block(character)["slots_current"]["4"] == 2
    assert _slot(resolver, character, "signature_1")["uses_remaining"] == 1


def test_matrix_19_signature_spells_have_no_replacement_entry(
    runtime: Dnd2024Runtime, rest: Dnd2024RestEngine,
) -> None:
    twenty = _wizard(runtime, 20)
    with pytest.raises(RestError):
        rest.complete_long_rest(twenty, free_cast={"signature_1": "spell:fireball"})
    # 官方本段没有给出签名法术的升级换选规则，本切片也不提供：
    # 20 级是封顶等级，除了长休研究之外没有任何改写入口。
    assert _slot(
        Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE)),
        twenty, "signature_1",
    )["rest_replace"] == 0


# --------------------------------------------------------------------------
# 20-25: the ordinary rules still apply
# --------------------------------------------------------------------------


def test_matrix_20_a_free_cast_still_pays_the_action_and_the_target_checks(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    engine, instance = _combat_wizard(runtime, 18)
    # 动作已经花掉：免费不等于免动作。
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    economy["action"] = 0
    assert not _cast(engine, instance, "spell:magic_missile", 0)["ok"]
    economy["action"] = 1
    # 目标无效：同类相残被拒。
    assert not _cast(
        engine, instance, "spell:scorching_ray", 0, target="player:gm",
    )["ok"]
    # 施法攻击仍然要掷骰：scorching_ray 是 spell_attack。
    events = _apply(engine, instance, _cast(
        engine, instance, "spell:scorching_ray", 0,
    ))
    assert "dnd2024.spell.cast" in events
    assert _slot(resolver, _player(instance), "spell_mastery_2")["uses_remaining"] is None


def test_matrix_21_a_refused_cast_spends_nothing_and_a_cast_is_never_refunded(
    runtime: Dnd2024Runtime, resolver: Dnd2024ClassFeatureResolver,
) -> None:
    engine, instance = _combat_wizard(runtime, 20)
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    economy["action"] = 0
    # 被拒的施法不扣次数：动作都没有，谈不上"已经用过一次"。
    assert not _cast(engine, instance, "spell:dispel_magic", 0)["ok"]
    assert _slot(resolver, _player(instance), "signature_1")["uses_remaining"] == 1

    economy["action"] = 1
    _apply(engine, instance, _cast(engine, instance, "spell:dispel_magic", 0))
    assert _slot(resolver, _player(instance), "signature_1")["uses_remaining"] == 0
    # 真的进入结算就不再退款：目标身上没有可解的法术效果，次数照样用掉。
    _refresh_action(instance)
    assert not _cast(engine, instance, "spell:dispel_magic", 0)["ok"]
    assert _slot(resolver, _player(instance), "signature_1")["uses_remaining"] == 0


def test_matrix_22_one_cast_uses_exactly_one_free_source(
    runtime: Dnd2024Runtime,
) -> None:
    from src.rulesets.dnd2024.combat.triggered_spells import free_cast_resource

    engine, instance = _combat_wizard(runtime, 20)
    character = _player(instance)
    actor = engine._player_view("gm", character)
    source = free_cast_resource(actor, "spell:magic_missile", 1)
    assert source is not None and source["kind"] == "free_slot"
    # 单次施法只扣一个来源：签名槽与法术精通槽同时在场也只出一条事件。
    _refresh_action(instance)
    events = _apply(engine, instance, _cast(engine, instance, "spell:magic_missile", 0))
    assert events.count("dnd2024.free_cast.spent") == 0
    assert _block(_player(instance))["slots_current"] == _block(character)["slots_current"]


def test_matrix_23_replacing_a_granted_spell_keeps_the_other_sources(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    eighteen = _wizard(runtime, 18)
    # 同一个法术既在普通准备里、又是法术精通的选择。
    assert "spell:magic_missile" in _block(eighteen)["prepared_spell_refs"]
    view = selection.prepared_view(eighteen)
    entry = next(
        row for row in view["prepared"] if row["spell_ref"] == "spell:magic_missile"
    )
    assert {source["kind"] for source in entry["sources"]} == {
        "class_list", "class_feature",
    }
    # 换掉精通那一项：普通准备的那一条来源必须留着。
    replaced = deepcopy(eighteen)
    _block(replaced)["free_cast_slots"]["spell_mastery_1"]["spell_ref"] = "spell:burning_hands"
    view = selection.prepared_view(replaced)
    kept = next(
        row for row in view["prepared"] if row["spell_ref"] == "spell:magic_missile"
    )
    assert [source["kind"] for source in kept["sources"]] == ["class_list"]
    assert _slot(resolver, replaced, "spell_mastery_1")["spell_ref"] == "spell:burning_hands"


def test_matrix_24_slots_survive_a_save_load_advancement_and_replay(
    runtime: Dnd2024Runtime, selection: Dnd2024SpellSelection,
    resolver: Dnd2024ClassFeatureResolver,
) -> None:
    import json

    twenty = _wizard(runtime, 20)
    round_trip = json.loads(json.dumps(twenty))
    before = _block(twenty)["free_cast_slots"]
    after = _block(round_trip)["free_cast_slots"]
    assert before == after
    for slot_id in ("spell_mastery_1", "spell_mastery_2", "signature_1", "signature_2"):
        assert _slot(resolver, round_trip, slot_id) == _slot(resolver, twenty, slot_id)

    # 升级重建：19 级角色的两项精通选择在升到 20 级后原样保留。
    nineteen = _wizard(runtime, 19)
    probe = _probe(nineteen, "class:wizard", 20, "subclass:evoker")
    choices = _spell_choices(selection, "class:wizard", 20, probe, BOOK_EXTRAS)
    choices["free_cast"] = dict(SIGNATURE)
    parsed, errors = selection.validate("class:wizard", 20, choices, character=probe)
    assert errors == [], errors
    assert parsed["free_cast_slots"]["spell_mastery_1"]["spell_ref"] == "spell:magic_missile"
    assert parsed["free_cast_slots"]["signature_1"]["spell_ref"] == "spell:dispel_magic"


def test_matrix_25_the_class_resource_free_cast_still_works(
    runtime: Dnd2024Runtime,
) -> None:
    # 至圣斩走的是职业资源那条老路：本次改动没有把它挤进新表。
    from tests.rulesets.test_dnd2024_smite import smite_instance, attack, cast

    _runtime, engine, instance = smite_instance()
    attack(engine, instance)
    result = cast(engine, instance, 0)
    assert result["ok"], result
    engine.apply_batch(instance, result["event_batch"])
    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert character["resources"]["class"]["paladins_smite"]["current"] == 0
    assert "free_cast_slots" not in character["spellcasting"]["class"]
