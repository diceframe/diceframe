"""#companion 委派检定与协助：AI 队友作为检定主体。

- "让米拉去推门" → actor_ref=companion:mira，属性/技能取米拉角色卡；
- "米拉帮我推门" → actor 仍是玩家，assist 包含 companion 引用；
- 模型数值不可信：modifier 一律来自服务器角色卡。
"""

from __future__ import annotations

from pathlib import Path

from src.commands.check_planner import _planner_context, normalize_check_specs
from src.engine.checks import resolve_check_request
from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem

ROOT = Path(__file__).resolve().parents[1]


def make_instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "room", "bot"), rule_id="test")
    instance.players = {
        "p1": {
            "user_id": "p1",
            "character_name": "阿岚",
            "character_sheet": {"attributes": {"str": 8, "dex": 12}, "skills": []},
        },
        "p2": {
            "user_id": "p2",
            "character_name": "白露",
            "character_sheet": {"attributes": {"str": 14, "dex": 12}, "skills": []},
        },
    }
    instance.action_queue = [
        {"user_id": "p1", "text": "让米拉去推开沉重的石门"},
        {"user_id": "p2", "text": "我观察四周"},
    ]
    instance.ruleset_state = {"party": {"companions": {
        "mira": {
            "id": "mira", "name": "米拉", "controller": "ai", "active": True,
            "ruleset_character": {
                "resources": {"hp": 20, "max_hp": 20},
                "abilities": {
                    "str": 18, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 10,
                },
                "derived": {
                    "armor_class": 16, "speed": 30, "proficiency_bonus": 2,
                    "saving_throws": {},
                },
                "proficiencies": {"skill_values": {"运动": 6}},
                "spellcasting": {"class": {
                    "ability": "wis", "slots_current": {}, "concentration": None,
                    "prepared_spell_refs": [], "cantrip_refs": [],
                }},
                "build": {"class_levels": [{"class_ref": "class:fighter", "level": 5}]},
            },
        },
    }}}
    return instance


def _plan_one(instance: GameInstance, rule: RuleSystem, raw: dict) -> tuple[dict, list[str]]:
    planned, errors = normalize_check_specs(instance, rule, [raw])
    return planned, errors


def test_planner_context_lists_active_companions() -> None:
    instance = make_instance()
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "base_d20.json")
    context = _planner_context(instance, rule)
    assert '"actor_ref":"companion:mira"' in context
    assert "米拉" in context


def test_delegated_companion_check_uses_companion_sheet() -> None:
    """§37.8/37.9：委派检定使用队友 STR（+4），而不是玩家 STR 8（-1）。"""
    instance = make_instance()
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "base_d20.json")

    planned, errors = normalize_check_specs(instance, rule, [{
        "player": "p1", "actor": "companion:mira", "attribute": "str", "target": 15,
    }])
    assert errors == []
    request = planned[0][1]
    assert request["actor_ref"] == "companion:mira"
    assert request["actor_name"] == "米拉"
    assert request["actor_uid"] == "p1"  # 行动仍挂回委派玩家

    action = {
        "user_id": "p1", "text": "让米拉去推开沉重的石门",
        "check_request": request, "dice_value": 10, "dice_rolls": [10],
    }
    check = resolve_check_request(instance, action, rule)
    assert check is not None
    assert check["actor_ref"] == "companion:mira"
    assert check["actor_name"] == "米拉"
    assert check["modifier"] == 4  # 米拉 STR 18 → +4；玩家 STR 8 是 -1
    assert check["total"] == 14


def test_companion_delegation_by_name_and_rejects_unknown() -> None:
    instance = make_instance()
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "base_d20.json")

    planned, errors = normalize_check_specs(instance, rule, [{
        "player": "p1", "actor": "米拉", "attribute": "str", "target": 12,
    }])
    assert errors == []
    assert planned[0][1]["actor_ref"] == "companion:mira"

    planned, errors = normalize_check_specs(instance, rule, [{
        "player": "p1", "actor": "companion:不存在", "attribute": "str", "target": 12,
    }])
    assert planned == []
    assert errors and "actor" in errors[0]

    # 未指定 actor 时默认玩家本人
    planned, errors = normalize_check_specs(instance, rule, [{
        "player": "p1", "attribute": "str", "target": 12,
    }])
    assert errors == []
    assert planned[0][1]["actor_ref"] == "player:p1"


def test_companion_assist_grants_help_advantage() -> None:
    """§37.10：玩家主检定 + 队友协助 → assist 含 companion 引用并按规则给优势。"""
    instance = make_instance()
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "base_d20.json")

    planned, errors = normalize_check_specs(instance, rule, [{
        "player": "p1", "attribute": "str", "target": 15, "assist": ["米拉"],
    }])
    assert errors == []
    request = planned[0][1]
    assert request["actor_ref"] == "player:p1"
    assert "companion:mira" in request["assist"]
    assert request["advantage_mode"] == "advantage"


def test_assist_rejects_unknown_actors() -> None:
    instance = make_instance()
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "base_d20.json")

    planned, errors = normalize_check_specs(instance, rule, [{
        "player": "p1", "attribute": "str", "target": 15, "assist": ["路人甲"],
    }])
    assert planned == []
    assert errors and "assist" in errors[0]


def test_companion_skill_delegation_uses_companion_skill_values() -> None:
    instance = make_instance()
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "base_d20.json")

    planned, errors = normalize_check_specs(instance, rule, [{
        "player": "p1", "actor": "companion:mira", "skill": "运动", "target": 14,
    }])
    assert errors == []
    request = planned[0][1]
    assert request["skill"] == "运动"

    action = {
        "user_id": "p1", "text": "让米拉去推开沉重的石门",
        "check_request": request, "dice_value": 10, "dice_rolls": [10],
    }
    check = resolve_check_request(instance, action, rule)
    assert check is not None
    assert check["skill"] == "运动"  # 技能从米拉角色卡解析（玩家卡无技能）
    # base_d20 是 narrative skill_mode：技能名仅叙事，不产生数值加值；
    # modifier 来自米拉 STR 18（+4），证明 sheet 已切换到队友。
    assert check["modifier"] == 4
