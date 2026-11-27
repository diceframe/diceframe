"""Typed, validated D&D 2024 class feature declarations.

This module owns the *shape* of the D&D-owned feature catalog and the
presentation-neutral views the runtime exposes.  It deliberately contains no
combat resolution and no state mutation: identity, availability, and scalar
parameters only.

The authoritative source of "what a character owns" stays the ruleset bundle:
``progression_catalog`` says which feature ids a class table grants at each
level, and ``class_feature_catalog`` parameterizes those ids (minimum level,
resource link, capability declaration, display labels).  Nothing here may be
read from a localized display name, and nothing here is persisted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.primitives import ABILITY_IDS


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_OWNER_RE = re.compile(r"^(class|subclass):[a-z0-9][a-z0-9_.-]*$")
# 动作槽的名字。一个能力最多占一个槽。目录里的旧写法把槽名当键
# （``action: 1`` / ``bonus_action: 1``），新写法是 ``slot: "action"``；两种都
# 读，同时出现视为声明错误，不让"占不占动作"有两个来源。
ACTION_COST_KINDS = ("action", "bonus_action")
SLOT_COST_KEY = "slot"
# 回合开始窗口的支付时机。与动作槽互斥：一个能力要么花动作，要么只在自己
# 回合开始时花资源 —— 两者同时声明会让"这个能力到底占不占动作"没有唯一答案。
# 旧写法是 ``turn_start: true``，新写法是 ``timing: "turn_start"``。
TURN_START_COST_KEY = "turn_start"
TURN_START_TIMING = "turn_start"
TIMING_COST_KEY = "timing"
COST_TIMINGS = frozenset({TURN_START_TIMING})
# 装备前提里可声明的武器类别；同样 fail closed。``simple_melee`` 是 SRD 的
# Simple Melee 武器栏，``martial_melee_light`` 是带 Light 的 Martial Melee 武器。
WEAPON_KINDS = frozenset({"simple_melee", "martial_melee_light"})
EQUIPMENT_REQUIREMENT_KEYS = frozenset(
    {"unarmored", "no_shield", "no_heavy_armor", "weapon_kinds"}
)
# 本切片只支持成长专长选择与技能熟练类选择；未知 kind 必须加载期拒绝，
# 不能静默当成无选项特性。``skill_proficiency`` 是 Expertise / Scholar 这类
# "在已有技能熟练里再选几项"的选择：它的候选项来自技能目录而不是专长目录，
# 且可以一次选多项（Expertise 一次两项）——用 ``advancement_feat`` 表达不了。
FEATURE_CHOICE_KINDS = frozenset({
    "advancement_feat", "skill_proficiency", "feature_option", "language",
})
# 技能选择最终"授予什么"的闭集。``expertise`` 是子职侧的加值翻倍，
# ``proficiency`` 是专长侧（Skilled）的"直接获得熟练"——两者都写在这张表里，
# 因为玩家看到的是同一个"选技能"动作，只有结果不同。未知值必须加载期拒绝，
# 否则选中一个技能却在运行时什么都不发生。
#: 选中的技能"得到什么"：Expertise 是加倍，proficiency 是新增熟练本身
#: （Primal Knowledge / Bonus Proficiencies 那种"再选一个技能"）。
SKILL_CHOICE_GRANTS = frozenset({"expertise", "proficiency"})

#: 修正能作用的掷骰。**只有 skill_check**：这是本片唯一有消费者的面。
#: 攻击、伤害、裸属性检定与豁免都还没有声明用到它们，而一个没有消费者的
#: 枚举值等于"内容能写、引擎静默丢弃"的假契约——等真有特性要用时再加。
CHECK_MODIFIER_SURFACES = frozenset({"skill_check"})
_CHECK_MODIFIER_KEYS = frozenset({
    "surface", "skills", "abilities", "when_condition",
    "replace_ability", "add",
})
_CHECK_ADD_KEYS = frozenset({"source", "ability", "value", "minimum", "maximum", "multiplier"})
_CHECK_ADD_SOURCES = frozenset({"constant", "ability_modifier", "proficiency_bonus"})
# 特性参数的射程键统一以英尺计。``range_ft`` 是"该特性对目标生效的射程"，
# 与前缀式 ``aura_range_ft``/``defeat_share_range_ft`` 并列：后者是"一个特性有
# 多条射程"时才需要的消歧前缀，单条射程用裸键即可。
#
# 一个特性同时声明 ``range_ft`` 和 ``trigger``，就是一张"掷后窗口"声明：
# 某个事件发生了，且目标在射程内，窗口才打开。两个键互为前提，所以
# ``trigger`` 的闭集只在声明了 ``range_ft`` 时校验——其他机制（例如
# Dark One's Blessing 的 defeat payload）有各自的 trigger 词汇，由各自的
# 消费方校验，不能被这张表的闭集误伤。
CHECK_WINDOW_TRIGGERS = frozenset({
    "attack_roll_success", "grant", "own_check_failure", "save_failure",
})
# 一个能力允许声明的目标集。``creature`` 是"另一个活着的生物"（可以是友方，
# 不能是自己），``bloodied_creature`` 追加"结算时已受伤过半"这一条，
# ``undead`` 只接受亡灵，``none`` 表示不取目标（例如自身侦测）。
TARGET_KINDS = frozenset({
    "none", "creature", "bloodied_creature", "undead", "hostile",
})


class ClassFeatureError(ValueError):
    """Raised when a class feature catalog cannot be used authoritatively."""


def _require_id(value: Any, field_name: str) -> str:
    text = str(value or "")
    if not _ID_RE.fullmatch(text):
        raise ClassFeatureError(f"{field_name} is not a canonical id: {value!r}")
    return text


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ClassFeatureError(f"{field_name} must not be empty")
    return text


def _require_owner(value: Any, field_name: str, class_id: str) -> str:
    """Validate an owner reference, defaulting an omitted class owner.

    A feature declared in ``classes.<id>.features`` without an explicit owner
    belongs to that class.  Subclass owners are always explicit: the catalog
    cannot infer a subclass from the class table, because the character may not
    have selected one.
    """

    text = str(value or "").strip()
    if not text:
        return f"class:{class_id}"
    if not _OWNER_RE.fullmatch(text):
        raise ClassFeatureError(f"{field_name} is not a valid owner ref: {value!r}")
    if text.startswith("class:") and text != f"class:{class_id}":
        raise ClassFeatureError(
            f"{field_name} must name the declaring class: {text!r}"
        )
    return text


def _require_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ClassFeatureError(f"{field_name} must be a positive integer")
    return value


def _require_scalar(value: Any, field_name: str) -> Any:
    if isinstance(value, list):
        for item in value:
            _require_scalar(item, field_name)
        return list(value)
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
        return value
    if isinstance(value, str):
        return value
    raise ClassFeatureError(f"{field_name} must contain scalars only")


@dataclass(frozen=True, slots=True)
class EquipmentRequirement:
    """The canonical equipment precondition a class feature declares.

    ``unarmored`` / ``no_shield`` / ``no_heavy_armor`` are evaluated against the
    character's item types and armor categories; a non-empty ``weapon_kinds``
    additionally requires *every* wielded weapon to match one of the declared
    kinds.  The same kinds are the weapons the feature's own weapon benefits
    cover (Martial Arts: the Monk Weapon), so a feature never repeats the weapon
    definition in a second table.

    ``no_heavy_armor`` is the precondition Fast Movement and Roving state:
    Light, Medium, and no armor all keep the benefit, and a Shield never
    interferes.
    """

    unarmored: bool = False
    no_shield: bool = False
    no_heavy_armor: bool = False
    weapon_kinds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceCost:
    """One class-resource price of a capability."""

    resource_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class SpellSlotCost:
    """Exchange one slot of an exact level from the class spell slot table."""

    level: int


@dataclass(frozen=True, slots=True)
class CapabilityCost:
    """The action-slot, resource and timing price of one capability.

    The three fields answer three separate questions, so none of them can be
    derived from another:

    * ``slot`` -- which action-economy slot it occupies (``action`` or
      ``bonus_action``); empty means it occupies none.
    * ``resources`` -- what it spends: spell slots, Focus, uses, resource pools.
    * ``timing`` -- when it may be paid. ``turn_start`` is a timing
      restriction, not a cheaper action: Superior Defense is "at the start of
      your turn", so it consumes no slot and cannot be declared later in the
      turn.  Folding it into ``slot`` would make one field answer both
      "what does it cost" and "when can I pay".

    ``action`` and ``turn_start`` remain as read-only aliases for callers that
    predate the split; writes go through the fields above.
    """

    slot: str = ""
    resources: tuple[ResourceCost | SpellSlotCost, ...] = ()
    timing: str = ""

    @property
    def action(self) -> str:
        return self.slot

    @property
    def turn_start(self) -> bool:
        return self.timing == TURN_START_TIMING


@dataclass(frozen=True, slots=True)
class CapabilityAction:
    """One underlying canonical action a capability performs.

    ``effect_id`` names *what* the action acts on when the kind alone is not
    enough.  ``end_long_effect`` was the first case: it used to infer the
    effect from ``capability.feature_id``, which only worked because every
    ending capability happened to share the effect's id -- a harmless-dismiss
    capability named ``quivering_palm_dismiss`` would have asked to end an
    effect that does not exist.
    """

    kind: str
    count: int = 1
    effect_id: str = ""


ACTIVE_LONG_EFFECT_REQUIREMENT = "active_long_effect:"


@dataclass(frozen=True, slots=True)
class FeatureCapability:
    """A combat capability a class feature grants.

    ``target_kind`` is the closed vocabulary of legal target sets, and it is
    *not* the same question as ``requires_hostile_target``: a Divine Spark may
    target any living creature except the caster, a Lay On Hands may also
    target the caster, and Turn Undead only accepts Undead.  Spelling the set
    out here is what lets validation and the availability projection agree
    without re-reading the effect kind.

    ``pool_resource_id`` names a resource whose *remaining amount* gates
    availability but whose spend is decided per intent (Lay On Hands).  A
    fixed ``ResourceCost`` cannot express "up to what is left", so the pool is
    only checked here and charged at resolution.

    ``requires`` lists prerequisites independent of costs and action kinds.
    """

    id: str
    feature_id: str
    cost: CapabilityCost
    requires_hostile_target: bool
    actions: tuple[CapabilityAction, ...]
    requires_condition: str | None = None
    forbidden_condition: str | None = None
    once_per_turn: bool = False
    target_kind: str = "none"
    pool_resource_id: str = ""
    # 特性自身的使用次数（每长休）。资源表达不了"特性还剩几次"：资源是
    # 角色卡上的条，特性使用是一次性开关，两者在投影上不该混在一起。
    uses_per_long_rest: int = 0
    requires: tuple[str, ...] = ()
    # 必须**为空**才算成立的那个资源池：Innate Sorcery 的 2 点术法点价格
    # 只在次数用完之后才存在。``pool_resource_id`` 要求"还有余额"，这个刚好
    # 相反，两者都是成本模型说不出来的前置。
    exhausted_resource_id: str = ""
    # 第二条价格挂在另一条特性上：Innate Sorcery 属于 1 级特性，而"用术法点
    # 换一次"从 7 级的 Sorcery Incarnate 才开始。声明出来，引擎就不必记住
    # "哪条能力在几级多出一种付法"。
    requires_feature_id: str = ""


@dataclass(frozen=True, slots=True)
class FeatureChoice:
    """A player choice one class feature requires at the level it is gained.

    Identity is ``(owner, feature_id, choice.id)``: the same choice id may
    legitimately hang off two features, so neither part alone is a key.

    ``enabled_options`` is an executability whitelist, not a UI filter.
    Every other option in the category is still projected, but marked
    unavailable with ``effect_not_implemented`` and rejected on commit --
    a selection that grants nothing must never look finished.

    ``requires_proficiency`` / ``exclude_expertise`` are only meaningful for
    ``skill_proficiency``: Expertise and Scholar both pick from skills the
    character is already proficient in, and neither may re-pick a skill the
    character already has Expertise in (SRD: "You can't have Expertise in the
    same skill proficiency more than once").  An empty ``enabled_options`` means
    "every skill in the catalog"; Scholar names its six explicitly.
    """

    id: str
    kind: str
    category: str
    count: int
    enabled_options: tuple[str, ...]
    grants: str = ""
    requires_proficiency: bool = True
    exclude_expertise: bool = True


def _parse_check_modifiers(feature_id: str, raw: Any) -> tuple[dict[str, Any], ...]:
    """Parse the ``check_modifiers`` array, failing closed on every shape.

    修正声明放在特性顶层而不是 ``parameters``：后者只接受标量，嵌套结构放
    不进去，而放宽那条约束会让所有参数失去形状保证。
    """

    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise ClassFeatureError(f"{feature_id}.check_modifiers must be a non-empty array")
    parsed: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        label = f"{feature_id}.check_modifiers[{index}]"
        if not isinstance(entry, dict):
            raise ClassFeatureError(f"{label} must be an object")
        unknown = sorted(set(entry) - _CHECK_MODIFIER_KEYS)
        if unknown:
            raise ClassFeatureError(f"{label} has an unknown field: {unknown[0]}")
        surface = str(entry.get("surface") or "")
        if surface not in CHECK_MODIFIER_SURFACES:
            raise ClassFeatureError(
                f"{label}.surface must be one of {sorted(CHECK_MODIFIER_SURFACES)}"
            )
        skills = entry.get("skills") or []
        abilities = entry.get("abilities") or []
        if not isinstance(skills, list) or not all(isinstance(r, str) for r in skills):
            raise ClassFeatureError(f"{label}.skills must be an array of refs")
        if not isinstance(abilities, list) or not all(isinstance(a, str) for a in abilities):
            raise ClassFeatureError(f"{label}.abilities must be an array of abilities")
        if not skills and not abilities:
            # 没有作用域的修正会加到每一次检定上，规则里没有这样的特性。
            raise ClassFeatureError(f"{label} must scope itself to skills or abilities")
        for ref in skills:
            _require_id(str(ref).removeprefix("skill:"), f"{label}.skills")
            if not str(ref).startswith("skill:"):
                raise ClassFeatureError(f"{label}.skills must hold skill: refs")
        for ability in abilities:
            if ability not in ABILITY_IDS:
                raise ClassFeatureError(f"{label} names an unknown ability: {ability}")
        when = entry.get("when_condition")
        if when is not None and (not isinstance(when, str) or not when):
            raise ClassFeatureError(f"{label}.when_condition must be a condition id")
        replace = entry.get("replace_ability")
        if replace is not None and replace not in ABILITY_IDS:
            raise ClassFeatureError(f"{label}.replace_ability is not an ability")
        add = entry.get("add")
        if add is None and replace is None:
            raise ClassFeatureError(f"{label} must declare replace_ability or add")
        if add is not None:
            add = _parse_check_add(add, f"{label}.add")
        parsed.append({
            "surface": surface,
            "skills": list(skills),
            "abilities": list(abilities),
            "when_condition": when,
            "replace_ability": replace,
            "add": add,
        })
    return tuple(parsed)


def _parse_check_add(add: Any, label: str) -> dict[str, Any]:
    if not isinstance(add, dict):
        raise ClassFeatureError(f"{label} must be an object")
    unknown = sorted(set(add) - _CHECK_ADD_KEYS)
    if unknown:
        raise ClassFeatureError(f"{label} has an unknown field: {unknown[0]}")
    source = str(add.get("source") or "")
    if source not in _CHECK_ADD_SOURCES:
        raise ClassFeatureError(f"{label}.source must be one of {sorted(_CHECK_ADD_SOURCES)}")
    for key in ("minimum", "maximum"):
        value = add.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ClassFeatureError(f"{label}.{key} must be an integer")
    low, high = add.get("minimum"), add.get("maximum")
    if isinstance(low, int) and isinstance(high, int) and low > high:
        raise ClassFeatureError(f"{label}.minimum must not exceed maximum")
    result: dict[str, Any] = {"source": source}
    if low is not None:
        result["minimum"] = low
    if high is not None:
        result["maximum"] = high
    if source == "constant":
        value = add.get("value")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ClassFeatureError(f"{label}.value must be an integer")
        result["value"] = value
    elif source == "ability_modifier":
        ability = add.get("ability")
        if ability not in ABILITY_IDS:
            raise ClassFeatureError(f"{label}.ability is not an ability")
        result["ability"] = ability
    else:
        multiplier = add.get("multiplier", 1)
        if multiplier not in (1, 2):
            raise ClassFeatureError(f"{label}.multiplier must be 1 or 2")
        result["multiplier"] = multiplier
    return result


def _parse_feature_choice(
    feature_id: str,
    raw: Any,
    *,
    allow_skill_or_tool: bool = False,
    allow_proficiency_grant: bool = False,
) -> FeatureChoice | None:
    """Validate a declared player choice, failing closed on unknown shapes.

    ``allow_skill_or_tool`` / ``allow_proficiency_grant`` are opt-in because the
    two widenings belong to the *feat* side of the catalog: a subclass skill
    slot always picks from skills and always grants Expertise.  Leaving them on
    for every caller would ship two values nothing on that side can consume --
    exactly the "declared but unread" shape this slice exists to remove.
    """

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ClassFeatureError(f"{feature_id}.choice must be an object")
    choice_id = _require_id(raw.get("id"), f"{feature_id}.choice.id")
    kind = str(raw.get("kind") or "")
    if kind not in FEATURE_CHOICE_KINDS:
        raise ClassFeatureError(
            f"{feature_id}.choice.kind is not a supported choice kind: {kind!r}"
        )
    category = _require_id(raw.get("category"), f"{feature_id}.choice.category")
    count = raw.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ClassFeatureError(f"{feature_id}.choice.count must be a positive integer")
    if kind == "advancement_feat" and count != 1:
        raise ClassFeatureError(f"{feature_id}.choice.count must be 1")
    if kind == "feature_option" and count != 1:
        # 互斥选项（Divine Strike / Potent Spellcasting）只能选一个：选两个
        # 会让同一个 hit 同时拿到 1d8 与 Wis 加值，而它们在规则上是替代关系。
        raise ClassFeatureError(
            f"{feature_id}.choice.count must be 1 for a feature option choice"
        )
    raw_refs = raw.get("enabled_options")
    if raw_refs is None and kind in {"skill_proficiency", "language"}:
        # 空清单 = 该目录的可选全集：技能按"已有熟练"再筛，语言按 rarity 再筛。
        raw_refs = []
    if not isinstance(raw_refs, list):
        raise ClassFeatureError(
            f"{feature_id}.choice.enabled_options must be an array"
        )
    if not raw_refs and kind in {"advancement_feat", "feature_option"}:
        raise ClassFeatureError(
            f"{feature_id}.choice.enabled_options must be a non-empty array"
        )
    # 语言选择可以留空：空清单 = 建卡可选的 Standard 语言全集，与建卡器
    # 用的是同一批（罕见的留给 DM，秘密的只能由特性固定授予）。
    prefix = {
        "advancement_feat": "feat:",
        "feature_option": "feature:",
        "language": "language:",
    }.get(kind, "skill:")
    # ``skill_or_tool`` 的候选是"技能或工具"的并集，一个前缀表达不了：Skilled
    # 的三个名额可以在两类里任意分配。子职侧只用 ``skill``，因此这条分支不
    # 改变既有形状。
    prefixes: tuple[str, ...] = (prefix,)
    if allow_skill_or_tool and kind == "skill_proficiency" and category == "skill_or_tool":
        prefixes = ("skill:", "tool:")
    refs: list[str] = []
    seen: set[str] = set()
    for raw_ref in raw_refs:
        matched = next(
            (
                candidate for candidate in prefixes
                if isinstance(raw_ref, str) and raw_ref.startswith(candidate)
            ),
            "",
        )
        if not matched:
            raise ClassFeatureError(
                f"{feature_id}.choice.enabled_options must hold "
                f"{' or '.join(prefixes)} refs"
            )
        ref = f"{matched}{_require_id(raw_ref[len(matched):], f'{feature_id}.choice.enabled_options')}"
        if ref in seen:
            raise ClassFeatureError(
                f"{feature_id}.choice.enabled_options repeats {ref}"
            )
        seen.add(ref)
        refs.append(ref)
    flags: dict[str, bool] = {}
    for key in ("requires_proficiency", "exclude_expertise"):
        value = raw.get(key, True)
        if not isinstance(value, bool):
            raise ClassFeatureError(f"{feature_id}.choice.{key} must be a boolean")
        flags[key] = value
    if kind in {"advancement_feat", "feature_option", "language"}:
        # 成长专长、特性选项与语言都不使用技能选择的两个约束；写在它们上面
        # 会被静默忽略，因此宁可加载期拒绝。
        for key in ("requires_proficiency", "exclude_expertise"):
            if key in raw and raw[key] is not True:
                raise ClassFeatureError(
                    f"{feature_id}.choice.{key} is only meaningful for a "
                    "skill_proficiency choice"
                )
    skill_categories = (
        {"skill", "skill_or_tool"} if allow_skill_or_tool else {"skill"}
    )
    if kind == "skill_proficiency" and category not in skill_categories:
        raise ClassFeatureError(
            f"{feature_id}.choice.category must be one of "
            f"{sorted(skill_categories)} for a skill choice"
        )
    if kind == "feature_option" and category != "feature":
        raise ClassFeatureError(
            f"{feature_id}.choice.category must be 'feature' for a feature option choice"
        )
    if kind == "language" and category != "language":
        raise ClassFeatureError(
            f"{feature_id}.choice.category must be 'language' for a language choice"
        )
    grants = str(raw.get("grants") or "")
    if kind == "skill_proficiency":
        # 选中的技能"得到什么"必须显式声明：Expertise 与"获得熟练"是两种不同
        # 效果，靠 choice id 猜等于让命名承担语义。
        allowed_grants = (
            SKILL_CHOICE_GRANTS if allow_proficiency_grant else frozenset({"expertise"})
        )
        if grants not in allowed_grants:
            raise ClassFeatureError(
                f"{feature_id}.choice.grants must be one of {sorted(allowed_grants)}"
            )
    elif grants:
        raise ClassFeatureError(
            f"{feature_id}.choice.grants is only meaningful for a "
            "skill_proficiency choice"
        )
    return FeatureChoice(
        id=choice_id,
        kind=kind,
        category=category,
        count=count,
        enabled_options=tuple(refs),
        grants=grants,
        requires_proficiency=flags["requires_proficiency"],
        exclude_expertise=flags["exclude_expertise"],
    )


def parse_feature_choice(
    feature_id: str,
    raw: Any,
    *,
    allow_skill_or_tool: bool = False,
    allow_proficiency_grant: bool = False,
) -> FeatureChoice | None:
    """Validate one declared choice; ``None`` means the declaration has none.

    Public so feat declarations can reuse the exact same shape instead of
    growing a second, weaker parser of their own.  The two flags are what the
    feat side turns on; a subclass caller leaves them off.
    """

    return _parse_feature_choice(
        feature_id,
        raw,
        allow_skill_or_tool=allow_skill_or_tool,
        allow_proficiency_grant=allow_proficiency_grant,
    )


def _advancement_feat_categories(bundle: LoadedRulesetBundle) -> dict[str, str]:
    """Every declared advancement feat's id and category, for cross-catalog checks."""

    catalog = bundle.get("advancement_feat_catalog", "srd_advancement_feats")
    feats = catalog.get("feats") if isinstance(catalog, Mapping) else None
    if not isinstance(feats, Mapping):
        return {}
    return {
        str(feat_id): str(feat.get("category") or "")
        for feat_id, feat in feats.items()
        if isinstance(feat, Mapping)
    }


def _validate_feature_choices(
    features: list[ClassFeatureDefinition], bundle: LoadedRulesetBundle,
) -> None:
    """Cross-check every declared choice against the catalog it picks from.

    A choice naming a category nothing declares, or whitelisting a ref that is
    missing or belongs to another category, would offer a slot the runtime can
    never satisfy.  All three fail at load instead of on the player's turn.

    A ``skill_proficiency`` choice is checked against the *skill* catalog: a
    whitelisted skill that the bundle does not define could never be granted
    Expertise, and the count must not exceed the number of skills that exist.
    """

    categories = _advancement_feat_categories(bundle)
    known = set(categories.values())
    skill_ids = {
        str(entity.get("id")) for entity in bundle.list("skill")
        if isinstance(entity, Mapping)
    }
    language_ids = {
        str(entity.get("id")) for entity in bundle.list("language")
        if isinstance(entity, Mapping)
    }
    tool_ids = {
        str(entity.get("id")) for entity in bundle.list("tool")
        if isinstance(entity, Mapping)
    }
    for definition in features:
        choice = definition.choice
        if choice is None:
            continue
        if choice.kind == "skill_proficiency":
            # ``skill_or_tool`` 从两个目录的并集里选，所以池子是一张表而不是
            # 一个集合；``skill`` 退化成只有一项，行为与之前完全一致。
            pools = (
                {"skill": skill_ids, "tool": tool_ids}
                if choice.category == "skill_or_tool"
                else {"skill": skill_ids}
            )
            for ref in choice.enabled_options:
                prefix, _, local_id = ref.partition(":")
                if prefix not in pools or local_id not in pools[prefix]:
                    raise ClassFeatureError(
                        f"{definition.id}.choice references an undeclared {prefix}: {ref}"
                    )
            pool = len(choice.enabled_options) or sum(len(ids) for ids in pools.values())
            if choice.count > pool:
                raise ClassFeatureError(
                    f"{definition.id}.choice.count exceeds the {choice.category} pool"
                )
            continue
        if choice.kind == "language":
            for ref in choice.enabled_options:
                if ref[len("language:"):] not in language_ids:
                    raise ClassFeatureError(
                        f"{definition.id}.choice references an undeclared language: {ref}"
                    )
            pool = (
                len(choice.enabled_options) if choice.enabled_options
                else len(language_ids)
            )
            if choice.count > pool:
                raise ClassFeatureError(
                    f"{definition.id}.choice.count exceeds the {choice.category} pool"
                )
            continue
        if choice.kind == "feature_option":
            # 候选是同一份特性目录里的声明，不是成长专长：校验它长得像特性
            # （有声明、同 owner、且自己不是选项父），而不是去专长目录里找。
            for ref in choice.enabled_options:
                local_id = ref[len("feature:"):]
                if not any(
                    definition.owner == other.owner and other.id == local_id
                    for other in features
                ):
                    raise ClassFeatureError(
                        f"{definition.id}.choice references an undeclared feature: {ref}"
                    )
            continue
        if choice.category not in known:
            raise ClassFeatureError(
                f"{definition.id}.choice.category is not a declared feat category: "
                f"{choice.category}"
            )
        for ref in choice.enabled_options:
            category = categories.get(ref[5:])
            if category is None:
                raise ClassFeatureError(
                    f"{definition.id}.choice references an undeclared advancement feat: {ref}"
                )
            if category != choice.category:
                raise ClassFeatureError(
                    f"{definition.id}.choice option {ref} is not a {choice.category} feat"
                )


@dataclass(frozen=True, slots=True)
class ClassFeatureDefinition:
    """One declared class feature."""

    id: str
    class_id: str
    minimum_level: int
    source_ref: str
    summary: str
    parameters: Mapping[str, Any]
    resource_id: str
    option_of: str
    capabilities: tuple[FeatureCapability, ...]
    owner: str
    equipment_requirement: EquipmentRequirement | None = None
    choice: FeatureChoice | None = None
    #: 检定修正声明，与 `choice` 并列（`parameters` 只收标量）。
    check_modifiers: tuple[dict[str, Any], ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        """The feature's owner-scoped identity: ``(owner, local_id)``."""

        return (self.owner, self.id)


def _parse_equipment_requirement(
    feature_id: str, raw: Any,
) -> EquipmentRequirement | None:
    """Validate a declared equipment precondition, failing closed on unknowns."""

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ClassFeatureError(f"{feature_id}.equipment_requirement must be an object")
    unknown = {str(key) for key in raw}.difference(EQUIPMENT_REQUIREMENT_KEYS)
    if unknown:
        raise ClassFeatureError(
            f"{feature_id}.equipment_requirement has an unknown field: {sorted(unknown)[0]}"
        )
    flags: dict[str, bool] = {}
    for key in ("unarmored", "no_shield", "no_heavy_armor"):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise ClassFeatureError(f"{feature_id}.equipment_requirement.{key} must be a boolean")
        flags[key] = value
    raw_kinds = raw.get("weapon_kinds")
    if raw_kinds is None:
        raw_kinds = []
    if not isinstance(raw_kinds, list):
        raise ClassFeatureError(
            f"{feature_id}.equipment_requirement.weapon_kinds must be an array"
        )
    kinds: list[str] = []
    for raw_kind in raw_kinds:
        kind = str(raw_kind or "")
        if kind not in WEAPON_KINDS:
            raise ClassFeatureError(
                f"{feature_id}.equipment_requirement has an unknown weapon kind: {raw_kind!r}"
            )
        if kind not in kinds:
            kinds.append(kind)
    return EquipmentRequirement(
        unarmored=flags["unarmored"],
        no_shield=flags["no_shield"],
        no_heavy_armor=flags["no_heavy_armor"],
        weapon_kinds=tuple(kinds),
    )


class FeatureCatalogIndex(Mapping[Any, ClassFeatureDefinition]):
    """Owner-scoped feature index with a narrow legacy lookup path.

    The authoritative key is ``(owner, local_id)`` so two classes may
    declare the same local id without overwriting each other.  A bare local id
    remains readable while it identifies exactly one declaration; that keeps
    existing presentation code and tests working without making short ids the
    catalog's unique key.  Ambiguous short ids must be resolved through
    :attr:`by_key` or :meth:`definitions_for`.
    """

    __slots__ = ("_by_key", "_by_local")

    def __init__(self, definitions: tuple[ClassFeatureDefinition, ...]) -> None:
        by_key: dict[tuple[str, str], ClassFeatureDefinition] = {}
        by_local: dict[str, list[tuple[str, str]]] = {}
        for definition in definitions:
            if definition.key in by_key:
                raise ClassFeatureError(
                    f"duplicate class feature identity: {definition.key!r}"
                )
            by_key[definition.key] = definition
            by_local.setdefault(definition.id, []).append(definition.key)
        self._by_key = by_key
        self._by_local = {key: tuple(keys) for key, keys in by_local.items()}

    @property
    def by_key(self) -> Mapping[tuple[str, str], ClassFeatureDefinition]:
        return MappingProxyType(self._by_key)

    def definitions_for(self, local_id: str) -> tuple[ClassFeatureDefinition, ...]:
        """Every declaration with one local id, across all owners."""

        return tuple(
            self._by_key[key] for key in self._by_local.get(str(local_id), ())
        )

    def __getitem__(self, key: Any) -> ClassFeatureDefinition:
        if isinstance(key, tuple) and len(key) == 2:
            return self._by_key[(str(key[0]), str(key[1]))]
        if isinstance(key, str):
            keys = self._by_local.get(key, ())
            if len(keys) == 1:
                return self._by_key[keys[0]]
            if len(keys) > 1:
                raise KeyError(
                    f"class feature id {key!r} is ambiguous; use its owner-scoped key"
                )
        raise KeyError(key)

    def __iter__(self):
        for key, definition in self._by_key.items():
            if len(self._by_local[definition.id]) == 1:
                yield definition.id
            else:
                yield key

    def __len__(self) -> int:
        return len(self._by_key)


@dataclass(frozen=True, slots=True)
class Dnd2024ClassFeatureCatalog:
    """Read-only, validated access to the bundled class feature declarations."""

    source_ref: str
    labels: Mapping[str, str]
    features: Mapping[Any, ClassFeatureDefinition]
    by_class: Mapping[str, tuple[tuple[str, str], ...]]

    @property
    def features_by_key(self) -> Mapping[tuple[str, str], ClassFeatureDefinition]:
        """The authoritative owner-scoped index."""

        if isinstance(self.features, FeatureCatalogIndex):
            return self.features.by_key
        by_key: dict[tuple[str, str], ClassFeatureDefinition] = {}
        for definition in self.features.values():
            if not isinstance(definition, ClassFeatureDefinition):
                continue
            by_key[definition.key] = definition
        return by_key

    @classmethod
    def empty(cls) -> Dnd2024ClassFeatureCatalog:
        """An empty catalog, for bundles that ship no feature declarations."""

        return cls(source_ref="", labels={}, features=FeatureCatalogIndex(()), by_class={})

    @classmethod
    def from_bundle(cls, bundle: LoadedRulesetBundle) -> Dnd2024ClassFeatureCatalog:
        raw = bundle.get("class_feature_catalog", "srd_class_features")
        if raw is None:
            raise ClassFeatureError("D&D 2024 class feature catalog is missing")
        raw_labels = raw.get("labels")
        if not isinstance(raw_labels, dict):
            raise ClassFeatureError("class feature labels must be an object")
        labels = {
            _require_id(key, "class feature label key"): _require_text(
                value, f"class feature label {key}",
            )
            for key, value in raw_labels.items()
        }
        raw_classes = raw.get("classes")
        if not isinstance(raw_classes, dict):
            raise ClassFeatureError("class feature classes must be an object")
        features: list[ClassFeatureDefinition] = []
        by_key: dict[tuple[str, str], ClassFeatureDefinition] = {}
        by_class: dict[str, tuple[tuple[str, str], ...]] = {}
        for raw_class_id, raw_class in raw_classes.items():
            class_id = _require_id(raw_class_id, "class feature class id")
            if not isinstance(raw_class, dict):
                raise ClassFeatureError(f"classes.{class_id} must be an object")
            source_ref = _require_text(raw_class.get("source_ref"), f"classes.{class_id}.source_ref")
            raw_features = raw_class.get("features")
            if not isinstance(raw_features, list) or not raw_features:
                raise ClassFeatureError(f"classes.{class_id}.features must be a non-empty array")
            ordered: list[tuple[str, str]] = []
            for raw_feature in raw_features:
                definition = cls._parse_feature(class_id, source_ref, raw_feature)
                if definition.key in by_key:
                    raise ClassFeatureError(
                        f"duplicate class feature identity: {definition.key!r}"
                    )
                features.append(definition)
                by_key[definition.key] = definition
                ordered.append(definition.key)
            by_class[class_id] = tuple(ordered)
        for definition in features:
            if not definition.option_of:
                continue
            parent_key = (definition.owner, definition.option_of)
            if parent_key not in by_key:
                raise ClassFeatureError(
                    f"{definition.id}.option_of references an undeclared feature "
                    "with the same owner"
                )
            if definition.option_of == definition.id:
                raise ClassFeatureError(f"{definition.id} cannot be its own option")
        _validate_feature_choices(features, bundle)
        return cls(
            source_ref=_require_text(raw.get("source_ref"), "class feature catalog source_ref"),
            labels=labels,
            features=FeatureCatalogIndex(tuple(features)),
            by_class=by_class,
        )

    @classmethod
    def _parse_feature(
        cls, class_id: str, class_source_ref: str, raw: Any,
    ) -> ClassFeatureDefinition:
        if not isinstance(raw, dict):
            raise ClassFeatureError(f"classes.{class_id} contains an invalid feature")
        feature_id = _require_id(raw.get("id"), f"classes.{class_id} feature id")
        owner = _require_owner(
            raw.get("owner"), f"{feature_id}.owner", class_id,
        )
        minimum_level = _require_positive_int(
            raw.get("minimum_level", 1), f"{feature_id}.minimum_level",
        )
        if minimum_level > 20:
            raise ClassFeatureError(f"{feature_id}.minimum_level must not exceed 20")
        raw_parameters = raw.get("parameters") or {}
        if not isinstance(raw_parameters, dict):
            raise ClassFeatureError(f"{feature_id}.parameters must be an object")
        parameters = {
            str(key): _require_scalar(value, f"{feature_id}.parameters.{key}")
            for key, value in raw_parameters.items()
        }
        threshold = parameters.get("crit_threshold")
        if "crit_threshold" in parameters and (
            isinstance(threshold, bool) or not isinstance(threshold, int)
            or not 2 <= threshold <= 19
        ):
            raise ClassFeatureError(
                f"{feature_id}.crit_threshold must be an integer from 2 to 19"
            )
        range_ft = parameters.get("range_ft")
        if "range_ft" in parameters:
            if (
                isinstance(range_ft, bool) or not isinstance(range_ft, int)
                or range_ft <= 0
            ):
                raise ClassFeatureError(
                    f"{feature_id}.range_ft must be a positive integer"
                )
            if parameters.get("trigger") not in CHECK_WINDOW_TRIGGERS:
                raise ClassFeatureError(
                    f"{feature_id}.trigger must be one of "
                    f"{sorted(CHECK_WINDOW_TRIGGERS)} when range_ft is declared"
                )
        target_range_ft = parameters.get("target_range_ft")
        if "target_range_ft" in parameters and (
            isinstance(target_range_ft, bool) or not isinstance(target_range_ft, int)
            or target_range_ft <= 0
        ):
            # ``target_range_ft`` 是"这次能力能打多远"，与上面的掷后窗口
            # 互不相干：窗口的 ``range_ft`` 描述的是"事件发生时才判定射程"，
            # 两者写成同一个键会让一个纯粹的距离声明被迫带一个 trigger。
            raise ClassFeatureError(
                f"{feature_id}.target_range_ft must be a positive integer"
            )
        resource_id = str(raw.get("resource_id") or "")
        if resource_id:
            resource_id = _require_id(resource_id, f"{feature_id}.resource_id")
        option_of = str(raw.get("option_of") or "")
        if option_of:
            option_of = _require_id(option_of, f"{feature_id}.option_of")
        raw_capabilities = raw.get("capabilities") or []
        if not isinstance(raw_capabilities, list):
            raise ClassFeatureError(f"{feature_id}.capabilities must be an array")
        capabilities: list[FeatureCapability] = []
        seen_capability_ids: set[str] = set()
        for raw_capability in raw_capabilities:
            capability = cls._parse_capability(feature_id, raw_capability)
            if capability.id in seen_capability_ids:
                raise ClassFeatureError(f"{feature_id} declares a duplicate capability")
            seen_capability_ids.add(capability.id)
            capabilities.append(capability)
        return ClassFeatureDefinition(
            id=feature_id,
            class_id=class_id,
            minimum_level=minimum_level,
            source_ref=str(raw.get("source_ref") or class_source_ref),
            summary=str(raw.get("summary") or ""),
            parameters=parameters,
            resource_id=resource_id,
            option_of=option_of,
            capabilities=tuple(capabilities),
            owner=owner,
            equipment_requirement=_parse_equipment_requirement(
                feature_id, raw.get("equipment_requirement"),
            ),
            choice=_parse_feature_choice(feature_id, raw.get("choice"), allow_proficiency_grant=True),
            check_modifiers=_parse_check_modifiers(
                feature_id, raw.get("check_modifiers"),
            ),
        )

    @classmethod
    def _parse_capability(cls, feature_id: str, raw: Any) -> FeatureCapability:
        # Handlers depend on declaration types, so look up the registry only at load time.
        from .kinds import KIND_HANDLERS

        if not isinstance(raw, dict):
            raise ClassFeatureError(f"{feature_id} contains an invalid capability")
        capability_id = _require_id(raw.get("id"), f"{feature_id} capability id")
        raw_cost = raw.get("cost") or {}
        if not isinstance(raw_cost, dict):
            raise ClassFeatureError(f"{capability_id}.cost must be an object")
        keyed_slot = ""
        for slot_kind in ACTION_COST_KINDS:
            if slot_kind not in raw_cost:
                continue
            if keyed_slot:
                raise ClassFeatureError(f"{capability_id} declares more than one action cost")
            if _require_positive_int(raw_cost[slot_kind], f"{capability_id}.{slot_kind}") != 1:
                raise ClassFeatureError(f"{capability_id}.{slot_kind} must be 1")
            keyed_slot = slot_kind
        slot = keyed_slot
        if SLOT_COST_KEY in raw_cost:
            slot = str(raw_cost[SLOT_COST_KEY] or "")
            if slot not in ACTION_COST_KINDS:
                raise ClassFeatureError(
                    f"{capability_id}.{SLOT_COST_KEY} is not a known action slot: {slot!r}"
                )
            if keyed_slot:
                raise ClassFeatureError(
                    f"{capability_id} declares both {SLOT_COST_KEY} and an action cost"
                )
        raw_resources = raw_cost.get("resources") or []
        if not isinstance(raw_resources, list):
            raise ClassFeatureError(f"{capability_id}.cost.resources must be an array")
        resources: list[ResourceCost | SpellSlotCost] = []
        spell_levels: set[int] = set()
        for raw_resource in raw_resources:
            if not isinstance(raw_resource, dict):
                raise ClassFeatureError(f"{capability_id} contains an invalid resource cost")
            if "spell_slot" in raw_resource:
                if raw_resource.keys() != {"spell_slot"}:
                    raise ClassFeatureError(
                        f"{capability_id} spell slot cost must contain only spell_slot"
                    )
                level = _require_positive_int(
                    raw_resource["spell_slot"], f"{capability_id}.cost.resources.spell_slot",
                )
                if level > 9:
                    raise ClassFeatureError(f"{capability_id} spell slot level must be at most 9")
                if level in spell_levels:
                    raise ClassFeatureError(f"{capability_id} repeats a spell slot cost level")
                spell_levels.add(level)
                resources.append(SpellSlotCost(level))
                continue
            resources.append(ResourceCost(
                resource_id=_require_id(
                    raw_resource.get("id"), f"{capability_id} resource cost id",
                ),
                amount=_require_positive_int(
                    raw_resource.get("amount"), f"{capability_id} resource cost amount",
                ),
            ))
        timing = ""
        if TURN_START_COST_KEY in raw_cost:
            if raw_cost[TURN_START_COST_KEY] is not True:
                raise ClassFeatureError(
                    f"{capability_id}.{TURN_START_COST_KEY} must be true"
                )
            if TIMING_COST_KEY in raw_cost:
                raise ClassFeatureError(
                    f"{capability_id} declares both {TURN_START_COST_KEY} and {TIMING_COST_KEY}"
                )
            timing = TURN_START_TIMING
        if TIMING_COST_KEY in raw_cost:
            timing = str(raw_cost[TIMING_COST_KEY] or "")
            if timing not in COST_TIMINGS:
                raise ClassFeatureError(
                    f"{capability_id}.{TIMING_COST_KEY} is not a known timing: {timing!r}"
                )
        if timing:
            if slot:
                raise ClassFeatureError(
                    f"{capability_id} cannot cost an action and a turn-start window at once"
                )
            if not resources:
                # 既不要动作也不要资源，等于一个任意时刻可用的免费能力：
                # "只能在回合开始支付" 就失去意义，必须当成声明错误。
                raise ClassFeatureError(
                    f"{capability_id} turn-start capability must cost a resource"
                )
        raw_actions = raw.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise ClassFeatureError(f"{capability_id}.actions must be a non-empty array")
        actions: list[CapabilityAction] = []
        for raw_action in raw_actions:
            if not isinstance(raw_action, dict):
                raise ClassFeatureError(f"{capability_id} contains an invalid action")
            kind = str(raw_action.get("kind") or "")
            handler = KIND_HANDLERS.get(kind)
            if handler is None:
                raise ClassFeatureError(f"{capability_id} declares an unknown action kind: {kind!r}")
            if handler.requires_resource_cost and not resources:
                raise ClassFeatureError(f"{capability_id} must declare its price in cost.resources")
            effect_id = str(raw_action.get("effect_id") or "")
            if effect_id:
                _require_id(effect_id, f"{capability_id} action effect_id")
            actions.append(CapabilityAction(
                kind=kind,
                count=_require_positive_int(
                    raw_action.get("count", 1), f"{capability_id} action count",
                ),
                effect_id=effect_id,
            ))
        raw_requires = raw.get("requires", [])
        if not isinstance(raw_requires, list):
            raise ClassFeatureError(f"{capability_id}.requires must be an array")
        requires: list[str] = []
        for prerequisite in raw_requires:
            if not isinstance(prerequisite, str) or not prerequisite.startswith(
                ACTIVE_LONG_EFFECT_REQUIREMENT,
            ):
                raise ClassFeatureError(
                    f"{capability_id}.requires has an unknown prerequisite: {prerequisite!r}"
                )
            _require_id(
                prerequisite.removeprefix(ACTIVE_LONG_EFFECT_REQUIREMENT),
                f"{capability_id}.requires effect id",
            )
            if prerequisite in requires:
                raise ClassFeatureError(f"{capability_id}.requires repeats {prerequisite!r}")
            requires.append(prerequisite)
        requires_condition = str(raw.get("requires_condition") or "")
        forbidden_condition = str(raw.get("forbidden_condition") or "")
        if requires_condition and forbidden_condition:
            raise ClassFeatureError(
                f"{capability_id} cannot require and forbid a condition at the same time"
            )
        for condition_field, condition_value in (
            ("requires_condition", requires_condition),
            ("forbidden_condition", forbidden_condition),
        ):
            if condition_value:
                _require_id(condition_value, f"{capability_id}.{condition_field}")
        once_per_turn = raw.get("once_per_turn", False)
        if not isinstance(once_per_turn, bool):
            raise ClassFeatureError(f"{capability_id}.once_per_turn must be a boolean")
        target_kind = str(raw.get("target_kind") or "none")
        if target_kind not in TARGET_KINDS:
            raise ClassFeatureError(
                f"{capability_id}.target_kind is not a known target set: {target_kind!r}"
            )
        pool_resource_id = str(raw.get("pool_resource_id") or "")
        if pool_resource_id and not _ID_RE.fullmatch(pool_resource_id):
            raise ClassFeatureError(
                f"{capability_id}.pool_resource_id is not a canonical id"
            )
        uses_per_long_rest = raw.get("uses_per_long_rest", 0)
        if (
            isinstance(uses_per_long_rest, bool)
            or not isinstance(uses_per_long_rest, int)
            or uses_per_long_rest < 0
        ):
            raise ClassFeatureError(
                f"{capability_id}.uses_per_long_rest must be a non-negative integer"
            )
        exhausted_resource_id = str(raw.get("exhausted_resource_id") or "")
        if exhausted_resource_id:
            _require_id(exhausted_resource_id, f"{capability_id}.exhausted_resource_id")
            if exhausted_resource_id in {
                cost.resource_id for cost in resources if isinstance(cost, ResourceCost)
            }:
                # 既"要先花掉一单位"又"必须是零"：没有角色能同时满足。
                raise ClassFeatureError(
                    f"{capability_id} cannot both spend and require-empty "
                    f"{exhausted_resource_id}"
                )
        requires_feature_id = str(raw.get("requires_feature_id") or "")
        if requires_feature_id:
            _require_id(requires_feature_id, f"{capability_id}.requires_feature_id")
        return FeatureCapability(
            id=capability_id,
            feature_id=feature_id,
            cost=CapabilityCost(
                slot=slot, resources=tuple(resources), timing=timing,
            ),
            requires_hostile_target=bool(raw.get("requires_hostile_target")),
            actions=tuple(actions),
            requires_condition=requires_condition or None,
            forbidden_condition=forbidden_condition or None,
            once_per_turn=once_per_turn,
            target_kind=target_kind,
            pool_resource_id=pool_resource_id,
            uses_per_long_rest=uses_per_long_rest,
            requires=tuple(requires),
            exhausted_resource_id=exhausted_resource_id,
            requires_feature_id=requires_feature_id,
        )

    def label(self, key: str, default: str = "") -> str:
        return self.labels.get(str(key), default or str(key))

    def class_features(self, class_id: str) -> tuple[ClassFeatureDefinition, ...]:
        return tuple(
            self.features_by_key[key] for key in self.by_class.get(class_id, ())
        )


@dataclass(frozen=True, slots=True)
class FeatureView:
    """Presentation-safe view of one feature the character actually owns.

    ``active`` says whether the feature's declared equipment precondition
    currently holds.  An owned-but-inactive feature keeps its identity (the
    character really does have it) but exposes no effect values: the numbers
    that are not in force right now must not be rendered as if they were.
    """

    id: str
    name: str
    summary: str
    source_ref: str
    minimum_level: int
    owner: str = ""
    values: Mapping[str, Any] = field(default_factory=dict)
    active: bool = True
    # 只有声明了 choice 的特性才带 selection；其余特性不得凭空多出字段。
    selection: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "owner": self.owner,
            "name": self.name,
            "summary": self.summary,
            "source_ref": self.source_ref,
            "minimum_level": self.minimum_level,
            "values": dict(self.values),
            "active": self.active,
        }
        if self.selection is not None:
            payload["selection"] = dict(self.selection)
        return payload


@dataclass(frozen=True, slots=True)
class CapabilityCostView:
    """One user-visible price of a capability."""

    kind: str
    name: str
    amount: int
    current: int
    maximum: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "amount": self.amount,
            "current": self.current,
            "maximum": self.maximum,
        }


@dataclass(frozen=True, slots=True)
class CombatCapabilityView:
    """Presentation-safe view of one feature-provided combat capability."""

    id: str
    feature_id: str
    name: str
    available: bool
    blocked_reason: str
    requires_hostile_target: bool
    costs: tuple[CapabilityCostView, ...]
    target_kind: str = "none"
    kind_details: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **({"kind_details": dict(self.kind_details)} if self.kind_details else {}),
            "capability_id": self.id,
            "feature_id": self.feature_id,
            "label": self.name,
            "available": self.available,
            "blocked_reason": self.blocked_reason,
            "requires_target": self.requires_hostile_target,
            "target_kind": self.target_kind,
            "costs": [cost.to_dict() for cost in self.costs],
        }


def parse_capability(feature_id: str, raw: Any) -> FeatureCapability:
    """Validate one declared capability; the grantor id comes from the caller.

    A capability always names the declaration that grants it, so the payload
    never carries ``feature_id`` -- passing it in is what keeps a feat's
    capability from accidentally claiming a class feature as its owner.
    """

    return Dnd2024ClassFeatureCatalog._parse_capability(feature_id, raw)
