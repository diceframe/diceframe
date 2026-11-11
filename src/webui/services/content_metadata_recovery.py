"""Owner-scoped undo for narrative world and character-card metadata edits."""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.webui.services.assistant_agent import _digest
from src.localization.messages import t

_MAX_SNAPSHOT_BYTES = 512 * 1024
_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_WORLD_FIELDS = frozenset({"name", "description", "language", "world_setting", "starter_scene"})
_CARD_FIELDS = frozenset({"character_name", "background", "race", "class"})


@dataclass(frozen=True)
class MetadataRecoveryDependencies:
    store: Any
    world_snapshot: Callable[[str], dict[str, Any]]
    world_update: Callable[[str, dict[str, Any]], dict[str, Any]]
    card_snapshot: Callable[[str], dict[str, Any]]
    card_update: Callable[[str, dict[str, Any]], dict[str, Any]]


class MetadataRecoveryService:
    def __init__(self, deps: MetadataRecoveryDependencies):
        self.deps = deps

    @staticmethod
    def _owner(owner: str) -> str:
        if not isinstance(owner, str) or not owner:
            raise PermissionError("Owner identity required")
        return _digest(owner)

    @staticmethod
    def _bounded(payload: dict[str, Any]) -> None:
        if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) > _MAX_SNAPSHOT_BYTES:
            raise ValueError(t("webui.services.content_metadata_recovery.snapshot_too_large"))

    @staticmethod
    def _snapshot(snapshot: Any, fields: frozenset[str]) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            raise ValueError(t("webui.services.content_metadata_recovery.no_safe_snapshot"))
        required = {"target_name", "fields", "version"}
        if set(snapshot) != required or not isinstance(snapshot["fields"], dict):
            raise ValueError(t("webui.services.content_metadata_recovery.snapshot_format_unsupported"))
        if set(snapshot["fields"]) - fields or not isinstance(snapshot["version"], str) or not snapshot["version"]:
            raise ValueError(t("webui.services.content_metadata_recovery.snapshot_field_or_version_invalid"))
        return deepcopy(snapshot)

    def _prepare(self, owner: str, kind: str, target_id: str, fields: dict[str, Any], before: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        owner_key = self._owner(owner)
        operation_id = uuid.uuid4().hex
        summary = {"target_name": str(before["target_name"]), "target_count": 1}
        payload = {
            "summary": summary,
            "target_kind": "world" if kind == "world_fields" else "card",
            "target_id": target_id,
            "fields": sorted(fields),
            "before": before,
        }
        self._bounded(payload)
        self.deps.store.record_lorebook_operation({
            "id": operation_id, "owner": owner_key, "kind": kind,
            "status": "prepared", "payload": payload,
        })
        return owner_key, operation_id, payload

    def _write(self, owner: str, target_kind: str, target_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        self._owner(owner)
        if not isinstance(patch, dict) or not patch:
            raise ValueError(t("webui.services.content_metadata_recovery.editable_field_required"))
        allowed = _WORLD_FIELDS if target_kind == "world" else _CARD_FIELDS
        if set(patch) - allowed:
            raise ValueError(t("webui.services.content_metadata_recovery.unsupported_field"))
        snapshot_fn = self.deps.world_snapshot if target_kind == "world" else self.deps.card_snapshot
        update_fn = self.deps.world_update if target_kind == "world" else self.deps.card_update
        before = self._snapshot(snapshot_fn(target_id), allowed)
        owner_key, operation_id, payload = self._prepare(
            owner, "world_fields" if target_kind == "world" else "card_fields", target_id, patch, before,
        )
        result = update_fn(target_id, deepcopy(patch))
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValueError(str(result.get("error") if isinstance(result, dict) else "元数据写入失败"))
        after = self._snapshot(snapshot_fn(target_id), allowed)
        payload["after"] = after
        self._bounded(payload)
        if not self.deps.store.update_lorebook_operation(operation_id, owner_key, {"status": "applied", "payload": payload}):
            raise ValueError(t("webui.services.content_metadata_recovery.save_record_uncertain"))
        return {"ok": True, "operation_id": operation_id, "reversible": True,
                "navigation": {"route": "worlds" if target_kind == "world" else "characters"}}

    def write_world(self, owner: str, target_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self._write(owner, "world", target_id, patch)

    def write_card(self, owner: str, target_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self._write(owner, "card", target_id, patch)

    def _operation(self, owner: str, operation_id: str) -> tuple[str, dict[str, Any]]:
        if not isinstance(operation_id, str) or not _ID_RE.fullmatch(operation_id):
            raise ValueError("Invalid metadata operation identity")
        operation = self.deps.store.get_lorebook_operation(operation_id, self._owner(owner))
        if operation is None or operation.get("status") != "applied":
            raise ValueError(t("webui.services.content_metadata_recovery.record_missing_or_handled"))
        if operation.get("kind") not in {"world_fields", "card_fields"}:
            raise ValueError(t("webui.services.content_metadata_recovery.unsupported_record"))
        payload = operation.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(t("webui.services.content_metadata_recovery.record_invalid"))
        return operation_id, payload

    def _current(self, payload: dict[str, Any]) -> dict[str, Any]:
        target_kind = payload.get("target_kind")
        fields = _WORLD_FIELDS if target_kind == "world" else _CARD_FIELDS if target_kind == "card" else frozenset()
        if not fields or not isinstance(payload.get("target_id"), str):
            raise ValueError(t("webui.services.content_metadata_recovery.target_invalid"))
        fn = self.deps.world_snapshot if target_kind == "world" else self.deps.card_snapshot
        current = self._snapshot(fn(payload["target_id"]), fields)
        # Known pre-expansion journals captured these three metadata fields.
        # Keep their version check and compare the same projection; undo still
        # writes only the journal's explicit field list.
        recorded = (payload.get('after') or {}).get('fields', {})
        legacy_fields = {'name', 'description', 'language'}
        if target_kind == 'world' and set(recorded) == legacy_fields:
            current['fields'] = {key: current['fields'][key] for key in legacy_fields}
        return current

    def recovery_preview(self, owner: str, operation_id: str) -> dict[str, Any]:
        _, payload = self._operation(owner, operation_id)
        current = self._current(payload)
        if current != payload.get("after"):
            raise ValueError(t("webui.services.content_metadata_recovery.content_changed_since_operation"))
        return {**deepcopy(payload["summary"]), "operation_id": operation_id,
                "target_kind": payload["target_kind"], "target_id": payload["target_id"],
                "fields": deepcopy(payload["fields"])}

    def recovery_guard(self, owner: str, operation_id: str) -> str:
        preview = self.recovery_preview(owner, operation_id)
        operation = self.deps.store.get_lorebook_operation(operation_id, self._owner(owner))
        return _digest({"preview": preview, "operation": operation})

    def undo(self, owner: str, operation_id: str) -> dict[str, Any]:
        owner_key = self._owner(owner)
        _, payload = self._operation(owner, operation_id)
        current = self._current(payload)
        if current != payload.get("after"):
            raise ValueError(t("webui.services.content_metadata_recovery.content_changed_since_operation"))
        undo_payload = deepcopy(payload)
        undo_payload["undo_prepared"] = True
        self._bounded(undo_payload)
        if not self.deps.store.update_lorebook_operation(operation_id, owner_key,
                                                         {"status": "prepared", "payload": undo_payload}):
            raise ValueError(t("webui.services.content_metadata_recovery.prepare_record_failed"))
        before_fields = payload["before"]["fields"]
        target_kind = payload["target_kind"]
        update_fn = self.deps.world_update if target_kind == "world" else self.deps.card_update
        result = update_fn(payload["target_id"], {key: deepcopy(before_fields[key]) for key in payload["fields"]})
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValueError(str(result.get("error") if isinstance(result, dict) else "恢复写入失败"))
        recovered_after = self._current(payload)
        undo_payload["recovered_after"] = recovered_after
        self._bounded(undo_payload)
        if not self.deps.store.update_lorebook_operation(operation_id, owner_key,
                                                         {"status": "undone", "payload": undo_payload}):
            raise ValueError(t("webui.services.content_metadata_recovery.save_result_uncertain"))
        return {"ok": True, "operation_id": operation_id,
                "navigation": {"route": "worlds" if target_kind == "world" else "characters"}}
