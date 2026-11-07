"""CoC 守秘人指导：规则级规划器附录、近期检定、安全网不强补重复检定。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.commands.check_planner import (
    _merge_safety_net_checks,
    _planner_context,
    _planner_system_prompt,
    _recent_checks,
)
from src.content.rule_locale import materialize_rule
from src.engine.game_instance import GameInstance
from src.llm.protocol import KNOWN_PROTOCOL_TAGS
from src.rules.rule_system import RuleSystem

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "templates/rules/freeform_coc.json"


def coc_rule(locale: str | None = None) -> RuleSystem:
    path = ROOT / "templates/rules/locales" / locale / "freeform_coc.json" if locale else CORE
    return RuleSystem.load(path)


def coc_instance(text: str = "我用侦查再仔细找找书桌的抽屉", *, language: str = "zh-CN") -> GameInstance:
    instance = GameInstance(game_key=("web", "room", "bot"), rule_id="freeform_coc")
    instance.language = language
    instance.players = {
        "p1": {
            "user_id": "p1",
            "character_name": "阿岚",
            "character_sheet": {
                "attributes": {"int": 60, "pow": 50},
                "skills": [{"name": "侦查", "value": 60}, {"name": "聆听", "value": 40}],
            },
        },
    }
    instance.action_queue = [{"user_id": "p1", "text": text}]
    return instance


def add_round(instance: GameInstance, round_no: int, text: str, checks: list[dict]) -> None:
    instance.log.append({
        "round": round_no,
        "actions": [{"user_id": "p1", "text": text}],
        "gm_response": "……",
        "check_results": checks,
    })


# ---------- 规则字段 ----------

@pytest.mark.parametrize("locale", [None, "en", "ja"])
def test_coc_rule_declares_both_appendices(locale):
    rule = coc_rule(locale)
    language = locale or "zh-CN"
    assert rule.get_check_planner_appendix(language).strip()
    appendix = rule.get_gm_prompt_appendix(language)
    assert "PRIVATE" in appendix and "QUICK_ACTIONS" in appendix and "MEMORY" in appendix


@pytest.mark.parametrize("locale", [None, "en", "ja"])
def test_coc_appendices_reference_only_supported_protocol_tags(locale):
    rule = coc_rule(locale)
    language = locale or "zh-CN"
    for text in (rule.get_gm_prompt_appendix(language), rule.get_check_planner_appendix(language)):
        tags = set(re.findall(r"\b([A-Z][A-Z_]{1,31})\s*[:：]", text))
        assert tags <= KNOWN_PROTOCOL_TAGS


def test_planner_appendix_is_a_display_field_not_mechanics():
    assert coc_rule().mechanics_snapshot() == coc_rule("en").mechanics_snapshot() == coc_rule("ja").mechanics_snapshot()
    core = json.loads(CORE.read_text(encoding="utf-8"))
    overlay = {
        "locale_schema_version": 1, "locale": "xx",
        "target": {"kind": "rule", "id": "freeform_coc"},
        "rule": {"check_planner_appendix": "overlay text"},
    }
    assert materialize_rule(core, overlay)["check_planner_appendix"] == "overlay text"


# ---------- 规划器 prompt ----------

@pytest.mark.parametrize(("locale", "language", "heading"), [
    (None, "zh-CN", "## 当前规则的裁定要点"),
    ("en", "en", "## Adjudication notes for the current rules"),
    ("ja", "ja", "## 現在のルールの裁定要点"),
])
def test_planner_prompt_appends_the_rule_appendix(locale, language, heading):
    rule = coc_rule(locale)
    instance = coc_instance(language=language)
    prompt = _planner_system_prompt(instance, rule)
    assert heading in prompt
    assert prompt.rstrip().endswith(rule.get_check_planner_appendix(language).strip())


def test_planner_prompt_without_rule_appendix_is_unchanged():
    instance = coc_instance()
    plain = _planner_system_prompt(instance, None)
    d20 = _planner_system_prompt(instance, RuleSystem.load(ROOT / "templates/rules/dnd2024_srd.json"))
    assert plain == d20
    assert "## 当前规则的裁定要点" not in plain


@pytest.mark.parametrize("locale", ["zh", "en", "ja", "de", "ru"])
def test_generic_planner_prompt_explains_recent_checks(locale):
    text = (ROOT / f"prompts/check_planner_{locale}.md").read_text(encoding="utf-8-sig")
    assert "`recent_checks`" in text


# ---------- 近期检定 ----------

def test_recent_checks_summarise_resolved_checks_with_the_action():
    instance = coc_instance()
    add_round(instance, 1, "我用侦查找找书桌", [{
        "actor_uid": "p1", "skill": "侦查", "verdict": "失败",
        "push": {"status": "resolved"}, "luck_decision": "declined",
    }])
    add_round(instance, 2, "我听听门外", [{"actor_uid": "p1", "skill": "聆听", "verdict": "普通成功"}])
    rows = json.loads(_planner_context(instance, coc_rule()))["recent_checks"]
    assert rows == [
        {"round": 1, "player": "p1", "check": "侦查", "verdict": "失败", "action": "我用侦查找找书桌", "pushed": True},
        {"round": 2, "player": "p1", "check": "聆听", "verdict": "普通成功", "action": "我听听门外"},
    ]


def test_recent_checks_are_bounded():
    instance = coc_instance()
    for round_no in range(1, 7):
        add_round(instance, round_no, "x" * 300, [
            {"actor_uid": "p1", "skill": "侦查", "verdict": "失败"},
            {"actor_uid": "p1", "skill": "聆听", "verdict": "失败"},
            {"actor_uid": "p1", "attribute_key": "int", "verdict": "失败"},
        ])
    rows = _recent_checks(instance)
    assert len(rows) == 8
    assert {row["round"] for row in rows} <= {3, 4, 5, 6}
    assert all(len(row["action"]) == 120 for row in rows)


# ---------- 安全网 ----------

def test_safety_net_still_adds_a_named_skill_check_without_history():
    instance = coc_instance()
    planned = _merge_safety_net_checks(instance, coc_rule(), [])
    assert [request["skill"] for _, request in planned] == ["侦查"]


def test_safety_net_does_not_force_a_repeat_of_a_recent_check():
    instance = coc_instance()
    add_round(instance, 1, "我用侦查找找书桌", [{"actor_uid": "p1", "skill": "侦查", "verdict": "失败"}])
    assert _merge_safety_net_checks(instance, coc_rule(), []) == []


def test_safety_net_repeat_guard_only_looks_at_recent_rounds():
    instance = coc_instance()
    add_round(instance, 1, "我用侦查找找书桌", [{"actor_uid": "p1", "skill": "侦查", "verdict": "失败"}])
    add_round(instance, 2, "我离开书房", [])
    add_round(instance, 3, "我回到大厅", [])
    planned = _merge_safety_net_checks(instance, coc_rule(), [])
    assert [request["skill"] for _, request in planned] == ["侦查"]


def test_explicit_skill_selection_is_still_honoured_after_a_recent_check():
    instance = coc_instance()
    instance.action_queue[0]["selected_skill"] = "侦查"
    add_round(instance, 1, "我用侦查找找书桌", [{"actor_uid": "p1", "skill": "侦查", "verdict": "失败"}])
    planned = _merge_safety_net_checks(instance, coc_rule(), [])
    assert [request["skill"] for _, request in planned] == ["侦查"]
