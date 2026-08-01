"""数据驱动检定词表（intents）的回归测试。"""

from __future__ import annotations

from pathlib import Path

from src.commands.dice_resolver import DiceResolver
from src.engine.checks import build_check_request
from src.engine.game_instance import GameInstance, GameState
from src.rules.rule_system import RuleSystem


def _d20_instance() -> GameInstance:
    instance = GameInstance(("web", "room", "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    instance.language = "zh-CN"
    instance.players["p1"] = {
        "character_name": "冒险者",
        "character_sheet": {
            "attributes": {"dex": 16, "int": 12, "wis": 14},
            "skills": [{"name": "潜行", "value": 20}, {"name": "侦查", "value": 45}],
        },
    }
    return instance


def _dnd_rule() -> RuleSystem:
    return RuleSystem.load(Path("templates/rules/dnd5e.json"))


def _coc_rule() -> RuleSystem:
    return RuleSystem.load(Path("templates/rules/freeform_coc.json"))


def test_observe_maps_to_perception_wis_for_d20():
    """『观察/看看/张望/瞅瞅』在 D&D 5e 下应走感知（wis），而不是智力。"""
    instance = _d20_instance()
    rule = _dnd_rule()
    for text in ("观察周围的情况", "看看周边的情况", "用眼睛张望四周", "瞅瞅前面"):
        request = build_check_request(instance, {"user_id": "p1", "text": text}, rule)
        assert request is not None
        assert request["intent"] == "perception"
        assert request["attribute"] == "wis"


def test_observe_maps_to_spot_hidden_skill_for_coc():
    """『观察周围』在 CoC 下应走侦查技能（d100），而不是属性检定。"""
    instance = _d20_instance()
    rule = _coc_rule()
    request = build_check_request(instance, {"user_id": "p1", "text": "观察周围的情况"}, rule)
    assert request is not None
    assert request["dice_system"] == "d100"
    assert request["skill"] == "侦查"


def test_english_word_boundary_matching():
    """英文触发词应走整词边界匹配，避免 'roll' 命中 'scroll' 之类误报。"""
    instance = _d20_instance()
    instance.language = "en"
    rule = _dnd_rule()

    hit = build_check_request(instance, {"user_id": "p1", "text": "look around the room"}, rule)
    assert hit is not None
    assert hit["intent"] == "perception"

    miss = build_check_request(instance, {"user_id": "p1", "text": "I pick up the scroll on the desk"}, rule)
    assert miss is None, "'scroll' 不应命中 'roll'"


def test_english_stealth_and_attack():
    instance = _d20_instance()
    instance.language = "en"
    rule = _dnd_rule()

    stealth = build_check_request(instance, {"user_id": "p1", "text": "sneak behind the guard"}, rule)
    assert stealth is not None and stealth["intent"] == "stealth"

    attack = build_check_request(instance, {"user_id": "p1", "text": "attack the goblin"}, rule)
    assert attack is not None and attack["intent"] == "combat"


def test_tavern_free_never_rolls_even_with_intents():
    """dice_system none 规则即使有词表也不触发检定。"""
    instance = _d20_instance()
    rule = RuleSystem.load(Path("templates/rules/tavern_free.json"))
    assert build_check_request(
        instance, {"user_id": "p1", "text": "悄悄上楼", "selected_skill": "潜行"}, rule
    ) is None


def test_coc_inherits_intents_from_base():
    """freeform_coc 未 extends base_d20，但应通过 intents_base 继承词表。"""
    rule = _coc_rule()
    assert "perception" in rule.intents
    assert "观察" in rule.intent_aliases("perception", "zh-CN")


def test_guess_attribute_key_consistent_with_intents():
    """_guess_attribute_key 应与词表给出同一属性（观察->wis）。"""
    resolver = DiceResolver()
    rule = _dnd_rule()
    assert resolver._guess_attribute_key("观察周围", rule) == "wis"
    assert resolver._guess_attribute_key("说服他", rule) == "cha"
    assert resolver._guess_attribute_key("攻击他", rule) == "dex"
