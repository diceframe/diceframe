"""Shared identifiers, errors, and dice primitives for D&D 2024 combat stages."""

from __future__ import annotations

import re
from collections.abc import Mapping
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
#: S0-F30：恐惧来自 Berserker 的特性而不是圣武士的引导神力，时长同为一分钟，
#: 但解除条件与免疫来源不同，所以它是自己的 duration 而不是复用上面那条。
INTIMIDATING_PRESENCE_DURATION = "intimidating_presence"


def world_minutes(instance: Any) -> int:
    """The world clock as an absolute minute; 0 when unset."""

    from src.engine.world_state import clock_to_minutes, world_clock

    minutes = clock_to_minutes(world_clock(getattr(instance, "world_state", None)))
    return minutes if isinstance(minutes, int) and minutes >= 0 else 0
TURNS_SCOPED_DURATIONS = frozenset({
    TURN_UNDEAD_DURATION, ABJURE_FOES_DURATION, INTIMIDATING_PRESENCE_DURATION,
})
# 只有世界时钟能结束的效果：它们不参与回合域到期，也不随战斗结束消失。
WORLD_CLOCK_DURATIONS = frozenset({DIVINE_SENSE_DURATION, SACRED_WEAPON_DURATION})


def combat_turn_slots(combat: Mapping[str, Any]) -> list[dict[str, Any]]:
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


def current_turn_slot(combat: Mapping[str, Any]) -> dict[str, Any] | None:
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
    # 一次裸属性检定。成本由调用方声明（可能是动作、附赠动作或免费）：
    # 规则里它既可能是"花动作开锁"，也可能是对抗/失败窗口的一部分。
    "ability_check",
    # 职业特性提供的战斗能力：Combat 只认 capability id、动作/资源成本与底层
    # canonical action，"哪个职业在第几级授予它" 由 feature boundary 回答。
    "class_capability",
    # F19：把标记的额外伤害转给 30 尺内的第二个目标。
    "superior_hunters_prey",
    # S0-MM3b：一次动作里做若干次攻击。它不是"多打几次 attack"——那会各付
    # 一次动作；也不是一次的别名——它的结果是一个批次里的 N 次攻击。
    "multiattack",
    # S0-MM3c：目标掷一次豁免，失败吃全额、成功吃（数据声明的）半伤。
    # 掷骰由服务端做，客户端只提交动作 id。
    "save_action",
    # S0-MM3e：从条件内部解除它。要求（DC / 允许的检定 / 成本）全部来自
    # 状态：实例给 DC，注册表给形状。客户端只能选"用哪个能力挣"。
    "escape",
    # 起身：花一半速度解除自己的 Prone。不是动作，所以与 escape 分开。
    "stand_up",
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


