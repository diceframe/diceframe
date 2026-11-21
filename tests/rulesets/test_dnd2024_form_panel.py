"""S0-F25 step 3: a form overlays a panel slot by slot, not wholesale.

The point of these tests is that the *shape of the rule* is data.  Wild Shape and
Polymorph run through the same composition function and differ only in their
policy tables, so a third shape-shift later cannot become a third code path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rulesets.dnd2024.forms import (
    POLYMORPH_POLICY,
    WILD_SHAPE_POLICY,
    FormPanelError,
    FormState,
    FormStateError,
    StatPanel,
    assume_form,
    compose,
    panel_from_statblock,
    panel_from_view,
    restrictions,
    wild_shape_state,
)
from src.rulesets.dnd2024.monsters import MonsterCatalog

CATALOG = Path(
    "templates/rulesets/dnd2024_srd/content/monster_catalogs/srd_monsters.json"
)


@pytest.fixture(scope="module")
def wolf_panel() -> StatPanel:
    catalog = MonsterCatalog(json.loads(CATALOG.read_text(encoding="utf-8")))
    wolf = catalog.get("wolf")
    assert wolf is not None
    return panel_from_statblock(wolf)


def _druid_view() -> dict:
    """A level-2 druid: STR 8 DEX 12 CON 14 INT 13 WIS 16 CHA 10, HP 17."""

    return {
        "abilities": {"str": 8, "dex": 12, "con": 14, "int": 13, "wis": 16, "cha": 10},
        "armor_class": 13,
        "speed": 30,
        "senses": {},
        "size": "medium",
        "creature_type": "humanoid",
        "saving_throws": {"int": 3, "wis": 5},
        "skill_values": {"perception": 5, "nature": 3, "stealth": 1},
        "attacks": [{"id": "staff", "name": "Staff", "attack_bonus": 2, "damage": "1d6"}],
        "max_hp": 17,
        "hit_dice": "2d8",
        "languages": ["Common", "Druidic"],
    }


# ---------------------------------------------------------------------------
# 1–3. Wild Shape 的保留 / 替换 / 取高
# ---------------------------------------------------------------------------

def test_wild_shape_replaces_physical_scores_and_keeps_mental_ones(wolf_panel) -> None:
    merged = compose(panel_from_view(_druid_view()), wolf_panel, WILD_SHAPE_POLICY)
    # 物理三项来自狼（STR 14 / DEX 15 / CON 12）
    assert merged.abilities["str"] == 14
    assert merged.abilities["dex"] == 15
    assert merged.abilities["con"] == 12
    assert merged.sources["abilities.str"] == "form"
    # 心智三项保留德鲁伊自己的
    assert merged.abilities["int"] == 13
    assert merged.abilities["wis"] == 16
    assert merged.abilities["cha"] == 10
    assert merged.sources["abilities.wis"] == "character"


def test_wild_shape_keeps_hp_and_hit_dice(wolf_panel) -> None:
    merged = compose(panel_from_view(_druid_view()), wolf_panel, WILD_SHAPE_POLICY)
    assert merged.hp == 17            # 狼是 11，但变形的 HP 是你的
    assert merged.hit_dice == "2d8"
    assert merged.sources["hp"] == "character"


def test_skills_and_saves_take_the_higher_side(wolf_panel) -> None:
    merged = compose(panel_from_view(_druid_view()), wolf_panel, WILD_SHAPE_POLICY)
    # 狼 Perception 5 / Stealth 4；德鲁伊 Perception 5 / Stealth 1
    assert merged.skills["perception"] == 5
    assert merged.skills["stealth"] == 4        # 形态更高
    assert merged.sources["skills"] == "form"
    # 豁免取高：狼的豁免表是空的（它没有熟练豁免，导入时被归一掉了），
    # 所以这里保留德鲁伊自己的两项。
    assert merged.saving_throws == {"int": 3, "wis": 5}


def test_a_save_only_the_form_has_still_wins(wolf_panel) -> None:
    """取高是逐项取，不是整张表二选一。"""

    base = panel_from_view(_druid_view())
    base = StatPanel(**{**base.__dict__, "saving_throws": {}})
    form = StatPanel(**{**wolf_panel.__dict__, "saving_throws": {"str": 4}})
    merged = compose(base, form, WILD_SHAPE_POLICY)
    assert merged.saving_throws == {"str": 4}


def test_armor_class_takes_the_higher(wolf_panel) -> None:
    merged = compose(panel_from_view(_druid_view()), wolf_panel, WILD_SHAPE_POLICY)
    assert merged.armor_class == 13   # 德鲁伊 13 > 狼 12


def test_attacks_come_from_the_form(wolf_panel) -> None:
    merged = compose(panel_from_view(_druid_view()), wolf_panel, WILD_SHAPE_POLICY)
    assert [attack["id"] for attack in merged.attacks] == [
        attack["id"] for attack in wolf_panel.attacks
    ]
    assert merged.sources["attacks"] == "form"


# ---------------------------------------------------------------------------
# 4–5. Polymorph 是同一函数的另一张表
# ---------------------------------------------------------------------------

def test_polymorph_replaces_everything_including_hp_and_mental_scores(
    wolf_panel,
) -> None:
    merged = compose(panel_from_view(_druid_view()), wolf_panel, POLYMORPH_POLICY)
    assert merged.hp == 11                       # 变成狼的 HP
    assert merged.hit_dice == wolf_panel.hit_dice
    assert merged.abilities["wis"] == 12         # 狼的 WIS，不是德鲁伊的 16
    assert merged.abilities["int"] == 3
    assert merged.skills == wolf_panel.skills    # 不取高，直接换
    assert merged.sources["hp"] == "form"


def test_the_two_effects_share_one_code_path(wolf_panel) -> None:
    """Only the policy table differs; the composition call is identical."""

    base = panel_from_view(_druid_view())
    wild = compose(base, wolf_panel, WILD_SHAPE_POLICY)
    poly = compose(base, wolf_panel, POLYMORPH_POLICY)
    assert wild.hp != poly.hp
    assert wild.abilities["wis"] != poly.abilities["wis"]
    # 两者都产出同一种结构
    assert isinstance(wild, StatPanel) and isinstance(poly, StatPanel)


# ---------------------------------------------------------------------------
# 6–8. 策略表的边界
# ---------------------------------------------------------------------------

def test_a_slot_without_a_policy_keeps_the_character_value(wolf_panel) -> None:
    merged = compose(panel_from_view(_druid_view()), wolf_panel, {})
    assert merged.hp == 17
    assert merged.attacks[0]["id"] == "staff"


def test_an_unknown_slot_in_the_policy_is_rejected(wolf_panel) -> None:
    with pytest.raises(FormPanelError, match="unknown slot"):
        compose(panel_from_view(_druid_view()), wolf_panel, {"wingspan": "replace"})


def test_an_unknown_policy_name_is_rejected(wolf_panel) -> None:
    with pytest.raises(FormPanelError, match="unknown policy"):
        compose(panel_from_view(_druid_view()), wolf_panel, {"hp": "merge"})


def test_max_is_not_a_valid_policy_for_a_list_slot(wolf_panel) -> None:
    with pytest.raises(FormPanelError, match="not valid for a list slot"):
        compose(panel_from_view(_druid_view()), wolf_panel, {"attacks": "max"})


# ---------------------------------------------------------------------------
# 9. 面板来源
# ---------------------------------------------------------------------------

def test_a_statblock_panel_drops_hover_from_speeds() -> None:
    """``hover`` 是布尔能力，不是尺数；混进 speeds 会被当成 0 尺速度读。"""

    panel = panel_from_statblock({
        "abilities": {}, "armor_class": 12,
        "speeds": {"walk": 30, "fly": 50, "hover": True},
        "senses": {}, "size": "small", "creature_type": "beast",
        "saving_throws": {}, "skills": {}, "attacks": [], "hp": 1,
        "hit_dice": "1d4", "languages": [],
    })
    assert panel.speeds == {"walk": 30, "fly": 50}


# ---------------------------------------------------------------------------
# 10–14. 变形状态：能不能施法、能不能用装备、存档能不能还原
# ---------------------------------------------------------------------------

def _wolf_entry() -> dict:
    catalog = MonsterCatalog(json.loads(CATALOG.read_text(encoding="utf-8")))
    wolf = catalog.get("wolf")
    assert wolf is not None
    return wolf


def test_wild_shape_state_carries_the_duration_from_the_druid_level() -> None:
    state = wild_shape_state(_wolf_entry(), druid_level=6, beast_spells=False)
    assert state.effect_id == "wild_shape"
    assert state.form_ref == "wolf"
    assert state.duration_hours == 3  # floor(6 / 2)


def test_wild_shape_refuses_a_non_beast_panel() -> None:
    catalog = MonsterCatalog(json.loads(CATALOG.read_text(encoding="utf-8")))
    skeleton = catalog.get("skeleton")
    assert skeleton is not None
    with pytest.raises(FormStateError, match="beast"):
        wild_shape_state(skeleton, druid_level=2, beast_spells=False)


def test_shaping_blocks_spellcasting_unless_beast_spells() -> None:
    state = wild_shape_state(_wolf_entry(), druid_level=18, beast_spells=False)
    assert restrictions(state)["spellcasting"] is False
    allowed = wild_shape_state(_wolf_entry(), druid_level=18, beast_spells=True)
    assert restrictions(allowed)["spellcasting"] is True


def test_merged_equipment_has_no_effect_rather_than_being_hidden() -> None:
    """"Merged" means the item stops working; hiding it in the UI is not enough."""

    merged = wild_shape_state(_wolf_entry(), druid_level=2, beast_spells=False)
    assert restrictions(merged)["equipment_effects"] is False
    worn = wild_shape_state(
        _wolf_entry(), druid_level=2, beast_spells=False, equipment_mode="wear",
    )
    assert restrictions(worn)["equipment_effects"] is True


def test_form_state_round_trips_through_a_save_payload() -> None:
    state = wild_shape_state(
        _wolf_entry(), druid_level=8, beast_spells=True,
        equipment_mode="drop", assumed_at_turn=4,
    )
    restored = FormState.from_payload(state.as_payload())
    assert restored == state
    # 存档里不写面板数值，只写 ref：数值一律从内容重读，避免第二份权威。
    assert "abilities" not in state.as_payload()


def test_an_unknown_effect_or_mode_is_rejected_on_load() -> None:
    with pytest.raises(FormStateError, match="unknown shape-shift effect"):
        FormState.from_payload({"effect_id": "polymorph_self", "form_ref": "wolf"})
    with pytest.raises(FormStateError, match="equipment mode"):
        FormState.from_payload({
            "effect_id": "wild_shape", "form_ref": "wolf", "equipment_mode": "pocket",
        })


def test_the_active_panel_keeps_your_hp_while_you_are_a_wolf() -> None:
    state = wild_shape_state(_wolf_entry(), druid_level=2, beast_spells=False)
    panel = assume_form(panel_from_view(_druid_view()), _wolf_entry(), state)
    assert panel.hp == 17
    assert panel.abilities["str"] == 14
    assert panel.sources["hp"] == "character"


# ---------------------------------------------------------------------------
# 15–17. 接进战斗：事件落库 + 视图换面板（GAP-21 / GAP-23）
# ---------------------------------------------------------------------------

def _shape(engine, instance, form_ref: str = "wolf"):
    from src.engine.modules import ruleset_runtime

    state = ruleset_runtime.state(instance)
    probe = engine.resolve_intent(instance, {
        "intent_id": "probe", "type": "dodge",
        "expected_version": state["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, _rng())
    batch = dict(probe["event_batch"])
    batch["events"] = [{
        "type": "dnd2024.form.assumed", "actor_id": "player:gm",
        "form": wild_shape_state(
            MonsterCatalog(json.loads(CATALOG.read_text(encoding="utf-8"))).get(form_ref),
            druid_level=6, beast_spells=False,
        ).as_payload(),
    }]
    engine.apply_batch(instance, batch)


def _rng():
    from tests.rulesets.dnd2024_monk_common import SequenceRng

    return SequenceRng([])


def _combat():
    from src.rulesets.dnd2024 import Dnd2024Runtime
    from tests.rulesets.dnd2024_monk_common import (
        monk_instance, monk_sheet, start_combat,
    )

    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, monk_sheet(runtime, level=6))
    start_combat(engine, instance)
    return engine, instance


def _view(engine, instance):
    from src.engine.modules import ruleset_runtime

    return engine._actor_view(
        instance, ruleset_runtime.state(instance)["combat"], "player:gm",
    )


def test_an_assumed_form_swaps_the_panel_but_keeps_character_hp() -> None:
    engine, instance = _combat()
    before = _view(engine, instance)
    _shape(engine, instance)
    after = _view(engine, instance)
    assert after["abilities"]["str"] == 14          # 狼的
    assert after["abilities"]["wis"] == before["abilities"]["wis"]  # 保留
    assert after["max_hp"] == before["max_hp"]      # HP 是你的
    assert [a["id"] for a in after["attacks"]] == ["bite-0"]
    assert after["form_ref"] == "wolf"
    assert after["spellcasting_allowed"] is False


def test_reverting_restores_the_character_panel() -> None:
    from src.engine.modules import ruleset_runtime

    engine, instance = _combat()
    before = _view(engine, instance)
    _shape(engine, instance)
    state = ruleset_runtime.state(instance)
    probe = engine.resolve_intent(instance, {
        "intent_id": "probe2", "type": "dodge",
        "expected_version": state["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, _rng())
    batch = dict(probe["event_batch"])
    batch["events"] = [{"type": "dnd2024.form.reverted", "actor_id": "player:gm"}]
    engine.apply_batch(instance, batch)
    after = _view(engine, instance)
    assert after["abilities"] == before["abilities"]
    assert after.get("form_ref") is None


def test_an_unknown_form_ref_leaves_the_panel_untouched() -> None:
    engine, instance = _combat()
    before = _view(engine, instance)
    state_mod = __import__("src.engine.modules", fromlist=["ruleset_runtime"])
    probe = engine.resolve_intent(instance, {
        "intent_id": "probe3", "type": "dodge",
        "expected_version": state_mod.ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, _rng())
    batch = dict(probe["event_batch"])
    batch["events"] = [{
        "type": "dnd2024.form.assumed", "actor_id": "player:gm",
        "form": {"effect_id": "wild_shape", "form_ref": "no-such-beast"},
    }]
    engine.apply_batch(instance, batch)
    after = _view(engine, instance)
    assert after["abilities"] == before["abilities"]
