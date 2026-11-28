"""Pure FVTT Scene import-plan construction and commit policy (foundry-02).

Frozen source of truth: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.9
(import preview/commit contract), §6.6 (eight degradation classes), §13.4
(security tests), §8.3 (assets) and §8.4 (credentials). This module builds the
normalized plan
that a preview returns and a commit re-validates: it performs no IO, takes no
locks and never touches a ``GameInstance``.

A plan is a deterministic description of one import: per scene item it carries
the converted TacticalSceneDocument, its conflict marker against the current scene slot,
and a digest that binds the whole thing (including the slot revision the preview
saw). Any state change between preview and commit makes the recomputed digest
differ, so commit fails closed instead of applying a stale plan.

Boundaries:

- Read-only dependency on the frozen scene contract and the adapter's report
  vocabulary; no webui, engine or ruleset imports.
- Unknown or unstable payloads fail closed with a stable ``code``.
- Asset references are validated without being resolved: absolute paths, drive
  letters, parent traversal, backslashes and URI schemes (including ``data:``)
  are rejected rather than normalized.
- Credential-looking keys inside preserved ``extensions`` are scrubbed, never
  written into a scene.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Final

from src.tactical_scene.contract import TacticalSceneContractError, normalize_tactical_scene_payload

from .report import ConversionLevel

PLAN_SCHEMA_VERSION: Final = 1
ADAPTER_ID: Final = "compat.foundry.scene_adapter@1"

ACTION_CREATE: Final = "create"
ACTION_UPDATE: Final = "update"
ACTION_DUPLICATE: Final = "duplicate"
ACTION_SKIP: Final = "skip"
ACTIONS: Final = frozenset({ACTION_CREATE, ACTION_UPDATE, ACTION_DUPLICATE, ACTION_SKIP})

CONFLICT_EXISTING: Final = "existing"
CONFLICT_NEW: Final = "new"

CODE_PLAN_INVALID: Final = "foundry_import_plan_invalid"
CODE_STALE: Final = "foundry_import_stale"
CODE_UNSAFE_PAYLOAD: Final = "tactical_scene_invalid_payload"

_LEVELS: Final[frozenset[str]] = frozenset(level.value for level in ConversionLevel)
_OPERATION_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

_ASSET_KEYS: Final = frozenset({"src", "path", "source_path"})
_SCHEME: Final = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_DRIVE: Final = re.compile(r"^[A-Za-z]:")

_CREDENTIAL_KEYS: Final = frozenset({
    "password", "passwd", "token", "secret", "apikey", "api_key",
    "credential", "credentials", "access_token", "refresh_token",
    "session_token", "seat_token", "room_token",
    # The room_access scrub list (src/engine/modules/room_access.py) covers the
    # same shapes for save exports; an import must not carry them either.
    "room_password_hash", "room_tokens", "seat_credentials", "bot_bind_token",
})
_CREDENTIAL_SUFFIXES: Final = ("_password", "_passwd", "_token", "_secret", "_apikey", "_api_key")
# AGENTS.md: shared-provider credentials persist only under this prefix
# (secrets.json); they never belong in imported content.
_PROVIDER_SECRET_PREFIX: Final = "ai_provider_key_"


class FoundryImportPlanError(ValueError):
    """A plan cannot be built or re-validated; ``code`` is stable contract data."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _invalid(detail: str) -> None:
    raise FoundryImportPlanError(CODE_PLAN_INVALID, detail)


def _unsafe(detail: str) -> None:
    raise FoundryImportPlanError(CODE_UNSAFE_PAYLOAD, detail)


