import asyncio
from copy import deepcopy
import pytest
from pathlib import Path
import random

from src.rulesets.contracts import (
    AutomaticIntentRuntime,
    RulesetCapabilities,
    VersionedStateRuntime,
)
from src.rulesets.registry import RulesetRuntimeRegistry
from src.rulesets.remote_adapter import RemoteRulesetRuntimeAdapter
from src.rulesets.runtime_probe import RuntimeProbeError


def test_remote_ruleset_adapter_forwards_dto_and_can_register():
    calls = []

    def transport(request):
        calls.append(request)
        operation = request["operation"]
        result = [] if operation == "validate_character" else {"operation": operation}
        return {"request_id": request["request_id"], "ok": True, "result": result}

    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(character_builder="professional"), transport,
    )
    registry = RulesetRuntimeRegistry([runtime])
    assert registry.get("external:demo").validate_character({}, {}) == []
    assert calls[0]["runtime_id"] == "external:demo"
    assert calls[0]["operation"] == "validate_character"


def test_remote_ruleset_adapter_fails_closed_on_bad_response():
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(), lambda _request: {"ok": True},
    )
    with pytest.raises(RuntimeProbeError, match="identity"):
        runtime.describe_experience({}, "en")


def test_remote_ruleset_adapter_requires_json_state_dtos():
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(),
        lambda request: {"request_id": request["request_id"], "ok": True, "result": []},
    )
    with pytest.raises(RuntimeProbeError, match="JSON DTO"):
        runtime.available_intents(object(), "actor")


def test_remote_ruleset_adapter_serializes_rng_state_without_exposing_rng_object():
    seen = []

    class Rng:
        def getstate(self):
            return ("state", 7)

    def transport(request):
        seen.append(request)
        return {"request_id": request["request_id"], "ok": True, "result": {}}

    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(), transport,
    )
    runtime.resolve_intent({}, {"type": "check"}, Rng())
    assert seen[0]["payload"]["rng"] == {"state": ["state", 7]}


def test_remote_ruleset_adapter_can_reuse_receipts_after_restart(tmp_path: Path):
    calls = []

    def transport(request):
        calls.append(request)
        return {"request_id": request["request_id"], "ok": True, "result": {"ok": True}}

    ledger = tmp_path / "remote-receipts.json"
    first = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(), transport, ledger_path=ledger,
        request_id_factory=lambda: "fixed-request",
    )
    first.describe_experience({}, "en")
    second = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(), transport, ledger_path=ledger,
        request_id_factory=lambda: "fixed-request",
    )
    second.describe_experience({}, "en")
    assert ledger.exists()
    assert len(calls) == 1


def test_remote_mutation_identity_replays_from_ledger_after_adapter_restart(tmp_path: Path):
    calls = []

    def transport(request):
        calls.append(request)
        return {
            "request_id": request["request_id"],
            "ok": True,
            "result": {"ok": True, "event_batch": {"batch_id": "b1"}},
        }

    ledger = tmp_path / "mutations.json"
    first = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(), transport, ledger_path=ledger,
    )
    # P1-01 复审：收据身份需要服务器权威的 (game_key, run_id)——空实例会被 fail closed。
    instance = {"game_key": ["web", "room-a", "alice"], "run_id": "run-a"}
    first.resolve_intent(instance, {"intent_id": "i1"}, type("Rng", (), {"getstate": lambda self: ("s", 1)})())
    second = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(),
        lambda _request: (_ for _ in ()).throw(AssertionError("ledger replay must avoid transport")),
        ledger_path=ledger,
    )
    replayed = second.resolve_intent(
        instance, {"intent_id": "i1"}, type("Rng", (), {"getstate": lambda self: ("s", 1)})(),
    )
    assert replayed["event_batch"]["batch_id"] == "b1"
    assert len(calls) == 1


def test_remote_ruleset_adapter_applies_only_opaque_state_and_ledger_dtos():
    from src.engine.game_instance import GameInstance
    from src.engine.modules import ruleset_runtime

    instance = GameInstance(game_key=("test", "group", "remote"), run_id="run-remote")
    ruleset_runtime.bind(instance, {
        "id": "external:demo", "version": 1, "rule_id": "demo",
        "content_version": "1", "state_schema_version": 1,
    })
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(),
        lambda request: {
            "request_id": request["request_id"],
            "ok": True,
            "result": {
                "ok": True,
                "applied": True,
                "state": {"version": 1, "opaque": {"hp": 9}},
                "event_ledger": [{"batch_id": "b1"}],
            },
        },
    )
    result = runtime.apply_event_batch(instance, {"batch_id": "b1"})
    assert result == {"ok": True, "applied": True}
    assert ruleset_runtime.state(instance)["opaque"] == {"hp": 9}
    assert ruleset_runtime.event_ledger(instance) == [{"batch_id": "b1"}]


