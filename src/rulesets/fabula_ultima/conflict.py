"""Fabula Ultima 冲突场景（Conflict Scene）引擎。

只负责规则语义：先攻、轮次、行动、伤害、相性、危机、命刻、归零。
状态存在 ``ruleset_runtime.state["fabula_ultima"]``，由 runtime 统一读写。
"""

from __future__ import annotations

from typing import Any

from src.rulesets.fabula_ultima.dice import fu_high_roll
from src.rulesets.fabula_ultima.content import (
    ATTRIBUTE_NAMES,
    CONDITION_ATTRIBUTES,
    CONDITION_NAMES,
)
from src.rulesets.fabula_ultima.i18n import render
from src.rulesets.fabula_ultima.runtime_messages import ACTION_LABELS, AFFINITY_LABELS, MESSAGES

ATTR_KEYS = ("dex", "ins", "mig", "wlp")

#: 行动目录（核心书 p68）。
ACTIONS = (
    {"id": "attack", "needs_target": True},
    {"id": "defend", "needs_target": False},
    {"id": "hinder", "needs_target": True},
    {"id": "objective", "needs_target": False},
    {"id": "investigate", "needs_target": False},
    {"id": "resource", "needs_target": False},
    {"id": "equip", "needs_target": False},
    {"id": "skill", "needs_target": False},
)


def action_label(language: object, action_id: str) -> str:
    """行动的显示名；``id`` 才是 canonical 标识。"""
    return render(language, ACTION_LABELS, action_id)


def _die_size(value: Any) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return 6
    return max(6, min(12, size))


def effective_die(sheet: dict[str, Any], key: str, conditions: list[str]) -> int:
    """属性骰在下异常状态后的有效骰面（每层 -1 档，最低 d6）。"""
    attrs = sheet.get("attributes") or {}
    size = _die_size(attrs.get(key, 6))
    steps = 0
    for condition in conditions:
        if key in CONDITION_ATTRIBUTES.get(condition, ()):
            steps += 1
    order = (6, 8, 10, 12)
    index = order.index(size) if size in order else 0
    return order[max(0, index - steps)]


def roll_pair(rng: Any, size_a: int, size_b: int) -> list[int]:
    return [rng.randint(1, size_a), rng.randint(1, size_b)]


def verdict_of(rolls: list[int], total: int, dc: int) -> str:
    if rolls and rolls[0] == rolls[1]:
        if rolls[0] == 1:
            return "大失败"
        if rolls[0] >= 6:
            return "大成功"
    return "成功" if total >= dc else "失败"


def affinity_multiplier(affinities: dict[str, Any], damage_type: str, language: object = "") -> tuple[float, str]:
    value = str(affinities.get(damage_type) or "")
    if value == "weak":
        return 2.0, render(language, AFFINITY_LABELS, value)
    if value == "resist":
        return 0.5, render(language, AFFINITY_LABELS, value)
    if value == "immune":
        return 0.0, render(language, AFFINITY_LABELS, value)
    if value == "absorb":
        return -1.0, render(language, AFFINITY_LABELS, value)
    return 1.0, ""


def build_enemy(template: dict[str, Any], enemy_id: str, *, name: str = "", language: object = "") -> dict[str, Any]:
    return {
        "id": enemy_id,
        "name": name or str(template.get("name") or enemy_id),
        "tier": str(template.get("tier") or "minor"),
        "hp": int(template.get("hp") or 20),
        "max_hp": int(template.get("hp") or 20),
        "defense": int(template.get("defense") or 10),
        "mdef": int(template.get("mdef") or 10),
        "initiative": int(template.get("initiative") or 8),
        "attacks": list(template.get("attacks") or [{"name": render(language, MESSAGES, "default_attack_name"), "attributes": ["mig", "mig"], "damage": 6}]),
        "skills": list(template.get("skills") or []),
        "skill_cooldowns": {},
        "affinities": dict(template.get("affinities") or {}),
        "conditions": [],
        "statuses": [],
        "buffs": [],
        "villain": bool(template.get("villain")),
        "fate": int(template.get("fate") or 0),
        "defeated": False,
    }


def new_participant(player_id: str) -> dict[str, Any]:
    return {
        "id": player_id,
        "conditions": [],
        "statuses": [],
        "buffs": [],
        "defending": False,
        "down": False,
        "used_action": False,
        "used_quick": False,
    }


def buff_total(row: dict[str, Any], stat: str) -> int:
    """某角色当前生效的增益总值（stat=defense/damage/check）。"""
    total = 0
    for buff in row.get("buffs") or []:
        if str(buff.get("stat") or "") == stat:
            total += int(buff.get("value") or 0)
    return total


def tick_row(row: dict[str, Any], step: int | None = None) -> None:
    """目标行动结束后结算持续回合；None 表示持久状态，不递减。"""
    statuses = row.get("statuses") or []
    for status in statuses:
        if step is not None and status.get("created_step") == step:
            continue
        remaining = status.get("remaining")
        if remaining is None:
            continue
        status["remaining"] = int(remaining or 0) - 1
    expired = [
        status for status in statuses
        if status.get("remaining") is not None
        and int(status.get("remaining") or 0) <= 0
        and not (step is not None and status.get("created_step") == step)
    ]
    if expired:
        row["statuses"] = [status for status in statuses if status not in expired]
        for status in expired:
            status_id = str(status.get("id") or "")
            if not any(str(item.get("id") or "") == status_id for item in row["statuses"]):
                conditions = row.get("conditions") or []
                if status_id in conditions:
                    conditions.remove(status_id)
    buffs = row.get("buffs") or []
    for buff in buffs:
        if step is not None and buff.get("created_step") == step:
            continue
        buff["remaining"] = int(buff.get("remaining") or 0) - 1
    row["buffs"] = [
        buff for buff in buffs
        if int(buff.get("remaining") or 0) > 0
        or (step is not None and buff.get("created_step") == step)
    ]


