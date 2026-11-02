"""游戏引擎模块 —— 状态机 + 骰子 + 战斗 + 回合协调。"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "AttackResult",
    "DiceResult",
    "GameInstance",
    "GameRegistry",
    "GameState",
    "check_d20",
    "check_d20_advantage",
    "check_d100",
    "parse_player_roll",
    "resolve_attack",
    "roll",
]

_EXPORTS = {
    "AttackResult": (".combat", "AttackResult"),
    "resolve_attack": (".combat", "resolve_attack"),
    "DiceResult": (".dice", "DiceResult"),
    "check_d20": (".dice", "check_d20"),
    "check_d20_advantage": (".dice", "check_d20_advantage"),
    "check_d100": (".dice", "check_d100"),
    "parse_player_roll": (".dice", "parse_player_roll"),
    "roll": (".dice", "roll"),
    "GameInstance": (".game_instance", "GameInstance"),
    "GameRegistry": (".game_registry", "GameRegistry"),
    "GameState": (".game_instance", "GameState"),
}


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
