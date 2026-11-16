"""PR754 remote-runtime RNG regression (not PR755's plugin registration bridge).

The host in this branch does not implement ruleset-runtime plugins or export a
runtime declaration parser. Exercise the existing adapter and real stdio through
the gameplay service instead of importing APIs from the dependent PR.
"""

import json
import random
import sys
from unittest.mock import AsyncMock

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.registry import RulesetRuntimeRegistry
from src.rulesets.remote_adapter import RemoteRulesetRuntimeAdapter
from src.rulesets.stdio_transport import StdioRuntimeTransport
from src.webui.services import ruleset_gameplay
from src.webui.services._common import _parse_game_key


@pytest.mark.asyncio
async def test_default_service_rng_reaches_real_stdio_and_retries_without_executing_twice(tmp_path):
    script = tmp_path / "runtime.py"
    calls = tmp_path / "resolved.jsonl"
    script.write_text('''import json, random, sys
from pathlib import Path

def as_tuple(value):
    return tuple(as_tuple(item) for item in value) if isinstance(value, list) else value

for line in sys.stdin:
    request = json.loads(line)
    payload = request["payload"]
    op = request["operation"]
    if op == "prepare_intent_submission":
        result = payload["intent"]
    elif op == "resolve_intent":
        rng = random.Random()
        rng.setstate(as_tuple(payload["rng"]["state"]))
        version = payload["instance"]["modules"]["ruleset_runtime"]["state"].get("version", 0)
        intent_id = payload["intent"]["intent_id"]
        with Path(sys.argv[1]).open("a", encoding="utf-8") as output:
            output.write(json.dumps({"intent_id": intent_id, "roll": rng.randint(1, 20)}) + "\\n")
        result = {"ok": True, "event_batch": {
            "batch_id": "batch-" + intent_id, "intent_id": intent_id,
            "expected_version": version, "result_version": version + 1, "events": [],
        }}
    elif op == "apply_event_batch":
        current = payload["instance"]["modules"]["ruleset_runtime"]
        batch = payload["batch"]
        ledger = current["event_ledger"]
        duplicate = any(row["batch_id"] == batch["batch_id"] for row in ledger)
        result = {"ok": True, "applied": not duplicate,
                  "state": current["state"] if duplicate else {"version": batch["result_version"]},
                  "event_ledger": ledger if duplicate else [*ledger, batch]}
    elif op in {"available_intents", "memory_deltas_from_event_batch"}:
        result = []
    else:
        result = {}
    print(json.dumps({"request_id": request["request_id"], "ok": True, "result": result}), flush=True)
''', encoding="utf-8")
    transport = StdioRuntimeTransport([sys.executable, "-u", str(script), str(calls)])
    runtime = RemoteRulesetRuntimeAdapter(
        "external:stdio", 1,
        RulesetCapabilities(authoritative_intents=True, versioned_state=True),
        transport, ledger_path=tmp_path / "receipts.json",
    )
    instance = GameInstance(game_key=("web", "rng-regression", "host"))
    instance.players["alice"] = {"character_sheet": {}}
    ruleset_runtime.bind(instance, {
        "id": "external:stdio", "version": 1, "rule_id": "sample",
        "content_version": "1", "state_schema_version": 1,
    })
    rule = RuleSystem({"rule_id": "sample", "runtime": {"id": "external:stdio", "minimum_version": 1}})
    dependencies = ruleset_gameplay.RulesetGameplayDependencies(
        get_instance=lambda key: instance if key == instance.game_key else None,
        parse_game_key=_parse_game_key,
        load_rule_for_game=lambda _instance: rule,
        ruleset_registry=RulesetRuntimeRegistry([runtime]),
        resolve_adventure_binding=lambda *args: {},
        save_instance=AsyncMock(), apply_memory_delta=None,
        # Deliberately omit rng_factory: production default must serialize.
    )
    try:
        assert isinstance(dependencies.rng_factory(), random.Random)
        for intent_id in ("first", "first", "second"):
            result = await ruleset_gameplay.submit_intent(
                dependencies, "|".join(instance.game_key), "alice", False,
                {"type": "sample.check", "intent_id": intent_id},
            )
            assert result["ok"], result
        assert ruleset_runtime.state(instance)["version"] == 2
        resolved = [json.loads(line) for line in calls.read_text().splitlines()]
        assert [row["intent_id"] for row in resolved] == ["first", "second"]
        assert all(1 <= row["roll"] <= 20 for row in resolved)
    finally:
        runtime.close()
    assert transport.process.poll() is not None
