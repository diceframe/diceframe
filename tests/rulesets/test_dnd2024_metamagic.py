"""S0-F28：超魔容器与已接好的选项。

用例只走 `cast_spell` 意图：扣点、上限、独占与类型替换都必须能在事件流里
看到，直接调 `metamagic.plan` 只能算单元测试，不能证明接好了。
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024 import Dnd2024Runtime
from src.rulesets.dnd2024.spells import metamagic
from tests.rulesets.dnd2024_monk_common import (
    SequenceRng, advancement_choices, monk_instance, start_combat,
)

LOCALE = "en"


def engine_catalog(runtime) -> dict:
    from src.rulesets.dnd2024.combat.catalog import Dnd2024CombatCatalog

    return Dnd2024CombatCatalog.from_bundle(runtime.load_bundle(LOCALE)).spell_effects


def _sorcerer(level: int = 2, points: int = 10, known=("transmuted_spell", "empowered_spell",
                                                       "subtle_spell", "quickened_spell"),
              extra_spells=()):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_dnd2024_stoneskin import wizard_sheet

    runtime = Dnd2024Runtime()
    # 夹具按默认等级建好再改职业等级：wizard_sheet 的升级路径有自己的一套选择。
    sheet = wizard_sheet(runtime)
    character = sheet["ruleset_character"]
    character["build"]["class_levels"] = [{"class_ref": "class:sorcerer", "level": level}]
    character["build"]["level"] = level
    resources = character.setdefault("resources", {}).setdefault("class", {})
    resources["sorcery_points"] = {"current": points, "maximum": level}
    resources["wild_shape"] = {"current": 0, "maximum": 0}
    character.setdefault("features", {})["metamagic"] = {"known": list(known)}
    # 保证手里至少有一个"有自动化效果"的伤害法术，否则施法在超魔之前就被拒。
    catalog = engine_catalog(runtime)
    prepared = character.setdefault("spellcasting", {}).setdefault("class", {})
    refs = list(prepared.get("prepared_spell_refs") or [])
    for spell_id, effect in catalog.items():
        if not effect.get("damage_type"):
            continue
        entity = runtime.load_bundle(LOCALE).get("spell", spell_id) or {}
        if int(entity.get("level", 0) or 0) < 1:
            continue
        ref = f"spell:{spell_id}"
        if ref not in refs:
            refs.append(ref)
        break
    for ref in extra_spells:
        # 夹具直接把法术塞进准备列表：施法口只看"拥有关系"，这正是要证明的。
        if ref not in refs:
            refs.append(str(ref))
    prepared["prepared_spell_refs"] = refs
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance)
    return engine, instance


def _state(instance) -> dict:
    from src.engine.modules import ruleset_runtime

    return ruleset_runtime.state(instance)


def _cast(engine, instance, intent: dict, rolls=()):
    payload = {
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", **intent,
    }
    return engine.resolve_intent(instance, payload, SequenceRng(list(rolls)))


def _castable(engine, instance, types=None) -> tuple[str, int]:
    """An automated, leveled, damaging spell the fixture can actually cast."""

    actor = engine._actor_view(instance, _state(instance)["combat"], "player:gm")
    for ref in actor.get("spell_refs") or []:
        effect = engine.catalog.spell_effects.get(str(ref).removeprefix("spell:"))
        if not effect or not effect.get("damage_type"):
            continue
        if types is not None and effect["damage_type"] not in types:
            continue
        spell = engine.spells.get(ref) or {}
        level = int(spell.get("level", 0) or 0)
        if level >= 1:
            return str(ref), level
    raise AssertionError("the fixture must know one automated leveled damaging spell")


def test_an_unknown_or_unselected_option_is_refused_before_any_cost() -> None:
    engine, instance = _sorcerer(known=("transmuted_spell",))
    ref, level = _castable(engine, instance)
    result = _cast(engine, instance, {
        "intent_id": "mm-1", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1",
        "metamagic": [{"option_id": "quickened_spell"}],
    })
    assert result["ok"] is False
    assert "not known" in result["error"]


def test_transmuted_spell_changes_the_damage_type_and_pays_one_point() -> None:
    engine, instance = _sorcerer()
    # Transmuted 只能在六种元素之间切换，所以这里必须挑一个元素伤害的法术。
    ref, level = _castable(engine, instance, types=set(metamagic.DAMAGE_TYPES))
    original = engine.catalog.spell_effects[ref.removeprefix("spell:")]["damage_type"]
    target = "fire" if original != "fire" else "cold"
    result = _cast(engine, instance, {
        "intent_id": "mm-2", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1",
        "metamagic": [{"option_id": "transmuted_spell", "damage_type": target}],
    }, rolls=[1] * 40)
    assert result["ok"] is True, result.get("error")
    events = result["event_batch"]["events"]
    applied = [e for e in events if e.get("type") == "dnd2024.metamagic.applied"]
    assert applied and applied[0]["options"] == ["transmuted_spell"]
    assert applied[0]["cost"] == 1
    spent = [
        event for event in events
        if event.get("type") == "dnd2024.class_resource.spent"
        and event.get("resource_id") == "sorcery_points"
    ]
    assert spent and int(spent[0]["amount"]) == 1
    damage = [
        event for event in events
        if event.get("type") == "resource.changed" and event.get("damage_type")
    ]
    assert damage, "a damaging spell must produce a damage event"
    assert damage[0]["damage_type"] == target


def test_two_exclusive_options_need_sorcery_incarnate() -> None:
    engine, instance = _sorcerer(level=10)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("features", {})["metamagic"] = {
        "known": ["quickened_spell", "distant_spell"],
    }
    ref, level = _castable(engine, instance)
    result = _cast(engine, instance, {
        "intent_id": "mm-3", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1", "metamagic": [
            {"option_id": "quickened_spell"},
            {"option_id": "distant_spell"},
        ],
    })
    assert result["ok"] is False
    assert "metamagic option" in result["error"]


def test_subtle_spell_strips_components_except_costly_material() -> None:
    plain = {"components": ["V", "S"]}
    costly = {"components": ["V", "S", "M"], "material_cost_specified": True}
    plan = metamagic.Plan(("subtle_spell",), 0, 0, {})
    assert metamagic.effective_components(plain, plan) == ()
    assert metamagic.effective_components(costly, plan) == ("M",)
    assert metamagic.effective_components(plain, metamagic.Plan((), 0, 0, {})) == ("V", "S")


def test_innate_sorcery_spends_a_bonus_action_and_one_use() -> None:
    engine, instance = _sorcerer(level=2)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["innate_sorcery"] = {
        "current": 2, "maximum": 2,
    }
    result = _cast(engine, instance, {
        "intent_id": "is-1", "type": "innate_sorcery",
    })
    assert result["ok"] is True, result.get("error")
    engine.apply_batch(instance, result["event_batch"])
    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert character["resources"]["class"]["innate_sorcery"]["current"] == 1
    assert "innate_sorcery" in (character.get("conditions") or {})
    # +1 DC 只对"术士法术"生效，所以判定发生在按法术求 DC 的那一层。
    view = engine._actor_view(instance, _state(instance)["combat"], "player:gm")
    base = int(character["derived"]["spell_save_dc"])
    sorcerer_spell = {"id": "x", "class_refs": ["class:sorcerer"]}
    wizard_spell = {"id": "y", "class_refs": ["class:wizard"]}
    assert engine._spell_save_dc(view, sorcerer_spell) == base + 1
    assert engine._spell_save_dc(view, wizard_spell) == base


def test_sorcery_incarnate_pays_two_points_when_no_uses_remain() -> None:
    engine, instance = _sorcerer(level=10, points=5)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["innate_sorcery"] = {
        "current": 0, "maximum": 2,
    }
    result = _cast(engine, instance, {"intent_id": "is-2", "type": "innate_sorcery"})
    assert result["ok"] is True, result.get("error")
    spent = [
        e for e in result["event_batch"]["events"]
        if e.get("type") == "dnd2024.class_resource.spent"
        and e.get("resource_id") == "sorcery_points"
    ]
    assert spent and spent[0]["amount"] == 2

    # 7 级之前没有这条路径：次数为 0 直接拒绝。
    engine2, instance2 = _sorcerer(level=2, points=5)
    character2 = instance2.get_character_sheet("gm")["ruleset_character"]
    character2.setdefault("resources", {}).setdefault("class", {})["innate_sorcery"] = {
        "current": 0, "maximum": 2,
    }
    refused = _cast(engine2, instance2, {"intent_id": "is-3", "type": "innate_sorcery"})
    assert refused["ok"] is False
    assert "no Innate Sorcery uses" in refused["error"]


def test_quickened_spell_costs_the_bonus_action_not_the_action() -> None:
    engine, instance = _sorcerer(level=10, known=("quickened_spell",))
    ref, level = _castable(engine, instance)
    result = _cast(engine, instance, {
        "intent_id": "q-1", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1",
        "metamagic": [{"option_id": "quickened_spell"}],
    }, rolls=[1] * 40)
    assert result["ok"] is True, result.get("error")
    spent = [
        e for e in result["event_batch"]["events"]
        if e.get("type") == "dnd2024.action.spent"
    ]
    assert [e["resource"] for e in spent] == ["bonus_action"], spent


def _saving_throw_spell(engine, instance):
    actor = engine._actor_view(instance, _state(instance)["combat"], "player:gm")
    for ref in actor.get("spell_refs") or []:
        effect = engine.catalog.spell_effects.get(str(ref).removeprefix("spell:")) or {}
        spell = engine.spells.get(ref) or {}
        if effect.get("save") and effect.get("damage") and int(spell.get("level", 0) or 0) >= 1:
            return str(ref), int(spell["level"])
    raise AssertionError("fixture needs an automated save-for-damage spell")


def test_careful_spell_makes_a_chosen_ally_succeed_automatically() -> None:
    engine, instance = _sorcerer(level=10, known=("careful_spell",))
    ref, level = _saving_throw_spell(engine, instance)
    result = _cast(engine, instance, {
        "intent_id": "care-1", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1",
        "metamagic": [{"option_id": "careful_spell", "targets": ["enemy:goblin-1"]}],
    }, rolls=[1] * 40)
    assert result["ok"] is True, result.get("error")
    save = next(
        e for e in result["event_batch"]["events"]
        if e.get("kind") == "saving_throw" and e.get("actor_id") == "enemy:goblin-1"
    )
    assert save.get("auto_succeeded") is True
    assert save["rolls"] == []          # 自动成功不掷骰


def test_heightened_spell_gives_exactly_one_target_disadvantage() -> None:
    engine, instance = _sorcerer(level=10, points=10, known=("heightened_spell",))
    ref, level = _saving_throw_spell(engine, instance)
    result = _cast(engine, instance, {
        "intent_id": "height-1", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1",
        "metamagic": [{"option_id": "heightened_spell", "target": "enemy:goblin-1"}],
    }, rolls=[1] * 40)
    assert result["ok"] is True, result.get("error")
    save = next(
        e for e in result["event_batch"]["events"]
        if e.get("kind") == "saving_throw" and e.get("actor_id") == "enemy:goblin-1"
    )
    assert len(save["rolls"]) == 2
    assert min(save["rolls"]) == save["natural"]


def test_distant_spell_doubles_the_range_at_cast_time() -> None:
    engine, instance = _sorcerer(level=10, known=("distant_spell",))
    ref, level = _castable(engine, instance)
    effect = engine.catalog.spell_effects[ref.removeprefix("spell:")]
    base = int(effect.get("range") or 0)
    if base < 5:
        pytest.skip("fixture spell is Touch/Self; doubling is not the interesting case")
    # 把目标放到"基准射程之外、翻倍之内"的位置。
    combat = _state(instance)["combat"]
    far = base + 10
    combat["positions"]["enemy:goblin-1"] = far
    from src.engine.modules import ruleset_runtime

    ruleset_runtime.replace_state(instance, _state(instance))

    plain = _cast(engine, instance, {
        "intent_id": "d-1", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1",
    }, rolls=[1] * 40)
    assert plain["ok"] is False
    assert "range" in plain["error"]

    distant = _cast(engine, instance, {
        "intent_id": "d-2", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1",
        "metamagic": [{"option_id": "distant_spell"}],
    }, rolls=[1] * 40)
    assert distant["ok"] is True, distant.get("error")


def test_extended_spell_doubles_the_duration_it_records() -> None:
    engine, instance = _sorcerer(level=10, known=("extended_spell",))
    spell = {"id": "fog_cloud", "level": 1, "components": ["V", "S"],
             "casting_time": "Action", "range": "120 feet", "duration": "10 minutes"}
    effect = {"mode": "obscuring_zone", "range": 120, "radius_ft": 20,
              "duration_rounds": 100, "concentration": True}
    instance.get_character_sheet("gm")
    intent = {"metamagic": [{"option_id": "extended_spell"}]}
    _, _changed, plan = engine._metamagic_events(
        instance, _state(instance)["combat"], intent, "player:gm", spell, effect,
    )
    assert plan.has("extended_spell")
    # 时长不再写回 effect：由 duration 模块按"基础值 + 修正"求值。
    assert engine._duration_for(effect, spell, 1, plan).rounds == 200
    assert engine._duration_for(effect, spell, 1, None).rounds == 100


def test_twinned_spell_adds_one_target_without_a_higher_slot() -> None:
    engine, instance = _sorcerer(level=10, known=("twinned_spell",))
    spell = {"id": "charm_person", "level": 1, "components": ["V", "S"],
             "casting_time": "Action", "range": "30 feet"}
    effect = {"mode": "save_or_condition", "range": 30, "save": "wis",
              "target_count": 1, "upcast_target_count": 1}
    plan = engine._metamagic_plan(
        {"metamagic": [{"option_id": "twinned_spell"}]},
        instance.get_character_sheet("gm")["ruleset_character"],
        spell, effect, _state(instance)["combat"],
    )
    assert plan.has("twinned_spell")
    from src.rulesets.dnd2024.spells import metamagic

    assert metamagic.OPTIONS["twinned_spell"].requires["upcast_target_bonus"]


def test_the_sorcerer_picks_two_options_when_metamagic_arrives_at_level_two() -> None:
    """2 级必须且只能选两个；选过的不能再选，未选不算失败（老角色可重放）。"""

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_dnd2024_spell_preparation_advancement import sorcerer as sorcerer_fixture

    runtime = Dnd2024Runtime()
    character = sorcerer_fixture.__wrapped__(runtime)   # 仓库自己的术士夹具
    sheet = {"ruleset_character": character}
    character["build"]["class_levels"] = [{"class_ref": "class:sorcerer", "level": 1}]
    character["build"]["level"] = 1
    magic = character["spellcasting"]["class"]
    carry = {
        "cantrip_refs": list(magic.get("cantrip_refs") or []),
        "prepared_spell_refs": [
            *list(magic.get("prepared_spell_refs") or []),
            "spell:charm_person", "spell:color_spray",
        ],
    }
    preview = runtime.preview_advancement(None, sheet, {
        "class_spell_choices": carry,
        "metamagic_option_ids": ["subtle_spell", "quickened_spell"],
    })
    assert preview["ok"] is True, preview["errors"]
    requirement = next(
        row for row in preview["requirements"] if row["id"] == "metamagic_option_ids"
    )
    assert requirement["count"] == 2
    # 与仓库原则一致：可选槽位是升级的必要条件（不选就等于永久放弃）。
    assert not requirement.get("optional")
    missing = runtime.preview_advancement(None, sheet, {"class_spell_choices": carry})
    assert missing["ok"] is False
    assert "metamagic_option_ids is required" in " | ".join(missing["errors"])

    applied = runtime.apply_advancement(None, sheet, {
        "class_spell_choices": carry,
        "metamagic_option_ids": ["subtle_spell", "quickened_spell"],
    })
    assert applied["ruleset_character"]["features"]["metamagic"]["known"] == [
        "subtle_spell", "quickened_spell",
    ]


def test_extended_spell_gives_advantage_on_the_concentration_save() -> None:
    engine, instance = _sorcerer(level=10, known=("extended_spell",))
    combat = _state(instance)["combat"]
    view = engine._actor_view(instance, combat, "player:gm")
    assert view.get("concentration") is None or isinstance(view["concentration"], dict)
    from src.engine.modules import ruleset_runtime

    character = instance.get_character_sheet("gm")["ruleset_character"]
    # 职业施法者的专注住在 spellcasting.class 下（既有存档的形状）。
    holder = character.setdefault("spellcasting", {}).setdefault("class", {})
    holder["concentration"] = {"spell_ref": "spell:fog_cloud", "target_ids": [],
                               "extended": True}
    ruleset_runtime.replace_state(instance, _state(instance))

    view = engine._actor_view(instance, _state(instance)["combat"], "player:gm")
    events = engine._concentration_events(
        instance, _state(instance)["combat"], view, 20, SequenceRng([7, 15]),
    )
    check = next(e for e in events if e.get("kind") == "concentration")
    assert len(check["rolls"]) == 2                 # 优势 = 两次 d20
    assert check["natural"] == 15                   # 取高者
    assert check["advantage_sources"][0]["code"] == "extended_spell"


def test_empowered_spell_rerolls_the_lowest_dice_and_caps_at_charisma() -> None:
    engine, instance = _sorcerer(level=10, known=("empowered_spell",))
    ref, level = _castable(engine, instance)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    limit = max(1, (int(character["abilities"]["cha"]) - 10) // 2)
    result = _cast(engine, instance, {
        "intent_id": "emp-1", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1",
        "metamagic": [{"option_id": "empowered_spell", "count": 1}],
    }, rolls=[1] * 40)
    assert result["ok"] is True, result.get("error")
    rerolled = [
        e for e in result["event_batch"]["events"]
        if e.get("type") == "dnd2024.metamagic.rerolled"
    ]
    assert rerolled, "Empowered Spell must record its reroll"
    assert len(rerolled[0]["dice"]) == 1

    over = _cast(engine, instance, {
        "intent_id": "emp-2", "type": "cast_spell", "spell_ref": ref,
        "slot_level": level, "target_id": "enemy:goblin-1",
        "metamagic": [{"option_id": "empowered_spell", "count": limit + 3}],
    }, rolls=[1] * 40)
    assert over["ok"] is False
    assert "at most" in over["error"]


def test_seeking_spell_rerolls_a_missed_attack() -> None:
    engine, instance = _sorcerer(level=10, known=("seeking_spell",))
    actor = engine._actor_view(instance, _state(instance)["combat"], "player:gm")
    attack_ref = next(
        (str(ref) for ref in actor.get("spell_refs") or []
         if (engine.catalog.spell_effects.get(str(ref).removeprefix("spell:")) or {})
         .get("mode") == "spell_attack"),
        None,
    )
    if attack_ref is None:
        pytest.skip("fixture has no spell attack")
    spell = engine.spells.get(attack_ref) or {}
    # 第一颗 d20 交给"未命中"，重掷那颗交给"命中"。
    result = _cast(engine, instance, {
        "intent_id": "seek-1", "type": "cast_spell", "spell_ref": attack_ref,
        "slot_level": int(spell.get("level", 0) or 0),
        "target_id": "enemy:goblin-1",
        "metamagic": [{"option_id": "seeking_spell"}],
    }, rolls=[1, 20] + [1] * 40)
    assert result["ok"] is True, result.get("error")
    check = next(
        e for e in result["event_batch"]["events"]
        if e.get("kind") == "spell_attack"
    )
    assert check.get("seeking") == {"before": 1, "after": 20}


# ---------------------------------------------------------------------------
# 升级通道：每级可以换掉一个已知选项
# ---------------------------------------------------------------------------


def _spell_answers(preview: dict, character: dict) -> dict:
    """Fill the spell-selection slot up to the target capacity."""

    requirement = next(
        (row for row in preview.get("requirements") or []
         if row["id"] == "class_spell_choices"),
        None,
    )
    magic = (character.get("spellcasting") or {}).get("class") or {}
    cantrips = list(magic.get("cantrip_refs") or [])
    prepared = list(magic.get("prepared_spell_refs") or [])
    if requirement is not None:
        for row in requirement["cantrips"]:
            if len(cantrips) >= int(requirement["cantrip_count"]):
                break
            if row["ref"] not in cantrips:
                cantrips.append(row["ref"])
        for row in requirement["leveled_spells"]:
            if len(prepared) >= int(requirement["prepared_spell_count"]):
                break
            if row["ref"] not in prepared:
                prepared.append(row["ref"])
    return {"class_spell_choices": {
        "cantrip_refs": cantrips, "prepared_spell_refs": prepared,
    }}


def _sheet_at_level_one() -> dict:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_dnd2024_spell_preparation_advancement import sorcerer as sorcerer_fixture

    runtime = Dnd2024Runtime()
    sheet = {"ruleset_character": sorcerer_fixture.__wrapped__(runtime)}
    character = sheet["ruleset_character"]
    character["build"]["class_levels"] = [{"class_ref": "class:sorcerer", "level": 1}]
    character["build"]["level"] = 1
    return sheet


def _walk(runtime: Dnd2024Runtime, sheet: dict, target_level: int) -> dict:
    """Step through the real level-up path, answering every slot."""

    character = sheet["ruleset_character"]
    while int(character["build"]["level"]) < target_level:
        preview = runtime.preview_advancement(None, sheet, {})
        choices = {
            **advancement_choices(preview),
            **_spell_answers(preview, character),
        }
        preview = runtime.preview_advancement(None, sheet, choices)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
        character = sheet["ruleset_character"]
    return sheet


@pytest.fixture(scope="module")
def caster_levels() -> dict[int, dict]:
    """Sheets at levels 3 and 9, built once: a level-up walk is not cheap."""

    runtime = Dnd2024Runtime()
    three = _walk(runtime, _sheet_at_level_one(), 3)
    return {3: three, 9: _walk(runtime, three, 9)}


def _level_up_base(runtime: Dnd2024Runtime, sheet: dict) -> tuple[dict, dict]:
    preview = runtime.preview_advancement(None, sheet, {})
    body = sheet["ruleset_character"]
    return preview, {**advancement_choices(preview), **_spell_answers(preview, body)}


def test_a_known_option_can_be_replaced_on_level_up(caster_levels) -> None:
    runtime = Dnd2024Runtime()
    sheet = caster_levels[3]
    body = sheet["ruleset_character"]
    known = list(body["features"]["metamagic"]["known"])
    assert known == ["careful_spell", "distant_spell"], known

    preview, choices = _level_up_base(runtime, sheet)
    replacement = next(
        row for row in preview["requirements"] if row["id"] == "metamagic_replace"
    )
    assert replacement["known"] == known
    assert "twinned_spell" in [row["value"] for row in replacement["options"]]
    # 3→4 级不再发新选项，替换槽仍然在：换是"每级"，不是"授予级"。
    assert all(row["id"] != "metamagic_option_ids" for row in preview["requirements"])

    choices["metamagic_replace"] = {"drop": "careful_spell", "add": "twinned_spell"}
    accepted = runtime.preview_advancement(None, sheet, choices)
    assert accepted["ok"] is True, accepted["errors"]
    applied = runtime.apply_advancement(None, sheet, choices)
    assert applied["ruleset_character"]["features"]["metamagic"]["known"] == [
        "twinned_spell", "distant_spell",
    ]


def test_replace_refuses_the_option_picked_in_the_same_level(caster_levels) -> None:
    """Picks are written before replacements; adding one of them duplicates it."""

    runtime = Dnd2024Runtime()
    sheet = caster_levels[9]
    preview, choices = _level_up_base(runtime, sheet)
    option_ids = [row["value"] for row in next(
        row for row in preview["requirements"] if row["id"] == "metamagic_option_ids"
    )["options"]][:2]

    choices["metamagic_option_ids"] = option_ids
    choices["metamagic_replace"] = {"drop": "careful_spell", "add": option_ids[0]}
    refused = runtime.preview_advancement(None, sheet, choices)
    assert refused["ok"] is False
    assert "metamagic_replace.add must be an option you do not know" in refused["errors"]


def test_replace_may_drop_an_option_picked_in_the_same_level(caster_levels) -> None:
    """The dropdowns read the post-pick set, so preview order cannot change them."""

    runtime = Dnd2024Runtime()
    sheet = caster_levels[9]
    preview, choices = _level_up_base(runtime, sheet)
    option_ids = [row["value"] for row in next(
        row for row in preview["requirements"] if row["id"] == "metamagic_option_ids"
    )["options"]][:2]

    choices["metamagic_option_ids"] = option_ids
    choices["metamagic_replace"] = {"drop": option_ids[0], "add": "twinned_spell"}
    accepted = runtime.preview_advancement(None, sheet, choices)
    assert accepted["ok"] is True, accepted["errors"]

    replacement = next(
        row for row in accepted["requirements"] if row["id"] == "metamagic_replace"
    )
    assert replacement["known"] == ["careful_spell", "distant_spell", *option_ids]
    assert option_ids[0] not in [row["value"] for row in replacement["options"]]

    applied = runtime.apply_advancement(None, sheet, choices)
    assert applied["ruleset_character"]["features"]["metamagic"]["known"] == [
        "careful_spell", "distant_spell", "twinned_spell", option_ids[1],
    ]


# ---------------------------------------------------------------------------
# Innate Sorcery：增益的适用范围，不是施法门槛
# ---------------------------------------------------------------------------
#
# 这一组用两条法术做对照：
#   * spell:burning_hands —— 术士列表（也在法师列表上），豁免 + 伤害；
#   * spell:vicious_mockery —— 只有吟游诗人列表，豁免 + 伤害；
#   * spell:fire_bolt / spell:eldritch_blast —— 法术攻击戏法，一术士一邪术。
# 目标是证明：法术能不能施放与它是不是"术士法术"无关；只有后者决定 +1 与优势。


def _innate_dc(engine, instance) -> int:
    view = engine._actor_view(instance, _state(instance)["combat"], "player:gm")
    return int(view["spell_save_dc"])


def _activate_innate(engine, instance, intent_id: str = "is-scope") -> None:
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["innate_sorcery"] = {
        "current": 2, "maximum": 2,
    }
    result = _cast(engine, instance, {"intent_id": intent_id, "type": "innate_sorcery"})
    assert result["ok"] is True, result.get("error")
    engine.apply_batch(instance, result["event_batch"])


def _innate_engine(*extra_spells: str):
    engine, instance = _sorcerer(level=2, extra_spells=extra_spells)
    _activate_innate(engine, instance)
    return engine, instance


def test_a_spell_off_the_sorcerer_list_is_still_castable() -> None:
    """法表归属不是施法门槛：拥有关系是唯一判据。

    Innate 限定的只是"哪些法术吃 +1/优势"，它不拦施法。夹具把一条只属于吟游
    诗人列表的戏法放进准备列表（等价于 Magic Initiate 一类跨表授予），施放必须
    成功——否则就是把增益的适用范围误做成了施法前置。
    """

    engine, instance = _innate_engine("spell:vicious_mockery")
    spell = engine.spells.get("spell:vicious_mockery") or {}
    assert "class:sorcerer" not in spell["class_refs"], spell["class_refs"]

    result = _cast(engine, instance, {
        "intent_id": "scope-1", "type": "cast_spell",
        "spell_ref": "spell:vicious_mockery", "slot_level": 0,
        "target_id": "enemy:goblin-1",
    }, rolls=[1] * 40)
    assert result["ok"] is True, result.get("error")


def test_only_sorcerer_spells_take_the_innate_dc_bonus() -> None:
    """两条法术各用一个引擎实例：一次施法就花掉动作，不能同回合连放。"""

    engine, instance = _innate_engine()
    base = _innate_dc(engine, instance)
    sorcerer_spell = _cast(engine, instance, {
        "intent_id": "scope-2", "type": "cast_spell", "spell_ref": "spell:burning_hands",
        "slot_level": 1, "target_id": "enemy:goblin-1",
    }, rolls=[1] * 40)
    assert sorcerer_spell["ok"] is True, sorcerer_spell.get("error")
    save = next(
        event for event in sorcerer_spell["event_batch"]["events"]
        if event.get("kind") == "saving_throw"
    )
    assert save["target"] == base + 1
    assert save["dc_base"] == base
    assert save["innate_sorcery"] is True

    engine2, instance2 = _innate_engine("spell:vicious_mockery")
    base2 = _innate_dc(engine2, instance2)
    other = _cast(engine2, instance2, {
        "intent_id": "scope-3", "type": "cast_spell",
        "spell_ref": "spell:vicious_mockery", "slot_level": 0,
        "target_id": "enemy:goblin-1",
    }, rolls=[1] * 40)
    assert other["ok"] is True, other.get("error")
    save2 = next(
        event for event in other["event_batch"]["events"]
        if event.get("kind") == "saving_throw"
    )
    assert save2["target"] == base2
    # 没有 +1 就不该留下 +1 的痕迹：事件里连键都不能出现，否则日志会
    # 让读的人以为"这次也吃了加成，只是值为 0"。
    assert "dc_base" not in save2
    assert "innate_sorcery" not in save2


def test_innate_attack_advantage_is_limited_to_sorcerer_spells() -> None:
    engine, instance = _innate_engine("spell:eldritch_blast")
    sorcerer_spell = _cast(engine, instance, {
        "intent_id": "scope-4", "type": "cast_spell", "spell_ref": "spell:fire_bolt",
        "slot_level": 0, "target_id": "enemy:goblin-1",
    }, rolls=[20, 20] + [1] * 40)
    assert sorcerer_spell["ok"] is True, sorcerer_spell.get("error")
    attack = next(
        event for event in sorcerer_spell["event_batch"]["events"]
        if event.get("kind") == "spell_attack"
    )
    assert "innate_sorcery" in [row["code"] for row in attack["advantage_sources"]]
    assert attack["advantage_mode"] == "advantage"

    engine2, instance2 = _innate_engine("spell:eldritch_blast")
    other = _cast(engine2, instance2, {
        "intent_id": "scope-5", "type": "cast_spell", "spell_ref": "spell:eldritch_blast",
        "slot_level": 0, "target_id": "enemy:goblin-1",
        # 没有优势就只掷一个 d20：这里给一个 20，后面全是伤害骰。
    }, rolls=[20] + [1] * 40)
    assert other["ok"] is True, other.get("error")
    attack2 = next(
        event for event in other["event_batch"]["events"]
        if event.get("kind") == "spell_attack"
    )
    assert "innate_sorcery" not in [row["code"] for row in attack2["advantage_sources"]]
    assert attack2["advantage_mode"] == ""


def test_repeat_save_snapshot_uses_the_innate_boosted_dc() -> None:
    """持续性法术的后续豁免不能和首次豁免差 1。"""

    engine, instance = _innate_engine()
    base = _innate_dc(engine, instance)
    result = _cast(engine, instance, {
        "intent_id": "scope-6", "type": "cast_spell", "spell_ref": "spell:sleep",
        "slot_level": 1, "target_id": "enemy:goblin-1",
    }, rolls=[1] * 40)
    assert result["ok"] is True, result.get("error")
    first = next(
        event for event in result["event_batch"]["events"]
        if event.get("kind") == "saving_throw"
    )
    applied = next(
        event for event in result["event_batch"]["events"]
        if event.get("type") == "condition.applied"
    )
    assert first["target"] == base + 1
    assert applied["save_repeat"]["dc"] == first["target"]


def test_spell_rows_report_whether_innate_sorcery_applies() -> None:
    engine, instance = _sorcerer(level=2, extra_spells=("spell:vicious_mockery",))

    def rows():
        combat = _state(instance)["combat"]
        actor = engine._actor_view(instance, combat, "player:gm")
        return {
            row["spell_ref"]: row
            for row in engine._available_spells(
                instance, actor, combat.get("economy", {}),
            )
        }

    off = rows()
    assert off["spell:burning_hands"]["innate_sorcery_applies"] is False
    assert off["spell:vicious_mockery"]["innate_sorcery_applies"] is False

    _activate_innate(engine, instance)
    on = rows()
    assert on["spell:burning_hands"]["innate_sorcery_applies"] is True
    assert on["spell:vicious_mockery"]["innate_sorcery_applies"] is False
