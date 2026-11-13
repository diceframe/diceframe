"""Fabula Ultima 叙事检定：经通用管线委托到 FU 包自己的检定机制。"""

from __future__ import annotations

import string
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.rulesets.fabula_ultima  # noqa: F401  导入即注册 dice_system="fu"
from src.commands.check_planner import _dice_checks_tool, normalize_check_specs
from src.engine.check_mechanics import check_mechanic_for, supported_dice_systems
from src.engine.checks import build_check_request, resolve_check_request, roll_check_request
from src.engine.game_instance import GameInstance
from src.llm.tools import DICE_CHECKS_TOOL
from src.rules.rule_system import RuleSystem
from src.rulesets.fabula_ultima import check_mechanic as fu_mechanic
from src.rulesets.fabula_ultima.i18n import missing_translations

ROOT = Path(__file__).resolve().parents[1]
ATTRIBUTES = {"dex": 10, "ins": 8, "mig": 8, "wlp": 6}


@pytest.fixture(scope="module")
def rule() -> RuleSystem:
    return RuleSystem.load(ROOT / "templates" / "rules" / "fabula_ultima.json")


def _instance(language: str = "zh-CN", **sheet_overrides) -> GameInstance:
    sheet = {"hp": 40, "max_hp": 40, "attributes": dict(ATTRIBUTES), "skills": [], **sheet_overrides}
    return GameInstance(
        game_key=("test", "fu-checks", "bot"),
        language=language,
        players={
            "u1": {"character_name": "阿莉", "character_sheet": sheet},
            "u2": {
                "character_name": "贝克",
                "character_sheet": {"hp": 40, "max_hp": 40, "attributes": dict(ATTRIBUTES), "skills": []},
            },
        },
    )


def _request(**overrides) -> dict:
    return {
        "check_id": "c1", "actor_uid": "u1", "actor_ref": "player:u1", "dice_system": "fu",
        "attribute": "dex", "attribute_b": "mig", "dice_a": 10, "dice_b": 8, "target": 10,
        "kind": "check", **overrides,
    }


def _resolve(instance, rule, request, rolls, text="我试着翻过围墙"):
    action = {
        "user_id": "u1", "text": text, "check_request": request,
        "dice_value": sum(rolls), "dice_rolls": list(rolls),
    }
    return resolve_check_request(instance, action, rule)


def test_rule_template_loads_and_declares_the_registered_dice_system(rule) -> None:
    assert "fu" in supported_dice_systems()
    assert rule.dice_system == "fu"
    assert rule.check_mechanic["dice"] == "2d_attributes"
    assert rule.attribute_keys == ["dex", "ins", "mig", "wlp"]
    # 裁判说明由通用的 check_planner_appendix 字段承载，不再硬编码进通用规划器。
    assert "attribute_b" in rule.get_check_planner_appendix("zh-CN")


def test_generic_engine_has_no_fu_branches() -> None:
    for relative in ("src/engine/checks.py", "src/commands/check_planner.py", "src/commands/dice_resolver.py"):
        source = (ROOT / relative).read_text(encoding="utf-8-sig")
        assert '"fu"' not in source and "fabula" not in source.lower(), relative


def test_request_fields_pick_the_declared_attack_pair(rule) -> None:
    mechanic = check_mechanic_for("fu")
    fields = mechanic.request_fields(
        rule=rule, sheet_attributes=ATTRIBUTES, attribute="dex", action={}, is_attack=True,
    )
    assert fields == {"attribute_b": "mig", "dice_a": 10, "dice_b": 8}


def test_request_fields_fall_back_to_the_largest_other_die(rule) -> None:
    mechanic = check_mechanic_for("fu")
    fields = mechanic.request_fields(
        rule=rule, sheet_attributes={"dex": 6, "ins": 12, "mig": 8, "wlp": 6}, attribute="dex", action={},
    )
    assert fields == {"attribute_b": "ins", "dice_a": 6, "dice_b": 12}