def test_remote_state_bridge_rejects_history_replacement_without_mutating_live_state():
    from src.engine.game_instance import GameInstance
    from src.engine.modules import ruleset_runtime

    instance = GameInstance(game_key=("test", "group", "remote"), run_id="run-remote")
    ruleset_runtime.bind(instance, {
        "id": "external:demo", "version": 1, "rule_id": "demo",
        "content_version": "1", "state_schema_version": 1,
    })
    ruleset_runtime.replace_state(instance, {"version": 4, "opaque": {"hp": 9}})
    ruleset_runtime.replace_event_ledger(instance, [{"batch_id": "old"}])
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(),
        lambda request: {
            "request_id": request["request_id"], "ok": True,
            "result": {
                "ok": True,
                "state": {"version": 5},
                "event_ledger": [{"batch_id": "new"}],
            },
        },
    )
    with pytest.raises(RuntimeProbeError, match="preserve host history"):
        runtime.apply_event_batch(instance, {"batch_id": "new"})
    assert ruleset_runtime.state(instance) == {"version": 4, "opaque": {"hp": 9}}
    assert ruleset_runtime.event_ledger(instance) == [{"batch_id": "old"}]


def test_remote_state_bridge_rejects_duplicate_or_wrong_appended_batch():
    from src.engine.game_instance import GameInstance
    from src.engine.modules import ruleset_runtime

    instance = GameInstance(game_key=("test", "group", "remote"), run_id="run-remote")
    ruleset_runtime.bind(instance, {
        "id": "external:demo", "version": 1, "rule_id": "demo",
        "content_version": "1", "state_schema_version": 1,
    })
    responses = iter([
        {
            "state": {},
            "event_ledger": [{"batch_id": "b1"}, {"batch_id": "b1"}],
        },
        {
            "state": {},
            "event_ledger": [{"batch_id": "b2"}],
        },
    ])

    def transport(request):
        return {"request_id": request["request_id"], "ok": True, "result": next(responses)}

    runtime = RemoteRulesetRuntimeAdapter("external:demo", 1, RulesetCapabilities(), transport)
    with pytest.raises(RuntimeProbeError, match="duplicate"):
        runtime.apply_event_batch(instance, {"batch_id": "b1"})
    with pytest.raises(RuntimeProbeError, match="wrong batch"):
        runtime.apply_event_batch(instance, {"batch_id": "b3"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("automatic_intents", "automatic_reply", "expected_error"),
    [
        (False, "unsupported optional operation: next_automatic_intent", None),
        (True, None, None),
        (True, "unsupported optional operation: next_automatic_intent", "unsupported optional operation"),
        (True, ["invalid intent"], "must be an object or null"),
        (True, "transport_failure", "runtime probe transport failed"),
    ],
    ids=[
        "not-supported", "supported-exhausted", "supported-rejected",
        "supported-malformed", "supported-transport-failure",
    ],
)
async def test_remote_runtime_submission_negotiates_optional_automatic_intents(
    automatic_intents, automatic_reply, expected_error,
):
    from src.engine import module_hooks
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
        if operation == "next_automatic_intent":
            if automatic_reply == "transport_failure":
                raise TimeoutError("automatic intent RPC timed out")
            if isinstance(automatic_reply, str):
                return {"request_id": request["request_id"], "ok": False, "error": automatic_reply}
            return {"request_id": request["request_id"], "ok": True, "result": automatic_reply}
        if operation == "memory_deltas_from_event_batch":
            return {"request_id": request["request_id"], "ok": True, "result": []}
        raise AssertionError(operation)

    capabilities = RulesetCapabilities(
        authoritative_intents=True, versioned_state=True,
        automatic_intents=automatic_intents,
    )
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
    before_state = deepcopy(ruleset_runtime.state(instance))
    before_players = deepcopy(instance.players)
    before_log = deepcopy(instance.log)
    first = await ruleset_gameplay.submit_intent(
        dependencies, "|".join(instance.game_key), "one", False,
        {"type": "remote.action", "intent_id": "i-1"},
    )
    if expected_error:
        assert first["ok"] is False
        assert first["code"] == "INTENT_REJECTED"
        assert expected_error in first["error"]
        assert ruleset_runtime.state(instance) == before_state
        assert ruleset_runtime.event_ledger(instance) == []
        assert instance.players == before_players
        assert instance.log == before_log
        assert not module_hooks.seat_has_acted(instance, "one")
        assert saved == []
        assert [request["operation"] for request in calls] == [
            "prepare_intent_submission", "resolve_intent", "apply_event_batch",
            "next_automatic_intent",
        ]
        return
    second = await ruleset_gameplay.submit_intent(
        dependencies, "|".join(instance.game_key), "one", False,
        {"type": "remote.action", "intent_id": "i-2"},
    )
    assert first["ok"] and second["ok"]
    assert ruleset_runtime.state(instance)["version"] == 2
    assert len(ruleset_runtime.event_ledger(instance)) == 2
    assert len(saved) == 2
    operations = [request["operation"] for request in calls]
    assert operations.count("apply_event_batch") == 2
    assert operations.count("next_automatic_intent") == (2 if automatic_intents else 0)


@pytest.mark.asyncio
async def test_remote_runtime_concurrent_submissions_serialize_state_and_event_ledger():
    """Two external submissions must observe distinct host versions under one lock."""

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
        if operation in {"available_intents", "gameplay_view", "memory_deltas_from_event_batch"}:
            result = [] if operation in {"available_intents", "memory_deltas_from_event_batch"} else {}
            return {"request_id": request["request_id"], "ok": True, "result": result}
        if operation == "resolve_intent":
            payload = request["payload"]
            version = int(payload["instance"]["modules"]["ruleset_runtime"]["state"].get("version", 0))
            intent_id = payload["intent"]["intent_id"]
            return {"request_id": request["request_id"], "ok": True, "result": {
                "ok": True,
                "event_batch": {
                    "batch_id": f"remote-{version + 1}",
                    "intent_id": intent_id,
                    "expected_version": version,
                    "result_version": version + 1,
                    "events": [{"type": "remote.accepted", "version": version + 1}],
                },
            }}
        if operation == "apply_event_batch":
            payload = request["payload"]
            batch = payload["batch"]
            current = payload["instance"]["modules"]["ruleset_runtime"]["state"]
            ledger = payload["instance"]["modules"]["ruleset_runtime"]["event_ledger"]
            if any(row.get("batch_id") == batch["batch_id"] for row in ledger):
                return {"request_id": request["request_id"], "ok": True, "result": {
                    "ok": True, "applied": False, "replayed": True,
                    "state": current, "event_ledger": ledger,
                }}
            return {"request_id": request["request_id"], "ok": True, "result": {
                "ok": True, "applied": True,
                "state": {**current, "version": batch["result_version"]},
                "event_ledger": [*ledger, batch],
            }}
        raise AssertionError(operation)

    runtime = RemoteRulesetRuntimeAdapter(
        "external:concurrent", 1,
        RulesetCapabilities(authoritative_intents=True, versioned_state=True),
        transport,
    )
    registry = RulesetRuntimeRegistry([runtime])
    instance = GameInstance(game_key=("test", "group", "remote-concurrent"), run_id="run-concurrent")
    instance.players["one"] = {"user_id": "one", "character_sheet": {}}
    ruleset_runtime.bind(instance, {
        "id": "external:concurrent", "version": 1, "rule_id": "remote-rule",
        "content_version": "1", "state_schema_version": 1,
    })
    rule = RuleSystem({
        "rule_id": "remote-rule",
        "runtime": {"id": "external:concurrent", "minimum_version": 1},
    })
    saved = []

    async def save_instance(value):
        saved.append(ruleset_runtime.state(value).get("version"))

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
    game_key = "|".join(instance.game_key)
    first, second = await asyncio.gather(
        ruleset_gameplay.submit_intent(
            dependencies, game_key, "one", False,
            {"type": "remote.action", "intent_id": "concurrent-1"},
        ),
        ruleset_gameplay.submit_intent(
            dependencies, game_key, "one", False,
            {"type": "remote.action", "intent_id": "concurrent-2"},
        ),
    )
    assert first["ok"] and second["ok"]
    assert ruleset_runtime.state(instance)["version"] == 2
    assert [row["batch_id"] for row in ruleset_runtime.event_ledger(instance)] == ["remote-1", "remote-2"]
    assert sorted(saved) == [1, 2]
    assert all(request["operation"] != "next_automatic_intent" for request in calls)
    resolves = [request for request in calls if request["operation"] == "resolve_intent"]
    assert sorted(
        int(request["payload"]["instance"]["modules"]["ruleset_runtime"]["state"].get("version", 0))
        for request in resolves
    ) == [0, 1]


def test_remote_adapter_satisfies_versioned_state_binding():
    """Remote runtimes that own versioned state must answer the save binding.

    The binding is a protocol obligation rather than a per-ruleset special
    case: the host refuses to write ruleset state until it knows which runtime
    owns it, so an external runtime declaring ``versioned_state`` needs this
    exactly like a built-in one.
    """

    seen = []

    def transport(request):
        seen.append(request)
        return {
            "request_id": request["request_id"],
            "ok": True,
            "result": {
                "rule_id": "remote-rule",
                "runtime_id": "external:demo",
                "runtime_version": 1,
                "content_version": 3,
                "state_schema_version": 2,
            },
        }

    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(versioned_state=True), transport,
    )
    assert isinstance(runtime, VersionedStateRuntime)
    binding = runtime.game_binding({"rule_id": "remote-rule"}, "zh-CN")
    assert binding["runtime_id"] == "external:demo"
    assert seen[0]["operation"] == "game_binding"
    assert seen[0]["payload"] == {"rule": {"rule_id": "remote-rule"}, "locale": "zh-CN"}


