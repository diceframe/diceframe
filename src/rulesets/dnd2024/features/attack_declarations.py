"""Opt-in attack declarations, distinct from activations and reaction windows.

New intents select canonical IDs in ``attack_declarations``. The legacy flag
names are read here only; saved decisions and retries keep their original data.
A declaration changes when or how an attack is resolved. Feature ownership,
costs, target checks and effect rules still come from the authoritative runtime.
"""

from __future__ import annotations

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

    def parameter(self, feature_id: str, key: str) -> Any:
        return self.features.feature_value(self.character, feature_id, key, None)


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
    row.id: row for row in (HURL, QUIVERING_PALM, STUNNING_STRIKE, BRUTAL_STRIKE, SACRED_WEAPON, LIGHT_EXTRA_ATTACK)
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
