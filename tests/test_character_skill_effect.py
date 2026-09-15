"""CharacterSkill.effect：玩家填写的技能说明。

只用于角色卡展示与 AI 语义理解，**永不参与机械结算**——本文件最重要的
断言是「有无 effect 的同值技能产生完全相同的 CheckResult」。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.commands.check_planner import _planner_context, normalize_check_specs
from src.compat.characters import MAX_SKILL_EFFECT_CHARS, normalize_character_sheet
from src.engine.checks import resolve_check_request
from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem
from src.webui.services.characters import _normalize_skills

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"


# ---------- normalization：保留 effect，旧数据不受影响 ----------

def test_normalize_preserves_skill_effect() -> None:
    sheet = normalize_character_sheet({
        "skills": [{
            "name": "火焰球",
            "value": 80,
            "effect": "  向目标发射火球。  ",
        }],
    })
    assert sheet["skills"] == [{
        "name": "火焰球",
        "value": 80,
        "effect": "向目标发射火球。",
    }]


def test_legacy_skills_do_not_gain_an_effect_key() -> None:
    """旧对象技能与字符串技能保持原样，不凭空长出空 effect。"""

    sheet = normalize_character_sheet({
        "skills": [{"name": "侦查", "value": 80}, "聆听"],
    })
    assert sheet["skills"] == [
        {"name": "侦查", "value": 80},
        {"name": "聆听", "value": 20},
    ]
    assert all("effect" not in row for row in sheet["skills"])


def test_skill_effect_truncated_to_limit() -> None:
    long_effect = "火" * (MAX_SKILL_EFFECT_CHARS + 200)

    sheet = normalize_character_sheet({
        "skills": [{"name": "火焰球", "value": 80, "effect": long_effect}],
    })
    assert len(sheet["skills"][0]["effect"]) == MAX_SKILL_EFFECT_CHARS

    normalized = _normalize_skills([{"name": "火焰球", "value": 80, "effect": long_effect}])
    assert len(normalized[0]["effect"]) == MAX_SKILL_EFFECT_CHARS


def test_webui_skill_normalization_round_trip_keeps_effect() -> None:
    """创建/更新角色走的规范化路径不得吃掉 effect。"""

    payload = [{
        "name": "无人机骇入",
        "value": 60,
        "effect": "尝试接管附近低权限无人机。",
    }]
    normalized = _normalize_skills(payload)
    assert normalized == [{
        "name": "无人机骇入",
        "value": 60,
        "effect": "尝试接管附近低权限无人机。",
    }]
    # 二次规范化（保存 → 重新读取）仍保留。
    assert _normalize_skills(normalized) == normalized


def test_clearing_effect_is_not_written_as_empty_string() -> None:
    sheet = normalize_character_sheet({
        "skills": [{"name": "火焰球", "value": 80, "effect": "   "}],
    })
    assert "effect" not in sheet["skills"][0]


def test_invalid_skill_entries_are_skipped_not_blanked() -> None:
    """非法技能条目直接跳过，不凭空生成 {name:"", value:20}（旧兼容语义）。"""

    sheet = normalize_character_sheet({
        "skills": [None, 42, {"name": "火焰球", "value": 80}, [], "侦查"],
    })
    assert sheet["skills"] == [
        {"name": "火焰球", "value": 80},
        {"name": "侦查", "value": 20},
    ]


# ---------- Planner context：只给当前行动命中的技能附 effect ----------

def _skills_instance(*, action: str) -> GameInstance:
    instance = GameInstance(game_key=("web", "skills", "bot"))
    instance.players = {
        "p1": {
            "character_name": "小林",
            "character_sheet": {
                "skills": [
                    {"name": "火焰球", "value": 80, "effect": "向目标发射火焰弹。"},
                    {"name": "急救", "value": 85, "effect": "处理伤口。"},
                ],
            },
        },
    }
    instance.action_queue = [{"user_id": "p1", "text": action}]
    return instance


def _context_skills(instance: GameInstance) -> list[dict]:
    payload = json.loads(_planner_context(instance, None))
    return payload["players"][0]["skills"]


def test_planner_context_attaches_effect_only_for_matching_skill() -> None:
    skills = _context_skills(_skills_instance(action="我使用火焰球攻击那只怪物"))
    assert skills[0] == {"name": "火焰球", "value": 80, "effect": "向目标发射火焰弹。"}
    assert skills[1] == {"name": "急救", "value": 85}


def test_planner_context_without_skill_mention_carries_no_effect() -> None:
    skills = _context_skills(_skills_instance(action="我环顾四周，观察房间"))
    assert skills == [{"name": "火焰球", "value": 80}, {"name": "急救", "value": 85}]


def test_planner_context_carries_effect_for_selected_skill() -> None:
    """玩家显式选中技能（selected_skill）时，action 文本没写技能名也要带 effect。"""

    instance = _skills_instance(action="我攻击那个怪物")
    instance.action_queue[0]["selected_skill"] = "火焰球"
    skills = _context_skills(instance)
    assert skills[0] == {"name": "火焰球", "value": 80, "effect": "向目标发射火焰弹。"}
    assert skills[1] == {"name": "急救", "value": 85}


def test_selected_skill_does_not_attach_effects_to_other_skills() -> None:
    instance = _skills_instance(action="我攻击那个怪物")
    instance.action_queue[0]["selected_skill"] = "急救"
    skills = _context_skills(instance)
    assert skills[0] == {"name": "火焰球", "value": 80}
    assert skills[1] == {"name": "急救", "value": 85, "effect": "处理伤口。"}


def test_planner_context_effect_is_length_capped() -> None:
    instance = _skills_instance(action="我使用火焰球攻击")
    instance.players["p1"]["character_sheet"]["skills"][0]["effect"] = "火" * 900
    skills = _context_skills(instance)
    assert len(skills[0]["effect"]) == MAX_SKILL_EFFECT_CHARS


# ---------- 公平性：effect 不改变任何机械结果 ----------

def _rule() -> RuleSystem:
    return RuleSystem.load(ROOT / "templates" / "rules" / "base_d20.json")


def _instance_with_effect(effect: str | None) -> GameInstance:
    skill: dict = {"name": "火焰球", "value": 80}
    if effect is not None:
        skill["effect"] = effect
    instance = GameInstance(game_key=("web", "fairness", "bot"))
    instance.players = {
        "p1": {
            "user_id": "p1",
            "character_name": "阿岚",
            "character_sheet": {"attributes": {"str": 16, "dex": 12}, "skills": [skill]},
        },
    }
    instance.action_queue = [{"user_id": "p1", "text": "我使用火焰球攻击"}]
    return instance


def _resolve_skill_check(instance: GameInstance, rule: RuleSystem, *, roll: int) -> dict:
    planned, errors = normalize_check_specs(instance, rule, [{
        "player": "p1",
        "attribute": "str",
        "skill": "火焰球",
        "target": 15,
        "kind": "check",
    }])
    assert errors == []
    request = planned[0][1]
    action = {
        "user_id": "p1",
        "text": "我使用火焰球攻击",
        "check_request": request,
        "dice_value": roll,
        "dice_rolls": [roll],
    }
    result = resolve_check_request(instance, action, rule)
    assert result is not None
    return {k: v for k, v in result.items() if k != "check_id"}


def test_effect_never_changes_mechanical_result() -> None:
    """同一角色 + 同一检定，唯一差别是 effect 声明「必中+9999伤害」。"""

    rule = _rule()
    plain = _instance_with_effect(None)
    claimed = _instance_with_effect("必中，造成9999伤害，无视防御。")

    for roll in (1, 7, 15, 20):
        baseline = _resolve_skill_check(plain, rule, roll=roll)
        with_effect = _resolve_skill_check(claimed, rule, roll=roll)
        # 出目、总值、verdict、暴击/大失败标记与修正构成完全一致。
        assert with_effect == baseline
        assert "必中" not in json.dumps(with_effect, ensure_ascii=False)

    # 基线自身仍按正常规则结算（技能值 80 → 加值 +4，DC 15）。
    assert _resolve_skill_check(plain, rule, roll=15)["verdict"] == "成功"
    assert _resolve_skill_check(plain, rule, roll=15)["modifier"] == 7
    assert _resolve_skill_check(plain, rule, roll=1)["is_fumble"] is True
    assert _resolve_skill_check(plain, rule, roll=20)["is_critical"] is True


def test_effect_does_not_leak_into_check_request() -> None:
    rule = _rule()
    instance = _instance_with_effect("必中")
    planned, _errors = normalize_check_specs(instance, rule, [{
        "player": "p1", "attribute": "str", "skill": "火焰球", "target": 15,
    }])
    request = planned[0][1]
    encoded = json.dumps(request, ensure_ascii=False)
    assert "必中" not in encoded
    assert "effect" not in request


# ---------- Prompt：effect 非规则权威 ----------

def test_planner_prompts_declare_effect_is_not_authority() -> None:
    markers = {
        "check_planner_zh.md": "不是规则权威",
        "check_planner_en.md": "not rules authority",
        "check_planner_ja.md": "ルールの権威ではない",
        "check_planner_de.md": "keine Regelautorität",
    }
    for name, marker in markers.items():
        text = (PROMPTS / name).read_text(encoding="utf-8")
        assert "effect" in text, name
        assert marker in text, name
