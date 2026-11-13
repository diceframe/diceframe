"""Host-side authority for R05 sequential checks and composite actions.

The module deliberately knows only the stable plan contract.  Dice, resource
names, cooldowns, action economy and event semantics remain in the ruleset
runtime.  The host is responsible for bounded input, dependency ordering,
staged publication and retry identity.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from src.engine.staged_transaction import fork_domain_state
from src.engine.modules import ruleset_runtime
from src.rulesets.contracts import SequentialActionRuntime


MAX_SEQUENTIAL_STEPS = 8
MAX_SEQUENTIAL_PLAN_BYTES = 64 * 1024
MAX_STEP_ID_LENGTH = 80
MAX_CANONICAL_ID_LENGTH = 160
MAX_RESOURCE_COSTS = 16
MAX_DEPENDENCIES = MAX_SEQUENTIAL_STEPS - 1

_MODES = {"sequential_checks", "composite_action"}
_FAILURE_POLICIES = {"stop_dependents", "abort_plan"}
_FORBIDDEN_STEP_KEYS = {
    "dice", "dice_value", "dice_rolls", "roll", "rolls", "hp",
    "event_batch", "result", "result_status", "resource_after",
}


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message, **extra}


def _bounded_json(value: Any) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("sequential action must contain JSON-compatible values") from exc
    if len(encoded.encode("utf-8")) > MAX_SEQUENTIAL_PLAN_BYTES:
        raise ValueError("sequential action plan is too large")

    def walk(item: Any, depth: int = 0, nodes: list[int] | None = None) -> None:
        if nodes is None:
            nodes = [0]
        if depth > 16:
            raise ValueError("sequential action plan nesting is too deep")
        nodes[0] += 1
        if nodes[0] > 2000:
            raise ValueError("sequential action plan contains too many values")
        if isinstance(item, dict):
            for child in item.values():
                walk(child, depth + 1, nodes)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1, nodes)

    walk(value)


def _canonical_id(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > MAX_CANONICAL_ID_LENGTH:
        raise ValueError(f"{field} must be a non-empty canonical id")
    return result


def normalize_plan(intent: dict[str, Any]) -> dict[str, Any]:
    """Normalize and bound the client/model proposal without resolving it."""

    if not isinstance(intent, dict):
        raise ValueError("sequential action must be an object")
    if str(intent.get("type") or "") != "sequential_action":
        raise ValueError("sequential action type is invalid")
    plan_id = _canonical_id(intent.get("intent_id"), "intent_id")
    if len(plan_id) > 120:
        raise ValueError("intent_id is too long")
    mode = str(intent.get("mode") or "").strip()
    if mode not in _MODES:
        raise ValueError("sequential action mode is invalid")
    raw_steps = intent.get("steps")
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_SEQUENTIAL_STEPS:
        raise ValueError(f"steps must contain 1 to {MAX_SEQUENTIAL_STEPS} items")
    if mode == "sequential_checks" and len(raw_steps) < 2:
        raise ValueError("sequential_checks requires at least two steps")

    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ValueError(f"steps[{index}] must be an object")
        unknown_authority = _FORBIDDEN_STEP_KEYS.intersection(raw)
        if unknown_authority:
            raise ValueError(
                f"steps[{index}] contains server-owned fields: {sorted(unknown_authority)!r}"
            )
        step_id = _canonical_id(raw.get("step_id"), f"steps[{index}].step_id")
        if len(step_id) > MAX_STEP_ID_LENGTH:
            raise ValueError(f"steps[{index}].step_id is too long")
        if step_id in seen:
            raise ValueError(f"duplicate step_id: {step_id}")
        seen.add(step_id)
        order = raw.get("order", index)
        if isinstance(order, bool) or not isinstance(order, int) or order != index:
            raise ValueError("steps must use contiguous zero-based order")
        actor_id = _canonical_id(raw.get("actor_id"), f"steps[{index}].actor_id")
        skill_id = str(raw.get("skill_id") or "").strip()
        ability_id = str(raw.get("ability_id") or "").strip()
        if bool(skill_id) == bool(ability_id):
            raise ValueError(
                f"steps[{index}] must contain exactly one skill_id or ability_id"
            )
        if len(skill_id or ability_id) > MAX_CANONICAL_ID_LENGTH:
            raise ValueError(f"steps[{index}] canonical id is too long")
        dependencies = raw.get("depends_on", [])
        if not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES:
            raise ValueError(f"steps[{index}].depends_on is invalid")
        normalized_dependencies: list[str] = []
        for dependency in dependencies:
            dep = _canonical_id(dependency, f"steps[{index}].depends_on")
            if dep in normalized_dependencies:
                raise ValueError(f"steps[{index}] contains duplicate dependency")
            normalized_dependencies.append(dep)
        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"steps[{index}].params must be an object")
        target = raw.get("target")
        resource_cost = raw.get("resource_cost", {})
        if not isinstance(resource_cost, dict) or len(resource_cost) > MAX_RESOURCE_COSTS:
            raise ValueError(f"steps[{index}].resource_cost is invalid")
        normalized_cost: dict[str, int] = {}
        for key, value in resource_cost.items():
            resource_id = _canonical_id(key, f"steps[{index}].resource_cost")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
                raise ValueError(f"steps[{index}].resource_cost values must be non-negative integers")
            normalized_cost[resource_id] = value
        failure_policy = str(raw.get("on_failure") or "stop_dependents").strip()
        if failure_policy not in _FAILURE_POLICIES:
            raise ValueError(f"steps[{index}].on_failure is invalid")
        step = {
            "step_id": step_id,
            "actor_id": actor_id,
            "order": index,
            "depends_on": normalized_dependencies,
            "params": deepcopy(params),
            "resource_cost": normalized_cost,
            "on_failure": failure_policy,
        }
        if skill_id:
            step["skill_id"] = skill_id
        else:
            step["ability_id"] = ability_id
        # Keep the field present even for self-targeted abilities; ``null`` is
        # an explicit target decision, whereas omission would make the plan
        # ambiguous to a runtime or an audit reader.
        step["target"] = deepcopy(target)
        steps.append(step)

    step_ids = {step["step_id"] for step in steps}
    for index, step in enumerate(steps):
        for dependency in step["depends_on"]:
            if dependency not in step_ids:
                raise ValueError(f"steps[{index}] depends on an unknown step")
            dependency_order = next(item["order"] for item in steps if item["step_id"] == dependency)
            if dependency_order >= index:
                raise ValueError("step dependencies must point to earlier steps")
    normalized = {
        "type": "sequential_action",
        "intent_id": plan_id,
        "mode": mode,
        "steps": steps,
    }
    if intent.get("submitted_by"):
        normalized["submitted_by"] = _canonical_id(intent["submitted_by"], "submitted_by")
    _bounded_json(normalized)
    return normalized


def _plan_digest(plan: dict[str, Any]) -> str:
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _capability_for_mode(runtime: Any, mode: str) -> bool:
    capabilities = getattr(runtime, "capabilities", None)
    return bool(getattr(capabilities, mode, False))


def _prior_receipt(instance: Any, plan_id: str, digest: str) -> dict[str, Any] | None:
    batches = [
        item for item in ruleset_runtime.event_ledger(instance)
        if isinstance(item, dict) and str(item.get("sequential_plan_id") or "") == plan_id
    ]
    if not batches:
        return None
    if any(str(item.get("sequential_plan_digest") or "") != digest for item in batches):
        return _error("SEQUENTIAL_INTENT_CONFLICT", "intent_id 已用于另一份顺序动作计划")
    receipt = next(
        (item.get("sequential_receipt") for item in batches if isinstance(item.get("sequential_receipt"), dict)),
        None,
    )
    return {
        "ok": True,
        "replayed": True,
        "receipt_id": f"seq:{plan_id}",
        "steps": deepcopy((receipt or {}).get("steps") or []),
        "status": str((receipt or {}).get("status") or "completed"),
        "resolved_event_batches": deepcopy(batches),
    }


def execute_sequential_plan(
    instance: Any,
    runtime: SequentialActionRuntime,
    intent: dict[str, Any],
    requester_id: str,
    requester_is_gm: bool,
    rng: Any,
) -> dict[str, Any]:
    """Preflight and execute one plan on a staged aggregate.

    The caller holds the normal per-game authority lock.  Only the fully
    planned staged aggregate is published; a runtime rejection leaves the live
    instance untouched.
    """

    try:
        plan = normalize_plan(intent)
    except ValueError as exc:
        return _error("INVALID_SEQUENTIAL_PLAN", str(exc))
    mode = str(plan["mode"])
    if not _capability_for_mode(runtime, mode):
        return _error("SEQUENTIAL_ACTION_UNAVAILABLE", f"当前规则未声明 {mode} 能力")
    digest = _plan_digest(plan)
    prior = _prior_receipt(instance, str(plan["intent_id"]), digest)
    if prior is not None:
        return prior
    try:
        validation = runtime.validate_sequential_plan(
            instance, deepcopy(plan), requester_id, requester_is_gm,
        )
    except (ValueError, KeyError, TypeError) as exc:
        return _error("SEQUENTIAL_PLAN_REJECTED", str(exc))
    if not isinstance(validation, dict) or not validation.get("ok"):
        if isinstance(validation, dict):
            return {**validation, "ok": False}
        return _error("SEQUENTIAL_PLAN_REJECTED", "规则运行时拒绝了顺序动作计划")

    staged = fork_domain_state(instance)
    steps: list[dict[str, Any]] = []
    step_status: dict[str, str] = {}
    batches: list[dict[str, Any]] = []
    aborted = False
    for step in plan["steps"]:
        step_id = str(step["step_id"])
        dependencies = [str(item) for item in step.get("depends_on") or []]
        blocked = any(step_status.get(item) != "succeeded" for item in dependencies)
        if aborted or blocked:
            status = "skipped"
            step_status[step_id] = status
            steps.append({"step_id": step_id, "order": step["order"], "status": status,
                          "reason": "dependency_failed" if blocked else "plan_aborted"})
            continue
        try:
            resolved = runtime.resolve_sequential_step(staged, deepcopy(step), rng)
        except (ValueError, KeyError, TypeError) as exc:
            return _error("SEQUENTIAL_STEP_REJECTED", str(exc), step_id=step_id)
        if not isinstance(resolved, dict) or not resolved.get("ok"):
            return _error(
                "SEQUENTIAL_STEP_REJECTED",
                str((resolved or {}).get("error") or "规则运行时拒绝了步骤"),
                step_id=step_id,
            )
        batch = resolved.get("event_batch")
        if not isinstance(batch, dict):
            return _error("INVALID_EVENT_BATCH", "顺序动作步骤没有返回有效事件批次", step_id=step_id)
        batch = deepcopy(batch)
        batch.update({
            "sequential_plan_id": plan["intent_id"],
            "sequential_plan_digest": digest,
            "sequential_step_id": step_id,
            "sequential_step_order": step["order"],
            # The reducer normally deep-copies a committed batch into the
            # ledger.  Seed a receipt marker before apply, then replace it
            # with the final step summary after the whole staged plan has
            # been walked.
            "sequential_receipt": {
                "receipt_id": f"seq:{plan['intent_id']}",
                "status": "pending",
                "steps": [],
            },
        })
        try:
            applied = runtime.apply_event_batch(staged, batch)
        except (ValueError, KeyError, TypeError) as exc:
            return _error("SEQUENTIAL_STEP_REJECTED", str(exc), step_id=step_id)
        if not isinstance(applied, dict):
            return _error("INVALID_EVENT_BATCH_RESULT", "顺序动作步骤返回了无效结算结果", step_id=step_id)
        success = resolved.get("success")
        if not isinstance(success, bool):
            success = bool(resolved.get("passed", applied.get("success", True)))
        status = "succeeded" if success else "failed"
        step_status[step_id] = status
        result = {
            "step_id": step_id,
            "order": step["order"],
            "status": status,
            "success": success,
        }
        if isinstance(resolved.get("result"), dict):
            result["result"] = deepcopy(resolved["result"])
        steps.append(result)
        batches.append(batch)
        if not success and step.get("on_failure") == "abort_plan":
            aborted = True

    receipt = {
        "receipt_id": f"seq:{plan['intent_id']}",
        "status": "aborted" if aborted else "completed",
        "steps": deepcopy(steps),
    }
    for batch in batches:
        batch["sequential_receipt"] = deepcopy(receipt)
        for ledger_batch in ruleset_runtime.event_ledger(staged):
            if isinstance(ledger_batch, dict) and ledger_batch.get("batch_id") == batch.get("batch_id"):
                ledger_batch["sequential_receipt"] = deepcopy(receipt)
    if batches:
        instance.replace_persisted_state_from(staged)
    return {
        "ok": True,
        "replayed": False,
        "receipt_id": receipt["receipt_id"],
        "status": receipt["status"],
        "steps": steps,
        "resolved_event_batches": batches,
        "applied": bool(batches),
    }
