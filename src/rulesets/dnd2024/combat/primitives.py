"""Shared identifiers, errors, and dice primitives for D&D 2024 combat stages."""

from __future__ import annotations

import re
from typing import Any


DICE_RE = re.compile(r"^(\d+)d(\d+)([+-]\d+)?$")
# 徒手打击的 canonical 攻击引用：它是每个 player-like actor 的天然攻击能力，
# 不是捆绑物品，因此没有 "item:" 前缀，也永远不写入 equipment.item_refs。
UNARMED_STRIKE_REF = "unarmed_strike"
# 狂暴至多持续 10 分钟；一轮 6 秒，即 100 轮。
RAGE_MAX_ROUNDS = 100
# 掷先攻时可以选择使用的开场能力。声明只能出现在 combat.start 的
# ``initiative_choices`` 里，且只能表达"用不用"，不能带任何数量或骰值。
INITIATIVE_CHOICES = frozenset({"uncanny_metabolism", "persistent_rage"})
# 15 级持久狂怒的狂暴用这个 duration 记账：它不靠每回合续期维持，
# 只被 Unconscious 与真实穿上重甲提前结束。
PERSISTENT_RAGE_DURATION = "persistent_rage"

# 怪物尺寸（S0-MM1）。顺序即大小序：比较只能走 ``size_at_most``，不要在
# 各处自己比较字符串——"large 比 medium 大"这种事实只应该写一次。
SIZE_CODES = ("tiny", "small", "medium", "large", "huge", "gargantuan")


def size_at_most(size: str, limit: str) -> bool:
    """Whether ``size`` is no larger than ``limit``; unknown is always False.

    尺寸未知时一律不满足上限（fail closed）：Trip 只对 Large 或更小生效，
    Repelling Blast 只推 Large 或更小的目标——"不知道多大"不是"够小"。
    """

    if size not in SIZE_CODES or limit not in SIZE_CODES:
        return False
    return SIZE_CODES.index(size) <= SIZE_CODES.index(limit)
# 18 级卓越防御：一条自带"除 Force 外全部抗性"的条件。它既不是 ``combat``
# （战斗结束不等于一分钟到）也不是回合域（它按分钟计时），因此有自己的
# duration，并靠 duration_minutes + 到期轮数两个上限之一结束。
SUPERIOR_DEFENSE_CONDITION = "superior_defense"
SUPERIOR_DEFENSE_DURATION = "superior_defense"
# 回合开始窗口的关闭事件。窗口本身存在 economy 上（它不消耗任何经济，只是
# "此刻允许支付"的记号），所以关闭它同样是一条事件，而不是某处直接改状态。
TURN_WINDOW_CLOSED_EVENT = "dnd2024.turn_window.closed"
# 驱散亡灵与神圣感知各自的条件域。前者是"每个受影响生物身上的一分钟状态"
# （目标自己的回合按轮推进），后者是"施法者自己身上的一段侦测"（按分钟计时，
# 失能即结束），两者都不属于 combat 域，所以各有一个 duration。
TURN_UNDEAD_DURATION = "turn_undead"
TURN_UNDEAD_MAX_ROUNDS = 10
DIVINE_SENSE_CONDITION = "divine_sense"
DIVINE_SENSE_DURATION = "divine_sense"
# 神圣武器绑定的是一把具体的武器引用，因此它是"持有者身上 + weapon_ref"的
# 一段状态，按世界时钟计时（十分钟），也按装备协调结束（不再携带该武器）。
SACRED_WEAPON_CONDITION = "sacred_weapon"
SACRED_WEAPON_DURATION = "sacred_weapon"
# 守护至圣斩给的是"下一次施放至圣斩之后到你下一回合开始"的一段掩蔽。它挂在
# 圣武士自己身上而不是逐个盟友身上：掩蔽的范围是灵光，离开就失去、回到灵光
# 且未到期就恢复，逐个复制到盟友身上反而做不到这一点。
SMITE_OF_PROTECTION_CONDITION = "smite_of_protection"
SMITE_OF_PROTECTION_DURATION = "actor_turn_start"
# 斥退敌人给的是"一分钟或直到受伤"的 Frightened，另加一条"每回合只能三选一"
# 的限制。一分钟在回合制里同样是十个自己的回合，所以和 Turn Undead 共用同一套
# 计数逻辑，而不是另立一个计时器。
ABJURE_FOES_DURATION = "abjure_foes"
TURNS_SCOPED_DURATIONS = frozenset({TURN_UNDEAD_DURATION, ABJURE_FOES_DURATION})
# 只有世界时钟能结束的效果：它们不参与回合域到期，也不随战斗结束消失。
WORLD_CLOCK_DURATIONS = frozenset({DIVINE_SENSE_DURATION, SACRED_WEAPON_DURATION})


