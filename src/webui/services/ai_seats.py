"""Resolve seat policy against the live provider catalog; never copy credentials."""
from __future__ import annotations
from src.engine.modules import progression_state, ruleset_runtime

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.ai_providers import resolve_provider
from src.engine import module_hooks
from src.engine.game_instance import GameState
from src.engine.modules.ai_seats import (
    STYLES, configuration_token, effective_settings, record_outcome, restore_settings, settings_state, update_settings, validate_settings,
)
from src.engine.player_control import get_control
from src.llm.client import LLMClient, ProviderConfig
from src.llm.seat_policy import SeatGeneration
from src.network_proxy import effective_proxy_url

STYLE_PROMPTS = {
    "character": "Follow the character's established personality and motives.",
    "concise": "Be concise: express one concrete attempt in one short sentence.",
    "cautious": "Prefer cautious approaches when consistent with the character and known facts.",
    "bold": "Prefer bold approaches when consistent with the character and known facts.",
    "supportive": "Prefer supporting the group when consistent with the character and known facts.",
}


class AISeatRuntime:
    """Live config resolver and ephemeral generation state, injected at composition."""

    def __init__(self, *, config_state: Callable[[], dict], client_factory: Callable = LLMClient):
        self._config_state = config_state
        self._client_factory = client_factory
        self._generating: dict[tuple[str, str], int] = {}

    def _resolve(self, instance: Any, uid: str) -> tuple[dict, dict | None]:
        try:
            policy = effective_settings(instance, uid)
        except ValueError:
            return {"ready": False, "reason": "unsupported_settings"}, None
        return self._resolve_policy(policy)

    def _resolve_policy(self, policy: dict) -> tuple[dict, dict | None]:
        config = self._config_state()
        ref = policy["provider_ref"] or str(config.get("llm_provider_ref") or "")
        model = policy["model"]
        if model is None and ref == config.get("llm_provider_ref"):
            model = str(config.get("model") or "")
        entry = next((p for p in config.get("ai_providers", [])
                      if isinstance(p, dict) and p.get("id") == ref), None)
        connection = resolve_provider(config, ref)
        reason = ""
        if not entry or not connection:
            reason = "provider_missing"
        elif not connection["base_url"]:
            reason = "provider_unconfigured"
        elif not model:
            reason = "model_required"
        elif entry.get("models") and model not in entry["models"]:
            reason = "model_missing"
        elif entry.get("model_capabilities", {}).get(model, "chat") != "chat":
            reason = "model_not_chat"
        view = {"ready": not reason, "reason": reason, "provider_ref": ref,
                "provider_name": entry.get("name", ref) if entry else ref,
                "model": model or "", "style": policy["style"]}
        return view, connection

    def validate_selection(self, instance: Any, raw: Any, uid: str | None) -> None:
        value = validate_settings(raw)
        if value["provider_ref"] is None and value["model"] is None:
            return  # Reset/inherit and style changes remain possible without an application model.
        defaults = settings_state(instance)["defaults"] if uid is not None else {}
        policy = {key: value[key] if value[key] is not None else defaults.get(key) for key in value}
        policy["style"] = policy["style"] or "character"
        view, _ = self._resolve_policy(policy)
        if not view["ready"]:
            raise ValueError(view["reason"])

    def begin(self, instance: Any, uid: str) -> SeatGeneration:
        view, connection = self._resolve(instance, uid)
        config = self._config_state()
        snapshot = deepcopy((view, connection, config.get("proxy_enabled"), config.get("proxy_url"),
                             config.get("model_request_timeout_seconds", 120), configuration_token(instance, uid)))
        run, round_number = instance.run_id, progression_state.round_value(instance)
        control = get_control(instance, uid)
        revision = instance.modules.get("ai_seats", {}).get("revision", 0)
        key = (run, uid)
        self._generating[key] = self._generating.get(key, 0) + 1

        def guard() -> str:
            current_view, current_connection = self._resolve(instance, uid)
            current_config = self._config_state()
            current = (current_view, current_connection, current_config.get("proxy_enabled"),
                       current_config.get("proxy_url"), current_config.get("model_request_timeout_seconds", 120),
                       configuration_token(instance, uid))
            return "" if current == snapshot else "ai_config_changed"

        def finish(outcome: dict) -> bool:
            count = self._generating.get(key, 0) - 1
            if count > 0:
                self._generating[key] = count
            else:
                self._generating.pop(key, None)
            if (guard() or instance.run_id != run or progression_state.round_value(instance) != round_number
                    or uid not in instance.players or get_control(instance, uid) != control
                    or outcome["status"] in {"duplicate", "discarded"}):
                return False
            try:
                record_outcome(instance, uid, {**outcome, "run_id": run, "round": round_number,
                    "control_revision": control["revision"], "config_revision": revision})
            except ValueError:
                return False
            return True

        client = None
        unavailable_reason = view["reason"]
        if view["ready"] and connection:
            try:
                client = self._client_factory(providers=[ProviderConfig(
                    provider_name=view["provider_ref"], base_url=connection["base_url"],
                    api_key=connection["api_key"], api_format=connection["api_format"],
                    model_name=view["model"],
                    reasoning_effort=config.get("model_reasoning_effort") or "off",
                )], default=view["provider_ref"],
                    proxy_url=effective_proxy_url(bool(config.get("proxy_enabled")), str(config.get("proxy_url") or "")),
                    request_timeout_seconds=float(config.get("model_request_timeout_seconds", 120)))
            except (TypeError, ValueError):
                unavailable_reason = "provider_unconfigured"
        return SeatGeneration(client, STYLE_PROMPTS.get(view.get("style", "character"), ""),
                              unavailable_reason, guard, finish)

    def project(self, instance: Any) -> dict[str, Any]:
        try:
            slot = settings_state(instance)
            defaults, overrides, outcomes = validate_settings(slot["defaults"]), slot["seats"], slot["outcomes"]
            revision = slot["revision"]
        except ValueError:
            defaults, overrides, outcomes, revision = {}, {}, {}, 0
        seats = {}
        for uid in instance.players:
            view, _connection = self._resolve(instance, uid)
            last = outcomes.get(uid)
            # Restored in-flight markers are never treated as running. Old-run outcomes are hidden.
            if not isinstance(last, dict) or last.get("run_id") != instance.run_id:
                last = None
            if last is not None:
                visible_round = last.get("combat_round", last.get("round"))
                last = {"status": str(last.get("status")) if last.get("status") in {"added", "skipped", "fallback"} else "skipped",
                        "reason": safe_reason(last.get("reason")),
                        "round": visible_round if type(visible_round) is int else 0}
            try:
                settings = validate_settings(overrides.get(uid, {}))
            except ValueError:
                settings = {}
            seats[uid] = {**view, "settings": settings,
                "inherits": uid not in overrides,
                "generating": self._generating.get((instance.run_id, uid), 0) > 0,
                "last_outcome": deepcopy(last), "retry_reason": retry_block(instance, uid)}
        providers = []
        for entry in self._config_state().get("ai_providers", []):
            if not isinstance(entry, dict):
                continue
            models = [m for m in entry.get("models", [])
                      if entry.get("model_capabilities", {}).get(m, "chat") == "chat"]
            providers.append({"id": entry["id"], "name": entry.get("name", entry["id"]), "models": models})
        return {"ok": True, "revision": revision, "defaults": deepcopy(defaults),
                "seats": seats, "providers": providers, "styles": list(STYLES),
                "application": {"provider_ref": str(self._config_state().get("llm_provider_ref") or ""),
                                "model": str(self._config_state().get("model") or "")},
                "combat_policy": "model_choice_with_rules_fallback"}


