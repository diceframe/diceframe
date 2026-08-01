"""规则系统安全表达式求值测试。"""

import json
import re
from pathlib import Path

import pytest
from src.commands.dice_resolver import DiceResolver
from src.engine.character_utils import initial_special_stat_value, make_default_character
from src.engine.game_instance import GameInstance
from src.llm.protocol import KNOWN_PROTOCOL_TAGS
from src.rules.rule_system import RuleSystem, _safe_eval, list_available_rules


def test_builtin_rule_appendices_only_reference_supported_protocol_tags():
    unknown: dict[str, list[str]] = {}
    legacy_aliases: list[str] = []
    for path in Path("templates/rules").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        appendix = str(data.get("gm_prompt_appendix") or "")
        tag_names = set(re.findall(r"\b([A-Z][A-Z_]{1,31})\s*[:：]", appendix))
        unsupported = sorted(tag_names - KNOWN_PROTOCOL_TAGS)
        if unsupported:
            unknown[path.name] = unsupported
        if "SANCheck:" in appendix:
            legacy_aliases.append(path.name)

    assert unknown == {}
    assert legacy_aliases == []


class TestSafeEval:
    def test_simple_addition(self):
        assert _safe_eval("10 + 5", {}) == 15

    def test_variable_substitution(self):
        assert _safe_eval("10 + con * 5", {"con": 12}) == 70

    def test_complex_expression(self):
        assert _safe_eval("con * 5 + str_mod * 2", {"con": 14, "str_mod": 3}) == 76

    def test_floor_division(self):
        assert _safe_eval("con // 2", {"con": 15}) == 7

    def test_max_function(self):
        assert _safe_eval("max(10, con)", {"con": 15}) == 15

    def test_min_function(self):
        assert _safe_eval("min(10, dex - 2)", {"dex": 14}) == 10

    def test_abs_function(self):
        assert _safe_eval("abs(-10)", {}) == 10

    def test_negative_number(self):
        assert _safe_eval("1 - 5", {}) == -4

    def test_unary_negation(self):
        assert _safe_eval("-con", {"con": 10}) == -10

    def test_invalid_operator(self):
        with pytest.raises(ValueError):
            _safe_eval("10 ** 2", {})

    def test_invalid_function(self):
        with pytest.raises(ValueError):
            _safe_eval("eval('1+1')", {})

    def test_missing_variable(self):
        assert _safe_eval("con * 5", {"str": 10}) == 0

    def test_dnd5e_formula(self):
        assert _safe_eval("con * 5 + 10", {"con": 12}) == 70

    def test_coc_formula(self):
        assert _safe_eval("(con + siz) // 5", {"con": 50, "siz": 60}) == 22


class TestRuleInheritance:
    def test_extends_merges_base_template(self, tmp_path):
        (tmp_path / "base.json").write_text(
            """{
              "rule_id": "base",
              "rule_name": "Base",
              "abstract": true,
              "dice_system": "d20",
              "combat_model": "hp_based",
              "item_categories": {"equipment": ["剑"], "misc": ["地图"]},
              "max_skills": 3
            }""",
            encoding="utf-8",
        )
        (tmp_path / "child.json").write_text(
            """{
              "extends": "base",
              "rule_id": "child",
              "rule_name": "Child",
              "item_categories": {"consumable": ["药水"]},
              "max_skills": 4
            }""",
            encoding="utf-8",
        )

        rule = RuleSystem.load(tmp_path / "child.json")

        assert rule.rule_id == "child"
        assert rule.dice_system == "d20"
        assert rule.combat_model == "hp_based"
        assert rule.max_skills == 4
        assert rule.item_categories == {
            "equipment": ["剑"],
            "misc": ["地图"],
            "consumable": ["药水"],
        }

    def test_loaded_base_d20_mechanics(self):
        rule = RuleSystem.load("templates/rules/freeform_fantasy.json")

        assert rule.mechanics == "freeform_d20_core"
        assert rule.ruleset_level == "assisted"
        assert rule.dc_for_difficulty("标准") == 15
        assert rule.dc_for_difficulty("硬核") == 17
        assert rule.skill_bonus(20) == 1
        assert rule.skill_bonus(80) == 4

    def test_coc_skill_points_spend_above_base(self):
        rule = RuleSystem.load("templates/rules/freeform_coc.json")

        assert rule.skill_point_spend_mode == "above_base"
        errors = rule.validate_character({
            "attributes": {a["key"]: a.get("min", 3) for a in rule.attributes},
            "skills": [{"name": "侦查", "value": 85}, {"name": "医学", "value": 85}, {"name": "考古学", "value": 50}],
        })

        assert not any("技能点" in e for e in errors)

    def test_coc_hp_uses_percentile_con_siz_formula(self):
        rule = RuleSystem.load("templates/rules/freeform_coc.json")

        assert rule.calculate_hp({"con": 50, "siz": 60}, "") == 11

    def test_coc_initial_sanity_uses_percentile_pow(self):
        assert initial_special_stat_value({"key": "sanity", "max": 99}, {"pow": 55}) == 55
        assert initial_special_stat_value({"key": "sanity", "max": 99}, {"pow": 11}) == 55

    def test_special_stat_initial_value_override(self):
        assert initial_special_stat_value({"key": "heat", "max": 10, "initial": 0}, {}) == 0
        assert initial_special_stat_value({"key": "humanity", "max": 100, "initial": 120}, {}) == 100

    def test_cyberpunk_lite_has_flavor_resources_without_cyberware_item_conflict(self):
        rule = RuleSystem.load("templates/rules/freeform_cyberpunk.json")
        keys = [s["key"] for s in rule.special_stats]

        assert keys == ["humanity", "cyberware_load", "heat"]
        character = make_default_character("Runner", "freeform_cyberpunk")
        assert character["humanity"] == 100
        assert character["cyberware_load"] == 0
        assert character["heat"] == 0
        assert "cyberware" not in character

    def test_list_available_rules_hides_abstract_base(self, tmp_path):
        (tmp_path / "base_d20.json").write_text(
            """{"rule_id": "base_d20", "rule_name": "Base", "abstract": true}""",
            encoding="utf-8",
        )
        (tmp_path / "freeform.json").write_text(
            """{"extends": "base_d20", "rule_id": "freeform", "rule_name": "Freeform"}""",
            encoding="utf-8",
        )

        rule_ids = {r["rule_id"] for r in list_available_rules(tmp_path)}

        assert rule_ids == {"freeform"}

    def test_extends_cycle_is_rejected(self, tmp_path):
        (tmp_path / "a.json").write_text(
            """{"extends": "b", "rule_id": "a"}""",
            encoding="utf-8",
        )
        (tmp_path / "b.json").write_text(
            """{"extends": "a", "rule_id": "b"}""",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="继承出现循环"):
            RuleSystem.load(tmp_path / "a.json")


