"""S0-F31: Use Magic Device, spellbook rituals, Memorize Spell and Evocation Savant."""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.character.attunement import (
    BASE_ATTUNEMENT_SLOTS,
    attunement_capacity,
)
from src.rulesets.dnd2024.exploration.engine import Dnd2024ExplorationEngine
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.progression.advancement import Dnd2024AdvancementEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from tests.rulesets.test_dnd2024_item_activation import (
    SequenceRng,
    _carry,
    _instance,
    _install_item,
    _charged_potion,
    _reconcile,
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


def _advance(
    runtime: Dnd2024Runtime, character: dict, target: int,
    *, subclass: str = "subclass:thief",
) -> dict:
    """Level a character up, filling every required choice generically.

    The rogue needs expertise picks and a feat on the way to 13, so a fixed
    script would break the moment the catalog adds one more required choice.
    """

    engine = Dnd2024AdvancementEngine(runtime.load_bundle("en"))
    while int(character["build"]["level"]) < target:
        requirements = engine.preview_next_level(character)["requirements"]
        choices: dict = {}
        for row in requirements:
            if not row.get("required"):
                continue
            kind = row.get("kind")
            if row["id"] == "subclass_ref":
                choices["subclass_ref"] = subclass
            elif row["id"] == "ability_score_increases":
                abilities = character.get("abilities") or {}
                # 13 级前会拿到两次 ASI：固定加一项会在第二次撞 20 上限。
                # 注意别叫 `target`——那是本函数的循环变量。
                ability = next(
                    (
                        key for key in ("con", "dex", "int", "wis", "str", "cha")
                        if isinstance(abilities.get(key), int) and abilities[key] + 2 <= 20
                    ),
                    "con",
                )
                choices["ability_score_increases"] = {ability: 2}
            elif kind == "feature_choice":
                options = [
                    o["value"] for o in row.get("options") or [] if o.get("available", True)
                ]
                picked = options[: int(row.get("count", 1) or 1)]
                choices.setdefault("feature_choices", []).append({
                    "owner": row["owner"], "feature_id": row["feature_id"],
                    "choice_id": row["choice_id"], "selected_refs": picked,
                })
            else:
                options = [o["value"] for o in row.get("options") or [] if o.get("available", True)]
                if options:
                    choices[row["id"]] = options[0]
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
