"""Intent validation stage for D&D 2024 combat."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from src.engine.player_control import is_ai_controlled
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.content.contracts import CREATURE_TYPES

from ..character.attunement import (
    AttunementError,
    attuned_refs,
    canonical_item_ref,
    inventory_ref_counts,
    item_requires_attunement,
    owned_ref_counts,
)
from .action_adapter import is_supported_damage_formula
from ..features.models import ClassFeatureError
from ..forms import (
    FormState, FormStateError, bootstrap_known_forms, mode_for, restrictions,
)
from ..features.resources import (
    OVERCHANNEL_FEATURE_ID,
    OVERCHANNEL_OWNER,
    OVERCHANNEL_SPELL_CLASS,
    feature_usage_count,
    resource_state,
)
from ..items.activation import (
    ItemActivationError,
    validate_activation_declaration,
    validate_consumable_declaration,
)
from ..items.charges import (
    ChargesError,
    charges_of,
    validate_charges_declaration,
    validate_destroy_declaration,
)
from ..spells.font_of_magic import (
    FontOfMagicError,
    create_slot_plan,
    declaring_class_level,
    parse_created_slot_costs,
    slot_to_points_plan,
)
from .cover import COVER_LEVELS
from .obscurement import OBSCUREMENT_LEVELS
from .primitives import (
    CONDITION_CODES,
    DICE_RE,
    INITIATIVE_CHOICES,
    INTENT_TYPES,
    MAX_ATTACK_DAMAGE_PARTS,
    MAX_ATTACK_RIDERS,
    MULTIATTACK_MAX_ATTACKS,
    RELENTLESS_RAGE_DECISION,
    RIDER_DURATIONS,
    RIDER_KINDS,
    RIDER_PUSH_MAXIMUM_FT,
    RIDER_PUSH_MINIMUM_FT,
    LEGENDARY_RESISTANCE_DEFAULT_PER_DAY,
    SACRED_WEAPON_CONDITION,
    stand_up_declaration,
    UNARMED_STRIKE_REF,
    SIZE_CODES,
    TRAIT_IDS,
    TRAIT_PARAMS,
    CombatIntentError,
    actor_kind as _actor_kind,
    canonical as _canonical,
    rider_attack_kind,
)
from .check_modes import condition_flags, effective_speed, marked_target_id, owned_mark, vision_codes
from .hands import apply_swap
from .check_windows import (
    CHECK_WINDOW_DECISION,
    DAMAGE_WINDOW_DECISION,
    LUCKY_RESOURCE,
)
from .readiness import required_encounter_player_ids
from .reaction_spells import COUNTER_SPELL_DECISION, HIT_REACTION_DECISION
from .triggered_spells import HIT_SPELL_DECISION, free_cast_resource, hit_spell_decision


_TURN_DECISION_ERRORS = {
    HIT_SPELL_DECISION: "resolve the melee hit spell decision first",
    HIT_REACTION_DECISION: "waiting for the defender's reaction spell decision",
    COUNTER_SPELL_DECISION: "resolve the counter spell decision first",
    CHECK_WINDOW_DECISION: "resolve the pending check window first",
    DAMAGE_WINDOW_DECISION: "resolve the pending check window first",
    RELENTLESS_RAGE_DECISION: "resolve the relentless rage decision first",
}


# S0-MM1：敌人契约的已知字段。未知字段一律拒绝——它过去被静默忽略，
# 于是 GM 或导入器带来的 MM 字段"看起来进去了"，实际从没生效。
_ENEMY_KEYS = frozenset({
    "id", "name", "description", "profile_id", "source_ref", "creature_type",
    "hp", "max_hp", "armor_class", "speed", "speeds", "size", "position",
    "initiative_modifier", "counterspell_dc", "abilities", "saving_throws",
    "attacks", "senses", "resist_damage_types", "immune_damage_types",
    "vulnerable_damage_types", "condition_immunities", "cr", "xp",
    # S0-MM2：除特质与动作之外，stat block 的其余字段都要能进契约。
    "skills", "languages", "proficiency_bonus", "hit_dice", "subtype",
    "passive_perception",
    # S0-MM3b：动作容器。Multiattack 不是"攻击"，它有自己的一次动作预算。
    "actions",
    # S0-MM3d：特质容器。闭集 id，未知即拒绝。
    "traits",
})
# 速度档位：walk 是唯一被移动预算读的一档，其余四档本切片只投影（阶段三接）。
def _cr_value(label: str) -> float:
    text = str(label or "0")
    if "/" in text:
        numerator, denominator = text.split("/")[:2]
        return float(numerator) / float(denominator)
    return float(text)


_HIT_DICE_RE = re.compile(r"^(\d{1,2})d(\d{1,2})(?:[+-]\d{1,3})?$")
_SPEED_MODES = ("walk", "fly", "swim", "climb", "burrow")
# 感官档位：blindsight 已接入观察链；truesight/darkvision/tremorsense 只存不接。
_SENSE_MODES = ("darkvision", "blindsight", "tremorsense", "truesight")
# CR 闭集。分数只写到 1/2：MM 里没有比 1/8 更细的档。
_CHALLENGE_RATINGS = (
    ("0",), ("1/8",), ("1/4",), ("1/2",),
) + tuple((str(value),) for value in range(1, 31))
_CR_CODES = frozenset(code for group in _CHALLENGE_RATINGS for code in group)


def _normalize_speeds(enemy: dict[str, Any], label: str) -> dict[str, int | bool]:
    """Accept the old scalar and the structured form; return the structured one.

    旧形状 ``speed: 30`` 等价于 ``{"walk": 30}``。归一化放在装载期，是为了让
    事件与投影只有一种形状，而旧 intent / 旧存档继续可读。
    """

    raw = enemy.get("speeds")
    if raw is None:
        walk = enemy.get("speed", 30)
        if isinstance(walk, bool) or not isinstance(walk, int) or not 0 <= walk <= 200:
            raise CombatIntentError(f"{label} speed is invalid")
        return {"walk": walk}
    if not isinstance(raw, dict):
        raise CombatIntentError(f"{label} speeds must be an object")
    extra = sorted(set(raw) - set(_SPEED_MODES) - {"hover"})
    if extra:
        raise CombatIntentError(f"{label} speeds has unknown mode: {extra[0]!r}")
    speeds: dict[str, int | bool] = {}
    for mode in _SPEED_MODES:
        value = raw.get(mode, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 200:
            raise CombatIntentError(f"{label} speeds.{mode} is invalid")
        if value:
            speeds[mode] = value
    hover = raw.get("hover", False)
    if not isinstance(hover, bool):
        raise CombatIntentError(f"{label} speeds.hover must be a boolean")
    if hover and "fly" not in speeds:
        raise CombatIntentError(f"{label} speeds.hover requires a fly speed")
    if hover:
        speeds["hover"] = True
    if not any(mode in speeds for mode in _SPEED_MODES):
        raise CombatIntentError(f"{label} speeds must have at least one mode")
    return speeds


def _validate_size(enemy: dict[str, Any], label: str) -> None:
    if "size" not in enemy:
        # 旧数据没有这一栏：装载成功但记为未知，消费方一律 fail closed。
        return
    size = enemy.get("size")
    if not isinstance(size, str) or size not in SIZE_CODES:
        raise CombatIntentError(
            f"{label} size must be one of {list(SIZE_CODES)}"
        )


def _damage_type_list(enemy: dict[str, Any], key: str, label: str) -> set[str]:
    # 局部导入：catalog 反过来依赖 validation 的常量，模块级导入会成环。
    from .catalog import DAMAGE_TYPES

    raw = enemy.get(key)
    if raw is None:
        return set()
    if not isinstance(raw, list) or len(raw) > 20:
        raise CombatIntentError(f"{label} {key} must be a list of damage types")
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str) or value not in DAMAGE_TYPES:
            raise CombatIntentError(f"{label} {key} has an unknown damage type")
        if value in values:
            raise CombatIntentError(f"{label} {key} repeats {value}")
        values.append(value)
    return set(values)


def _validate_defenses(enemy: dict[str, Any], label: str) -> None:
    resist = _damage_type_list(enemy, "resist_damage_types", label)
    immune = _damage_type_list(enemy, "immune_damage_types", label)
    vulnerable = _damage_type_list(enemy, "vulnerable_damage_types", label)
    # 同一类型不可能既是抗性又是免疫（也不是"既是抗性又是易伤"）：那两种写法
    # 只会让结算顺序决定结果，而顺序不该由内容作者决定。
    for first, second, left, right in (
        (resist, immune, "resist", "immune"),
        (resist, vulnerable, "resist", "vulnerable"),
        (immune, vulnerable, "immune", "vulnerable"),
    ):
        overlap = sorted(first & second)
        if overlap:
            raise CombatIntentError(
                f"{label} lists {overlap[0]!r} as both {left} and {right}"
            )


def _validate_senses(enemy: dict[str, Any], label: str) -> None:
    raw = enemy.get("senses")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise CombatIntentError(f"{label} senses must be an object")
    extra = sorted(set(raw) - set(_SENSE_MODES))
    if extra:
        raise CombatIntentError(f"{label} senses has unknown kind: {extra[0]!r}")
    for kind, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 300:
            raise CombatIntentError(f"{label} senses.{kind} must be an integer range")


def _validate_challenge(enemy: dict[str, Any], label: str) -> None:
    if "cr" in enemy:
        cr = enemy.get("cr")
        if isinstance(cr, int) and not isinstance(cr, bool):
            cr = str(cr)
        if not isinstance(cr, str) or cr not in _CR_CODES:
            raise CombatIntentError(f"{label} cr must be one of the SRD challenge ratings")
    # XP 是 stat block 自带的事实（"Challenge 3 (700 XP)"），由内容提供；
    # 这里只约束形状，不推导：推导需要 DMG 的 CR→XP 表，而 SRD 不含该表。
    # 上界是 CR 30 = 155,000 XP（DMG 表）。
    if "xp" in enemy:
        xp = enemy.get("xp")
        if isinstance(xp, bool) or not isinstance(xp, int) or not 0 <= xp <= 155000:
            raise CombatIntentError(f"{label} xp must be a non-negative integer")
        if "cr" not in enemy:
            raise CombatIntentError(f"{label} xp requires a cr")


def _validate_condition_immunities(enemy: dict[str, Any], label: str) -> None:
    """条件免疫必须是良构的条件 id 列表（review #809 P2）。

    条件目录按需装载且不构成全局闭集，这里守住的是会写脏战斗状态的
    形状漏洞：裸字符串会在消费端被 ``set(str(code))`` 炸成单字符集合，
    dict/int 则直接进不了集合语义。未知但良构的 id 只是永远匹配不上
    的惰性条目，不构成状态污染。
    """
    raw = enemy.get("condition_immunities")
    if raw is None:
        return
    if not isinstance(raw, list):
        raise CombatIntentError(f"{label} condition_immunities must be an array")
    if not 0 <= len(raw) <= 40:
        raise CombatIntentError(f"{label} condition_immunities must list 0 to 40 codes")
    seen: set[str] = set()
    for code in raw:
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", code):
            raise CombatIntentError(
                f"{label} condition_immunities has an invalid condition id"
            )
        if code in seen:
            raise CombatIntentError(
                f"{label} condition_immunities lists {code!r} twice"
            )
        seen.add(code)


def validate_enemy_profiles(enemies: Any) -> None:
    """Public enemy profile validation shared by combat.start, preset
    loading, and AI temporary-encounter generation.

    Only one implementation of the safety boundary exists; callers must not
    copy or relax it.
    """

    if not isinstance(enemies, list) or not 1 <= len(enemies) <= 50:
        raise CombatIntentError("combat requires 1 to 50 enemies")
    seen: set[str] = set()
    for enemy in enemies:
        if not isinstance(enemy, dict):
            raise CombatIntentError("enemy must be an object")
        enemy_id = str(enemy.get("id") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", enemy_id) or enemy_id in seen:
            raise CombatIntentError("enemy id is invalid or duplicated")
        seen.add(enemy_id)
        unknown = sorted(set(enemy) - _ENEMY_KEYS)
        if unknown:
            raise CombatIntentError(
                f"enemy {enemy_id} has unknown field(s): {', '.join(unknown)}"
            )
        if len(str(enemy.get("name") or enemy_id)) > 120:
            raise CombatIntentError("enemy name is too long")
        _validate_size(enemy, f"enemy {enemy_id}")
        _validate_defenses(enemy, f"enemy {enemy_id}")
        _validate_senses(enemy, f"enemy {enemy_id}")
        _validate_challenge(enemy, f"enemy {enemy_id}")
        _validate_condition_immunities(enemy, f"enemy {enemy_id}")
        _normalize_speeds(enemy, f"enemy {enemy_id}")
        _validate_statblock_extras(enemy, f"enemy {enemy_id}")
        _validate_enemy_actions(enemy, f"enemy {enemy_id}")
        _validate_enemy_traits(enemy, f"enemy {enemy_id}")
        if not isinstance(enemy.get("creature_type", ""), str) or (enemy.get("creature_type") and enemy["creature_type"] not in CREATURE_TYPES):
            raise CombatIntentError("enemy creature_type must be a canonical creature type")
        # ``speeds`` 在场时旧标量 ``speed`` 不再参与校验：结构形式才是权威，
        # 两者都校验会让"给了 speeds 就必须同时给 speed"变成隐式必填。
        legacy_speed = () if "speeds" in enemy else (("speed", 0, 200),)
        for field_name, minimum, maximum in (
            ("hp", 1, 10000), ("armor_class", 1, 40),
            *legacy_speed, ("position", -10000, 10000),
            ("initiative_modifier", -20, 30),
            # 反击法术能力：0 表示没有该项能力，缺省即 0。
            ("counterspell_dc", 0, 30),
        ):
            default = (
                30 if field_name in {"speed", "position"}
                else 0 if field_name in {"initiative_modifier", "counterspell_dc"} else None
            )
            value = enemy.get(field_name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise CombatIntentError(f"enemy {field_name} is invalid")
        attacks = enemy.get("attacks")
        # 0 是合法形状：Giant Fly / Seahorse 在 SRD 里本来就没有攻击，
        # 但它们是合法形态（德鲁伊能变成海马）。没有攻击的敌人只是永远
        # 没有 attack 意图，而不是数据错误。
        if not isinstance(attacks, list) or not 0 <= len(attacks) <= 20:
            raise CombatIntentError("enemy requires 0 to 20 attacks")
        attack_ids: set[str] = set()
        for attack in attacks:
            if not isinstance(attack, dict):
                raise CombatIntentError("enemy attack profile is invalid")
            _validate_attack_profile(attack, attack_ids)


def _validate_damage_formula(formula: Any) -> None:
    """One damage instance: a dice formula or a flat amount the AST can read.

    公式层（``formula_node_from_dice_formula``）本来就接受固定值（"1 点穿刺"
    这类 Tiny 野兽），校验层不该比它更严——否则目录里一半的 Animals 进不来。
    """

    text = str(formula or "")
    dice_match = DICE_RE.fullmatch(text)
    if dice_match is not None:
        dice_count = int(dice_match.group(1))
        dice_sides = int(dice_match.group(2))
        if not 1 <= dice_count <= 100 or not 2 <= dice_sides <= 1000:
            raise CombatIntentError("enemy attack damage dice are invalid")
        return
    if not is_supported_damage_formula(text):
        raise CombatIntentError("enemy attack damage is invalid")
    if not 1 <= int(text) <= 1000:
        raise CombatIntentError("enemy attack flat damage is invalid")


def _validate_attack_damage(attack: dict[str, Any]) -> None:
    """S0-MM3a: one to two damage instances, structured or legacy.

    The two shapes are alternatives, not layers: an attack that carries both
    would leave two answers to "how much damage does this do", so it is refused
    rather than resolved by precedence.
    """

    raw = attack.get("damage")
    legacy_type = attack.get("damage_type")
    # 局部导入：catalog 反向依赖本模块的常量，模块级导入会成环。
    from .catalog import DAMAGE_TYPES
    if legacy_type is not None and (
        not isinstance(legacy_type, str) or legacy_type not in DAMAGE_TYPES
    ):
        raise CombatIntentError("enemy attack damage_type is invalid")
    if isinstance(raw, list):
        if not 1 <= len(raw) <= MAX_ATTACK_DAMAGE_PARTS:
            raise CombatIntentError("enemy attack damage must list 1 or 2 parts")
        if legacy_type is not None:
            raise CombatIntentError(
                "enemy attack damage is both a list and a legacy damage_type"
            )
        for row in raw:
            if not isinstance(row, dict) or set(row) - {"dice", "type"}:
                raise CombatIntentError("enemy attack damage part is invalid")
            damage_type = row.get("type")
            if not isinstance(damage_type, str) or damage_type not in DAMAGE_TYPES:
                raise CombatIntentError("enemy attack damage part type is invalid")
            _validate_damage_formula(row.get("dice"))
        return
    if isinstance(raw, str):
        _validate_damage_formula(raw)
        return
    # 都缺 —— 每次命中要打多少点没有答案，不能默认 0。
    raise CombatIntentError("enemy attack requires damage")


def _validate_rider(rider: dict[str, Any], label: str) -> None:
    """S0-MM3a: what a hit does beyond damage.

    One closed set of kinds, each with exactly the fields it needs.  A rider
    that names a condition outside the canonical vocabulary, a save outside the
    six abilities, or a push distance outside 5–60 ft is a data error the
    engine must not paper over.
    """

    kind = str(rider.get("kind") or "")
    if kind not in RIDER_KINDS:
        raise CombatIntentError(f"{label} rider kind is invalid")
    from .catalog import SAVE_ABILITIES

    allowed = {"kind", "save", "max_target_size", "duration"}
    if kind == "condition":
        allowed.add("condition")
    if kind == "grapple":
        allowed.add("escape_dc")
    if kind == "push":
        allowed.add("distance")
    if set(rider) - allowed:
        raise CombatIntentError(f"{label} rider has unknown field(s)")
    if kind == "push":
        distance = rider.get("distance")
        if (
            isinstance(distance, bool) or not isinstance(distance, int)
            or not RIDER_PUSH_MINIMUM_FT <= distance <= RIDER_PUSH_MAXIMUM_FT
        ):
            raise CombatIntentError(f"{label} rider push distance is invalid")
    if kind == "grapple":
        escape_dc = rider.get("escape_dc")
        if isinstance(escape_dc, bool) or not isinstance(escape_dc, int) or not 1 <= escape_dc <= 30:
            raise CombatIntentError(f"{label} rider escape DC is invalid")
    if kind == "condition":
        condition = str(rider.get("condition") or "")
        if condition not in CONDITION_CODES:
            raise CombatIntentError(f"{label} rider condition is not canonical")
    size = rider.get("max_target_size")
    if size is not None and (
        not isinstance(size, str) or size not in SIZE_CODES
    ):
        raise CombatIntentError(f"{label} rider target size limit is invalid")
    duration = rider.get("duration")
    if duration is not None and duration not in RIDER_DURATIONS:
        raise CombatIntentError(f"{label} rider duration is not supported")
    save = rider.get("save")
    if save is not None:
        if not isinstance(save, dict) or set(save) - {"ability", "dc"}:
            raise CombatIntentError(f"{label} rider save is invalid")
        ability = save.get("ability")
        dc = save.get("dc")
        if not isinstance(ability, str) or ability not in SAVE_ABILITIES:
            raise CombatIntentError(f"{label} rider save ability is invalid")
        if isinstance(dc, bool) or not isinstance(dc, int) or not 1 <= dc <= 30:
            raise CombatIntentError(f"{label} rider save DC is invalid")


def _validate_attack_profile(attack: dict[str, Any], attack_ids: set[str]) -> None:
    """One attack profile: identity, reach, damage instances and riders."""

    attack_id = str(attack.get("id") or "")
    attack_bonus = attack.get("attack_bonus")
    normal_range = attack.get("range", 5)
    long_range = attack.get("long_range", normal_range)
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", attack_id)
        or attack_id in attack_ids
        or isinstance(attack_bonus, bool)
        or not isinstance(attack_bonus, int)
        or not -20 <= attack_bonus <= 30
        or isinstance(normal_range, bool)
        or not isinstance(normal_range, int)
        or not 1 <= normal_range <= 1000
        or isinstance(long_range, bool)
        or not isinstance(long_range, int)
        or long_range < normal_range
        or long_range > 2000
    ):
        raise CombatIntentError("enemy attack profile is invalid")
    _validate_attack_damage(attack)
    riders = attack.get("riders")
    if riders is not None:
        if not isinstance(riders, list) or not 0 <= len(riders) <= MAX_ATTACK_RIDERS:
            raise CombatIntentError("enemy attack riders must list 0 to 3 entries")
        for rider in riders:
            if not isinstance(rider, dict):
                raise CombatIntentError("enemy attack rider is invalid")
            _validate_rider(rider, "enemy attack")
    aliases = attack.get("aliases")
    if aliases is not None:
        # 生物内的别名：SRD 的 Multiattack 常用一个不存在的动作名指代组合
        # （"two Rend attacks" 而动作表里只有 Claw / Bite）。别名只在这只
        # 生物自己的攻击表里解析，禁止全局匹配。
        if (
            not isinstance(aliases, list) or not 1 <= len(aliases) <= 3
            or any(
                not isinstance(alias, str) or not alias.strip() or len(alias) > 40
                for alias in aliases
            )
        ):
            raise CombatIntentError("enemy attack aliases are invalid")
    attack_ids.add(attack_id)


def _validate_multiattack_action(action: dict[str, Any], label: str) -> None:
    """S0-MM3b: repeat / combo / choice, and nothing else."""

    shapes = [key for key in ("repeat", "combo", "choice") if key in action]
    if len(shapes) != 1:
        raise CombatIntentError(
            f"{label} multiattack needs exactly one of repeat, combo or choice"
        )
    shape = shapes[0]
    allowed = {"id", "name", "kind", "description", shape}
    if set(action) - allowed:
        raise CombatIntentError(f"{label} multiattack has unknown field(s)")

    def parts_of(entry: Any, where: str) -> list[dict[str, Any]]:
        if not isinstance(entry, list) or not 1 <= len(entry) <= 4:
            raise CombatIntentError(f"{label} {where} must list 1 to 4 parts")
        total = 0
        for part in entry:
            if not isinstance(part, dict) or set(part) - {"attack_id", "count"}:
                raise CombatIntentError(f"{label} {where} part is invalid")
            attack_id = part.get("attack_id")
            count = part.get("count")
            if not isinstance(attack_id, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9_.-]{0,63}", attack_id
            ):
                raise CombatIntentError(f"{label} {where} attack id is invalid")
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 6:
                raise CombatIntentError(f"{label} {where} attack count is invalid")
            total += count
        if total > MULTIATTACK_MAX_ATTACKS:
            raise CombatIntentError(f"{label} multiattack makes too many attacks")
        return entry

    if shape == "repeat":
        repeat = action.get("repeat")
        if not isinstance(repeat, dict) or set(repeat) - {"attack_id", "count"}:
            raise CombatIntentError(f"{label} multiattack repeat is invalid")
        parts_of([repeat], "repeat")
        return
    if shape == "combo":
        parts_of(action.get("combo"), "combo")
        return
    branches = action.get("choice")
    if not isinstance(branches, list) or not 2 <= len(branches) <= 4:
        raise CombatIntentError(f"{label} multiattack choice needs 2 to 4 branches")
    seen: set[str] = set()
    for branch in branches:
        if not isinstance(branch, dict) or set(branch) - {"label", "parts"}:
            raise CombatIntentError(f"{label} multiattack choice branch is invalid")
        branch_label = str(branch.get("label") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,31}", branch_label):
            raise CombatIntentError(f"{label} multiattack choice label is invalid")
        if branch_label in seen:
            raise CombatIntentError(f"{label} multiattack choice label is duplicated")
        seen.add(branch_label)
        parts_of(branch.get("parts"), f"choice {branch_label}")


def _validate_save_action(action: dict[str, Any], label: str) -> None:
    """S0-MM3c: "one save, then one of two outcomes".

    The save is rolled by the server, so the client only ever names the action.
    Both outcome lists may carry damage and nothing else: an action that also
    applies a condition or moves a target is a different primitive, and letting
    a half-implemented one through would silently drop half its text.
    """

    from .catalog import SAVE_ABILITIES

    allowed = {
        "id", "name", "kind", "description", "ability", "dc",
        "on_fail", "on_success", "usage_limits",
    }
    if set(action) - allowed:
        raise CombatIntentError(f"{label} save action has unknown field(s)")
    ability = action.get("ability")
    if not isinstance(ability, str) or ability not in SAVE_ABILITIES:
        raise CombatIntentError(f"{label} save action ability is invalid")
    dc = action.get("dc")
    if isinstance(dc, bool) or not isinstance(dc, int) or not 1 <= dc <= 30:
        raise CombatIntentError(f"{label} save action DC is invalid")
    for key in ("on_fail", "on_success"):
        if key not in action:
            continue
        rows = action[key]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 2:
            raise CombatIntentError(f"{label} save action {key} must list 1 to 2 outcomes")
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"damage"}:
                raise CombatIntentError(f"{label} save action {key} outcome is invalid")
            parts = row.get("damage")
            if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_ATTACK_DAMAGE_PARTS:
                raise CombatIntentError(
                    f"{label} save action {key} needs 1 or 2 damage parts"
                )
            for part in parts:
                if not isinstance(part, dict) or set(part) - {"dice", "type", "half"}:
                    raise CombatIntentError(f"{label} save action damage part is invalid")
                _validate_damage_formula(part.get("dice"))
                from .catalog import DAMAGE_TYPES
                damage_type = part.get("type")
                if not isinstance(damage_type, str) or damage_type not in DAMAGE_TYPES:
                    raise CombatIntentError(
                        f"{label} save action damage type is invalid"
                    )
                half = part.get("half")
                if half is not None and not isinstance(half, bool):
                    raise CombatIntentError(f"{label} save action half flag is invalid")
    if "on_fail" not in action:
        raise CombatIntentError(f"{label} save action needs an on_fail outcome")
    _validate_usage_limits(action.get("usage_limits"), f"{label} save action")


def _validate_usage_limits(limits: Any, label: str) -> None:
    """S0-MM3c: ``recharge`` or ``per_day`` — the two the engine can answer."""

    if limits is None:
        return
    if not isinstance(limits, dict) or len(limits) != 1:
        raise CombatIntentError(f"{label} usage_limits must name one limit")
    if "recharge" in limits:
        window = limits["recharge"]
        if not isinstance(window, dict) or set(window) - {"from", "to"}:
            raise CombatIntentError(f"{label} recharge window is invalid")
        start = window.get("from")
        end = window.get("to", 6)
        if (
            isinstance(start, bool) or not isinstance(start, int) or not 2 <= start <= 6
            or isinstance(end, bool) or not isinstance(end, int) or not start <= end <= 6
        ):
            raise CombatIntentError(f"{label} recharge window is invalid")
        return
    if "per_day" in limits:
        count = limits["per_day"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
            raise CombatIntentError(f"{label} per_day limit is invalid")
        return
    raise CombatIntentError(f"{label} usage_limits must name recharge or per_day")


def _validate_enemy_traits(enemy: dict[str, Any], label: str) -> None:
    """S0-MM3d: eight closed trait ids, each with only its own parameters."""

    raw = enemy.get("traits")
    if raw is None:
        return
    if not isinstance(raw, list) or not 0 <= len(raw) <= 20:
        raise CombatIntentError(f"{label} traits must list 0 to 20 entries")
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            raise CombatIntentError(f"{label} trait must be an object")
        if set(row) - {"id", "params"}:
            raise CombatIntentError(f"{label} trait has unknown field(s)")
        trait_id = str(row.get("id") or "")
        if trait_id not in TRAIT_IDS:
            raise CombatIntentError(f"{label} trait id is not supported: {trait_id!r}")
        if trait_id in seen:
            raise CombatIntentError(f"{label} repeats trait {trait_id}")
        seen.add(trait_id)
        params = row.get("params")
        if params is not None and not isinstance(params, dict):
            raise CombatIntentError(f"{label} trait {trait_id} params must be an object")
        params = params if isinstance(params, dict) else {}
        extra = set(params) - TRAIT_PARAMS[trait_id]
        if extra:
            raise CombatIntentError(
                f"{label} trait {trait_id} has unknown parameter(s): "
                f"{', '.join(sorted(extra))}"
            )
        if trait_id == "legendary_resistance":
            per_day = params.get("per_day", LEGENDARY_RESISTANCE_DEFAULT_PER_DAY)
            if isinstance(per_day, bool) or not isinstance(per_day, int) or not 1 <= per_day <= 5:
                raise CombatIntentError(
                    f"{label} legendary resistance per_day is invalid"
                )


def _validate_enemy_actions(enemy: dict[str, Any], label: str) -> None:
    """S0-MM3b: the stat block's ``actions[]`` container.

    Only Multiattack is expressible today; a save-based action is the next
    slice's kind, and an unknown kind must fail closed rather than be ignored —
    an action the engine cannot settle is worse than an action it refuses.
    """

    raw = enemy.get("actions")
    if raw is None:
        return
    if not isinstance(raw, list) or not 0 <= len(raw) <= 20:
        raise CombatIntentError(f"{label} actions must list 0 to 20 entries")
    seen: set[str] = set()
    for action in raw:
        if not isinstance(action, dict):
            raise CombatIntentError(f"{label} action must be an object")
        action_id = str(action.get("id") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", action_id) or action_id in seen:
            raise CombatIntentError(f"{label} action id is invalid or duplicated")
        seen.add(action_id)
        kind = str(action.get("kind") or "")
        if kind == "multiattack":
            _validate_multiattack_action(action, f"{label} {action_id}")
            continue
        if kind == "save_action":
            _validate_save_action(action, f"{label} {action_id}")
            continue
        raise CombatIntentError(f"{label} action kind is not supported")


def _validate_statblock_extras(enemy: dict[str, Any], label: str) -> None:
    """S0-MM2: the stat-block fields that are neither traits nor actions.

    Six fields, each with the narrowest shape that answers the question the
    engine actually asks.  Nothing here invents a default: a missing field stays
    missing so consumers fail closed instead of reading a guess.
    """

    subtype = enemy.get("subtype")
    if subtype is not None and (
        not isinstance(subtype, str) or len(subtype) > 40
    ):
        raise CombatIntentError(f"{label} subtype is invalid")

    skills = enemy.get("skills")
    if skills is not None:
        if not isinstance(skills, dict) or len(skills) > 32:
            raise CombatIntentError(f"{label} skills must be an object")
        for skill_id, bonus in skills.items():
            if not isinstance(skill_id, str) or not skill_id:
                raise CombatIntentError(f"{label} skill id is invalid")
            if isinstance(bonus, bool) or not isinstance(bonus, int) or not -20 <= bonus <= 40:
                raise CombatIntentError(f"{label} skill bonus is invalid")

    languages = enemy.get("languages")
    if languages is not None:
        if not isinstance(languages, list) or len(languages) > 20:
            raise CombatIntentError(f"{label} languages must be a list")
        seen: set[str] = set()
        for item in languages:
            if not isinstance(item, str) or not item.strip() or len(item) > 40:
                raise CombatIntentError(f"{label} language entry is invalid")
            if item in seen:
                raise CombatIntentError(f"{label} language entry is duplicated")
            seen.add(item)

    bonus = enemy.get("proficiency_bonus")
    if bonus is not None and (
        isinstance(bonus, bool) or not isinstance(bonus, int) or not 1 <= bonus <= 9
    ):
        raise CombatIntentError(f"{label} proficiency_bonus is invalid")

    hit_dice = enemy.get("hit_dice")
    if hit_dice is not None:
        # 只校验形状，不校验与 hp 相等：SRD 允许取平均值或取整。
        match = _HIT_DICE_RE.fullmatch(str(hit_dice))
        if match is None:
            raise CombatIntentError(f"{label} hit_dice is invalid")
        count, sides = int(match.group(1)), int(match.group(2))
        if not 1 <= count <= 30 or sides not in {4, 6, 8, 10, 12, 20}:
            raise CombatIntentError(f"{label} hit_dice is invalid")

    passive = enemy.get("passive_perception")
    if passive is not None and (
        isinstance(passive, bool) or not isinstance(passive, int)
        or not 0 <= passive <= 40
    ):
        raise CombatIntentError(f"{label} passive_perception is invalid")


class CombatValidationMixin:
    """Validate combat declarations without resolving dice or changing state."""

    __slots__ = ()

    @staticmethod
    def _validate_enemies(enemies: Any) -> None:
        validate_enemy_profiles(enemies)

    @staticmethod
    def _validate_temporary_encounter_enemies(instance: Any, enemies: Any) -> None:
        # 延迟导入：director 层复用本模块的 validate_enemy_profiles，
        # 顶层导入会形成循环依赖。
        from src.rulesets.dnd2024.director.temporary_encounter import (
            validate_final_temporary_enemies,
        )
        try:
            validate_final_temporary_enemies(instance, enemies)
        except ValueError as exc:
            raise CombatIntentError(str(exc)) from None

    def _validate(self, instance: Any, intent: dict[str, Any]) -> None:
        if not isinstance(intent, dict):
            raise CombatIntentError("intent must be an object")
        intent_id = str(intent.get("intent_id") or "").strip()
        if not intent_id or len(intent_id) > 120:
            raise CombatIntentError("intent_id is required and must be at most 120 characters")
        intent_type = str(intent.get("type") or "")
        if intent_type not in INTENT_TYPES:
            raise CombatIntentError("intent type is not supported")
        state = self.initialize_state(instance)
        if intent_type in {"wild_resurgence", "nature_magician"}:
            self._validate_druid_conversion(instance, intent, state)
        if intent_type in {"form.assume", "form.revert"}:
            self._validate_form_intent(instance, intent)
        if intent_type == "ability_check":
            # 成本可变的检定不属于"动作经济块"，所以放在通用分支之前：
            # 挂在 hide 那段里会因为外层的动作成本守卫而永远不执行。
            self._validate_ability_check(intent, state.get("combat", {}))
        expected = intent.get("expected_version")
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise CombatIntentError("expected_version must be an integer")
        if expected != state["version"]:
            raise CombatIntentError(
                f"state version conflict: expected {expected}, current {state['version']}"
            )
        combat = state["combat"]
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        submitted_by = str(intent.get("submitted_by") or "")
        if intent_type in {"encounter.ready", "encounter.unready"}:
            request = state.get("encounter_request")
            if combat.get("status") == "active":
                raise CombatIntentError("combat is already active")
            if not isinstance(request, dict) or request.get("status") != "pending":
                raise CombatIntentError("there is no pending encounter to prepare for")
            if submitted_by not in instance.players or submitted_by == gm_uid:
                raise CombatIntentError("only a non-GM party member can change readiness")
            if submitted_by not in required_encounter_player_ids(instance):
                raise CombatIntentError("only an active human party member can change readiness")
            ready_ids = {
                str(item) for item in request.get("ready_player_ids") or [] if str(item)
            }
            if intent_type == "encounter.ready" and submitted_by in ready_ids:
                raise CombatIntentError("the player is already ready")
            if intent_type == "encounter.unready" and submitted_by not in ready_ids:
                raise CombatIntentError("the player is not ready")
            return
        if intent_type == "combat.start":
            if submitted_by != gm_uid:
                raise CombatIntentError("only the GM can start combat")
            if combat.get("status") == "active":
                raise CombatIntentError("combat is already active")
            if self.encounter_access.unprepared:
                # 活动冒险包内没有合法绑定：这是可解释的“尚未准备遭遇”，
                # 而不是让 GM 在通用目录里随手挑一个顶上。
                raise CombatIntentError(
                    "the active adventure has no bound encounter; declare a sandbox encounter explicitly"
                )
            if not self.encounter_access.can_start:
                raise CombatIntentError("the current story does not allow an encounter to start")
            declared_mode = str(intent.get("mode") or "")
            if declared_mode not in {"", "sandbox"}:
                # 客户端只能声明“这是 GM 主动准备的一场自由遭遇”，
                # 不能自己声明剧情模式或其它模式。
                raise CombatIntentError("encounter mode is invalid")
            preset_id = str(intent.get("encounter_preset_id") or "")
            guided_preset_id = (
                self.encounter_access.encounter_preset_id
                if self.encounter_access.mode == "story"
                else ""
            )
            if guided_preset_id:
                # 剧情已绑定遭遇：不接受在战斗开始页改用通用目录，也不接受
                # 用 "sandbox" 声明把这场战斗从冒险包里摘出去。
                if declared_mode == "sandbox":
                    raise CombatIntentError(
                        "the current story requires its assigned encounter preset"
                    )
                if preset_id != guided_preset_id:
                    raise CombatIntentError(
                        "the current story requires its assigned encounter preset"
                    )
                requested_instance_id = str(intent.get("encounter_instance_id") or "")
                if requested_instance_id and requested_instance_id != self.encounter_access.encounter_instance_id:
                    raise CombatIntentError("the story encounter identity is stale")
            elif preset_id and self._preset(preset_id) is None:
                raise CombatIntentError("encounter preset is not available")
            if not preset_id:
                self._validate_enemies(intent.get("enemies"))
                if bool(intent.get("temporary_encounter")):
                    # AI 临时遭遇确认：编辑并展开后的最终 payload 在权威入口
                    # 重走生成期同一套收紧范围与队伍安全上限。该标记只追加
                    # 校验，不存在“跳过校验”的语义，也不放宽既有边界。
                    self._validate_temporary_encounter_enemies(
                        instance, intent.get("enemies"),
                    )
            # With a preset, the resolver replaces any submitted enemy list
            # with the catalog entry. Enemy data is intentionally optional so
            # clients cannot smuggle a forged stat block into combat.
            self._validate_player_positions(instance, intent.get("player_positions"))
            self._validate_mounted_players(instance, intent.get("mounted_player_ids"))
            self._validate_initiative_choices(instance, intent)
            return
        if combat.get("status") != "active":
            raise CombatIntentError("combat is not active")
        if error := self._turn_decision_error(combat, intent_type):
            raise CombatIntentError(error)
        if intent_type == "combat.set_cover":
            # 掩护是桌子上的判断（"他躲在箱子后面"），所以只有 GM 能声明；
            # 客户端提交的是等级枚举，永远不是加值。
            if submitted_by != gm_uid:
                raise CombatIntentError("only the GM can declare cover")
            target_id = str(intent.get("target_id") or "")
            level = str(intent.get("level") or "")
            if level not in COVER_LEVELS:
                raise CombatIntentError(
                    f"cover level must be one of {list(COVER_LEVELS)}"
                )
            kind, raw_id = _actor_kind(target_id)
            if kind == "player":
                if raw_id not in instance.players:
                    raise CombatIntentError("cover target actor does not exist")
            elif kind == "enemy":
                if raw_id not in (combat.get("enemies") or {}):
                    raise CombatIntentError("cover target actor does not exist")
            else:
                raise CombatIntentError("cover target must be a combat actor")
            return
        if intent_type == "combat.set_obscurement":
            # 遮蔽同样是桌子上的判断（"他站在黑烟里"），只有 GM 能声明；
            # 客户端提交的是等级枚举，永远不是派生布尔。
            if submitted_by != gm_uid:
                raise CombatIntentError("only the GM can declare obscurement")
            target_id = str(intent.get("target_id") or "")
            level = str(intent.get("level") or "")
            if level not in OBSCUREMENT_LEVELS:
                raise CombatIntentError(
                    f"obscurement level must be one of {list(OBSCUREMENT_LEVELS)}"
                )
            kind, raw_id = _actor_kind(target_id)
            if kind == "player":
                if raw_id not in instance.players:
                    raise CombatIntentError("obscurement target actor does not exist")
            elif kind == "enemy":
                if raw_id not in (combat.get("enemies") or {}):
                    raise CombatIntentError("obscurement target actor does not exist")
            else:
                raise CombatIntentError("obscurement target must be a combat actor")
            return
        if intent_type == "combat.message":
            if submitted_by not in instance.players:
                raise CombatIntentError("only a party member can send a combat message")
            text = str(intent.get("text") or "").strip()
            if not text or len(text) > 500:
                raise CombatIntentError("combat message must contain 1 to 500 characters")
            return
        if intent_type == "combat.end":
            if submitted_by != gm_uid:
                raise CombatIntentError("only the GM can end combat")
            return
        if intent_type == "decision.resolve":
            decision = next(
                (
                    item for item in combat.get("pending_decisions", [])
                    if item.get("decision_id") == intent.get("decision_id")
                ),
                None,
            )
            if decision is None or intent.get("option") not in decision.get("options", []):
                raise CombatIntentError("pending decision or option is invalid")
            if decision.get("kind") in {CHECK_WINDOW_DECISION, DAMAGE_WINDOW_DECISION}:
                # A post-roll window may belong to someone who is not the
                # current actor, so ownership is exactly ``assigned_to``.
                if submitted_by != str(decision.get("assigned_to") or gm_uid):
                    raise CombatIntentError("only the window owner can resolve this check window")
                self._validate_check_window(instance, combat, decision, intent)
                return
            if decision.get("kind") == RELENTLESS_RAGE_DECISION:
                # 归零存活是本人（或托管该席位的 GM）的决定；GM 始终可以
                # 代为处理，与其它托管决策同一条边界。
                kind, raw_id = _actor_kind(str(decision.get("actor_id") or ""))
                expected_owner = (
                    gm_uid
                    if kind == "companion"
                    or (kind == "player" and is_ai_controlled(instance, raw_id))
                    else str(decision.get("assigned_to") or gm_uid)
                )
                if submitted_by != expected_owner and submitted_by != gm_uid:
                    raise CombatIntentError(
                        "only the assigned actor can resolve this survival decision"
                    )
                return
            if decision.get("kind") in {HIT_SPELL_DECISION, HIT_REACTION_DECISION}:
                kind, raw_id = _actor_kind(str(decision.get("actor_id") or ""))
                expected_owner = gm_uid if kind == "companion" or (kind == "player" and is_ai_controlled(instance, raw_id)) else decision.get("assigned_to")
                if submitted_by != expected_owner:
                    raise CombatIntentError("only the assigned actor can resolve this spell decision")
                if decision.get("kind") == HIT_REACTION_DECISION:
                    self._validate_hit_reaction(instance, combat, decision, intent)
                    return
            elif submitted_by != str(decision.get("assigned_to") or gm_uid):
                raise CombatIntentError("only the assigned GM can resolve this reaction")
            # 借机攻击的最终资格由 resolution 的 _reaction_eligible 复查：
            # 开窗后威胁者失能 / 禁反应 / 反应耗尽时清除决策并继续移动。
            # 这里若提前按反应预算拒绝，最终复查将永远不可达。
            return
        actor_id = str(intent.get("actor_id") or "")
        if actor_id != self._current_actor(combat):
            raise CombatIntentError("it is not this actor's turn")
        kind, raw_id = _actor_kind(actor_id)
        if not kind:
            raise CombatIntentError("actor_id is invalid")
        if kind == "player":
            if is_ai_controlled(instance, raw_id):
                # AI 托管席位由 GM/server automation authority 代为提交（与
                # companion 同一条自动链路）。席位一旦被真人接管就不再走这里。
                if submitted_by != gm_uid:
                    raise CombatIntentError(
                        "only the GM can submit intents for an AI-hosted character"
                    )
            elif submitted_by != raw_id:
                raise CombatIntentError("a player can submit intents only for their own character")
        if kind == "companion":
            # AI 队友由 GM/server automation authority 提交；玩家不能直接控制。
            if submitted_by != gm_uid:
                raise CombatIntentError("only the GM can submit companion intents")
            if intent_type == "death_save":
                raise CombatIntentError("companions do not make death saves")
        if kind == "enemy" and submitted_by != gm_uid:
            raise CombatIntentError("only the GM can submit enemy intents")
        actor = self._actor_view(instance, combat, actor_id)
        if "dead" in actor["conditions"]:
            if intent_type == "end_turn":
                return
            raise CombatIntentError("a dead actor cannot act")
        if actor["hp"] <= 0 and intent_type != "death_save":
            if not (intent_type == "end_turn" and "stable" in actor["conditions"]):
                raise CombatIntentError("an unconscious actor can only make a death save")
        if actor["hp"] > 0 and intent_type == "death_save":
            raise CombatIntentError("death saves are only available at 0 HP")
        if intent_type == "death_save" and "stable" in actor["conditions"]:
            raise CombatIntentError("a stable actor does not make death saves")
        if intent_type == "death_save":
            self._validate_lucky_spend(instance, intent, actor)
        # 失能族只禁"动作"（动作/附赠动作/反应），移动不是动作：纯 incapacitated
        # （睡眠术）的单位仍可移动，只有 speed_zero（麻痹/昏迷）才门控 move。
        # 读的是有效条件：被光环抑制的 Frightened 不能继续禁动作。
        conditions = self._effective_conditions(instance, combat, actor)
        flags = condition_flags(self.catalog.conditions, conditions)
        if flags["incapacitated"] and intent_type in {
            "attack", "multiattack", "save_action", "escape", "stand_up",
            "cast_spell", "activate_item", "class_capability", "dash", "dodge",
            "disengage", "hide", "stabilize",
        }:
            raise CombatIntentError("an incapacitated actor cannot take actions")
        if flags["speed_zero"] and intent_type == "move":
            raise CombatIntentError("an actor with speed zero cannot move")
        economy = combat.get("economy") or {}
        self._validate_turn_choice(
            intent, intent_type, actor, economy, conditions,
        )
        if intent_type in {
            "attack", "multiattack", "save_action", "dash", "dodge", "disengage",
            "hide", "stabilize",
        }:
            has_action = int(economy.get("action", 0) or 0) > 0 or int(
                economy.get("surge_action", 0) or 0
            ) > 0
            if intent_type in {"attack", "multiattack", "save_action"}:
                # Horde Breaker 的第二击不付动作：它由"本回合已经打过一次"
                # 这个事实买断，校验里另行证明。
                #
                # ``attacks_remaining`` 只兑现 Attack action 剩下的那几次攻击。
                # Multiattack 与存档型动作各自要有一次完整的动作：让它们也吃
                # 这份额度，会把"截断的 Multiattack 还剩 2 击"变成"还能再放
                # 两次吐息"。
                if intent.get("second_target_of"):
                    pass
                elif not has_action and not (
                    intent_type == "attack"
                    and int(economy.get("attacks_remaining", 0) or 0) >= 1
                ):
                    raise CombatIntentError(
                        "the Attack action is no longer available"
                        if intent_type == "attack"
                        else "the action has already been spent"
                    )
            elif not has_action:
                raise CombatIntentError("the action has already been spent")
            if intent_type == "hide":
                gate = self.hide_preconditions(
                    instance, combat, str(actor["actor_id"]),
                )
                if not gate["allowed"]:
                    raise CombatIntentError(str(gate["reason"]))
        if intent_type == "move":
            distance = intent.get("distance")
            if isinstance(distance, bool) or not isinstance(distance, int) or distance == 0:
                raise CombatIntentError("movement distance must be a non-zero integer")
            if abs(distance) > int(economy.get("movement", 0) or 0):
                raise CombatIntentError("movement exceeds the remaining speed")
            for source_id in self._restriction_sources(conditions, "cannot_approach_source"):
                before = self._distance(combat, actor["actor_id"], source_id)
                after = self._distance_after_move(
                    combat, actor["actor_id"], source_id, distance,
                )
                if after < before:
                    raise CombatIntentError(
                        "a frightened creature cannot move closer to the source of its fear"
                    )
        if intent_type == "attack":
            self._validate_attack(instance, combat, intent, actor)
            self._validate_horde_breaker(instance, combat, intent, actor)
        if intent_type == "multiattack":
            self._validate_multiattack(instance, combat, intent, actor)
        if intent_type == "save_action":
            self._validate_save_action_intent(instance, combat, intent, actor)
        if intent_type == "escape":
            self._validate_escape(intent, actor, economy)
        if intent_type == "stand_up":
            self._validate_stand_up(intent, actor, economy)
        if intent_type == "superior_hunters_prey":
            self._validate_superior_prey(instance, combat, intent, actor)
        if intent_type == "end_turn":
            self._validate_self_restoration(actor, intent)
        if intent_type == "class_capability":
            self._validate_class_capability(instance, combat, intent, actor, economy)
        if intent_type == "cast_spell":
            if actor.get("spellcasting_allowed") is False:
                raise CombatIntentError("this shape does not allow spellcasting")
            self._validate_shaped_spellcasting(instance, actor_id, intent)
            self._validate_spell(instance, combat, intent, actor, economy)
        if intent_type == "activate_item":
            self._validate_item_activation(instance, combat, intent, actor, economy)
        if intent_type == "stabilize":
            target = self._actor_view(instance, combat, str(intent.get("target_id") or ""))
            if target["kind"] != "player" or target["hp"] > 0 or "dead" in target["conditions"]:
                raise CombatIntentError("stabilize requires a living player at 0 HP")
            self._require_range(combat, actor_id, target["actor_id"], 5)

    @staticmethod
    def _turn_decision_error(combat: dict[str, Any], intent_type: str) -> str:
        if intent_type in {"decision.resolve", "combat.message"}:
            return ""
        pending_kinds = {row.get("kind") for row in combat.get("pending_decisions", [])}
        if intent_type == "cast_spell":
            pending_kinds.discard(HIT_SPELL_DECISION)
        return next(
            (message for kind, message in _TURN_DECISION_ERRORS.items() if kind in pending_kinds),
            "",
        )

    @staticmethod
    def _choice_spent(choice: str, economy: dict[str, Any]) -> bool:
        """Whether this turn has already spent one of the three choices.

        Movement is answered from the ``moved`` marker the reducer writes when
        the current actor actually moves; a forced move of somebody else does
        not set it.  Action and Bonus Action are answered from the remaining
        budget.  A surge action is deliberately *not* folded in here: refusing
        the surge itself is a separate rule in ``_validate_turn_choice``, and
        counting it as "already acted" would blame the wrong thing.
        """

        if choice == "move":
            return bool(economy.get("moved"))
        if choice == "action":
            return int(economy.get("action", 0) or 0) < 1
        if choice == "bonus_action":
            return int(economy.get("bonus_action", 0) or 0) < 1
        return False

    def _intent_choice(
        self, intent: dict[str, Any], intent_type: str, actor: dict[str, Any],
    ) -> str:
        """Which of move / action / Bonus Action this intent would spend.

        Empty means "none of the three": a reaction, a decision, or ending the
        turn is not one of the choices the Abjure Foes Frightened restricts.
        """

        if intent_type == "move":
            return "move"
        if intent_type in {
            "attack", "multiattack", "save_action", "dash", "dodge", "disengage",
            "hide", "stabilize", "activate_item",
        }:
            return "action"
        if intent_type == "cast_spell":
            spell = self.spells.get(str(intent.get("spell_ref") or ""))
            if spell is None:
                return ""
            casting_time = str(spell["casting_time"])
            if casting_time.startswith("Bonus Action"):
                return "bonus_action"
            # 反应法术不占用三选一里的任何一项：斥退敌人不禁止反应。
            if casting_time.startswith("Reaction"):
                return ""
            return "action"
        if intent_type == "class_capability":
            capability = self.features.declared_capability(
                self._feature_character(actor),
                str(intent.get("capability_id") or ""),
            )
            if capability is None:
                return ""
            # ``cost.action`` 是经济槽位的名字本身（action / bonus_action /
            # reaction），不是一个布尔。
            if capability.cost.action == "bonus_action":
                return "bonus_action"
            return "action" if capability.cost.action == "action" else ""
        return ""

    ABILITY_CHECK_COSTS = frozenset({"action", "bonus_action", "none"})

    def _validate_ability_check(
        self, intent: dict[str, Any], combat: dict[str, Any],
    ) -> None:
        """A bare ability check: ability, DC and what it costs.

        The cost is *declared* rather than implied, because the same arithmetic
        serves a check paid with an action, one paid with a bonus action, and one
        that costs nothing at all (a contest, a failed-window consequence).
        """

        ability = str(intent.get("ability") or "")
        if ability not in {"str", "dex", "con", "int", "wis", "cha"}:
            raise CombatIntentError("ability check needs a canonical ability")
        dc = intent.get("dc")
        if isinstance(dc, bool) or not isinstance(dc, int) or not 1 <= dc <= 40:
            raise CombatIntentError("ability check DC must be 1..40")
        cost = str(intent.get("check_cost") or "action")
        if cost not in self.ABILITY_CHECK_COSTS:
            raise CombatIntentError("ability check cost is not supported")
        economy = combat.get("economy") or {}
        if cost != "none" and int(economy.get(cost, 0) or 0) < 1:
            raise CombatIntentError(f"the {cost.replace('_', ' ')} has already been spent")

    _WILD_SHAPE_CR_BY_LEVEL = ((4, "1/4"), (8, "1/2"), (99, "1"))

    def _validate_form_intent(self, instance: Any, intent: dict[str, Any]) -> None:
        """变形只校验"能不能变"，不校验面板怎么合成。

        形态的 CR/Fly 门槛按德鲁伊等级走（SRD p42：2/4/8 级分别 1/4、1/2、1，
        8 级前不能有飞行速度）。这些门槛属于**动作资格**，所以放在校验层，
        而不是放进合成策略里。
        """

        actor_id = str(intent.get("actor_id") or "")
        kind, raw_id = _actor_kind(actor_id)
        if kind != "player":
            raise CombatIntentError("only a player can change shape")
        character = _canonical(instance.get_character_sheet(raw_id))
        level = declaring_class_level(character, "druid")
        # 门控按"是否拥有该特性"，不再按等级硬编码：等级只是授予条件，
        # 特性声明才是权威（多职业/兼职时两者会分叉）。
        if not self.features.has_feature(character, "wild_shape"):
            raise CombatIntentError("Wild Shape starts at druid level 2")
        forms = character.get("forms")
        active = forms.get("active") if isinstance(forms, dict) else None
        if str(intent.get("type")) == "form.revert":
            if not isinstance(active, dict):
                raise CombatIntentError("no shape is active")
            # SRD p42：主动离开形态是 Bonus Action。
            economy = (self.initialize_state(instance).get("combat") or {}).get("economy") or {}
            if int(economy.get("bonus_action", 0) or 0) < 1:
                raise CombatIntentError("the bonus action has already been spent")
            return
        self._validate_wild_shape_form(intent, level)
        self._validate_known_form(character, int(level), str(intent.get("form_ref") or ""))
        economy = (self.initialize_state(instance).get("combat") or {}).get("economy") or {}
        if int(economy.get("bonus_action", 0) or 0) < 1:
            raise CombatIntentError("the bonus action has already been spent")
        remaining = self._class_resource_state(character, "wild_shape")
        if remaining["maximum"] < 1:
            raise CombatIntentError("Wild Shape uses are not initialized")
        if remaining["current"] < 1:
            raise CombatIntentError("no Wild Shape uses left")

    def _validate_wild_shape_form(self, intent: dict[str, Any], level: int) -> None:
        form_ref = str(intent.get("form_ref") or "")
        entry = self._catalog_entry(form_ref)
        if entry is None:
            raise CombatIntentError(f"unknown form reference: {form_ref!r}")
        if str(entry.get("creature_type") or "") != "beast":
            raise CombatIntentError("Wild Shape needs a beast stat block")
        ceiling = next(
            cap for minimum, cap in self._WILD_SHAPE_CR_BY_LEVEL if level < minimum
        )
        if _cr_value(str(entry.get("cr") or "0")) > _cr_value(ceiling):
            raise CombatIntentError(
                f"a level {level} druid cannot take a CR {entry.get('cr')} form"
            )
        if level < 8 and int((entry.get("speeds") or {}).get("fly", 0) or 0) > 0:
            raise CombatIntentError("a druid below level 8 cannot take a flying form")
        modes = intent.get("equipment_modes")
        if isinstance(modes, dict):
            for item_ref, mode in modes.items():
                if not str(item_ref) or str(mode) not in {"drop", "merge", "wear"}:
                    raise CombatIntentError("equipment mode is not supported")
        else:
            mode = str(intent.get("equipment_mode") or "merge")
            if mode not in {"drop", "merge", "wear"}:
                raise CombatIntentError("equipment mode is not supported")

    def _validate_known_form(
        self, character: dict[str, Any], level: int, form_ref: str,
    ) -> None:
        """形态必须"已学会"。

        老存档（`forms.known` 不存在）按推荐四形态兜底：任务书 §4.1.6 要求目录
        覆盖 SRD 推荐的那四个，用它当默认不引入新数据。显式学会之后只认列表。
        """

        forms = character.get("forms")
        known = forms.get("known") if isinstance(forms, dict) else None
        if isinstance(known, dict):
            known = known.get("wild_shape")
        if isinstance(known, list) and known:
            if form_ref not in [str(item) for item in known]:
                raise CombatIntentError(
                    "that form is not among the known forms; learn it on a long rest"
                )
            return
        seeded = bootstrap_known_forms(
            [entry for catalog in self.bundle.list("monster_catalog")
             for entry in catalog.get("entries") or []],
            level,
        )
        if form_ref not in seeded:
            raise CombatIntentError(
                "that form is not among the known forms; learn it on a long rest"
            )

    def _catalog_entry(self, form_ref: str) -> dict[str, Any] | None:
        if not form_ref:
            return None
        for catalog in self.bundle.list("monster_catalog"):
            for entry in catalog.get("entries") or []:
                if str(entry.get("id") or "") == form_ref:
                    return dict(entry)
        return None

    @classmethod
    def _material_restriction(cls, spell: dict[str, Any]) -> str:
        """18 级 Beast Spells 的材料例外（SRD p43）。

        判定只读目录里的两个结构化布尔（`material_cost_specified` /
        `material_consumed`），它们由 `scripts/rulesets/import_srd_spell_materials.py`
        从上游材料文本生成。**不在运行时解析材料描述**：那等于把规则建在
        一个会随文案变化而失效的正则上。

        法术用材料但目录没标这两个布尔 → fail closed（禁止），因为"不知道"
        不能当成"没有价格"。
        """

        components = spell.get("components")
        if not isinstance(components, list):
            return "the spell's components are unknown"
        if not any(str(component).upper().startswith("M") for component in components):
            return ""
        if spell.get("material_consumed"):
            return "that spell consumes its material component"
        if spell.get("material_cost_specified"):
            return "that spell has a material component with a specified cost"
        if "material_consumed" not in spell or "material_cost_specified" not in spell:
            return "the spell's material component is not classified"
        return ""

    def _validate_shaped_spellcasting(
        self, instance: Any, actor_id: str, intent: dict[str, Any],
    ) -> None:
        """变形中的施法限制（SRD p43）。

        2–17 级一律禁止；18 级 Beast Spells 放开，但"材料有价格 / 材料被消耗"
        两类仍然禁止。禁止的是**这个动作**，不是角色的专注——已有的
        Concentration 不会被这里碰到。
        """

        kind, raw_id = _actor_kind(actor_id)
        if kind != "player":
            return
        character = _canonical(instance.get_character_sheet(raw_id))
        forms = character.get("forms")
        active = forms.get("active") if isinstance(forms, dict) else None
        if not isinstance(active, dict):
            return
        try:
            state = FormState.from_payload(active)
        except FormStateError:  # pragma: no cover - reducer guarantees the shape
            return
        if not restrictions(state)["spellcasting"]:
            raise CombatIntentError("a shape-shifted druid cannot cast spells")
        spell = self._spell_entity(str(intent.get("spell_ref") or ""))
        if spell is None:  # pragma: no cover - _validate_spell reports this
            return
        reason = self._material_restriction(spell)
        if reason:
            raise CombatIntentError(f"beast spells cannot cast it: {reason}")

    def _spell_entity(self, spell_ref: str) -> dict[str, Any] | None:
        """与其它施法校验读同一本目录：`self.spells`，不是 bundle.get。

        `bundle.get("spell", id)` 对法术目录返回空，写在那里的门控永远不会触发。
        """

        if not spell_ref:
            return None
        return self.spells.get(spell_ref)

    def _class_resource_state(self, character: dict[str, Any], resource_id: str) -> dict[str, int]:
        state = (character.get("resources") or {}).get("class", {}).get(resource_id)
        if not isinstance(state, dict):
            return {"current": 0, "maximum": 0}
        return {
            "current": int(state.get("current", 0) or 0),
            "maximum": int(state.get("maximum", 0) or 0),
        }

    def _validate_druid_conversion(
        self, instance: Any, intent: dict[str, Any], state: dict[str, Any],
    ) -> None:
        """Wild Resurgence 与 Nature Magician 的资格校验。

        两条都要求"次数不够就不能换"：`current == 0` 才能用法术位换回一次，
        `current >= 1` 才能把次数换成法术位。前者还要求本回合没用过。
        """

        actor_id = str(intent.get("actor_id") or "")
        kind, raw_id = _actor_kind(actor_id)
        if kind != "player":
            raise CombatIntentError("only a player can convert Wild Shape uses")
        character = _canonical(instance.get_character_sheet(raw_id))
        level = declaring_class_level(character, "druid")
        uses = self._class_resource_state(character, "wild_shape")
        combat = state.get("combat") or {}
        if str(intent.get("type")) == "nature_magician":
            if not self.features.has_feature(character, "archdruid"):
                raise CombatIntentError("Nature Magician starts at druid level 20")
            chosen = intent.get("uses")
            if isinstance(chosen, bool) or not isinstance(chosen, int):
                raise CombatIntentError("uses must be an integer")
            if not 1 <= chosen <= min(4, uses["current"]):
                raise CombatIntentError("uses must be between 1 and the uses you have (max 4)")
            rest = self._class_resource_state(character, "archdruid")
            if rest["maximum"] < 1:
                # 资源没初始化就 fail closed：放行等于"没有次数限制"，
                # 而这条特性恰好就是靠次数限制成立的。
                raise CombatIntentError(
                    "Archdruid uses are not initialized on this character"
                )
            if rest["current"] < 1:
                raise CombatIntentError(
                    "Nature Magician needs a long rest before it can be used again"
                )
            return
        direction = str(intent.get("direction") or "")
        if not self.features.has_feature(character, "wild_resurgence"):
            raise CombatIntentError("Wild Resurgence starts at druid level 5")
        if direction == "slot_to_use":
            if uses["current"] != 0:
                raise CombatIntentError("you still have Wild Shape uses left")
            if combat.get("wild_resurgence_turn"):
                raise CombatIntentError("Wild Resurgence was already used this turn")
            slot_level = intent.get("slot_level")
            if isinstance(slot_level, bool) or not isinstance(slot_level, int) or slot_level < 1:
                raise CombatIntentError("a spell slot level is required")
            return
        if direction == "use_to_slot":
            if uses["current"] < 1:
                raise CombatIntentError("no Wild Shape use left to convert")
            rest = self._class_resource_state(character, "wild_resurgence")
            if rest["maximum"] < 1:
                raise CombatIntentError("Wild Resurgence uses are not initialized")
            if rest["current"] < 1:
                raise CombatIntentError(
                    "this direction needs a long rest before it can be used again"
                )
            return
        raise CombatIntentError("wild_resurgence direction is not supported")

    def _item_is_merged_in_form(
        self, instance: Any, actor_id: str, item_ref: str,
    ) -> bool:
        kind, raw_id = _actor_kind(actor_id)
        if kind != "player":
            return False
        character = _canonical(instance.get_character_sheet(raw_id))
        forms = character.get("forms")
        active = forms.get("active") if isinstance(forms, dict) else None
        if not isinstance(active, dict):
            return False
        try:
            state = FormState.from_payload(active)
        except FormStateError:  # pragma: no cover - reducer guarantees the shape
            return False
        return mode_for(state, str(item_ref)) == "merge"


    def _validate_turn_choice(
        self, intent: dict[str, Any], intent_type: str, actor: dict[str, Any],
        economy: dict[str, Any], conditions: dict[str, Any],
    ) -> None:
        """Abjure Foes: one of move / action / Bonus Action per turn, no more.

        The restriction is data on the condition, so it applies to whatever
        the intent actually costs rather than to a list of banned intent types.
        Repeating the *same* choice is left to the ordinary economy: a second
        attack of the Attack action, or a second Bonus Action, is refused by
        the budget, not by this rule.

        One exception stays here rather than in the budget: Action Surge is the
        only thing that can hand out a second action after the first is spent,
        and the restriction exists precisely to cap the creature at one of the
        three for the turn, so a surge action is refused while the limit holds.
        """

        limit = self._turn_choice_limit(conditions)
        if not limit:
            return
        choice = self._intent_choice(intent, intent_type, actor)
        if choice not in limit:
            return
        if (
            choice == "action"
            and int(economy.get("action", 0) or 0) < 1
            and int(economy.get("surge_action", 0) or 0) > 0
        ):
            raise CombatIntentError(
                "while frightened this way you can only take one action "
                "on your turn: Action Surge cannot buy another"
            )
        spent = {
            name for name in limit
            if name != choice and self._choice_spent(name, economy)
        }
        if spent:
            labels = {
                "move": "move", "action": "take an action",
                "bonus_action": "take a Bonus Action",
            }
            used = ", ".join(sorted(labels.get(name, name) for name in spent))
            raise CombatIntentError(
                "while frightened this way you can only move, take an action, "
                f"or take a Bonus Action on your turn: you already chose to {used}"
            )

    def _turn_choice_limit(self, conditions: dict[str, Any]) -> list[str]:
        """The three-way cap this condition set carries, if any."""

        for meta in conditions.values():
            if not isinstance(meta, dict):
                continue
            declared = meta.get("turn_choice_limit")
            if isinstance(declared, list):
                limit = [str(item) for item in declared if str(item)]
                if limit:
                    return limit
        return []

    def _validate_class_capability(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        """Authoritative gate for feature-provided combat capabilities.

        The UI only ever offers capabilities the server reported as available;
        this check is what actually protects the boundary, so a forged
        capability id, a forged target, or a stale resource state is rejected
        here even when the button was never rendered.
        """

        if actor.get("kind") not in {"player", "companion"}:
            raise CombatIntentError("class capabilities are not available to this actor")
        capability_id = str(intent.get("capability_id") or "")
        if not capability_id:
            raise CombatIntentError("capability_id is required")
        via_fleet_step = intent.get("via_fleet_step")
        if via_fleet_step is not None and not isinstance(via_fleet_step, bool):
            raise CombatIntentError("via_fleet_step must be a boolean")
        if via_fleet_step:
            if capability_id != "step_of_the_wind":
                raise CombatIntentError(
                    "Fleet Step can only grant Step of the Wind"
                )
            if not bool(economy.get("fleet_step_available")):
                raise CombatIntentError("Fleet Step is not available this turn")
        capability = self.features.declared_capability(
            self._feature_character(actor), capability_id,
        )
        if capability is not None:
            conditions = actor.get("conditions") or {}
            required = capability.requires_condition
            if required and required not in conditions:
                raise CombatIntentError(
                    f"{self.features.catalog.label(capability_id, capability_id)} requires "
                    f"the {self.features.catalog.label(required, required)} condition"
                )
            forbidden = capability.forbidden_condition
            if forbidden and forbidden in conditions:
                raise CombatIntentError(
                    f"{self.features.catalog.label(capability_id, capability_id)} cannot be "
                    f"used while {self.features.catalog.label(forbidden, forbidden)} is active"
                )
        if capability_id == "steady_aim":
            if self.has_moved_this_turn(combat, str(actor["actor_id"])):
                raise CombatIntentError("you have already moved this turn")
        rider_move = intent.get("rider_move")
        if rider_move is not None:
            allowed = capability is not None and capability_id in {
                "rage", "second_wind",
            }
            if not allowed:
                raise CombatIntentError(
                    "granted movement is not available for this capability"
                )
            if isinstance(rider_move, bool) or not isinstance(rider_move, int):
                raise CombatIntentError("granted movement must be an integer")
            if rider_move <= 0:
                raise CombatIntentError("granted movement must be positive")
            allowance = effective_speed(
                self.catalog.conditions, actor.get("conditions") or {},
                int(actor.get("speed", 30) or 30),
            ) // 2
            if rider_move > allowance:
                raise CombatIntentError("granted movement exceeds half your speed")
        if capability is not None and any(
            action.kind == "hide" for action in capability.actions
        ):
            gate = self.hide_preconditions(instance, combat, str(actor["actor_id"]))
            if not gate["allowed"]:
                raise CombatIntentError(str(gate["reason"]))
        choice = intent.get("open_hand_choice")
        if choice is not None:
            # Open Hand Technique 只在 Flurry of Blows 授予的攻击上成立；把它
            # 挂在普通徒手攻击上等于凭空多出一个免费 rider。
            if capability_id != "flurry_of_blows":
                raise CombatIntentError(
                    "open hand technique applies only to Flurry of Blows attacks"
                )
            character = self._feature_character(actor)
            if not self.features.has_feature(character, "open_hand_technique"):
                raise CombatIntentError(
                    "open hand technique is not available to this actor"
                )
            allowed = self.features.feature_value(
                character, "open_hand_technique", "flurry_hit_choices", [],
            ) or []
            if choice not in allowed:
                raise CombatIntentError(
                    "the declared open hand technique choice is not available"
                )
        views = {
            view.id: view
            for view in self._available_class_capabilities(
                actor, economy, include_unavailable=True,
            )
        }
        view = views.get(capability_id)
        if view is None:
            raise CombatIntentError("class capability is not available to this actor")
        if not view.available:
            raise CombatIntentError(view.blocked_reason or "class capability is not available")
        if capability is None:  # pragma: no cover - availability already proved it
            raise CombatIntentError("class capability is not available to this actor")
        has_unarmed_strike = any(
            action.kind == "unarmed_strike" for action in capability.actions
        )
        has_bardic_grant = any(
            action.kind == "bardic_grant" for action in capability.actions
        )
        if has_unarmed_strike:
            # 复用 canonical 徒手打击校验：目标必须存活且敌对、射程由同一份
            # 攻击档案决定，Combat 不自己算命中/伤害。
            rider_actor = deepcopy(actor)
            # The capability's own spend precedes every granted hit. Validate
            # rider affordability against that balance, not the original pool.
            for cost in capability.cost.resources:
                for pool in rider_actor.get("class_resources") or []:
                    if pool.get("id") == cost.resource_id:
                        pool["current"] = int(pool.get("current", 0)) - cost.amount
            self._validate_attack(instance, combat, {
                "target_id": str(intent.get("target_id") or ""),
                "weapon_ref": UNARMED_STRIKE_REF,
                **{key: intent[key] for key in (
                    "unarmed_damage_type", "rider_damage_type", "stunning_strike",
                ) if key in intent},
            }, rider_actor)
        elif has_bardic_grant:
            # 授予目标是另一位、能看见或听见你、且在 60 尺内的友方活体，并且
            # 对方此刻没有一枚未过期的骰。这些事实在校验期就能用同一组权威
            # 状态判定，所以非法提交在这里得到结构化拒绝，而不是等到结算时
            # 抛异常；resolution 仍会重验一遍（纵深防御，且它要写缩略视图）。
            target_id = str(intent.get("target_id") or "")
            target = self._actor_view(instance, combat, target_id)
            if target_id == actor["actor_id"]:
                raise CombatIntentError("you cannot grant Bardic Inspiration to yourself")
            if str(target.get("side") or "") != str(actor.get("side") or "") or target["hp"] <= 0:
                raise CombatIntentError("Bardic Inspiration targets a living ally")
            range_ft = self.features.feature_value(
                self._feature_character(actor), "bardic_inspiration", "range_ft", 0,
            )
            if not isinstance(range_ft, int) or range_ft <= 0:
                raise CombatIntentError("no Bardic Inspiration range is declared")
            self._require_range(combat, actor["actor_id"], target_id, range_ft)
            # "can see or hear you"：现有状态词表里能同时切断两条通道的只有
            # 隐身施予者 + 目盲承受者；deafened 未建模，因此不参与判定。
            if (
                "invisible" in (actor.get("conditions") or {})
                and "blinded" in (target.get("conditions") or {})
            ):
                raise CombatIntentError("the target can neither see nor hear you")
            if self._active_bardic_instance(instance, combat, target_id) is not None:
                raise CombatIntentError("the target already holds a Bardic Inspiration die")
        elif any(action.kind == "inspiration_slot_exchange" for action in capability.actions):
            # Font 的槽换次数：必须有已消耗次数、且该法术位仍然可用。
            character = self._canonical_actor(instance, actor)
            pool = dict(resource_state(character, "bardic_inspiration"))
            if int(pool.get("current", 0) or 0) >= int(pool.get("maximum", 0) or 0):
                raise CombatIntentError("no expended Bardic Inspiration use to regain")
            slot_level = intent.get("slot_level")
            if isinstance(slot_level, bool) or not isinstance(slot_level, int) or slot_level < 1:
                raise CombatIntentError("a spell slot level is required")
            slots = dict(
                ((character.get("spellcasting") or {}).get("class") or {}).get("slots_current") or {}
            )
            if int(slots.get(str(slot_level), 0) or 0) < 1:
                raise CombatIntentError("the spell slot is already spent")
        elif any(action.kind == "font_slot_to_points" for action in capability.actions):
            # 槽换点没有动作成本，所以"能不能换"全在这里判：源槽必须是真槽、
            # 目标点数不能超过上限（超了整笔拒绝，不静默截断）。
            try:
                slot_to_points_plan(
                    self._canonical_actor(instance, actor),
                    intent.get("slot_level"),
                )
            except FontOfMagicError as exc:
                raise CombatIntentError(str(exc)) from exc
        elif any(action.kind == "font_create_slot" for action in capability.actions):
            # 点换槽的五行走内容表；等级下限按"授予该特性的职业等级"算，
            # 不是角色总等级——兼职时两者不是一回事。
            character = self._canonical_actor(instance, actor)
            definition = self.features.feature_definition(capability.feature_id)
            class_id = str(getattr(definition, "class_id", "") or "")
            try:
                costs = parse_created_slot_costs(
                    self.features.feature_value(
                        character, capability.feature_id, "created_slot_costs", (),
                    )
                )
                create_slot_plan(
                    character, intent.get("slot_level"), costs,
                    declaring_class_level(character, class_id),
                )
            except FontOfMagicError as exc:
                raise CombatIntentError(str(exc)) from exc
        elif any(action.kind == "divine_spark" for action in capability.actions):
            self._validate_divine_spark(instance, combat, intent, actor, capability)
        elif any(action.kind == "turn_undead" for action in capability.actions):
            self._validate_turn_undead(instance, combat, intent, actor, capability)
        elif any(action.kind == "preserve_life" for action in capability.actions):
            self._validate_preserve_life(instance, combat, intent, actor, capability)
        elif any(action.kind == "lay_on_hands" for action in capability.actions):
            self._validate_lay_on_hands(instance, combat, intent, actor, capability)
        elif any(action.kind == "abjure_foes" for action in capability.actions):
            self._validate_abjure_foes(instance, combat, intent, actor, capability)
        elif capability.requires_hostile_target:
            target = self._actor_view(
                instance, combat, str(intent.get("target_id") or ""),
            )
            if str(target.get("side") or "") == str(actor.get("side") or "") or target["hp"] <= 0:
                raise CombatIntentError("this capability requires a living hostile target")

    def _capability_range(
        self, actor: dict[str, Any], capability: Any, key: str, default: int,
    ) -> int:
        """The declared reach of one capability, in feet.

        Range comes from the feature declaration, never from the intent: a
        client that sends a friendlier number must not be able to widen it.
        The declaring feature is read (not the capability id) because the
        Channel Divinity options are capabilities *of* one feature, and their
        numbers live in that feature's parameters under a prefixed key.
        """

        range_ft = self.features.feature_value(
            self._feature_character(actor), capability.feature_id, key, default,
        )
        return int(range_ft) if isinstance(range_ft, int) and range_ft > 0 else default

    def _validate_divine_spark(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Divine Spark: another living creature, 30 feet, one of two modes."""

        target_id = str(intent.get("target_id") or "")
        if not target_id:
            raise CombatIntentError("Divine Spark requires a target")
        if target_id == str(actor["actor_id"]):
            raise CombatIntentError("Divine Spark cannot target yourself")
        target = self._actor_view(instance, combat, target_id)
        if target["hp"] <= 0:
            raise CombatIntentError("Divine Spark targets a living creature")
        self._require_range(
            combat, str(actor["actor_id"]), target_id,
            self._capability_range(actor, capability, "spark_target_range_ft", 30),
        )
        mode = str(intent.get("spark_mode") or "")
        if mode not in {"heal", "damage"}:
            raise CombatIntentError("Divine Spark must be declared as heal or damage")
        damage_type = str(intent.get("damage_type") or "")
        allowed = [
            str(kind) for kind in (
                self.features.feature_value(
                    self._feature_character(actor), capability.feature_id,
                    "spark_damage_types", ["necrotic", "radiant"],
                ) or []
            )
        ]
        if mode == "damage":
            if damage_type not in allowed:
                raise CombatIntentError(
                    "Divine Spark damage must be one of its declared damage types"
                )
        elif damage_type:
            raise CombatIntentError("Divine Spark healing does not choose a damage type")

    def _validate_turn_undead(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Turn Undead: a de-duplicated set of Undead inside the declared reach."""

        raw = intent.get("target_ids")
        if not isinstance(raw, list) or not raw:
            raise CombatIntentError("Turn Undead requires at least one Undead target")
        targets = [str(item) for item in raw]
        if len(set(targets)) != len(targets):
            raise CombatIntentError("Turn Undead targets must not repeat")
        limit = self._capability_range(actor, capability, "turn_target_range_ft", 30)
        for target_id in targets:
            target = self._actor_view(instance, combat, target_id)
            if target["hp"] <= 0:
                raise CombatIntentError("Turn Undead targets a living Undead")
            if "undead" not in str(target.get("creature_type") or "").lower():
                raise CombatIntentError("Turn Undead targets only Undead creatures")
            self._require_range(combat, str(actor["actor_id"]), target_id, limit)
        if intent.get("sear_undead") not in (None, False):
            if intent.get("sear_undead") is not True:
                raise CombatIntentError("sear_undead must be a boolean")
            if not self.features.has_feature(
                self._feature_character(actor), "sear_undead",
            ):
                raise CombatIntentError("sear undead is not available to this actor")

    def _validate_abjure_foes(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Abjure Foes: exactly Charisma-modifier creatures you can see.

        "Exactly" is the rule, not a convenience: the feature targets *a number
        of creatures equal to* the modifier, so a shorter list is as illegal as
        a longer one, and refusing the whole intent is the only rejection that
        cannot leave a partially-paid Channel use behind.
        """

        raw = intent.get("target_ids")
        if not isinstance(raw, list) or not raw:
            raise CombatIntentError("Abjure Foes requires at least one target")
        targets = [str(item) for item in raw]
        if len(set(targets)) != len(targets):
            raise CombatIntentError("Abjure Foes targets must not repeat")
        character = self._feature_character(actor)
        ability = str(self.features.feature_value(
            character, capability.feature_id, "target_count_ability", "cha",
        ) or "cha")
        minimum = int(self.features.feature_value(
            character, capability.feature_id, "target_count_minimum", 1,
        ) or 1)
        count = max(
            minimum,
            ability_modifier(int((actor.get("abilities") or {}).get(ability, 10) or 10)),
        )
        if len(targets) != count:
            raise CombatIntentError(
                f"Abjure Foes must target exactly {count} creatures"
            )
        limit = self._capability_range(actor, capability, "target_range_ft", 60)
        for target_id in targets:
            if target_id == str(actor["actor_id"]):
                raise CombatIntentError("Abjure Foes cannot target yourself")
            target = self._actor_view(instance, combat, target_id)
            if target["hp"] <= 0:
                raise CombatIntentError("Abjure Foes targets a living creature")
            self._require_range(combat, str(actor["actor_id"]), target_id, limit)
            if not self._spell_target_visible(instance, combat, actor, target):
                raise CombatIntentError("Abjure Foes targets creatures you can see")

    def _validate_preserve_life(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Preserve Life: a split of one budget, capped by each target's half HP.

        Both ceilings are checked here because both are facts the server owns:
        the budget is 5 x Cleric level for *this* action, and a creature can
        never be restored above half its Hit Point maximum by this feature.
        """

        rows = intent.get("allocations")
        if not isinstance(rows, list) or not rows:
            raise CombatIntentError("Preserve Life requires at least one allocation")
        character = self._feature_character(actor)
        per_level = self.features.feature_value(
            character, capability.id, "preserve_life_per_level", 5,
        )
        state = self.features.class_state(character)
        level = int(state[1]) if state else 0
        budget = int(per_level or 5) * level
        limit = self._capability_range(actor, capability, "target_range_ft", 30)
        seen: set[str] = set()
        total = 0
        for row in rows:
            if not isinstance(row, dict):
                raise CombatIntentError("each Preserve Life allocation must name a target")
            target_id = str(row.get("target_id") or "")
            amount = row.get("amount")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise CombatIntentError("a Preserve Life amount must be a non-negative integer")
            if target_id in seen:
                raise CombatIntentError("a Preserve Life target may appear only once")
            seen.add(target_id)
            target = self._actor_view(instance, combat, target_id)
            if target["hp"] <= 0:
                raise CombatIntentError("Preserve Life targets a living creature")
            self._require_range(combat, str(actor["actor_id"]), target_id, limit)
            maximum = int(target["max_hp"])
            if int(target["hp"]) * 2 > maximum:
                raise CombatIntentError(
                    "Preserve Life targets only Bloodied creatures"
                )
            ceiling = max(0, maximum // 2 - int(target["hp"]))
            if amount > ceiling:
                raise CombatIntentError(
                    "Preserve Life cannot restore a creature above half its Hit Points"
                )
            total += amount
        if total > budget:
            raise CombatIntentError(
                "the Preserve Life allocation exceeds this use's budget"
            )
        if total <= 0:
            raise CombatIntentError("Preserve Life requires a positive allocation")

    def _lay_on_hands_conditions(
        self, actor: dict[str, Any], capability_id: str,
    ) -> tuple[dict[str, int], int]:
        """Conditions this Paladin may pay to remove, and the price of each.

        Restoring Touch (level 14) is a second declaration over the same pool,
        so the allowed set is the union of what each owned feature names.  The
        base list is never edited in place: a character without the feature
        simply has the shorter set.
        """

        character = self._feature_character(actor)
        cost = int(self.features.feature_value(
            character, capability_id, "condition_cost", 5,
        ) or 5)
        allowed: dict[str, int] = {
            str(code): cost
            for code in (self.features.feature_value(
                character, "lay_on_hands", "removable_conditions", ["poisoned"],
            ) or [])
        }
        for code in self.features.feature_value(
            character, "restoring_touch", "removable_conditions", [],
        ) or []:
            allowed[str(code)] = int(self.features.feature_value(
                character, "restoring_touch", "condition_cost", 5,
            ) or 5)
        return allowed, cost

    def _validate_lay_on_hands(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], capability: Any,
    ) -> None:
        """Lay On Hands: one touched creature, a pool that pays, conditions owned."""

        target_id = str(intent.get("target_id") or "")
        if not target_id:
            raise CombatIntentError("Lay On Hands requires a target")
        target = self._actor_view(instance, combat, target_id)
        if "dead" in (target.get("conditions") or {}):
            raise CombatIntentError("Lay On Hands cannot restore a dead creature")
        self._require_range(
            combat, str(actor["actor_id"]), target_id,
            self._capability_range(actor, capability, "target_range_ft", 5),
        )
        amount = intent.get("healing_amount", 0)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise CombatIntentError("healing_amount must be a non-negative integer")
        raw_conditions = intent.get("remove_conditions") or []
        if not isinstance(raw_conditions, list):
            raise CombatIntentError("remove_conditions must be an array")
        conditions = [str(code) for code in raw_conditions]
        if len(set(conditions)) != len(conditions):
            raise CombatIntentError("a condition may be removed only once per use")
        allowed, _base_cost = self._lay_on_hands_conditions(actor, capability.id)
        held = target.get("conditions") or {}
        total = amount
        for condition_id in conditions:
            if condition_id not in allowed:
                raise CombatIntentError(
                    "this Paladin cannot remove that condition with Lay On Hands"
                )
            if condition_id not in held:
                # 目标身上没有这个状态时拒绝，而不是照扣 5 点却什么也没移除。
                raise CombatIntentError(
                    "the target does not have the condition being removed"
                )
            total += allowed[condition_id]
        if total <= 0:
            raise CombatIntentError(
                "Lay On Hands needs healing or a condition to remove"
            )
        character = self._canonical_actor(instance, actor)
        pool = resource_state(character, "lay_on_hands")
        if int(pool.get("current", 0) or 0) < total:
            raise CombatIntentError("the Lay On Hands pool is too small for that use")

    def _validate_sacred_weapon(
        self, intent: dict[str, Any], actor: dict[str, Any],
        weapon: dict[str, Any], combat: dict[str, Any],
    ) -> None:
        """Sacred Weapon is declared on an attack, not as its own action.

        Two facts are re-derived here rather than trusted from the panel: that
        this is the *first* attack of a real Attack action (a Channel use
        cannot be bolted on to the second swing of a round, nor to an
        opportunity attack), and that the weapon being imbued is the very
        weapon this attack uses -- a Melee weapon the actor is carrying.
        """

        declared = intent.get("activate_sacred_weapon")
        damage_choice = intent.get("sacred_weapon_damage")
        if declared in (None, False) and damage_choice is None:
            return
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "sacred_weapon"):
            raise CombatIntentError("sacred weapon is not available to this actor")
        weapon_ref = str(weapon.get("weapon_ref") or "")
        binding = (actor.get("conditions") or {}).get(SACRED_WEAPON_CONDITION)
        bound_here = (
            isinstance(binding, dict)
            and str(binding.get("weapon_ref") or "") == weapon_ref
        )
        if declared not in (None, False):
            if declared is not True:
                raise CombatIntentError("activate_sacred_weapon must be a boolean")
            if weapon.get("ranged") or weapon.get("unarmed"):
                raise CombatIntentError(
                    "sacred weapon imbues a Melee weapon you are holding"
                )
            economy = combat.get("economy") or {}
            if int(economy.get("attacks_remaining", 0) or 0) > 0:
                raise CombatIntentError(
                    "sacred weapon is imbued when you take the Attack action, "
                    "not after its first attack"
                )
            if (
                int(economy.get("action", 0) or 0) < 1
                and int(economy.get("surge_action", 0) or 0) < 1
            ):
                raise CombatIntentError("sacred weapon requires the Attack action")
            pool = resource_state(character, "channel_divinity")
            if int(pool.get("current", 0) or 0) < 1:
                raise CombatIntentError("channel divinity is already spent")
        if damage_choice is not None:
            if not bound_here and declared is not True:
                raise CombatIntentError(
                    "this weapon carries no sacred weapon bond"
                )
            allowed = [
                str(value) for value in (
                    self.features.feature_value(
                        character, "sacred_weapon", "weapon_damage_type_choices",
                        ["normal", "radiant"],
                    ) or []
                )
            ]
            if str(damage_choice) not in allowed:
                raise CombatIntentError(
                    "the declared sacred weapon damage type is not available"
                )

    def _validate_two_handed(
        self, actor: dict[str, Any], intent: dict[str, Any], weapon: dict[str, Any],
        hands: dict[str, Any] | None = None,
    ) -> None:
        """两用武器双手持用的声明校验。

        ``equipment_refs`` 是携带清单，``hands`` 才是这一场的持握状态：角色
        可以随身带备用武器而不握着它，所以"另一只手是否被占"由 hands 回答，
        而不是由"背包里有没有盾"回答。
        """

        declared = intent.get("two_handed")
        if declared is None or declared is False:
            return
        if declared is not True:
            raise CombatIntentError("two_handed must be a boolean")
        if actor["kind"] not in {"player", "companion"}:
            # 敌方 stat block 没有两用概念，其攻击档也不声明该字段。
            raise CombatIntentError("two_handed is not available to this actor")
        if not weapon.get("damage_two_handed"):
            raise CombatIntentError("this weapon has no two-handed damage")
        if self._held_shield_blocks(
            actor, str(weapon.get("weapon_ref") or ""), hands=hands,
        ):
            raise CombatIntentError("a shield in the other hand blocks two-handed use")

    def _validate_lucky_spend(
        self, instance: Any, intent: dict[str, Any], actor: dict[str, Any],
    ) -> None:
        """Declaring Lucky's advantage face requires an unspent point in hand.

        Ownership of the pool *is* the feat requirement (the resource is only
        synced for characters with ``feat:lucky``), so an absent pool is
        rejected exactly like an empty one.
        """

        spend_luck = intent.get("spend_luck")
        if spend_luck is None or spend_luck is False:
            return
        if spend_luck is not True:
            raise CombatIntentError("spend_luck must be a boolean")
        if not self._window_pool_available(
            instance, str(actor["actor_id"]), LUCKY_RESOURCE,
        ):
            raise CombatIntentError("lucky pool is empty")

    def _validate_self_restoration(
        self, actor: dict[str, Any], intent: dict[str, Any],
    ) -> None:
        """The turn-end removal is a choice, and a choice has to be legal.

        Absent means "not used": the engine never picks a condition for the
        player, and it never removes two.  A declared value must name one of
        the feature's own conditions *and* be one the actor actually has -- a
        stale client must not be able to delete a condition that is not there,
        nor one that arrived after the panel was rendered.
        """

        if "self_restoration_condition" not in intent:
            return
        requested = intent["self_restoration_condition"]
        if requested is None:
            return
        chosen = self._self_restoration_choice(actor)
        if chosen is None:
            raise CombatIntentError(
                "this actor has no feature that removes a condition at turn end"
            )
        feature_id, allowed = chosen
        if not isinstance(requested, str) or not requested:
            raise CombatIntentError("self_restoration_condition must be a condition id")
        if requested not in allowed:
            raise CombatIntentError(
                f"{feature_id} cannot remove the {requested} condition"
            )
        if requested not in (actor.get("conditions") or {}):
            raise CombatIntentError(f"the actor does not have the {requested} condition")

    def _restriction_sources(
        self, conditions: dict[str, Any], flag: str,
    ) -> list[str]:
        """Actor ids named by the conditions that declare ``flag``.

        A condition such as Frightened restricts behaviour *towards its own
        source*, so the restriction is data on the stored entry, not a branch
        on the condition id.  An entry without a recorded source cannot be
        scoped to anyone and is therefore not enforced -- blocking every
        direction would be a worse answer than a restriction that visibly does
        nothing, and the settlement paths always record the source.
        """

        sources: list[str] = []
        for condition_id, entry in self.catalog.conditions.items():
            if not entry.get(flag) or condition_id not in conditions:
                continue
            stored = conditions.get(condition_id)
            source = (
                str(stored.get("source_actor_id") or "")
                if isinstance(stored, dict) else ""
            )
            if source and source not in sources:
                sources.append(source)
        return sources

    def _is_weapon_ref(self, ref: str) -> bool:
        return str(ref).removeprefix("item:") in self.catalog.weapons

    def _two_handed_refs(self, actor: dict[str, Any]) -> set[str]:
        """Carried refs whose weapon needs both hands (mounted exception aside)."""

        refs: set[str] = set()
        for raw in actor.get("equipment_refs") or []:
            ref = str(raw)
            weapon = self.catalog.weapons.get(ref.removeprefix("item:"))
            if not isinstance(weapon, dict) or not weapon.get("two_handed"):
                continue
            exception = str(weapon.get("two_handed_exception") or "")
            if exception == "mounted" and bool(actor.get("mounted")):
                continue
            refs.add(ref)
        return refs

    def _weapon_swap_hands(
        self, actor: dict[str, Any], intent: dict[str, Any],
    ) -> dict[str, str | None] | None:
        """Validate the declared swap and return the grip it produces.

        One equip/unequip per attack is the SRD budget, so a declaration that
        tries to do two things at once is malformed rather than rich.  Stowing
        is limited to weapons: a shield comes off with an action, not for free.
        """

        swap = intent.get("weapon_swap")
        if swap is None:
            return None
        if actor["kind"] not in {"player", "companion"}:
            raise CombatIntentError("weapon_swap is not available to this actor")
        carried = {str(ref) for ref in actor.get("equipment_refs") or []}
        if isinstance(swap, dict):
            draw = str(swap.get("draw") or "")
            stow = str(swap.get("stow") or "")
            if draw and draw not in carried:
                raise CombatIntentError("weapon to draw is not carried")
            if stow and not self._is_weapon_ref(stow):
                raise CombatIntentError(
                    "only a weapon can be stowed as part of an attack"
                )
        hands, error = apply_swap(
            actor.get("hands"), swap, two_handed_refs=self._two_handed_refs(actor),
        )
        if error:
            raise CombatIntentError(error)
        return hands

    def _held_shield_blocks(
        self, actor: dict[str, Any], weapon_ref: str,
        hands: dict[str, Any] | None = None,
    ) -> bool:
        """Whether a shield occupies the hand this weapon does not.

        ``equipment`` 是携带清单，``hands`` 才是持握。带在身上的备用武器随时
        可以按"每次攻击一次"的规则收起来，因此不构成阻挡；盾牌是绑在手臂上
        的，不能免费脱手，所以它才是真正的阻挡源。缺少 ``hands`` 的调用方
        （只读视图、旧测试夹具）退回旧口径：按携带清单里的盾牌判断。
        """

        hands = actor.get("hands") if hands is None else hands
        if isinstance(hands, dict):
            for side in ("left", "right"):
                ref = str(hands.get(side) or "")
                if not ref or ref == weapon_ref:
                    continue
                item = self.bundle.get("item", ref.removeprefix("item:")) or {}
                if item.get("item_type") == "shield":
                    return True
            return False
        for ref in actor.get("equipment_refs") or []:
            item = self.bundle.get("item", str(ref).removeprefix("item:")) or {}
            if item.get("item_type") == "shield":
                return True
        return False

    def _validate_loading(
        self, combat: dict[str, Any], actor: dict[str, Any], intent: dict[str, Any],
        weapon: dict[str, Any],
    ) -> None:
        """A Loading weapon fires once per action / Bonus Action / Reaction.

        窗口由"支付这次攻击的经济池"标识：同一个 Attack action 里的两次攻击
        共用 ``action``（或 ``surge_action``），所以第二发会被拒绝，尽管
        ``attacks_remaining`` 还允许它。换一个窗口（Bonus Action、Action
        Surge）是另一发，仍然合法。
        """

        if not weapon.get("loading"):
            return
        if actor["kind"] not in {"player", "companion"}:
            return
        resource = (
            "bonus_action" if intent.get("light_extra_attack")
            else self._spend_action_resource(combat)
        )
        record = (combat.get("loading_shots") or {}).get(str(actor["actor_id"]))
        if not isinstance(record, dict):
            return
        if (
            int(record.get("round", -1)) == int(combat.get("round", 0) or 0)
            and int(record.get("turn_index", -1)) == int(combat.get("turn_index", 0) or 0)
            and str(record.get("window") or "") == resource
        ):
            raise CombatIntentError("a Loading weapon can fire only once per action")

    def _validate_light_extra_attack(
        self, combat: dict[str, Any], actor: dict[str, Any], intent: dict[str, Any],
        weapon: dict[str, Any], hands: dict[str, Any] | None = None,
    ) -> None:
        """The Light extra attack: a Bonus Action, a different Light weapon.

        There is no "off-hand" in these rules.  What the extra attack needs is
        (a) an Attack action with a Light weapon this turn, (b) a *different*
        Light weapon in the other hand, and (c) an unspent Bonus Action.  Its
        damage omits the ability modifier unless Two-Weapon Fighting says
        otherwise -- that decision lives in resolution, next to the damage roll.
        """

        declared = intent.get("light_extra_attack")
        if declared is None:
            return
        if declared is not True:
            raise CombatIntentError("light_extra_attack must be a boolean")
        if actor["kind"] not in {"player", "companion"}:
            raise CombatIntentError("light_extra_attack is not available to this actor")
        if not weapon.get("light"):
            raise CombatIntentError("the extra attack must use a Light weapon")
        grip = hands if isinstance(hands, dict) else (actor.get("hands") or {})
        ref = str(weapon.get("weapon_ref") or "")
        other = next(
            (
                str(grip.get(side) or "")
                for side in ("left", "right")
                if str(grip.get(side) or "") not in {"", ref}
            ),
            "",
        )
        other_weapon = self.catalog.weapons.get(other.removeprefix("item:")) if other else None
        if not isinstance(other_weapon, dict) or not other_weapon.get("light"):
            raise CombatIntentError(
                "the other hand must hold a different Light weapon"
            )
        record = (combat.get("light_attacks") or {}).get(str(actor["actor_id"]))
        this_round = int(combat.get("round", 0) or 0)
        this_turn = int(combat.get("turn_index", 0) or 0)
        if not isinstance(record, dict) or (
            int(record.get("round", -1)) != this_round
            or int(record.get("turn_index", -1)) != this_turn
        ):
            raise CombatIntentError(
                "the extra attack requires an Attack action with a Light weapon this turn"
            )
        economy = combat.get("economy") or {}
        if str(economy.get("actor_id") or "") != str(actor["actor_id"]):
            raise CombatIntentError("the extra attack can only be made on your own turn")
        if int(economy.get("bonus_action", 0) or 0) < 1:
            raise CombatIntentError(
                "the extra attack costs a Bonus Action that is already spent"
            )

    def _validate_grip(
        self, actor: dict[str, Any], weapon: dict[str, Any],
        hands: dict[str, Any] | None = None,
    ) -> None:
        """A Two-Handed weapon must actually get both hands.

        The versatile declaration already asked this question; Two-Handed
        weapons ask it implicitly, every time they are used.  Without this
        check "shield in one hand, greatsword in the other" is a legal attack,
        which is exactly what the data now says it is not.
        """

        if not weapon.get("two_handed"):
            return
        if actor["kind"] not in {"player", "companion"}:
            return
        exception = str(weapon.get("two_handed_exception") or "")
        if exception == "mounted" and bool(actor.get("mounted")):
            return
        if self._held_shield_blocks(
            actor, str(weapon.get("weapon_ref") or ""), hands=hands,
        ):
            raise CombatIntentError("a shield in the other hand blocks two-handed use")

    def _validate_horde_breaker(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """Horde Breaker's second attack, if this intent is one.

        An ordinary attack returns immediately.  A second attack has to prove
        five things the SRD states in one sentence: the Hunter owns the option,
        has not used it this turn, the original target was attacked this turn,
        the new target is a different creature within 5 feet of the original,
        and that creature has not been attacked this turn.
        """

        original_id = str(intent.get("second_target_of") or "")
        if not original_id:
            return
        actor_id = str(intent["actor_id"])
        character = self._feature_character(actor)
        if not self.features.has_feature(
            character, "horde_breaker", owner="subclass:hunter",
        ):
            raise CombatIntentError("this character has no horde breaker")
        if self._feature_used_this_turn(combat, actor_id, "horde_breaker"):
            raise CombatIntentError("horde breaker is already used this turn")
        attacked = self.attacked_this_turn(combat, actor_id)
        if original_id not in attacked:
            raise CombatIntentError(
                "horde breaker needs an attack against the original target this turn"
            )
        target_id = str(intent.get("target_id") or "")
        if target_id == original_id:
            raise CombatIntentError("horde breaker must pick a different target")
        if target_id in attacked:
            raise CombatIntentError(
                "horde breaker cannot attack a creature attacked this turn"
            )
        limit = int(self.features.feature_value(
            character, "horde_breaker", "target_within_of_original_ft", 5,
        ) or 0)
        if self._distance(combat, target_id, original_id) > limit:
            raise CombatIntentError(
                f"horde breaker's target must be within {limit} feet of the original"
            )

    def _validate_superior_prey(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """Superior Hunter's Prey: the second target, and why it qualifies."""

        actor_id = str(intent["actor_id"])
        character = self._feature_character(actor)
        if not self.features.has_feature(
            character, "superior_hunters_prey", owner="subclass:hunter",
        ):
            raise CombatIntentError("this character has no superior hunter's prey")
        if self._feature_used_this_turn(combat, actor_id, "superior_hunters_prey"):
            raise CombatIntentError("superior hunter's prey is already used this turn")
        original_id = str(intent.get("target_id") or "")
        second_id = str(intent.get("second_target_id") or "")
        if not original_id or not second_id:
            raise CombatIntentError("superior hunter's prey needs both targets")
        if second_id == original_id:
            raise CombatIntentError("superior hunter's prey needs a different target")
        if marked_target_id(actor) != original_id:
            raise CombatIntentError(
                "superior hunter's prey needs the creature marked by your Hunter's Mark"
            )
        if not self.mark_damage_this_turn(combat, actor_id, original_id):
            raise CombatIntentError(
                "superior hunter's prey needs mark damage dealt to the marked creature this turn"
            )
        limit = int(self.features.feature_value(
            character, "superior_hunters_prey", "second_target_range_ft", 30,
        ) or 0)
        if self._distance(combat, second_id, original_id) > limit:
            raise CombatIntentError(
                f"superior hunter's prey's target must be within {limit} feet of the marked one"
            )

    def _validate_attack(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], actor: dict[str, Any],
    ) -> None:
        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        # 敌我判断基于 side：companion/player 同为 party，互不视为合法攻击目标。
        actor_side = str(actor.get("side") or "")
        target_side = str(target.get("side") or "")
        if target_side == actor_side or target["hp"] <= 0:
            raise CombatIntentError("attack target must be a living hostile actor")
        conditions = self._effective_conditions(instance, combat, actor)
        if target_id in self._restriction_sources(conditions, "cannot_attack_source"):
            raise CombatIntentError(
                "a charmed creature cannot attack the creature that charmed it"
            )
        weapon = self._declared_attack(actor, intent)
        if weapon is None:
            # 一个 lookup 同时覆盖装备武器、徒手打击与敌人 attack profile：
            # 客户端无法声明角色并不真正拥有的攻击档案。
            raise CombatIntentError("attack profile is not available to the actor")
        # 先算出手部变化，再按变化后的握持判定：规则允许"先拔出再攻击"，
        # 所以这一击可以合法地使用刚拔出的那把武器。
        swap_hands = self._weapon_swap_hands(actor, intent)
        self._validate_grip(actor, weapon, hands=swap_hands)
        self._validate_ammunition(instance, actor, weapon)
        self._validate_two_handed(actor, intent, weapon, hands=swap_hands)
        self._validate_light_extra_attack(combat, actor, intent, weapon, hands=swap_hands)
        self._validate_loading(combat, actor, intent, weapon)
        self._validate_lucky_spend(instance, intent, actor)
        self._validate_sacred_weapon(intent, actor, weapon, combat)
        distance = self._distance(combat, actor["actor_id"], target_id)
        normal_range = int(weapon.get("thrown_range") or weapon.get("range", 5))
        long_range = int(weapon.get("long_range") or normal_range)
        if distance > long_range:
            raise CombatIntentError(f"target is out of range ({distance} > {long_range} feet)")
        self._validate_hit_riders(actor, intent, combat)
        self._validate_attack_state_options(instance, combat, intent, actor, weapon, distance)

    def _validate_save_action_intent(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """S0-MM3c: name an action the creature has and still may use.

        Availability is checked here rather than during resolution because a
        spent per-day action or a recharge that has not come back must refuse
        the whole intent — and must not consume the creature's action either.
        """

        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        if str(target.get("side") or "") == str(actor.get("side") or "") or target["hp"] <= 0:
            raise CombatIntentError("save action target must be a living hostile actor")
        action = self._declared_action(actor, intent)
        if action is None or str(action.get("kind") or "") != "save_action":
            raise CombatIntentError("save action profile is not available to the actor")
        usage = self._action_usage(actor, str(action["id"]))
        if not usage["available"]:
            raise CombatIntentError("this action is not available yet")
        if set(intent) - {
            "intent_id", "type", "expected_version", "submitted_by", "actor_id",
            "target_id", "action_id",
        }:
            raise CombatIntentError("save action intent has unknown field(s)")

    @staticmethod
    def _action_usage(actor: dict[str, Any], action_id: str) -> dict[str, Any]:
        """Whether a stat-block action can be used right now, and why not.

        Absence of state means "never used": a fresh combat starts with every
        recharge available and every per-day budget whole.
        """

        stored = (actor.get("usage") or {}).get(action_id)
        stored = stored if isinstance(stored, dict) else {}
        return {
            "available": bool(stored.get("available", True)),
            "used": int(stored.get("used", 0) or 0),
        }

    def _validate_escape(
        self, intent: dict[str, Any], actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        """S0-MM3e: escape a condition whose DC and shape are already on record.

        The intent names a condition and an ability; the DC, the legal checks
        and the cost all come from state.  A caller cannot declare a DC — that
        is the whole point of reading it from the applied instance.
        """

        if set(intent) - {
            "intent_id", "type", "expected_version", "submitted_by", "actor_id",
            "condition", "check",
        }:
            raise CombatIntentError("escape intent has unknown field(s)")
        condition_id = str(intent.get("condition") or "")
        declared = self.catalog.conditions.get(condition_id) or {}
        escape = declared.get("escape")
        if not isinstance(escape, dict):
            raise CombatIntentError("this condition cannot be escaped")
        carried = (actor.get("conditions") or {}).get(condition_id)
        if not isinstance(carried, dict):
            raise CombatIntentError("the actor does not have that condition")
        dc = carried.get("escape_dc")
        if isinstance(dc, bool) or not isinstance(dc, int) or dc <= 0:
            # 施加时没有写 DC 的条件无法挣：不替它编一个数。
            raise CombatIntentError("this application declares no escape DC")
        chosen = str(intent.get("check") or "")
        allowed = {
            str(row.get("ability")): (str(row.get("skill") or "") or None)
            for row in escape.get("checks") or []
            if isinstance(row, dict)
        }
        if chosen not in allowed:
            raise CombatIntentError("escape check must be one of the declared abilities")
        cost = str(escape.get("cost") or "action")
        if int(economy.get(cost, 0) or 0) < 1:
            raise CombatIntentError(f"escaping costs a {cost.replace('_', ' ')}")

    def _validate_stand_up(
        self, intent: dict[str, Any], actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        """Standing up is movement, not an action: half the walking speed."""

        if set(intent) - {
            "intent_id", "type", "expected_version", "submitted_by", "actor_id",
        }:
            raise CombatIntentError("stand up intent has unknown field(s)")
        declared = stand_up_declaration(self.catalog.conditions, actor.get("conditions") or {})
        if declared is None:
            raise CombatIntentError("the actor is not in a condition one can stand up from")
        # ``or`` would treat a real speed of 0 as "absent" and fall back to the
        # printed speed — a grappled (speed 0) creature would then be quoted the
        # price of a stand-up it cannot pay for.
        current = economy.get("speed")
        cost = self.stand_up_cost(
            actor, declared,
            speed=int(current) if isinstance(current, int) else None,
        )
        if int(economy.get("movement", 0) or 0) < cost:
            raise CombatIntentError("not enough movement left to stand up")

    @staticmethod
    def stand_up_cost(
        actor: dict[str, Any], declared: dict[str, Any], *, speed: int | None = None,
    ) -> int:
        """Half the walking speed, rounded down (2024: "spend half your Speed").

        The *effective* speed is what the movement budget already uses, so a
        creature whose speed is halved pays half of the halved value — reading
        the printed speed instead would let it buy the same stand-up twice.
        """

        base = max(0, int(actor.get("speed", 0) or 0)) if speed is None else max(0, int(speed))
        fraction = max(1, int(declared.get("fraction") or 2))
        return base // fraction

    def _validate_multiattack(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """S0-MM3b: one action, N attacks, all of them provable now.

        The whole batch is validated here rather than per attack during
        resolution: "任一击被拒绝则整批拒绝" only holds if nothing can fail
        halfway through.  A missing attack id, an out-of-range part or a branch
        the action does not offer all end the intent before any die is rolled.
        """

        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        if str(target.get("side") or "") == str(actor.get("side") or "") or target["hp"] <= 0:
            raise CombatIntentError("attack target must be a living hostile actor")
        # 字段闭集：`second_target_of` 是 Horde Breaker 的免费第二击通道，
        # 让它出现在 Multiattack 上等于白送一次动作；其余字段（换手、双手、
        # Lucky）都只属于玩家武器意图。
        if set(intent) - {
            "intent_id", "type", "expected_version", "submitted_by", "actor_id",
            "target_id", "action_id", "choice",
        }:
            raise CombatIntentError("multiattack intent has unknown field(s)")
        action = self._declared_action(actor, intent)
        if action is None:
            raise CombatIntentError("multiattack profile is not available to the actor")
        parts = self._multiattack_parts(actor, action, intent)
        if not parts:
            raise CombatIntentError("multiattack resolves to no attack")
        distance = self._distance(combat, str(actor["actor_id"]), target_id)
        for attack, _count in parts:
            long_range = int(attack.get("long_range") or attack.get("range", 5))
            if distance > long_range:
                raise CombatIntentError(
                    f"target is out of range for {attack.get('id')} "
                    f"({distance} > {long_range} feet)"
                )

    def _validate_attack_state_options(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], weapon: dict[str, Any], distance: int,
    ) -> None:
        """Reject a pre-roll declaration the actor cannot legally make now.

        Three facts are re-derived from the authoritative state rather than
        trusted from the panel: whether this is the first attack roll of the
        turn (Reckless), whether any Disadvantage source exists at all
        (Brutal), and whether the once-per-turn budget is already spent.  A
        submitted declaration that fails any of them must fail the intent --
        silently ignoring it would let the client believe it had Advantage
        while the roll was a straight d20.
        """

        reckless = intent.get("reckless_attack")
        brutal = intent.get("brutal_strike")
        effects = intent.get("brutal_strike_effects")
        if reckless is None and brutal is None and effects is None:
            return
        character = self._feature_character(actor)
        actor_id = str(actor["actor_id"])

        if reckless is not None:
            if not isinstance(reckless, bool):
                raise CombatIntentError("reckless_attack must be a boolean")
            if reckless:
                if not self.features.has_feature(character, "reckless_attack"):
                    raise CombatIntentError(
                        "reckless attack is not available to this actor"
                    )
                condition = str(self.features.feature_value(
                    character, "reckless_attack", "self_condition", "",
                ) or "")
                if condition and condition in (actor.get("conditions") or {}):
                    raise CombatIntentError("this actor is already attacking recklessly")
                if str((combat.get("economy") or {}).get("actor_id") or "") != actor_id:
                    # "When you make your first attack roll on your turn": an
                    # Opportunity Attack happens on someone else's turn.
                    raise CombatIntentError(
                        "reckless attack can only be declared on your own turn"
                    )
                if self._attack_roll_count(combat, actor_id) > 0:
                    raise CombatIntentError(
                        "reckless attack must be declared before the first attack roll"
                    )

        if brutal is not None:
            if not isinstance(brutal, bool):
                raise CombatIntentError("brutal_strike must be a boolean")
            if brutal:
                if not self.features.has_feature(character, "brutal_strike"):
                    raise CombatIntentError(
                        "brutal strike is not available to this actor"
                    )
                ability = str(self.features.feature_value(
                    character, "brutal_strike", "ability", "",
                ) or "")
                if ability and self.attack_ability(actor, weapon) != ability:
                    raise CombatIntentError(
                        "brutal strike requires a Strength-based attack"
                    )
                required = str(self.features.feature_value(
                    character, "brutal_strike", "requires_declaration", "",
                ) or "")
                reckless_condition = str(self.features.feature_value(
                    character, required, "self_condition", "",
                ) or "") if required else ""
                declared_now = bool(reckless) or (
                    reckless_condition in (actor.get("conditions") or {})
                )
                if not declared_now:
                    raise CombatIntentError(
                        "brutal strike requires reckless attack this turn"
                    )
                if self._feature_used_this_turn(combat, actor_id, "brutal_strike"):
                    raise CombatIntentError("brutal strike was already used this turn")
                # "The chosen attack roll mustn't have Disadvantage." 相消不算：
                # 只要来源里存在任何一份 Disadvantage 就不合格，否则"劣势被
                # 另一份优势抵消"会变成 Brutal 的通行证。
                check_mode = self._attack_check_mode(
                    instance, combat, actor, self._actor_view(
                        instance, combat, str(intent["target_id"]),
                    ), self.attack_range_disadvantage(weapon, distance),
                    ability=self.attack_ability(actor, weapon),
                    declared_reckless=bool(reckless),
                    spend_luck=bool(intent.get("spend_luck")),
                )
                if any(
                    str(row.get("mode") or "") == "disadvantage"
                    for row in check_mode["sources"]
                ):
                    raise CombatIntentError(
                        "brutal strike is not available while the attack has disadvantage"
                    )
                self._validate_brutal_strike_effects(
                    character, intent.get("brutal_strike_effects"),
                )
        elif effects:
            # Options without the declaration are a forged submission: ignoring
            # them would let a client believe a push was coming while the roll
            # was a plain one.
            raise CombatIntentError(
                "brutal strike effects require a brutal strike declaration"
            )

    def _validate_brutal_strike_effects(
        self, character: Any, submitted: Any,
    ) -> None:
        """Every chosen option must be one this level unlocks, at most ``cap``."""

        allowed = [str(value) for value in self.features.feature_value(
            character, "brutal_strike", "effect_options", [],
        ) or []]
        if self.features.has_feature(character, "improved_brutal_strike"):
            allowed.extend(
                str(value) for value in self.features.feature_value(
                    character, "improved_brutal_strike",
                    "additional_effect_options", [],
                ) or []
            )
        state = self.features.class_state(character)
        level = int(state[1]) if state else 0
        cap = int(self.features.feature_value(
            character, "brutal_strike", "effects_per_use", 0,
        ) or 0)
        if level >= int(self.features.feature_value(
            character, "brutal_strike", "upgrade_level", 0,
        ) or 0):
            cap = int(self.features.feature_value(
                character, "brutal_strike", "effects_per_use_upgrade", cap,
            ) or cap)
        if submitted is None:
            return
        if not isinstance(submitted, list) or any(
            not isinstance(value, str) for value in submitted
        ):
            raise CombatIntentError("brutal strike effects must be a list of ids")
        if len(submitted) > cap:
            raise CombatIntentError(
                f"brutal strike allows at most {cap} effect(s) at this level"
            )
        if len(set(submitted)) != len(submitted):
            raise CombatIntentError(
                "brutal strike cannot apply the same effect twice"
            )
        unavailable = sorted(set(submitted) - set(allowed))
        if unavailable:
            raise CombatIntentError(
                f"brutal strike effect is not available: {unavailable[0]}"
            )

    @staticmethod
    def _attack_roll_count(combat: dict[str, Any], actor_id: str) -> int:
        """Attack rolls this actor has already made in the current turn."""

        record = (combat.get("attack_rolls") or {}).get(str(actor_id))
        if not isinstance(record, dict):
            return 0
        if int(record.get("round", -1)) != int(combat.get("round", 0)):
            return 0
        if int(record.get("turn_index", -1)) != int(combat.get("turn_index", 0)):
            return 0
        return int(record.get("count", 0) or 0)

    def _validate_hit_riders(
        self, actor: dict[str, Any], intent: dict[str, Any], combat: dict[str, Any],
    ) -> None:
        """Reject a rider choice the actor cannot legally make right now.

        Every rejection happens here rather than being silently ignored at
        resolve time: a submitted ``stunning_strike`` the actor cannot pay for
        must fail the intent, not quietly produce a plain hit.
        """

        unarmed_type = intent.get("unarmed_damage_type")
        rider_type = intent.get("rider_damage_type")
        stunning = intent.get("stunning_strike")
        if intent.get("open_hand_choice") is not None:
            raise CombatIntentError(
                "open hand technique applies only to Flurry of Blows attacks"
            )
        if unarmed_type is None and rider_type is None and stunning is None:
            return
        weapon = self._declared_attack(actor, intent)
        if weapon is None:
            raise CombatIntentError("attack profile is not available to the actor")
        character = self._feature_character(actor)

        if unarmed_type is not None:
            if not weapon.get("unarmed"):
                raise CombatIntentError(
                    "an unarmed damage type applies to Unarmed Strikes only"
                )
            allowed = self.features.feature_value(
                character, "empowered_strikes", "unarmed_damage_type_choices", [],
            ) or []
            if unarmed_type not in allowed:
                raise CombatIntentError(
                    "the declared unarmed damage type is not available"
                )

        if rider_type is not None:
            allowed_types: set[str] = set()
            kind = rider_attack_kind(weapon)
            for feature_id in ("divine_strike", "primal_strike"):
                if not self.features.has_feature(character, feature_id):
                    continue
                kinds = {
                    str(value) for value in self.features.feature_value(
                        character, feature_id, "attack_kinds", [],
                    ) or []
                }
                if kind not in kinds and not (
                    kind != "unarmed_strike" and "weapon" in kinds
                ):
                    # 这一击不在该 rider 的 attack_kinds 里：声明它就是声明了一件
                    # 结算期会静默丢掉的事，必须当场拒绝。
                    continue
                values = self.features.feature_value(
                    character, feature_id, "damage_types", [],
                ) or []
                allowed_types.update(str(value) for value in values)
            if rider_type not in allowed_types:
                raise CombatIntentError(
                    "the declared rider damage type is not available"
                )

        if stunning is not None:
            if not isinstance(stunning, bool):
                raise CombatIntentError("stunning_strike must be a boolean")
            if not stunning:
                return
            if not self.features.has_feature(character, "stunning_strike"):
                raise CombatIntentError("stunning strike is not available to this actor")
            if not (weapon.get("unarmed") or weapon.get("ability_choice")):
                raise CombatIntentError(
                    "stunning strike requires a Monk weapon or an Unarmed Strike"
                )
            if self._feature_used_this_turn(
                combat, str(actor["actor_id"]), "stunning_strike",
            ):
                raise CombatIntentError("stunning strike was already used this turn")
            cost = int(
                self.features.feature_value(
                    character, "stunning_strike", "focus_cost", 0,
                ) or 0
            )
            current = 0
            for entry in actor.get("class_resources") or []:
                if isinstance(entry, dict) and str(entry.get("id")) == "focus_points":
                    current = int(entry.get("current", 0) or 0)
            if cost and current < cost:
                raise CombatIntentError(
                    "not enough Focus Points for a stunning strike"
                )

    def _canonical_actor(self, instance: Any, actor: dict[str, Any]) -> dict[str, Any]:
        if actor["kind"] == "player":
            return _canonical(instance.get_character_sheet(actor["id"]))
        if actor["kind"] == "companion":
            return self._companion(instance, actor["id"]).get("ruleset_character") or {}
        raise CombatIntentError("item activation is not available to this actor")

    def _activation_payload(self, instance: Any, actor: dict[str, Any]) -> Any:
        if actor["kind"] == "player":
            return instance.get_character_sheet(actor["id"])
        return self._companion(instance, actor["id"])

    def _validate_ammunition(
        self, instance: Any, actor: dict[str, Any], weapon: dict[str, Any],
    ) -> None:
        """A ranged profile that declares ammunition needs a carried copy.

        Companions have no live backpack in this slice; their ranged attacks
        keep the pre-D3 behaviour instead of inventing an inventory authority.
        """

        raw = weapon.get("ammunition_item_ref")
        if not raw or actor["kind"] != "player":
            return
        try:
            item_ref = canonical_item_ref(raw)
        except AttunementError as exc:
            raise CombatIntentError(str(exc)) from None
        payload = instance.get_character_sheet(actor["id"])
        if inventory_ref_counts(payload).get(item_ref, 0) < 1:
            raise CombatIntentError("no ammunition for this attack")

    def _validate_item_activation(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        if actor["kind"] not in {"player", "companion"}:
            raise CombatIntentError("item activation is not available to this actor")
        try:
            item_ref = canonical_item_ref(intent.get("item_ref"))
        except AttunementError as exc:
            raise CombatIntentError(str(exc)) from None
        item = self.bundle.get("item", item_ref.removeprefix("item:"))
        if not isinstance(item, dict):
            raise CombatIntentError("item is not defined in the active bundle")
        if self._item_is_merged_in_form(instance, actor["actor_id"], item_ref):
            # merged 的含义就是"没有效果"，不是"在 UI 里藏起来"：连激活也不行。
            raise CombatIntentError(
                "that item has no effect while you are shape-shifted (merged)"
            )
        try:
            declaration = validate_activation_declaration(item)
            maximum = validate_charges_declaration(item)
            consumable = validate_consumable_declaration(item)
            # 销毁判定在全部效果之后才掷骰；声明有误要在这里拒绝，
            # 不能等结算到一半才抛出。
            validate_destroy_declaration(item)
        except (ItemActivationError, ChargesError) as exc:
            raise CombatIntentError(str(exc)) from None
        if declaration is None:
            raise CombatIntentError("item cannot be activated")
        if consumable and actor["kind"] == "companion":
            # companion 还没有权威背包与原子扣件路径：在动作成本、充能和
            # consumed 事件之前拒绝，避免把 quantity 消耗留在玩家事件链之外。
            raise CombatIntentError(
                "companion consumable activation requires an authoritative backpack"
            )
        cost = declaration["cost"]
        if int(economy.get(cost, 0) or 0) < 1:
            raise CombatIntentError(f"the {cost.replace('_', ' ')} has already been spent")
        canonical = self._canonical_actor(instance, actor)
        payload = self._activation_payload(instance, actor)
        # Consumables are removed from the live inventory when the batch is
        # applied.  Equipment rows and canonical equipment refs are not a
        # backpack take path, so a consumable must prove an inventory copy here
        # instead of passing validation and failing after the roll.
        counted = inventory_ref_counts(payload) if consumable else owned_ref_counts(payload)
        owned = counted.get(item_ref, 0)
        if owned < 1:
            raise CombatIntentError("item is not carried")
        if item_requires_attunement(self.bundle, item_ref) and item_ref not in attuned_refs(canonical):
            raise CombatIntentError("item requires attunement")
        charge_cost = declaration.get("charge_cost")
        if maximum is not None:
            if charge_cost is None:
                raise CombatIntentError("charged item activation requires charge_cost")
            if owned > 1:
                raise CombatIntentError("cannot activate a charged item carried in multiple copies")
            current = charges_of(canonical, item_ref)
            if current is None:
                raise CombatIntentError("item charges are not initialized")
            if current < int(charge_cost):
                raise CombatIntentError("not enough charges")
        elif charge_cost is not None:
            raise CombatIntentError("item has no charges")
        if consumable and owned < 1:
            raise CombatIntentError("item is not carried")
        target_id = str(intent.get("target_id") or actor["actor_id"])
        if declaration["target"] == "self" and target_id != actor["actor_id"]:
            raise CombatIntentError("this item can only target its user")
        target = self._actor_view(instance, combat, target_id)
        if target["kind"] not in {"player", "companion"}:
            raise CombatIntentError("item targets a party member or the user")
        if str(target.get("side") or "") != str(actor.get("side") or ""):
            raise CombatIntentError("item targets a party member or the user")
        if "dead" in (target.get("conditions") or {}):
            raise CombatIntentError("item cannot target a dead actor")
        # Using an item on an ally is a touch-range action: the user must be
        # within 5 feet.  Self-use has no separate distance check.
        if target_id != actor["actor_id"]:
            distance = self._distance(combat, actor["actor_id"], target_id)
            if distance > 5:
                raise CombatIntentError(
                    f"ally is out of item range ({distance} > 5 feet)"
                )

    def _validate_spell(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        if actor["kind"] not in {"player", "companion"}:
            raise CombatIntentError("enemy spell profiles are not enabled for this actor")
        if "rage" in actor["conditions"]:
            raise CombatIntentError("you cannot cast spells while raging")
        if bool((actor.get("armor_training") or {}).get("untrained_armor_worn")):
            raise CombatIntentError("armor you are not trained in blocks spellcasting")
        spell_ref = str(intent.get("spell_ref") or "")
        spell = self.spells.get(spell_ref)
        effect = self.catalog.spell_effects.get(spell_ref.removeprefix("spell:"))
        if spell is None or effect is None:
            raise CombatIntentError("spell is not deterministically automated yet")
        if spell_ref not in actor["spell_refs"]:
            raise CombatIntentError("spell is not prepared or known by the actor")
        if effect["mode"] == "reaction_defense":
            raise CombatIntentError("this spell is cast as a reaction when you are hit")
        trigger = hit_spell_decision(combat, actor["actor_id"])
        if effect["mode"] == "triggered_damage" and trigger is None:
            raise CombatIntentError("this spell requires a pending melee hit")
        if trigger is not None and effect["mode"] != "triggered_damage":
            raise CombatIntentError("resolve the melee hit spell decision first")
        if intent.get("retarget_mark"):
            # 转移标记是一次独立的声明：它复用同一个法术实例，因此不走
            # 施法成本、目标数量与免费次数那一整套。
            self._validate_mark_retarget(instance, combat, intent, actor, economy, effect)
            return
        if intent.get("spend_luck") and effect["mode"] != "spell_attack":
            raise CombatIntentError("spend_luck requires a spell attack roll")
        self._validate_lucky_spend(instance, intent, actor)
        cost = "bonus_action" if str(spell["casting_time"]).startswith("Bonus Action") else "action"
        if int(economy.get(cost, 0) or 0) < 1:
            raise CombatIntentError(f"the {cost.replace('_', ' ')} has already been spent")
        slot_level = intent.get("slot_level", spell["level"])
        if isinstance(slot_level, bool) or not isinstance(slot_level, int):
            raise CombatIntentError("slot_level must be an integer")
        if spell["level"] == 0:
            if slot_level != 0:
                raise CombatIntentError("a cantrip does not use a spell slot")
        else:
            if slot_level == 0:
                if free_cast_resource(actor, spell_ref, int(spell["level"])) is None:
                    raise CombatIntentError("the free spell cast is not available")
            elif economy.get("slot_spell_cast"):
                raise CombatIntentError("only one spell slot can be expended on a turn")
            if slot_level != 0 and slot_level < spell["level"]:
                raise CombatIntentError("slot_level is lower than the spell level")
            if slot_level != 0 and int(actor["slots"].get(str(slot_level), 0) or 0) < 1 \
                    and int((actor.get("font_slots") or {}).get(str(slot_level), 0) or 0) < 1:
                raise CombatIntentError("the selected spell slot is not available")
        raw_targets = intent.get("target_ids")
        target_values = raw_targets if isinstance(raw_targets, list) else [intent.get("target_id")]
        target_ids: list[str] = [
            str(item or "") for item in target_values if str(item or "")
        ]
        required_count = int(effect.get("target_count", 1) or 1)
        upcast_targets = int(effect.get("upcast_target_count", 0) or 0)
        if upcast_targets:
            required_count += upcast_targets * max(0, slot_level - int(spell["level"]))
        has_duplicates = len(set(target_ids)) != len(target_ids)
        if not target_ids or len(target_ids) > required_count or (
            has_duplicates and not effect.get("repeat_targets")
        ):
            raise CombatIntentError(f"spell requires 1 to {required_count} unique targets")
        if effect["mode"] == "triggered_damage" and target_ids != [trigger["target_id"]]:
            raise CombatIntentError("this spell must target the creature hit by the melee attack")
        for target_id in target_ids:
            target = self._actor_view(instance, combat, target_id)
            actor_side = str(actor.get("side") or "")
            target_side = str(target.get("side") or "")
            if effect["mode"] in {
                "healing", "buff", "stabilize", "temp_hp", "teleport", "revive",
            }:
                if target_side != actor_side:
                    raise CombatIntentError("healing and beneficial spells require an allied target")
                if effect["mode"] == "revive":
                    if target["kind"] != "player":
                        raise CombatIntentError("revive spells target players only")
                    if "dead" not in target.get("conditions", {}):
                        raise CombatIntentError("revive spells require a dead target")
                elif "dead" in target.get("conditions", {}):
                    raise CombatIntentError("dead targets require a resurrection effect")
            elif target_side == actor_side and effect["mode"] not in {
                "dispel", "obscuring_zone",
            }:
                # 区域法术只借用目标所在的坐标来定圆心，不区分敌我：
                # 一团雾盖住自己也盖住敌人。
                raise CombatIntentError("offensive spells require a hostile target")
            if effect.get("target_scope") == "self" and target_id != str(actor["actor_id"]):
                # 声明式 Self 约束：内容声明什么，这里就只校验什么，
                # 不再为具体法术 mode 复制特判。
                raise CombatIntentError("this spell can only target the caster")
            if effect["mode"] == "dispel" and "dead" in target.get("conditions", {}):
                # 解除魔法可对任意阵营，但死亡目标不在"生物"范围内。
                raise CombatIntentError("dead targets cannot be dispelled")
            if effect["mode"] == "stabilize" and (
                target["kind"] != "player" or target["hp"] > 0
                or "stable" in target.get("conditions", {})
            ):
                raise CombatIntentError("this spell requires a dying ally at 0 HP")
            if target["hp"] <= 0 and effect["mode"] not in {
                "healing", "buff", "stabilize", "dispel", "revive",
            }:
                raise CombatIntentError("spell target is not active")
            allowed_types = effect.get("target_creature_types")
            if allowed_types and str(target.get("creature_type") or "") not in allowed_types:
                raise CombatIntentError("the spell target's creature type is not eligible")
            if effect["mode"] != "triggered_damage":
                # 射程是特性可能改写过的值（德鲁伊 15 的元素之怒），不是目录原文。
                self._require_range(
                    combat, actor["actor_id"], target_id,
                    self._spell_effective_range(actor, spell, effect),
                )
            if effect.get("marks_target") and not self._spell_target_visible(
                instance, combat, actor, target,
            ):
                # 只有"你能看见的一个生物"才能被标记；不可见目标在 30 尺内
                # 被 Blindsight 覆盖时算看得见，这正是 Feral Senses 的作用点。
                raise CombatIntentError("you cannot see the creature you are marking")
        self._validate_cantrip_temp_hp_target(instance, combat, intent, actor, spell, effect)
        damage_choices = effect.get("damage_type_choice")
        if damage_choices and intent.get("damage_type") not in damage_choices:
            raise CombatIntentError("spell damage_type choice is required")
        resist_choices = effect.get("resist_choice")
        if resist_choices:
            if intent.get("resist_type") not in resist_choices:
                raise CombatIntentError("spell resist_type choice is required")
        elif intent.get("resist_type") is not None:
            raise CombatIntentError("this spell does not take a resist_type")
        maximise = intent.get("overchannel")
        if maximise is not None and not isinstance(maximise, bool):
            raise CombatIntentError("overchannel must be a boolean")
        if maximise:
            character = self._feature_character(actor)
            if not self.features.has_feature(
                character, OVERCHANNEL_FEATURE_ID, owner=OVERCHANNEL_OWNER,
            ):
                raise CombatIntentError("overchannel is required to maximize damage")
            # 列表归属来自官方目录的 `class_refs`；学派不是门槛。
            if OVERCHANNEL_SPELL_CLASS not in spell["class_refs"]:
                raise CombatIntentError("overchannel only applies to Wizard spells")
            if effect["mode"] not in {"spell_attack", "saving_throw", "automatic_damage"} \
                    or not effect.get("damage"):
                raise CombatIntentError("this spell does not deal damage")
            max_slot = int(self.features.feature_value(
                character, OVERCHANNEL_FEATURE_ID, "maximum_slot_level", 0,
                owner=OVERCHANNEL_OWNER,
            ) or 0)
            if not 1 <= int(slot_level) <= max_slot:
                raise CombatIntentError(
                    f"maximized casting requires a spell slot of level 1-{max_slot}"
                )
            # 坏计数必须在这里炸掉，不能落成"首次免费"。
            try:
                feature_usage_count(
                    character, OVERCHANNEL_OWNER, OVERCHANNEL_FEATURE_ID,
                )
            except ClassFeatureError as exc:
                raise CombatIntentError(str(exc)) from exc
        if effect["mode"] == "teleport":
            distance = intent.get("teleport_distance")
            if isinstance(distance, bool) or not isinstance(distance, int) or distance == 0:
                raise CombatIntentError("teleport distance must be a non-zero integer")
            if abs(distance) > int(effect["range"]):
                raise CombatIntentError("teleport distance exceeds the spell range")
        elif intent.get("teleport_distance") is not None:
            raise CombatIntentError("teleport distance is not valid for this spell")

    def _spell_target_visible(
        self, instance: Any, combat: dict[str, Any],
        actor: dict[str, Any], target: dict[str, Any],
    ) -> bool:
        """Whether the caster can see this creature right now.

        Visibility is not "is it invisible": it is "does its Invisible still
        hide it from *me*".  The registry owns the second half -- the negations
        that cancel ``invisible`` -- so this method asks the same list the
        attack matrix asks, at this distance, instead of inventing a second
        rule for spell targeting.
        """

        target_conditions = target.get("conditions") or {}
        if "invisible" not in target_conditions:
            return True
        negations: set[str] = set()
        for declaration in (
            (self.catalog.conditions.get("invisible") or {}).get("attack_modes") or []
        ):
            negations.update(str(code) for code in declaration.get("negated_by") or [])
        distance = self._distance(
            combat, str(actor["actor_id"]), str(target["actor_id"]),
        )
        return bool(negations & vision_codes(self, actor, distance=distance))

    def _validate_mark_retarget(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], economy: dict[str, Any], effect: dict[str, Any],
    ) -> None:
        """Every precondition of "move the mark" is checked before any event.

        The move is free on purpose: it reuses the instance, so it must not
        look like a second cast to validation, the reducer or the log.  What it
        does cost is the Bonus Action, and what it requires is that the spell
        it moves is still *this caster's* current instance.
        """

        if not effect.get("marks_target"):
            raise CombatIntentError("this spell has no mark to move")
        if not owned_mark(actor):
            raise CombatIntentError("there is no Hunter's Mark to move")
        if int(economy.get("bonus_action", 0) or 0) < 1:
            raise CombatIntentError("the bonus action has already been spent")
        raw = intent.get("target_ids")
        target_ids = [str(item) for item in raw] if isinstance(raw, list) else []
        if len(target_ids) != 1 or not target_ids[0]:
            raise CombatIntentError("the mark moves to exactly one creature")
        new_target_id = target_ids[0]
        current_id = str(owned_mark(actor).get("mark_target_id") or "")
        if not current_id:
            raise CombatIntentError("this Hunter's Mark has no target")
        if new_target_id == current_id:
            raise CombatIntentError("the mark is already on that creature")
        current = self._actor_view(instance, combat, current_id)
        if int(current.get("hp", 0) or 0) > 0:
            raise CombatIntentError(
                "the mark can only move after its target drops to 0 hit points"
            )
        target = self._actor_view(instance, combat, new_target_id)
        if int(target.get("hp", 0) or 0) <= 0:
            raise CombatIntentError("spell target is not active")
        if str(target.get("side") or "") == str(actor.get("side") or ""):
            # 与施放同一条边界：标记属于进攻性法术，不能落在盟友身上。
            raise CombatIntentError("Hunter's Mark marks a hostile creature")
        self._require_range(
            combat, str(actor["actor_id"]), new_target_id,
            int(effect.get("mark_retarget_range") or effect.get("range") or 0),
        )
        if not self._spell_target_visible(instance, combat, actor, target):
            raise CombatIntentError("you cannot see the new target")

    def _validate_cantrip_temp_hp_target(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], spell: dict[str, Any], effect: dict[str, Any],
    ) -> None:
        """Improved Blessed Strikes' temp HP grant, checked before any dice roll.

        Every clause of "when you cast a Cleric cantrip that deals damage, you
        can grant ... to yourself or one creature you can see within 60 feet"
        is a separate gate: the feature, the Potent Spellcasting option being
        the one actually chosen, a damaging cantrip, an ally, and the range.
        Granting on a hostile is refused: temporary hit points are a benefit,
        and "one creature" is never read as "any creature, including the one I
        am shooting".
        """

        raw = intent.get("cantrip_temp_hp_target_id")
        if raw is None:
            return
        if not isinstance(raw, str) or not raw:
            raise CombatIntentError("cantrip_temp_hp_target_id must be an actor id")
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "improved_blessed_strikes"):
            raise CombatIntentError(
                "improved blessed strikes is required to grant temporary hit points"
            )
        if int(spell.get("level", 0) or 0) != 0 or not effect.get("damage"):
            raise CombatIntentError("the temporary hit points require a damaging cantrip")
        if self._potent_spellcasting_owner(actor, spell) != "class:cleric":
            raise CombatIntentError(
                "cleric potent spellcasting is required to grant the temporary hit points"
            )
        target = self._actor_view(instance, combat, raw)
        if str(target.get("side") or "") != str(actor.get("side") or ""):
            raise CombatIntentError(
                "the temporary hit points must go to the caster or an ally"
            )
        if "dead" in (target.get("conditions") or {}):
            raise CombatIntentError("the temporary hit point target is not active")
        if raw != str(actor["actor_id"]):
            self._require_range(
                combat, str(actor["actor_id"]), raw,
                int(self.features.feature_value(
                    character, "improved_blessed_strikes", "temp_hp_range_ft", 0,
                ) or 0),
            )

    def _validate_initiative_choices(self, instance: Any, intent: dict[str, Any]) -> None:
        """Validate the Initiative-time declarations before any dice are rolled.

        A declaration is a yes/no for an optional feature.  It cannot name
        another player, cannot spell out an amount, and cannot be made when the
        feature is missing or its Long Rest charge is already spent -- all three
        would otherwise turn into a free resource on the next replay.
        """

        raw = intent.get("initiative_choices")
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise CombatIntentError("initiative choices must be an object")
        for actor_id, entry in raw.items():
            actor = str(actor_id)
            kind, raw_id = _actor_kind(actor)
            if kind != "player" or raw_id not in instance.players:
                raise CombatIntentError("initiative choices can only name a player")
            if not isinstance(entry, dict):
                raise CombatIntentError("an initiative choice entry must be an object")
            unknown = sorted(set(entry) - INITIATIVE_CHOICES)
            if unknown:
                raise CombatIntentError(f"unknown initiative choice: {unknown[0]}")
            for value in entry.values():
                if value is not True and value is not False:
                    raise CombatIntentError("initiative choices only accept true or false")
            character = _canonical(instance.get_character_sheet(raw_id))
            for choice in sorted(INITIATIVE_CHOICES):
                if entry.get(choice) is not True:
                    continue
                if not self.features.has_feature(character, choice):
                    raise CombatIntentError(f"this character cannot use {choice}")
                if int(resource_state(character, choice).get("current", 0) or 0) < 1:
                    raise CombatIntentError(f"{choice} is already spent")

    @staticmethod
    def _validate_player_positions(instance: Any, positions: Any) -> None:
        if positions is None:
            return
        if not isinstance(positions, dict) or len(positions) > len(instance.players):
            raise CombatIntentError("player_positions must map current players to positions")
        for uid, value in positions.items():
            if uid not in instance.players:
                raise CombatIntentError("player_positions contains an unknown player")
            if isinstance(value, bool) or not isinstance(value, int) or not -10000 <= value <= 10000:
                raise CombatIntentError("player position is invalid")

    @staticmethod
    def _validate_mounted_players(instance: Any, mounted_player_ids: Any) -> None:
        """骑乘声明只接受在场玩家 id。

        骑乘目前只回答一个问题："骑枪是否还需要两只手"。因此它按遭遇声明、
        随遭遇结束，不做持久化；未知或重复的 id 一律拒绝，避免客户端用列表
        长度或花式 id 绕过边界。
        """

        if mounted_player_ids is None:
            return
        if not isinstance(mounted_player_ids, list):
            raise CombatIntentError("mounted_player_ids must be an array")
        seen: set[str] = set()
        for uid in mounted_player_ids:
            if not isinstance(uid, str) or not uid or uid not in instance.players:
                raise CombatIntentError("mounted_player_ids contains an unknown player")
            if uid in seen:
                raise CombatIntentError("mounted_player_ids contains a duplicate")
            seen.add(uid)
