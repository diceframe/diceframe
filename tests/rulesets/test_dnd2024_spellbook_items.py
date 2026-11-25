"""S0-F31: Use Magic Device, spellbook rituals, Memorize Spell and Evocation Savant."""

from __future__ import annotations

from typing import Any

import pytest

from src.rulesets.dnd2024.character.attunement import (
    BASE_ATTUNEMENT_SLOTS,
    attunement_capacity,
)
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.exploration.engine import Dnd2024ExplorationEngine
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.progression.advancement import Dnd2024AdvancementEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
from tests.rulesets.dnd2024_monk_common import advancement_choices
from tests.rulesets.test_dnd2024_item_activation import (
    SequenceRng,
    _carry,
    _instance,
    _install_item,
    _charged_potion,
    _reconcile,
    _start,
)


def _resolver() -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(Dnd2024Runtime().load_bundle("en"))


def _character(
    level: int, class_ref: str = "class:rogue", *, subclass_ref: str | None = None,
) -> dict:
    character: dict = {
        "build": {"class_levels": [{"class_ref": class_ref, "level": level}]},
    }
    if subclass_ref:
        character["features"] = {"subclass_ref": subclass_ref}
    return character


def _thief(level: int) -> dict:
    return _character(level, subclass_ref="subclass:thief")


def test_use_magic_device_declares_one_extra_attunement_slot() -> None:
    """声明与实现是两条线：这条用例锁住"1"这个数字本身。"""

    resolver = _resolver()

    assert resolver.feature_value(
        _thief(13), "use_magic_device", "attunement_slot_bonus",
    ) == 1


@pytest.mark.parametrize(("level", "capacity"), [(12, 3), (13, 4), (20, 4)])
def test_thief_gets_a_fourth_attunement_slot_at_thirteen(
    level: int, capacity: int,
) -> None:
    resolver = _resolver()

    assert attunement_capacity(_thief(level), resolver) == capacity


def test_a_rogue_without_the_thief_subclass_keeps_the_base_capacity() -> None:
    resolver = _resolver()

    assert attunement_capacity(_character(13), resolver) == BASE_ATTUNEMENT_SLOTS


def test_the_bonus_never_leaks_to_another_class() -> None:
    """子职特性按职业归属授予：把 thief 写进 Wizard 的角色代理不给加成。"""

    resolver = _resolver()

    assert attunement_capacity(
        _character(13, "class:wizard", subclass_ref="subclass:thief"), resolver,
    ) == BASE_ATTUNEMENT_SLOTS


# --------------------------------------------------------------------------
# Use Magic Device: one d6 per charge-spending use
# --------------------------------------------------------------------------


def _spell_choices(bundle: Any, character: dict, row: dict) -> dict:
    """A legal answer for one level's spell selection, built on the real book.

    升级要求书目"只增不减且恰好加 2"，准备法术必须都在书里，所以这里不是
    "挑前几个"，而是在**现有书**上补齐到 minimum 再选准备列表。
    """

    requirements = Dnd2024SpellSelection(bundle).options(
        str(row["class_ref"]), int(row["level"]),
    )
    limits = requirements["requirements"]
    block = (character.get("spellcasting") or {}).get("class") or {}
    book = [str(ref) for ref in block.get("spellbook_refs") or []]
    candidates = [
        row_["ref"] for row_ in requirements["leveled_spells"]
        if row_["ref"] not in book
    ]
    for ref in candidates:
        if len(book) >= int(limits["spellbook_minimum"]):
            break
        book.append(ref)
    # 准备位与戏法在升级时不许换（replace_limit 0），只能在**原有**的基上补足：
    # 从 leveled_spells 重新挑前 N 个会被判成"替换了一堆法术"。
    old_prepared = [str(ref) for ref in block.get("prepared_spell_refs") or []]
    prepared = [ref for ref in old_prepared if ref in book][
        : int(limits["prepared_spell_count"])
    ]
    for ref in book:
        if len(prepared) >= int(limits["prepared_spell_count"]):
            break
        if ref not in prepared:
            prepared.append(ref)
    old_cantrips = [str(ref) for ref in block.get("cantrip_refs") or []]
    cantrips = old_cantrips[: int(limits["cantrip_count"])]
    for row_ in requirements["cantrips"]:
        if len(cantrips) >= int(limits["cantrip_count"]):
            break
        if row_["ref"] not in cantrips:
            cantrips.append(row_["ref"])
    return {
        "cantrip_refs": cantrips,
        "prepared_spell_refs": prepared,
        "spellbook_refs": book,
    }


