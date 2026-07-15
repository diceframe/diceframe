"""游戏引擎模块 —— 状态机 + 骰子 + 战斗 + 回合协调。"""

from .combat import AttackResult, resolve_attack
from .dice import DiceResult, check_d20, check_d20_advantage, check_d100, parse_player_roll, roll
from .game_instance import GameInstance, GameRegistry, GameState

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
