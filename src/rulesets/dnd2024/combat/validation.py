"""Intent validation stage for D&D 2024 combat."""

from __future__ import annotations

import re
from typing import Any

from src.engine.player_control import is_ai_controlled
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
from ..features.combat import relaxed_bonus_action_targets
from ..features.attack_declarations import (
    BRUTAL_STRIKE, CUNNING_STRIKE_EFFECTS_KEY, LIGHT_EXTRA_ATTACK, SELECTION_KEY,
    STUNNING_STRIKE, declaration_value, selection_error, validate_cunning_strike,
    validate_uses, validate_weapon_selection,
)
from ..features.kinds import KIND_HANDLERS, KindContext
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
from ..spells.font_of_magic import declaring_class_level
from ..spells.font_of_magic import (
    FontOfMagicError,
    create_slot_plan,
    declaring_class_level,
    parse_created_slot_costs,
    slot_to_points_plan,
)
from ..spells.metamagic import MetamagicError
from ..character.primitives import ability_modifier
from .cover import COVER_LEVELS
from .obscurement import (
    LAND_AID_RANGE_FT,
    OBSCUREMENT_LEVELS,
    SANCTUARY_MOVE_LIMIT_FT,
    SANCTUARY_RANGE_FT,
    sanctuary_zone_id,
)
from .primitives import (
    slot_available,
    CONDITION_CODES,
    DICE_RE,
    GM_CONDITION_DURATIONS,
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
    stand_up_declaration,
    SIZE_CODES,
    TRAIT_IDS,
    TRAIT_PARAMS,
    CombatIntentError,
    actor_kind as _actor_kind,
    canonical as _canonical,
    rider_attack_kind,
    world_minutes,
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
from .validation_attack import CombatValidationAttackMixin
from .validation_spells import CombatValidationSpellsMixin
from .validation_forms import CombatValidationFormsMixin
from .validation_choices import CombatValidationChoicesMixin
from .validation_movement import CombatValidationMovementMixin


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


class CombatValidationMixin(
    CombatValidationAttackMixin,
    CombatValidationSpellsMixin,
    CombatValidationFormsMixin,
    CombatValidationChoicesMixin,
    CombatValidationMovementMixin,
):
    """Validate combat declarations; composes the five validation mixins."""

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
        error = selection_error(intent, intent_type)
        if error:
            raise CombatIntentError(error)
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
        if intent_type == "gm_condition":
            # 状态是规则书事实，所以只有 GM 能裁定；客户端提交的是状态 id
            # 与动作，永远不是"这个状态会做什么"（那由 catalog 解释器回答）。
            if submitted_by != gm_uid:
                raise CombatIntentError("only the GM can apply or remove a condition")
            target_id = str(intent.get("target_id") or "")
            condition = str(intent.get("condition") or "")
            action = str(intent.get("action") or "")
            if action not in {"apply", "remove"}:
                raise CombatIntentError("condition action must be apply or remove")
            if not condition:
                raise CombatIntentError("condition id is required")
            # 施加只认规则书状态：法术/特性/物品自己制造的效应不能手工授予，
            # 否则规则就会凭空多出一个来源。移除则宽松——摘掉什么都是清理动作。
            if action == "apply" and condition not in self.catalog.gm_appliable_conditions():
                raise CombatIntentError(
                    f"{condition} is not a condition the GM can apply"
                )
            duration = intent.get("duration")
            if duration is not None and (
                not isinstance(duration, str) or duration not in GM_CONDITION_DURATIONS
            ):
                # 时长是引擎的标记，不是自由文本：认不出的标记既不会到期，也不随
                # 战斗结束清除，状态会一直留在角色卡上。
                raise CombatIntentError(
                    "condition duration must be one of "
                    f"{sorted(GM_CONDITION_DURATIONS)}"
                )
            kind, raw_id = _actor_kind(target_id)
            if kind == "player":
                if raw_id not in instance.players:
                    raise CombatIntentError("condition target actor does not exist")
            elif kind == "enemy":
                if raw_id not in (combat.get("enemies") or {}):
                    raise CombatIntentError("condition target actor does not exist")
            else:
                raise CombatIntentError("condition target must be a combat actor")
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
        # 特性资格排在版本、回合与归属之后：轮不到的人、别人的角色，先拿到的是
        # 拒绝本身，而不是这个角色还剩几次 Wild Shape。
        if intent_type in {"wild_resurgence", "nature_magician"}:
            self._validate_druid_conversion(instance, intent, state)
        if intent_type in {"form.assume", "form.revert"}:
            self._validate_form_intent(instance, intent)
        if intent_type in {"land_aid", "nature_sanctuary"}:
            self._validate_land_feature(instance, intent, state)
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
            "disengage", "hide", "stabilize", "form.assume", "form.revert",
            "land_aid", "nature_sanctuary",
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
            if intent.get("ritual"):
                # 仪式是 10 分钟的施法方式，战斗里没有这个时间；静默按普通施法
                # 处理等于偷偷免掉了那 10 分钟，所以显式拒绝而不是忽略字段。
                raise CombatIntentError("ritual casting is not available in combat")
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

    ABILITY_CHECK_COSTS = frozenset({"action", "bonus_action", "none"})

    _WILD_SHAPE_CR_BY_LEVEL = ((4, "1/4"), (8, "1/2"), (99, "1"))

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
            # 放宽关系写在授予者（Fleet Step）身上，所以这里读声明：只有它
            # 点名的那个能力的各个使用选项才能走"免槽"路径。按能力名判会把
            # 同一特性的另一个使用选项（花气档）漏掉。
            if capability_id not in self._relaxed_capability_ids(actor):
                raise CombatIntentError(
                    "Fleet Step can only grant the step it declares"
                )
            if not bool(economy.get("fleet_step_available")):
                raise CombatIntentError("Fleet Step is not available this turn")
        capability = self.features.declared_capability(
            self._feature_character(actor), capability_id,
        )
        if capability is not None:
            if capability.uses_per_long_rest:
                used = feature_usage_count(
                    self._feature_character(actor), capability.feature_id,
                    capability.feature_id,
                )
                if used >= capability.uses_per_long_rest:
                    raise CombatIntentError(
                        "this feature has no use left until a long rest"
                    )
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
            feature_character = self._feature_character(actor)
            if (
                capability.requires_feature_id
                and not self.features.has_feature(
                    feature_character, capability.requires_feature_id,
                )
            ):
                # 第二条价格挂在更高的另一条特性上：能力属于低等级特性，所以
                # 这里要问"那条特性在不在"，而不是把等级写进引擎。
                raise CombatIntentError(
                    f"{self.features.catalog.label(capability_id, capability_id)} requires "
                    f"{self.features.catalog.label(capability.requires_feature_id, capability.requires_feature_id)}"
                )
            if capability.exhausted_resource_id:
                # 与池相反：还有余额时这条价格根本不成立。
                state = resource_state(
                    feature_character, capability.exhausted_resource_id,
                )
                if int(state.get("current", 0) or 0) > 0:
                    raise CombatIntentError(
                        f"{self.features.catalog.label(capability.exhausted_resource_id, capability.exhausted_resource_id)} "
                        "is not exhausted yet"
                    )
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
        if capability is not None:
            context = KindContext(self, instance, combat, actor, intent)
            for action in capability.actions:
                validate = KIND_HANDLERS[action.kind].validate
                if validate is not None:
                    reason = validate(capability, action, context)
                    if reason:
                        raise CombatIntentError(reason)
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
        # 按 (授予者, 能力) 建索引，不按能力 id 单独索引：专长与子职可能声明
        # 同名能力，只按 id 取会让"校验用的 capability"和"定价用的 view"来自
        # 不同的授予者。
        views = {
            (view.feature_id, view.id): view
            for view in self._available_class_capabilities(
                actor, economy, include_unavailable=True,
            )
        }
        view = (
            views.get((capability.feature_id, capability_id))
            if capability is not None else None
        )
        if view is None:
            raise CombatIntentError("class capability is not available to this actor")
        if not view.available:
            raise CombatIntentError(view.blocked_reason or "class capability is not available")
        if capability is None:  # pragma: no cover - availability already proved it
            raise CombatIntentError("class capability is not available to this actor")
        if SELECTION_KEY in intent and declaration_value(intent, STUNNING_STRIKE) and not any(
            KIND_HANDLERS[action.kind].grants_attack for action in capability.actions
        ):
            raise CombatIntentError("Stunning Strike requires a granted attack")
        context = KindContext(self, instance, combat, actor, intent)
        for action in capability.actions:
            validate = KIND_HANDLERS[action.kind].validate_available
            if validate is not None:
                validate(capability, action, context)
        if capability.requires_hostile_target:
            target = self._actor_view(
                instance, combat, str(intent.get("target_id") or ""),
            )
            if str(target.get("side") or "") == str(actor.get("side") or "") or target["hp"] <= 0:
                raise CombatIntentError("this capability requires a living hostile target")

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
        if not slot_available(economy, cost):
            raise CombatIntentError(f"the {cost.replace('_', ' ')} has already been spent")
        # Quickened 的双向互斥都靠这一条：本回合只要施放过 1 环+ 法术（含免费
        # 施法与专长来源），就不能再 Quickened，也不能在被 Quickened 之后放。

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