def test_remote_adapter_rejects_non_object_game_binding():
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(versioned_state=True),
        lambda request: {"request_id": request["request_id"], "ok": True, "result": []},
    )
    with pytest.raises(RuntimeProbeError, match="game_binding"):
        runtime.game_binding({"rule_id": "remote-rule"}, "en")


@pytest.mark.parametrize(
    "capabilities",
    [
        RulesetCapabilities(), RulesetCapabilities(authoritative_intents=True),
        RulesetCapabilities(deterministic_combat=True),
    ],
)
def test_remote_adapter_does_not_infer_optional_automatic_rpc(capabilities):
    def transport(_request):
        pytest.fail("undeclared optional RPC must not be sent")

    runtime = RemoteRulesetRuntimeAdapter("external:demo", 1, capabilities, transport)
    assert runtime.next_automatic_intent({}) is None


def test_remote_adapter_serves_automatic_intents_until_exhausted():
    """Enemy and AI-seat turns stay server-owned for remote runtimes too."""

    seen = []

    def transport(request):
        seen.append(request)
        step = len(seen)
        intent = None if step == 2 else {"type": "remote.enemy", "intent_id": f"auto-{step}"}
        return {"request_id": request["request_id"], "ok": True, "result": intent}

    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1,
        RulesetCapabilities(deterministic_combat=True, automatic_intents=True), transport,
    )
    assert isinstance(runtime, AutomaticIntentRuntime)
    assert runtime.next_automatic_intent({"hp": 3})["intent_id"] == "auto-1"
    assert runtime.next_automatic_intent({"hp": 3}) is None
    assert [request["operation"] for request in seen] == [
        "next_automatic_intent",
        "next_automatic_intent",
    ]


