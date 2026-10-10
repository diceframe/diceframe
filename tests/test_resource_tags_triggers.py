"""STAT 规则资源标签与阈值触发器测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.commands.madness_tracker import MadnessTracker
from src.commands.player_state_applier import PlayerStateApplier
from src.commands.resource_triggers import check_resource_triggers
from src.commands.state_recap import build_state_change_messages, snapshot_public_player_state
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance, GameState
from src.rules.rule_system import RuleSystem
from src.engine.modules import round_presentation


def _rule_with_resources(tmp_path: Path) -> RuleSystem:
    template = {
        "rule_id": "test_resource_rule",
        "rule_name": "资源测试规则",
        "dice_system": "d20",
        "attributes": [{"key": "brain", "name": "智商", "min": 3, "max": 18}],
        "special_stats": [
            {"key": "kpi", "name": "KPI", "max": 100, "initial": 42,
             "triggers": [{"at": 100, "notify": "触发『转正』结局或深夜述职会"}]},
            {"key": "days", "name": "剩余天数", "max": 7, "initial": 7,
             "triggers": [{"at": 0, "direction": "down", "notify": "限时耗尽，谜团落幕"}]},
        ],
    }
    path = tmp_path / "test_resource_rule.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    return RuleSystem.load(path)


def _instance(rule: RuleSystem) -> GameInstance:
    instance = GameInstance(("web", "res", "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    instance.rule_id = rule.rule_id
    instance.players["p1"] = {
        "character_name": "小林",
        "character_sheet": {
            "attributes": {"brain": 16},
            "kpi": 42, "max_kpi": 100,
            "days": 7, "max_days": 7,
            "resources": {
                "hp": {"label": "生命", "current": 30, "max": 30, "min": 0},
                "kpi": {"label": "KPI", "current": 42, "max": 100, "min": 0},
                "days": {"label": "剩余天数", "current": 7, "max": 7, "min": 0},
            },
        },
    }
    return instance


def test_stat_tag_parses_into_stat_changes():
    result = parse_tag_state(
        "叙事\n---\nSTAT:p1:kpi:10\nSTAT:p1:kpi:5\nSTAT:p1:days:-1\nNONE"
    )
    pud = result["state_update"]["players"]["p1"]
    assert pud["stat_changes"] == {"kpi": 15, "days": -1}


def test_stat_tag_rejects_exclusive_and_invalid():
    result = parse_tag_state("x\n---\nSTAT:p1:hp:-8\nSTAT:p1:mana:-10\nSTAT:p1:kpi:999\nSTAT:p1\nNONE")
    pud = result["state_update"]["players"].get("p1", {})
    assert "stat_changes" not in pud or pud["stat_changes"] == {}


def test_stat_changes_applied_with_clamp_and_unknown_ignored(tmp_path):
    rule = _rule_with_resources(tmp_path)
    instance = _instance(rule)
    applier = PlayerStateApplier(MadnessTracker())

    applier.apply_players(instance, {
        "p1": {"stat_changes": {"kpi": 80, "ghost": 10}},
    }, rule=rule)

    cs = instance.get_character_sheet("p1")
    assert cs["kpi"] == 100  # 42 + 80 钳制到 max
    assert "ghost" not in cs  # 未声明资源被忽略
    assert cs["resources"]["kpi"]["current"] == 100


def test_up_trigger_fires_once_and_injects_gm_directive(tmp_path):
    rule = _rule_with_resources(tmp_path)
    instance = _instance(rule)
    instance.players["p1"]["character_sheet"]["kpi"] = 100
    instance.players["p1"]["character_sheet"]["resources"]["kpi"]["current"] = 100

    fired = check_resource_triggers(instance, "p1", rule)
    assert len(fired) == 1
    assert "转正" in fired[0]
    assert any("转正" in d["text"] for d in round_presentation.gm_directives(instance))

    again = check_resource_triggers(instance, "p1", rule)
    assert again == []  # 一次性


def test_down_trigger_fires_when_countdown_reaches_zero(tmp_path):
    rule = _rule_with_resources(tmp_path)
    instance = _instance(rule)
    cs = instance.get_character_sheet("p1")
    cs["days"] = 0
    cs["resources"]["days"]["current"] = 0

    fired = check_resource_triggers(instance, "p1", rule)
    assert len(fired) == 1
    assert "限时耗尽" in fired[0]


def test_state_recap_reports_rule_resource_delta(tmp_path):
    rule = _rule_with_resources(tmp_path)
    instance = _instance(rule)
    before = snapshot_public_player_state(instance)
    assert before["p1"]["rule_resources"]["kpi"] == 42

    cs = instance.get_character_sheet("p1")
    cs["kpi"] = 52
    cs["resources"]["kpi"]["current"] = 52
    instance.set_character_sheet("p1", cs)

    data = {"state_update": {"players": {"p1": {"stat_changes": {"kpi": 10}}}}}
    messages = build_state_change_messages(instance, before, data)
    assert any("KPI 42 -> 52" in m for m in messages)


def test_resource_tag_appendix_lists_resources_and_trigger_note(tmp_path):
    rule = _rule_with_resources(tmp_path)
    appendix = rule.resource_tag_appendix("zh-CN")
    assert "KPI(kpi, 0-100)" in appendix
    assert "剩余天数" in appendix
    assert "自动提醒" in appendix
    assert "MANA" not in appendix

    from src.rules.rule_system import RuleSystem as RS
    empty = RS.load(Path("templates/rules/freeform_fantasy.json"))
    assert empty.resource_tag_appendix("zh-CN") == ""
