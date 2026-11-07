"""CoC 7e mechanics: required success levels, opposed rolls, Luck/Idea rolls,
push and Luck interplay, untrained skills, and 7e character creation math."""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.commands.check_planner import normalize_check_specs
from src.engine import check_decisions, checks as checks_engine
from src.engine.character_utils import roll_attribute_formula, roll_attributes, roll_special_stat_value
from src.engine.dice_rng import DiceResult
from src.engine.dice_rules_coc import apply_required_level, opposed_outcome, required_threshold
from src.engine.game_instance import GameInstance, GameState
from src.engine.modules import checks
from src.rules.rule_system import RuleSystem
from src.webui.services.characters import _clamp_attributes, _normalize_skills

ROOT = Path(__file__).resolve().parents[1]
COC = ROOT / "templates/rules/freeform_coc.json"
COC_EN = ROOT / "templates/rules/locales/en/freeform_coc.json"
DND = ROOT / "templates/rules/dnd2024_srd.json"


def coc() -> RuleSystem:
    return RuleSystem.load(COC)


def coc_game(*, opponent_skills: list | None = None) -> GameInstance:
    inst = GameInstance(game_key=("web", "coc", "bot"), rule_id="freeform_coc")
    inst.players = {
        "p1": {"user_id": "p1", "character_name": "阿岚", "character_sheet": {
            "attributes": {"dex": 70, "int": 60, "edu": 80, "pow": 50},
            "luck": 45,
            "skills": [{"name": "侦查", "value": 60}, {"name": "潜行", "value": 50}],
        }},
        "p2": {"user_id": "p2", "character_name": "白露", "character_sheet": {
            "attributes": {"dex": 40, "int": 70},
            "skills": opponent_skills if opponent_skills is not None else [{"name": "聆听", "value": 70}],
        }},
    }
    inst.action_queue = [{"user_id": "p1", "text": "我试试"}]
    return inst


def plan(inst: GameInstance, rule: RuleSystem, raw: dict) -> tuple[dict, dict]:
    planned, errors = normalize_check_specs(inst, rule, [{"player": "p1", **raw}])
    assert errors == []
    return planned[0]


def resolve(inst: GameInstance, rule: RuleSystem, action: dict, request: dict, roll_value: int) -> dict:
    result = checks_engine.resolve_check_request(
        inst, dict(action, check_request=request, dice_value=roll_value, dice_rolls=[roll_value]), rule,
    )
    assert result is not None
    return result


# ---------- 规则数学 ----------

def test_required_threshold_and_level():
    assert [required_threshold(60, level) for level in ("regular", "hard", "extreme")] == [60, 30, 12]
    assert apply_required_level("普通成功", "hard") == "失败"
    assert apply_required_level("困难成功", "hard") == "困难成功"
    assert apply_required_level("大成功", "extreme") == "大成功"
    assert apply_required_level("大失败", "regular") == "大失败"


@pytest.mark.parametrize(("actor", "actor_value", "opponent", "opponent_value", "expected"), [
    ("困难成功", 50, "普通成功", 80, "win"),
    ("普通成功", 80, "困难成功", 50, "lose"),
    ("普通成功", 60, "普通成功", 50, "win"),
    ("普通成功", 50, "普通成功", 50, "tie"),
    ("失败", 50, "大失败", 50, "tie"),
    ("普通成功", 50, "大失败", 90, "win"),
])
def test_opposed_outcome(actor, actor_value, opponent, opponent_value, expected):
    assert opposed_outcome(actor, actor_value, opponent, opponent_value) == expected


# ---------- 难度档位 ----------

def test_planner_carries_difficulty_and_drops_modifier_for_coc():
    inst = coc_game()
    _action, request = plan(inst, coc(), {"skill": "侦查", "difficulty": "hard", "modifier": -10, "modifier_reason": "黑暗"})
    assert request["difficulty"] == "hard"
    assert request["circumstance_modifier"] == 0
    assert "modifier_disabled_by_rule" in request["planner_notes"]


def test_unknown_difficulty_defaults_to_regular():
    _action, request = plan(coc_game(), coc(), {"skill": "侦查", "difficulty": "impossible"})
    assert request["difficulty"] == "regular"


def test_regular_success_fails_a_hard_check_and_luck_targets_the_hard_threshold():
    inst = coc_game()
    action, request = plan(inst, coc(), {"skill": "侦查", "difficulty": "hard"})
    result = resolve(inst, coc(), action, request, 40)
    assert result["verdict"] == "失败"
    assert result["achieved_verdict"] == "普通成功"
    assert result["required_threshold"] == 30
    assert result["luck_cost"] == 10