def _digest_canonical(value: Any) -> Any:
    """JSON-representation-independent form used only for digesting.

    A browser parses the plan response and serializes it again before commit;
    its single number type rewrites every integral float as an int
    (``360.0`` -> ``360``). The digest binds normalized content, not the
    transport encoding, so equal numbers hash the same either way.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {key: _digest_canonical(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_digest_canonical(child) for child in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(
        _digest_canonical(value),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- document preparation -------------------------------------------------


def _credential_key(key: str) -> bool:
    lowered = key.lower()
    return (
        lowered in _CREDENTIAL_KEYS
        or lowered.startswith(_PROVIDER_SECRET_PREFIX)
        or any(lowered.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES)
    )


def _scrub_node(value: Any, path: str, removed: list[str]) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and _credential_key(key):
                removed.append(child_path)
                continue
            clean[key] = _scrub_node(child, child_path, removed)
        return clean
    if isinstance(value, list):
        return [_scrub_node(child, f"{path}[{index}]", removed) for index, child in enumerate(value)]
    return deepcopy(value)


def _scrub_extensions(document: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    removed: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key in list(node):
                child_path = f"{path}.{key}"
                if key == "extensions" and isinstance(node[key], dict):
                    node[key] = _scrub_node(node[key], child_path, removed)
                else:
                    walk(node[key], child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(document, "scene")
    return document, sorted(set(removed))


def _unsafe_asset(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if text.startswith(("/", "\\", "~")):
        return "absolute path"
    if _DRIVE.match(text):
        return "drive-letter path"
    if "\\" in text:
        return "backslash path"
    if _SCHEME.match(text):
        return "URI scheme is not allowed (assets are never fetched or inlined)"
    if any(part == ".." for part in text.split("/")):
        return "parent traversal"
    return ""


def _reject_unsafe_assets(document: Mapping[str, Any]) -> None:
    problems: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                child_path = f"{path}.{key}"
                if key in _ASSET_KEYS:
                    if child is None:
                        continue
                    if not isinstance(child, str):
                        problems.append(f"{child_path}: asset path must be a string")
                    else:
                        reason = _unsafe_asset(child)
                        if reason:
                            problems.append(f"{child_path}: {reason}")
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(document, "scene")
    if problems:
        _unsafe("; ".join(sorted(problems)))


def _prepare_document(raw: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(raw, Mapping):
        _invalid("plan item document must be an object")
    try:
        document = normalize_tactical_scene_payload(deepcopy(raw))[0]
    except TacticalSceneContractError as exc:
        raise FoundryImportPlanError(CODE_PLAN_INVALID, f"document is invalid: {exc.detail}") from exc
    document, scrubbed = _scrub_extensions(document)
    _reject_unsafe_assets(document)
    return document, scrubbed


def sanitize_scene_document(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Normalize one scene document, scrub credentials and reject unsafe assets.

    Shared by the plan builder and the portable scene definition boundary so an
    imported document is sanitized by exactly one implementation. Returns the
    normalized document plus the credential paths that were removed.
    """

    return _prepare_document(raw)


def _report_dict(report: Any) -> dict[str, Any]:
    if hasattr(report, "to_dict"):
        data = report.to_dict()
    elif isinstance(report, Mapping):
        data = deepcopy(dict(report))
    else:
        _invalid("plan item report must be an object")
    if not isinstance(data, Mapping):
        _invalid("plan item report must be an object")
    data = dict(data)
    if data.get("level") not in _LEVELS:
        _invalid("plan item report has an unknown conversion level")
    if not isinstance(data.get("contains_script"), bool):
        _invalid("plan item report must declare contains_script")
    source_version = data.get("source_version")
    if not isinstance(source_version, str) or not source_version:
        _invalid("plan item report must declare its source_version")
    return data


# ---- conflict detection ---------------------------------------------------


def _existing_revision(raw: Any) -> int:
    revision = raw.get("revision", 0) if isinstance(raw, Mapping) else 0
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return 0
    return revision