# S0-MM3a：一次攻击可以打出两段伤害（`2d6+4 slashing plus 1d6 fire`），
# 每一段各自过抗性/免疫/易伤。契约收结构化数组；旧形状（一条 damage 字符串
# 加一个 damage_type）继续可读——冒险包、AI 临时敌人与形态数据都是旧形状。
MAX_ATTACK_DAMAGE_PARTS = 2
# 命中之后的附带效果。`prone` 与 `condition=prone` 表达同一件事，但 SRD 有
# 59 处击倒，把它单列成一种 kind 才能让数据与测试都说"击倒"而不是"一个恰好
# 叫 prone 的条件"。
RIDER_KINDS = frozenset({"condition", "grapple", "push", "prone"})
# 条件名与 `condition` kind 的差别：`grapple` 施加的条件是固定的。
GRAPPLE_CONDITION = "grappled"
MAX_ATTACK_RIDERS = 3
RIDER_PUSH_MINIMUM_FT = 5
RIDER_PUSH_MAXIMUM_FT = 60
# 附带效果能声明的时长。闭集，因为只有这几个被 ``reducer._expire_conditions``
# 真正兑现；写一个没人读的时长等于给数据一个"永远不会到期"的假承诺。
RIDER_DURATIONS = frozenset({
    "combat", "target_turn_start", "target_turn_end", "attacker_next_turn_start",
})
# 规则的 15 个标准条件码。校验用它回答"这是不是一个条件名"；某个条件是否
# 已经有机械落点，由战斗目录的 ``conditions`` 注册表回答（两者不混用）。
CONDITION_CODES = frozenset({
    "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned", "prone",
    "restrained", "stunned", "unconscious",
})
# S0-MM3b：Multiattack 是一次动作里的若干次攻击。上限 6 不是规则数字，而是
# 契约的健全性边界：SRD 里最多 5 击，超过它的数据只可能是解析错了。
MULTIATTACK_MAX_ATTACKS = 6
MULTIATTACK_KINDS = frozenset({"multiattack"})
# 生物内别名解析：只在"这只生物自己的攻击表"里按 id → 别名 → 名称找。
# 全局匹配会让"Rend"同时指向三种不同生物的攻击。
def resolve_attack_ref(
    attacks: list[dict[str, Any]], ref: str,
) -> dict[str, Any] | None:
    """The attack one Multiattack part names, searched inside this creature only."""

    wanted = str(ref or "")
    if not wanted:
        return None
    for attack in attacks:
        if str(attack.get("id") or "") == wanted:
            return attack
    lowered = wanted.lower()
    for attack in attacks:
        if any(str(alias).lower() == lowered for alias in attack.get("aliases") or []):
            return attack
    for attack in attacks:
        if str(attack.get("name") or "").lower() == lowered:
            return attack
    return None