def test_remote_adapter_rejects_non_object_automatic_intent():
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(automatic_intents=True),
        lambda request: {"request_id": request["request_id"], "ok": True, "result": ["nope"]},
    )
    with pytest.raises(RuntimeProbeError, match="next_automatic_intent"):
        runtime.next_automatic_intent({})


def test_remote_adapter_sends_rule_templates_not_host_objects():
    """Call sites pass ``RuleSystem``; only its template crosses the wire."""

    from src.rules.rule_system import RuleSystem

    seen = []

    def transport(request):
        seen.append(request)
        return {"request_id": request["request_id"], "ok": True, "result": {}}

    rule = RuleSystem({"rule_id": "remote-rule", "rule_name": "Remote", "dice_system": "d20"})
    runtime = RemoteRulesetRuntimeAdapter("external:demo", 1, RulesetCapabilities(), transport)
    runtime.describe_experience(rule, "en")
    runtime.game_binding(rule, "en")
    assert [request["payload"]["rule"]["rule_id"] for request in seen] == [
        "remote-rule", "remote-rule",
    ]


def test_remote_adapter_binds_versioned_state_for_new_game():
    """The creation phase accepts a remote binding rather than rolling back."""

    from src.engine.game_instance import GameInstance
    from src.rules.rule_system import RuleSystem
    from src.webui.services.game_creation_phases import bind_ruleset_runtime

    def transport(request):
        assert request["operation"] == "game_binding"
        return {"request_id": request["request_id"], "ok": True, "result": {
            "rule_id": "remote-rule",
            "runtime_id": "external:demo",
            "runtime_version": 1,
            "content_version": "1",
            "state_schema_version": 1,
        }}

    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(versioned_state=True), transport,
    )
    instance = GameInstance(game_key=("test", "group", "remote-binding"), run_id="run-binding")
    rule = RuleSystem({"rule_id": "remote-rule", "dice_system": "d20"})

    class Transaction:
        def rollback(self):
            raise AssertionError("creation must not roll back")

    assert bind_ruleset_runtime(Transaction(), instance, runtime, rule, "zh-CN") is None


