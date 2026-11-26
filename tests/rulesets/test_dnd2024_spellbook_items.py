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
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog
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

    升级要求书目"只增不减、正常增量恰好 N"，准备法术必须都在书里，所以这里
    不是"挑前几个"。免费入书的名额走 `spell_grants` 的授权槽位：槽位自己带着
    count / options / target，客户端只负责把未填的部分填满。
    """

    block = (character.get("spellcasting") or {}).get("class") or {}
    book = [str(ref) for ref in block.get("spellbook_refs") or []]
    candidates = [
        entry["ref"] for entry in row["leveled_spells"] if entry["ref"] not in book
    ]
    choices: dict = {}
    grants: dict[str, list[str]] = {}
    granted_refs: list[str] = []
    for slot in row.get("grant_slots") or []:
        if slot.get("target") != "spellbook":
            continue
        selected = [str(ref) for ref in slot.get("selected_refs") or ()]
        need = int(slot["count"]) - len(selected)
        picked = [
            option["ref"] for option in slot.get("options") or ()
            if option["ref"] not in book and option["ref"] not in selected
            and option["ref"] not in granted_refs
        ][: max(0, need)]
        grants[slot["feature_id"]] = selected + picked
        granted_refs.extend(picked)
    # 正常增量只是 policy 的每级两个；免费项另算，二者合起来才是这本书。
    total = int(row["spellbook_additions"])
    normal = [ref for ref in candidates if ref not in granted_refs][:total]
    book = book + granted_refs + normal
    if grants:
        choices["spell_grants"] = grants
    # 准备位与戏法在升级时不许换（replace_limit 0），只能在原有的基上补足。
    prepared = [
        str(ref) for ref in block.get("prepared_spell_refs") or [] if ref in book
    ][: int(row["prepared_spell_count"])]
    for ref in book:
        if len(prepared) >= int(row["prepared_spell_count"]):
            break
        if ref not in prepared:
            prepared.append(ref)
    cantrips = [str(ref) for ref in block.get("cantrip_refs") or []][
        : int(row["cantrip_count"])
    ]
    # 戏法数量会随等级涨（3 级多一个），涨出来的那一个必须补上，否则
    # "exactly N" 直接不过；但已在列表里的不能动（replace_limit 0）。
    for entry in row.get("cantrips") or []:
        if len(cantrips) >= int(row["cantrip_count"]):
            break
        if entry["ref"] not in cantrips:
            cantrips.append(entry["ref"])
    choices.update({
        "cantrip_refs": cantrips,
        "prepared_spell_refs": prepared,
        "spellbook_refs": book,
    })
    return choices


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
        # 再预览一次：这一级的法术选择要按"选完子职之后"的身份算，否则
        # 3 级 Evoker 的授权槽位在 requirements 里根本不会出现。
        probe = engine.preview_next_level(character, choices)["requirements"]
        spell_row = next(
            (row for row in probe if row.get("kind") == "spell_selection"), None,
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
    rolled = [e for e in events if e["type"] == "dnd2024.item.charge_roll"]
    assert rolled and rolled[0]["roll"] == 6 and rolled[0]["declared"] == 1
    assert rolled[0]["waived"] is True and rolled[0]["threshold"] == 6
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
    rolled = [e for e in events if e["type"] == "dnd2024.item.charge_roll"]
    # 1–5 也要留骰迹：否则"掷了但没免"和"根本没掷"分不开。
    assert rolled and rolled[0]["roll"] == 1 and rolled[0]["waived"] is False


def test_use_magic_device_waives_the_charge_in_combat_too() -> None:
    """战斗与探索同一判定：战斗里也要有骰迹、也要真的不扣。"""

    runtime = Dnd2024Runtime()
    sheet = _thief_sheet(runtime)
    item_ref = _install_item(runtime, _charged_potion())
    _carry(sheet, {item_ref: 1})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    _start(engine, instance)

    resolved = engine.resolve_intent(instance, {
        "intent_id": "umd-combat", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": item_ref,
    }, SequenceRng([6, 4]))

    assert resolved["ok"] is True, resolved.get("error")
    events = resolved["event_batch"]["events"]
    rolled = [e for e in events if e["type"] == "dnd2024.item.charge_roll"]
    assert rolled and rolled[0]["roll"] == 6 and rolled[0]["waived"] is True
    assert not [e for e in events if e["type"] == "dnd2024.item.charge_spent"]
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    charges = instance.get_character_sheet("gm")["ruleset_character"][
        "equipment"
    ]["item_state"][item_ref]["charges"]
    assert charges["current"] == 2


def test_combat_spends_the_declared_charge_on_a_low_roll() -> None:
    """战斗侧 1–5：照常扣一格，apply 只扣一次。"""

    runtime = Dnd2024Runtime()
    sheet = _thief_sheet(runtime)
    item_ref = _install_item(runtime, _charged_potion())
    _carry(sheet, {item_ref: 1})
    _reconcile(runtime, sheet)
    instance = _instance(sheet)
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    _start(engine, instance)

    resolved = engine.resolve_intent(instance, {
        "intent_id": "umd-combat-low", "type": "activate_item", "expected_version": 1,
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": item_ref,
    }, SequenceRng([2, 4]))

    assert resolved["ok"] is True, resolved.get("error")
    events = resolved["event_batch"]["events"]
    rolled = [e for e in events if e["type"] == "dnd2024.item.charge_roll"]
    assert rolled and rolled[0]["waived"] is False
    assert [e for e in events if e["type"] == "dnd2024.item.charge_spent"][0]["amount"] == 1
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    charges = instance.get_character_sheet("gm")["ruleset_character"][
        "equipment"
    ]["item_state"][item_ref]["charges"]
    assert charges["current"] == 1


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
    # 12 级没有这条特性：一颗骰都不该掷，也就没有骰迹。
    assert not [e for e in events if e["type"] == "dnd2024.item.charge_roll"]


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


def test_a_prepared_ritual_is_listed_once_and_still_offers_the_ritual() -> None:
    """已准备的仪式法术只列一条，但这一条同时承认两种施法方式。"""

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
    # 有槽可以普通施法，也仍然可以按仪式施放（不花槽）。
    assert detect[0]["available_slot_levels"]
    assert detect[0]["ritual"] is True


def test_a_prepared_ritual_survives_having_no_slots_left() -> None:
    """槽用光时，已准备的仪式法术仍应出现——它本来就不需要槽。"""

    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, prepared=True)
    block = sheet["ruleset_character"]["spellcasting"]["class"]
    block["slots_current"] = {
        level: 0 for level in block.get("slots_current") or {}
    }
    engine = Dnd2024ExplorationEngine(runtime.load_bundle("en"), "en")

    offered = [
        spell
        for action in engine.available_intents(_instance(sheet), "gm")
        for spell in action.get("spells") or []
    ]
    detect = [spell for spell in offered if spell["spell_ref"] == "spell:detect_magic"]
    assert len(detect) == 1
    assert detect[0]["ritual"] is True
    assert detect[0]["available_slot_levels"] == []


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


def test_a_ritual_flag_must_be_a_boolean() -> None:
    """`{"ritual": "false"}` 不能读成真：这个开关决定要不要花槽。"""

    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, prepared=True)

    resolved = _cast_ritual(runtime, sheet, "spell:detect_magic", ritual="false")

    assert resolved["ok"] is False
    assert "ritual must be a boolean" in resolved["error"]


def test_a_ritual_slot_level_must_be_an_integer() -> None:
    """畸形输入要给一条规则错误，不是 `int()` 抛出的裸 ValueError。"""

    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime)

    resolved = _cast_ritual(runtime, sheet, "spell:detect_magic", slot_level="two")

    assert resolved["ok"] is False
    assert "slot_level must be an integer" in resolved["error"]


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


def _swap(prepared: list[str], replaced: str, gained: str) -> list[str]:
    return [gained if ref == replaced else ref for ref in prepared]


def test_memorize_spell_swaps_one_prepared_spell_on_a_short_rest() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=5)
    prepared, book = _wizard_book(sheet)
    gained = next(ref for ref in book if ref not in prepared)
    replaced = prepared[0]

    result = runtime.complete_rest(
        None, sheet, "short", {},
        prepared_spells={"prepared_spell_refs": _swap(prepared, replaced, gained)},
    )

    after_prepared, after_book = _wizard_book(result["character"])
    assert gained in after_prepared and replaced not in after_prepared
    # 数量不变、书目不变：这次只动了准备位。
    assert len(after_prepared) == len(prepared)
    assert after_book == book
    # 与长休同一个事件：短休换准备不是另一条审计口径。
    assert [e for e in result["events"] if e["type"] == "prepared_spells_changed"]


def test_memorize_spell_is_reachable_through_the_declared_rest_option() -> None:
    """通道就是 `rest_option_names`：没声明就等于客户端提交被静默丢掉。"""

    runtime = Dnd2024Runtime()

    assert "prepared_spells" in runtime.rest_option_names


def test_memorize_spell_swaps_only_one_prepared_spell() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=5)
    prepared, book = _wizard_book(sheet)
    fresh = [ref for ref in book if ref not in prepared]

    two_swaps = _swap(_swap(prepared, prepared[0], fresh[0]), prepared[1], fresh[1])
    with pytest.raises(Exception) as excinfo:
        runtime.complete_rest(
            None, sheet, "short", {},
            prepared_spells={"prepared_spell_refs": two_swaps},
        )

    assert "at most 1" in str(excinfo.value)


def test_memorize_spell_refuses_a_spell_outside_the_book() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=5)
    prepared, _book = _wizard_book(sheet)

    with pytest.raises(Exception) as excinfo:
        runtime.complete_rest(
            None, sheet, "short", {},
            prepared_spells={
                "prepared_spell_refs": _swap(prepared, prepared[0], "spell:wish"),
            },
        )

    assert "spellbook" in str(excinfo.value)


def test_memorize_spell_keeps_the_prepared_count_unchanged() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=5)
    prepared, book = _wizard_book(sheet)
    gained = next(ref for ref in book if ref not in prepared)

    with pytest.raises(Exception) as excinfo:
        runtime.complete_rest(
            None, sheet, "short", {},
            prepared_spells={
                "prepared_spell_refs": [*prepared, gained],
            },
        )

    assert "must contain exactly" in str(excinfo.value)


def test_memorize_spell_needs_five_wizard_levels() -> None:
    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=1)
    block = sheet["ruleset_character"]["spellcasting"]["class"]
    prepared = [str(ref) for ref in block.get("prepared_spell_refs") or []]
    book = [str(ref) for ref in block.get("spellbook_refs") or []]
    gained = next(ref for ref in book if ref not in prepared)

    with pytest.raises(Exception) as excinfo:
        runtime.complete_rest(
            None, sheet, "short", {},
            prepared_spells={"prepared_spell_refs": _swap(prepared, prepared[0], gained)},
        )

    assert "short rest" in str(excinfo.value)


def test_a_long_rest_still_uses_the_class_budget() -> None:
    """短休预算只由特性给；长休仍走职业策略（法师不限数量）。"""

    runtime = Dnd2024Runtime()
    sheet = _wizard_sheet(runtime, level=5)
    prepared, book = _wizard_book(sheet)
    fresh = [ref for ref in book if ref not in prepared][:2]
    reshuffled = _swap(_swap(prepared, prepared[0], fresh[0]), prepared[1], fresh[1])

    result = runtime.complete_rest(
        None, sheet, "long", {},
        prepared_spells={"prepared_spell_refs": reshuffled},
    )

    after, _book = _wizard_book(result["character"])
    assert fresh[0] in after and fresh[1] in after


# --------------------------------------------------------------------------
# Evocation Savant: two free picks, then one per newly opened slot level
# --------------------------------------------------------------------------


def _evoker_step(
    runtime: Dnd2024Runtime, character: dict, *, fill_grants: bool = True,
):
    """Preview one level-up as an Evoker and answer the spell selection.

    ``fill_grants=False`` submits the two ordinary picks only, which is the
    "slot stays open" path the shared grant machinery allows.
    """

    engine = Dnd2024AdvancementEngine(runtime.load_bundle("en"))
    requirements = engine.preview_next_level(character)["requirements"]
    choices = advancement_choices({"requirements": requirements})
    choices["subclass_ref"] = "subclass:evoker"
    probe = engine.preview_next_level(character, choices)["requirements"]
    row = next(item for item in probe if item.get("kind") == "spell_selection")
    answered = row if fill_grants else {**row, "grant_slots": []}
    choices["class_spell_choices"] = _spell_choices(
        runtime.load_bundle("en"), character, answered,
    )
    return engine, choices, row


def _evoker_slot(row: dict) -> dict:
    return next(slot for slot in row["grant_slots"] if slot["feature_id"] == "evocation_savant")


def _book(character: dict) -> list[str]:
    return [str(ref) for ref in character["spellcasting"]["class"]["spellbook_refs"]]


def test_evocation_savant_is_a_spellbook_grant_slot_of_two() -> None:
    """同一个授权槽族：学派、列表、目标都由声明给出，代码不认特性 id。"""

    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=2)["ruleset_character"]
    _engine, _choices, row = _evoker_step(runtime, character)

    slot = _evoker_slot(row)
    assert (slot["count"], slot["target"], slot["school"]) == (2, "spellbook", "evocation")
    assert slot["spell_lists"] == ["class:wizard"]
    catalog = Dnd2024SpellCatalog.from_bundle(runtime.load_bundle("en"))
    assert slot["options"]
    for option in slot["options"]:
        spell = catalog.get(option["ref"])
        assert spell["school"] == "evocation"
        assert "class:wizard" in spell["class_refs"]
        assert 1 <= int(spell["level"]) <= 2
    # 正常增量仍是每级两个：免费项走 grants，不并进 additions。
    assert int(row["spellbook_additions"]) == 2


def test_the_free_picks_land_in_the_spellbook_not_in_the_prepared_list() -> None:
    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=2)["ruleset_character"]
    before = _book(character)
    engine, choices, _row = _evoker_step(runtime, character)
    picks = list(choices["class_spell_choices"]["spell_grants"]["evocation_savant"])

    advanced = engine.apply_next_level(character, choices)
    block = advanced["spellcasting"]["class"]

    assert len(picks) == 2
    assert set(picks) <= set(block["spellbook_refs"])
    assert block["spell_grant_choices"]["evocation_savant"]["spells"] == picks
    # 书 = 旧书 + 正常两个 + 免费两个；免费项不自动 prepared。
    assert len(block["spellbook_refs"]) == len(before) + 4
    assert not set(picks) <= set(block["prepared_spell_refs"])
    assert Dnd2024ClassFeatureResolver(
        runtime.load_bundle("en"),
    ).spell_grant_rows(advanced) == []


def test_a_free_pick_must_be_an_evocation_spell() -> None:
    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=2)["ruleset_character"]
    engine, choices, _row = _evoker_step(runtime, character)
    grants = choices["class_spell_choices"]["spell_grants"]["evocation_savant"]
    book = choices["class_spell_choices"]["spellbook_refs"]
    book[book.index(grants[0])] = "spell:shield"
    grants[0] = "spell:shield"

    preview = engine.preview_next_level(character, choices)

    assert preview["ok"] is False
    assert any("evocation" in error for error in preview["errors"])


def test_a_free_pick_must_be_on_the_wizard_list() -> None:
    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=2)["ruleset_character"]
    engine, choices, _row = _evoker_step(runtime, character)
    grants = choices["class_spell_choices"]["spell_grants"]["evocation_savant"]
    book = choices["class_spell_choices"]["spellbook_refs"]
    # 塑能但不是法师列表：Guiding Bolt 只在牧师表上。
    book[book.index(grants[0])] = "spell:guiding_bolt"
    grants[0] = "spell:guiding_bolt"

    preview = engine.preview_next_level(character, choices)

    assert preview["ok"] is False
    assert any("may choose from" in error for error in preview["errors"])


def test_a_cantrip_is_not_a_free_pick() -> None:
    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=2)["ruleset_character"]
    engine, choices, _row = _evoker_step(runtime, character)
    grants = choices["class_spell_choices"]["spell_grants"]["evocation_savant"]
    book = choices["class_spell_choices"]["spellbook_refs"]
    book[book.index(grants[0])] = "spell:fire_bolt"
    grants[0] = "spell:fire_bolt"

    preview = engine.preview_next_level(character, choices)

    assert preview["ok"] is False
    assert any("below the level" in error for error in preview["errors"])


def test_a_deferred_free_pick_stays_open_and_can_be_filled_later() -> None:
    """3 级不填不是"永久失去"：槽位还在，4 级补上仍然合法。"""

    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=2)["ruleset_character"]
    before = _book(character)
    engine, choices, _row = _evoker_step(runtime, character, fill_grants=False)
    advanced = engine.apply_next_level(character, choices)

    # 只加正常两个，槽位保持开放。
    assert len(_book(advanced)) == len(before) + 2
    assert "spell_grant_choices" not in advanced["spellcasting"]["class"]

    engine2, choices2, row2 = _evoker_step(runtime, advanced)
    slot = _evoker_slot(row2)
    assert (slot["count"], slot["selected_refs"]) == (2, [])
    preview = engine2.preview_next_level(advanced, choices2)
    assert preview["ok"] is True, preview["errors"]
    filled = engine2.apply_next_level(advanced, choices2)
    picks = choices2["class_spell_choices"]["spell_grants"]["evocation_savant"]
    assert set(picks) <= set(_book(filled))


def test_a_level_without_a_new_slot_level_adds_no_free_pick() -> None:
    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=3)["ruleset_character"]
    engine, choices, row = _evoker_step(runtime, character, fill_grants=False)

    slot = _evoker_slot(row)
    assert slot["count"] == 2
    assert len(slot["selected_refs"]) == 2
    assert int(row["spellbook_additions"]) == 2
    assert engine.preview_next_level(character, choices)["ok"] is True


def test_a_new_slot_level_at_five_adds_exactly_one_free_pick() -> None:
    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=4)["ruleset_character"]
    before = _book(character)
    engine, choices, row = _evoker_step(runtime, character)
    slot = _evoker_slot(row)

    assert slot["count"] == 3
    preview = engine.preview_next_level(character, choices)
    assert preview["ok"] is True, preview["errors"]
    advanced = engine.apply_next_level(character, choices)
    picks = choices["class_spell_choices"]["spell_grants"]["evocation_savant"]

    assert len(picks) == 3
    assert set(picks) <= set(_book(advanced))
    # 这一级新增的是"正常两个 + 免费一个"；前两个免费项 3 级就在书里了。
    assert len(_book(advanced)) == len(before) + 3


def test_two_normal_picks_without_grants_are_still_a_complete_level() -> None:
    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=2)["ruleset_character"]
    before = _book(character)
    engine, choices, _row = _evoker_step(runtime, character, fill_grants=False)

    preview = engine.preview_next_level(character, choices)
    assert preview["ok"] is True, preview["errors"]
    advanced = engine.apply_next_level(character, choices)
    assert len(_book(advanced)) == len(before) + 2


def test_four_normal_picks_cannot_stand_in_for_the_free_ones() -> None:
    """多出来的页必须正好是授予项，不能拿四个普通法术凑数。"""

    runtime = Dnd2024Runtime()
    character = _wizard_sheet(runtime, level=2)["ruleset_character"]
    engine, choices, _row = _evoker_step(runtime, character, fill_grants=False)
    row = next(
        item for item in engine.preview_next_level(character, choices)["requirements"]
        if item.get("kind") == "spell_selection"
    )
    block = choices["class_spell_choices"]
    extra = [
        entry["ref"] for entry in row["leveled_spells"]
        if entry["ref"] not in block["spellbook_refs"]
    ][:2]
    block["spellbook_refs"] = [*block["spellbook_refs"], *extra]
    block["prepared_spell_refs"] = [
        ref for ref in block["prepared_spell_refs"] if ref in block["spellbook_refs"]
    ]

    preview = engine.preview_next_level(character, choices)

    assert preview["ok"] is False
    assert any("exactly 2" in error for error in preview["errors"])


# --------------------------------------------------------------------------
# Spell Scroll: still a descriptive entry, so the scroll子能力 stays not_automated
# --------------------------------------------------------------------------


def test_no_spell_scroll_item_reaches_the_runtime() -> None:
    """卷轴没有物品身份，"从卷轴施法"就没有可读的事实。

    目录文件里确实有 `spell_scroll_cantrip` / `spell_scroll_level_1` 两个条目，
    但它们只有 `name`/`desc`/`cost_gp`——没有 `spell_ref`、没有等级、没有
    `activation`，而且**没有装进运行时 item 实体**（115 条里一条都没有）。
    这不是"还没接上"，是缺物品身份本身：自造一个 id 或猜一个法术，都会让
    Use Magic Device 的卷轴部分假装自动化。所以卷轴子能力保持 `not_automated`。
    """

    bundle = Dnd2024Runtime().load_bundle("en")

    assert [key for key in bundle.entities["item"] if "spell_scroll" in key] == []


def test_use_magic_device_declares_no_scroll_automation() -> None:
    """声明里不能出现"看起来支持卷轴"的参数，否则面板会给出假按钮。"""

    definition = _resolver().catalog.features["use_magic_device"]

    assert not [
        key for key in definition.parameters if key.startswith("scroll")
    ]