def safe_reason(raw: Any) -> str:
    reason = str(raw or "")
    if reason.startswith("call_failed:"):
        return "call_failed"
    allowed = {"", "client_unavailable", "provider_missing", "provider_unconfigured", "model_required",
               "model_missing", "model_not_chat", "unsupported_settings", "unusable_output", "deceased",
               "STRUCTURED_INTENT_REQUIRED", "action_rejected", "human_gate_changed",
               "provider_timeout", "provider_connection_failed", "provider_auth_failed", "provider_rate_limited",
               "decision_budget_exhausted"}
    return reason if reason in allowed else "action_unavailable"


def retry_block(instance: Any, uid: str) -> str:
    if uid not in instance.players:
        return "seat_missing"
    if get_control(instance, uid)["mode"] != "ai":
        return "not_ai_controlled"
    if instance.state != GameState.ACTIVE_ACTION:
        return "phase_not_actionable"
    state = ruleset_runtime.state(instance)
    combat = state.get("combat") if isinstance(state, dict) else None
    if isinstance(combat, dict) and combat.get("status") == "active":
        actor_id = module_hooks.combat_view(instance).get("current_actor_id")
        if actor_id is not None:
            return "" if actor_id == f"player:{uid}" else "not_this_seat"
        # Runtimes without a projector retain the previous single-slot contract.
        initiative, index = combat.get("initiative"), combat.get("turn_index")
        if (not isinstance(initiative, list) or type(index) is not int or not 0 <= index < len(initiative)
                or initiative[index] != f"player:{uid}"):
            return "not_this_seat"
        return ""  # Structured combat has its own turn authority, not narrative readiness.
    if any(row.get("user_id") == uid for row in instance.action_queue):
        return "already_declared"
    if instance.has_pending_dice():
        return "waiting_dice"
    if instance.active_human_players and not instance.human_actions_ready():
        return "waiting_humans"
    return ""