def begin_turn(conflict: dict[str, Any]) -> str:
    """当前行动者回合开始：清空行动标记并结算状态。"""
    actor = current_actor(conflict)
    if not actor:
        return ""
    if actor.startswith("player:"):
        row = conflict["participants"].get(actor)
    else:
        row = conflict["enemies"].get(actor)
    if isinstance(row, dict):
        row["used_action"] = False
        row["used_quick"] = False
        row["defending"] = False
    return actor


def new_conflict(
    state: dict[str, Any],
    player_ids: list[str],
    enemies: list[dict[str, Any]],
    *,
    objective: dict[str, Any] | None,
    first_side: str,
    language: object = "",
) -> dict[str, Any]:
    conflict = {
        "status": "active",
        "round": 1,
        "step": 0,
        "manual_steps": True,
        "current": "",
        "first_side": first_side,
        "turn_index": 0,
        "participants": {
            f"player:{uid}": new_participant(f"player:{uid}")
            for uid in player_ids
        },
        "enemies": {enemy["id"]: enemy for enemy in enemies},
        "clocks": (
            {objective["id"]: {"name": objective.get("name") or render(language, MESSAGES, "default_clock_name"), "segments": int(objective.get("segments") or 6), "filled": 0}}
            if objective else {}
        ),
        "history": [],
    }
    # 先攻顺序在冲突开始时固定，倒下的敌人仍保留位置，避免索引漂移。
    conflict["order"] = _build_order(conflict)
    return conflict


def _build_order(conflict: dict[str, Any]) -> list[str]:
    players = list(conflict["participants"].keys())
    enemies = list(conflict["enemies"].keys())
    first_players = conflict.get("first_side") == "players"
    left, right = (players, enemies) if first_players else (enemies, players)
    order: list[str] = []
    for index in range(max(len(left), len(right))):
        if index < len(left):
            order.append(left[index])
        if index < len(right):
            order.append(right[index])
    return order


def order_for(conflict: dict[str, Any]) -> list[str]:
    stored = conflict.get("order")
    if isinstance(stored, list) and stored:
        return list(stored)
    return _build_order(conflict)


def current_actor(conflict: dict[str, Any]) -> str:
    if conflict.get("manual_steps"):
        return str(conflict.get("current") or "")
    order = order_for(conflict)
    if not order:
        return ""
    index = int(conflict.get("turn_index", 0) or 0) % len(order)
    return order[index]


def select_actor(conflict: dict[str, Any], actor: str, language: object = "") -> str:
    """GM 手动选择当前行动者（步骤制）。"""
    if not conflict.get("manual_steps"):
        return current_actor(conflict)
    row = (
        conflict["participants"].get(actor)
        if str(actor).startswith("player:")
        else conflict["enemies"].get(actor)
    )
    if not isinstance(row, dict):
        raise KeyError(actor)
    if row.get("defeated") or row.get("down"):
        raise ValueError(render(language, MESSAGES, "unit_cannot_act"))
    conflict["current"] = str(actor)
    begin_turn(conflict)
    return str(actor)


def finish_step(conflict: dict[str, Any]) -> int:
    actor = current_actor(conflict)
    if actor:
        row = (
            conflict["participants"].get(actor)
            if actor.startswith("player:")
            else conflict["enemies"].get(actor)
        )
        if isinstance(row, dict):
            tick_row(row, step=int(conflict.get("step") or 0))
    conflict["step"] = int(conflict.get("step") or 0) + 1
    conflict["current"] = ""
    return int(conflict["step"])


def advance_turn(conflict: dict[str, Any]) -> dict[str, Any]:
    """推进到下一个行动者；倒下的单位跳过，但保留其固定位置。"""
    order = order_for(conflict)
    if not order:
        return {"kind": "conflict_end", "reason": "no_participants"}
    for _ in range(len(order)):
        conflict["turn_index"] = int(conflict.get("turn_index", 0) or 0) + 1
        if conflict["turn_index"] >= len(order):
            conflict["turn_index"] = 0
            conflict["round"] = int(conflict.get("round", 1) or 1) + 1
        actor = order[conflict["turn_index"]]
        row = (
            conflict["participants"].get(actor)
            if actor.startswith("player:")
            else conflict["enemies"].get(actor)
        ) or {}
        if row.get("defeated") or row.get("down"):
            continue
        return {"kind": "turn", "actor": actor}
    return {"kind": "conflict_end", "reason": "all_down"}


def _sheet_dice(sheet: dict[str, Any], keys: tuple[str, ...], conditions: list[str]) -> tuple[int, int]:
    first = effective_die(sheet, keys[0], conditions)
    second = effective_die(sheet, keys[1], conditions)
    return first, second


def attack_attributes(action: dict[str, Any], weapon: dict[str, Any] | None) -> tuple[str, str]:
    raw = action.get("attributes")
    if isinstance(raw, list) and len(raw) == 2 and all(str(item) in ATTR_KEYS for item in raw):
        return str(raw[0]), str(raw[1])
    if isinstance(weapon, dict) and isinstance(weapon.get("attributes"), list) and len(weapon["attributes"]) == 2:
        a, b = weapon["attributes"]
        if str(a) in ATTR_KEYS and str(b) in ATTR_KEYS:
            return str(a), str(b)
    return ("dex", "mig") if str(action.get("kind") or "") != "ranged" else ("dex", "ins")


def weapon_from_sheet(sheet: dict[str, Any]) -> dict[str, Any] | None:
    for item in sheet.get("equipment") or []:
        if isinstance(item, dict) and str(item.get("type") or "") == "weapon":
            return item
    return None
