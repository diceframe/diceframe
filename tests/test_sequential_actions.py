"""R05 / SK-01 host contract tests."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.registry import RulesetRuntimeRegistry
from src.rulesets.sequential_actions import execute_sequential_plan, normalize_plan
from src.webui.services.ruleset_gameplay import RulesetGameplayDependencies, submit_intent


class FakeSequentialRuntime:
    runtime_id = "test:sequential"
    runtime_version = 1
    capabilities = RulesetCapabilities(sequential_checks=True, composite_action=True)

    def __init__(self, outcomes: dict[str, bool] | None = None, *, valid: bool = True):
        self.outcomes = outcomes or {}
        self.valid = valid
        self.validated: list[dict] = []

    def validate_sequential_plan(self, instance, plan, requester_id, requester_is_gm):
        del instance, requester_id, requester_is_gm
        self.validated.append(deepcopy(plan))
        return {"ok": self.valid}

    def resolve_sequential_step(self, instance, step, rng):
        del rng
        version = int(ruleset_runtime.state(instance).get("version", 0) or 0)
        step_id = str(step["step_id"])
        return {
            "ok": True,
            "success": self.outcomes.get(step_id, True),
            "result": {"canonical_step": step_id},
            "event_batch": {
                "batch_id": f"batch_{version + 1:032d}",
                "intent_id": f"plan:{step_id}",
                "expected_version": version,
                "result_version": version + 1,
                "events": [{"type": "test.step", "step_id": step_id}],
            },
        }

    def apply_event_batch(self, instance, batch):
        state = ruleset_runtime.state(instance)
        ledger = ruleset_runtime.event_ledger(instance)
        if any(item.get("batch_id") == batch["batch_id"] for item in ledger):
            return {"ok": True, "applied": False, "replayed": True}
        assert state["version"] == batch["expected_version"]
        state["version"] = batch["result_version"]
        ledger.append(deepcopy(batch))
        return {"ok": True, "applied": True}


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("test", "group", "account"), run_id="run-test")
    ruleset_runtime.replace_state(instance, {"version": 0})
    return instance


def _plan(**overrides):
    plan = {
        "type": "sequential_action",
        "intent_id": "plan-1",
        "mode": "sequential_checks",
        "steps": [
            {"step_id": "a", "actor_id": "player:one", "skill_id": "skill:a", "order": 0},
            {"step_id": "b", "actor_id": "player:one", "skill_id": "skill:b", "order": 1,
             "depends_on": ["a"]},
            {"step_id": "c", "actor_id": "player:one", "skill_id": "skill:c", "order": 2,
             "depends_on": ["b"]},
        ],
    }
    plan.update(overrides)
    return plan


def test_normalize_plan_rejects_client_owned_dice_and_forward_dependencies():
    forged = _plan()
    forged["steps"][0]["roll"] = 20
    try:
        normalize_plan(forged)
    except ValueError as exc:
        assert "server-owned" in str(exc)
    else:
        raise AssertionError("client supplied dice must be rejected")

    forged = _plan()
    forged["steps"][0]["depends_on"] = ["b"]
    try:
        normalize_plan(forged)
    except ValueError as exc:
        assert "earlier" in str(exc)
    else:
        raise AssertionError("forward dependency must be rejected")


def test_failed_dependency_skips_following_step_and_commits_legal_prefix():
    instance = _instance()
    runtime = FakeSequentialRuntime({"a": True, "b": False, "c": True})
    result = execute_sequential_plan(instance, runtime, _plan(), "one", False, object())

    assert result["ok"] is True
    assert [step["status"] for step in result["steps"]] == ["succeeded", "failed", "skipped"]
    assert result["steps"][2]["reason"] == "dependency_failed"
    assert ruleset_runtime.state(instance)["version"] == 2
    assert len(ruleset_runtime.event_ledger(instance)) == 2


def test_preflight_rejection_is_atomic_and_duplicate_plan_is_replayed():
    instance = _instance()
    rejected_runtime = FakeSequentialRuntime(valid=False)
    rejected = execute_sequential_plan(instance, rejected_runtime, _plan(), "one", False, object())
    assert rejected["ok"] is False
    assert ruleset_runtime.state(instance)["version"] == 0
    assert ruleset_runtime.event_ledger(instance) == []

    runtime = FakeSequentialRuntime()
    first = execute_sequential_plan(instance, runtime, _plan(), "one", False, object())
    second = execute_sequential_plan(instance, runtime, _plan(), "one", False, object())
    assert first["ok"] is True
    assert second["ok"] is True
    assert second["replayed"] is True
    assert ruleset_runtime.state(instance)["version"] == 3
    assert len(ruleset_runtime.event_ledger(instance)) == 3


class _Rule:
    template = {"rule_id": "sequential-test", "runtime": {"id": "test:sequential"}}
    rule_id = "sequential-test"


class _HttpRuntime(FakeSequentialRuntime):
    capabilities = RulesetCapabilities(
        authoritative_intents=True,
        sequential_checks=True,
    )

    def prepare_intent_submission(self, intent, requester_id, requester_is_gm):
        del requester_is_gm
        intent = deepcopy(intent)
        intent["submitted_by"] = requester_id
        return intent

    def gameplay_view(self, instance, viewer_id="", viewer_is_gm=False):
        del instance, viewer_id, viewer_is_gm
        return {}

    def available_intents(self, instance, actor_id):
        del instance, actor_id
        return []

    def memory_deltas_from_event_batch(self, batch, instance):
        del batch, instance
        return []


@pytest.mark.asyncio
async def test_http_intent_uses_the_same_sequential_authority_path():
    instance = _instance()
    instance.players["one"] = {"user_id": "one", "character_name": "One", "character_sheet": {}}
    runtime = _HttpRuntime()
    ruleset_runtime.bind(instance, {
        "id": runtime.runtime_id,
        "version": runtime.runtime_version,
        "rule_id": "sequential-test",
        "content_version": "test",
        "state_schema_version": 1,
    })
    registry = RulesetRuntimeRegistry([runtime])
    saved = []

    async def save(value):
        saved.append(value)

    dependencies = RulesetGameplayDependencies(
        get_instance=lambda key: instance if key == instance.game_key else None,
        parse_game_key=lambda value: instance.game_key,
        load_rule_for_game=lambda value: _Rule(),
        ruleset_registry=registry,
        resolve_adventure_binding=lambda *args: {},
        save_instance=save,
        apply_memory_delta=None,
    )
    result = await submit_intent(
        dependencies,
        "|".join(instance.game_key),
        "one",
        False,
        _plan(intent_id="http-plan"),
    )

    assert result["ok"] is True, result
    assert result["result"]["receipt_id"] == "seq:http-plan"
    assert len(saved) == 1
