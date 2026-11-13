from __future__ import annotations

import json
from pathlib import Path

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.remote_adapter import RemoteRulesetRuntimeAdapter


def test_clean_external_runtime_fixture_covers_builder_action_projection_and_restart(tmp_path: Path):
    """A rights-clean sample exercises the external seam without importing FU code."""

    calls: list[str] = []

    def transport(request):
        operation = request["operation"]
        calls.append(operation)
        payload = request["payload"]
        if operation == "describe_experience":
            result = {"id": "sample-external", "locale": payload["locale"]}
        elif operation == "builder_choices":
            result = {"fields": [{"id": "name", "type": "string"}]}
        elif operation == "validate_character":
            result = [] if payload["draft"].get("name") else ["name is required"]
        elif operation == "derive_character":
            result = {**payload["draft"], "derived": {"score": 3}}
        elif operation == "finalize_character":
            result = {**payload["draft"], "finalized": True}
        elif operation == "gameplay_view":
            result = {
                "public": {"version": payload["instance"]["modules"]["ruleset_runtime"]["state"].get("version", 0)},
                **({"private": "gm-secret"} if payload["viewer_is_gm"] else {}),
            }
        elif operation == "prepare_intent_submission":
            result = payload["intent"]
        elif operation == "resolve_intent":
            version = int(payload["instance"]["modules"]["ruleset_runtime"]["state"].get("version", 0))
            intent = payload["intent"]
            result = {"ok": True, "event_batch": {
                "batch_id": f"sample-{version + 1}",
                "intent_id": intent["intent_id"],
                "expected_version": version,
                "result_version": version + 1,
                "events": [{"type": "sample.accepted", "version": version + 1}],
            }}
        elif operation == "apply_event_batch":
            instance = payload["instance"]
            batch = payload["batch"]
            current = instance["modules"]["ruleset_runtime"]["state"]
            ledger = instance["modules"]["ruleset_runtime"]["event_ledger"]
            if any(row.get("batch_id") == batch["batch_id"] for row in ledger):
                result = {"ok": True, "applied": False, "replayed": True,
                          "state": current, "event_ledger": ledger}
            else:
                result = {"ok": True, "applied": True,
                          "state": {**current, "version": batch["result_version"]},
                          "event_ledger": [*ledger, batch]}
        elif operation == "memory_deltas_from_event_batch":
            result = []
        else:
            raise AssertionError(operation)
        return {"request_id": request["request_id"], "ok": True, "result": result}

    ledger_path = tmp_path / "sample-receipts.json"
    runtime = RemoteRulesetRuntimeAdapter(
        "external:sample", 1,
        RulesetCapabilities(
            character_builder="professional",
            authoritative_intents=True,
            versioned_state=True,
        ),
        transport,
        ledger_path=ledger_path,
    )
    assert runtime.describe_experience({}, "en")["id"] == "sample-external"
    assert runtime.builder_choices({}, {})["fields"][0]["id"] == "name"
    assert runtime.validate_character({}, {"name": "Ada"}) == []
    assert runtime.finalize_character({}, {"name": "Ada"})["finalized"] is True

    instance = GameInstance(game_key=("test", "external", "sample"), run_id="sample-run")
    ruleset_runtime.bind(instance, {
        "id": "external:sample", "version": 1, "rule_id": "sample",
        "content_version": "1", "state_schema_version": 1,
    })
    first = runtime.resolve_intent(instance, {"intent_id": "sample-intent", "type": "advance"}, type("Rng", (), {"getstate": lambda self: ("s", 1)})())
    applied = runtime.apply_event_batch(instance, first["event_batch"])
    assert applied["applied"] is True
    assert ruleset_runtime.state(instance)["version"] == 1
    assert "private" not in runtime.gameplay_view(instance, "player", False)
    assert runtime.gameplay_view(instance, "gm", True)["private"] == "gm-secret"

    before_replay = len(calls)
    replay = runtime.apply_event_batch(instance, first["event_batch"])
    assert replay["applied"] is True
    assert len(calls) == before_replay + 0  # durable receipt avoids a second transport call
    assert json.loads(ledger_path.read_text(encoding="utf-8"))
