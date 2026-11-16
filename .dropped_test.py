async def test_remote_runtime_uses_gameplay_authority_for_two_submissions_and_persists_state():
    from src.engine.game_instance import GameInstance
    from src.engine.modules import ruleset_runtime
    from src.rules.rule_system import RuleSystem
    from src.rulesets.registry import RulesetRuntimeRegistry
    from src.webui.services import ruleset_gameplay
    from src.webui.services._common import _parse_game_key

    calls = []

    def transport(request):
        calls.append(request)
        operation = request["operation"]
        if operation == "prepare_intent_submission":
            return {"request_id": request["request_id"], "ok": True, "result": request["payload"]["intent"]}
        if operation == "available_intents":
            return {"request_id": request["request_id"], "ok": True, "result": []}
        if operation == "gameplay_view":
            return {"request_id": request["request_id"], "ok": True, "result": {}}
        if operation == "resolve_intent":
            version = int(request["payload"]["instance"]["modules"]["ruleset_runtime"]["state"].get("version", 0))
            return {"request_id": request["request_id"], "ok": True, "result": {
                "ok": True,
                "event_batch": {
                    "batch_id": f"remote-{version + 1}",
                    "intent_id": request["payload"]["intent"]["intent_id"],
                    "expected_version": version,
                    "result_version": version + 1,
                    "events": [{"type": "remote.accepted", "version": version + 1}],
                },
            }}
        if operation == "apply_event_batch":
            batch = request["payload"]["batch"]
            current = request["payload"]["instance"]["modules"]["ruleset_runtime"]["state"]
            ledger = request["payload"]["instance"]["modules"]["ruleset_runtime"]["event_ledger"]
            if any(row.get("batch_id") == batch["batch_id"] for row in ledger):
                return {"request_id": request["request_id"], "ok": True, "result": {"ok": True, "applied": False, "replayed": True, "state": current, "event_ledger": ledger}}
            next_state = {**current, "version": batch["result_version"]}
            return {"request_id": request["request_id"], "ok": True, "result": {
                "ok": True, "applied": True, "state": next_state,
                "event_ledger": [*ledger, batch],
            }}
        if operation == "memory_deltas_from_event_batch":
            return {"request_id": request["request_id"], "ok": True, "result": []}
        raise AssertionError(operation)

    capabilities = RulesetCapabilities(authoritative_intents=True, versioned_state=True)
    runtime = RemoteRulesetRuntimeAdapter("external:demo", 1, capabilities, transport)
    registry = RulesetRuntimeRegistry([runtime])
    instance = GameInstance(game_key=("test", "group", "remote-game"), run_id="run-remote")
    instance.players["one"] = {"user_id": "one", "character_sheet": {}}
    ruleset_runtime.bind(instance, {
        "id": "external:demo", "version": 1, "rule_id": "remote-rule",
        "content_version": "1", "state_schema_version": 1,
    })
    rule = RuleSystem({
        "rule_id": "remote-rule", "runtime": {"id": "external:demo", "minimum_version": 1},
    })
    saved = []
    async def save_instance(value):
        saved.append(value)

    dependencies = ruleset_gameplay.RulesetGameplayDependencies(
        get_instance=lambda key: instance if key == instance.game_key else None,
        parse_game_key=_parse_game_key,
        load_rule_for_game=lambda _instance: rule,
        ruleset_registry=registry,
        resolve_adventure_binding=lambda *args: {},
        save_instance=save_instance,
        apply_memory_delta=None,
        rng_factory=random.Random,
    )
    first = await ruleset_gameplay.submit_intent(
        dependencies, "|".join(instance.game_key), "one", False,
        {"type": "remote.action", "intent_id": "i-1"},
    )
    second = await ruleset_gameplay.submit_intent(
        dependencies, "|".join(instance.game_key), "one", False,
        {"type": "remote.action", "intent_id": "i-2"},
    )
    assert first["ok"] and second["ok"]
    assert ruleset_runtime.state(instance)["version"] == 2
    assert len(ruleset_runtime.event_ledger(instance)) == 2
    assert len(saved) == 2
    assert [request["operation"] for request in calls].count("apply_event_batch") == 2


@pytest.mark.asyncio