def test_remote_adapter_forwards_a_narrative_check_batch():
    """One round trip carries every free-text check the planner accepted."""

    seen = []

    def transport(request):
        seen.append(request)
        assert request["operation"] == "resolve_narrative_checks"
        return {"request_id": request["request_id"], "ok": True, "result": [
            {"check_id": item["check_id"], "roll": 9, "verdict": "成功"}
            for item in request["payload"]["requests"]
        ]}

    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(narrative_checks=True), transport,
    )

    result = runtime.resolve_narrative_checks({"run_id": "r"}, [{"check_id": "a"}, {"check_id": "b"}])

    assert [item["check_id"] for item in result] == ["a", "b"]
    assert seen[0]["payload"]["instance"] == {"run_id": "r"}


def test_remote_adapter_rejects_a_non_list_check_reply():
    def transport(request):
        return {"request_id": request["request_id"], "ok": True, "result": {"checks": []}}

    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(narrative_checks=True), transport,
    )

    with pytest.raises(RuntimeProbeError):
        runtime.resolve_narrative_checks({"run_id": "r"}, [{"check_id": "a"}])


def _bound_instance_with_seat(uid: str = "alice"):
    from src.engine.game_instance import GameInstance
    from src.engine.modules import ruleset_runtime

    instance = GameInstance(game_key=("test", "group", "sheet"), run_id="run-sheet")
    ruleset_runtime.bind(instance, {
        "id": "external:demo", "version": 1, "rule_id": "demo",
        "content_version": "1", "state_schema_version": 1,
    })
    instance.put_player(uid, {"character_name": "Livia", "character_sheet": {"hp": 10}})
    return instance


def test_remote_adapter_writes_back_only_known_character_sheets():
    from src.engine.modules import ruleset_runtime

    instance = _bound_instance_with_seat()
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(),
        lambda request: {
            "request_id": request["request_id"], "ok": True,
            "result": {
                "ok": True,
                "applied": True,
                "state": {"version": 1},
                "event_ledger": [{"batch_id": "b1"}],
                "character_sheets": {"alice": {"hp": 3, "max_hp": 10}},
            },
        },
    )

    result = runtime.apply_event_batch(instance, {"batch_id": "b1"})

    # The bridge keys never reach the caller: they are transport, not results.
    assert result == {"ok": True, "applied": True}
    assert instance.get_character_sheet("alice") == {"hp": 3, "max_hp": 10}
    assert ruleset_runtime.state(instance) == {"version": 1}


def test_remote_adapter_sheet_bridge_rejects_an_unknown_seat_without_mutating_anything():
    from src.engine.modules import ruleset_runtime

    instance = _bound_instance_with_seat()
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(),
        lambda request: {
            "request_id": request["request_id"], "ok": True,
            "result": {
                "ok": True,
                "state": {"version": 9},
                "event_ledger": [{"batch_id": "b1"}],
                "character_sheets": {"mallory": {"hp": 0}},
            },
        },
    )

    with pytest.raises(RuntimeProbeError, match="unknown seat"):
        runtime.apply_event_batch(instance, {"batch_id": "b1"})

    assert instance.get_character_sheet("alice") == {"hp": 10}
    assert ruleset_runtime.state(instance) == {"state_schema_version": 1}


def test_remote_adapter_sheet_bridge_rejects_a_non_object_sheet():
    instance = _bound_instance_with_seat()
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(),
        lambda request: {
            "request_id": request["request_id"], "ok": True,
            "result": {"ok": True, "character_sheets": {"alice": ["hp", 3]}},
        },
    )

    with pytest.raises(RuntimeProbeError, match="must be an object"):
        runtime.apply_event_batch(instance, {"batch_id": "b1"})

    assert instance.get_character_sheet("alice") == {"hp": 10}


def test_remote_adapter_accepts_a_sheet_only_handoff():
    instance = _bound_instance_with_seat()
    runtime = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(),
        lambda request: {
            "request_id": request["request_id"], "ok": True,
            "result": {"ok": True, "character_sheets": {"alice": {"hp": 7}}},
        },
    )

    result = runtime.apply_event_batch(instance, {"batch_id": "b1"})

    assert result == {"ok": True}
    assert instance.get_character_sheet("alice") == {"hp": 7}
