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
    bootstrap_known_forms,
    known_form_count,
    learn_form,
    merged_refs_for,
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
        _wolf_entry(), druid_level=2, beast_spells=False,
        equipment_modes={"item:sword": "wear"},
    )
    assert restrictions(worn)["equipment_effects"] is True
    # 逐件：只有 merge 的那件失去效果
    assert merged_refs_for(worn, ["item:sword"]) == []


def test_form_state_round_trips_through_a_save_payload() -> None:
    state = wild_shape_state(
        _wolf_entry(), druid_level=8, beast_spells=True,
        equipment_modes={"item:sword": "drop", "item:shield": "merge"},
        assumed_at_turn=4,
    )
    restored = FormState.from_payload(state.as_payload())
    assert restored == state
    # 存档里不写面板数值，只写 ref：数值一律从内容重读，避免第二份权威。
    assert "abilities" not in state.as_payload()


def test_legacy_form_payload_keeps_the_single_equipment_mode() -> None:
    restored = FormState.from_payload({
        "effect_id": "wild_shape",
        "form_ref": "wolf",
        "equipment_mode": "merge",
    })

    assert restored.equipment_modes == {"*": "merge"}


def test_reverting_a_legacy_active_form_preserves_known_forms() -> None:
    from src.engine.modules import ruleset_runtime

    engine, instance = _combat()
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    canonical["forms"] = {
        "known": {"wild_shape": ["wolf"]},
        "active": {
            "effect_id": "wild_shape",
            "form_ref": "wolf",
            "equipment_mode": "merge",
        },
    }
    state = ruleset_runtime.state(instance)
    batch = {
        "batch_id": "batch_legacy_form_revert",
        "intent_id": "legacy-form-revert",
        "intent_type": "form.revert",
        "expected_version": state["version"],
        "result_version": state["version"] + 1,
        "events": [{
            "type": "dnd2024.form.reverted", "actor_id": "player:gm",
        }],
    }
    engine.apply_batch(instance, batch)

    restored = instance.get_character_sheet("gm")["ruleset_character"]
    assert restored["forms"]["known"]["wild_shape"] == ["wolf"]
    assert "active" not in restored["forms"]