def test_request_fields_keep_an_explicit_open_check(rule) -> None:
    mechanic = check_mechanic_for("fu")
    fields = mechanic.request_fields(
        rule=rule, sheet_attributes=ATTRIBUTES, attribute="ins", action={},
        planned={"attribute_b": "ins"}, resolve_attribute=lambda value: str(value),
    )
    # 开放检定：同一属性两次是合法的，显式给出时必须保留。
    assert fields == {"attribute_b": "ins", "dice_a": 8, "dice_b": 8}


def test_keyword_fallback_request_carries_both_dice(rule) -> None:
    instance = _instance()
    action = {"user_id": "u1", "text": "检定", "selected_attribute": "dex"}
    request = build_check_request(instance, action, rule)
    assert request is not None
    assert request["dice_system"] == "fu"
    assert request["dice_a"] == 10 and request["dice_b"] in {6, 8}
    assert request["attribute_b"] in rule.attribute_keys


def test_roll_sums_two_dice_and_reports_high_roll(rule, monkeypatch) -> None:
    faces = iter([6, 6])
    monkeypatch.setattr(fu_mechanic, "roll", lambda _formula: SimpleNamespace(natural=next(faces)))
    rolled = roll_check_request(_request(), rule)
    assert rolled["value"] == 12 and rolled["rolls"] == [6, 6]
    assert rolled["critical"] is True and rolled["fumble"] is False
    assert rolled["high_roll"] == 6


def test_double_ones_are_a_fumble(rule, monkeypatch) -> None:
    monkeypatch.setattr(fu_mechanic, "roll", lambda _formula: SimpleNamespace(natural=1))
    rolled = roll_check_request(_request(), rule)
    assert rolled["fumble"] is True and rolled["critical"] is False


@pytest.mark.parametrize(
    ("rolls", "value"),
    [([11, 3], 14), ([4, 9], 13), ([4, 5], 10), ([4], 4), ([4, 5, 1], 10), ([0, 5], 5)],
)
def test_client_supplied_rolls_must_match_the_request(rule, rolls, value) -> None:
    action = {"user_id": "u1", "check_request": _request(), "dice_value": value, "dice_rolls": rolls}
    with pytest.raises(ValueError):
        resolve_check_request(_instance(), action, rule)


def test_resolution_compares_the_total_with_the_difficulty(rule) -> None:
    success = _resolve(_instance(), rule, _request(target=10), [7, 5])
    assert success["verdict"] == "成功" and success["total"] == 12 and success["dc"] == 10
    assert success["dice_pair"] == [7, 5] and success["high_roll"] == 7
    failure = _resolve(_instance(), rule, _request(target=13), [7, 5])
    assert failure["verdict"] == "失败"


def test_situational_difficulty_is_capped_by_the_rule(rule) -> None:
    result = _resolve(_instance(), rule, _request(target=99), [7, 5])
    assert result["dc"] == rule.template["max_check_dc"]


def test_pending_opportunity_bonus_is_consumed_once(rule) -> None:
    instance = _instance(pending_check_bonus=4, pending_check_bonus_reason="机会-优势")
    first = _resolve(instance, rule, _request(target=10), [3, 4])
    assert first["opportunity_bonus"] == 4 and first["total"] == 11 and first["verdict"] == "成功"
    sheet = instance.get_character_sheet("u1")
    assert sheet["pending_check_bonus"] == 0 and "pending_check_bonus_reason" not in sheet
    second = _resolve(instance, rule, _request(check_id="c2", target=10), [3, 4])
    assert second["opportunity_bonus"] == 0 and second["verdict"] == "失败"


def test_critical_success_offers_opportunities_in_the_game_language(rule) -> None:
    chinese = _resolve(_instance("zh-CN"), rule, _request(), [6, 6])
    english = _resolve(_instance("en"), rule, _request(), [6, 6])
    assert chinese["verdict"] == english["verdict"] == "大成功"
    assert len(chinese["opportunity"]) == len(english["opportunity"]) == len(fu_mechanic.OPPORTUNITY_KEYS)
    assert chinese["opportunity"][0].startswith("优势")
    assert english["opportunity"][0].startswith("Advantage")
    assert chinese["fumble_opportunity"] == []


