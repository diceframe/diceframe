from __future__ import annotations

from pathlib import Path

import pytest

from src.commands.dice_resolver import DiceResolver
from src.commands.round_actions import build_dice_constraint_block, collect_actions_text
from src.engine.checks import build_check_request
from src.engine.game_instance import GameInstance, GameState
from src.llm.parser import sanitize_narration
from src.rules.rule_system import RuleSystem
from src.webui.services.games import gm_command
from src.webui.services.logs import get_log


def _coc_instance() -> tuple[GameInstance, RuleSystem]:
    instance = GameInstance(("web", "room", "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    instance.players["p1"] = {
        "character_name": "冒险者",
        "character_sheet": {
            "attributes": {"dex": 60, "int": 50, "pow": 55},
            "skills": [{"name": "潜行", "value": 20}, {"name": "侦查", "value": 45}],
            "luck": 30,
            "max_luck": 99,
        },
    }
    return instance, RuleSystem.load(Path("templates/rules/freeform_coc.json"))


def test_natural_language_action_builds_rule_neutral_check_request():
    instance, rule = _coc_instance()
    request = build_check_request(
        instance,
        {"user_id": "p1", "text": "握紧左轮手枪悄悄上楼"},
        rule,
    )

    assert request is not None
    assert request["dice_system"] == "d100"
    assert request["skill"] == "潜行"
    assert request["attribute"] == "dex"
    assert request["label"] == "潜行检定"


def test_tavern_rule_never_requests_a_roll():
    instance, _ = _coc_instance()
    rule = RuleSystem.load(Path("templates/rules/tavern_free.json"))

    assert build_check_request(
        instance,
        {"user_id": "p1", "text": "悄悄上楼", "selected_skill": "潜行"},
        rule,
    ) is None


def test_confirmed_roll_is_reused_without_a_second_random_roll(monkeypatch):
    instance, rule = _coc_instance()
    request = build_check_request(
        instance,
        {"user_id": "p1", "text": "悄悄上楼"},
        rule,
    )
    instance.action_queue = [{
        "user_id": "p1",
        "text": "悄悄上楼\n(系统掷骰: d100=54)",
        "check_request": request,
        "dice_value": 54,
        "dice_rolls": [54],
        "dice_pending": False,
    }]
    monkeypatch.setattr("src.engine.dice.random.randint", lambda *_: pytest.fail("不得二次掷骰"))

    block = build_dice_constraint_block(instance, collect_actions_text(instance), rule, "d100", DiceResolver())

    assert "d100=54" in block
    assert instance.last_check["actor_uid"] == "p1"
    assert instance.last_check["skill"] == "潜行"
    assert instance.last_check["roll"] == 54
    assert instance.last_check["threshold"] == 20


def test_d20_advantage_reuses_both_confirmed_rolls():
    instance = GameInstance(("web", "room", "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    instance.players["p1"] = {
        "character_name": "Rogue",
        "character_sheet": {
            "attributes": {"dex": 16},
            "skills": [{"name": "Stealth", "value": 1}],
            "level": 5,
        },
    }
    rule = RuleSystem.load(Path("templates/rules/dnd5e.json"))
    request = build_check_request(
        instance,
        {
            "user_id": "p1",
            "text": "stealth check with advantage",
            "selected_skill": "Stealth",
            "selected_attribute": "dex",
            "advantage_mode": "advantage",
        },
        rule,
    )
    instance.action_queue = [{
        "user_id": "p1",
        "text": "stealth check with advantage",
        "check_request": request,
        "dice_value": 17,
        "dice_rolls": [4, 17],
        "dice_pending": False,
    }]

    build_dice_constraint_block(
        instance,
        collect_actions_text(instance),
        rule,
        "d20",
        DiceResolver(),
    )

    assert instance.last_check["dice"] == "d20"
    assert instance.last_check["rolls"] == [4, 17]
    assert instance.last_check["roll"] == 17
    assert instance.last_check["advantage_mode"] == "advantage"


class _Registry:
    def __init__(self, instance: GameInstance):
        self.instance = instance
        self.saved = 0

    def get(self, _key):
        return self.instance

    async def save(self, _instance):
        self.saved += 1


class _Api:
    def __init__(self, instance: GameInstance, rule: RuleSystem):
        self._reg = _Registry(instance)
        self.rule = rule

    @staticmethod
    def _parse_key(_key):
        return ("web", "room", "bot")

    def _load_rule_for_game(self, _instance):
        return self.rule


@pytest.mark.asyncio
async def test_gm_resource_command_updates_luck_directly_without_public_action():
    instance, rule = _coc_instance()
    api = _Api(instance, rule)

    result = await gm_command(api, "web|room|bot", "给用户加幸运20")

    assert result["ok"] is True
    assert result["kind"] == "resource_update"
    assert instance.get_character_sheet("p1")["luck"] == 50
    assert instance.action_queue == []
    assert instance.gm_directives == []


@pytest.mark.asyncio
async def test_gm_narrative_command_is_private_and_cannot_trigger_check_detection():
    instance, rule = _coc_instance()
    api = _Api(instance, rule)

    result = await gm_command(api, "web|room|bot", "让下一次判定伴随更强的死亡风险")

    assert result["kind"] == "directive"
    assert instance.action_queue == []
    assert instance.gm_directives[0]["text"] == "让下一次判定伴随更强的死亡风险"
    assert "GM指令" not in collect_actions_text(instance)


def test_player_narration_strips_internal_check_block_but_keeps_story():
    raw = """【系统潜行检定·必须遵循】
机制: coc7e_core / 标准
检定: d100=54 vs 潜行20
结果: 失败
要求: 必须遵循

你的鞋跟磕在松动的木板上。"""

    assert sanitize_narration(raw) == "你的鞋跟磕在松动的木板上。"


def test_public_log_filters_legacy_gm_instruction():
    instance, rule = _coc_instance()
    instance.log = [{
        "round": 1,
        "actions": [
            {"user_id": "system", "text": "【GM指令】秘密修正"},
            {"user_id": "p1", "text": "检查房门"},
        ],
        "gm_response": "门锁生锈了。",
    }]
    api = _Api(instance, rule)

    public = get_log(api, "web|room|bot", include_internal=False)
    internal = get_log(api, "web|room|bot", include_internal=True)

    assert [action["user_id"] for action in public["log"][0]["actions"]] == ["p1"]
    assert len(internal["log"][0]["actions"]) == 2
