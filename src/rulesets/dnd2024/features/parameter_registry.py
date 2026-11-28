"""The closed vocabulary of feat parameters, and the proof that each has a reader.

Feats used to validate their parameters against a two-key whitelist that also
pinned every numeric value to ``1..10``.  That was strict enough to reject real
declarations (a ``-1`` penalty, a ``"1d8"`` die, a list of abilities) and loose
enough to let ``armor_class_bonus`` remain the only passive effect in existence.

This module replaces that whitelist: one registry, one type vocabulary, and --
for every key that claims a consumer -- a ``(module, symbol)`` pair a test can
check on disk.  ``stage="unread"`` is the escape hatch for declarations that are
honestly not consumed yet; its count is ratcheted in
``tests/architecture/test_feat_declarations_have_readers.py``, so a new
parameter cannot join the "we will implement it later" pile without a test
failing.

The registry also answers "is this key's value the right shape?" without
knowing which feat declared it.  Bounds are per-key rather than a blanket
``1..10``: a penalty is a legitimate value, and a die is a string, so a single
range for everything would either reject both or accept neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

FEAT_PARAMETER_STAGES = frozenset({
    # Read while resolving a combat decision.
    "combat",
    # Read while deriving the character sheet (initiative, AC, speed, ...).
    # Read while planning a level-up or a rest.
    "progression",
    "derivation",
    # Read only to render a card; it changes nothing mechanically.
    "display",
    # Declared, deliberately not read yet.  Must be listed in GAP_REGISTER and
    # counted by the ratchet test.
    "unread",
})

FEAT_PARAMETER_TYPES = frozenset({
    "bool",
    "int",
    "str",
    # A closed set of lowercase identifiers, e.g. ``["str", "dex"]``.
    "str_list",
    # ``"1d8"`` / ``"2d6"`` -- the die itself, not a rolled value.
    "dice",
})


class FeatParameterError(ValueError):
    """Raised when a feat parameter is unknown or its value has the wrong shape."""


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """One key's type, bounds, and the evidence that something reads it.

    ``reader`` is ``(module path relative to ``src/``, symbol)``.  It is checked
    by text containment, not by import: the architecture guards in this repo are
    text scans, and making one guard use AST would make the guard itself the next
    thing to maintain.
    """

    value_type: str
    stage: str
    reader: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


PARAMETERS: Mapping[str, ParameterSpec] = {
    "ranged_attack_bonus": ParameterSpec(
        "int",
        stage="combat",
        reader=("rulesets/dnd2024/features/resolver.py", "ranged_attack_bonus"),
        minimum=-10,
        maximum=10,
    ),
    "light_extra_attack_ability_damage": ParameterSpec(
        "bool",
        stage="combat",
        reader=(
            "rulesets/dnd2024/features/resolver.py",
            "light_extra_attack_ability_damage",
        ),
    ),
    # Great Weapon Fighting: 1s and 2s on the weapon's own damage dice become 3.
    # The name carries the two conditions (two-handed melee) because the resolver
    # answers "does this character have it", while combat decides whether *this*
    # attack qualifies.
    "two_handed_melee_damage_die_minimum": ParameterSpec(
        "int",
        stage="combat",
        reader=(
            "rulesets/dnd2024/features/feat_parameters.py",
            "two_handed_melee_damage_die_minimum",
        ),
        minimum=2,
        maximum=12,
    ),
    # Savage Attacker: reroll the weapon's damage dice twice and keep the
    # higher total, once per turn.  The number is the per-turn budget; whether
    # this attack is a weapon hit and whether the budget is spent belong to the
    # combat layer.
    "weapon_damage_rerolls_per_turn": ParameterSpec(
        "int",
        stage="combat",
        reader=(
            "rulesets/dnd2024/features/feat_parameters.py",
            "weapon_damage_rerolls_per_turn",
        ),
        minimum=1,
        maximum=3,
    ),
    "initiative_proficiency_bonus": ParameterSpec(
        "str",
        stage="derivation",
        reader=("rulesets/dnd2024/character/derivation.py", "initiative_proficiency_bonus"),
        allowed=("proficiency",),
    ),
    # The spell list a feat casts from.  Who reads it is the feat-spell
    # projection; the value itself names a list in the bundle, which is checked
    # where the list is resolved rather than here (the registry has no bundle).
    "spell_list": ParameterSpec(
        "str",
        stage="derivation",
        reader=("rulesets/dnd2024/character/derivation.py", "spell_list"),
    ),
}


def validate_feat_parameters(feat_id: str, parameters: Any) -> dict[str, Any]:
    """Return the validated parameters, failing closed on anything unknown.

    The return value is a plain dict so callers can keep using it as a mapping;
    the validation is about shape, not about wrapping.
    """

    if parameters is None:
        return {}
    if not isinstance(parameters, Mapping):
        raise FeatParameterError(f"{feat_id}.parameters must be an object")
    result: dict[str, Any] = {}
    for raw_key, value in parameters.items():
        key = str(raw_key)
        spec = PARAMETERS.get(key)
        if spec is None:
            raise FeatParameterError(f"{feat_id} has an unknown parameter: {key}")
        result[key] = _validate_value(feat_id, key, spec, value)
    return result


def _validate_value(
    feat_id: str, key: str, spec: ParameterSpec, value: Any,
) -> Any:
    kind = spec.value_type
    if kind not in FEAT_PARAMETER_TYPES:
        # A registry entry with a typo would silently skip validation.
        raise FeatParameterError(f"{key} is registered with an unknown type: {kind!r}")
    if kind == "bool":
        if not isinstance(value, bool):
            raise FeatParameterError(f"{feat_id}.{key} must be a boolean")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise FeatParameterError(f"{feat_id}.{key} must be an integer")
        if spec.minimum is not None and value < spec.minimum:
            raise FeatParameterError(
                f"{feat_id}.{key} must be at least {spec.minimum}"
            )
        if spec.maximum is not None and value > spec.maximum:
            raise FeatParameterError(
                f"{feat_id}.{key} must be at most {spec.maximum}"
            )
        return value
    if kind == "str":
        if not isinstance(value, str) or not value:
            raise FeatParameterError(f"{feat_id}.{key} must be a non-empty string")
        if spec.allowed and value not in spec.allowed:
            raise FeatParameterError(
                f"{feat_id}.{key} must be one of {list(spec.allowed)}"
            )
        return value
    if kind == "str_list":
        if not isinstance(value, list) or not value:
            raise FeatParameterError(f"{feat_id}.{key} must be a non-empty array")
        items = []
        for item in value:
            if not isinstance(item, str) or not item:
                raise FeatParameterError(
                    f"{feat_id}.{key} must hold non-empty strings"
                )
            if spec.allowed and item not in spec.allowed:
                raise FeatParameterError(
                    f"{feat_id}.{key} must be one of {list(spec.allowed)}"
                )
            items.append(item)
        return items
    # dice
    if not isinstance(value, str) or "d" not in value:
        raise FeatParameterError(f"{feat_id}.{key} must be a die such as \"1d8\"")
    count, _, faces = value.partition("d")
    if not count.isdigit() or not faces.isdigit() or int(count) < 1 or int(faces) < 2:
        raise FeatParameterError(f"{feat_id}.{key} must be a die such as \"1d8\"")
    return value


def unread_parameters() -> tuple[str, ...]:
    """Every registered key that no consumer reads yet, sorted."""

    return tuple(sorted(k for k, spec in PARAMETERS.items() if spec.stage == "unread"))
