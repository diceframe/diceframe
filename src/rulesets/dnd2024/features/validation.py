"""Load-time validation of class-feature declarations.

Every parameter a feature may declare is checked here against the bundle
tables that back it.  A key nothing reads, or a value the rules cannot hold,
fails the load instead of becoming a declaration that looks implemented: that
is the one guarantee this module owns.  The checks are pure functions over a
definition and the catalogs it may reference, and the class-feature resolver
runs them once each time it is built.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.primitives import ABILITY_IDS
from src.rulesets.dnd2024.progression.catalog import Dnd2024ProgressionCatalog

from .models import (
    ClassFeatureDefinition,
    ClassFeatureError,
    Dnd2024ClassFeatureCatalog,
    FeatureCapability,
)
from .resources import FEATURE_USAGE_SCOPES, Dnd2024ClassResourceCatalog


#: 命中 rider 声明允许的参数键。每个键都必须被消费，缺键则加载期拒绝：一个
#: 写得像"已实现"、实际没有任何代码读取的参数，比不写更危险。
_RIDER_PARAMETER_KEYS: dict[str, frozenset[str]] = {
    "stunning_strike": frozenset({
        "once_per_turn", "focus_cost", "save_ability",
        "on_fail_condition", "on_success_condition", "condition_duration",
        "on_success_attack_condition",
    }),
    "empowered_strikes": frozenset({"unarmed_damage_type_choices"}),
    "open_hand_technique": frozenset({
        "flurry_hit_choices", "addle_condition", "push_distance_ft",
        "push_save_ability", "topple_save_ability", "topple_condition",
    }),
    "radiant_strikes": frozenset({
        "extra_damage", "extra_damage_type", "attack_kinds",
    }),
    "divine_strike": frozenset({
        "damage_dice", "damage_types", "once_per_turn", "attack_kinds",
    }),
    "primal_strike": frozenset({
        "damage_dice", "damage_types", "once_per_turn", "attack_kinds",
    }),
    "potent_spellcasting": frozenset({"cantrip_source_class", "ability"}),
    "improved_blessed_strikes": frozenset({
        "divine_strike_dice", "temp_hp_ability_multiplier", "temp_hp_range_ft",
    }),
    "improved_elemental_fury": frozenset({
        "primal_strike_dice", "range_bonus_ft", "range_minimum_ft",
    }),
}
#: 声明"这次攻击算哪一类"的闭集。``weapon`` 是任意武器攻击（近战或远程），
#: ``melee_weapon`` 是近战武器，``beast_form`` 是荒野形态的野兽攻击。
_RIDER_ATTACK_KINDS = frozenset({"weapon", "melee_weapon", "unarmed_strike", "beast_form"})
_OPEN_HAND_CHOICES = frozenset({"addle", "push", "topple"})
_RIDER_CONDITION_DURATIONS = frozenset({"attacker_next_turn_start", "target_turn_start"})

#: 攻击前声明与持续状态声明（S0-F15）允许的参数键。与 rider 表分开：这张表
#: 描述的是"掷骰之前就确定、之后一直生效"的状态，两者的消费时点不同，混在
#: 一张表里会让"缺键即加载失败"的断言说不清缺的是哪一种。
_ATTACK_STATE_PARAMETER_KEYS: dict[str, frozenset[str]] = {
    "reckless_attack": frozenset({
        "self_condition", "condition_duration", "ability",
    }),
    "frenzy": frozenset({
        "requires_condition", "requires_declaration", "ability",
        "damage_die_sides", "dice_count_track", "damage_type_from_attack",
        "once_per_turn",
    }),
    "brutal_strike": frozenset({
        "requires_declaration", "ability", "damage_dice", "damage_dice_upgrade",
        "upgrade_level", "effect_options", "effects_per_use",
        "effects_per_use_upgrade", "once_per_turn",
        "forceful_push_distance_ft", "forceful_follow_move_divisor",
        "hamstring_condition", "hamstring_speed_reduction_ft",
        "hamstring_duration",
    }),
    "improved_brutal_strike": frozenset({
        "additional_effect_options", "staggering_save_condition",
        "staggering_reaction_condition", "staggering_duration",
        "sundering_condition", "sundering_attack_bonus", "sundering_duration",
    }),
    "studied_attacks": frozenset({"mark_kind", "mode", "expiry"}),
}
#: Brutal Strike 的四个效果是闭集：效果名同时是意图里提交的值，拼错一个
#: 就会变成"提交了但没人实现"的静默失手。
_BRUTAL_STRIKE_EFFECTS = frozenset({
    "forceful", "hamstring", "staggering", "sundering",
})

#: Hunter's Mark 链的五个声明各自只能读自己的那几个键。Favored Enemy 只用
#: 既有授权机制（`granted_spell_id` + `resource_id`），所以它的闭集就是那一个键。
_MARK_PARAMETER_KEYS: dict[str, frozenset[str]] = {
    "favored_enemy": frozenset({"granted_spell_id"}),
    "relentless_hunter": frozenset({"concentration_damage_exempt_spell_id"}),
    "precise_hunter": frozenset({"marked_target_attack_advantage"}),
    "feral_senses": frozenset({"blindsight_range_ft"}),
    "foe_slayer": frozenset({"mark_damage_die"}),
}

#: Foe Slayer 只能把骰面换成同为标准骰的一种；d3 或 d20 都不是官方骰面。
#: 原文把替换写死为"a d10 rather than a d6"，所以这里只收 6/10：收下 12 会
#: 让内容把 1d6 加成 1d12，比规则强；收下 20 则比规则弱（S0-F18 §4.2.6）。
_MARK_DAMAGE_DICE = frozenset({6, 10})
# 光环免疫只承诺引擎真的会"抑制"的条件。一个条件若连机械效果都没有声明，
# 免疫它就是一句空话：被拦截的条件和没被拦截的条件在结算上完全一样，测试
# 也分辨不出来。因此这份白名单是能力边界，不是条件全集的子集。
_AURA_IMMUNITY_CONDITIONS = frozenset({"charmed", "frightened"})
# The defeat-trigger payload is one closed four-key set.  ``trigger`` names the
# only supported event; the other three describe the temp-HP formula.  Keeping
# the names in one place means the loader and the combat helper can never
# disagree about which keys exist.
_DEFEAT_TRIGGER_KEYS = (
    "trigger", "temp_hp_ability", "temp_hp_class_ref", "temp_hp_minimum",
)
# The one trigger this slice understands.  It is exported because the combat
# settlement reads the same name: a loader that accepts a trigger the engine
# never fires would ship a silently inert feature.
ENEMY_REDUCED_TO_ZERO_HP = "enemy_reduced_to_zero_hp"
_DEFEAT_TRIGGERS = frozenset({ENEMY_REDUCED_TO_ZERO_HP})
# Parameters the previous implementation accepted.  A declaration that still
# carries them describes a rule SRD 5.2.1 does not have, so loading fails
# instead of quietly ignoring the extra keys.
_RETIRED_DEFEAT_KEYS = (
    "defeat_temp_hp_ability",
    "defeat_temp_hp_add_class_level",
    "defeat_share_range_ft",
)


def validate_declarations(
    catalog: Dnd2024ClassFeatureCatalog,
    bundle: LoadedRulesetBundle,
    progression_catalog: Dnd2024ProgressionCatalog | None,
    class_resources: Dnd2024ClassResourceCatalog,
) -> None:
    """Fail closed when a declared parameter has no backing bundle table."""

    _validate_capability_ids_are_unique(catalog, bundle)
    if progression_catalog is None:  # pragma: no cover - guarded by ``available``
        return
    # 循环外取一次。``LoadedRulesetBundle.get`` 返回的是 deepcopy，放进按特性
    # 遍历的循环里就是每个特性复制一整份战斗目录——几百次大字典拷贝，把每次
    # 装载从毫秒推到数秒，而且只表现为"整个测试套件变慢"。
    combat_conditions = (
        bundle.get("combat_catalog", "srd_combat_core") or {}
    ).get("conditions", {})
    known_feature_ids = frozenset(
        definition.id for definition in catalog.features_by_key.values()
    )
    for definition in catalog.features_by_key.values():
        spell_ref = definition.parameters.get("granted_spell_id")
        if spell_ref is not None:
            spells = (bundle.get("spell_catalog", "srd_spells") or {}).get("spells", [])
            if not isinstance(spell_ref, str) or not spell_ref or ":" in spell_ref or not any(
                row.get("id") == spell_ref for row in spells
            ):
                raise ClassFeatureError(f"{definition.id} grants an unknown spell")
        progression = progression_catalog.classes.get(definition.class_id)
        if progression is None:
            raise ClassFeatureError(
                f"class feature {definition.id} references an unknown class table"
            )
        validate_owner_grant(definition, progression_catalog)
        validate_aura_parameters(definition, catalog)
        validate_condition_immunity_parameters(definition)
        for key, value in definition.parameters.items():
            if key == "crit_threshold":
                if (
                    isinstance(value, bool) or not isinstance(value, int)
                    or not 2 <= value <= 19
                ):
                    raise ClassFeatureError(
                        f"{definition.id}.{key} must be an integer from 2 to 19"
                    )
            if key == "attacks_per_action":
                # 声明值直接决定 Attack 动作能吃几次攻击；2 是 Extra Attack
                # 的下限，8 远高于任何官方职业的上限，超出区间一定写错了。
                if (
                    isinstance(value, bool) or not isinstance(value, int)
                    or not 2 <= value <= 8
                ):
                    raise ClassFeatureError(
                        f"{definition.id}.{key} must be an integer from 2 to 8"
                    )
            if key.endswith("_format"):
                if (
                    not isinstance(value, str)
                    or value.count("{die}") != 1
                    or value.replace("{die}", "").count("{") != 0
                    or value.replace("{die}", "").count("}") != 0
                ):
                    raise ClassFeatureError(
                        f"{definition.id}.{key} must be a single-placeholder template"
                    )
                continue
            if not key.endswith("_track"):
                continue
            if not isinstance(value, str) or not value:
                raise ClassFeatureError(f"{definition.id}.{key} must name a track")
            if value not in progression["tracks"]:
                raise ClassFeatureError(
                    f"{definition.id}.{key} names an unknown progression track: {value}"
                )
        validate_passive_parameters(definition)
        validate_ability_bonus_parameters(definition)
        validate_saving_throw_proficiency_parameters(definition)
        validate_defeat_trigger_parameters(definition, progression_catalog)
        validate_defense_parameters(definition)
        validate_speed_parameters(definition)
        validate_movement_parameters(definition)
        validate_overchannel_parameters(definition)
        validate_hit_rider_parameters(definition)
        validate_cunning_strike_parameters(definition, combat_conditions)
        validate_deflect_parameters(definition, known_feature_ids)
        validate_attack_state_parameters(definition)
        validate_mark_parameters(definition)
        if definition.resource_id:
            declaration = class_resources.declaration(
                definition.class_id, definition.resource_id,
            )
            if declaration is None:
                raise ClassFeatureError(
                    f"{definition.id} references an undeclared class resource: "
                    f"{definition.resource_id}"
                )

def validate_owner_grant(
    definition: ClassFeatureDefinition,
    progression_catalog: Dnd2024ProgressionCatalog,
) -> None:
    """Prove a non-option declaration has a real owner-side grant.

    Subclass declarations must name the subclass the progression catalog
    associates with the declaring class.  Class declarations may still be
    issued by the subclass table: several pre-existing subclass features
    were declared before owner scoping and retain ``class:`` as their
    default owner.

    A subclass declaration must state exactly the level its own table
    grants it at: the subclass table is its only source, so a drifted
    level would silently move the unlock.  A class declaration is looser
    on purpose.  Third-party bundles can hand an id out earlier than the
    catalog says it starts, and ``features_for`` already resolves that
    case through the ``minimum_level > level`` filter; failing the whole
    bundle there would turn a cosmetic mismatch into a ruleset that
    refuses to load.  What must never pass, either way, is a declaration
    that no table grants at all -- that feature can never become owned.
    """

    if definition.option_of:
        return
    class_id = definition.class_id
    progression = progression_catalog.classes[class_id]
    subclass = progression_catalog.subclasses.get(class_id)
    class_levels = [
        level
        for level, feature_ids in enumerate(
            progression["features_by_level"], start=1,
        )
        if definition.id in feature_ids
    ]
    subclass_levels: list[int] = []
    if subclass is not None:
        subclass_levels = [
            int(level)
            for level, feature_ids in subclass["feature_ids_by_level"].items()
            if definition.id in feature_ids
        ]
    if definition.owner.startswith("subclass:"):
        subclass_id = definition.owner.removeprefix("subclass:")
        if subclass is None or str(subclass.get("id") or "") != subclass_id:
            raise ClassFeatureError(
                f"{definition.id}.owner is not a subclass of {class_id}"
            )
        if not subclass_levels:
            raise ClassFeatureError(
                f"{definition.id} has no grant in {definition.owner}"
            )
        if definition.minimum_level != min(subclass_levels):
            raise ClassFeatureError(
                f"{definition.id}.minimum_level must match its subclass grant"
            )
    else:
        levels = class_levels or subclass_levels
        if not levels:
            raise ClassFeatureError(
                f"{definition.id} has no class or subclass grant"
            )

def validate_passive_parameters(definition: ClassFeatureDefinition) -> None:
    """Fail closed on passive primitives a consumer would otherwise misread.

    A declared advantage flag must be exactly ``True``: a ``False``/truthy
    lookalike would silently change death-save dice with no way to tell
    intent from typo.  The regeneration pair must appear together so the
    amount and its ability never drift apart.
    """

    parameters = definition.parameters
    if "death_save_advantage" in parameters and (
        parameters["death_save_advantage"] is not True
    ):
        raise ClassFeatureError(
            f"{definition.id}.death_save_advantage must be true when declared"
        )
    threshold = parameters.get("death_save_crit_threshold")
    if "death_save_crit_threshold" in parameters and (
        isinstance(threshold, bool) or not isinstance(threshold, int)
        or not 2 <= threshold <= 20
    ):
        raise ClassFeatureError(
            f"{definition.id}.death_save_crit_threshold must be an integer from 2 to 20"
        )
    base = parameters.get("turn_start_regen_base")
    if "turn_start_regen_base" in parameters and (
        isinstance(base, bool) or not isinstance(base, int) or base < 1
    ):
        raise ClassFeatureError(
            f"{definition.id}.turn_start_regen_base must be a positive integer"
        )
    ability = parameters.get("turn_start_regen_ability")
    if "turn_start_regen_ability" in parameters and (
        not isinstance(ability, str) or ability not in ABILITY_IDS
    ):
        raise ClassFeatureError(
            f"{definition.id}.turn_start_regen_ability must name an ability"
        )
    if (
        "turn_start_regen_base" in parameters
    ) != ("turn_start_regen_ability" in parameters):
        raise ClassFeatureError(
            f"{definition.id} must declare turn_start_regen_base and "
            "turn_start_regen_ability as a pair"
        )
    for flag in (
        "half_proficiency_bonus", "initiative_advantage",
        "strength_total_replacement",
    ):
        if flag in parameters and parameters[flag] is not True:
            raise ClassFeatureError(
                f"{definition.id}.{flag} must be true when declared"
            )
    floor = parameters.get("ability_check_floor")
    if "ability_check_floor" in parameters and (
        isinstance(floor, bool) or not isinstance(floor, int)
        or not 1 <= floor <= 20
    ):
        raise ClassFeatureError(
            f"{definition.id}.ability_check_floor must be an integer from 1 to 20"
        )
    advantage_refs = parameters.get("skill_advantage_refs")
    if "skill_advantage_refs" in parameters and (
        not isinstance(advantage_refs, list)
        or not advantage_refs
        or any(
            not isinstance(ref, str) or not ref.startswith("skill:")
            for ref in advantage_refs
        )
    ):
        raise ClassFeatureError(
            f"{definition.id}.skill_advantage_refs must list skill refs"
        )
    divisor = parameters.get("critical_hit_move_speed_divisor")
    if "critical_hit_move_speed_divisor" in parameters and (
        isinstance(divisor, bool) or not isinstance(divisor, int) or divisor < 2
    ):
        raise ClassFeatureError(
            f"{definition.id}.critical_hit_move_speed_divisor must be an "
            "integer of at least 2"
        )

def validate_ability_bonus_parameters(definition: ClassFeatureDefinition) -> None:
    """Fail closed on the ability-increase contract.

    Primal Champion and Body and Mind are the declarations today.  Three
    things must hold together, because a half-declared grant is a silent
    one: the bonus keys name real abilities (``ability_bonus_str`` and
    nothing else -- an unknown suffix would otherwise raise nothing), every
    bonus is a positive integer, and the increase declares the cap it is
    bounded by.  A cap other than 25 is refused rather than honoured: the
    number is an absolute ceiling in the rules, and a per-feature variant
    would quietly become a second cap system.
    """

    parameters = definition.parameters
    bonuses = {
        key: value for key, value in parameters.items()
        if key.startswith("ability_bonus_")
    }
    cap = parameters.get("ability_score_cap")
    if not bonuses:
        if cap is not None:
            raise ClassFeatureError(
                f"{definition.id}.ability_score_cap has no ability_bonus_* to bound"
            )
        return
    for key, value in bonuses.items():
        ability = key.removeprefix("ability_bonus_")
        if ability not in ABILITY_IDS:
            raise ClassFeatureError(
                f"{definition.id}.{key} does not name an ability"
            )
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ClassFeatureError(
                f"{definition.id}.{key} must be a positive integer"
            )
    if cap != 25:
        raise ClassFeatureError(
            f"{definition.id}.ability_score_cap must be 25 when declared"
        )

def validate_saving_throw_proficiency_parameters(
    definition: ClassFeatureDefinition,
) -> None:
    """Fail closed on the per-ability saving throw proficiency list.

    Slippery Mind is the only declaration today.  The value is a list of
    canonical ability ids -- not a sentence, not the ``"all"`` string the
    other declaration uses -- so merging it into the class's refs cannot
    silently grant nothing, and a repeat cannot quietly cheapen the rule
    that a proficiency is counted once.
    """

    parameters = definition.parameters
    if "saving_throw_proficiencies" not in parameters:
        return
    declared = parameters["saving_throw_proficiencies"]
    if not isinstance(declared, list) or not declared:
        raise ClassFeatureError(
            f"{definition.id}.saving_throw_proficiencies must be a non-empty list"
        )
    if any(
        not isinstance(ability, str) or ability not in ABILITY_IDS
        for ability in declared
    ):
        raise ClassFeatureError(
            f"{definition.id}.saving_throw_proficiencies must hold ability ids"
        )
    if len(set(declared)) != len(declared):
        raise ClassFeatureError(
            f"{definition.id}.saving_throw_proficiencies repeats an ability"
        )

def validate_defeat_trigger_parameters(
    definition: ClassFeatureDefinition,
    progression_catalog: Dnd2024ProgressionCatalog,
) -> None:
    """Fail closed on the defeat-trigger payload.

    The payload is one closed four-key set: what triggers it, which ability
    feeds the formula, which class level the formula adds, and the floor.
    Declaring a subset would leave the formula with either no ability or no
    level, which resolves to a silently wrong or inert feature -- so a
    partial set is rejected at load time rather than at settlement.

    Optional other_defeat_range_ft also grants the benefit for someone
    else's kill measured from the defeated enemy to the feature owner
    (Dark One's Blessing, SRD 5.2.1 p76).
    """

    parameters = definition.parameters
    retired = [key for key in _RETIRED_DEFEAT_KEYS if key in parameters]
    if retired:
        # Retired formula keys remain rejected at the catalog boundary.
        raise ClassFeatureError(
            f"{definition.id} declares the retired parameter {retired[0]}"
        )
    radius = parameters.get("other_defeat_range_ft")
    if "other_defeat_range_ft" in parameters and (
        isinstance(radius, bool) or not isinstance(radius, int) or radius <= 0
    ):
        raise ClassFeatureError(
            f"{definition.id}.other_defeat_range_ft must be a positive integer"
        )
    keys = _DEFEAT_TRIGGER_KEYS
    # Only claim a ``trigger`` key this payload actually owns.  Another
    # slice may declare its own trigger name on a different feature, and
    # rejecting it here would couple two unrelated payloads.
    formula_keys = [key for key in keys if key != "trigger" and key in parameters]
    if not formula_keys and parameters.get("trigger") not in _DEFEAT_TRIGGERS:
        return
    missing = [key for key in keys if key not in parameters]
    if missing:
        raise ClassFeatureError(
            f"{definition.id} must declare {' / '.join(keys)} together; "
            f"missing {' / '.join(missing)}"
        )
    trigger = parameters.get("trigger")
    if trigger not in _DEFEAT_TRIGGERS:
        raise ClassFeatureError(
            f"{definition.id}.trigger must be one of {sorted(_DEFEAT_TRIGGERS)}"
        )
    ability = parameters.get("temp_hp_ability")
    if not isinstance(ability, str) or ability not in ABILITY_IDS:
        raise ClassFeatureError(
            f"{definition.id}.temp_hp_ability must name an ability"
        )
    class_ref = parameters.get("temp_hp_class_ref")
    if not isinstance(class_ref, str) or not class_ref.startswith("class:"):
        raise ClassFeatureError(
            f"{definition.id}.temp_hp_class_ref must be a class:<id> reference"
        )
    class_id = class_ref.removeprefix("class:")
    progression = progression_catalog
    if (
        not class_id
        or progression is None
        or class_id not in progression.classes
    ):
        raise ClassFeatureError(
            f"{definition.id}.temp_hp_class_ref names an unknown class table"
        )
    minimum = parameters.get("temp_hp_minimum")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ClassFeatureError(
            f"{definition.id}.temp_hp_minimum must be an integer of at least 1"
        )

def validate_speed_parameters(definition: ClassFeatureDefinition) -> None:
    """Fail closed on the Speed-bonus contract.

    A flat Speed bonus is a small positive number of feet; a negative or
    absurd value would silently rewrite every derived Speed in the game.
    The level-scaled form is validated by the generic ``_track`` rule.
    """

    parameters = definition.parameters
    value = parameters.get("speed_bonus_ft")
    if "speed_bonus_ft" in parameters and (
        isinstance(value, bool) or not isinstance(value, int)
        or not 1 <= value <= 120
    ):
        raise ClassFeatureError(
            f"{definition.id}.speed_bonus_ft must be an integer from 1 to 120"
        )

def validate_movement_parameters(definition: ClassFeatureDefinition) -> None:
    """Fail closed on the special-Speed and jump contract.

    ``climb_speed_from_speed`` / ``swim_speed_from_speed`` are flags, not
    amounts: the distance always equals the character's current Speed, so a
    ``False`` or a number would promise a mode the resolver cannot honour.
    ``jump_ability`` names the score that sets the jump distance, and a typo
    there would silently fall back to Strength -- the very substitution the
    Thief feature exists to make.
    """

    parameters = definition.parameters
    for key in ("climb_speed_from_speed", "swim_speed_from_speed"):
        if key in parameters and parameters[key] is not True:
            raise ClassFeatureError(
                f"{definition.id}.{key} must be true when declared"
            )
    ability = parameters.get("jump_ability")
    if "jump_ability" in parameters and ability not in ("str", "dex"):
        raise ClassFeatureError(
            f"{definition.id}.jump_ability must be str or dex"
        )

def validate_condition_immunity_parameters(
    definition: ClassFeatureDefinition,
) -> None:
    """Fail closed on the condition-immunity and condition-removal contract.

    Immunity is a set of condition ids a feature *suppresses*; removal is a
    set the actor may *delete*, gated by a timing that has to be one the
    engine actually honours.  Both are lists of ids, never sentences: a
    typo here would silently immunize nothing, and an unrecognised timing
    would promise a removal that never happens at the wrong moment.
    """

    parameters = definition.parameters

    def _string_list(key: str) -> None:
        value = parameters[key]
        if not isinstance(value, list) or not value:
            raise ClassFeatureError(
                f"{definition.id}.{key} must be a non-empty list of condition ids"
            )
        if any(not isinstance(code, str) or not code for code in value):
            raise ClassFeatureError(
                f"{definition.id}.{key} must hold condition ids"
            )
        if len(set(value)) != len(value):
            raise ClassFeatureError(f"{definition.id}.{key} repeats a condition")

    for key in (
        "immune_conditions", "removable_conditions", "exhaustion_sources_ignored",
        "removes_conditions_on_gaining",
    ):
        if key in parameters:
            _string_list(key)

    if "immune_while_condition" in parameters:
        value = parameters["immune_while_condition"]
        if not isinstance(value, str) or not value:
            raise ClassFeatureError(
                f"{definition.id}.immune_while_condition must name a condition"
            )

    trigger_key = "removes_conditions_on_gaining_condition"
    if ("removes_conditions_on_gaining" in parameters) != (trigger_key in parameters):
        # 清理规则必须同时说明"获得哪个条件时"与"清理什么"；只有一半
        # 的声明会被引擎读成一条永远不触发的规则。
        raise ClassFeatureError(
            f"{definition.id} must declare removes_conditions_on_gaining and "
            f"{trigger_key} together"
        )
    if trigger_key in parameters:
        trigger = parameters[trigger_key]
        if not isinstance(trigger, str) or not trigger:
            raise ClassFeatureError(
                f"{definition.id}.{trigger_key} must name the gained condition"
            )

    if "removable_at" in parameters:
        if parameters["removable_at"] != "own_turn_end":
            raise ClassFeatureError(
                f"{definition.id}.removable_at currently supports own_turn_end only"
            )
        if "removable_conditions" not in parameters:
            raise ClassFeatureError(
                f"{definition.id} declares removable_at without removable_conditions"
            )

def validate_aura_parameters(
    definition: ClassFeatureDefinition, catalog: Dnd2024ClassFeatureCatalog,
) -> None:
    """Fail closed on the aura declaration contract.

    An aura is three separate declarations that only mean something
    together: the base aura owns the range and the saving-throw bonus, an
    expansion names a base aura and overrides its range, and a condition
    immunity names the base aura it rides on.  Nothing here names Paladin
    -- the relation is catalog data, so the shape is reusable -- and a
    dangling or self-referential relation is rejected at load because the
    consumer would otherwise project no aura at all and say nothing.
    """

    parameters = definition.parameters
    # 同一 id 可能同时存在于 class 与 subclass 两个 owner 下，按 id 收集后
    # 只认真的声明了范围的那一条，避免拿错 owner 的同名条目。
    index = catalog.features_by_key

    def _base_aura(feature_id: Any) -> ClassFeatureDefinition | None:
        if not isinstance(feature_id, str) or not feature_id:
            return None
        for candidate in index.values():
            if candidate.id == feature_id and "aura_range_ft" in candidate.parameters:
                return candidate
        return None

    for key in ("aura_range_ft", "aura_range_override_ft"):
        value = parameters.get(key)
        if key in parameters and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise ClassFeatureError(
                f"{definition.id}.{key} must be a positive integer"
            )

    has_ability = "aura_save_bonus_ability" in parameters
    has_minimum = "aura_save_bonus_minimum" in parameters
    if has_ability != has_minimum:
        raise ClassFeatureError(
            f"{definition.id} must declare aura_save_bonus_ability and "
            f"aura_save_bonus_minimum together"
        )
    if has_ability:
        ability = parameters["aura_save_bonus_ability"]
        if not isinstance(ability, str) or ability not in ABILITY_IDS:
            raise ClassFeatureError(
                f"{definition.id}.aura_save_bonus_ability must name an ability"
            )
        minimum = parameters["aura_save_bonus_minimum"]
        if (
            isinstance(minimum, bool) or not isinstance(minimum, int)
            or minimum < 1
        ):
            raise ClassFeatureError(
                f"{definition.id}.aura_save_bonus_minimum must be an integer "
                f"of at least 1"
            )

    # 豁免加值必须挂在一个有范围的基础光环上，否则它永远不会生效。
    bonus_declared = has_ability or "aura_save_bonus_minimum" in parameters
    if bonus_declared and "aura_range_ft" not in parameters:
        raise ClassFeatureError(
            f"{definition.id} declares a saving-throw bonus without aura_range_ft"
        )

    has_immunities = "aura_condition_immunities" in parameters
    has_source = "aura_source_feature_id" in parameters
    if has_immunities != has_source:
        raise ClassFeatureError(
            f"{definition.id} must declare aura_source_feature_id and "
            f"aura_condition_immunities together"
        )
    if has_immunities:
        immunities = parameters["aura_condition_immunities"]
        if not isinstance(immunities, list) or not immunities:
            raise ClassFeatureError(
                f"{definition.id}.aura_condition_immunities must be a non-empty list"
            )
        if any(not isinstance(code, str) or not code for code in immunities):
            raise ClassFeatureError(
                f"{definition.id}.aura_condition_immunities must hold condition ids"
            )
        if len(set(immunities)) != len(immunities):
            raise ClassFeatureError(
                f"{definition.id}.aura_condition_immunities repeats a condition"
            )
        unsupported = sorted(code for code in immunities if code not in _AURA_IMMUNITY_CONDITIONS)
        if unsupported:
            raise ClassFeatureError(
                f"{definition.id}.aura_condition_immunities does not support "
                f"{', '.join(unsupported)} yet"
            )
        source = _base_aura(parameters["aura_source_feature_id"])
        if source is None:
            raise ClassFeatureError(
                f"{definition.id}.aura_source_feature_id must name a base aura"
            )

    has_override = "aura_range_override_ft" in parameters
    has_expands = "aura_expands_feature_id" in parameters
    if has_override != has_expands:
        raise ClassFeatureError(
            f"{definition.id} must declare aura_expands_feature_id and "
            f"aura_range_override_ft together"
        )
    if has_expands:
        expanded = _base_aura(parameters["aura_expands_feature_id"])
        if expanded is None or expanded.id == definition.id:
            raise ClassFeatureError(
                f"{definition.id}.aura_expands_feature_id must name another base aura"
            )
        # 扩展链必须收敛：A 覆盖 B、B 又覆盖 A 时，"有效范围"取决于从哪
        # 条开始折叠，任何确定性说法都是假的，因此整条链拒绝加载。
        seen = {definition.id}
        cursor = expanded.id
        while cursor:
            if cursor in seen:
                raise ClassFeatureError(
                    f"{definition.id}.aura_expands_feature_id forms a cycle"
                )
            seen.add(cursor)
            following = _base_aura(cursor)
            if following is None:
                break
            next_id = following.parameters.get("aura_expands_feature_id")
            cursor = next_id if isinstance(next_id, str) and next_id else ""

def validate_defense_parameters(definition: ClassFeatureDefinition) -> None:
    """Fail closed on the defense-calculation contract.

    The unarmored AC bonus is one ability id, never a stacked expression;
    the two Hit Point knobs are bounded non-negative integers so a typo
    cannot silently become a huge or negative maximum.
    """

    parameters = definition.parameters
    ability = parameters.get("unarmored_ac_ability_bonus")
    if "unarmored_ac_ability_bonus" in parameters and (
        not isinstance(ability, str) or ability not in ABILITY_IDS
    ):
        raise ClassFeatureError(
            f"{definition.id}.unarmored_ac_ability_bonus must name an ability"
        )
    for key in ("max_hp_bonus_first", "max_hp_bonus_per_level_after"):
        value = parameters.get(key)
        if key in parameters and (
            isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= 20
        ):
            raise ClassFeatureError(
                f"{definition.id}.{key} must be an integer from 0 to 20"
            )

def validate_overchannel_parameters(definition: ClassFeatureDefinition) -> None:
    """Fail closed on the maximize-damage contract.

    A typo in the slot cap, the self-damage die or the base cost would
    silently change how much a maximized cast costs, so each declared knob
    is range-checked at load time.  Missing knobs are fine: consumers fall
    back to their documented defaults.
    """

    parameters = definition.parameters
    for key, low, high in (
        ("maximum_slot_level", 1, 9),
        ("self_damage_die", 2, 20),
        ("initial_self_damage_dice", 1, 10),
    ):
        value = parameters.get(key)
        if key in parameters and (
            isinstance(value, bool) or not isinstance(value, int)
            or not low <= value <= high
        ):
            raise ClassFeatureError(
                f"{definition.id}.{key} must be an integer from {low} to {high}"
            )
    scope = parameters.get("usage_scope")
    if "usage_scope" in parameters and scope not in FEATURE_USAGE_SCOPES:
        raise ClassFeatureError(
            f"{definition.id}.usage_scope must be one of "
            f"{sorted(FEATURE_USAGE_SCOPES)}"
        )

def validate_hit_rider_parameters(definition: ClassFeatureDefinition) -> None:
    """Fail closed on every hit-rider declaration this slice consumes.

    The check is deliberately strict in both directions: an unknown key
    means the author expected behaviour no code implements, and a missing
    key means the code would read a default the SRD never states.  Both
    are load-time errors rather than silent no-ops.
    """

    expected = _RIDER_PARAMETER_KEYS.get(definition.id)
    if expected is None:
        return
    # 延迟导入：``combat.catalog`` 经物品声明回到 character 包，模块级导入
    # 会把 features <-> character 变成环。这里只在加载期各声明校验时读一次
    # 两个闭集，不承担运行时职责。
    from src.rulesets.dnd2024.combat.catalog import (
        DAMAGE_TYPES, DICE_RE, SAVE_ABILITIES,
    )

    parameters = definition.parameters
    unknown = sorted(set(parameters) - expected)
    if unknown:
        raise ClassFeatureError(
            f"{definition.id} does not support {', '.join(unknown)}"
        )
    # A separate next-attack effect is optional for declarations that only
    # grant a persistent success condition.
    optional = {"on_success_attack_condition"} if definition.id == "stunning_strike" else set()
    missing = sorted(expected - set(parameters) - optional)
    if missing:
        raise ClassFeatureError(
            f"{definition.id} is missing {', '.join(missing)}"
        )

    feature_id = definition.id

    def flag(key: str) -> None:
        if not isinstance(parameters[key], bool):
            raise ClassFeatureError(f"{feature_id}.{key} must be a boolean")

    def positive_int(key: str) -> None:
        value = parameters[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ClassFeatureError(
                f"{feature_id}.{key} must be a positive integer"
            )

    def bare_id(key: str) -> None:
        value = parameters[key]
        if not isinstance(value, str) or not value:
            raise ClassFeatureError(f"{feature_id}.{key} must name a value")

    def one_of(key: str, allowed: frozenset[str]) -> None:
        value = parameters[key]
        if not isinstance(value, str) or value not in allowed:
            raise ClassFeatureError(
                f"{feature_id}.{key} must be one of {sorted(allowed)}"
            )

    def id_list(key: str, allowed: frozenset[str]) -> None:
        value = parameters[key]
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(set(value)) != len(value)
        ):
            raise ClassFeatureError(
                f"{feature_id}.{key} must be a list drawn from {sorted(allowed)}"
            )

    def dice(key: str) -> None:
        value = parameters[key]
        if not isinstance(value, str) or not DICE_RE.match(value):
            raise ClassFeatureError(
                f"{feature_id}.{key} must be a dice formula"
            )

    if feature_id == "stunning_strike":
        flag("once_per_turn")
        positive_int("focus_cost")
        one_of("save_ability", SAVE_ABILITIES)
        bare_id("on_fail_condition")
        bare_id("on_success_condition")
        if "on_success_attack_condition" in parameters:
            bare_id("on_success_attack_condition")
        one_of("condition_duration", _RIDER_CONDITION_DURATIONS)
    elif feature_id == "empowered_strikes":
        id_list("unarmed_damage_type_choices", DAMAGE_TYPES)
    elif feature_id == "open_hand_technique":
        id_list("flurry_hit_choices", _OPEN_HAND_CHOICES)
        bare_id("addle_condition")
        positive_int("push_distance_ft")
        one_of("push_save_ability", SAVE_ABILITIES)
        one_of("topple_save_ability", SAVE_ABILITIES)
        bare_id("topple_condition")
    elif feature_id == "radiant_strikes":
        dice("extra_damage")
        one_of("extra_damage_type", DAMAGE_TYPES)
        id_list("attack_kinds", _RIDER_ATTACK_KINDS)
    elif feature_id in {"divine_strike", "primal_strike"}:
        dice("damage_dice")
        id_list("damage_types", DAMAGE_TYPES)
        flag("once_per_turn")
        id_list("attack_kinds", _RIDER_ATTACK_KINDS)
    elif feature_id == "potent_spellcasting":
        value = parameters["cantrip_source_class"]
        if not isinstance(value, str) or not value.startswith("class:"):
            raise ClassFeatureError(
                f"{feature_id}.cantrip_source_class must name a class"
            )
        one_of("ability", frozenset(ABILITY_IDS))
    elif feature_id == "improved_blessed_strikes":
        dice("divine_strike_dice")
        positive_int("temp_hp_ability_multiplier")
        positive_int("temp_hp_range_ft")
    elif feature_id == "improved_elemental_fury":
        dice("primal_strike_dice")
        positive_int("range_bonus_ft")
        positive_int("range_minimum_ft")

#: 诡诈打击的效果 id 与各自的参数键。成本、豁免属性、条件、时长全部逐
#: 效果读，所以"每个效果的键集"本身就是契约：多一个键说明作者期待了没有
#: 代码实现的行为，少一个键说明代码会退回到规则没说过的默认值。
_CUNNING_STRIKE_BASE_EFFECTS = ("poison", "trip", "withdraw")
_CUNNING_STRIKE_ADDITIONAL_EFFECTS = ("daze", "knock_out", "obscure")
_CUNNING_STRIKE_EFFECT_KEYS: dict[str, frozenset[str]] = {
    "poison": frozenset({
        "dice_cost", "save_ability", "condition", "duration", "repeat_save",
        "requires_item",
    }),
    "trip": frozenset({"dice_cost", "save_ability", "condition", "max_target_size"}),
    "withdraw": frozenset({"dice_cost", "speed_divisor", "provokes_opportunity"}),
    "daze": frozenset({
        "dice_cost", "save_ability", "condition", "duration", "turn_choice_limit",
    }),
    "knock_out": frozenset({
        "dice_cost", "save_ability", "condition", "duration", "repeat_save",
        "ends_on_damage",
    }),
    "obscure": frozenset({"dice_cost", "save_ability", "condition", "duration"}),
}
#: SRD 5.2.1 p62 的价格，一颗不差。内容里重复写一遍不是为了"可配置"，而是
#: 为了让这里能把它拦住：定价漂移（多打一个 0、复制粘贴成别的效果）不会
#: 报错，只会让特性静默变贵。
_CUNNING_STRIKE_DICE_COSTS: dict[str, int] = {
    "poison": 1, "trip": 1, "withdraw": 1,
    "daze": 2, "knock_out": 6, "obscure": 3,
}
_CUNNING_STRIKE_DURATIONS = frozenset({"target_turn_end", "one_minute"})
_CUNNING_STRIKE_TURN_CHOICES = frozenset({"move", "action", "bonus_action"})


def validate_cunning_strike_parameters(
    definition: ClassFeatureDefinition, conditions: dict[str, Any],
) -> None:
    """Fail closed on the three Cunning Strike declarations.

    The cost table is authoritative here rather than in content: two places
    state the same number only so that a typo in one of them fails at load
    instead of silently re-pricing an effect mid-campaign.
    """

    feature_id = definition.id
    if feature_id not in {"cunning_strike", "improved_cunning_strike", "devious_strikes"}:
        return
    # 延迟导入：与 ``validate_hit_rider_parameters`` 同一个理由（features ->
    # combat 的顶层导入会把 character 包也拉成环）。
    from src.rulesets.dnd2024.combat.catalog import SAVE_ABILITIES
    from src.rulesets.dnd2024.combat.primitives import SIZE_CODES

    parameters = definition.parameters
    if feature_id == "improved_cunning_strike":
        # 11 级只抬个数，个数写在 cunning_strike 的 upgrade 参数上。
        if parameters:
            raise ClassFeatureError(
                f"{feature_id} does not support {', '.join(sorted(parameters))}"
            )
        return
    base = feature_id == "cunning_strike"
    effects = _CUNNING_STRIKE_BASE_EFFECTS if base else _CUNNING_STRIKE_ADDITIONAL_EFFECTS
    expected = {"effect_options", "effects_per_use", "upgrade_level", "save_dc_ability"} if base else {
        "additional_effect_options",
    }
    if base:
        expected.add("effects_per_use_upgrade")
    for effect in effects:
        for key in _CUNNING_STRIKE_EFFECT_KEYS[effect]:
            expected.add(f"{effect}_{key}")
    unknown = sorted(set(parameters) - expected)
    if unknown:
        raise ClassFeatureError(f"{feature_id} does not support {', '.join(unknown)}")
    missing = sorted(expected - set(parameters))
    if missing:
        raise ClassFeatureError(f"{feature_id} is missing {', '.join(missing)}")
    if base:
        if parameters["effect_options"] != list(_CUNNING_STRIKE_BASE_EFFECTS):
            raise ClassFeatureError(
                f"{feature_id}.effect_options must list {list(_CUNNING_STRIKE_BASE_EFFECTS)}"
            )
        positive_int_parameter(feature_id, parameters, "effects_per_use")
        positive_int_parameter(feature_id, parameters, "effects_per_use_upgrade")
        if parameters["effects_per_use_upgrade"] < parameters["effects_per_use"]:
            raise ClassFeatureError(
                f"{feature_id}.effects_per_use_upgrade cannot lower the cap"
            )
        if parameters["upgrade_level"] != 11:
            raise ClassFeatureError(f"{feature_id}.upgrade_level must be 11")
    else:
        if parameters["additional_effect_options"] != list(_CUNNING_STRIKE_ADDITIONAL_EFFECTS):
            raise ClassFeatureError(
                f"{feature_id}.additional_effect_options must list "
                f"{list(_CUNNING_STRIKE_ADDITIONAL_EFFECTS)}"
            )
    if base and parameters["save_dc_ability"] not in ABILITY_IDS:
        raise ClassFeatureError(f"{feature_id} declares an unknown save DC ability")
    for effect in effects:
        prefix = f"{effect}_"
        cost = parameters[f"{prefix}dice_cost"]
        if isinstance(cost, bool) or cost != _CUNNING_STRIKE_DICE_COSTS[effect]:
            raise ClassFeatureError(
                f"{feature_id}.{prefix}dice_cost must be "
                f"{_CUNNING_STRIKE_DICE_COSTS[effect]}"
            )
        if "save_ability" in _CUNNING_STRIKE_EFFECT_KEYS[effect]:
            ability = parameters[f"{prefix}save_ability"]
            if ability not in SAVE_ABILITIES:
                raise ClassFeatureError(
                    f"{feature_id}.{prefix}save_ability must be one of "
                    f"{sorted(SAVE_ABILITIES)}"
                )
        if "condition" in _CUNNING_STRIKE_EFFECT_KEYS[effect]:
            condition = parameters[f"{prefix}condition"]
            if condition not in conditions:
                raise ClassFeatureError(
                    f"{feature_id}.{prefix}condition names an unknown condition"
                )
        if "duration" in _CUNNING_STRIKE_EFFECT_KEYS[effect]:
            if parameters[f"{prefix}duration"] not in _CUNNING_STRIKE_DURATIONS:
                raise ClassFeatureError(
                    f"{feature_id}.{prefix}duration must be one of "
                    f"{sorted(_CUNNING_STRIKE_DURATIONS)}"
                )
        for flag in ("repeat_save", "ends_on_damage", "provokes_opportunity"):
            key = f"{prefix}{flag}"
            if key in _CUNNING_STRIKE_EFFECT_KEYS[effect] and not isinstance(
                parameters[key], bool
            ):
                raise ClassFeatureError(f"{feature_id}.{key} must be a boolean")
        if "requires_item" in _CUNNING_STRIKE_EFFECT_KEYS[effect]:
            item = parameters[f"{prefix}requires_item"]
            if not isinstance(item, str) or not item:
                raise ClassFeatureError(
                    f"{feature_id}.{prefix}requires_item must name an item"
                )
        if "max_target_size" in _CUNNING_STRIKE_EFFECT_KEYS[effect]:
            if parameters[f"{prefix}max_target_size"] not in SIZE_CODES:
                raise ClassFeatureError(
                    f"{feature_id}.{prefix}max_target_size must be one of {list(SIZE_CODES)}"
                )
        if "speed_divisor" in _CUNNING_STRIKE_EFFECT_KEYS[effect]:
            positive_int_parameter(feature_id, parameters, f"{prefix}speed_divisor")
        if "turn_choice_limit" in _CUNNING_STRIKE_EFFECT_KEYS[effect]:
            choices = parameters[f"{prefix}turn_choice_limit"]
            if (
                not isinstance(choices, list)
                or not choices
                or any(choice not in _CUNNING_STRIKE_TURN_CHOICES for choice in choices)
            ):
                raise ClassFeatureError(
                    f"{feature_id}.{prefix}turn_choice_limit must be drawn from "
                    f"{sorted(_CUNNING_STRIKE_TURN_CHOICES)}"
                )


def positive_int_parameter(
    feature_id: str, parameters: Mapping[str, Any], key: str,
) -> None:
    value = parameters[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ClassFeatureError(f"{feature_id}.{key} must be a positive integer")


_DEFLECT_KEYS = frozenset({
    "reduction_dice",
    "reduction_ability",
    "reduction_level_scale",
    "deflect_damage_types",
    "redirect_focus_cost",
    "redirect_dice_count",
    "redirect_ability",
    "redirect_range_melee_ft",
    "redirect_range_ranged_ft",
    "redirect_requires_total_cover",
})
_DEFLECT_ENERGY_KEYS = frozenset({"extends_feature", "deflect_all_damage_types"})


def validate_deflect_parameters(
    definition: ClassFeatureDefinition, known_ids: frozenset[str],
) -> None:
    """Fail closed on the two Deflect declarations.

    ``deflect_energy`` is not a second implementation: it names the feature it
    extends and flips one switch.  Anything else it declares would promise
    behaviour no code reads, so the key set is exact in both directions.
    """

    feature_id = definition.id
    if feature_id not in {"deflect_attacks", "deflect_energy"}:
        return
    # 延迟导入：与 ``validate_cunning_strike_parameters`` 同一个理由。
    from src.rulesets.dnd2024.combat.action_adapter import _NDM_RE
    from src.rulesets.dnd2024.combat.catalog import DAMAGE_TYPES

    parameters = definition.parameters
    if feature_id == "deflect_energy":
        unknown = sorted(set(parameters) - _DEFLECT_ENERGY_KEYS)
        if unknown:
            raise ClassFeatureError(f"{feature_id} does not support {', '.join(unknown)}")
        missing = sorted(_DEFLECT_ENERGY_KEYS - set(parameters))
        if missing:
            raise ClassFeatureError(f"{feature_id} is missing {', '.join(missing)}")
        target = parameters["extends_feature"]
        if not isinstance(target, str) or target not in known_ids:
            raise ClassFeatureError(
                f"{feature_id}.extends_feature names an unknown feature"
            )
        if parameters["deflect_all_damage_types"] is not True:
            raise ClassFeatureError(
                f"{feature_id}.deflect_all_damage_types must be true"
            )
        return
    unknown = sorted(set(parameters) - _DEFLECT_KEYS)
    if unknown:
        raise ClassFeatureError(f"{feature_id} does not support {', '.join(unknown)}")
    missing = sorted(_DEFLECT_KEYS - set(parameters))
    if missing:
        raise ClassFeatureError(f"{feature_id} is missing {', '.join(missing)}")
    dice = parameters["reduction_dice"]
    if not isinstance(dice, str) or not _NDM_RE.match(dice):
        raise ClassFeatureError(f"{feature_id}.reduction_dice must be an NdM formula")
    for key in ("reduction_ability", "redirect_ability"):
        if parameters[key] not in ABILITY_IDS:
            raise ClassFeatureError(f"{feature_id}.{key} must be one of {sorted(ABILITY_IDS)}")
    types = parameters["deflect_damage_types"]
    if (
        not isinstance(types, list)
        or not types
        or any(not isinstance(item, str) or item not in DAMAGE_TYPES for item in types)
    ):
        raise ClassFeatureError(
            f"{feature_id}.deflect_damage_types must list known damage types"
        )
    positive_int_parameter(feature_id, parameters, "reduction_level_scale")
    positive_int_parameter(feature_id, parameters, "redirect_focus_cost")
    positive_int_parameter(feature_id, parameters, "redirect_dice_count")
    positive_int_parameter(feature_id, parameters, "redirect_range_melee_ft")
    positive_int_parameter(feature_id, parameters, "redirect_range_ranged_ft")
    if parameters["redirect_requires_total_cover"] is not True:
        raise ClassFeatureError(
            f"{feature_id}.redirect_requires_total_cover must be true"
        )


def validate_attack_state_parameters(
    definition: ClassFeatureDefinition) -> None:
    """Fail closed on the pre-roll declaration and persistent-state contract.

    Same two-way rule as the rider table: an unknown key promises behaviour
    no code reads, and a missing key would make the runtime fall back to a
    number the SRD never states.  The effect ids are a closed set because
    they travel through an intent -- a typo there is a silent no-op that
    still consumes the once-per-turn budget.
    """

    expected = _ATTACK_STATE_PARAMETER_KEYS.get(definition.id)
    if expected is None:
        return
    from src.rulesets.dnd2024.combat.catalog import DICE_RE

    parameters = definition.parameters
    unknown = sorted(set(parameters) - expected)
    if unknown:
        raise ClassFeatureError(
            f"{definition.id} does not support {', '.join(unknown)}"
        )
    missing = sorted(expected - set(parameters))
    if missing:
        raise ClassFeatureError(
            f"{definition.id} is missing {', '.join(missing)}"
        )

    feature_id = definition.id

    def flag(key: str) -> None:
        if not isinstance(parameters[key], bool):
            raise ClassFeatureError(f"{feature_id}.{key} must be a boolean")

    def positive_int(key: str) -> None:
        value = parameters[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ClassFeatureError(
                f"{feature_id}.{key} must be a positive integer"
            )

    def bare_id(key: str) -> None:
        value = parameters[key]
        if not isinstance(value, str) or not value or ":" in value:
            raise ClassFeatureError(f"{feature_id}.{key} must name a value")

    def one_of(key: str, allowed: frozenset[str]) -> None:
        value = parameters[key]
        if not isinstance(value, str) or value not in allowed:
            raise ClassFeatureError(
                f"{feature_id}.{key} must be one of {sorted(allowed)}"
            )

    def id_list(key: str, allowed: frozenset[str]) -> None:
        value = parameters[key]
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(set(value)) != len(value)
        ):
            raise ClassFeatureError(
                f"{feature_id}.{key} must be a list drawn from {sorted(allowed)}"
            )

    def dice(key: str) -> None:
        value = parameters[key]
        if not isinstance(value, str) or not DICE_RE.match(value):
            raise ClassFeatureError(f"{feature_id}.{key} must be a dice formula")

    if feature_id == "reckless_attack":
        bare_id("self_condition")
        one_of("condition_duration", _RIDER_CONDITION_DURATIONS)
        one_of("ability", frozenset(ABILITY_IDS))
    elif feature_id == "frenzy":
        bare_id("requires_condition")
        bare_id("requires_declaration")
        one_of("ability", frozenset(ABILITY_IDS))
        # 骰面决定每一颗狂怒骰的点数上限；颗数来自 class track，不在这里。
        positive_int("damage_die_sides")
        flag("damage_type_from_attack")
        flag("once_per_turn")
    elif feature_id == "brutal_strike":
        bare_id("requires_declaration")
        one_of("ability", frozenset(ABILITY_IDS))
        dice("damage_dice")
        dice("damage_dice_upgrade")
        positive_int("upgrade_level")
        id_list("effect_options", _BRUTAL_STRIKE_EFFECTS)
        positive_int("effects_per_use")
        positive_int("effects_per_use_upgrade")
        flag("once_per_turn")
        positive_int("forceful_push_distance_ft")
        positive_int("forceful_follow_move_divisor")
        bare_id("hamstring_condition")
        positive_int("hamstring_speed_reduction_ft")
        one_of("hamstring_duration", _RIDER_CONDITION_DURATIONS)
        if parameters["effects_per_use_upgrade"] < parameters["effects_per_use"]:
            raise ClassFeatureError(
                f"{feature_id}.effects_per_use_upgrade cannot lower the allowance"
            )
    elif feature_id == "improved_brutal_strike":
        id_list("additional_effect_options", _BRUTAL_STRIKE_EFFECTS)
        bare_id("staggering_save_condition")
        bare_id("staggering_reaction_condition")
        one_of("staggering_duration", _RIDER_CONDITION_DURATIONS)
        bare_id("sundering_condition")
        positive_int("sundering_attack_bonus")
        one_of("sundering_duration", _RIDER_CONDITION_DURATIONS)
    elif feature_id == "studied_attacks":
        bare_id("mark_kind")
        one_of("mode", frozenset({"advantage", "disadvantage"}))
        # 到期点是攻击者"下一个回合结束"：引擎只实现这一个时点，写成
        # 别的值会得到一条永远不会到期的标记。
        one_of("expiry", frozenset({"attacker_next_turn_end"}))

def validate_mark_parameters(definition: ClassFeatureDefinition) -> None:
    """Fail closed on the Hunter's Mark chain's declared numbers.

    The five declarations are one shape -- a spell instance the character
    owns plus four narrow readings of it -- and every reading takes its
    number from here.  An unknown key promises behaviour nothing reads; a
    die size outside the canonical set would roll a d3 nobody prints.
    """

    expected = _MARK_PARAMETER_KEYS.get(definition.id)
    if expected is None:
        return
    parameters = definition.parameters
    unknown = sorted(set(parameters) - expected)
    if unknown:
        raise ClassFeatureError(
            f"{definition.id} does not support {', '.join(unknown)}"
        )
    feature_id = definition.id
    if feature_id == "favored_enemy":
        if not parameters:
            raise ClassFeatureError(
                f"{feature_id} must grant its spell through granted_spell_id"
            )
    elif feature_id == "relentless_hunter":
        # 例外必须绑到一个具名法术上：写成"所有专注"就是另一条规则。
        spell_id = parameters["concentration_damage_exempt_spell_id"]
        if not isinstance(spell_id, str) or not spell_id or ":" in spell_id:
            raise ClassFeatureError(
                f"{feature_id}.concentration_damage_exempt_spell_id must name a spell"
            )
    elif feature_id == "precise_hunter":
        if not isinstance(parameters["marked_target_attack_advantage"], bool):
            raise ClassFeatureError(
                f"{feature_id}.marked_target_attack_advantage must be a boolean"
            )
    elif feature_id == "feral_senses":
        # 原文给的是固定 30 尺（"Blindsight with a range of 30 feet"）。
        # 允许 1..120 等于让内容把这条特性改成任意范围，而引擎的
        # "30 内抵消、31 外不抵消"边界就只有声明撑着了（S0-F18 §4.2.4）。
        range_ft = parameters["blindsight_range_ft"]
        if isinstance(range_ft, bool) or range_ft != 30:
            raise ClassFeatureError(
                f"{feature_id}.blindsight_range_ft must be 30 (the source "
                f"grants exactly 30 feet)"
            )
    elif feature_id == "foe_slayer":
        die = parameters["mark_damage_die"]
        if isinstance(die, bool) or die not in _MARK_DAMAGE_DICE:
            raise ClassFeatureError(
                f"{feature_id}.mark_damage_die must be one of "
                f"{sorted(_MARK_DAMAGE_DICE)}"
            )


def _validate_capability_ids_are_unique(
    catalog: Dnd2024ClassFeatureCatalog, bundle: LoadedRulesetBundle,
) -> None:
    """One capability id identifies one (grantor, capability) pair, globally.

    Combat declares a capability by id alone, so two grantors sharing an id would
    let the forgery gate resolve one declaration while availability priced the
    other.  Feats made this reachable: until they could grant capabilities, the
    id space was class features only.
    """

    from .feat_models import load_feat_definitions

    seen: dict[str, str] = {}

    def claim(owner_id: str, capabilities: tuple[FeatureCapability, ...]) -> None:
        for capability in capabilities:
            previous = seen.get(capability.id)
            if previous is not None:
                raise ClassFeatureError(
                    f"capability id {capability.id!r} is declared by both "
                    f"{previous} and {owner_id}; combat names it by id alone"
                )
            seen[capability.id] = owner_id

    for definition in catalog.features_by_key.values():
        claim(definition.id, definition.capabilities)
    for feat_definition in load_feat_definitions(bundle).values():
        claim(feat_definition.id, feat_definition.capabilities)