def test_default_label_follows_the_game_language(rule) -> None:
    chinese = _resolve(_instance("zh-CN"), rule, _request(), [3, 4])
    english = _resolve(_instance("en"), rule, _request(), [3, 4])
    assert chinese["label"].endswith("检定")
    assert english["label"].endswith("Check")


def test_attack_uses_the_target_defense_from_server_state(rule) -> None:
    instance = _instance()
    instance.npcs["wolf"] = {"name": "灰狼", "hp": 20, "max_hp": 20, "attributes": {"dex": 8}}
    request = _request(kind="attack", opponent="npc:wolf", target=20)
    result = _resolve(instance, rule, request, [5, 4], text="我攻击灰狼")
    # 物防 = 目标敏捷骰面（+护甲），不采用裁判给的情境 DL。
    assert result["dc"] == 8 and result["target_source"] == "server_armor_class"
    assert result["verdict"] == "成功"


def test_attack_that_only_mentions_an_ally_does_not_target_them(rule) -> None:
    instance = _instance()
    request = _request(kind="attack", opponent="u2", target=13)
    result = _resolve(instance, rule, request, [5, 4], text="贝克小心！我朝那团黑影挥剑")
    assert result["opponent"] == "" and result["target_source"] == "request_dc"
    assert result["dc"] == 13


def test_planner_specs_become_fu_requests(rule) -> None:
    instance = _instance()
    instance.action_queue.append({"user_id": "u1", "text": "我撬开锈住的铁门"})
    planned, errors = normalize_check_specs(
        instance, rule,
        [{"player": "u1", "attribute": "mig", "attribute_b": "mig", "target": 25, "modifier": 2}],
    )
    assert errors == []
    request = planned[0][1]
    assert request["dice_system"] == "fu"
    assert (request["attribute"], request["attribute_b"]) == ("mig", "mig")
    assert (request["dice_a"], request["dice_b"]) == (8, 8)
    assert request["target"] <= rule.template["max_check_dc"]


def test_planner_tool_gains_the_second_attribute_only_for_this_dice_system(rule) -> None:
    properties = _dice_checks_tool(rule)["function"]["parameters"]["properties"]["checks"]["items"]["properties"]
    assert "attribute_b" in properties
    generic = DICE_CHECKS_TOOL["function"]["parameters"]["properties"]["checks"]["items"]["properties"]
    assert "attribute_b" not in generic
    assert _dice_checks_tool(None) is DICE_CHECKS_TOOL


def test_manual_single_die_roll_is_refused_in_the_request_language() -> None:
    mechanic = check_mechanic_for("fu")
    chinese = mechanic.manual_roll_refusal("zh-CN")
    english = mechanic.manual_roll_refusal("en")
    assert chinese["code"] == english["code"] == "FU_CHECK_NEEDS_ATTRIBUTES"
    assert chinese["error"] != english["error"]
    assert "Fabula Ultima" in english["error"]


def test_result_prompt_states_the_server_resolved_outcome(rule) -> None:
    instance = _instance("en")
    check = _resolve(instance, rule, _request(target=10), [7, 5])
    prompt = check_mechanic_for("fu").result_prompt(instance, check)
    assert "d10+d8 = 7+5 = 12" in prompt and "DL 10" in prompt and "Success" in prompt


def test_message_table_is_complete_and_consistent() -> None:
    assert missing_translations(fu_mechanic.MESSAGES) == []
    for key, variants in fu_mechanic.MESSAGES.items():
        fields = {
            language: {name for _, name, _, _ in string.Formatter().parse(text) if name}
            for language, text in variants.items()
        }
        assert fields["zh-CN"] == fields["en"], key
