"""D&D attack-mode authority shared by pre-roll projection and resolution."""
from __future__ import annotations
from src.engine.modules import ruleset_runtime

from typing import Any

from src.engine.modules.check_modes import scoped


def context_id(engine: Any, instance: Any) -> str:
    combat = ruleset_runtime.state(instance).get("combat", {})
    order = combat.get("initiative", [])
    index = combat.get("turn_index", -1)
    current = order[index] if type(index) is int and 0 <= index < len(order) else ""
    return f"combat:{int(ruleset_runtime.state(instance).get('version', 0))}:{current}"


def condition_flags(
    registry: dict[str, dict[str, Any]], conditions: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate the registry-declared mechanical flags a condition set carries.

    Incapacitation and speed-zero gate intents, the auto-fail list feeds saving
    throws, and ``crit_on_hit_within_5`` upgrades an already-hit attack. Any
    single source turns a boolean on; the save list is the union of every
    source. The whole incapacitated family (paralyzed, stunned, unconscious,
    and sleep's plain ``incapacitated``) therefore works without engine
    branches naming each condition.
    """

    incapacitated = False
    speed_zero = False
    auto_fail_saves: set[str] = set()
    crit_on_hit_within_5 = False
    for condition_id, entry in registry.items():
        if condition_id not in conditions:
            continue
        incapacitated = incapacitated or bool(entry.get("implies_incapacitated"))
        speed_zero = speed_zero or bool(entry.get("speed_zero"))
        crit_on_hit_within_5 = crit_on_hit_within_5 or bool(
            entry.get("crit_on_hit_within_5")
        )
        auto_fail_saves.update(
            str(ability) for ability in entry.get("auto_fail_saves") or []
        )
    return {
        "incapacitated": incapacitated,
        "speed_zero": speed_zero,
        "auto_fail_saves": auto_fail_saves,
        "crit_on_hit_within_5": crit_on_hit_within_5,
    }


def effective_speed(
    registry: dict[str, dict[str, Any]], conditions: dict[str, Any], base_speed: int,
) -> int:
    """回合开始的速度上限：speed_zero 压倒一切修正；否则叠加全部 speed_modifier，向下取整于 0。"""

    if condition_flags(registry, conditions)["speed_zero"]:
        return 0
    total = 0
    for condition_id, entry in registry.items():
        if condition_id in conditions:
            total += int(entry.get("speed_modifier", 0) or 0)
    return max(0, int(base_speed) + total)


def attack_sources(
    registry: dict[str, dict], actor: dict, target: dict, range_disadvantage: bool,
) -> list[dict]:
    """Collect the registry-declared attack modifiers for this attack roll.

    A condition declares whether it matters when it sits on the attacker
    (``side == "actor"``) or on the target (``side == "target"``). ``negated_by``
    is checked against the *observer* -- the attacker for target-side
    declarations, the target for actor-side ones -- so a creature that can see
    through the trick does not grant/benefit from it.

    ``long_range`` stays code-generated: it is a positional fact of the attack,
    not a condition.
    """

    sources = []
    for condition_id, entry in registry.items():
        for declaration in entry.get("attack_modes") or []:
            on_actor = declaration["side"] == "actor"
            holder = actor if on_actor else target
            observer = target if on_actor else actor
            if condition_id not in holder["conditions"]:
                continue
            if any(code in observer["conditions"] for code in declaration.get("negated_by") or []):
                continue
            sources.append({"code": condition_id, "mode": declaration["mode"]})
    if range_disadvantage:
        sources.append({"code": "long_range", "mode": "disadvantage"})
    return sources


def mode_from_sources(sources: list[dict]) -> str:
    modes = {row["mode"] for row in sources}
    return next(iter(modes)) if len(modes) == 1 else ""


def attack_mode(engine: Any, instance: Any, actor: dict, target: dict,
                range_disadvantage: bool, *, use_grant: bool = True) -> dict:
    sources = attack_sources(engine.catalog.conditions, actor, target, range_disadvantage)
    if use_grant and actor["kind"] == "player":
        grant = scoped(instance, actor["actor_id"].removeprefix("player:"), context_id(engine, instance)).get("grant")
        if grant:
            sources.append({"code": "gm_grant", "mode": grant["mode"], "reason": grant["reason"]})
    return {"mode": mode_from_sources(sources), "sources": sources}


def policy(engine: Any, instance: Any, uid: str) -> dict:
    combat = ruleset_runtime.state(instance).get("combat", {})
    scope = context_id(engine, instance)
    result = {"kind": "d20_keep_high_low", "scope": "combat_attack", "context_id": scope,
              "blocked_reason": "", "previews": []}
    if combat.get("status") != "active":
        result["blocked_reason"] = "combat_required"
        return result
    actor_id = f"player:{uid}"
    order = combat.get("initiative", [])
    index = combat.get("turn_index", -1)
    if type(index) is not int or not 0 <= index < len(order) or order[index] != actor_id:
        result["blocked_reason"] = "not_this_seat"
        return result
    actor = engine._actor_view(instance, combat, actor_id)
    for available in engine.available_intents(instance, uid):
        if available["type"] == "cast_spell":
            for spell in available.get("spells", []):
                if spell.get("mode") != "spell_attack":
                    continue
                for target in available.get("targets", []):
                    intent = {"intent_id": "check-mode-spell-preview", "submitted_by": instance.gm_uid,
                              "type": "cast_spell", "actor_id": actor_id, "target_ids": [target["actor_id"]],
                              "spell_ref": spell["spell_ref"], "slot_level": spell["available_slot_levels"][0],
                              "expected_version": int(ruleset_runtime.state(instance)["version"])}
                    if engine.validate_intent(instance, intent).get("ok"):
                        result["previews"].append({"spell_ref": spell["spell_ref"], "target_id": target["actor_id"],
                            "label": f"{spell['name']} → {target.get('name') or target['actor_id']}",
                            **attack_mode(engine, instance, actor, engine._actor_view(instance, combat, target["actor_id"]), False)})
        if available["type"] != "attack":
            continue
        for weapon in available.get("weapons", []):
            for target in available.get("targets", []):
                intent = {"intent_id": "check-mode-preview", "submitted_by": instance.gm_uid,
                          "type": "attack", "actor_id": actor_id, "target_id": target["actor_id"],
                          "weapon_ref": weapon["weapon_ref"], "expected_version": int(ruleset_runtime.state(instance)["version"])}
                if not engine.validate_intent(instance, intent).get("ok"):
                    continue
                profile = engine._declared_attack(actor, intent)
                distance = engine._distance(combat, actor_id, target["actor_id"])
                normal = int(profile.get("thrown_range") or profile["range"])
                maximum = int(profile.get("long_range") or profile.get("thrown_range") or profile["range"])
                result["previews"].append({"weapon_ref": weapon["weapon_ref"], "target_id": target["actor_id"],
                    "label": f"{weapon.get('name') or weapon['weapon_ref']} → {target.get('name') or target['actor_id']}",
                    **attack_mode(engine, instance, actor, engine._actor_view(instance, combat, target["actor_id"]),
                                  normal < distance <= maximum)})
    return result
