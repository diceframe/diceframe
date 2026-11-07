"""CoC 7e 理智检定：损失写法、成功等级结算、旁观者、公开结果与相关投影。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.commands.madness_tracker import MadnessTracker
from src.commands.player_state_applier import PlayerStateApplier
from src.commands.state_recap import build_state_change_messages, snapshot_public_player_state
from src.engine import dice_rules_coc
from src.engine.dice_rng import DiceResult
from src.engine.dice_rules_coc import SanityLoss, parse_sanity_loss, resolve_sanity_check
from src.engine.game_instance import GameInstance
from src.engine.legacy_game_projection import project_legacy_character_sheet
from src.rules.rule_system import RuleSystem

ROOT = Path(__file__).resolve().parents[1]


def _fix_d100(monkeypatch, value: int, verdict: str) -> None:
    monkeypatch.setattr(
        dice_rules_coc, "check_coc",
        lambda threshold: (DiceResult(formula="d100", rolls=[value], modifier=0, total=value, natural=value), verdict),
    )


def _fix_loss_roll(monkeypatch, total: int) -> list[str]:
    seen: list[str] = []

    def fake_roll(formula: str):
        seen.append(formula)
        return SimpleNamespace(total=total, rolls=[total])

    monkeypatch.setattr(dice_rules_coc, "roll", fake_roll)
    return seen


# ---------- 损失写法 ----------

@pytest.mark.parametrize(("raw", "expected"), [
    ("0/1d6", SanityLoss("0", "1d6", True)),
    ("1/1d4+1", SanityLoss("1", "1d4+1", True)),
    ("1D3 / 1D10", SanityLoss("1d3", "1d10", True)),
    ("0／1d6", SanityLoss("0", "1d6", True)),
    ("1d6", SanityLoss("1d6", "1d6", False)),
    ("3", SanityLoss("3", "3", False)),
])
def test_parse_sanity_loss_accepts_standard_and_legacy_notation(raw, expected):
    assert parse_sanity_loss(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1/2/3", "/1d6", "1d6/", "101", "0/1d6x", "-1d6"])
def test_parse_sanity_loss_rejects_unknown_notation(raw):
    with pytest.raises(ValueError):
        parse_sanity_loss(raw)


def test_loss_text_round_trips():
    assert parse_sanity_loss("0/1d6").text == "0/1d6"
    assert parse_sanity_loss("1d6").text == "1d6"


# ---------- 成功等级结算 ----------

@pytest.mark.parametrize("verdict", ["普通成功", "困难成功", "极难成功", "大成功"])
def test_every_success_level_takes_the_success_loss(monkeypatch, verdict):
    _fix_d100(monkeypatch, 20, verdict)
    seen = _fix_loss_roll(monkeypatch, 4)
    outcome = resolve_sanity_check(55, parse_sanity_loss("1/1d6"))
    assert outcome["loss"] == 1
    assert outcome["verdict"] == verdict
    assert seen == []  # 成功损失是固定值，不掷失败骰


def test_success_loss_can_be_a_die(monkeypatch):
    _fix_d100(monkeypatch, 20, "普通成功")
    seen = _fix_loss_roll(monkeypatch, 2)
    outcome = resolve_sanity_check(55, parse_sanity_loss("1d3/1d10"))
    assert outcome["loss"] == 2
    assert seen == ["1d3"]


def test_failure_rolls_the_failure_loss(monkeypatch):
    _fix_d100(monkeypatch, 80, "失败")
    seen = _fix_loss_roll(monkeypatch, 5)
    outcome = resolve_sanity_check(55, parse_sanity_loss("0/1d6"))
    assert outcome["loss"] == 5
    assert outcome["loss_rolls"] == [5]
    assert seen == ["1d6"]


@pytest.mark.parametrize(("raw", "maximum"), [("0/1d6", 6), ("1/1d4+1", 5), ("1d10", 10), ("0/3", 3)])
def test_fumble_takes_the_maximum_failure_loss(monkeypatch, raw, maximum):
    _fix_d100(monkeypatch, 100, "大失败")
    seen = _fix_loss_roll(monkeypatch, 1)
    outcome = resolve_sanity_check(55, parse_sanity_loss(raw))
    assert outcome["loss"] == maximum
    assert seen == []


def test_legacy_single_die_halves_on_success(monkeypatch):
    _fix_d100(monkeypatch, 30, "困难成功")
    _fix_loss_roll(monkeypatch, 5)
    assert resolve_sanity_check(70, parse_sanity_loss("1d6"))["loss"] == 3


def test_threshold_is_clamped_to_percentile_range(monkeypatch):
    thresholds: list[int] = []

    def fake_check(threshold):
        thresholds.append(threshold)
        return DiceResult(formula="d100", rolls=[50], modifier=0, total=50, natural=50), "失败"

    monkeypatch.setattr(dice_rules_coc, "check_coc", fake_check)
    _fix_loss_roll(monkeypatch, 1)
    resolve_sanity_check(0, parse_sanity_loss("0/1"))
    resolve_sanity_check(120, parse_sanity_loss("0/1"))
    assert thresholds == [1, 99]


# ---------- 应用到角色 ----------

def _instance(*uids: str, sanity: int = 50) -> GameInstance:
    inst = GameInstance(("web", "coc_san", "bot"))
    for uid in uids:
        inst.players[uid] = {
            "character_name": f"调查员{uid}",
            "character_sheet": {"hp": 10, "max_hp": 10, "sanity": sanity, "max_sanity": 99},
        }
    return inst


def test_regular_success_with_zero_success_loss_keeps_sanity(monkeypatch):
    """回归：旧实现判断笼统的「成功」，普通/困难/极难成功都吃满额损失。"""
    _fix_d100(monkeypatch, 20, "普通成功")
    _fix_loss_roll(monkeypatch, 6)
    inst = _instance("p1")
    update = {"p1": {"san_check_loss": "0/1d6"}}
    PlayerStateApplier(MadnessTracker()).apply_players(inst, update)
    assert inst.get_character_sheet("p1")["sanity"] == 50
    assert update["p1"]["san_check_result"]["verdict"] == "普通成功"


def test_unknown_notation_is_not_resolved_as_six(monkeypatch):
    """回归：旧实现解析失败时静默扣 6 点。"""
    monkeypatch.setattr(dice_rules_coc, "check_coc", lambda threshold: pytest.fail("must not roll"))
    inst = _instance("p1")
    before = snapshot_public_player_state(inst)
    data = {"state_update": {"players": {"p1": {"san_check_loss": "看见了深潜者"}}}}
    PlayerStateApplier(MadnessTracker()).apply_players(inst, data["state_update"]["players"])
    assert inst.get_character_sheet("p1")["sanity"] == 50
    assert data["state_update"]["players"]["p1"]["san_check_result"] == {"invalid": True}
    messages = build_state_change_messages(inst, before, data)
    # GM 可能把秘密写进标签：公开消息只说无法识别，不复述原文。
    assert messages == ["【理智检定】调查员p1：损失写法无法识别，未结算"]


def test_bystander_cannot_take_san_or_hp_without_authoritative_presence(monkeypatch):
    """多人局：不在本轮参与者白名单里的旁观者，即使 GM 输出 SAN 标签也不改状态。

    「活着」不等于「在场目睹」。过去靠 instance.alive_players 放行 SAN，GM 输出
    （或被诱导的模型）可以替不在场的人掷 SAN；现在 fail closed，越权目标整条丢弃。
    """

    _fix_d100(monkeypatch, 80, "失败")
    _fix_loss_roll(monkeypatch, 4)
    inst = _instance("actor", "bystander")
    update = {
        "actor": {"san_check_loss": "0/1d6"},
        "bystander": {"san_check_loss": "0/1d6", "hp_change": -5},
    }
    PlayerStateApplier(MadnessTracker()).apply_players(inst, update, allowed_player_uids={"actor"})

    # 行动者照常结算
    assert inst.get_character_sheet("actor")["sanity"] < 50
    # 旁观者既不掉 SAN 也不掉 HP，标签连解析都不该发生
    bystander = inst.get_character_sheet("bystander")
    assert bystander["sanity"] == 50
    assert bystander["hp"] == 10
    assert update["bystander"] == {"san_check_loss": "0/1d6", "hp_change": -5}


def test_dead_or_unknown_targets_never_resolve_a_san_check(monkeypatch):
    """已死亡 / 不存在的 UID 即使出现在标签里也不会被结算。"""

    _fix_d100(monkeypatch, 80, "失败")
    _fix_loss_roll(monkeypatch, 6)
    inst = _instance("actor", "dead")
    inst.players["dead"]["character_sheet"]["deceased"] = True
    update = {
        "actor": {"san_check_loss": "0/1d6"},
        "dead": {"san_check_loss": "0/1d6"},
        "ghost": {"san_check_loss": "0/1d6"},
    }
    PlayerStateApplier(MadnessTracker()).apply_players(
        inst, update, allowed_player_uids={"actor", "dead", "ghost"},
    )

    assert inst.get_character_sheet("dead")["sanity"] == 50
    assert "ghost" not in inst.players
    # 行动者仍然只结算自己的那次
    assert inst.get_character_sheet("actor")["sanity"] < 50


def test_bystander_without_sanity_check_is_still_rejected():
    inst = _instance("actor", "bystander")
    PlayerStateApplier(MadnessTracker()).apply_players(
        inst, {"bystander": {"hp_change": -5}}, allowed_player_uids={"actor"},
    )
    assert inst.get_character_sheet("bystander")["hp"] == 10


def test_dead_bystander_is_not_checked(monkeypatch):
    monkeypatch.setattr(dice_rules_coc, "check_coc", lambda threshold: pytest.fail("must not roll"))
    inst = _instance("actor", "bystander")
    inst.get_character_sheet("bystander").update(deceased=True, hp=0)
    PlayerStateApplier(MadnessTracker()).apply_players(
        inst, {"bystander": {"san_check_loss": "0/1d6"}}, allowed_player_uids={"actor"},
    )
    assert inst.get_character_sheet("bystander")["sanity"] == 50


# ---------- 公开结果 ----------

@pytest.mark.parametrize(("language", "expected"), [
    ("zh-CN", "【理智检定】调查员p1：d100=80 / 理智 50 → 失败，损失 4（0/1d6）"),
    ("en", "[Sanity Check] 调查员p1: d100=80 vs SAN 50 → failure, lost 4 (0/1d6)"),
    ("ja", "【正気度ロール】调查员p1：d100=80 / 正気度 50 → 失敗、喪失 4（0/1d6）"),
])
def test_sanity_check_result_is_published(monkeypatch, language, expected):
    _fix_d100(monkeypatch, 80, "失败")
    _fix_loss_roll(monkeypatch, 4)
    inst = _instance("p1")
    inst.language = language
    before = snapshot_public_player_state(inst)
    data = {"state_update": {"players": {"p1": {"san_check_loss": "0/1d6"}}}}
    PlayerStateApplier(MadnessTracker()).apply_players(inst, data["state_update"]["players"])
    messages = build_state_change_messages(inst, before, data)
    assert messages[0] == expected
    assert len(messages) == 2  # 随后仍有理智 50 → 46 的状态变动


# ---------- GM 视图 ----------

def test_percentile_attributes_do_not_get_d20_modifiers():
    sheet = project_legacy_character_sheet({"attributes": {"str": 60, "int": 75}})
    assert "_modifiers" not in sheet


def test_d20_attributes_keep_their_modifiers():
    sheet = project_legacy_character_sheet({"attributes": {"str": 14, "dex": 9}})
    assert sheet["_modifiers"] == {"str": 2, "dex": -1}


def test_active_madness_is_visible_to_the_gm_view():
    sheet = project_legacy_character_sheet({
        "attributes": {"pow": 50},
        "madness": {"type": "temporary", "remaining_rounds": 2, "symptom": "临时行为异常"},
    })
    assert sheet["_madness"] == {"type": "temporary", "remaining_rounds": 2, "symptom": "临时行为异常"}
    cleared = project_legacy_character_sheet({"attributes": {"pow": 50}, "madness": {"type": None}})
    assert "_madness" not in cleared


# ---------- 规划器与提示词 ----------

def test_d100_planner_context_has_no_d20_dc_table():
    from src.commands.check_planner import _planner_context

    inst = _instance("p1")
    inst.rule_id = "freeform_coc"
    inst.action_queue = [{"user_id": "p1", "text": "我翻看书桌上的信件"}]
    rule = RuleSystem.load(ROOT / "templates/rules/freeform_coc.json")
    ruleset = json.loads(_planner_context(inst, rule))["ruleset"]
    assert ruleset["dice_system"] == "d100"
    assert "dc_table" not in ruleset


@pytest.mark.parametrize("locale", ["zh", "en", "ja", "de", "ru"])
def test_gm_prompt_tag_examples_do_not_grant_luck_or_use_legacy_sanity_loss(locale):
    text = (ROOT / f"prompts/gm_system_{locale}.md").read_text(encoding="utf-8-sig")
    assert "LUCK:web_user:5" not in text
    assert "SAN_CHECK:web_user:0/1d6" in text


@pytest.mark.parametrize("locale", [None, "en", "ja"])
def test_coc_appendix_uses_success_failure_loss_and_present_witnesses(locale):
    path = (ROOT / "templates/rules/locales" / locale / "freeform_coc.json"
            if locale else ROOT / "templates/rules/freeform_coc.json")
    appendix = RuleSystem.load(path).template["gm_prompt_appendix"]
    assert "0/1d6" in appendix
    assert "SAN_CHECK:" in appendix