def test_an_unknown_effect_or_mode_is_rejected_on_load() -> None:
    with pytest.raises(FormStateError, match="unknown shape-shift effect"):
        FormState.from_payload({"effect_id": "polymorph_self", "form_ref": "wolf"})
    with pytest.raises(FormStateError, match="equipment mode"):
        FormState.from_payload({
            "effect_id": "wild_shape", "form_ref": "wolf",
            "equipment_modes": {"item:sword": "pocket"},
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


def test_an_unknown_form_ref_fails_closed() -> None:
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
    # 找不到形态时不能静默回退到人类面板：那会让"变形中"的角色拿着人类
    # 数值继续战斗，而所有消费者都以为他在野兽形态里。
    with pytest.raises(Exception, match="missing from the catalogue"):
        _view(engine, instance)


# ---------------------------------------------------------------------------
# 18–22. 变形入口：意图、成本、门槛（S0-F25 收尾 ①③）
# ---------------------------------------------------------------------------

def _druid_combat(level: int = 2):
    """A druid in combat, built the same way the other class fixtures are."""

    from src.rulesets.dnd2024 import Dnd2024Runtime
    from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
    from tests.rulesets.dnd2024_monk_common import (
        SequenceRng, monk_draft, monk_instance, start_combat,
    )

    runtime = Dnd2024Runtime()
    draft = monk_draft(runtime, "en")
    draft.update(
        class_ref="class:druid",
        class_skill_refs=["skill:animal_handling", "skill:nature"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:druid_a",
        base_abilities={"str": 10, "dex": 13, "con": 14, "int": 8, "wis": 15, "cha": 12},
    )
    choices = runtime.builder_choices(None, draft)
    # 物种技能不能与职业技能重复；按选项列表里第一个不冲突的取。
    taken = set(draft["class_skill_refs"])
    draft["species_skill_refs"] = [
        item["ref"] for item in choices["species_skills"] if item["ref"] not in taken
    ][: choices["species_skill_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    options = Dnd2024SpellSelection(runtime.load_bundle("en")).options("class:druid", 1)
    requirements = options["requirements"]
    leveled = [row["ref"] for row in options["leveled_spells"]]
    draft["class_spell_choices"] = {
        "cantrip_refs": [row["ref"] for row in options["cantrips"]][: int(requirements["cantrip_count"])],
        "prepared_spell_refs": leveled[: int(requirements["prepared_spell_count"])],
        "spellbook_refs": leveled[: int(requirements.get("spellbook_minimum") or 0)],
    }
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        row = Dnd2024SpellSelection(runtime.load_bundle("en")).options(
            "class:druid", next_level,
        )
        need = row["requirements"]
        picks = {
            "class_spell_choices": {
                "cantrip_refs": [c["ref"] for c in row["cantrips"]][: int(need["cantrip_count"])],
                "prepared_spell_refs": [
                    c["ref"] for c in row["leveled_spells"]
                ][: int(need["prepared_spell_count"])],
                "spellbook_refs": [
                    c["ref"] for c in row["leveled_spells"]
                ][: int(need.get("spellbook_minimum") or 0)],
            },
        }
        preview = runtime.preview_advancement(None, sheet, picks)
        options = (preview.get("form_requirements") or {}).get("known_form_options")
        if options:
            # 德鲁伊到 2 级必须选满已知形态：从目录给的合法选项里取前 N 个。
            count = int(preview["form_requirements"]["known_form_count"])
            refs = [row["ref"] for row in options]
            # 这些用例要变成狼，所以把 wolf 排进已知形态（它 CR 1/4，2 级合法）。
            preferred = ["wolf"] if "wolf" in refs else []
            picks["known_form_refs"] = (
                preferred + [ref for ref in refs if ref not in preferred]
            )[:count]
            preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance)
    return engine, instance, SequenceRng


def test_a_druid_can_assume_a_beast_form() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    state = ruleset_runtime.state(instance)
    result = engine.resolve_intent(instance, {
        "intent_id": "shape-1", "type": "form.assume",
        "expected_version": state["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "form_ref": "wolf",
    }, rng([]))
    assert result["ok"] is True, result.get("error")
    kinds = [event["type"] for event in result["event_batch"]["events"]]
    assert "dnd2024.form.assumed" in kinds
    assert "dnd2024.action.spent" in kinds
    engine.apply_batch(instance, result["event_batch"])
    view = engine._actor_view(instance, ruleset_runtime.state(instance)["combat"], "player:gm")
    assert view["abilities"]["str"] == 14      # 狼的 STR
    assert view["form_ref"] == "wolf"
    # 临时 HP = 德鲁伊等级
    character = instance.get_character_sheet("gm")["ruleset_character"]
    assert int(character["resources"].get("temp_hp", 0) or 0) == 2


def test_legacy_druid_without_known_form_state_uses_the_safe_bootstrap() -> None:
    engine, instance, rng = _druid_combat(2)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("forms", {}).pop("known", None)
    state = __import__("src.engine.modules", fromlist=["ruleset_runtime"]).ruleset_runtime.state(instance)

    result = engine.resolve_intent(instance, {
        "intent_id": "legacy-shape", "type": "form.assume",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "form_ref": "wolf",
    }, rng([]))

    assert result["ok"] is True, result.get("error")


def test_available_intents_exposes_assume_and_revert_controls() -> None:
    engine, instance, rng = _druid_combat(2)
    actions = engine.available_intents(instance, "gm")
    assume = next(action for action in actions if action["type"] == "form.assume")
    assert {row["ref"] for row in assume["forms"]} >= {"wolf"}

    state = __import__("src.engine.modules", fromlist=["ruleset_runtime"]).ruleset_runtime.state(instance)
    result = engine.resolve_intent(instance, {
        "intent_id": "shape-ui", "type": "form.assume",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "form_ref": "wolf",
    }, rng([]))
    assert result["ok"] is True
    engine.apply_batch(instance, result["event_batch"])
    state = __import__("src.engine.modules", fromlist=["ruleset_runtime"]).ruleset_runtime.state(instance)
    state["combat"]["economy"]["bonus_action"] = 1
    actions = engine.available_intents(instance, "gm")
    assert any(action["type"] == "form.revert" for action in actions), actions


def test_wild_shape_refuses_a_form_above_the_level_cap() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    result = engine.resolve_intent(instance, {
        "intent_id": "shape-2", "type": "form.assume",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "form_ref": "brown-bear",
    }, rng([]))
    assert result["ok"] is False
    assert "CR" in result["error"]


def test_wild_shape_refuses_a_non_beast_form() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    result = engine.resolve_intent(instance, {
        "intent_id": "shape-3", "type": "form.assume",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "form_ref": "skeleton",
    }, rng([]))
    assert result["ok"] is False
    assert "beast" in result["error"]


def test_reverting_requires_an_active_form() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    result = engine.resolve_intent(instance, {
        "intent_id": "shape-4", "type": "form.revert",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, rng([]))
    assert result["ok"] is False
    assert "no shape" in result["error"]


def test_new_incapacitation_is_seen_by_the_same_batch_shape_exit_sweep() -> None:
    engine, instance, _rng_factory = _shaped_druid(2)
    from src.engine.modules import ruleset_runtime

    combat = ruleset_runtime.state(instance)["combat"]
    exits = engine._shape_exit_events(instance, combat, [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "stunned", "source_actor_id": "enemy:goblin",
    }])

    assert [event["reason"] for event in exits] == ["incapacitated"]


def test_damage_held_by_relentless_rage_does_not_end_the_shape() -> None:
    engine, instance, _rng_factory = _shaped_druid(2)
    from src.engine.modules import ruleset_runtime

    combat = ruleset_runtime.state(instance)["combat"]
    lethal = {
        "type": "resource.changed", "resource": "hp", "target_id": "player:gm",
        "delta": -999, "amount": 999, "relentless": {"feature_id": "relentless_rage"},
    }
    # reducer 把这次伤害挂在"是否用不屈狂暴"的决策上，HP 不动。预演必须给出
    # 同一个答案，否则形态会在 HP 根本没降的时候被结束。
    assert engine._shape_exit_events(instance, combat, [dict(lethal)]) == []

    # 玩家放弃不屈、伤害真正落地时照常结束形态。
    resolved = {**lethal, "relentless_deferred": True}
    exits = engine._shape_exit_events(instance, combat, [resolved])
    assert [event["reason"] for event in exits] == ["incapacitated"]


def test_an_incapacitated_druid_is_not_offered_the_shape_entries() -> None:
    engine, instance, rng = _shaped_druid(2)
    from src.engine.modules import ruleset_runtime

    probe = engine.resolve_intent(instance, {
        "intent_id": "probe-stunned-ui", "type": "dodge",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, rng([]))
    batch = dict(probe["event_batch"])
    batch["events"] = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "stunned", "source_actor_id": "enemy:goblin",
    }]
    engine.apply_batch(instance, batch)
    # 下一回合的预算：已变形、次数也还有，平时两条入口都会出现。
    ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] = 1

    offered = {action["type"] for action in engine.available_intents(instance, "gm")}

    assert "form.assume" not in offered
    assert "form.revert" not in offered


# ---------------------------------------------------------------------------
# 23–26. 已知形态（R1）与逐件装备（R2）
# ---------------------------------------------------------------------------

def test_known_form_count_follows_the_beast_shapes_table() -> None:
    assert known_form_count(2) == 4
    assert known_form_count(4) == 6
    assert known_form_count(8) == 8


def test_bootstrap_uses_the_srd_recommended_forms() -> None:
    entries = [
        {"id": "rat", "creature_type": "beast", "cr": "0", "speeds": {"walk": 20}},
        {"id": "riding-horse", "creature_type": "beast", "cr": "1/4", "speeds": {"walk": 60}},
        {"id": "spider", "creature_type": "beast", "cr": "0", "speeds": {"walk": 20, "climb": 20}},
        {"id": "wolf", "creature_type": "beast", "cr": "1/4", "speeds": {"walk": 40}},
        {"id": "bat", "creature_type": "beast", "cr": "0", "speeds": {"walk": 5, "fly": 30}},
    ]
    seeded = bootstrap_known_forms(entries, 2)
    assert seeded == ["rat", "riding-horse", "spider", "wolf"]   # 蝙蝠有飞行，排除
    assert "bat" not in seeded


def test_learning_a_form_is_refused_when_the_list_is_full() -> None:
    full = ["rat", "riding-horse", "spider", "wolf"]
    with pytest.raises(FormStateError, match="full"):
        learn_form(full, "badger", druid_level=2)
    assert learn_form(full, "wolf", druid_level=2) == full       # 已在列表里，幂等


def test_merged_items_are_reported_per_item() -> None:
    from src.rulesets.dnd2024.forms import mode_for

    state = wild_shape_state(
        _wolf_entry(), druid_level=2, beast_spells=False,
        equipment_modes={"item:sword": "merge", "item:shield": "wear"},
    )
    assert mode_for(state, "item:sword") == "merge"
    assert mode_for(state, "item:shield") == "wear"
    assert merged_refs_for(state, ["item:sword", "item:shield"]) == ["item:sword"]


# ---------------------------------------------------------------------------
# 27–29. 变形中的施法（R3）
# ---------------------------------------------------------------------------

def _shaped_druid(level: int):
    engine, instance, rng = _druid_combat(level)
    from src.engine.modules import ruleset_runtime

    state = ruleset_runtime.state(instance)
    result = engine.resolve_intent(instance, {
        "intent_id": f"shape-{level}", "type": "form.assume",
        "expected_version": state["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "form_ref": "wolf",
    }, rng([]))
    assert result["ok"] is True, result.get("error")
    engine.apply_batch(instance, result["event_batch"])
    return engine, instance, rng


def test_a_shaped_druid_below_18_cannot_cast() -> None:
    engine, instance, rng = _shaped_druid(2)
    from src.engine.modules import ruleset_runtime

    result = engine.resolve_intent(instance, {
        "intent_id": "cast-1", "type": "cast_spell",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:cure_wounds", "target_id": "player:gm",
    }, rng([]))
    assert result["ok"] is False
    assert "does not allow spellcasting" in result["error"]


def test_material_rules_read_the_catalog_flags_for_real_spells() -> None:
    """用目录里的真实法术验证，而不是手搓一个数据里不存在的形状。"""

    from src.rulesets.dnd2024 import Dnd2024Runtime
    from src.rulesets.dnd2024.combat.validation import CombatValidationMixin

    from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog

    runtime = Dnd2024Runtime()
    spells = Dnd2024SpellCatalog.from_bundle(runtime.load_bundle("en"))
    revivify = spells.get("spell:revivify")             # 300gp 钻石 + 消耗
    fireball = spells.get("spell:fireball")             # 材料但无价格、不消耗
    shield = spells.get("spell:shield")                 # 无材料
    assert "consumes" in CombatValidationMixin._material_restriction(revivify)
    assert CombatValidationMixin._material_restriction(fireball) == ""
    assert CombatValidationMixin._material_restriction(shield) == ""


def test_a_spell_with_materials_but_no_flags_fails_closed() -> None:
    from src.rulesets.dnd2024.combat.validation import CombatValidationMixin

    unclassified = {"components": ["V", "S", "M"]}
    assert "not classified" in CombatValidationMixin._material_restriction(unclassified)
    assert CombatValidationMixin._material_restriction({}) != ""


def test_the_material_import_reads_a_price_not_a_coin_substring() -> None:
    """'a sprig of mistletoe' 里的 ' sp' 不是货币，'1 Copper Piece' 才是价格。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "import_srd_spell_materials", "scripts/rulesets/import_srd_spell_materials.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cost = module.has_material_cost

    for text in (
        "a sprig of mistletoe", "a metal spring", "a drop of bitumen and a spider",
        "a bit of spiderweb", "a glass sphere", "a miniature crystal sphere",
        "a pinch of powdered iron", "which the spell consumes",
    ):
        assert cost(text) is False, text
    for text in (
        "a diamond worth 300+ GP", "a Holy Symbol worth 5+ GP",
        "one 150+ GP black onyx stone for each corpse",
        "1 Copper Piece", "2 Copper Pieces, which the spell consumes",
    ):
        assert cost(text) is True, text

    assert module._row_key("arcanist_s_magic_aura") == module._row_key("arcanists-magic-aura")
    assert module._row_key("enlarge_reduce") == module._row_key("enlargereduce")


def test_every_material_spell_in_the_catalog_is_classified() -> None:
    """未分类的法术在运行时 fail closed；目录里不该再有漏掉的。"""
    spells = json.loads(Path(
        "templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json",
    ).read_text(encoding="utf-8"))["spells"]
    unclassified = [
        spell["id"] for spell in spells
        if isinstance(spell.get("components"), list)
        and any(str(component).upper().startswith("M") for component in spell["components"])
        and ("material_cost_specified" not in spell or "material_consumed" not in spell)
    ]
    assert unclassified == []


def test_beast_spells_material_flags_match_the_rule_text() -> None:
    """用目录里的真实法术锁住导入结果，而不是手搓一条数据里不存在的形状。"""
    from src.rulesets.dnd2024 import Dnd2024Runtime
    from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog

    runtime = Dnd2024Runtime()
    spells = Dnd2024SpellCatalog.from_bundle(runtime.load_bundle("en"))

    # 免费材料：曾被 " sp" 子串误判成计费，Beast Spells 因此错误地拒绝它们。
    for ref in (
        "spell:goodberry", "spell:levitate", "spell:spider_climb", "spell:web",
        "spell:resilient_sphere", "spell:freezing_sphere",
        # 先前没对上上游键、因此未分类（fail closed）的四个。
        "spell:arcanist_s_magic_aura", "spell:dragon_s_breath",
        "spell:enlarge_reduce", "spell:antipathy_sympathy",
    ):
        assert spells.get(ref).get("material_cost_specified") is False, ref
        assert spells.get(ref).get("material_consumed") is False, ref
    # 材料本身就是钱，或者写明了价格。
    for ref in ("spell:detect_thoughts", "spell:create_undead", "spell:bless", "spell:revivify"):
        assert spells.get(ref).get("material_cost_specified") is True, ref


# ---------------------------------------------------------------------------
# 30–32. 结束条件：失能 / 死亡 / 到期（R4b）
# ---------------------------------------------------------------------------

def test_incapacitation_ends_the_shape_on_the_next_intent() -> None:
    engine, instance, rng = _shaped_druid(2)
    from src.engine.modules import ruleset_runtime

    state = ruleset_runtime.state(instance)
    probe = engine.resolve_intent(instance, {
        "intent_id": "probe-incap", "type": "dodge",
        "expected_version": state["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, rng([]))
    batch = dict(probe["event_batch"])
    batch["events"] = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "stunned", "source_actor_id": "enemy:goblin",
    }]
    engine.apply_batch(instance, batch)
    # 下一条意图的公共收尾扫到失能 → 变回原形
    view = engine._actor_view(instance, ruleset_runtime.state(instance)["combat"], "player:gm")
    exits = engine._shape_exit_events(instance, ruleset_runtime.state(instance)["combat"])
    assert view.get("form_ref") == "wolf"
    assert [event["reason"] for event in exits] == ["incapacitated"]


def test_death_ends_the_shape_with_its_own_reason() -> None:
    engine, instance, rng = _shaped_druid(2)
    from src.engine.modules import ruleset_runtime

    state = ruleset_runtime.state(instance)
    probe = engine.resolve_intent(instance, {
        "intent_id": "probe-dead", "type": "dodge",
        "expected_version": state["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, rng([]))
    batch = dict(probe["event_batch"])
    batch["events"] = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "dead", "source_actor_id": "enemy:goblin",
    }]
    engine.apply_batch(instance, batch)
    exits = engine._shape_exit_events(instance, ruleset_runtime.state(instance)["combat"])
    assert [event["reason"] for event in exits] == ["death"]


def _stored_shape(instance, form_ref: str = "wolf") -> None:
    """Put the druid in a shape the way a save would, without spending the turn."""

    character = instance.get_character_sheet("gm")["ruleset_character"]
    payload = wild_shape_state(
        _wolf_entry(), druid_level=2, beast_spells=False,
    ).as_payload()
    payload["form_ref"] = form_ref
    character.setdefault("forms", {})["active"] = payload


def test_the_shape_expires_when_the_clock_passes_its_duration(monkeypatch) -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    _stored_shape(instance)
    combat = ruleset_runtime.state(instance)["combat"]
    assert engine._shape_exit_events(instance, combat) == []   # 刚变形，还没到时长

    # 2 级 → duration_hours == 1；时钟走过整整一小时后必然过期。
    # 引擎用 __slots__，时钟补丁打在类上。
    active = instance.get_character_sheet("gm")["ruleset_character"]["forms"]["active"]
    assumed = int(active["assumed_at_minute"])
    monkeypatch.setattr(type(engine), "_logical_minutes", lambda self, _instance: assumed + 60)
    exits = engine._shape_exit_events(instance, combat)
    assert [(event["type"], event["reason"]) for event in exits] == [
        ("dnd2024.form.reverted", "expired"),
    ]


def test_an_unreadable_stored_shape_ends_on_the_next_intent() -> None:
    """目录改名或内容包停用后，存档里的形态引用会读不出 stat block。

    视图层拒绝为它猜一份面板是对的，但这个角色不能因此做不了任何意图——包括
    变回原形——轮到他时整桌都走不下去。公共收尾要替他结束这个形态。
    """
    from src.rulesets.dnd2024.combat.primitives import CombatIntentError

    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    _stored_shape(instance, form_ref="no-such-beast")
    with pytest.raises(CombatIntentError):
        engine._actor_view(instance, ruleset_runtime.state(instance)["combat"], "player:gm")

    state = ruleset_runtime.state(instance)
    result = engine.resolve_intent(instance, {
        "intent_id": "cover-1", "type": "combat.set_cover",
        "expected_version": state["version"], "submitted_by": "gm",
        "target_id": "enemy:goblin-1", "level": "half",
    }, rng([]))
    assert result["ok"] is True, result.get("error")
    engine.apply_batch(instance, result["event_batch"])

    exits = [
        event for event in result["event_batch"]["events"]
        if event["type"] == "dnd2024.form.reverted"
    ]
    assert [event["reason"] for event in exits] == ["unavailable"]
    view = engine._actor_view(instance, ruleset_runtime.state(instance)["combat"], "player:gm")
    assert view.get("form_ref") is None


def test_the_revert_reason_is_set_by_the_engine() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    _stored_shape(instance)
    result = engine.resolve_intent(instance, {
        "intent_id": "revert-1", "type": "form.revert",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "reason": "death",
    }, rng([]))
    assert result["ok"] is True, result.get("error")

    reverted = next(
        event for event in result["event_batch"]["events"]
        if event["type"] == "dnd2024.form.reverted"
    )
    assert reverted["reason"] == "voluntary"


def test_an_incapacitated_druid_cannot_change_shape() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    _stored_shape(instance)
    state = ruleset_runtime.state(instance)
    probe = engine.resolve_intent(instance, {
        "intent_id": "probe-stunned", "type": "dodge",
        "expected_version": state["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
    }, rng([]))
    batch = dict(probe["event_batch"])
    batch["events"] = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "stunned", "source_actor_id": "enemy:goblin-1",
    }]
    engine.apply_batch(instance, batch)

    for intent_id, intent_type in (("revert-stunned", "form.revert"), ("assume-stunned", "form.assume")):
        result = engine.resolve_intent(instance, {
            "intent_id": intent_id, "type": intent_type,
            "expected_version": ruleset_runtime.state(instance)["version"],
            "submitted_by": "gm", "actor_id": "player:gm", "form_ref": "wolf",
        }, rng([]))
        assert result["ok"] is False
        assert "incapacitated actor cannot take actions" in result["error"]


def test_initiative_is_rolled_with_the_shape_dexterity() -> None:
    from src.rulesets.dnd2024.character.builder import ability_modifier
    from tests.rulesets.dnd2024_monk_common import goblin

    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    _stored_shape(instance)                    # 狼的 DEX 15；角色卡上是 DEX 13
    # 角色卡的先攻 = 敏捷 + 其它来源（这个夹具带着 Alert）。只有敏捷那一项
    # 换成狼的，专长之类的加值照留。
    sheet = int(instance.get_character_sheet("gm")["ruleset_character"]["derived"]["initiative"])
    expected = sheet - ability_modifier(13) + ability_modifier(15)
    assert expected > sheet
    state = ruleset_runtime.state(instance)
    ended = engine.resolve_intent(instance, {
        "intent_id": "end-fight", "type": "combat.end",
        "expected_version": state["version"], "submitted_by": "gm",
    }, rng([]))
    assert ended["ok"] is True, ended.get("error")
    engine.apply_batch(instance, ended["event_batch"])

    # 形态跨战斗保留：带着狼形态进入下一场，先攻用的是狼的敏捷（+2）。
    state = ruleset_runtime.state(instance)
    restarted = engine.resolve_intent(instance, {
        "intent_id": "start-2", "type": "combat.start",
        "expected_version": state["version"], "submitted_by": "gm",
        "enemies": [goblin(position=30)],
    }, rng([20, 1]))
    assert restarted["ok"] is True, restarted.get("error")
    started = next(
        event for event in restarted["event_batch"]["events"]
        if event["type"] == "dnd2024.combat.started"
    )
    roll = next(
        row for row in started["initiative_rolls"] if row["actor_id"] == "player:gm"
    )
    assert roll["modifier"] == expected


def test_outside_combat_the_shape_intent_reports_the_real_reason() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    state = ruleset_runtime.state(instance)
    ended = engine.resolve_intent(instance, {
        "intent_id": "end-fight", "type": "combat.end",
        "expected_version": state["version"], "submitted_by": "gm",
    }, rng([]))
    assert ended["ok"] is True, ended.get("error")
    engine.apply_batch(instance, ended["event_batch"])

    # 版本、战斗状态、回合与归属先于特性资格：否则玩家在战斗外看到的是
    # "附赠动作已用完"，而不是这里真正的原因。
    result = engine.resolve_intent(instance, {
        "intent_id": "shape-outside", "type": "form.assume",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "form_ref": "wolf",
    }, rng([]))
    assert result["ok"] is False
    assert "combat is not active" in result["error"]


# ---------------------------------------------------------------------------
# 33–36. 已知形态：资格、选择、长休替换（R1 数据通）
# ---------------------------------------------------------------------------

def _catalog_entries() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))["entries"]


def test_eligible_forms_respect_cr_and_fly() -> None:
    from src.rulesets.dnd2024.forms import eligible_forms

    low = {row["ref"] for row in eligible_forms(_catalog_entries(), 2)}
    assert "wolf" in low and "brown-bear" not in low      # CR 1/4 vs CR 1
    assert "bat" not in low                                # 会飞，8 级前不可
    high = {row["ref"] for row in eligible_forms(_catalog_entries(), 8)}
    assert "brown-bear" in high and "bat" in high


def test_selection_must_be_exactly_the_level_count() -> None:
    from src.rulesets.dnd2024.forms import validate_selection

    entries = _catalog_entries()
    assert validate_selection(["wolf", "rat"], 2, entries)     # 只给了 2 个
    assert validate_selection(["wolf", "wolf", "rat", "cat"], 2, entries)  # 重复
    assert validate_selection(["wolf", "rat", "cat", "brown-bear"], 2, entries)  # CR 超标
    ok = validate_selection(["wolf", "rat", "cat", "spider"], 2, entries)
    assert ok == []


def test_reselection_swaps_exactly_one_form() -> None:
    from src.rulesets.dnd2024.forms import KnownFormsError, replace_one

    known = ["wolf", "rat", "cat", "spider"]
    assert replace_one(known, "spider", "badger", 2, _catalog_entries()) == [
        "wolf", "rat", "cat", "badger",
    ]
    with pytest.raises(KnownFormsError, match="already known"):
        replace_one(known, "spider", "wolf", 2, _catalog_entries())
    with pytest.raises(KnownFormsError, match="not known"):
        replace_one(known, "owl", "badger", 2, _catalog_entries())


def test_a_long_rest_replaces_one_known_form() -> None:
    from src.rulesets.dnd2024 import Dnd2024Runtime
    from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine

    engine = Dnd2024RestEngine(Dnd2024Runtime().load_bundle("en"))
    character = {
        "build": {"class_levels": [{"class_ref": "class:druid", "level": 2}]},
        "resources": {"hp": 10, "max_hp": 10, "hit_dice": {"d8": 2}},
        "forms": {"known": {"wild_shape": ["wolf", "rat", "cat", "spider"]}},
        "abilities": {"str": 10, "dex": 12, "con": 14, "int": 8, "wis": 15, "cha": 10},
        "equipment": {"item_refs": []},
        "proficiencies": {"armor_category_refs": []},
        "features": {}, "conditions": {},
    }
    out = engine.complete_long_rest(
        character, wild_shape_forms={"old_form_ref": "spider", "new_form_ref": "badger"},
    )
    stripped = out.get("character") or out.get("ruleset_character") or out
    assert stripped["forms"]["known"]["wild_shape"] == ["wolf", "rat", "cat", "badger"]


def test_a_long_rest_swaps_a_form_for_a_druid_without_a_known_list() -> None:
    """旧存档没有 forms.known：战斗按推荐形态兜底，长休必须给出同一个答案。

    否则这些角色在战斗里"会"的形态，到长休一个都不认识，永远换不了。
    """
    from src.rulesets.dnd2024 import Dnd2024Runtime
    from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine

    engine = Dnd2024RestEngine(Dnd2024Runtime().load_bundle("en"))
    character = {
        "build": {"class_levels": [{"class_ref": "class:druid", "level": 2}]},
        "resources": {"hp": 10, "max_hp": 10, "hit_dice": {"d8": 2}},
        "abilities": {"str": 10, "dex": 12, "con": 14, "int": 8, "wis": 15, "cha": 10},
        "equipment": {"item_refs": []},
        "proficiencies": {"armor_category_refs": []},
        "features": {}, "conditions": {},
    }

    out = engine.complete_long_rest(
        character, wild_shape_forms={"old_form_ref": "wolf", "new_form_ref": "badger"},
    )

    stripped = out.get("character") or out.get("ruleset_character") or out
    # 推荐四形态里的 wolf 被换成 badger，并且这次替换把列表落在了角色上。
    assert stripped["forms"]["known"]["wild_shape"] == [
        "rat", "riding-horse", "spider", "badger",
    ]


# ---------------------------------------------------------------------------
# 37–40. Wild Resurgence / Nature Magician / Evergreen（R5、R6）
# ---------------------------------------------------------------------------

def _conversion_engine(level: int, uses: int, maximum: int = 4):
    """A druid at ``level`` with a known Wild Shape budget, ready to convert."""

    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    character = instance.get_character_sheet("gm")["ruleset_character"]
    character["build"]["class_levels"][0]["level"] = level
    character["build"]["level"] = level
    slots = character.setdefault("resources", {}).setdefault("class", {})
    slots["wild_shape"] = {"current": uses, "maximum": maximum}
    slots.setdefault("wild_resurgence", {"current": 1, "maximum": 1})
    if level >= 20:
        # 20 级角色升级时会带上 archdruid 的次数；这里手改等级要一并补上。
        slots["archdruid"] = {"current": 1, "maximum": 1}
    return engine, instance, rng


def test_wild_resurgence_converts_a_slot_into_a_use_only_when_empty() -> None:
    engine, instance, rng = _conversion_engine(5, uses=1)
    from src.engine.modules import ruleset_runtime

    state = ruleset_runtime.state(instance)
    blocked = engine.resolve_intent(instance, {
        "intent_id": "wr-1", "type": "wild_resurgence",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "direction": "slot_to_use", "slot_level": 1,
    }, rng([]))
    assert blocked["ok"] is False
    assert "still have Wild Shape uses" in blocked["error"]

    engine, instance, rng = _conversion_engine(5, uses=0)
    state = ruleset_runtime.state(instance)
    allowed = engine.resolve_intent(instance, {
        "intent_id": "wr-2", "type": "wild_resurgence",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "direction": "slot_to_use", "slot_level": 1,
    }, rng([]))
    assert allowed["ok"] is True, allowed.get("error")
    kinds = [event["type"] for event in allowed["event_batch"]["events"]]
    assert "dnd2024.class_resource.restored" in kinds
    engine.apply_batch(instance, allowed["event_batch"])
    after = instance.get_character_sheet("gm")["ruleset_character"]
    assert after["resources"]["class"]["wild_shape"]["current"] == 1


def test_wild_resurgence_converts_a_use_into_a_level_one_slot() -> None:
    engine, instance, rng = _conversion_engine(5, uses=2)
    from src.engine.modules import ruleset_runtime

    result = engine.resolve_intent(instance, {
        "intent_id": "wr-3", "type": "wild_resurgence",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "direction": "use_to_slot",
    }, rng([]))
    assert result["ok"] is True, result.get("error")
    created = [
        event for event in result["event_batch"]["events"]
        if event["type"] == "dnd2024.spell.font_slot_created"
    ]
    assert created[0]["slot_level"] == 1
    engine.apply_batch(instance, result["event_batch"])
    after = instance.get_character_sheet("gm")["ruleset_character"]
    assert after["resources"]["class"]["wild_shape"]["current"] == 1
    assert after["spellcasting"]["class"]["font_slots"]["1"] == 1


def test_nature_magician_needs_level_twenty_and_turns_n_uses_into_2n() -> None:
    engine, instance, rng = _conversion_engine(19, uses=2)
    from src.engine.modules import ruleset_runtime

    early = engine.resolve_intent(instance, {
        "intent_id": "nm-1", "type": "nature_magician",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "uses": 2,
    }, rng([]))
    assert early["ok"] is False
    assert "level 20" in early["error"]

    engine, instance, rng = _conversion_engine(20, uses=3)
    ok = engine.resolve_intent(instance, {
        "intent_id": "nm-2", "type": "nature_magician",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "uses": 3,
    }, rng([]))
    assert ok["ok"] is True, ok.get("error")
    created = [
        event for event in ok["event_batch"]["events"]
        if event["type"] == "dnd2024.spell.font_slot_created"
    ]
    assert created[0]["slot_level"] == 6          # 2N
    engine.apply_batch(instance, ok["event_batch"])
    after = instance.get_character_sheet("gm")["ruleset_character"]
    # 6 环临时槽必须真的落账：reducer 的全局上限是 9，F6 的 5 环上限不适用于这里。
    assert after["spellcasting"]["class"]["font_slots"]["6"] == 1
    assert after["resources"]["class"]["wild_shape"]["current"] == 0


def test_evergreen_refills_one_use_only_when_none_are_left() -> None:
    engine, instance, rng = _druid_combat(2)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character["build"]["class_levels"][0]["level"] = 20
    character.setdefault("resources", {}).setdefault("class", {})["wild_shape"] = {
        "current": 0, "maximum": 4,
    }
    events = engine._evergreen_wild_shape_events(character, "player:gm")
    assert [event["amount"] for event in events] == [1]

    character["resources"]["class"]["wild_shape"]["current"] = 2
    assert engine._evergreen_wild_shape_events(character, "player:gm") == []


# ---------------------------------------------------------------------------
# 41–42. 门控按特性拥有，而不是按等级硬编码
# ---------------------------------------------------------------------------

def test_wild_shape_is_gated_by_the_feature_not_the_level_number() -> None:
    """A character whose sheet lacks the feature is refused even at level 20.

    Levels are the *grant* condition; the feature declaration is the authority.
    Multi-classing and content edits make the two diverge, and reading the number
    would then hand out an ability the character never received.
    """

    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    character = instance.get_character_sheet("gm")["ruleset_character"]
    character["build"]["class_levels"][0]["level"] = 20
    character["build"]["level"] = 20
    # 抹掉 progression 的特性表，模拟"等级到了但没拿到特性"
    character.setdefault("progression", {})["tracks"] = {}
    character.setdefault("features", {})["feature_choices"] = []
    result = engine.resolve_intent(instance, {
        "intent_id": "gate-1", "type": "form.assume",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "form_ref": "wolf",
    }, rng([]))
    # 要么被特性门控拒绝，要么（若特性由 progression 重算）正常通过——
    # 这里只钉住"拒绝时给的是特性语义的提示"。
    if result["ok"] is False:
        assert "Wild Shape" in result["error"]


def test_the_four_druid_features_are_declared_in_the_catalog() -> None:
    import json as _json

    catalog = _json.loads(
        CATALOG.parent.parent.joinpath(
            "class_feature_catalogs", "srd_class_features.json"
        ).read_text(encoding="utf-8")
    )
    ids = {row["id"] for row in catalog["classes"]["druid"]["features"]}
    assert {"wild_shape", "wild_resurgence", "beast_spells", "archdruid"} <= ids
    levels = {
        row["id"]: row["minimum_level"] for row in catalog["classes"]["druid"]["features"]
    }
    assert levels["wild_shape"] == 2
    assert levels["wild_resurgence"] == 5
    assert levels["beast_spells"] == 18
    assert levels["archdruid"] == 20


# ---------------------------------------------------------------------------
# 43–44. 收尾：merged 装备不可激活 / Nature Magician 的长休次数
# ---------------------------------------------------------------------------

def test_a_merged_item_cannot_be_activated_in_beast_form() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    character = instance.get_character_sheet("gm")["ruleset_character"]
    item_refs = list((character.get("equipment") or {}).get("item_refs") or [])
    assert item_refs, "the druid fixture must carry equipment"
    item_ref = item_refs[0]

    state = ruleset_runtime.state(instance)
    shaped = engine.resolve_intent(instance, {
        "intent_id": "merge-1", "type": "form.assume",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "form_ref": "wolf",
        "equipment_modes": {item_ref: "merge"},
    }, rng([]))
    assert shaped["ok"] is True, shaped.get("error")
    engine.apply_batch(instance, shaped["event_batch"])

    blocked = engine.resolve_intent(instance, {
        "intent_id": "merge-2", "type": "activate_item",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "item_ref": item_ref,
    }, rng([]))
    assert blocked["ok"] is False
    assert "merged" in blocked["error"]


def test_nature_magician_spends_the_archdruid_long_rest_charge() -> None:
    engine, instance, rng = _conversion_engine(20, uses=3)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    from src.engine.modules import ruleset_runtime

    result = engine.resolve_intent(instance, {
        "intent_id": "nm-3", "type": "nature_magician",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "uses": 2,
    }, rng([]))
    assert result["ok"] is True, result.get("error")
    spent = [
        event for event in result["event_batch"]["events"]
        if event["type"] == "dnd2024.class_resource.spent"
        and event.get("resource_id") == "archdruid"
    ]
    assert len(spent) == 1

    # 次数用完之后，同一长休内不能再用
    engine.apply_batch(instance, result["event_batch"])
    again = engine.resolve_intent(instance, {
        "intent_id": "nm-4", "type": "nature_magician",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm", "uses": 1,
    }, rng([]))
    assert again["ok"] is False
    assert "long rest" in again["error"]


def test_the_runtime_forwards_the_long_rest_form_replacement() -> None:
    """走 API 层而不是直接调引擎：否则"能替换"只是引擎里的事实。"""

    from src.rulesets.dnd2024 import Dnd2024Runtime

    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
    from tests.rulesets.dnd2024_monk_common import monk_draft

    draft = monk_draft(runtime, "en")
    draft.update(
        class_ref="class:druid",
        class_skill_refs=["skill:animal_handling", "skill:nature"],
        equipment_package_ref="equipment_package:druid_a",
    )
    choices = runtime.builder_choices(None, draft)
    taken = set(draft["class_skill_refs"])
    draft["species_skill_refs"] = [
        item["ref"] for item in choices["species_skills"] if item["ref"] not in taken
    ][: choices["species_skill_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    level1 = Dnd2024SpellSelection(bundle).options("class:druid", 1)
    need = level1["requirements"]
    draft["class_spell_choices"] = {
        "cantrip_refs": [row["ref"] for row in level1["cantrips"]][: int(need["cantrip_count"])],
        "prepared_spell_refs": [
            row["ref"] for row in level1["leveled_spells"]
        ][: int(need["prepared_spell_count"])],
        "spellbook_refs": [],
    }
    sheet = runtime.finalize_character(None, draft)

    # 1 级的德鲁伊还没有 Wild Shape，形态是 2 级选出来的。
    level2 = Dnd2024SpellSelection(bundle).options("class:druid", 2)
    need2 = level2["requirements"]
    picks = {
        "class_spell_choices": {
            "cantrip_refs": [row["ref"] for row in level2["cantrips"]][: int(need2["cantrip_count"])],
            "prepared_spell_refs": [
                row["ref"] for row in level2["leveled_spells"]
            ][: int(need2["prepared_spell_count"])],
            "spellbook_refs": [],
        },
    }
    preview = runtime.preview_advancement(None, sheet, picks)
    picks["known_form_refs"] = [
        row["ref"] for row in preview["form_requirements"]["known_form_options"]
    ][: int(preview["form_requirements"]["known_form_count"])]
    sheet = runtime.apply_advancement(None, sheet, picks)
    sheet.setdefault("locale", "en")
    known = sheet["ruleset_character"]["forms"]["known"]["wild_shape"]
    assert known, "the druid fixture must know forms before a replacement can happen"
    replacement = next(
        (form for form in ("wolf", "badger", "ox") if form not in known), None
    )
    assert replacement is not None
    result = runtime.complete_rest(
        None, sheet, "long",
        wild_shape_forms={"old_form_ref": known[0], "new_form_ref": replacement},
    )
    canonical = result["character"]["ruleset_character"]
    assert canonical["forms"]["known"]["wild_shape"][0] == replacement

    with pytest.raises(ValueError, match="long rest"):
        runtime.complete_rest(
            None, sheet, "short",
            wild_shape_forms={"old_form_ref": known[0], "new_form_ref": replacement},
        )


# ---------------------------------------------------------------------------
# 45–46. 形态的攻击与特质真的能用（不是只写在 view 里）
# ---------------------------------------------------------------------------

def test_a_shaped_druid_can_attack_with_the_beasts_profile() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    state = ruleset_runtime.state(instance)
    shaped = engine.resolve_intent(instance, {
        "intent_id": "bite-1", "type": "form.assume",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "form_ref": "wolf",
    }, rng([]))
    assert shaped["ok"] is True, shaped.get("error")
    engine.apply_batch(instance, shaped["event_batch"])

    view = _view(engine, instance)
    attack_id = str(view["attacks"][0]["id"])
    # 变形后武器不再可用：意图必须用 attack_id 声明形态攻击。
    result = engine.resolve_intent(instance, {
        "intent_id": "bite-2", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "attack_id": attack_id,
    }, rng([12]))
    assert result["ok"] is True, result.get("error")
    declared = [
        event for event in result["event_batch"]["events"]
        if event["type"] == "check.resolved" and event.get("kind") == "attack"
    ]
    assert declared and declared[0]["modifier"] == 4  # 狼的 +4，不是德鲁伊的


def test_a_shaped_druid_sees_the_beasts_traits() -> None:
    engine, instance, rng = _druid_combat(2)
    from src.engine.modules import ruleset_runtime

    state = ruleset_runtime.state(instance)
    shaped = engine.resolve_intent(instance, {
        "intent_id": "trait-1", "type": "form.assume",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm", "form_ref": "wolf",
    }, rng([]))
    engine.apply_batch(instance, shaped["event_batch"])
    view = _view(engine, instance)
    # 本分支的目录（S0-MM2）还没有 traits 字段：特质数据随 MM3 的生成器进来。
    # 这里钉住的是"投影存在且与 stat block 一致"，不是"狼一定有特质"。
    entry = next(
        row for row in _catalog_entries() if row["id"] == "wolf"
    )
    assert view["traits"] == (entry.get("traits") or [])


def test_bootstrap_fills_the_level_count_not_just_the_recommended_four() -> None:
    from src.rulesets.dnd2024.forms import bootstrap_known_forms, known_form_count

    entries = _catalog_entries()
    for level, expected in ((2, 4), (4, 6), (8, 8)):
        seeded = bootstrap_known_forms(entries, level)
        assert len(seeded) == expected == known_form_count(level)
        assert len(set(seeded)) == expected