def _existing_match(document: Mapping[str, Any], existing_scenes: Mapping[str, Any]) -> str | None:
    canonical = document.get("canonical_id")
    if isinstance(canonical, str) and canonical in existing_scenes:
        return canonical
    external = document.get("external_ids") or {}
    if not isinstance(external, Mapping) or not external:
        return None
    pairs = [
        (key, value) for key, value in external.items()
        if isinstance(key, str) and isinstance(value, str) and value
    ]
    if not pairs:
        return None
    # Match on the recorded identity value regardless of namespace: the same
    # FVTT document keeps its _id across versions, so a v13 payload must find a
    # scene first imported from v14 (workplan known-limitation 8).
    values = {value for _, value in pairs}
    for scene_id in sorted(existing_scenes, key=str):
        raw = existing_scenes[scene_id]
        if not isinstance(raw, Mapping):
            continue
        other = raw.get("external_ids")
        if not isinstance(other, Mapping) or not other:
            continue
        if any(
            isinstance(value, str) and value in values
            for value in other.values()
        ):
            return str(scene_id)
    return None


def _build_item(
    document: Mapping[str, Any],
    report: Mapping[str, Any],
    existing_scenes: Mapping[str, Any],
    *,
    source_canonical_id: str | None = None,
) -> dict[str, Any]:
    canonical = document["canonical_id"]
    target = _existing_match(document, existing_scenes)
    conflict = CONFLICT_EXISTING if target else CONFLICT_NEW
    stamped = document
    if target and target != canonical:
        # Match by external identity (e.g. a v13 payload for an existing v14
        # scene): the canonical identity is immutable, so the update targets it.
        stamped = deepcopy(dict(document))
        stamped["canonical_id"] = target
    return {
        "canonical_id": target or canonical,
        "source_canonical_id": source_canonical_id or canonical,
        "display_name": str(stamped.get("display_name") or ""),
        "external_ids": dict(stamped.get("external_ids") or {}),
        "conflict": conflict,
        "existing_revision": _existing_revision(existing_scenes[target]) if target else None,
        "level": report["level"],
        "contains_script": bool(report["contains_script"]),
        "report": deepcopy(dict(report)),
        "document": stamped,
    }


# ---- digests --------------------------------------------------------------


def _item_basis(item: Mapping[str, Any]) -> dict[str, Any]:
    document = item.get("document")
    if not isinstance(document, Mapping):
        _invalid("plan item document must be an object")
    return {
        "canonical_id": item.get("canonical_id"),
        "external_ids": dict(item.get("external_ids") or {}),
        "conflict": item.get("conflict"),
        "existing_revision": item.get("existing_revision"),
        "level": item.get("level"),
        "contains_script": item.get("contains_script"),
        "document": _sha256(_json(document)),
    }


def compute_plan_digest(plan: Mapping[str, Any]) -> str:
    """The digest of a normalized plan; any state or payload change moves it."""

    items = plan.get("items")
    if not isinstance(items, list):
        _invalid("plan items must be a list")
    basis = {
        "schema_version": plan.get("schema_version"),
        "adapter": plan.get("adapter"),
        "source_version": plan.get("source_version"),
        "base_revision": plan.get("base_revision"),
        "items": sorted((_item_basis(item) for item in items), key=lambda row: str(row["canonical_id"])),
    }
    return _sha256(_json(basis))


def _check_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid("base_revision must be a non-negative integer")
    return value


# ---- plan construction ----------------------------------------------------


def build_plan(
    entries: Iterable[tuple[Any, Any]],
    existing_scenes: Mapping[str, Any],
    *,
    base_revision: Any,
) -> dict[str, Any]:
    """Build the normalized import plan from ``(document, report)`` entries.

    ``existing_scenes`` is the scene slot mapping (canonical id -> persisted
    document). ``base_revision`` is the slot revision the preview observed.
    """

    _check_revision(base_revision)
    if not isinstance(existing_scenes, Mapping):
        _invalid("existing scenes must be a mapping")
    items: list[dict[str, Any]] = []
    scrubbed: list[str] = []
    versions: set[str] = set()
    for entry in entries:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            _invalid("each plan entry must be (document, report)")
        document, report = entry
        prepared, removed = _prepare_document(document)
        data = _report_dict(report)
        versions.add(data["source_version"])
        scrubbed.extend(removed)
        items.append(_build_item(prepared, data, existing_scenes))
    if not items:
        _invalid("plan has no items")
    if len(versions) != 1:
        _invalid("a plan cannot mix source versions")
    items.sort(key=lambda item: item["canonical_id"])
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "adapter": ADAPTER_ID,
        "source_version": versions.pop(),
        "base_revision": base_revision,
        "items": items,
        "scrubbed_fields": sorted(set(scrubbed)),
    }
    plan["plan_digest"] = compute_plan_digest(plan)
    return plan


