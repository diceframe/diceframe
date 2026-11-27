"""What a *bound* invocation does at resolution time.

Every question here is the same shape -- "does this character own that option,
and is it bound to *this* spell" -- so the answers live together instead of
being spelled out again at each call site.  Nothing in this module writes state
or rolls dice: it returns numbers the resolution path puts into events, which is
what keeps replay deterministic.

The one exception is Repelling Blast, whose distance is the player's choice; the
caller passes the declared value and this module answers whether it is legal.
"""

from __future__ import annotations

from typing import Any, Mapping

from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.combat.primitives import size_at_most

WARLOCK = "class:warlock"


def _class_level(features: Any, character: Mapping[str, Any] | None) -> int:
    """The Warlock level, or 0 when this is not a Warlock character.

    ``class_state`` deliberately answers ``None`` for a multiclass card, and a
    level-scaled bonus that guessed would be worse than not applying: a
    Ranger/Warlock would get the Warlock's whole class level attached to the
    wrong number.
    """

    state = features.class_state(character)
    if state is None:
        return 0
    class_id, level = state
    return int(level) if f"class:{class_id}" == WARLOCK else 0


def _carrier(
    features: Any, character: Mapping[str, Any] | None, key: str,
) -> Any:
    """The owned feature that declares ``key``, or ``None``.

    Found from the declaration, never by name: combat must not know that "the
    option adding Charisma to a cantrip" is called Agonizing Blast, or the next
    option of that shape needs an engine edit -- the coupling guard (GAP-43)
    exists exactly to stop that.
    """

    for definition in features.features_for(character):
        if key in definition.parameters:
            return definition
    return None


def _bound_to(
    features: Any, character: Mapping[str, Any] | None, definition: Any,
    spell_ref: str,
) -> bool:
    return bool(features.owns_binding(character, definition.id, spell_ref))


def agonizing_bonus(
    features: Any, character: Mapping[str, Any] | None,
    actor: Mapping[str, Any], spell: Mapping[str, Any], spell_ref: str,
) -> int:
    """The modifier added to every damage roll of the bound cantrip.

    Per roll, not per casting: a three-beam Eldritch Blast adds it three times,
    which falls out of adding it to the damage node before each beam rolls.
    """

    if int(spell.get("level", 0) or 0) != 0:
        return 0
    definition = _carrier(features, character, "invocation_damage_bonus_ability")
    if definition is None or not _bound_to(features, character, definition, spell_ref):
        return 0
    ability = str(definition.parameters.get("invocation_damage_bonus_ability") or "")
    if not ability:
        return 0
    return ability_modifier((actor.get("abilities") or {}).get(ability, 10))


def spear_range_bonus(
    features: Any, character: Mapping[str, Any] | None,
    spell_ref: str, base_range: int,
) -> int:
    """``30 x class level`` feet on the bound cantrip, 0 when it does not apply.

    ``invocation_cantrip_min_range_ft`` is re-checked here: eligibility was
    proved when the pick was made, but a cantrip's own range can change (a spell
    edit, a legacy card), and an option bound to something that no longer
    qualifies must not silently extend a Touch spell.
    """

    definition = _carrier(features, character, "invocation_range_bonus_per_level_ft")
    if definition is None or not _bound_to(features, character, definition, spell_ref):
        return 0
    minimum = int(definition.parameters.get("invocation_cantrip_min_range_ft", 0) or 0)
    if minimum and int(base_range) < minimum:
        return 0
    per_level = int(
        definition.parameters.get("invocation_range_bonus_per_level_ft") or 0
    )
    if not per_level:
        return 0
    return per_level * _class_level(features, character)


def concentration_advantage(
    features: Any, character: Mapping[str, Any] | None,
) -> str:
    """The option granting Advantage on the concentration save, or ``""``.

    Returns the id rather than a bool so the event records *which* option did
    it: the source label belongs to the declaration, and a second such option
    must not be reported as the first one.
    """

    definition = _carrier(features, character, "invocation_concentration_advantage")
    if definition is None:
        return ""
    return definition.id if definition.parameters.get(
        "invocation_concentration_advantage",
    ) else ""


def repelling_push_ft(
    features: Any, character: Mapping[str, Any] | None,
    spell_ref: str, declared_ft: Any, target_size: str,
) -> tuple[str, int]:
    """``(option id, legal distance)``; ``("", 0)`` when the choice is illegal.

    ``declared_ft`` is the player's 0-10; anything else (missing, negative, over
    the cap, or present when the option is not bound to this cantrip) is refused
    rather than clamped -- clamping would move a creature the player never asked
    to move.
    """

    if declared_ft is None:
        return "", 0
    if isinstance(declared_ft, bool) or not isinstance(declared_ft, int):
        return "", 0
    definition = _carrier(features, character, "invocation_push_distance_ft")
    if definition is None or not _bound_to(features, character, definition, spell_ref):
        return "", 0
    cap = int(definition.parameters.get("invocation_push_distance_ft", 0) or 0)
    if declared_ft < 0 or declared_ft > cap:
        return "", 0
    limit = str(definition.parameters.get("invocation_push_size_limit") or "")
    if limit and not size_at_most(target_size, limit):
        # 逐命中判定：多目标施法里一个过大，不该把整次施法判死——规则说的是
        # "命中 Large 或更小的生物时可以推它"。
        return "", 0
    return definition.id, declared_ft


def temp_hp_maximized(
    features: Any, character: Mapping[str, Any] | None,
    free_cast_rows: list[Mapping[str, Any]], slot_level: int, spell_ref: str,
    paid_slot_id: str,
) -> bool:
    """Fiendish Vigor: the False Life cast through its own slot takes max dice.

    Identified from the free-cast row that matches this spell, not from the
    feature name: the row already says which feature granted the slot, so a
    second carrier of the same flag needs no engine change.
    """

    if int(slot_level or 0) != 0 or not paid_slot_id:
        return False
    for row in free_cast_rows:
        if str(row.get("spell_ref") or "") != spell_ref:
            continue
        # 只认**这次真正支付**的那条槽：角色同时有两个无限次 False Life 来源时，
        # "拥有其中一个"不能推出"用的是这一个"。
        if str(row.get("slot_id") or "") != paid_slot_id:
            continue
        feature_id = str(row.get("feature_id") or "")
        if not feature_id or not features.has_feature(character, feature_id):
            continue
        if features.feature_value(
            character, feature_id, "invocation_free_cast_maximized", False,
        ):
            return True
    return False