def _advance(
    runtime: Dnd2024Runtime, character: dict, target: int,
    *, subclass: str = "subclass:thief",
) -> dict:
    """Level a character up, answering every required choice.

    Wizard levels also ask for `class_spell_choices`; the generic picker in
    `dnd2024_monk_common` answers the rest (feat / ASI / feature_choice), so
    this only adds the spell-selection branch and a fixed subclass.
    """

    bundle = runtime.load_bundle("en")
    engine = Dnd2024AdvancementEngine(bundle)
    while int(character["build"]["level"]) < target:
        requirements = engine.preview_next_level(character)["requirements"]
        choices = advancement_choices({"requirements": requirements})
        subclass_row = next(
            (row for row in requirements if row["id"] == "subclass_ref"), None,
        )
        if subclass_row is not None:
            allowed = [str(o["value"]) for o in subclass_row.get("options") or []]
            # rogue 用 thief；wizard 的子职在 3 级选，写死 thief 会被拒。
            choices["subclass_ref"] = (
                subclass if subclass in allowed else (allowed[0] if allowed else subclass)
            )
        spell_row = next(
            (row for row in requirements if row.get("kind") == "spell_selection"), None,
        )
        if spell_row is not None:
            choices["class_spell_choices"] = _spell_choices(
                bundle, character, spell_row,
            )
        character = engine.apply_next_level(character, choices)
    return character


def _thief_sheet(runtime: Dnd2024Runtime, level: int = 13) -> dict:
    """A full character sheet (not just the canonical block) at ``level``.

    ``_instance`` puts the whole sheet on the player, so levelling has to write
    back into ``ruleset_character`` instead of replacing the sheet.
    """

    presets = runtime.builder_choices(None, {"locale": "en"})["quick_presets"]
    preset = next(item for item in presets if item["id"] == "lucky_scout")
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Vex"},
    )
    sheet["ruleset_character"] = _advance(runtime, sheet["ruleset_character"], level)
    return sheet


def _activate(runtime: Dnd2024Runtime, sheet: dict, item_ref: str, rolls: list[int]):
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    return engine.resolve_intent(instance, {
        "intent_id": "umd-activate", "type": "exploration.activate_item",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "item_ref": item_ref,
    }, SequenceRng(rolls))


def test_a_thief_waives_the_charge_when_the_d6_shows_six() -> None:
    runtime = Dnd2024Runtime()
    sheet = _thief_sheet(runtime)
    item_ref = _install_item(runtime, _charged_potion())
    _carry(sheet, {item_ref: 1})
    _reconcile(runtime, sheet)

    resolved = _activate(runtime, sheet, item_ref, [6, 4])

    assert resolved["ok"] is True, resolved.get("error")
    events = resolved["event_batch"]["events"]
    waived = [e for e in events if e["type"] == "dnd2024.item.charge_waived"]
    assert waived and waived[0]["roll"] == 6 and waived[0]["declared"] == 1
    # 免收就是"这次没有扣费事件"，不是 amount=0 的扣费：apply 侧不收 0 价。
    assert not [e for e in events if e["type"] == "dnd2024.item.charge_spent"]


def test_a_low_roll_spends_the_declared_charge() -> None:
    runtime = Dnd2024Runtime()
    sheet = _thief_sheet(runtime)
    item_ref = _install_item(runtime, _charged_potion())
    _carry(sheet, {item_ref: 1})
    _reconcile(runtime, sheet)

    resolved = _activate(runtime, sheet, item_ref, [1, 4])

    assert resolved["ok"] is True, resolved.get("error")
    events = resolved["event_batch"]["events"]
    spent = [e for e in events if e["type"] == "dnd2024.item.charge_spent"]
    assert spent and spent[0]["amount"] == 1
    assert not [e for e in events if e["type"] == "dnd2024.item.charge_waived"]