def claimed_plan(plan: Any) -> dict[str, Any]:
    """Parse a plan returned to a client, without trusting its conflict markers."""

    if not isinstance(plan, Mapping):
        _invalid("plan must be an object")
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        _invalid("unsupported plan schema version")
    if plan.get("adapter") != ADAPTER_ID:
        _invalid("plan was produced by an unsupported adapter")
    base_revision = _check_revision(plan.get("base_revision"))
    source_version = plan.get("source_version")
    if not isinstance(source_version, str) or not source_version:
        _invalid("plan must declare its source_version")
    raw_items = plan.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        _invalid("plan has no items")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            _invalid("plan item must be an object")
        canonical = raw.get("canonical_id")
        if not isinstance(canonical, str) or not canonical:
            _invalid("plan item must declare its canonical_id")
        if canonical in seen:
            _invalid(f"duplicate canonical_id in plan: {canonical}")
        seen.add(canonical)
        document = raw.get("document")
        if not isinstance(document, Mapping):
            _invalid(f"plan item {canonical} must carry its document")
        report = _report_dict(raw.get("report"))
        conflict = raw.get("conflict")
        if conflict not in (CONFLICT_EXISTING, CONFLICT_NEW):
            _invalid(f"plan item {canonical} has an unknown conflict marker")
        existing_revision = raw.get("existing_revision")
        if conflict == CONFLICT_EXISTING:
            if isinstance(existing_revision, bool) or not isinstance(existing_revision, int) or existing_revision < 0:
                _invalid(f"plan item {canonical} must carry its existing_revision")
        elif existing_revision is not None:
            _invalid(f"plan item {canonical} is new but claims an existing revision")
        external = raw.get("external_ids", {})
        if not isinstance(external, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in external.items()
        ):
            _invalid(f"plan item {canonical} has invalid external_ids")
        source_canonical_id = raw.get("source_canonical_id")
        if not isinstance(source_canonical_id, str) or not source_canonical_id:
            source_canonical_id = canonical
        items.append({
            "canonical_id": canonical,
            "source_canonical_id": source_canonical_id,
            "display_name": str(document.get("display_name") or ""),
            "external_ids": dict(external),
            "conflict": conflict,
            "existing_revision": existing_revision,
            "level": report["level"],
            "contains_script": bool(report["contains_script"]),
            "report": report,
            "document": deepcopy(dict(document)),
        })
    items.sort(key=lambda item: item["canonical_id"])
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "adapter": ADAPTER_ID,
        "source_version": source_version,
        "base_revision": base_revision,
        "items": items,
    }


def verify_and_refresh(
    plan: Any,
    confirmed_digest: Any,
    existing_scenes: Mapping[str, Any],
    *,
    base_revision: Any,
) -> dict[str, Any]:
    """Re-validate a submitted plan against the current scene slot.

    The submitted payload must reproduce the digest it was given, and the plan
    rebuilt against current state must produce the same digest. Any difference
    means the preview is stale; the caller must show a fresh preview.
    """

    if not isinstance(confirmed_digest, str) or not confirmed_digest:
        raise FoundryImportPlanError(CODE_STALE, "plan_digest is required")
    if not isinstance(existing_scenes, Mapping):
        _invalid("existing scenes must be a mapping")
    claim = claimed_plan(plan)
    embedded = plan.get("plan_digest") if isinstance(plan, Mapping) else None
    if embedded is not None and embedded != confirmed_digest:
        raise FoundryImportPlanError(CODE_STALE, "the confirmed digest does not match the plan")
    if compute_plan_digest(claim) != confirmed_digest:
        raise FoundryImportPlanError(CODE_STALE, "the plan payload was modified after preview")

    _check_revision(base_revision)
    items: list[dict[str, Any]] = []
    scrubbed: list[str] = []
    for item in claim["items"]:
        prepared, removed = _prepare_document(item["document"])
        scrubbed.extend(removed)
        items.append(_build_item(
            prepared, item["report"], existing_scenes,
            source_canonical_id=item["source_canonical_id"],
        ))
    items.sort(key=lambda item: item["canonical_id"])
    refreshed: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "adapter": ADAPTER_ID,
        "source_version": claim["source_version"],
        "base_revision": base_revision,
        "items": items,
        "scrubbed_fields": sorted(set(scrubbed)),
    }
    digest = compute_plan_digest(refreshed)
    if digest != confirmed_digest:
        raise FoundryImportPlanError(
            CODE_STALE, "the scene state changed since the preview; review the new plan",
        )
    refreshed["plan_digest"] = digest
    return refreshed