def test_hard_success_passes_a_hard_check():
    inst = coc_game()
    action, request = plan(inst, coc(), {"skill": "侦查", "difficulty": "hard"})
    result = resolve(inst, coc(), action, request, 25)
    assert result["verdict"] == "困难成功"
    assert "achieved_verdict" not in result


# ---------- 对抗检定 ----------

def test_opposed_check_rolls_the_opponent_and_compares_levels(monkeypatch):
    inst = coc_game()
    calls: list[int] = []

    def fake_check(threshold):
        calls.append(threshold)
        return DiceResult(formula="d100", rolls=[30], modifier=0, total=30, natural=30), "困难成功"

    monkeypatch.setattr(checks_engine, "check_coc", fake_check)
    action, request = plan(inst, coc(), {"skill": "潜行", "opponent": "p2", "opponent_skill": "聆听"})
    result = resolve(inst, coc(), action, request, 45)  # 潜行 50 → 普通成功；对手 30/70 → 困难成功
    assert calls == [70]
    assert result["opposed"]["outcome"] == "lose"
    assert result["verdict"] == "失败"
    assert result["achieved_verdict"] == "普通成功"
    assert result["luck_cost"] is None
    # 重试同一请求不会重掷对手骰。
    monkeypatch.setattr(checks_engine, "check_coc", Mock(side_effect=AssertionError("rerolled")))
    again = resolve(inst, coc(), action, request, 45)
    assert again["opposed"]["opponent_roll"] == 30


def test_opponent_without_values_falls_back_to_an_ordinary_check(monkeypatch):
    inst = coc_game(opponent_skills=[])
    monkeypatch.setattr(checks_engine, "check_coc", Mock(side_effect=AssertionError("no opposed roll")))
    action, request = plan(inst, coc(), {"skill": "潜行", "opponent": "p2", "opponent_skill": "聆听"})
    result = resolve(inst, coc(), action, request, 45)
    assert "opposed" not in result
    assert result["verdict"] == "普通成功"


# ---------- 孤注一掷与幸运 ----------