def test_a_rogue_without_use_magic_device_never_rolls_the_d6() -> None:
    """没有这条特性时 RNG 流不能多出一颗骰：`charge_waiver_roll` 返回 None。"""

    runtime = Dnd2024Runtime()
    sheet = _thief_sheet(runtime, 12)
    item_ref = _install_item(runtime, _charged_potion())
    _carry(sheet, {item_ref: 1})
    _reconcile(runtime, sheet)

    resolved = _activate(runtime, sheet, item_ref, [4])

    assert resolved["ok"] is True, resolved.get("error")
    events = resolved["event_batch"]["events"]
    assert [e for e in events if e["type"] == "dnd2024.item.charge_spent"][0]["amount"] == 1
    assert not [e for e in events if e["type"] == "dnd2024.item.charge_waived"]


# --------------------------------------------------------------------------
# Ritual Adept: read the book, spend no slot
# --------------------------------------------------------------------------


def _wizard_sheet(
    runtime: Dnd2024Runtime, *, prepared: bool = False, level: int = 1,
) -> dict:
    """法师；默认把 `detect_magic` 从准备列表里摘掉，留它在书里。"""

    presets = runtime.builder_choices(None, {"locale": "en"})["quick_presets"]
    preset = next(item for item in presets if item["id"] == "curious_arcanist")
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Mira"},
    )
    if level > 1:
        sheet["ruleset_character"] = _advance(runtime, sheet["ruleset_character"], level)
        return sheet
    if not prepared:
        block = sheet["ruleset_character"]["spellcasting"]["class"]
        block["prepared_spell_refs"] = [
            ref for ref in block.get("prepared_spell_refs") or []
            if ref != "spell:detect_magic"
        ]
    return sheet


def _cast_ritual(
    runtime: Dnd2024Runtime, sheet: dict, spell_ref: str, **extra,
):
    instance = _instance(sheet)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")
    return engine.resolve_intent(instance, {
        "intent_id": "ritual-1", "type": "exploration.cast_spell",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": spell_ref, "ritual": True, **extra,
    }, SequenceRng([4]))


def test_ritual_adept_offers_an_unprepared_ritual_from_the_book() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")

    offered = [
        spell
        for action in engine.available_intents(_instance(sheet), "gm")
        for spell in action.get("spells") or []
    ]
    ritual = [spell for spell in offered if spell.get("ritual")]
    assert [spell["spell_ref"] for spell in ritual] == ["spell:detect_magic"]
    assert ritual[0]["available_slot_levels"] == [0]


def test_a_ritual_cast_spends_no_spell_slot() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime)

    resolved = _cast_ritual(runtime, sheet, "spell:detect_magic")

    assert resolved["ok"] is True, resolved.get("error")
    events = resolved["event_batch"]["events"]
    cast = [e for e in events if e["type"] == "dnd2024.spell.cast"]
    assert cast and cast[0]["ritual"] is True and cast[0]["slot_level"] == 0
    spent = [
        e for e in events
        if e["type"] in {"dnd2024.spell.slot_spent", "dnd2024.spell.font_slot_spent"}
    ]
    assert spent == []


def test_a_prepared_ritual_is_listed_once_as_an_ordinary_cast() -> None:
    """已准备的仪式法术不额外列一条：它是同一个法术的两种施法方式。"""

    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, prepared=True)
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")

    offered = [
        spell
        for action in engine.available_intents(_instance(sheet), "gm")
        for spell in action.get("spells") or []
    ]
    detect = [spell for spell in offered if spell["spell_ref"] == "spell:detect_magic"]
    assert len(detect) == 1
    assert not detect[0].get("ritual")


def test_a_spell_without_the_ritual_tag_cannot_be_cast_ritually() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime)

    resolved = _cast_ritual(runtime, sheet, "spell:magic_missile")

    assert resolved["ok"] is False
    assert "Ritual tag" in resolved["error"]


