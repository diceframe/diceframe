"""优劣势判定职责：明确声明走 deterministic 识别，情境裁量走 LLM 规划器。

情境词（高地/黑暗/偷袭/负伤等）不得由 deterministic 代码机械改判；
LLM 经 dice_checks.advantage 下发的情境判断需通过规则能力校验；
多人协助继续由 _apply_d20_assistance 把优势给到被协助者。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.commands.check_planner import (
    _apply_d20_assistance,
    _apply_explicit_advantage_modes,
    normalize_check_specs,
)
from src.engine.checks import (
    build_check_request,
    detect_advantage_mode,
    resolve_check_request,
    roll_check_request,
)
from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem

ROOT = Path(__file__).resolve().parents[1]


def _rule(name: str) -> RuleSystem:
    return RuleSystem.load(ROOT / "templates" / "rules" / name)


def _instance(skills: list[dict] | None = None) -> GameInstance:
    instance = GameInstance(game_key=("web", "advantage-authority", "bot"))
    instance.players = {
        "a": {
            "character_name": "尤落",
            "character_sheet": {
                "deceased": False,
                "attributes": {"str": 12, "dex": 14},
                "skills": skills if skills is not None else [],
            },
        },
        "b": {
            "character_name": "星墨",
            "character_sheet": {"deceased": False, "attributes": {"str": 10}, "skills": []},
        },
    }
    return instance


def test_context_words_do_not_force_d20_advantage() -> None:
    rule = _rule("dnd5e.json")

    assert detect_advantage_mode("我从高地射击", {}, rule) == ("", "")
    assert detect_advantage_mode("我在黑暗里搜索", {}, rule) == ("", "")
    assert detect_advantage_mode("负伤疲惫地偷袭", {}, rule) == ("", "")


def test_context_words_do_not_leak_into_check_request() -> None:
    rule = _rule("dnd5e.json")
    instance = _instance()
    instance.action_queue = [{"user_id": "a", "text": "我从高地攻击守卫"}]

    request = build_check_request(instance, instance.action_queue[0], rule)

    assert request is not None
    assert request["advantage_mode"] == ""


def test_explicit_declarations_still_recognized() -> None:
    rule = _rule("dnd5e.json")

    assert detect_advantage_mode("以优势检定", {}, rule)[0] == "advantage"
    assert detect_advantage_mode("以劣势进行", {}, rule)[0] == "disadvantage"
    mode, note = detect_advantage_mode("我有优势但也有劣势", {}, rule)
    assert mode == "" and "抵消" in note

    coc = _rule("freeform_coc.json")
    assert detect_advantage_mode("奖励骰", {}, coc)[0] == "advantage"
    assert detect_advantage_mode("惩罚骰", {}, coc)[0] == "disadvantage"
    mode, note = detect_advantage_mode("奖励骰和惩罚骰", {}, coc)
    assert mode == "" and "抵消" in note


def test_explicit_declaration_overrides_llm_but_context_does_not() -> None:
    rule = _rule("dnd5e.json")
    instance = _instance()
    instance.action_queue = [
        {"user_id": "a", "text": "我从高地射击"},
        {"user_id": "b", "text": "以劣势检定"},
    ]
    planned = [
        (instance.action_queue[0], {"actor_uid": "a", "advantage_mode": "", "assist": []}),
        (instance.action_queue[1], {"actor_uid": "b", "advantage_mode": "", "assist": []}),
    ]

    _apply_explicit_advantage_modes(rule, planned)

    assert planned[0][1]["advantage_mode"] == ""
    assert planned[1][1]["advantage_mode"] == "disadvantage"


def test_llm_advantage_rolls_two_d20_keep_high(monkeypatch) -> None:
    rule = _rule("dnd5e.json")
    instance = _instance()
    instance.action_queue = [{"user_id": "a", "text": "我潜行绕过守卫"}]

    planned, errors = normalize_check_specs(
        instance, rule, [{"player": "尤落", "attribute": "dex", "target": 12, "advantage": "advantage"}]
    )

    assert not errors and len(planned) == 1
    request = planned[0][1]
    assert request["advantage_mode"] == "advantage"
    rolls = iter([5, 17])
    monkeypatch.setattr("random.randint", lambda _a, _b: next(rolls))

    result = roll_check_request(request, rule)

    assert result["rolls"] == [5, 17]
    assert result["value"] == 17


def test_llm_disadvantage_rolls_two_d20_keep_low(monkeypatch) -> None:
    rule = _rule("dnd5e.json")
    instance = _instance()
    instance.action_queue = [{"user_id": "a", "text": "我被束缚着挣脱"}]

    planned, errors = normalize_check_specs(
        instance, rule, [{"player": "尤落", "attribute": "str", "target": 12, "advantage": "disadvantage"}]
    )

    assert not errors and len(planned) == 1
    request = planned[0][1]
    assert request["advantage_mode"] == "disadvantage"
    rolls = iter([5, 17])
    monkeypatch.setattr("random.randint", lambda _a, _b: next(rolls))

    result = roll_check_request(request, rule)

    assert result["rolls"] == [5, 17]
    assert result["value"] == 5


def test_llm_advantage_rejected_when_rule_lacks_mechanic(tmp_path) -> None:
    rule_file = tmp_path / "no_advantage.json"
    rule_file.write_text(
        json.dumps(
            {
                "rule_id": "no_advantage",
                "dice_system": "d20",
                "attributes": [{"key": "dex", "name": "敏捷", "min": 3, "max": 20}],
                "check_mechanic": {
                    "dice": "d20",
                    "comparison": "roll_plus_modifier_gte_target",
                    "critical": {},
                },
            }
        ),
        encoding="utf-8",
    )
    rule = RuleSystem.load(rule_file)
    instance = _instance()
    instance.action_queue = [{"user_id": "a", "text": "我潜行绕过守卫"}]

    planned, errors = normalize_check_specs(
        instance, rule, [{"player": "尤落", "attribute": "dex", "target": 12, "advantage": "advantage"}]
    )

    assert not errors and len(planned) == 1
    assert planned[0][1]["advantage_mode"] == ""


def test_assist_grants_advantage_to_target_not_helper() -> None:
    rule = _rule("dnd5e.json")
    instance = _instance()
    instance.action_queue = [
        {"user_id": "a", "text": "我攀爬断墙"},
        {"user_id": "b", "text": "我协助尤落攀爬"},
    ]

    assert detect_advantage_mode("我协助尤落攀爬", {}, rule) == ("", "")
    planned = [
        (instance.action_queue[0], {"actor_uid": "a", "advantage_mode": "", "assist": []}),
        (instance.action_queue[1], {"actor_uid": "b", "advantage_mode": "", "assist": []}),
    ]

    result = _apply_d20_assistance(instance, rule, planned)

    assert len(result) == 1
    assert result[0][1]["actor_uid"] == "a"
    assert result[0][1]["advantage_mode"] == "advantage"
    assert result[0][1]["assist"] == ["b"]


def test_coc_context_words_never_grant_penalty_and_llm_bonus_die_works(monkeypatch) -> None:
    coc = _rule("freeform_coc.json")

    assert detect_advantage_mode("我在黑暗里搜索", {}, coc) == ("", "")

    instance = _instance(skills=[{"name": "侦查", "value": 60}])
    instance.action_queue = [{"user_id": "a", "text": "我搜索书房"}]
    planned, errors = normalize_check_specs(
        instance, coc, [{"player": "尤落", "skill": "侦查", "advantage": "advantage"}]
    )

    assert not errors and len(planned) == 1
    request = planned[0][1]
    assert request["advantage_mode"] == "advantage"
    rolls = iter([0, 0, 3])
    monkeypatch.setattr("random.randint", lambda _a, _b: next(rolls))

    result = roll_check_request(request, coc)

    assert result["rolls"] == [100, 30]
    assert result["value"] == 30


def test_llm_advantage_without_reason_is_preserved_and_audited() -> None:
    """任务三：缺少 advantage_reason 只追加审计提示，不改写掷骰方式。

    项目方案只要求非零 ``modifier`` 必须给出理由；优势/劣势是模型已声明的合法
    判定，没有依据不足以证明它与其它渠道重复，因此服务端保留 advantage 并记
    ``advantage_without_reason``。该 note 只用于审计，不给玩家追加解释行。
    """
    rule = _rule("dnd5e.json")
    instance = _instance()
    instance.action_queue = [{"user_id": "a", "text": "我潜行绕过守卫"}]

    planned, errors = normalize_check_specs(
        instance, rule, [{
            "player": "尤落", "attribute": "dex", "target": 12,
            "advantage": "advantage",
        }]
    )

    assert not errors and len(planned) == 1
    request = planned[0][1]
    assert request["advantage_mode"] == "advantage"
    assert request["advantage_reason"] is None
    assert request["planner_notes"] == ["advantage_without_reason"]
    assert request["planner_dropped"] == {}

    check = resolve_check_request(instance, {
        "user_id": "a",
        "text": "我潜行绕过守卫",
        "check_request": request,
        "dice_value": 17,
        "dice_rolls": [5, 17],
    }, rule)

    assert check is not None
    assert check["advantage_mode"] == "advantage"
    assert check["roll"] == 17  # 优势仍取两枚 d20 的高值
    assert check["planner_notes"] == ["advantage_without_reason"]
    assert "单一渠道" not in (check["modifier_breakdown"] or "")
