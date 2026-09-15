"""Currency base-unit 语义迁移使用的纯缩放辅助函数。

迁移原则：只有 base_unit 语义发生变更的规则才缩放数据（例如内置 CoC 从
「1 amount = 1 美元」升级为「1 amount = 1 美分」时旧数据 ×100）。这里只提供
确定性的字段缩放，不判断规则身份、不做幂等判断（版本化迁移入口负责只执行一次）。
"""

from __future__ import annotations

from typing import Any

_SHEET_AMOUNT_KEYS = ("currency", "gold")


def _scaled_int(value: Any, factor: int) -> Any:
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    return value * factor


def scale_character_sheet(sheet: dict[str, Any], factor: int) -> None:
    """Scale canonical currency fields on one character sheet in place."""

    if not isinstance(sheet, dict):
        return
    currency = sheet.get("currency")
    if isinstance(currency, dict):
        if "amount" in currency:
            currency["amount"] = _scaled_int(currency.get("amount"), factor)
        elif isinstance(sheet.get("gold"), int) and not isinstance(sheet["gold"], bool):
            currency["amount"] = sheet["gold"] * factor
    sheet["gold"] = _scaled_int(sheet.get("gold"), factor)


def scale_player_sheets(players: Any, factor: int) -> None:
    """Scale ``players[uid].character_sheet`` mappings on a save payload."""

    if not isinstance(players, dict):
        return
    for player in players.values():
        if isinstance(player, dict):
            sheet = player.get("character_sheet")
            if isinstance(sheet, dict):
                scale_character_sheet(sheet, factor)


def scale_player_rollback_snapshots(snapshots: Any, factor: int) -> None:
    """Scale ``{uid: character_sheet}`` rollback snapshots in place."""

    if not isinstance(snapshots, dict):
        return
    for sheet in snapshots.values():
        scale_character_sheet(sheet, factor)


def scale_economy_aggregate(economy: Any, factor: int) -> None:
    """Scale every canonical amount inside the persisted economy aggregate.

    Proposals/outcomes 携带金额；transactions 的每条 entry 同时包含 delta 与
    绝对 before/after 余额镜像，全部属于 canonical base-unit 数值，必须一起缩放，
    否则回滚重放 before-image 时会恢复出错误余额。
    """

    if not isinstance(economy, dict):
        return
    for proposal in economy.get("proposals") or []:
        if isinstance(proposal, dict):
            proposal["amount"] = _scaled_int(proposal.get("amount"), factor)
    for outcome in economy.get("outcomes") or []:
        if isinstance(outcome, dict):
            outcome["amount"] = _scaled_int(outcome.get("amount"), factor)
    for transaction in economy.get("transactions") or []:
        if not isinstance(transaction, dict):
            continue
        for entry in transaction.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            for key in ("delta", "before", "after"):
                if key in entry:
                    entry[key] = _scaled_int(entry.get(key), factor)


def scale_reward_policy(policy: Any, factor: int) -> None:
    """Scale ``auto_reward_cap``（canonical base-unit amount）in place."""

    if not isinstance(policy, dict) or "auto_reward_cap" not in policy:
        return
    policy["auto_reward_cap"] = _scaled_int(policy.get("auto_reward_cap"), factor)


def scale_game_state_payload_for_base_unit_change(
    payload: dict[str, Any],
    factor: int,
) -> None:
    """Scale every persisted canonical currency amount on one save payload.

    覆盖：角色卡余额、经济聚合（提案/流水/结果）、奖励策略上限、
    顶层 round_start_snapshot 与日志中的回合快照（回滚/恢复的 authority）。
    """

    scale_player_sheets(payload.get("players"), factor)
    scale_economy_aggregate(payload.get("economy"), factor)
    scale_reward_policy(payload.get("economy_reward_policy"), factor)
    scale_player_rollback_snapshots(payload.get("round_start_snapshot"), factor)
    log = payload.get("log")
    if isinstance(log, list):
        for entry in log:
            if not isinstance(entry, dict):
                continue
            scale_player_rollback_snapshots(entry.get("round_start_snapshot"), factor)
            scale_player_rollback_snapshots(entry.get("pre_state_snapshot"), factor)
