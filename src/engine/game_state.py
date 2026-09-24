"""Game lifecycle state contract.

`GameState` 只是一个极小、无依赖的枚举契约：任何需要判断对局阶段的模块
（engine helper、player_control、combat 调度等）都从这里导入，而不是反向
依赖 `src.engine.game_instance`，避免形成 import cycle。

它与 `GameInstance` 的绑定关系（哪个状态允许哪些操作）仍由 GameInstance
与各 authority 路径拥有；本模块不承载任何行为。
"""

from enum import Enum


class GameState(Enum):
    """游戏生命周期状态。"""
    CREATED = "created"                  # 已创建，等待开始
    WAITING = "waiting"                  # 等待玩家加入
    ACTIVE_ACTION = "active_action"      # 行动阶段：接受玩家声明
    ACTIVE_JUDGMENT = "active_judgment"  # 判定阶段：LLM 处理中
    PUZZLE = "puzzle"                    # 当前无运行时入口；保留持久化枚举以兼容旧存档
    PAUSED = "paused"                    # 暂停（bot 重启后恢复为此状态）
    ENDED = "ended"                      # 已结束
