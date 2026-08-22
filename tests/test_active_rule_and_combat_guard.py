from __future__ import annotations

from pathlib import Path

from src.commands.combat_resolver import CombatResolver
from src.commands.check_planner import (
    _apply_d20_assistance,
    _apply_explicit_advantage_modes,
    _is_non_combat_declaration,
    _merge_safety_net_checks,
)
from src.commands.dice_resolver import DiceResolver
from src.commands.prompt_composer import PromptComposer
from src.commands.progression_resolver import ProgressionResolver
from src.engine.game_instance import GameInstance
from src.engine.checks import build_check_request, resolve_check_request, roll_check_request
from src.rules.rule_system import RuleSystem


ROOT = Path(__file__).resolve().parents[1]


def test_prompt_uses_save_rule_instead_of_world_default() -> None:
    instance = GameInstance(
        game_key=("web", "rule-choice", "bot"),
        world_id="default_fantasy",
        rule_id="dnd5e",
    )
    composer = PromptComposer(ROOT / "prompts", ROOT / "templates" / "rules")

    context = composer.load_rule_context(
        instance,
        lambda _world_id: {"world_id": "default_fantasy", "default_rule": "freeform_fantasy"},
    )

    assert context.rule is not None
    assert context.rule.rule_id == "dnd5e"
    assert context.rule.check_mechanic["critical"] == {"success": None, "failure": None}


def _combat_instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "combat-guard", "bot"))
    instance.players = {
        "a": {
            "character_name": "冒险者",
            "character_sheet": {
                "hp": 14,
                "max_hp": 14,
                "deceased": False,
                "attributes": {"str": 12},
                "equipment": [{"name": "巨斧", "slot": "main_hand", "damage": 11}],
            },
        },
        "b": {
            "character_name": "星墨",
            "character_sheet": {
                "hp": 7,
                "max_hp": 7,
                "deceased": False,
                "attributes": {"str": 8},
                "equipment": [{"name": "法杖", "slot": "main_hand", "damage": 3}],
            },
        },
    }
    return instance


def test_magic_research_does_not_attack_first_player() -> None:
    instance = _combat_instance()
    instance.action_queue = [
        {"user_id": "a", "text": "我检查附近的物理痕迹，并与魔法线索相互印证。"},
        {"user_id": "b", "text": "我辨识异常的法术学派和风险。"},
    ]

    text = CombatResolver().resolve_combat(instance, "【冒险者】...\n【星墨】...", "hp_based")

    assert text == ""
    assert instance.get_character_sheet("a")["hp"] == 14
    assert instance.pending_combat_results == []


def test_only_named_attacker_hits_named_target() -> None:
    instance = _combat_instance()
    instance.action_queue = [
        {"user_id": "a", "text": "我观察星墨的法术。"},
        {
            "user_id": "b",
            "text": "我用法杖攻击冒险者。",
            "check_request": {
                "check_id": "b-attacks-a",
                "actor_uid": "b",
                "kind": "attack",
                "opponent": "a",
            },
        },
    ]
    instance.last_checks = [{
        "check_id": "b-attacks-a",
        "actor_uid": "b",
        "actor_name": "星墨",
        "kind": "attack",
        "opponent": "a",
        "dice": "d20",
        "roll": 20,
        "total": 21,
        "verdict": "成功",
        "is_critical": False,
        "is_fumble": False,
    }]

    text = CombatResolver().resolve_combat(instance, "ignored", "hp_based")

    assert "星墨持法杖攻击冒险者" in text
    assert len(instance.pending_combat_results) == 1
    assert instance.pending_combat_results[0]["attacker"] == "星墨"
    assert instance.pending_combat_results[0]["target"] == "冒险者"


def test_dnd_level_up_uses_active_rule_and_class_hit_die() -> None:
    instance = GameInstance(
        game_key=("web", "dnd-level", "bot"),
        world_id="default_fantasy",
        rule_id="dnd5e",
    )
    instance.players["a"] = {
        "character_name": "冒险者",
        "character_sheet": {
            "class": "野蛮人",
            "level": 1,
            "xp": 100,
            "hp": 14,
            "max_hp": 14,
            "attributes": {"con": 14},
        },
    }
    resolver = ProgressionResolver(
        ROOT / "templates" / "rules",
        ROOT / "templates" / "worlds",
    )

    resolver.try_level_up(instance, "a")

    sheet = instance.get_character_sheet("a")
    assert sheet["level"] == 2
    assert sheet["max_hp"] == 23  # d12 固定值 7 + CON +2