def attack_damage_parts(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The damage instances this attack profile deals, in declaration order.

    Two shapes reach one reader: the structured ``damage: [{dice, type}]`` the
    catalog now ships, and the flat ``damage: "1d6+2"`` + ``damage_type`` still
    produced by adventure packages and AI encounters.  Returning a list for both
    keeps every consumer (resolution, the opportunity-attack estimate, the
    view) from branching on the shape.
    """

    raw = profile.get("damage")
    parts: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            parts.append({
                "dice": str(row.get("dice") or ""),
                "type": str(row.get("type") or ""),
            })
        return parts
    text = str(raw or "")
    if text:
        parts.append({
            "dice": text,
            # 旧形状可以不带类型（AI 临时敌人与模块怪物档案都不写这一栏）。
            # 缺省用 bludgeoning 是这条路径一直以来的口径；留空会让
            # ``_resist_damage`` 认为"没有类型"，等于绕过全部抗性与免疫。
            "type": str(profile.get("damage_type") or "bludgeoning"),
        })
    return parts


def attack_riders(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The riders a hit from this profile applies, in declaration order."""

    raw = profile.get("riders")
    if not isinstance(raw, list):
        return []
    return [dict(row) for row in raw if isinstance(row, dict)]


def normalize_attack_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Store one attack profile in the structured shape.

    ``combat.start`` is the one place where the two accepted input shapes are
    collapsed: after it, every attack in the authoritative state carries
    ``damage`` as a list, so a reader never has to ask which era the data came
    from.  The legacy ``damage_type`` becomes the first part's type and is not
    kept beside the list — one question, one answer.
    """

    normalized = {
        key: value for key, value in profile.items()
        if key not in {"damage", "damage_type"}
    }
    normalized["damage"] = attack_damage_parts(profile)
    if "riders" in profile:
        normalized["riders"] = attack_riders(profile)
    return normalized


def rider_condition(rider: Mapping[str, Any]) -> str:
    """Which condition a rider applies; ``""`` when it applies none.

    ``push`` moves the target and grants nothing, so it is the one kind that
    answers empty — the caller has to treat that as "no condition", not as a
    missing field.
    """

    kind = str(rider.get("kind") or "")
    if kind == "condition":
        return str(rider.get("condition") or "")
    if kind == "prone":
        return "prone"
    if kind == "grapple":
        return GRAPPLE_CONDITION
    return ""


# S0-MM3d：八个高频被动特质。闭集 id，一个特质一种拼写；参数与 id 一起
# 声明，未知 id 或未知参数都 fail closed —— 加载成功但从不生效的特质，比
# 拒绝加载更糟。
TRAIT_IDS = frozenset({
    "magic_resistance", "amphibious", "legendary_resistance", "pack_tactics",
    "spider_climb", "water_breathing", "flyby", "amorphous",
})
# 每个特质允许的参数。空集表示"这个特质就是事实本身"，没有可调的东西。
TRAIT_PARAMS: dict[str, frozenset[str]] = {
    "magic_resistance": frozenset(),
    "amphibious": frozenset(),
    "legendary_resistance": frozenset({"per_day"}),
    "pack_tactics": frozenset(),
    "spider_climb": frozenset(),
    "water_breathing": frozenset(),
    "flyby": frozenset(),
    "amorphous": frozenset(),
}
# 传奇抗性的默认次数：SRD 的"3/日（巢穴 4/日）"取前一条，巢穴变体属于
# 巢穴行动，本片不做。
LEGENDARY_RESISTANCE_DEFAULT_PER_DAY = 3


def trait_ids(traits: Any) -> frozenset[str]:
    """The declarative trait ids a stat block carries."""

    if not isinstance(traits, list):
        return frozenset()
    return frozenset(
        str(row.get("id") or "")
        for row in traits
        if isinstance(row, dict) and row.get("id")
    )


def trait_params(traits: Any, trait_id: str) -> dict[str, Any]:
    """One trait's declared parameters (empty when the trait is a bare fact)."""

    if not isinstance(traits, list):
        return {}
    for row in traits:
        if isinstance(row, dict) and str(row.get("id") or "") == trait_id:
            params = row.get("params")
            return dict(params) if isinstance(params, dict) else {}
    return {}


# S0-MM3e：条件自带"怎么解除"。类型面在注册表（能不能挣脱、用什么检定、
# 花什么成本），数值面在实例（`escape_dc` 来自施加者）。两者缺一不可：
# 只在注册表写 DC 会让不同擒抱者有同一个 DC，只在实例写又会让"能不能挣脱"
# 变成施加者的自由裁量。
def condition_escapes(
    registry: Mapping[str, Any], conditions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Every carried condition that declares an escape, with its DC.

    A declaration without ``escape_dc`` on the instance is *not* listed: an
    attempt whose DC nobody declared cannot be made, and inventing one here
    would be the engine deciding a number the stat block never printed.
    """

    found: list[dict[str, Any]] = []
    for condition_id, entry in conditions.items():
        declared = (registry.get(str(condition_id)) or {}).get("escape")
        if not isinstance(declared, dict):
            continue
        instance = entry if isinstance(entry, dict) else {}
        dc = instance.get("escape_dc")
        if isinstance(dc, bool) or not isinstance(dc, int) or dc <= 0:
            continue
        found.append({
            "condition": str(condition_id),
            "cost": str(declared.get("cost") or "action"),
            "checks": [dict(row) for row in declared.get("checks") or []],
            "escape_dc": dc,
            "source_actor_id": str(instance.get("source_actor_id") or ""),
        })
    return found


def stand_up_declaration(
    registry: Mapping[str, Any], conditions: Mapping[str, Any],
) -> dict[str, Any] | None:
    """The movement price of ending a carried condition on yourself."""

    for condition_id in conditions:
        declared = (registry.get(str(condition_id)) or {}).get("stand_up")
        if not isinstance(declared, dict):
            continue
        fraction = int(declared.get("fraction") or 0)
        if fraction <= 0:
            continue
        return {"condition": str(condition_id), "fraction": fraction}
    return None