def test_a_spell_outside_the_book_cannot_be_cast_ritually() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime)

    resolved = _cast_ritual(runtime, sheet, "spell:alarm")

    assert resolved["ok"] is False
    assert "spellbook" in resolved["error"]


def test_only_a_ritual_adept_may_cast_ritually() -> None:
    runtime = Dnd2024Runtime()
    sheet = _thief_sheet(runtime, 13)

    resolved = _cast_ritual(runtime, sheet, "spell:detect_magic")

    assert resolved["ok"] is False
    assert "Ritual Adept" in resolved["error"]


def test_ritual_casting_is_refused_in_combat() -> None:
    """战斗里没有那 10 分钟：拒绝，而不是静默按普通施法扣槽。"""

    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime)
    instance = _instance(sheet)
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    _start(engine, instance)

    resolved = engine.resolve_intent(instance, {
        "intent_id": "ritual-combat", "type": "cast_spell",
        "expected_version": 1, "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:detect_magic", "ritual": True,
    }, SequenceRng([4]))

    assert resolved["ok"] is False
    assert "ritual casting is not available in combat" in resolved["error"]


# --------------------------------------------------------------------------
# Memorize Spell: one swap on a Short Rest
# --------------------------------------------------------------------------


def _wizard_book(sheet: dict) -> tuple[list[str], list[str]]:
    block = sheet["ruleset_character"]["spellcasting"]["class"]
    return (
        [str(ref) for ref in block.get("prepared_spell_refs") or []],
        [str(ref) for ref in block.get("spellbook_refs") or []],
    )


def test_memorize_spell_swaps_one_prepared_spell_on_a_short_rest() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=5)
    prepared, book = _wizard_book(sheet)
    gained = next(ref for ref in book if ref not in prepared)
    replaced = prepared[0]

    result = runtime.complete_rest(
        None, sheet, "short", {}, memorize_spell={
            "replace_ref": replaced, "with_ref": gained,
        },
    )

    after_prepared, after_book = _wizard_book(result["character"])
    assert gained in after_prepared and replaced not in after_prepared
    # 数量不变、书目不变：这次只动了准备位。
    assert len(after_prepared) == len(prepared)
    assert after_book == book
    assert [e for e in result["events"] if e["type"] == "memorize_spell"]


def test_memorize_spell_refuses_a_spell_outside_the_book() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=5)
    prepared, _book = _wizard_book(sheet)

    with pytest.raises(Exception) as excinfo:
        runtime.complete_rest(
            None, sheet, "short", {}, memorize_spell={
                "replace_ref": prepared[0], "with_ref": "spell:wish",
            },
        )

    assert "spellbook" in str(excinfo.value)


def test_memorize_spell_refuses_a_spell_that_is_not_prepared() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=5)
    prepared, book = _wizard_book(sheet)
    gained = next(ref for ref in book if ref not in prepared)

    with pytest.raises(Exception) as excinfo:
        runtime.complete_rest(
            None, sheet, "short", {}, memorize_spell={
                "replace_ref": gained, "with_ref": prepared[0],
            },
        )

    assert "not prepared" in str(excinfo.value)


def test_memorize_spell_needs_five_wizard_levels() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=1)
    block = sheet["ruleset_character"]["spellcasting"]["class"]
    prepared = [str(ref) for ref in block.get("prepared_spell_refs") or []]
    book = [str(ref) for ref in block.get("spellbook_refs") or []]
    gained = next(ref for ref in book if ref not in prepared)

    with pytest.raises(Exception) as excinfo:
        runtime.complete_rest(
            None, sheet, "short", {}, memorize_spell={
                "replace_ref": prepared[0], "with_ref": gained,
            },
        )

    assert "Memorize Spell" in str(excinfo.value)


def test_memorize_spell_is_not_a_long_rest_option() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=5)

    with pytest.raises(ValueError) as excinfo:
        runtime.complete_rest(
            None, sheet, "long", {}, memorize_spell={
                "replace_ref": "spell:detect_magic", "with_ref": "spell:sleep",
            },
        )

    assert "short rest" in str(excinfo.value)
