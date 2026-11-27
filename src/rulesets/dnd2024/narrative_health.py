"""Bridge narrative HP to the same canonical state used by combat and rest."""
from copy import deepcopy

from src.engine.health import record_health_event
from src.rulesets.dnd2024.character.health_display import sync_health_display
from src.rulesets.events import stable_batch_id


def settle_narrative_hp(engine, instance, user_id: str, delta: int) -> int:
    state = engine.initialize_state(instance)
    canonical = instance.get_character_sheet(user_id).get("ruleset_character")
    if not isinstance(canonical, dict):
        raise ValueError("D&D character has no canonical health state")
    before = int(canonical["resources"].get("hp", 0) or 0)
    conditions = canonical.get("conditions") or {}
    dead = "dead" in conditions
    active = (state.get("combat") or {}).get("status") == "active"
    if dead or active:
        if delta > 0 and dead:
            zh = str(instance.language or "").startswith("zh")
            record_health_event(
                instance, "ruleset_health", "RESURRECTION_REQUIRED", "warning",
                "死亡角色需要复活" if zh else "A dead character requires resurrection",
                "普通治疗和长休不能复活死亡角色；请执行复活操作。" if zh else
                "Ordinary healing and rest cannot resurrect the dead. Use an explicit revival operation.",
            )
        delta = 0
    if delta:
        expected = int(state.get("version", 0) or 0)
        intent = {"intent_id": f"narrative-hp:{expected}:{user_id}", "type": "character.narrative_hp", "expected_version": expected}
        result = engine.apply_batch(instance, {
            "batch_id": stable_batch_id(intent, expected), "intent_id": intent["intent_id"],
            "intent_type": intent["type"], "expected_version": expected, "result_version": expected + 1,
            "events": [{"type": "resource.changed", "resource": "hp", "target_id": f"player:{user_id}", "delta": int(delta), "healing": delta > 0}],
            "source_ref": "srd-5.2.1:p24-p27:playing-the-game",
        })
        if not result.get("ok"):
            raise ValueError(str(result.get("error") or "unable to settle HP"))
        if result.get("applied"):
            updated = instance.get_character_sheet(user_id)
            revision = int(updated.get("ruleset_revision", 0) or 0) + 1
            operations = updated.get("ruleset_operation_log")
            operations = deepcopy(operations) if isinstance(operations, list) else []
            operations.append({
                "operation_id": result["event_batch"]["batch_id"], "kind": "event_batch",
                "intent_type": intent["type"], "revision": revision,
            })
            updated["ruleset_revision"] = revision
            updated["ruleset_operation_log"] = operations[-32:]
    sheet = instance.get_character_sheet(user_id)
    canonical = sheet["ruleset_character"]
    after = int(canonical["resources"].get("hp", 0) or 0)
    sync_health_display(sheet, canonical)
    instance.set_character_sheet(user_id, sheet)
    return after - before
