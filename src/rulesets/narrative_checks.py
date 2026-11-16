"""Free-text checks owned by a rules runtime instead of the d20/d100 core.

The core can only roll d20 and d100, so a rule that declares
``dice_system: none`` gets no narrative checks at all.  A runtime that ships its
own dice (attribute dice added together, pools, draws) declares
``RulesetCapabilities.narrative_checks`` and answers
``resolve_narrative_checks`` instead.  This module owns the capability gate and
the shape check on the reply, so the engines only ever see one canonical
CheckResult.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from src.rulesets.contracts import NarrativeCheckResolverRuntime

logger = logging.getLogger(__name__)

# Fields copied from the planner request so the UI and the prompt keep the same
# context a core-rolled check would carry.
_REQUEST_FIELDS = (
    "label",
    "actor_ref",
    "actor_name",
    "skill",
    "attribute",
    "kind",
    "opponent",
    "opponent_name",
    "advantage_mode",
    "advantage_note",
    "assist",
    "dc_reason",
    "advantage_reason",
    "modifier_reason",
    "planner_notes",
)


def resolver_for(runtime: Any) -> NarrativeCheckResolverRuntime | None:
    """Return the runtime when it both declares and implements the capability.

    The capability bit is what gates the call, not ``isinstance``: a transport
    adapter always exposes the method, while a runtime that did not declare the
    capability must keep its checks out of this path.
    """

    if runtime is None:
        return None
    capabilities = getattr(runtime, "capabilities", None)
    if not bool(getattr(capabilities, "narrative_checks", False)):
        return None
    if not callable(getattr(runtime, "resolve_narrative_checks", None)):
        return None
    return runtime  # type: ignore[return-value]


def normalize_resolved_checks(
    requests: list[dict[str, Any]],
    results: Any,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair each request with a runtime answer, dropping unusable pairs.

    Matching is by ``check_id``; a reply that omits ids is matched positionally,
    which keeps simple runtimes from having to echo them.  An answer without a
    positive roll or a verdict is rejected rather than stored, so a broken
    runtime degrades to "no check this round" instead of an unreadable record.
    """

    if not isinstance(results, (list, tuple)):
        raise ValueError("resolve_narrative_checks must return a list")
    by_id: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        ordered.append(item)
        check_id = str(item.get("check_id") or "")
        if check_id:
            by_id[check_id] = item
    positional = len(ordered) == len(requests)
    paired: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, request in enumerate(requests):
        answer = by_id.get(str(request.get("check_id") or ""))
        if answer is None and positional:
            answer = ordered[index]
        if answer is None:
            logger.warning("规则运行时没有结算检定: check_id=%s", request.get("check_id"))
            continue
        check = _canonical_check(request, answer)
        if check is None:
            logger.warning(
                "规则运行时返回的检定结果不可用: check_id=%s", request.get("check_id"),
            )
            continue
        paired.append((request, check))
    return paired


def _canonical_check(
    request: dict[str, Any], answer: dict[str, Any],
) -> dict[str, Any] | None:
    raw_roll: Any = answer.get("roll")
    if raw_roll is None:
        raw_roll = answer.get("value")
    try:
        roll = int(raw_roll)
    except (TypeError, ValueError):
        return None
    if roll <= 0:
        return None
    verdict = str(answer.get("verdict") or "").strip()
    if not verdict:
        return None
    try:
        rolls = [int(value) for value in (answer.get("rolls") or [roll])]
    except (TypeError, ValueError):
        rolls = [roll]
    check: dict[str, Any] = {
        field: request.get(field) for field in _REQUEST_FIELDS
    }
    check.update({
        "check_id": str(request.get("check_id") or answer.get("check_id") or ""),
        "actor_uid": str(request.get("actor_uid") or ""),
        "dice": str(answer.get("dice") or "runtime"),
        "roll": roll,
        "rolls": rolls,
        "verdict": verdict,
        "planner_source": "runtime",
    })
    for field in ("total", "dc", "threshold"):
        value = answer.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            check[field] = value
    if "dc" not in check and "threshold" in check:
        check["dc"] = check["threshold"]
    check["is_critical"] = bool(answer.get("is_critical") or answer.get("critical"))
    check["is_fumble"] = bool(answer.get("is_fumble") or answer.get("fumble"))
    detail = str(answer.get("detail") or "").strip()
    if detail:
        check["detail"] = detail[:300]
    constraint = str(answer.get("constraint") or "").strip()
    if constraint:
        check["constraint"] = constraint[:1200]
    return check


def resolve_planned_checks(
    instance: Any,
    resolver: NarrativeCheckResolverRuntime,
    planned: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Hand the round's accepted checks to the runtime and store the rolls.

    The roll lands on the action exactly like a core-rolled check, so a retry or
    a re-plan reuses it instead of rolling again.  Unanswered requests are
    dropped: keeping them would let the core fall back to a d20 roll for a rule
    that has no d20.
    """

    # The action owns the settled check, not the next planner invocation. Keep
    # the original request/identity even if a retry proposes different inputs.
    reused: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unresolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for action, request in planned:
        check = action.get("check_result")
        if isinstance(check, dict) and (
            check.get("planner_source") == "runtime"
            or action.get("dice_roll_source") == "runtime"
        ):
            original = deepcopy(action.get("check_request") or request)
            original["check_id"] = check["check_id"]
            original["dice_system"] = "runtime"
            reused.append((action, original))
        else:
            unresolved.append((action, request))
    if not unresolved:
        return reused
    requests = [request for _, request in unresolved]
    try:
        results = resolver.resolve_narrative_checks(
            instance, [deepcopy(request) for request in requests],
        )
    except Exception:
        game_key = getattr(instance, "game_key", "")
        logger.exception("规则运行时未能结算叙事检定，本轮不掷骰: game=%s", game_key)
        return reused
    by_request = {
        id(request): check
        for request, check in normalize_resolved_checks(requests, results)
    }
    surviving = list(reused)
    for action, request in unresolved:
        check = by_request.get(id(request))
        if check is None:
            continue
        request["dice_system"] = "runtime"
        action["check_request"] = request
        action["check_result"] = check
        action["dice_value"] = check["roll"]
        action["dice_rolls"] = list(check["rolls"])
        action["dice_system"] = "runtime"
        action["dice_roll_source"] = "runtime"
        action["dice_pending"] = False
        surviving.append((action, request))
    return surviving