def test_dnd_help_gives_target_advantage_and_helper_no_own_roll() -> None:
    instance = _combat_instance()
    instance.world_id = "default_fantasy"
    instance.rule_id = "dnd5e"
    instance.action_queue = [
        {"user_id": "a", "text": "我在星墨掩护下检查前方，并接受他的协助。"},
        {"user_id": "b", "text": "我专心协助冒险者观察危险，为他提供掩护。"},
    ]
    rule = PromptComposer(
        ROOT / "prompts", ROOT / "templates" / "rules"
    ).load_rule_context(
        instance,
        lambda _world_id: {"world_id": "default_fantasy", "default_rule": "freeform_fantasy"},
    ).rule
    planned = [
        (instance.action_queue[0], {"actor_uid": "a", "advantage_mode": "", "assist": []}),
        (instance.action_queue[1], {"actor_uid": "b", "advantage_mode": "advantage", "assist": []}),
    ]

    result = _apply_d20_assistance(instance, rule, planned)

    assert len(result) == 1
    assert result[0][1]["actor_uid"] == "a"
    assert result[0][1]["advantage_mode"] == "advantage"
    assert result[0][1]["assist"] == ["b"]


def test_coc_bonus_die_in_round_pipeline_shares_units(monkeypatch) -> None:
    instance = GameInstance(game_key=("web", "coc-bonus", "bot"), rule_id="freeform_coc")
    instance.players["a"] = {
        "character_name": "调查员",
        "character_sheet": {
            "attributes": {"int": 70},
            "skills": [{"name": "侦查", "value": 60}],
            "deceased": False,
        },
    }
    action = {"user_id": "a", "text": "我用侦查搜索，并获得一个奖励骰。"}
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "freeform_coc.json")
    request = build_check_request(instance, action, rule)
    assert request is not None
    assert request["advantage_mode"] == "advantage"
    rolls = iter([0, 0, 3])
    monkeypatch.setattr("random.randint", lambda _a, _b: next(rolls))

    result = roll_check_request(request, rule)

    assert result["value"] == 30
    assert result["rolls"] == [100, 30]
    action["check_request"] = request
    action["dice_value"] = result["value"]
    action["dice_rolls"] = result["rolls"]
    DiceResolver().resolve_action_check(instance, action, rule)
    assert instance.last_check["roll"] == 30
    assert instance.last_check["advantage_mode"] == "advantage"


def test_explicit_coc_penalty_die_overrides_percent_modifier() -> None:
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "freeform_coc.json")
    action = {"user_id": "a", "text": "在黑暗中用侦查搜索，承受一个惩罚骰。"}
    request = {
        "actor_uid": "a",
        "advantage_mode": "",
        "circumstance_modifier": -20,
    }

    result = _apply_explicit_advantage_modes(rule, [(action, request)])

    assert result[0][1]["advantage_mode"] == "disadvantage"
    assert result[0][1]["circumstance_modifier"] == 0


def test_avoiding_combat_does_not_force_a_safety_net_roll() -> None:
    instance = _combat_instance()
    instance.world_id = "default_fantasy"
    instance.rule_id = "dnd5e"
    instance.action_queue = [
        {"user_id": "a", "text": "我清点装备并提醒大家避免不必要的战斗。"},
    ]
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")

    assert _merge_safety_net_checks(instance, rule, []) == []


def test_cyberpunk_weapon_check_without_attack_does_not_force_combat_roll() -> None:
    instance = _combat_instance()
    instance.world_id = "scifi_cyberpunk"
    instance.rule_id = "freeform_cyberpunk"
    instance.action_queue = [{
        "user_id": "a",
        "text": "我清点弹匣并确认保险，没有敌人时不随意开枪。",
    }]
    rule = RuleSystem.load(
        ROOT / "templates" / "rules" / "freeform_cyberpunk.json"
    )

    assert _merge_safety_net_checks(instance, rule, []) == []


def test_cyberpunk_inherits_rule_declared_d20_advantage(monkeypatch) -> None:
    instance = _combat_instance()
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "freeform_cyberpunk.json")
    action = {
        "user_id": "a",
        "text": "我占据高地，以优势射击目标。",
        "selected_attribute": "str",
    }
    request = build_check_request(instance, action, rule)
    assert request is not None
    assert rule.advantage_mechanic["type"] == "d20_keep_high_low"
    assert request["advantage_mode"] == "advantage"
    rolls = iter([4, 16])
    monkeypatch.setattr("src.engine.dice_rng.random.randint", lambda _a, _b: next(rolls))

    result = roll_check_request(request, rule)

    assert result["rolls"] == [4, 16]
    assert result["value"] == 16