def test_dnd5e_rule_check_uses_advantage_from_action_text(monkeypatch):
    rolls = iter([5, 17])
    monkeypatch.setattr("random.randint", lambda a, b: next(rolls))
    rule = RuleSystem.load("templates/rules/dnd5e.json")
    instance = GameInstance(("web", "test", "bot"))
    instance.players["u1"] = {
        "character_name": "艾琳",
        "character_sheet": {
            "level": 1,
            "attributes": {"dex": 14, "str": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "skills": [{"name": "潜行", "value": 0}],
        },
    }
    instance.action_queue = [{
        "user_id": "u1",
        "text": "我借着夜色有优势潜行绕后",
        "selected_attribute": "dex",
        "selected_skill": "潜行",
    }]

    text = DiceResolver().roll_rule_check(instance, "我借着夜色有优势潜行绕后", rule)

    assert "d20优势=[5, 17] 取 17" in text
    assert instance.last_check["advantage_mode"] == "advantage"
    assert instance.last_check["rolls"] == [5, 17]
    assert instance.last_check["roll"] == 17


def test_dnd5e_rule_check_cancels_advantage_and_disadvantage(monkeypatch):
    monkeypatch.setattr("random.randint", lambda a, b: 11)
    rule = RuleSystem.load("templates/rules/dnd5e.json")
    instance = GameInstance(("web", "test", "bot"))
    instance.players["u1"] = {
        "character_name": "艾琳",
        "character_sheet": {
            "level": 1,
            "attributes": {"dex": 14, "str": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "skills": [],
        },
    }
    instance.action_queue = [{
        "user_id": "u1",
        "text": "我有优势但也在黑暗中不利地射击",
        "selected_attribute": "dex",
    }]

    text = DiceResolver().roll_rule_check(instance, "我有优势但也在黑暗中不利地射击", rule)

    assert "优势与劣势同时存在" in text
    assert instance.last_check["advantage_mode"] == ""
    assert instance.last_check["rolls"] == [11]


def test_rule_abstraction_defaults_from_legacy_fields():
    rule = RuleSystem.load("templates/rules/freeform_fantasy.json")

    assert rule.conflict_model == {"type": rule.combat_model}
    assert rule.currency_system["units"][0]["name"] == rule.currency
    assert rule.resource_schema[0]["key"] == "hp"
    assert rule.identity_schema[0]["legacy_field"] == "race"
    assert rule.progression_schema["type"] == rule.growth_system
    assert rule.ui_schema["primary_resources"] == ["hp"]


def test_rule_abstraction_reads_explicit_schema(tmp_path):
    (tmp_path / "schema_rule.json").write_text(
        """{
          "rule_id": "schema_rule",
          "rule_name": "Schema Rule",
          "combat_model": "legacy",
          "conflict_model": {"type": "clock"},
          "currency_system": {"base_unit": "credit", "units": [{"id": "credit", "name": "Credit", "rate": 1}]},
          "resource_schema": [{"key": "stress", "label": "Stress", "min": 0, "max": 6}],
          "identity_schema": [{"key": "role", "label": "Role", "type": "text"}],
          "progression_schema": {"type": "milestone"},
          "ui_schema": {"primary_resources": ["stress"], "show_level": false}
        }""",
        encoding="utf-8",
    )

    rule = RuleSystem.load(tmp_path / "schema_rule.json")

    assert rule.conflict_model["type"] == "clock"
    assert rule.currency_system["base_unit"] == "credit"
    assert rule.resource_schema[0]["key"] == "stress"
    assert rule.identity_schema[0]["key"] == "role"
    assert rule.progression_schema["type"] == "milestone"
    assert rule.ui_schema["primary_resources"] == ["stress"]