# ---- commit policy --------------------------------------------------------


def validate_commit_policy(
    plan: Mapping[str, Any], policies: Any,
) -> list[dict[str, Any]]:
    """Validate one explicit policy per plan item and return ordered decisions."""

    if not isinstance(policies, Mapping):
        _invalid("policies must be an object")
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        _invalid("plan has no items")
    known = {item["canonical_id"] for item in items}
    unknown = sorted(str(key) for key in policies if key not in known)
    if unknown:
        _invalid(f"policy targets unknown plan items: {', '.join(unknown)}")
    decisions: list[dict[str, Any]] = []
    for item in items:
        canonical = item["canonical_id"]
        raw = policies.get(canonical)
        if not isinstance(raw, Mapping):
            _invalid(f"plan item {canonical} needs an explicit create/update/duplicate/skip policy")
        action = raw.get("action")
        if action not in ACTIONS:
            _invalid(f"plan item {canonical} has an unknown action")
        decision = {"canonical_id": canonical, "action": action, "expected_revision": None}
        if action == ACTION_CREATE and item["conflict"] != CONFLICT_NEW:
            _invalid(f"create would duplicate the existing scene {canonical}")
        if action == ACTION_UPDATE:
            if item["conflict"] != CONFLICT_EXISTING:
                _invalid(f"update requires an existing scene: {canonical}")
            expected = raw.get("expected_revision")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
                _invalid(f"update for {canonical} requires an integer expected_revision")
            if expected != item["existing_revision"]:
                raise FoundryImportPlanError(CODE_STALE, f"scene {canonical} changed since the preview")
            decision["expected_revision"] = expected
        decisions.append(decision)
    return decisions


def validate_operation_id(operation_id: Any) -> str:
    if not isinstance(operation_id, str) or not _OPERATION_ID.match(operation_id):
        _invalid("operation_id must be a stable identifier (letters, digits, _ . : -)")
    return operation_id


def duplicate_canonical_id(source_canonical_id: Any, operation_id: Any) -> str:
    """Deterministic identity for a duplicated scene, so a retry is idempotent."""

    if not isinstance(source_canonical_id, str) or not source_canonical_id:
        _invalid("duplicate requires a source canonical_id")
    validate_operation_id(operation_id)
    digest = hashlib.sha256(
        f"{source_canonical_id}:{operation_id}".encode("utf-8")
    ).hexdigest()[:16]
    return f"import.dup.{digest}"


__all__ = [
    "ACTION_CREATE",
    "ACTION_DUPLICATE",
    "ACTION_SKIP",
    "ACTION_UPDATE",
    "ACTIONS",
    "ADAPTER_ID",
    "CODE_PLAN_INVALID",
    "CODE_STALE",
    "CODE_UNSAFE_PAYLOAD",
    "CONFLICT_EXISTING",
    "CONFLICT_NEW",
    "FoundryImportPlanError",
    "PLAN_SCHEMA_VERSION",
    "build_plan",
    "claimed_plan",
    "compute_plan_digest",
    "duplicate_canonical_id",
    "sanitize_scene_document",
    "validate_commit_policy",
    "validate_operation_id",
    "verify_and_refresh",
]