@dataclass(frozen=True)
class AISeatDependencies:
    get_instance: Callable[[str], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]
    resume: Callable[[str, str], Awaitable[dict]]


class AISeatService:
    def __init__(self, dependencies: AISeatDependencies, runtime: AISeatRuntime):
        self._dependencies, self.runtime = dependencies, runtime

    def read(self, game_key: str) -> dict:
        inst = self._dependencies.get_instance(game_key)
        return self.runtime.project(inst) if inst else {"ok": False, "error_code": "game_missing"}

    async def update(self, game_key: str, body: Any, uid: str | None = None) -> dict:
        inst = self._dependencies.get_instance(game_key)
        if inst is None:
            return {"ok": False, "error_code": "game_missing"}
        if not isinstance(body, dict) or body.keys() - {"settings", "expected_revision"}:
            return {"ok": False, "error_code": "invalid_settings"}
        try:
            async with inst.authoritative_write() as entered:
                if not entered:
                    return {"ok": False, "error_code": "game_busy"}
                self.runtime.validate_selection(inst, body.get("settings"), uid)
                before = deepcopy(settings_state(inst))
                update_settings(inst, body["settings"], uid=uid, expected_revision=body.get("expected_revision"))
                try:
                    await self._dependencies.save_instance(inst)
                except BaseException:
                    restore_settings(inst, before)
                    raise
        except ValueError:
            return {"ok": False, "error_code": "invalid_or_stale_settings"}
        return self.runtime.project(inst)

    async def retry(self, game_key: str, uid: str) -> dict:
        inst = self._dependencies.get_instance(game_key)
        if inst is None:
            return {"ok": False, "error_code": "game_missing"}
        reason = retry_block(inst, uid)
        view, _ = self.runtime._resolve(inst, uid)
        reason = reason or view["reason"]
        if reason:
            return {"ok": False, "error_code": reason}
        outcome = await self._dependencies.resume(game_key, uid)
        payload = outcome.get("payload", outcome)
        return {"ok": bool(payload.get("ok")), "resume": payload,
                "ai_seats": self.runtime.project(inst)}
