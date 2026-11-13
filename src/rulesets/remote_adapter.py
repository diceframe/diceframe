"""Minimal transport-backed RulesetRuntime adapter from the R00 experiment.

The transport is injected. This adapter does not choose process IPC, sockets,
containers, or a public SDK; those decisions remain later experiments.
"""

from __future__ import annotations

import secrets
import json
from copy import deepcopy
from typing import Any, Callable

from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.runtime_probe import RuntimeProbeClient, RuntimeProbeError, RuntimeProbeLedger


MAX_EVENT_LEDGER_ROWS = 4096
MAX_EVENT_BATCH_ID_LENGTH = 200


class RemoteRulesetRuntimeAdapter:
    def __init__(
        self,
        runtime_id: str,
        runtime_version: int,
        capabilities: RulesetCapabilities,
        transport: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        timeout_seconds: float = 5.0,
        ledger_path: Any = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not runtime_id or isinstance(runtime_version, bool) or runtime_version < 1:
            raise ValueError("remote runtime identity is invalid")
        self.runtime_id = runtime_id
        self.runtime_version = runtime_version
        self.capabilities = capabilities
        self._request_id_factory = request_id_factory or (lambda: secrets.token_urlsafe(18))
        self._probe = RuntimeProbeClient(
            transport,
            timeout_seconds=timeout_seconds,
            ledger=RuntimeProbeLedger(ledger_path) if ledger_path is not None else None,
        )

    def close(self) -> None:
        """Release an injected transport when its owning runtime is revoked."""

        closer = getattr(self._probe.transport, "close", None)
        if callable(closer):
            closer()

    def __enter__(self) -> "RemoteRulesetRuntimeAdapter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _call(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        live_state: Any = None,
        request_id: str | None = None,
        receipt_payload: dict[str, Any] | None = None,
    ) -> Any:
        request = {
            "protocol_version": 1,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "request_id": request_id or self._request_id_factory(),
            "operation": operation,
            "payload": payload,
        }
        receipt_request = None
        if receipt_payload is not None:
            receipt_request = {**request, "payload": receipt_payload}
        response = self._probe.call_sync(
            request, live_state=live_state, receipt_request=receipt_request,
        )
        if response.get("ok") is False:
            raise RuntimeProbeError(str(response.get("error") or "remote runtime rejected request"))
        if "result" not in response:
            raise RuntimeProbeError("remote runtime response has no result")
        return response["result"]

    @staticmethod
    def _dto(value: Any, field: str) -> Any:
        if isinstance(value, dict):
            result = deepcopy(value)
        elif isinstance(value, (list, tuple, str, int, float, bool)) or value is None:
            result = deepcopy(value)
        elif callable(getattr(value, "getstate", None)):
            result = {"state": value.getstate()}
        elif callable(getattr(value, "to_dict", None)):
            result = value.to_dict()
        else:
            raise RuntimeProbeError(f"{field} is not JSON DTO compatible")
        try:
            result = json.loads(json.dumps(result, ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise RuntimeProbeError(f"{field} is not JSON serializable") from exc
        return result

    def describe_experience(self, rule: Any, locale: str) -> dict[str, Any]:
        return self._call("describe_experience", {"rule": rule, "locale": locale})

    def builder_choices(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]:
        return self._call("builder_choices", {"rule": rule, "draft": draft})

    def validate_character(self, rule: Any, draft: dict[str, Any]) -> list[str]:
        result = self._call("validate_character", {"rule": rule, "draft": draft})
        if not isinstance(result, list):
            raise RuntimeProbeError("remote validate_character result must be a list")
        return [str(item) for item in result]

    def derive_character(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]:
        return self._call("derive_character", {"rule": rule, "draft": draft})

    def finalize_character(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]:
        return self._call("finalize_character", {"rule": rule, "draft": draft})

    def normalize_character_submission(
        self, rule: Any, character: dict[str, Any], locale: str = "",
    ) -> dict[str, Any]:
        return self._call(
            "normalize_character_submission",
            {"rule": rule, "character": character, "locale": locale},
        )

    def available_intents(self, instance: Any, actor_id: str) -> list[dict[str, Any]]:
        result = self._call("available_intents", {"instance": self._dto(instance, "instance"), "actor_id": actor_id})
        if not isinstance(result, list):
            raise RuntimeProbeError("remote available_intents result must be a list")
        return result

    def prepare_intent_submission(
        self, intent: dict[str, Any], requester_id: str, requester_is_gm: bool,
    ) -> dict[str, Any]:
        result = self._call(
            "prepare_intent_submission",
            {"intent": intent, "requester_id": requester_id, "requester_is_gm": requester_is_gm},
            request_id=(f"prepare:{intent['intent_id']}"
                        if isinstance(intent.get("intent_id"), str) and intent["intent_id"] else None),
            receipt_payload={
                "intent": deepcopy(intent),
                "requester_id": requester_id,
                "requester_is_gm": requester_is_gm,
            },
        )
        if not isinstance(result, dict):
            raise RuntimeProbeError("remote prepare_intent_submission result must be an object")
        return result

    def validate_intent(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        return self._call("validate_intent", {"instance": self._dto(instance, "instance"), "intent": intent})

    def resolve_intent(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        return self._call(
            "resolve_intent",
            {"instance": self._dto(instance, "instance"), "intent": intent, "rng": self._dto(rng, "rng")},
            request_id=(f"intent:{intent['intent_id']}"
                        if isinstance(intent.get("intent_id"), str) and intent["intent_id"] else None),
            receipt_payload={"intent": deepcopy(intent)},
        )

    def apply_event_batch(self, instance: Any, batch: dict[str, Any]) -> dict[str, Any]:
        instance_dto = self._dto(instance, "instance")
        result = self._call(
            "apply_event_batch",
            {"instance": instance_dto, "batch": batch},
            live_state=instance_dto,
            request_id=(f"batch:{batch['batch_id']}"
                        if isinstance(batch.get("batch_id"), str) and batch["batch_id"] else None),
            receipt_payload={"batch": deepcopy(batch)},
        )
        if not isinstance(result, dict):
            raise RuntimeProbeError("remote apply_event_batch result must be an object")
        # R02 state bridge: an external runtime may return its opaque state and
        # append-only ledger as JSON DTOs.  The host writes them through the
        # existing ruleset_runtime module owner; it never accepts a live object
        # or an arbitrary instance patch from the transport.
        if "state" in result or "event_ledger" in result:
            state, ledger = self._validate_state_bridge(instance, batch, result)
            from src.engine.modules import ruleset_runtime

            binding = ruleset_runtime.binding(instance)
            if str(binding.get("id") or "") != self.runtime_id:
                raise RuntimeProbeError("remote event batch runtime binding mismatch")
            # Validation above is deliberately complete before either owner
            # slot is touched.  A malformed child response therefore cannot
            # leave state and ledger half-updated.
            ruleset_runtime.replace_state(instance, state)
            ruleset_runtime.replace_event_ledger(instance, ledger)
            return {key: deepcopy(value) for key, value in result.items()
                    if key not in {"state", "event_ledger"}}
        return result

    def _validate_state_bridge(
        self, instance: Any, batch: dict[str, Any], result: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Validate an opaque state/ledger handoff before mutating host state.

        The child owns the opaque contents, but it may only append to the
        host's already persisted ledger.  This keeps restart recovery and
        replay deterministic without allowing a runtime process to erase or
        reorder another owner's history.
        """

        state = result.get("state")
        ledger = result.get("event_ledger")
        if not isinstance(state, dict) or not isinstance(ledger, list):
            raise RuntimeProbeError("remote event batch state bridge is invalid")
        state = self._dto(state, "remote state")
        ledger = self._dto(ledger, "remote event ledger")
        if not isinstance(state, dict) or not isinstance(ledger, list):
            raise RuntimeProbeError("remote event batch state bridge is invalid")
        if len(ledger) > MAX_EVENT_LEDGER_ROWS:
            raise RuntimeProbeError("remote event ledger is too large")
        ids: list[str] = []
        for row in ledger:
            if not isinstance(row, dict):
                raise RuntimeProbeError("remote event ledger row must be an object")
            batch_id = str(row.get("batch_id") or "").strip()
            if not batch_id or len(batch_id) > MAX_EVENT_BATCH_ID_LENGTH:
                raise RuntimeProbeError("remote event ledger row has an invalid batch id")
            if batch_id in ids:
                raise RuntimeProbeError("remote event ledger contains duplicate batch ids")
            ids.append(batch_id)

        from src.engine.modules import ruleset_runtime

        current = ruleset_runtime.event_ledger(instance)
        if not isinstance(current, list) or len(current) > MAX_EVENT_LEDGER_ROWS:
            raise RuntimeProbeError("host event ledger is invalid")
        current_ids: list[str] = []
        for row in current:
            if not isinstance(row, dict):
                raise RuntimeProbeError("host event ledger row is invalid")
            row_id = str(row.get("batch_id") or "").strip()
            if not row_id or row_id in current_ids:
                raise RuntimeProbeError("host event ledger has invalid batch ids")
            current_ids.append(row_id)
        if ids[: len(current_ids)] != current_ids:
            raise RuntimeProbeError("remote event ledger must preserve host history")
        if len(ids) > len(current_ids):
            expected_batch_id = str(batch.get("batch_id") or "").strip()
            if expected_batch_id and ids[-1] != expected_batch_id:
                raise RuntimeProbeError("remote event ledger appended the wrong batch")
        return deepcopy(state), deepcopy(ledger)

    def memory_deltas_from_event_batch(
        self, batch: dict[str, Any], instance: Any,
    ) -> list[dict[str, Any]]:
        result = self._call(
            "memory_deltas_from_event_batch",
            {"batch": batch, "instance": self._dto(instance, "instance")},
        )
        if not isinstance(result, list):
            raise RuntimeProbeError("remote memory deltas result must be a list")
        return result

    def gameplay_view(
        self, instance: Any, viewer_id: str = "", viewer_is_gm: bool = False,
    ) -> dict[str, Any]:
        return self._call(
            "gameplay_view",
            {"instance": self._dto(instance, "instance"), "viewer_id": viewer_id, "viewer_is_gm": viewer_is_gm},
        )

    def build_llm_view(self, instance: Any) -> dict[str, Any]:
        return self._call("build_llm_view", {"instance": self._dto(instance, "instance")})

    def project_legacy_character(self, character: dict[str, Any]) -> dict[str, Any]:
        return self._call("project_legacy_character", {"character": character})

    def migrate_state(self, payload: dict[str, Any], from_version: int) -> dict[str, Any]:
        return self._call("migrate_state", {"payload": payload, "from_version": from_version})
