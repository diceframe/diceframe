"""Opt-in attack declarations, distinct from activations and reaction windows.

New intents select canonical IDs in ``attack_declarations``. The legacy flag
names are read here only; saved decisions and retries keep their original data.
A declaration changes when or how an attack is resolved. Feature ownership,
costs, target checks and effect rules still come from the authoritative runtime.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .resources import feature_usage_count, long_effect_active, resource_state

if TYPE_CHECKING:
    from .resolver import Dnd2024ClassFeatureResolver


class HitOperations(Protocol):
    def _hurl_through_hell_hit_events(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        target: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class HitConditionContext:
    features: Dnd2024ClassFeatureResolver
    character: dict[str, Any]
    weapon: Mapping[str, Any]
    now_minutes: int
    used_this_turn: Callable[[str], bool]
    #: The submitted intent. Declarations that take a payload (Cunning Strike's
    #: effect list) read it here; the context is the only handle a
    #: ``plan_condition`` has on the client's choice.
    intent: Mapping[str, Any]

    def parameter(self, feature_id: str, key: str) -> Any:
        return self.features.feature_value(self.character, feature_id, key, None)


@dataclass(frozen=True, slots=True)
class CunningStrikeEffect:
    """One effect row, read from content and never from the client.

    The client names effects only; every cost, save ability, condition and
    gate below comes from the class-feature catalog, so a forged payload
    cannot make an effect cheaper or skip its prerequisite.
    """

    id: str
    owner: str
    dice_cost: int
    save_ability: str = ""
    #: WHICH ability builds the save DC (Dexterity for every Cunning Strike
    #: effect), as opposed to ``save_ability``, which is the ability the target
    #: rolls.  They coincide for Trip and Obscure and differ everywhere else.
    save_dc_ability: str = ""
    condition: str = ""
    duration: str = ""
    save_repeat: bool = False
    ends_on_damage: bool = False
    turn_choice_limit: tuple[str, ...] = ()
    max_target_size: str = ""
    requires_item: str = ""
    speed_divisor: int = 0
    provokes_opportunity: bool = True

    def plan_row(self) -> dict[str, Any]:
        return {
            "kind": "cunning_strike",
            "effect": self.id,
            "feature_id": self.owner,
            "dice_cost": self.dice_cost,
            "save_ability": self.save_ability,
            "save_dc_ability": self.save_dc_ability,
            # The executor reads ``on_fail_condition``: a failed save is what
            # carries every Cunning Strike condition, and naming it the same
            # as Stunning Strike's row keeps one shape for both.
            "on_fail_condition": self.condition,
            "duration": self.duration,
            "save_repeat": self.save_repeat,
            "ends_on_damage": self.ends_on_damage,
            "turn_choice_limit": list(self.turn_choice_limit),
            "max_target_size": self.max_target_size,
            "requires_item": self.requires_item,
            "speed_divisor": self.speed_divisor,
            "provokes_opportunity": self.provokes_opportunity,
        }


@dataclass(frozen=True, slots=True)
class AttackDeclaration:
    id: str
    legacy_flag: str
    surfaces: tuple[str, ...] = ("attack",)
    plan_condition: Callable[[HitConditionContext], dict[str, Any] | None] | None = None
    # Final-hit effects run after held damage and hit conditions settle.
    on_hit: Callable[
        [HitOperations, Any, dict[str, Any], dict[str, Any], dict[str, Any], Any],
        list[dict[str, Any]],
    ] | None = None
    validate_use: Callable[[Dnd2024ClassFeatureResolver, dict[str, Any]], str] | None = None


def _validate_hurl(features: Dnd2024ClassFeatureResolver, character: dict[str, Any]) -> str:
    if not features.has_feature(character, HURL.id):
        return "Hurl Through Hell is not available to this actor"
    if feature_usage_count(character, HURL.id, HURL.id) >= 1:
        return "Hurl Through Hell has no use left until a long rest"
    return ""


def _hurl(
    operations: HitOperations, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
    target: dict[str, Any], rng: Any,
) -> list[dict[str, Any]]:
    return operations._hurl_through_hell_hit_events(instance, combat, attacker, target, rng)


def _quivering_palm_condition(context: HitConditionContext) -> dict[str, Any] | None:
    feature_id = QUIVERING_PALM.id
    if (
        not context.weapon.get("unarmed")
        or not context.features.has_feature(context.character, feature_id)
        or long_effect_active(context.character, feature_id, context.now_minutes) is not None
    ):
        return None
    return {
        "kind": feature_id,
        "feature_id": feature_id,
        "focus_cost": _quivering_palm_focus_cost(context.features, context.character),
        "duration_minutes": int((context.character.get("build") or {}).get("level", 1) or 1) * 1440,
    }


def _stunning_strike_condition(context: HitConditionContext) -> dict[str, Any] | None:
    feature_id = STUNNING_STRIKE.id
    if (
        not context.features.has_feature(context.character, feature_id)
        or context.used_this_turn(feature_id)
        # Martial Arts projects ability_choice on Monk weapons.
        or not (context.weapon.get("unarmed") or context.weapon.get("ability_choice"))
    ):
        return None
    save_ability = str(context.parameter(feature_id, "save_ability") or "")
    on_fail = str(context.parameter(feature_id, "on_fail_condition") or "")
    on_success = str(context.parameter(feature_id, "on_success_condition") or "")
    duration = str(context.parameter(feature_id, "condition_duration") or "")
    if not (save_ability and on_fail and on_success and duration):
        return None
    return {
        "kind": feature_id,
        "feature_id": feature_id,
        "save_ability": save_ability,
        "focus_cost": int(context.parameter(feature_id, "focus_cost") or 1),
        "on_fail_condition": on_fail,
        "on_success_condition": on_success,
        "on_success_attack_condition": str(context.parameter(feature_id, "on_success_attack_condition") or ""),
        "duration": duration,
    }


#: ``3d6`` / ``3d6+2``: the Sneak Attack pool the budget is measured against.
_SNEAK_DICE_RE = re.compile(r"^(\d+)d(\d+)([+-]\d+)?$")


def cunning_strike_options(
    features: Dnd2024ClassFeatureResolver, character: dict[str, Any],
) -> tuple[CunningStrikeEffect, ...]:
    """Every effect this character has unlocked, in catalog order.

    The order is the content's, not the client's: two effects that both land
    on one hit are resolved in this order so an RNG trace is reproducible no
    matter how the panel happened to serialize its checkboxes.
    """

    if not features.has_feature(character, CUNNING_STRIKE.id):
        return ()
    blocks: list[tuple[str, Any]] = [
        (CUNNING_STRIKE.id, features.feature_value(
            character, CUNNING_STRIKE.id, "effect_options", [],
        )),
    ]
    if features.has_feature(character, DEVIOUS_STRIKES.id):
        blocks.append((DEVIOUS_STRIKES.id, features.feature_value(
            character, DEVIOUS_STRIKES.id, "additional_effect_options", [],
        )))
    effects: list[CunningStrikeEffect] = []
    for owner, declared in blocks:
        for effect_id in declared or []:
            effects.append(_cunning_strike_effect(features, character, owner, str(effect_id)))
    return tuple(effects)


def _cunning_strike_effect(
    features: Dnd2024ClassFeatureResolver, character: dict[str, Any],
    owner: str, effect_id: str,
) -> CunningStrikeEffect:
    def read(suffix: str, default: Any = None) -> Any:
        return features.feature_value(character, owner, f"{effect_id}_{suffix}", default)

    return CunningStrikeEffect(
        id=effect_id,
        owner=owner,
        dice_cost=int(read("dice_cost", 0) or 0),
        save_ability=str(read("save_ability", "") or ""),
        save_dc_ability=str(
            read("save_dc_ability", "")
            # 14 级的三项由 devious_strikes 授予，但 DC 仍归 cunning_strike：
            # 那是同一个特性在说"我的豁免 DC 用敏捷"。
            or features.feature_value(character, CUNNING_STRIKE.id, "save_dc_ability", "")
            or ""
        ),
        condition=str(read("condition", "") or ""),
        duration=str(read("duration", "") or ""),
        save_repeat=bool(read("repeat_save", False)),
        ends_on_damage=bool(read("ends_on_damage", False)),
        turn_choice_limit=tuple(str(value) for value in read("turn_choice_limit", []) or []),
        max_target_size=str(read("max_target_size", "") or ""),
        requires_item=str(read("requires_item", "") or ""),
        speed_divisor=int(read("speed_divisor", 0) or 0),
        provokes_opportunity=bool(read("provokes_opportunity", True)),
    )


def _cunning_strike_condition(context: HitConditionContext) -> dict[str, Any] | None:
    """Translate the client's effect list into one execution row.

    No legality is decided here: ownership, budget and prerequisites are
    rejected at submit time (``validate_cunning_strike``), and the sneak
    predicate itself is only known once the attack has been rolled.
    """

    options = cunning_strike_options(context.features, context.character)
    if not options:
        return None
    chosen = context.intent.get(CUNNING_STRIKE_EFFECTS_KEY)
    if not isinstance(chosen, list):
        return None
    selected = {str(value) for value in chosen if isinstance(value, str)}
    rows = [effect.plan_row() for effect in options if effect.id in selected]
    if not rows:
        return None
    return {"kind": "cunning_strike", "effects": rows}


def cunning_strike_capacity(
    features: Dnd2024ClassFeatureResolver, character: dict[str, Any],
) -> int:
    """How many effects one Sneak Attack may carry at this level."""

    cap = int(features.feature_value(
        character, CUNNING_STRIKE.id, "effects_per_use", 1,
    ) or 1)
    state = features.class_state(character)
    level = int(state[1]) if state else 0
    # ``upgrade_level`` absent means "never upgrades"; defaulting it to 0 would
    # silently hand every rogue the 11th-level cap.
    upgrade = int(features.feature_value(
        character, CUNNING_STRIKE.id, "upgrade_level", 99,
    ) or 99)
    if level >= upgrade:
        cap = int(features.feature_value(
            character, CUNNING_STRIKE.id, "effects_per_use_upgrade", cap,
        ) or cap)
    return cap


def validate_cunning_strike(
    features: Dnd2024ClassFeatureResolver, character: dict[str, Any],
    intent: Mapping[str, Any], *, target_size: str = "",
    carried_refs: Mapping[str, int] | None = None, speed: int = 0,
) -> str:
    """Reject a payload the character cannot legally pay for right now.

    ``target_size`` / ``carried_refs`` / ``speed`` are passed in as plain
    values so this module never imports the combat package: the caller
    owns the state, this function owns the rule.
    """

    submitted = intent.get(CUNNING_STRIKE_EFFECTS_KEY)
    declared = bool(declaration_value(intent, CUNNING_STRIKE))
    if submitted is None:
        return ""
    if not declared:
        # Effects without the declaration are a forged submission: ignoring
        # them would let a client believe a condition was coming while the
        # engine quietly paid nothing.
        return "cunning strike effects require a cunning strike declaration"
    if not isinstance(submitted, list) or not submitted:
        return "cunning_strike_effects must be a non-empty array of effect ids"
    if not features.has_feature(character, CUNNING_STRIKE.id):
        return "Cunning Strike is not available to this actor"
    if any(not isinstance(value, str) for value in submitted):
        return "cunning_strike_effects must contain effect ids"
    if len(set(submitted)) != len(submitted):
        return "cunning_strike_effects cannot repeat an effect"
    options = {effect.id: effect for effect in cunning_strike_options(features, character)}
    chosen: list[CunningStrikeEffect] = []
    for effect_id in submitted:
        effect = options.get(effect_id)
        if effect is None:
            return f"{effect_id} is not a Cunning Strike option for this character"
        chosen.append(effect)
    if len(chosen) > cunning_strike_capacity(features, character):
        return "too many Cunning Strike effects for one Sneak Attack"
    pool = sneak_attack_dice_count(features, character)
    if sum(effect.dice_cost for effect in chosen) > pool:
        return "not enough Sneak Attack dice for the declared effects"
    carried = carried_refs or {}
    for effect in chosen:
        if effect.requires_item and int(carried.get(f"item:{effect.requires_item}", 0) or 0) < 1:
            return f"{effect.id} requires {effect.requires_item.replace('_', ' ')}"
        if effect.max_target_size and not _size_at_most(target_size, effect.max_target_size):
            return f"{effect.id} works only on a {effect.max_target_size} creature or smaller"
        if effect.speed_divisor and speed // effect.speed_divisor <= 0:
            return f"{effect.id} needs a Speed of at least {effect.speed_divisor} feet"
    return ""


def sneak_attack_dice_count(
    features: Dnd2024ClassFeatureResolver, character: dict[str, Any],
) -> int:
    formula = features.feature_value(character, "sneak_attack", "damage_dice", "")
    match = _SNEAK_DICE_RE.fullmatch(str(formula or ""))
    return int(match.group(1)) if match else 0


def _size_at_most(size: str, limit: str) -> bool:
    """Delegates to the combat package's one size ordering.

    Imported lazily: ``combat`` imports this module at load time, so a
    top-level import would close the cycle.  A second copy of the ordering
    here would be worse -- two places to edit when the size list changes.
    """
    from src.rulesets.dnd2024.combat.primitives import size_at_most

    return size_at_most(size, limit)


CUNNING_STRIKE = AttackDeclaration(
    "cunning_strike", "cunning_strike", ("attack",),
    plan_condition=_cunning_strike_condition,
)
#: 14th-level gate for the last three effects. Declared here so the effect
#: reader has one name for it, but deliberately absent from
#: ``ATTACK_DECLARATIONS``: it is not something a client may declare.
DEVIOUS_STRIKES = AttackDeclaration("devious_strikes", "devious_strikes")
CUNNING_STRIKE_EFFECTS_KEY = "cunning_strike_effects"

HURL = AttackDeclaration(
    "hurl_through_hell", "hurl_through_hell", ("attack", "cast_spell"),
    on_hit=_hurl, validate_use=_validate_hurl,
)
QUIVERING_PALM = AttackDeclaration("quivering_palm", "quivering_palm", plan_condition=_quivering_palm_condition)
STUNNING_STRIKE = AttackDeclaration(
    "stunning_strike", "stunning_strike", ("attack", "class_capability"), plan_condition=_stunning_strike_condition,
)
BRUTAL_STRIKE = AttackDeclaration("brutal_strike", "brutal_strike")
SACRED_WEAPON = AttackDeclaration("sacred_weapon", "activate_sacred_weapon")
LIGHT_EXTRA_ATTACK = AttackDeclaration("light_extra_attack", "light_extra_attack")

ATTACK_DECLARATIONS = {
    row.id: row
    for row in (
        HURL, QUIVERING_PALM, STUNNING_STRIKE, BRUTAL_STRIKE, SACRED_WEAPON,
        LIGHT_EXTRA_ATTACK, CUNNING_STRIKE,
    )
}
SELECTION_KEY = "attack_declarations"
HELD_HIT_KEY = "attack_hit_triggers"


def selection_error(intent: Mapping[str, Any], surface: str) -> str:
    if HELD_HIT_KEY in intent:
        return "attack_hit_triggers is server-owned hit metadata"
    if SELECTION_KEY not in intent:
        return ""
    if any(row.legacy_flag in intent for row in ATTACK_DECLARATIONS.values()):
        return "attack_declarations cannot be mixed with legacy flags"
    raw = intent[SELECTION_KEY]
    if not isinstance(raw, list):
        return "attack_declarations must be an array of declaration ids"
    seen: set[str] = set()
    for declaration_id in raw:
        if not isinstance(declaration_id, str) or declaration_id not in ATTACK_DECLARATIONS:
            return "attack_declarations contains an unknown declaration id"
        if declaration_id in seen:
            return "attack_declarations contains a duplicate declaration id"
        if surface not in ATTACK_DECLARATIONS[declaration_id].surfaces:
            return f"the {declaration_id} declaration is not supported for {surface}"
        seen.add(declaration_id)
    if {SACRED_WEAPON.id, LIGHT_EXTRA_ATTACK.id} <= seen:
        return "sacred weapon requires the Attack action, not a Light bonus attack"
    return ""


def declaration_value(intent: Mapping[str, Any], declaration: AttackDeclaration) -> Any:
    """Read an opt-in, preserving old flag types for existing diagnostics."""
    if SELECTION_KEY not in intent:
        return intent.get(declaration.legacy_flag)
    raw = intent[SELECTION_KEY]
    return True if isinstance(raw, list) and declaration.id in raw else None


def validate_uses(
    features: Dnd2024ClassFeatureResolver, character: dict[str, Any],
    intent: Mapping[str, Any], surface: str, *, attack_roll: bool = True,
) -> str:
    for row in ATTACK_DECLARATIONS.values():
        if surface not in row.surfaces or not declaration_value(intent, row):
            continue
        if not attack_roll:
            # Legacy spell flags were ignored on casts without attack rolls.
            if SELECTION_KEY in intent:
                return f"the {row.id} declaration requires a spell attack roll"
            continue
        if row.validate_use is not None:
            error = row.validate_use(features, character)
            if error:
                return error
    return ""


def _quivering_palm_focus_cost(features: Dnd2024ClassFeatureResolver, character: dict[str, Any]) -> int:
    return int(features.feature_value(character, QUIVERING_PALM.id, "focus_cost", 4) or 4)


def validate_weapon_selection(
    features: Dnd2024ClassFeatureResolver, character: dict[str, Any],
    intent: Mapping[str, Any], weapon: Mapping[str, Any], now_minutes: int,
) -> str:
    # The old Quivering Palm flag was a best-effort opt-in. Do not reinterpret
    # old intents, but require the canonical declaration to prove its use.
    if SELECTION_KEY not in intent or not declaration_value(intent, QUIVERING_PALM):
        return ""
    if not features.has_feature(character, QUIVERING_PALM.id):
        return "Quivering Palm is not available to this actor"
    if not weapon.get("unarmed"):
        return "Quivering Palm requires an Unarmed Strike"
    if long_effect_active(character, QUIVERING_PALM.id, now_minutes) is not None:
        return "Quivering Palm already has an active vibration"
    cost = _quivering_palm_focus_cost(features, character)
    if declaration_value(intent, STUNNING_STRIKE):
        cost += int(features.feature_value(character, STUNNING_STRIKE.id, "focus_cost", 1) or 1)
    if int(resource_state(character, "focus_points").get("current", 0) or 0) < cost:
        return "not enough Focus Points for the declared strike effects"
    return ""


def granted_strike_selection(intent: Mapping[str, Any], *, already_used: bool = False) -> dict[str, Any]:
    if already_used:
        return {}
    if SELECTION_KEY in intent:
        return {SELECTION_KEY: [STUNNING_STRIKE.id]} if declaration_value(intent, STUNNING_STRIKE) else {}
    flag = STUNNING_STRIKE.legacy_flag
    return {flag: intent[flag]} if flag in intent else {}


def plan_hit_conditions(intent: Mapping[str, Any], context: HitConditionContext) -> list[dict[str, Any]]:
    conditions = []
    # Rule order is independent of the order of the client's choices.
    for row in ATTACK_DECLARATIONS.values():
        if row.plan_condition is not None and declaration_value(intent, row):
            condition = row.plan_condition(context)
            if condition is not None:
                conditions.append(condition)
    return conditions


def held_hit_declarations(intent: Mapping[str, Any]) -> dict[str, bool]:
    # Preserve the existing serialized map for pending decisions and replays.
    return {
        row.id: True for row in ATTACK_DECLARATIONS.values()
        if row.on_hit is not None and declaration_value(intent, row)
    }


def committed_hit_events(
    operations: HitOperations, instance: Any, combat: dict[str, Any],
    attacker: dict[str, Any], target: dict[str, Any], held: Mapping[str, Any], rng: Any,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for declaration_id, row in ATTACK_DECLARATIONS.items():
        if held.get(declaration_id) is True and row.on_hit is not None:
            events.extend(row.on_hit(operations, instance, combat, attacker, target, rng))
    return events
