"""P1-C：百分制规则（CoC 7e）不使用 d20 属性伤害修正。"""

from pathlib import Path

from src.engine.combat import attribute_damage_modifier, resolve_attack
from src.rules.loader import RuleBundleLoader
from src.rules.rule_system import RuleSystem

DND_RULE = RuleSystem(RuleBundleLoader().load_rule("templates/rules", "dnd5e", "zh-CN"))
COC_RULE = RuleSystem(RuleBundleLoader().load_rule("templates/rules", "freeform_coc", "zh-CN"))


def _check(**overrides) -> dict:
    base = {
        "check_id": "attack-1", "actor_uid": "a", "actor_name": "调查员",
        "kind": "attack", "opponent": "npc:cultist", "dice": "d100",
        "roll": 30, "rolls": [30], "total": 30, "verdict": "成功",
        "is_critical": False, "is_fumble": False,
    }
    base.update(overrides)
    return base


def test_d20_rules_keep_their_attribute_bonus() -> None:
    # D&D：STR 16 → +3
    assert attribute_damage_modifier(16, DND_RULE) == 3


def test_percentile_rules_do_not_scale_attributes_into_damage() -> None:
    # CoC：STR 70 曾经被算成 +30，lethal 模型再翻倍成 +60
    assert attribute_damage_modifier(70, COC_RULE) == 0
    assert attribute_damage_modifier(15, COC_RULE) == 0


def test_coc_attack_damage_is_weapon_only() -> None:
    target = {"character_name": "邪教徒", "hp": 12, "max_hp": 12, "armor_class": 10}
    result = resolve_attack(
        "调查员",
        target,
        {"name": "手枪", "damage": 6},
        attr_value=70,
        combat_model="hp_based",
        check_result=_check(),
        rule=COC_RULE,
    )
    # 武器 6 点，不含 d20 属性修正
    assert result.damage == 6
    assert result.target_hp_after == 6
