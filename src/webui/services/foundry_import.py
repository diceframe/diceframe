"""FVTT Scene import preview/commit service (foundry-02).

Frozen contract: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.9 and §8.1.

Preview converts one FVTT scene through the B3 adapter and returns a normalized
plan plus its digest; it never writes authoritative state. Commit re-validates
the digest against the live scene slot, applies each item through the scene-03
authority path (``authoritative_write`` + expected revision + save rollback) and
records a provenance receipt on every written scene. A repeated commit with the
same ``plan_digest`` and ``operation_id`` returns the stored receipt instead of
writing again.

The service takes explicit dependencies (never the WebAPI facade); pure plan
logic lives in ``src.compat.foundry.import_plan``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Awaitable, Callable

from src.compat.foundry.import_plan import (
    ACTION_DUPLICATE,
    ACTION_SKIP,
    ACTION_UPDATE,
    ADAPTER_ID,
    CODE_PLAN_INVALID,
    FoundryImportPlanError,
    build_plan,
    claimed_plan,
    duplicate_canonical_id,
    validate_commit_policy,
    validate_operation_id,
    verify_and_refresh,
)
from src.compat.foundry.report import ConversionLevel
from src.compat.foundry.scene_adapter import FoundrySceneError, import_scene
from src.engine.module_state import ModuleStateError
from src.engine.modules import tactical_scene
from src.engine.participant_view import Viewer
from src.tactical_scene.codec import decode_tactical_scene_document
from src.tactical_scene.contract import TacticalSceneContractError
from src.tactical_scene.ops import TacticalSceneOpError
from src.tactical_scene.projection import project_tactical_scene_gm, project_tactical_scene_player
from src.webui.errors import error_payload

#: ``ConnectionPool.broadcast(game_key, data, exclude_uid=..., private_uid=...)``.
Broadcast = Callable[..., Awaitable[None]]

MAX_IMPORT_BYTES = 16 * 1024 * 1024

_STATUS_BY_ACTION = {"create": "created", "update": "updated", "duplicate": "duplicated"}


@dataclass(frozen=True)
class FoundryImportDependencies:
    """Explicit owner seams; no WebAPI facade and no transport objects."""

    get_instance: Callable[[str], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]


def _error(code: str) -> dict[str, Any]:
    return error_payload(f"webui.services.foundry_import.{code}", code=code)


def _payload_size(raw: Any) -> int:
    try:
        return len(json.dumps(raw, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return MAX_IMPORT_BYTES + 1


def _slot_state(instance: Any) -> tuple[dict[str, Any], int]:
    return tactical_scene.scenes(instance), tactical_scene.revision(instance)


# ---- preview --------------------------------------------------------------


def preview(
    dependencies: FoundryImportDependencies,
    game_key: str,
    viewer: Viewer,
    scene: Any,
    *,
    source_version: Any,
) -> dict[str, Any]:
    """Convert one FVTT scene into an import plan; never writes."""

    if not viewer.is_gm:
        return _error("tactical_scene_forbidden")
    instance = dependencies.get_instance(game_key)
    if instance is None:
        return _error("tactical_scene_not_found")
    if not isinstance(scene, Mapping):
        return _error("tactical_scene_invalid_payload")
    if _payload_size(scene) > MAX_IMPORT_BYTES:
        return _error("tactical_scene_invalid_payload")
    try:
        document, report = import_scene(scene, source_version=source_version)
    except FoundrySceneError as exc:
        code = (
            "foundry_version_unsupported"
            if exc.code == "foundry_version_unsupported"
            else "tactical_scene_invalid_payload"
        )
        return _error(code)
    try:
        existing, base_revision = _slot_state(instance)
    except ModuleStateError:
        return _error("tactical_scene_invalid_payload")
    try:
        plan = build_plan(
            [(document, report)], existing, base_revision=base_revision,
        )
    except FoundryImportPlanError as exc:
        return _error(exc.code)
    return {
        "ok": True,
        "plan_digest": plan["plan_digest"],
        "plan": plan,
        "contains_script": any(item["contains_script"] for item in plan["items"]),
        "level": (
            report.level.value if isinstance(report.level, ConversionLevel) else str(report.level)
        ),
    }


# ---- commit ---------------------------------------------------------------


def _marker(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    provenance = raw.get("provenance")
    if not isinstance(provenance, Mapping):
        return None
    marker = provenance.get("foundry_import")
    return dict(marker) if isinstance(marker, Mapping) else None


def _decision_target(decision: Mapping[str, Any], operation_id: str) -> str | None:
    action = decision["action"]
    if action == ACTION_SKIP:
        return None
    target = decision["canonical_id"]
    if action == ACTION_DUPLICATE:
        target = duplicate_canonical_id(target, operation_id)
    return target


def _existing_receipt(
    scenes: Mapping[str, Any],
    decisions: list[dict[str, Any]],
    *,
    plan_digest: str,
    operation_id: str,
) -> list[dict[str, Any]] | str | None:
    """The receipt rows of a completed identical commit, ``"partial"`` or ``None``."""

    rows: list[dict[str, Any]] = []
    expected = 0
    found = 0
    for decision in decisions:
        target = _decision_target(decision, operation_id)
        if target is None:
            rows.append({
                "canonical_id": decision["canonical_id"], "action": ACTION_SKIP, "status": "skipped",
            })
            continue
        expected += 1
        marker = _marker(scenes.get(target))
        if (
            marker is not None
            and marker.get("operation_id") == operation_id
            and marker.get("plan_digest") == plan_digest
            and isinstance(marker.get("status"), str)
        ):
            found += 1
            rows.append({
                "canonical_id": target, "action": decision["action"], "status": marker["status"],
            })
        else:
            rows.append({"canonical_id": target, "action": decision["action"], "status": "missing"})
    if expected and found == expected:
        return rows
    if found:
        return "partial"
    return None


def _stamped_document(
    item: Mapping[str, Any],
    *,
    action: str,
    target: str,
    plan_digest: str,
    operation_id: str,
    source_version: str,
) -> dict[str, Any]:
    document = deepcopy(dict(item["document"]))
    document["canonical_id"] = target
    if action == ACTION_DUPLICATE:
        # A local duplicate must not keep the source's external identity, or the
        # next import would see two scenes claiming the same FVTT document.
        document["external_ids"] = {}
    provenance = document.get("provenance")
    provenance = deepcopy(dict(provenance)) if isinstance(provenance, Mapping) else {}
    marker = {
        "adapter": ADAPTER_ID,
        "source_version": source_version,
        "plan_digest": plan_digest,
        "operation_id": operation_id,
        "action": action,
        "status": _STATUS_BY_ACTION[action],
        "source_canonical_id": item.get("source_canonical_id") or item["canonical_id"],
    }
    if action == ACTION_DUPLICATE:
        marker["duplicated_from"] = item["canonical_id"]
    provenance["foundry_import"] = marker
    document["provenance"] = provenance
    return document


def _operation(decision: Mapping[str, Any], item: Mapping[str, Any], *, target: str, **stamp: Any) -> dict[str, Any]:
    document = _stamped_document(item, action=decision["action"], target=target, **stamp)
    if decision["action"] == ACTION_UPDATE:
        return {"kind": "tactical_scene_update", "scene_id": target, "scene": document}
    return {"kind": "tactical_scene_create", "scene": document}


def _receipt(
    decisions: list[dict[str, Any]],
    written: Mapping[str, dict[str, Any]],
    *,
    plan_digest: str,
    operation_id: str,
    source_version: str,
    revision: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        target = written.get(decision["canonical_id"])
        if target is None:
            if decision["action"] == ACTION_SKIP:
                rows.append({
                    "canonical_id": decision["canonical_id"],
                    "action": ACTION_SKIP,
                    "status": "skipped",
                })
            continue
        rows.append(target)
    return {
        "operation_id": operation_id,
        "plan_digest": plan_digest,
        "source_version": source_version,
        "revision": revision,
        "items": rows,
    }


async def _broadcast_commit(
    broadcast: Broadcast | None,
    game_key: str,
    instance: Any,
    summaries: list[Mapping[str, Any]],
    scenes: Mapping[str, Any],
) -> None:
    """Mirror the scene-03 ``tactical_scene_updated`` projection broadcast per written scene."""

    if broadcast is None:
        return
    gm_uid = str(getattr(instance, "gm_uid", "") or "")
    for summary in summaries:
        scene_id = summary["scene_id"]
        raw = scenes.get(scene_id)
        try:
            document = decode_tactical_scene_document(raw) if isinstance(raw, Mapping) else None
        except (ModuleStateError, TacticalSceneContractError):
            document = None
        base = {
            "event_type": "tactical_scene_updated",
            "run_id": instance.run_id,
            "scene_id": scene_id,
            "revision": summary["revision"],
            "source_operation": summary["operation"],
        }
        if document is None:
            seat_event = {**base, "visibility": "seat", "projection": None}
            gm_event = {**base, "visibility": "gm", "projection": None}
        else:
            seat_event = {
                **base,
                "visibility": "seat",
                "projection": project_tactical_scene_player(document, uid="", owned_actor_refs=set()),
            }
            gm_event = {**base, "visibility": "gm", "projection": project_tactical_scene_gm(document)}
        await broadcast(game_key, seat_event, exclude_uid=gm_uid)
        if gm_uid:
            await broadcast(game_key, gm_event, private_uid=gm_uid)


async def commit(
    dependencies: FoundryImportDependencies,
    game_key: str,
    viewer: Viewer,
    plan: Any,
    *,
    plan_digest: Any,
    policies: Any,
    operation_id: Any,
    broadcast: Broadcast | None = None,
) -> dict[str, Any]:
    """Re-validate a preview and apply every item through the authority path."""

    if not viewer.is_gm:
        return _error("tactical_scene_forbidden")
    instance = dependencies.get_instance(game_key)
    if instance is None:
        return _error("tactical_scene_not_found")
    try:
        operation = validate_operation_id(operation_id)
    except FoundryImportPlanError as exc:
        return _error(exc.code)
    if not isinstance(plan_digest, str) or not plan_digest:
        return _error("foundry_import_stale")

    async with instance.authoritative_write() as entered:
        if not entered:
            return _error("rewrite_in_progress")
        if dependencies.get_instance(game_key) is not instance:
            return _error("stale_run")
        async with instance._lock:
            try:
                before = tactical_scene.snapshot(instance)
                scenes, base_revision = _slot_state(instance)
            except ModuleStateError:
                return _error("tactical_scene_invalid_payload")
            try:
                claim = claimed_plan(plan)
                claim_decisions = validate_commit_policy(claim, policies)
            except FoundryImportPlanError as exc:
                return _error(exc.code)

            replay = _existing_receipt(
                scenes, claim_decisions, plan_digest=plan_digest, operation_id=operation,
            )
            if replay == "partial":
                return _error("foundry_import_stale")
            if isinstance(replay, list):
                return {
                    "ok": True,
                    "idempotent": True,
                    "operation_id": operation,
                    "plan_digest": plan_digest,
                    "receipt": {
                        "operation_id": operation,
                        "plan_digest": plan_digest,
                        "source_version": claim["source_version"],
                        "revision": base_revision,
                        "items": replay,
                    },
                }

            try:
                refreshed = verify_and_refresh(
                    plan, plan_digest, scenes, base_revision=base_revision,
                )
                decisions = validate_commit_policy(refreshed, policies)
            except FoundryImportPlanError as exc:
                return _error(exc.code)

            items_by_id = {item["canonical_id"]: item for item in refreshed["items"]}
            revision = base_revision
            summaries: list[dict[str, Any]] = []
            written: dict[str, dict[str, Any]] = {}
            try:
                for decision in decisions:
                    target = _decision_target(decision, operation)
                    if target is None:
                        continue
                    item = items_by_id[decision["canonical_id"]]
                    op = _operation(
                        decision, item, target=target,
                        plan_digest=plan_digest, operation_id=operation,
                        source_version=refreshed["source_version"],
                    )
                    summary = tactical_scene.apply_op(instance, op, expected_revision=revision)
                    revision = summary["revision"]
                    summaries.append(summary)
                    written[decision["canonical_id"]] = {
                        "canonical_id": target,
                        "action": decision["action"],
                        "status": _STATUS_BY_ACTION[decision["action"]],
                    }
            except TacticalSceneOpError as exc:
                tactical_scene.restore(instance, before)
                code = (
                    "foundry_import_stale" if exc.code == "tactical_scene_stale_revision" else exc.code
                )
                return _error(str(code))
            try:
                if summaries:
                    await dependencies.save_instance(instance)
            except asyncio.CancelledError:
                tactical_scene.restore(instance, before)
                raise
            except Exception:
                tactical_scene.restore(instance, before)
                return _error("save_failed")
            try:
                final_scenes = tactical_scene.scenes(instance)
            except ModuleStateError:
                final_scenes = {}

            receipt = _receipt(
                decisions, written,
                plan_digest=plan_digest, operation_id=operation,
                source_version=refreshed["source_version"], revision=revision,
            )

    await _broadcast_commit(broadcast, game_key, instance, summaries, final_scenes)
    return {
        "ok": True,
        "idempotent": False,
        "operation_id": operation,
        "plan_digest": plan_digest,
        "revision": revision,
        "receipt": receipt,
    }


__all__ = [
    "FoundryImportDependencies",
    "MAX_IMPORT_BYTES",
    "commit",
    "preview",
]