def push_game(verdict: str = "失败", **extra) -> GameInstance:
    inst = GameInstance(("web", "push", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.gm_uid = "gm"
    inst.players = {"a": {"character_name": "A", "character_sheet": {"hp": 10, "luck": 50,
                                                                     "skills": [{"name": "Investigate", "value": 60}]}}}
    checks.replace_last_checks(inst, [{
        "check_id": "c1", "actor_uid": "a", "kind": "check", "skill": "Investigate", "dice": "d100",
        "dice_mechanic": "coc_bonus_penalty", "roll": 40, "rolls": [40], "threshold": 60, "verdict": verdict,
        "advantage_mode": "", "luck_spend_available": True, "luck_decision": "pending", **extra,
    }])
    inst.complete_round_check_preparation()
    return inst


def test_fumbles_are_not_pushable_under_coc():
    inst = push_game("大失败", roll=100, rolls=[100])
    check_decisions.prepare(inst, coc())
    assert checks.last_checks(inst)[0].get("push") is None


def test_pushed_reroll_keeps_the_required_level():
    inst = push_game(difficulty="hard", required_threshold=30)
    check_decisions.prepare(inst, coc())

    def change(uid, operation, **kwargs):
        push = checks.last_checks(inst)[0]["push"]
        return check_decisions.change(inst, "c1", uid, operation, run_id=inst.run_id,
                                      round_number=inst.round_number, expected_revision=push["revision"], **kwargs)

    change("a", "request", reason="翻遍整个书房")
    change("gm", "offer", consequence="管家听到动静")
    change("a", "confirm", rng=Mock(return_value=45))
    check = checks.last_checks(inst)[0]
    assert check["verdict"] == "失败"
    assert check["achieved_verdict"] == "普通成功"


@pytest.mark.asyncio
async def test_spending_luck_on_a_hard_check_reaches_hard_success():
    inst = push_game(difficulty="hard", required_threshold=30)
    result = await inst.resolve_luck_decision("c1", "a", True)
    assert result["ok"]
    check = checks.last_checks(inst)[0]
    assert check["verdict"] == "困难成功"
    assert check["luck_spent"] == 10


@pytest.mark.asyncio
async def test_declining_luck_keeps_the_push_offer_open():
    inst = push_game()
    check_decisions.prepare(inst, coc())
    assert (await inst.resolve_luck_decision("c1", "a", False))["ok"]
    assert checks.last_checks(inst)[0]["push"]["status"] == "pending"


# ---------- 幸运检定与灵感 ----------

def test_luck_roll_uses_the_sheet_value_and_never_offers_luck():
    inst = coc_game()
    action, request = plan(inst, coc(), {"attribute": "幸运值"})
    assert (request["attribute"], request["target"], request["label"]) == ("luck", 45, "幸运值检定")
    result = resolve(inst, coc(), action, request, 60)
    assert result["threshold"] == 45 and result["verdict"] == "失败"
    assert result["luck_cost"] is None and result["luck_spend_available"] is False


def test_idea_roll_uses_int_and_cannot_pick_another_skill():
    inst = coc_game()
    _action, request = plan(inst, coc(), {"kind": "idea", "skill": "侦查"})
    assert (request["kind"], request["attribute"], request["skill"], request["target"]) == ("idea", "int", "", 60)
    assert request["label"] == "灵感检定"


def test_idea_kind_is_an_ordinary_check_for_rules_without_it():
    inst = coc_game()
    inst.rule_id = "dnd2024_srd"
    inst.players["p1"]["character_sheet"]["attributes"] = {"int": 12, "wis": 14}
    planned, errors = normalize_check_specs(inst, RuleSystem.load(DND), [
        {"player": "p1", "kind": "idea", "attribute": "int", "target": 12},
    ])
    assert errors == [] and planned[0][1]["kind"] == "check"


# ---------- 没学过的技能 ----------

def test_untrained_skill_rolls_at_its_derived_base():
    inst = coc_game()
    action, request = plan(inst, coc(), {"skill": "闪避"})
    assert request["untrained_skill_value"] == 35
    result = resolve(inst, coc(), action, request, 30)
    assert (result["skill"], result["threshold"], result["verdict"]) == ("闪避", 35, "普通成功")


def test_untrained_localized_skill_name_is_recognized():
    inst = coc_game()
    action, request = plan(inst, RuleSystem.load(COC_EN), {"skill": "Spot Hidden"})
    assert request["target"] == 25


def test_unknown_skill_is_still_rejected():
    planned, errors = normalize_check_specs(coc_game(), coc(), [{"player": "p1", "skill": "篮子编织"}])
    assert planned == [] and errors


# ---------- 7e 建卡 ----------

def test_rule_budget_and_derived_bases_follow_attributes():
    rule = RuleSystem.load(COC_EN)
    attrs = {"dex": 70, "edu": 80, "int": 60}
    assert rule.skill_point_budget(attrs) == 440
    assert rule.skill_point_budget() == 320
    assert rule.skill_base_value_for("Dodge", attrs) == 35
    assert rule.skill_base_value_for("Own Language", attrs) == 80
    assert rule.skill_base_value_for("Dodge") == 25
    assert rule.known_skill_name("spot hidden") == "Spot Hidden"


def test_validator_uses_the_attribute_budget():
    rule = coc()
    sheet = {
        # 预算 = 40×4 + 40×2 = 240；前两项花 60 + 35，再加三项共 300 就超了。
        "attributes": {"str": 50, "con": 50, "siz": 60, "dex": 50, "app": 50, "int": 40, "pow": 50, "edu": 40},
        "skills": [{"name": "侦查", "value": 85}, {"name": "闪避", "value": 60}],
    }
    assert not any("技能点" in error for error in rule.validate_character(sheet))
    sheet["skills"].append({"name": "图书馆使用", "value": 85})
    sheet["skills"].append({"name": "心理学", "value": 85})
    sheet["skills"].append({"name": "聆听", "value": 85})
    assert any("技能点" in error for error in rule.validate_character(sheet))


def test_attribute_formulas_and_luck_follow_7e_dice():
    random.seed(7)
    rolls = [roll_attribute_formula("(2d6+6)*5") for _ in range(200)]
    assert min(rolls) >= 40 and max(rolls) <= 90 and all(value % 5 == 0 for value in rolls)
    assert roll_attribute_formula("2d6+") is None
    luck = [roll_special_stat_value(99) for _ in range(500)]
    assert min(luck) >= 15 and max(luck) <= 90 and all(value % 5 == 0 for value in luck)
    assert sum(1 for value in luck if 40 <= value <= 65) > sum(1 for value in luck if value < 40 or value > 65)
    rolled = roll_attributes(specs=coc().attributes, total=460)
    assert set(rolled) == {"str", "con", "siz", "dex", "app", "int", "pow", "edu"}


def test_join_clamps_attributes_and_skills_to_rule_bounds():
    rule = coc()
    attrs = _clamp_attributes(rule, {"str": 99, "int": 5, "luck": 99})
    assert attrs["str"] == 90 and attrs["int"] == 40 and attrs["luck"] == 99
    skills = _normalize_skills([{"name": "侦查", "value": 99}, "闪避"], rule,
                               attributes={"dex": 70}, clamp=True)
    assert skills == [{"name": "侦查", "value": 85}, {"name": "闪避", "value": 35}]


def test_other_rules_keep_the_old_skill_default():
    rule = SimpleNamespace(skill_base_values={}, skill_point_spend_mode="total_value", max_skill_value=0)
    assert _normalize_skills(["Athletics"], rule) == [{"name": "Athletics", "value": 20}]
