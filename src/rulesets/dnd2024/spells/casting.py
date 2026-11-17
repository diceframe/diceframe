"""Shared prepared/granted spells and free-cast resource authority.

Combat owns action economy; exploration does not. Both consume this same
projection, source priority, cost events and canonical resource settlement.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver

from .feat_spells import feat_spell_grants, spend_feat_free_cast
from .font_of_magic import font_slots


def spellcasting_projection(
    character: dict[str, Any],
    features: Dnd2024ClassFeatureResolver,
    feature_projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    projected = (
        feature_projection
        if feature_projection is not None
        else features.actor_projection(character)
    )
    block = (character.get("spellcasting") or {}).get("class") or {}
    class_refs = list(
        dict.fromkeys(
            [
                *list(block.get("cantrip_refs") or []),
                *list(block.get("prepared_spell_refs") or []),
            ]
        )
    )
    grants = [*projected["spell_grants"], *feat_spell_grants(character)]
    slots = features.free_cast_slots(character)
    return {
        "font_slots": font_slots(character),
        "class_spell_refs": class_refs,
        "spell_refs": list(
            dict.fromkeys(
                [
                    *class_refs,
                    *[row["spell_ref"] for row in grants],
                    *[row["spell_ref"] for row in slots if row["spell_ref"]],
                ]
            )
        ),
        "spell_grants": deepcopy(grants),
        "free_cast_slots": deepcopy(slots),
        "class_resources": deepcopy(projected["class_resources"]),
    }


def spell_caster(actor: dict[str, Any], spell_ref: str) -> dict[str, Any]:
    """Class-known spells use class ability; feat-only spells use their grant."""
    if spell_ref in actor.get("class_spell_refs", []):
        return actor
    grant = next(
        (
            row
            for row in actor.get("spell_grants", [])
            if row.get("spell_ref") == spell_ref and row.get("source") == "feat"
        ),
        None,
    )
    if grant is None:
        return actor
    return {
        **actor,
        "spell_ability": grant["spell_ability"],
        "spell_attack_bonus": int(grant["spell_attack_bonus"]),
        "spell_save_dc": int(grant["spell_save_dc"]),
    }


def free_cast_resource(
    actor: dict[str, Any],
    spell_ref: str,
    spell_level: int | None = None,
) -> dict[str, Any] | None:
    """Choose at most one source; unlimited slots win over finite sources.

    Free casting never upcasts. A free slot must match the spell's own level.
    This preserves combat's source order: unlimited, feat, limited slot, pool.
    """
    slots = [
        row
        for row in actor.get("free_cast_slots", []) or []
        if row.get("valid", True)
        and row.get("spell_ref") == spell_ref
        and (
            spell_level is None
            or int(row.get("spell_level", 0) or 0) == int(spell_level)
        )
    ]
    unlimited = next((row for row in slots if row.get("recharge") == "unlimited"), None)
    if unlimited is not None:
        return {
            "kind": "free_slot",
            "free_slot_id": unlimited["slot_id"],
            "spell_level": int(unlimited["spell_level"]),
            "unlimited": True,
        }
    grants = [
        row
        for row in actor.get("spell_grants", [])
        if row.get("spell_ref") == spell_ref
    ]
    feat_grant = next((row for row in grants if row.get("free_cast_available")), None)
    if feat_grant is not None:
        return {"kind": "feat", "feat_ref": feat_grant["feat_ref"]}
    limited = next(
        (
            row
            for row in slots
            if row.get("uses_remaining") is None or int(row["uses_remaining"]) > 0
        ),
        None,
    )
    if limited is not None:
        return {
            "kind": "free_slot",
            "free_slot_id": limited["slot_id"],
            "spell_level": int(limited["spell_level"]),
        }
    grant = next((row for row in grants if row.get("resource_id")), None)
    if grant is None:
        return None
    return next(
        (
            row
            for row in actor.get("class_resources", [])
            if row.get("id") == grant["resource_id"] and int(row.get("current", 0)) > 0
        ),
        None,
    )


def spell_cost_events(
    actor: dict[str, Any], spell: dict[str, Any], slot_level: int
) -> list[dict[str, Any]]:
    """Resource-only cost; action/reaction accounting belongs to combat."""
    if int(spell["level"]) == 0:
        return []
    actor_id = actor["actor_id"]
    if slot_level > 0:
        # Created slots are real paid slots, never a free-cast source. Prefer
        # them at the requested level, emitting exactly one resource spend.
        if int((actor.get("font_slots") or {}).get(str(slot_level), 0) or 0) > 0:
            return [
                {
                    "type": "dnd2024.spell.font_slot_spent",
                    "actor_id": actor_id,
                    "slot_level": slot_level,
                    "amount": 1,
                    "source": "font_of_magic",
                }
            ]
        return [
            {
                "type": "dnd2024.spell.slot_spent",
                "actor_id": actor_id,
                "slot_level": slot_level,
                "amount": 1,
            }
        ]
    source = free_cast_resource(actor, f"spell:{spell['id']}", int(spell["level"]))
    if source is None:
        raise ValueError("the free spell cast is not available")
    if source.get("kind") == "feat":
        return [
            {
                "type": "dnd2024.feat_spell.free_cast_spent",
                "actor_id": actor_id,
                "feat_ref": source["feat_ref"],
            }
        ]
    if source.get("kind") == "free_slot":
        if source.get("unlimited"):
            return []
        return [
            {
                "type": "dnd2024.free_cast.spent",
                "actor_id": actor_id,
                "free_slot_id": source["free_slot_id"],
                "spell_ref": f"spell:{spell['id']}",
                "spell_level": int(source["spell_level"]),
            }
        ]
    return [
        {
            "type": "dnd2024.class_resource.spent",
            "actor_id": actor_id,
            "resource_id": source["id"],
            "amount": 1,
        }
    ]


def apply_free_cast_cost(character: dict[str, Any], event: dict[str, Any]) -> None:
    """Apply one canonical free-source spend to a transaction snapshot.

    Callers validate the actor and translate ValueError to their batch error.
    No second resource store is introduced for exploration.
    """
    event_type = event["type"]
    if event_type == "dnd2024.feat_spell.free_cast_spent":
        spend_feat_free_cast(character, str(event["feat_ref"]))
        return
    if event_type == "dnd2024.class_resource.spent":
        resource_id = str(event["resource_id"])
        state = (character.get("resources") or {}).get("class", {}).get(resource_id)
        if not isinstance(state, dict):
            raise ValueError(f"class resource is not initialized: {resource_id}")
        current = int(state.get("current", 0) or 0)
        amount = int(event.get("amount", 1) or 1)
        if amount < 1 or current < amount:
            raise ValueError(f"class resource is already spent: {resource_id}")
        state["current"] = current - amount
        return
    if event_type != "dnd2024.free_cast.spent":
        raise ValueError("unsupported free cast cost")
    block = (character.get("spellcasting") or {}).get("class")
    stored = block.get("free_cast_slots") if isinstance(block, dict) else None
    slot_id = str(event["free_slot_id"])
    record = stored.get(slot_id) if isinstance(stored, dict) else None
    if not isinstance(record, dict):
        raise ValueError(f"free cast slot is not initialized: {slot_id}")
    if str(record.get("spell_ref") or "") != str(event.get("spell_ref") or ""):
        raise ValueError(f"free cast slot holds another spell: {slot_id}")
    remaining = record.get("uses_remaining")
    if not isinstance(remaining, int) or isinstance(remaining, bool):
        raise ValueError(f"free cast slot is not spent-limited: {slot_id}")
    if remaining < 1:
        raise ValueError(f"free cast slot is already spent: {slot_id}")
    record["uses_remaining"] = remaining - 1
