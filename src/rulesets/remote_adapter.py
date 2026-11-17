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
from src.rulesets import remote_event_receipts
from src.rulesets.runtime_probe import RuntimeProbeClient, RuntimeProbeError, RuntimeProbeLedger


MAX_EVENT_LEDGER_ROWS = remote_event_receipts.MAX_EVENT_LEDGER_ROWS
MAX_EVENT_BATCH_ID_LENGTH = remote_event_receipts.MAX_EVENT_BATCH_ID_LENGTH
MAX_SHEET_BRIDGE_ROWS = 64
MAX_CHARACTER_SHEET_BYTES = 262_144


def _game_scope(instance: Any) -> str:
    """收据请求身份里的服务器权威游戏/Run 隔离段（P1-03；复审 P1-01 收紧）。

    生产入口传入真实的 GameInstance 聚合（game_key / run_id 属性）；测试与
    DTO 路径允许顶层携带这两个键的字典。缺失身份时 fail closed
    （RuntimeProbeError）——绝不静默落到跨游戏共享的 unknown 桶。
    """
    if isinstance(instance, dict):
        game_key = instance.get("game_key")
        run_id = str(instance.get("run_id") or "")
    else:
        game_key = getattr(instance, "game_key", None)
        run_id = str(getattr(instance, "run_id", "") or "")
    parts = [str(part) for part in (game_key if isinstance(game_key, (list, tuple)) else [])]
    if run_id:
        parts.append(run_id)
    scope = "|".join(part for part in parts if part)
    if not scope:
        raise RuntimeProbeError(
            "external ruleset receipts require server-authoritative game identity "
            "(game_key and run_id on the instance)",
        )
    return scope


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
        adventure_resolver: Any = None,
    ) -> None:
        if not runtime_id or isinstance(runtime_version, bool) or runtime_version < 1:
            raise ValueError("remote runtime identity is invalid")
        self.runtime_id = runtime_id
        self.runtime_version = runtime_version
        self.capabilities = capabilities
        self._request_id_factory = request_id_factory or (lambda: secrets.token_urlsafe(18))
        self._adventure_resolver = adventure_resolver
        self._probe = RuntimeProbeClient(
            transport,
            timeout_seconds=timeout_seconds,
            ledger=RuntimeProbeLedger(ledger_path) if ledger_path is not None else None,
        )

    def set_adventure_resolver(self, resolver: Any) -> None:
        """Adopt the application's single AdventureResolver (composition injection).

        The registry pushes this after construction, and again for runtimes that
        are registered later (plugins enabled at runtime), so a projection never
        depends on startup order.
        """

        self._adventure_resolver = resolver

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
        result: Any
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

    @staticmethod
    def _rule_dto(rule: Any) -> Any:
        """Send the rule's template, never the host's rule object.

        ``RuleSystem`` is a host-side behaviour object with no wire form; its
        ``template`` is the canonical JSON the child is allowed to read.  Call
        sites pass the object, so the unwrapping belongs here rather than in
        every service that calls a remote runtime.
        """

        template = getattr(rule, "template", None)
        return RemoteRulesetRuntimeAdapter._dto(
            rule if template is None else template, "rule",
        )

    def _instance_dto(self, instance: Any) -> dict[str, Any]:
        """Serialize the instance, plus the adventure graph when it is needed.

        The persisted instance shape is the wire contract for every runtime, so
        the projection has to ride along with it rather than in a sibling field:
        the child rebuilds an instance from this object and nothing else.
        """

        dto = self._dto(instance, "instance")
        if isinstance(dto, dict):
            modules = dto.get("modules")
            slot = modules.get("ruleset_runtime") if isinstance(modules, dict) else None
            if isinstance(slot, dict) and "event_ledger" in slot:
                slot["event_ledger"] = remote_event_receipts.child_ledger(slot["event_ledger"])
        if not isinstance(dto, dict) or not self.capabilities.adventure_projection:
            return dto
        if self._adventure_resolver is None:
            return dto
        from src.rulesets.adventure_projection import project_bound_adventure

        projection = project_bound_adventure(
            instance, self._adventure_resolver,
            locale=str(getattr(instance, "language", "") or ""),
        )
        if projection:
            dto["adventure"] = projection
        return dto

    def game_binding(self, rule: Any, locale: str) -> dict[str, Any]:
        """Name the runtime that owns this rule's versioned save state.

        ``VersionedStateRuntime`` requires the binding before any ruleset state
        is written, so a remote runtime that declares ``versioned_state`` must
        answer it exactly like a built-in one.  The host keeps transaction,
        save and upgrade ownership; the runtime only states its own identity
        and schema version (ADR-0014 §4).
        """

        result = self._call("game_binding", {"rule": self._rule_dto(rule), "locale": locale})
        if not isinstance(result, dict):
            raise RuntimeProbeError("remote game_binding result must be an object")
        return result

    @staticmethod
    def _rule_dto(rule: Any) -> Any:
        """Send the rule's template, never the host's rule object.

        ``RuleSystem`` is a host-side behaviour object with no wire form; its
        ``template`` is the canonical JSON the child is allowed to read.  Call
        sites pass the object, so the unwrapping belongs here rather than in
        every service that calls a remote runtime.
        """

        template = getattr(rule, "template", None)
        return RemoteRulesetRuntimeAdapter._dto(
            rule if template is None else template, "rule",
        )

    def game_binding(self, rule: Any, locale: str) -> dict[str, Any]:
        """Name the runtime that owns this rule's versioned save state.

        ``VersionedStateRuntime`` requires the binding before any ruleset state
        is written, so a remote runtime that declares ``versioned_state`` must
        answer it exactly like a built-in one.  The host keeps transaction,
        save and upgrade ownership; the runtime only states its own identity
        and schema version (ADR-0014 §4).
        """

        result = self._call("game_binding", {"rule": self._rule_dto(rule), "locale": locale})
        if not isinstance(result, dict):
            raise RuntimeProbeError("remote game_binding result must be an object")
        return result

    def describe_experience(self, rule: Any, locale: str) -> dict[str, Any]:
        return self._call("describe_experience", {"rule": self._rule_dto(rule), "locale": locale})

    def builder_choices(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]:
        return self._call("builder_choices", {"rule": self._rule_dto(rule), "draft": draft})

    def validate_character(self, rule: Any, draft: dict[str, Any]) -> list[str]:
        result = self._call("validate_character", {"rule": self._rule_dto(rule), "draft": draft})
        if not isinstance(result, list):
            raise RuntimeProbeError("remote validate_character result must be a list")
        return [str(item) for item in result]

    def derive_character(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]:
        return self._call("derive_character", {"rule": self._rule_dto(rule), "draft": draft})

    def finalize_character(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]:
        return self._call("finalize_character", {"rule": self._rule_dto(rule), "draft": draft})

    def normalize_character_submission(
        self, rule: Any, character: dict[str, Any], locale: str = "",
    ) -> dict[str, Any]:
        return self._call(
            "normalize_character_submission",
            {"rule": self._rule_dto(rule), "character": character, "locale": locale},
        )

    def available_intents(self, instance: Any, actor_id: str) -> list[dict[str, Any]]:
        result = self._call("available_intents", {"instance": self._instance_dto(instance), "actor_id": actor_id})
        if not isinstance(result, list):
            raise RuntimeProbeError("remote available_intents result must be a list")
        return result

    def next_automatic_intent(self, instance: Any) -> dict[str, Any] | None:
        """Return one server-owned automatic intent, or ``None`` when done.

        ``AutomaticIntentRuntime`` drives enemies, companions and AI-hosted
        seats without asking the model for a rules action.  A remote runtime
        opts in with ``capabilities.automatic_intents`` before this optional
        RPC is sent. Without that declaration there is no automatic ladder,
        even though this adapter structurally satisfies the protocol. Once
        declared, RPC errors must propagate so the host can reject/roll back
        the submission; only a successful null response ends the ladder.
        The host resolves returned intents through the normal
        ``resolve_intent`` / ``apply_event_batch`` chain.
        """

        if not self.capabilities.automatic_intents:
            return None
        result = self._call(
            "next_automatic_intent", {"instance": self._instance_dto(instance)},
        )
        if result is None:
            return None
        if not isinstance(result, dict):
            raise RuntimeProbeError(
                "remote next_automatic_intent result must be an object or null"
            )
        return result

    def prepare_intent_submission(
        self, intent: dict[str, Any], requester_id: str, requester_is_gm: bool,
        instance: Any,
    ) -> dict[str, Any]:
        result = self._call(
            "prepare_intent_submission",
            {"intent": intent, "requester_id": requester_id, "requester_is_gm": requester_is_gm},
            request_id=(f"prepare:{_game_scope(instance)}:{intent['intent_id']}"
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
        return self._call("validate_intent", {"instance": self._instance_dto(instance), "intent": intent})

    def validate_sequential_plan(
        self, instance: Any, plan: dict[str, Any],
        requester_id: str, requester_is_gm: bool,
    ) -> dict[str, Any]:
        result = self._call(
            "validate_sequential_plan",
            {"instance": self._instance_dto(instance), "plan": plan,
             "requester_id": requester_id, "requester_is_gm": requester_is_gm},
            request_id=(f"seq-validate:{_game_scope(instance)}:{plan['intent_id']}"
                        if isinstance(plan.get("intent_id"), str) and plan["intent_id"] else None),
            receipt_payload={
                "plan": deepcopy(plan),
                "requester_id": requester_id,
                "requester_is_gm": requester_is_gm,
            },
        )
        if not isinstance(result, dict):
            raise RuntimeProbeError("remote sequential plan validation must be an object")
        return result

    def resolve_sequential_step(
        self, instance: Any, step: dict[str, Any], rng: Any, plan_id: str = "",
    ) -> dict[str, Any]:
        # 复审 P1-01：步骤 ID（step-1/step-2）只在单个计划内唯一——收据身份
        # 必须携带父计划的 intent_id，否则同局连续两个计划会互串步骤结果。
        return self._call(
            "resolve_sequential_step",
            {"instance": self._instance_dto(instance), "step": step,
             "rng": self._dto(rng, "rng"), "plan_id": plan_id},
            request_id=(f"seq-resolve:{_game_scope(instance)}:{plan_id}:{step['step_id']}"
                        if isinstance(step.get("step_id"), str) and step["step_id"] else None),
            receipt_payload={"plan_id": plan_id, "step": deepcopy(step)},
        )

    def resolve_intent(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        return self._call(
            "resolve_intent",
            {"instance": self._instance_dto(instance), "intent": intent, "rng": self._dto(rng, "rng")},
            request_id=(f"intent:{_game_scope(instance)}:{intent['intent_id']}"
                        if isinstance(intent.get("intent_id"), str) and intent["intent_id"] else None),
            receipt_payload={"intent": deepcopy(intent)},
        )

    def resolve_narrative_checks(
        self,
        instance: Any,
        requests: list[dict[str, Any]],
        rng: Any = None,
    ) -> list[dict[str, Any]]:
        """Ask the runtime to roll free-text checks the core cannot roll."""

        result = self._call(
            "resolve_narrative_checks",
            {
                "instance": self._instance_dto(instance),
                "requests": [deepcopy(item) for item in requests],
                "rng": self._dto(rng, "rng"),
            },
        )
        if not isinstance(result, list):
            raise RuntimeProbeError("remote resolve_narrative_checks result must be a list")
        return result

    def apply_event_batch(self, instance: Any, batch: dict[str, Any]) -> dict[str, Any]:
        from src.engine.modules import ruleset_runtime

        remote_event_receipts.batch_identity(batch)
        scope = _game_scope(instance)
        ruleset_runtime.require_writable(instance)
        if ruleset_runtime.bound_runtime_id(instance) != self.runtime_id:
            raise RuntimeProbeError("remote event batch runtime binding mismatch")
        # Read without materializing/repairing any live module on rejection.
        slot = instance.modules.get("ruleset_runtime", {})
        current = deepcopy(slot.get("event_ledger", []))
        replayed = remote_event_receipts.replay(
            current, batch, self.runtime_id, self.runtime_version, scope,
        )
        if replayed is not None:
            return replayed
        instance_dto = self._instance_dto(instance)
        result = self._call(
            "apply_event_batch",
            {"instance": instance_dto, "batch": batch},
            live_state=instance_dto,
            request_id=f"batch:{scope}:{batch['batch_id']}",
            # A transport success is not an application receipt. After rollback
            # it may be reused only against the very same aggregate pre-image.
            receipt_payload={"batch": deepcopy(batch), "base": remote_event_receipts.digest(instance_dto)},
        )
        if not isinstance(result, dict):
            raise RuntimeProbeError("remote apply_event_batch result must be an object")
        # R02 state bridge: an external runtime may return its opaque state and
        # append-only ledger as JSON DTOs.  The host writes them through the
        # existing ruleset_runtime module owner; it never accepts a live object
        # or an arbitrary instance patch from the transport.
        bridge_keys = {"state", "event_ledger", "character_sheets"}
        if bridge_keys.intersection(result):
            state, ledger = self._validate_state_bridge(instance, batch, result)
            sheets = self._validate_sheet_bridge(instance, result)
            public_result = {key: deepcopy(value) for key, value in result.items()
                             if key not in bridge_keys}
            # A rejected/no-op child must not smuggle a post-image into a new
            # ledger version. Historical replays were already handled above.
            if result.get("ok") is False or result.get("applied") is False or result.get("replayed") is True:
                raise RuntimeProbeError("remote bridge did not apply a new batch")
            ledger = remote_event_receipts.record(
                current, ledger, batch, public_result,
                self.runtime_id, self.runtime_version, scope,
            )
            # Validation above is deliberately complete before any owner slot
            # is touched.  A malformed child response therefore cannot leave
            # state, ledger and sheets half-updated.
            if state is not None:
                ruleset_runtime.replace_state(instance, state)
            if ledger is not None:
                ruleset_runtime.replace_event_ledger(instance, ledger)
            for uid, sheet in sheets.items():
                instance.set_character_sheet(uid, sheet)
            return public_result
        return result

    def _validate_sheet_bridge(
        self, instance: Any, result: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Validate an authoritative runtime's character-sheet handoff.

        A runtime that keeps ``hp``/``mp``/inventory inside the sheet it was
        given must be able to hand the mutated sheet back: the process owns the
        rules, the host owns persistence.  The bridge is deliberately narrow—
        only seats that already exist on the roster, only their
        ``character_sheet`` slot, whole-sheet replacement, bounded size—so a
        broken or hostile runtime cannot invent, delete or re-parent a player.
        """

        raw = result.get("character_sheets")
        if raw is None:
            return {}
        if not isinstance(raw, dict) or len(raw) > MAX_SHEET_BRIDGE_ROWS:
            raise RuntimeProbeError("remote character sheet bridge is invalid")
        players = getattr(instance, "players", None)
        if not isinstance(players, dict):
            raise RuntimeProbeError("remote character sheet bridge needs a roster")
        sheets: dict[str, dict[str, Any]] = {}
        for uid, sheet in raw.items():
            if (not isinstance(uid, str) or not uid or uid not in players
                    or not isinstance(players[uid], dict)):
                raise RuntimeProbeError("remote character sheet bridge names an unknown seat")
            if not isinstance(sheet, dict):
                raise RuntimeProbeError("remote character sheet must be an object")
            payload = self._dto(sheet, "remote character sheet")
            if not isinstance(payload, dict):
                raise RuntimeProbeError("remote character sheet must be an object")
            try:
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise RuntimeProbeError("remote character sheet is not JSON serializable") from exc
            if len(encoded) > MAX_CHARACTER_SHEET_BYTES:
                raise RuntimeProbeError("remote character sheet is too large")
            sheets[uid] = payload
        return sheets

    def _validate_state_bridge(
        self, instance: Any, batch: dict[str, Any], result: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
        """Validate an opaque state/ledger handoff before mutating host state.

        The child owns the opaque contents, but it may only append to the
        host's already persisted ledger.  This keeps restart recovery and
        replay deterministic without allowing a runtime process to erase or
        reorder another owner's history.

        A runtime that owns no versioned state answers with neither key; that
        is not an error, it simply has nothing to hand back.
        """

        state = result.get("state")
        ledger = result.get("event_ledger")
        if state is None and ledger is None:
            return None, None
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
            if remote_event_receipts.RECEIPT_KEY in row:
                raise RuntimeProbeError("remote event ledger contains reserved host receipt metadata")
            batch_id = str(row.get("batch_id") or "").strip()
            if not batch_id or len(batch_id) > MAX_EVENT_BATCH_ID_LENGTH:
                raise RuntimeProbeError("remote event ledger row has an invalid batch id")
            if batch_id in ids:
                raise RuntimeProbeError("remote event ledger contains duplicate batch ids")
            ids.append(batch_id)

        current = remote_event_receipts.child_ledger(
            instance.modules.get("ruleset_runtime", {}).get("event_ledger", []),
        )
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
        # 复审 P1-02：历史前缀必须与宿主持久账本**逐字段一致**——ID 不变而
        # 内容被改写同样属于篡改，拒绝写入任何状态。（状态对宿主是不透明
        # DTO，"无事件却推进状态"无法在桥上判定，由运行时侧的版本化状态
        # 契约与存档投影负责。）
        if ledger[: len(current)] != deepcopy(current):
            raise RuntimeProbeError("remote event ledger rewrote persisted history")
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
            {"batch": batch, "instance": self._instance_dto(instance)},
        )
        if not isinstance(result, list):
            raise RuntimeProbeError("remote memory deltas result must be a list")
        return result

    def gameplay_view(
        self, instance: Any, viewer_id: str = "", viewer_is_gm: bool = False,
    ) -> dict[str, Any]:
        return self._call(
            "gameplay_view",
            {"instance": self._instance_dto(instance), "viewer_id": viewer_id, "viewer_is_gm": viewer_is_gm},
        )

    def build_llm_view(self, instance: Any) -> dict[str, Any]:
        return self._call("build_llm_view", {"instance": self._instance_dto(instance)})

    def project_legacy_character(self, character: dict[str, Any]) -> dict[str, Any]:
        return self._call("project_legacy_character", {"character": character})

    def migrate_state(self, payload: dict[str, Any], from_version: int) -> dict[str, Any]:
        return self._call("migrate_state", {"payload": payload, "from_version": from_version})
