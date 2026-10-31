"""Game/session configuration write details for :class:`GameInstance`.

The aggregate keeps the public compatibility facade and transaction boundary;
this module owns validation order and the field-owner calls for configuration
updates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.engine import module_hooks
from src.engine.language import normalize_language
from src.engine.narrative_perspective import validate_narrative_perspective
from src.engine.modules import economy_state, game_config, room_access

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance, GameState


def configure_game(
    instance: GameInstance,
    *,
    world_id: str | None,
    rule_id: str,
    world_name: str,
    group_name: str,
    seed_code: str,
    difficulty: str,
    language: str,
    state: GameState,
) -> None:
    """配置新局身份信息；调用方在并发路径中应持有 ``_lock``。"""
    game_config.require_writable(instance)
    instance.world_id = world_id
    instance.rule_id = rule_id or "freeform_fantasy"
    instance.world_name = world_name
    instance.group_name = group_name
    game_config.replace_seed_code(instance, seed_code)
    game_config.replace_difficulty(instance, difficulty)
    instance.language = normalize_language(language)
    instance.state = state


def configure_session(
    instance: GameInstance,
    *,
    solo_mode: bool | None = None,
    entry_point: str | None = None,
    room_password: str | None = None,
    gm_uid: str | None = None,
    luck_timeout_seconds: int | None = None,
    narrative_perspective: str | None = None,
    economy_reward_policy: dict | None = None,
    dice_reveal_mode: str | None = None,
) -> None:
    """集中更新入口与房间身份配置，保留旧存档字段。

    luck_timeout_seconds：每玩家幸运超时秒数（0=禁用，异步局建议 0）。
    economy_reward_policy：本局奖励自动结算策略；非法载荷整体拒绝。
    dice_reveal_mode：纯表现偏好（auto=自动出结果，click=点击共享揭示），
    不改变任何机械判定与推进语义。
    任一字段非法时整体拒绝，不写入任何字段。
    """
    # Validate every participating owner and value before writing any part
    # of this configuration transaction.
    if solo_mode is not None or entry_point is not None:
        game_config.require_writable(instance)
    if room_password is not None:
        room_access.require_writable(instance)
        room_password = str(room_password or "")
        room_access.validate_new_room_password(room_password)
    if luck_timeout_seconds is not None or dice_reveal_mode is not None:
        module_hooks.preflight(instance, module_hooks.CHECK_PRESENTATION)
    if narrative_perspective is not None:
        module_hooks.preflight(instance, module_hooks.NARRATION_PREFERENCES)
        narrative_perspective = validate_narrative_perspective(narrative_perspective)
    if luck_timeout_seconds is not None:
        luck_timeout_seconds = int(luck_timeout_seconds)
        if not 0 <= luck_timeout_seconds <= 3600:
            raise ValueError("幸运超时需在 0..3600 秒之间（0=禁用）")
    if dice_reveal_mode is not None and dice_reveal_mode not in {"auto", "click"}:
        raise ValueError("骰子揭示方式仅支持 auto 或 click")
    if economy_reward_policy is not None:
        from src.engine.economy import normalize_reward_policy

        economy_state.require_writable(instance)
        if not isinstance(economy_reward_policy, dict):
            raise ValueError("奖励策略无效")
        if not str(economy_reward_policy.get("mode") or "").strip():
            economy_reward_policy = {}
        else:
            normalized = normalize_reward_policy(economy_reward_policy)
            if not normalized:
                raise ValueError("奖励策略无效（mode 或 auto_reward_cap 不合法）")
            economy_reward_policy = normalized

    if solo_mode is not None:
        game_config.replace_solo_mode(instance, bool(solo_mode))
    if entry_point is not None:
        game_config.replace_entry_point(instance, entry_point)
    if room_password is not None:
        room_access.set_room_password(instance, room_password)
    if gm_uid is not None:
        instance.gm_uid = gm_uid
    if luck_timeout_seconds is not None:
        module_hooks.record(instance, module_hooks.LUCK_TIMEOUT_SECONDS, int(luck_timeout_seconds))
    if dice_reveal_mode is not None:
        module_hooks.record(instance, module_hooks.DICE_REVEAL_MODE, dice_reveal_mode)
    if narrative_perspective is not None:
        instance.set_narrative_perspective(narrative_perspective)
    if economy_reward_policy is not None:
        economy_state.replace_reward_policy(instance, economy_reward_policy)