def combat_turn_slots(combat: dict[str, Any]) -> list[dict[str, Any]]:
    """The authoritative turn schedule: one slot per turn, not per actor.

    A Thief's Reflexes grants a second turn in round one, so "who acts now"
    cannot be answered from the deduplicated ``initiative`` list.  ``initiative``
    keeps its meaning ("who is in this combat"); ``turn_slots`` answers the
    schedule.  Combats started before this slice carry no slots and fall back to
    one slot per Initiative entry, so their order is unchanged by a read.
    """

    slots = combat.get("turn_slots")
    if isinstance(slots, list):
        rows = [
            slot for slot in slots
            if isinstance(slot, dict) and str(slot.get("actor_id") or "")
        ]
        if rows:
            return rows
    return [
        {
            "slot_id": f"{actor_id}#1", "actor_id": str(actor_id),
            "initiative_total": 0, "kind": "normal",
        }
        for actor_id in combat.get("initiative") or []
    ]


def current_turn_slot(combat: dict[str, Any]) -> dict[str, Any] | None:
    """The slot whose turn it is, or ``None`` when the schedule is inconsistent."""

    slots = combat_turn_slots(combat)
    index = int(combat.get("turn_index", 0) or 0)
    if not slots or not 0 <= index < len(slots):
        return None
    return slots[index]


def rider_attack_kind(weapon: dict[str, Any]) -> str:
    """Which ``attack_kinds`` value this weapon qualifies as.

    The three hit riders (Radiant, Divine, Primal) declare overlapping but not
    identical kinds: Radiant Strikes names "melee weapon or Unarmed Strike",
    Divine Strike just "a weapon", Primal Strike adds a Beast form's attack.
    Validation and resolution both need the same classification, so it lives
    here rather than being guessed twice.
    """

    if bool(weapon.get("unarmed")):
        return "unarmed_strike"
    return "ranged_weapon" if bool(weapon.get("ranged")) else "melee_weapon"
INTENT_TYPES = frozenset({
    "encounter.ready", "encounter.unready", "combat.start", "combat.end", "combat.message",
    # C2A：GM 声明某个目标当前处于什么掩护等级（剧场模式）。
    "combat.set_cover",
    "combat.set_obscurement",
    "attack", "cast_spell", "activate_item", "move", "dash", "dodge", "disengage", "hide", "end_turn",
    "death_save", "stabilize", "decision.resolve",
    # 职业特性提供的战斗能力：Combat 只认 capability id、动作/资源成本与底层
    # canonical action，"哪个职业在第几级授予它" 由 feature boundary 回答。
    "class_capability",
    # F19：把标记的额外伤害转给 30 尺内的第二个目标。
    "superior_hunters_prey",
})
# 归零前的存活决策（Relentless Rage）：伤害已被掷出并挂起，HP 尚未改变。
# 它和其它 pending decision 一样由 resolution 结算、reducer 记账，区别只在
# 开窗者是 reducer —— 只有它知道"扣完临时 HP 之后是否真的到 0"。
RELENTLESS_RAGE_DECISION = "relentless_rage"
# 该决策只能写回这一处角色特性状态；未知键必须 fail closed，不能变成通用
# character 写入口。同一个闭集也是 combat actor view 的投影范围：战斗读不到
# 的状态，reducer 就不该写，休息引擎也不该写第二种形状。
FEATURE_STATE_KEYS = {
    "relentless_rage": frozenset({"next_dc"}),
    "fiendish_resilience": frozenset({"damage_type"}),
}


class CombatIntentError(ValueError):
    """Raised when a combat intent or state is structurally invalid."""


def canonical(sheet: dict[str, Any]) -> dict[str, Any]:
    nested = sheet.get("ruleset_character")
    if isinstance(nested, dict):
        return nested
    if sheet.get("rule_binding"):
        return sheet
    raise CombatIntentError("professional character is missing canonical state")


def player_actor(uid: str) -> str:
    return f"player:{uid}"


def companion_actor(companion_id: str) -> str:
    return f"companion:{companion_id}"


def enemy_actor(enemy_id: str) -> str:
    return f"enemy:{enemy_id}"


def actor_kind(actor_id: str) -> tuple[str, str]:
    if actor_id.startswith("player:"):
        return "player", actor_id.removeprefix("player:")
    if actor_id.startswith("companion:"):
        return "companion", actor_id.removeprefix("companion:")
    if actor_id.startswith("enemy:"):
        return "enemy", actor_id.removeprefix("enemy:")
    return "", ""


def actor_side(kind: str) -> str:
    """kind → 阵营的统一入口；敌我判断必须基于 side，而不是 kind。"""
    return {"player": "party", "companion": "party", "enemy": "enemy"}.get(kind, "")


def roll(formula: str, rng: Any, *, critical: bool = False) -> tuple[int, list[int]]:
    match = DICE_RE.fullmatch(str(formula or ""))
    if match is None:
        raise CombatIntentError(f"unsupported dice formula: {formula}")
    count, sides, bonus = (int(value or 0) for value in match.groups())
    if critical:
        count *= 2
    rolls = [int(rng.randint(1, sides)) for _ in range(count)]
    return sum(rolls) + bonus, rolls
