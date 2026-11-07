"""P1-B：声明建卡边界的规则（CoC 7e）在服务端入场时执行 validate_character。"""

from pathlib import Path

import pytest

from src.rules.loader import RuleBundleLoader
from src.rules.rule_system import RuleSystem
from src.webui.services.characters import creation_bounds_violation

COC_ATTRIBUTES = {
    "str": 50, "con": 50, "siz": 50, "dex": 50,
    "app": 50, "int": 50, "pow": 50, "edu": 50,
}


def coc_rule() -> RuleSystem:
    return RuleSystem(RuleBundleLoader().load_rule("templates/rules", "freeform_coc", "zh-CN"))


def sheet(skills: list[dict]) -> dict:
    return {"attributes": dict(COC_ATTRIBUTES), "skills": skills, "class": "", "equipment": []}


def test_balanced_sheet_passes_the_server_side_boundary() -> None:
    rule = coc_rule()
    assert rule.template["character_creation"]["clamp_to_rule_bounds"] is True
    assert creation_bounds_violation(rule, sheet([{"name": "侦查", "value": 70}])) is None


def test_over_budget_skill_points_are_rejected() -> None:
    rule = coc_rule()
    budget = rule.skill_point_budget(COC_ATTRIBUTES)
    # above_base 计点：把一个技能顶到上限，再把剩下的点数堆到第二个技能上，总额超预算
    overspending = [
        {"name": "侦查", "value": rule.max_skill_value},
        {"name": "图书馆使用", "value": rule.max_skill_value},
        {"name": "聆听", "value": rule.max_skill_value},
        {"name": "潜行", "value": rule.max_skill_value},
        {"name": "闪避", "value": rule.max_skill_value},
    ]
    rejection = creation_bounds_violation(rule, sheet(overspending))
    assert rejection is not None
    assert rejection["error_code"] == "CHARACTER_SHEET_INVALID"
    assert "技能点" in rejection["error"]
    assert budget > 0


def test_too_many_skills_are_rejected() -> None:
    rule = coc_rule()
    too_many = [{"name": f"技能{index}", "value": 1} for index in range(rule.max_skills + 1)]
    rejection = creation_bounds_violation(rule, sheet(too_many))
    assert rejection is not None
    assert "技能数量" in rejection["error"]


def test_rules_without_declared_bounds_are_untouched() -> None:
    # 未声明 clamp_to_rule_bounds 的规则不进入该边界（调用方用 clamp 开关控制）
    assert creation_bounds_violation(None, sheet([])) is None


@pytest.mark.parametrize("skill_value", [0, -5])
def test_nonsense_values_do_not_crash_the_boundary(skill_value: int) -> None:
    rule = coc_rule()
    result = creation_bounds_violation(rule, sheet([{"name": "侦查", "value": skill_value}]))
    # 值本身是否合法由规则决定；这里只要求不抛异常、返回结构化结果
    assert result is None or result["error_code"] == "CHARACTER_SHEET_INVALID"


def test_neutral_partial_attribute_sheets_keep_the_legacy_path() -> None:
    """中性冒险/旧卡入场可以只带部分属性（服务端 clamp 兜底）。

    validate_character 把缺失属性当 0，若不豁免会把合法形态误判为非法
    （CI 上 freeform_coc 中性冒险入场曾因此被拒：体质 0 不在 [15,90]）。
    """
    rule = coc_rule()
    declared = {str(spec.get("key") or "") for spec in rule.attributes if spec.get("key")}
    assert len(declared) > 1
    partial = {"str": 10} if "str" in declared else {next(iter(declared)): 10}
    assert creation_bounds_violation(rule, {"attributes": partial, "skills": []}) is None
    assert creation_bounds_violation(rule, {"attributes": {}, "skills": []}) is None


def test_complete_attribute_sheet_is_still_validated() -> None:
    # 带齐全部规则属性 = 正式建卡结果：超预算技能照样拒绝，豁免不放走绕过
    rule = coc_rule()
    budget_buster = sheet([
        {"name": "侦查", "value": rule.max_skill_value},
        {"name": "图书馆使用", "value": rule.max_skill_value},
        {"name": "聆听", "value": rule.max_skill_value},
        {"name": "潜行", "value": rule.max_skill_value},
        {"name": "闪避", "value": rule.max_skill_value},
    ])
    rejection = creation_bounds_violation(rule, budget_buster)
    assert rejection is not None
    assert "技能点" in rejection["error"]