def test_custom_rule_can_disable_advantage_even_if_request_asks_for_it(monkeypatch) -> None:
    rule = RuleSystem({
        "rule_id": "plain_d20",
        "dice_system": "d20",
        "check_mechanic": {
            "dice": "d20",
            "critical": {},
            "advantage": {
                "type": "",
                "allow_explicit": False,
                "assistance_grants": "",
            },
        },
    })
    monkeypatch.setattr("src.engine.dice_rng.random.randint", lambda _a, _b: 12)

    result = roll_check_request(
        {"dice_system": "d20", "advantage_mode": "advantage"},
        rule,
    )

    assert result["rolls"] == [12]
    assert result["value"] == 12


def test_coc_bonus_die_is_selected_by_capability_not_mechanics_name() -> None:
    rule = RuleSystem({
        "rule_id": "custom_percentile",
        "dice_system": "d100",
        "mechanics": "homebrew_percentile",
        "check_mechanic": {
            "dice": "d100",
            "advantage": {
                "type": "coc_bonus_penalty",
                "allow_explicit": True,
                "assistance_grants": "",
            },
        },
    })
    action = {"text": "我获得一个奖励骰", "advantage_mode": ""}
    request = {"actor_uid": "a", "advantage_mode": "", "circumstance_modifier": 10}

    result = _apply_explicit_advantage_modes(rule, [(action, request)])

    assert result[0][1]["advantage_mode"] == "advantage"
    assert result[0][1]["circumstance_modifier"] == 0


def test_cyberpunk_assistance_uses_rule_declared_advantage() -> None:
    instance = _combat_instance()
    instance.action_queue = [
        {"user_id": "a", "text": "我在星墨掩护下破解门锁，并接受他的协助。"},
        {"user_id": "b", "text": "我专心协助冒险者破解门锁，为冒险者提供掩护。"},
    ]
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "freeform_cyberpunk.json")
    planned = [
        (instance.action_queue[0], {"actor_uid": "a", "advantage_mode": "", "assist": []}),
        (instance.action_queue[1], {"actor_uid": "b", "advantage_mode": "", "assist": []}),
    ]

    result = _apply_d20_assistance(instance, rule, planned)

    assert len(result) == 1
    assert result[0][1]["assist"] == ["b"]
    assert result[0][1]["advantage_mode"] == "advantage"


def test_core_resolution_is_the_same_result_recorded_by_command_adapter() -> None:
    instance = _combat_instance()
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")
    action = {
        "user_id": "a",
        "text": "我用力量推开石门",
        "check_request": {
            "check_id": "single-authority",
            "actor_uid": "a",
            "dice_system": "d20",
            "attribute": "str",
            "target": 15,
            "circumstance_modifier": 1,
        },
        "dice_value": 13,
        "dice_rolls": [13],
    }

    expected = resolve_check_request(instance, action, rule)
    DiceResolver().resolve_action_check(instance, action, rule)

    assert expected is not None
    assert instance.last_check == expected
    assert expected["total"] == 15
    assert expected["verdict"] == "成功"


def test_negated_combat_recognizer_keeps_real_attack_in_mixed_sentence() -> None:
    assert _is_non_combat_declaration("我不会主动开火，只清点弹匣和确认保险。") is True
    assert _is_non_combat_declaration("I avoid unnecessary fighting and keep watch.") is True
    assert _is_non_combat_declaration("我不开枪，改用刀攻击敌人。") is False


def test_model_planned_attack_is_removed_for_non_combat_declaration() -> None:
    instance = _combat_instance()
    action = {"user_id": "a", "text": "我不会主动开火，只观察出口。"}
    instance.action_queue = [action]
    rule = RuleSystem.load(ROOT / "templates" / "rules" / "freeform_cyberpunk.json")
    planned = [(action, {
        "actor_uid": "a",
        "kind": "attack",
        "intent": "ai_planned",
        "planner_source": "llm_tool",
    })]

    result = _merge_safety_net_checks(instance, rule, planned)

    assert all(request.get("kind") != "attack" for _, request in result)
