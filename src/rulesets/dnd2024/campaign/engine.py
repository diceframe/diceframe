"""Authoritative Session 0, campaign proposal, and starter-adventure engine."""

from __future__ import annotations

import re
from copy import deepcopy
from hashlib import sha256
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.play.contracts import story_encounter_instance_id
from src.rulesets.events import EventBatchError, apply_event_batch, stable_batch_id


CAMPAIGN_INTENT_TYPES = frozenset({
    "session_zero.quick_start",
    "session_zero.propose",
    "session_zero.respond",
    "session_zero.lock",
    "campaign.propose",
    "campaign.proposal.resolve",
    "automation.set",
    "tutorial.start",
    "tutorial.choose",
    "party_decision.submit",
    "party_decision.resolve",
})
ENTITY_KINDS = frozenset({"task", "clue", "fact", "item", "relationship"})
VISIBILITIES = frozenset({"public", "gm"})
DIFFICULTIES = frozenset({"story", "standard", "challenging", "lethal"})
PVP_POLICIES = frozenset({"disabled", "consent", "enabled"})
CONTENT_RATINGS = frozenset({"family", "teen", "mature"})
AUTOMATION_MODES = frozenset({"auto", "assist", "manual"})
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")


class CampaignIntentError(ValueError):
    """Raised when campaign state or a structured campaign intent is invalid."""


def _text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    parsed = str(value or "").strip()
    if required and not parsed:
        raise CampaignIntentError(f"{field} is required")
    if len(parsed) > maximum:
        raise CampaignIntentError(f"{field} must be at most {maximum} characters")
    return parsed


def _text_list(value: Any, field: str, *, maximum_items: int = 20) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum_items:
        raise CampaignIntentError(f"{field} must contain at most {maximum_items} items")
    result: list[str] = []
    for item in value:
        parsed = _text(item, field, 120, required=True)
        if parsed not in result:
            result.append(parsed)
    return result


class Dnd2024CampaignEngine:
    """Resolve non-combat professional play without granting authority to an LLM."""

    def __init__(
        self, bundle: LoadedRulesetBundle, adventure: dict[str, Any] | None = None,
    ):
        self.bundle = bundle
        self.adventure = deepcopy(adventure) if isinstance(adventure, dict) else None
        self.steps = {
            str(item.get("id") or ""): item
            for item in (self.adventure or {}).get("steps") or []
            if isinstance(item, dict) and item.get("id")
        }
        self.choices = {
            str(item.get("id") or ""): item
            for item in (self.adventure or {}).get("choices") or []
            if isinstance(item, dict) and item.get("id")
        }
        self.chapters = {
            str(item.get("id") or ""): item
            for item in (self.adventure or {}).get("chapters") or []
            if isinstance(item, dict) and item.get("id")
        }
        start_step = str((self.adventure or {}).get("start_step_id") or "")
        # FIX-04 §6.9：Adventure v2 图不是 v1 steps/choices 模型——它的推进由
        # application 层的 adventure_progress 权威决定。这里只做"v2 图不接受
        # v1 校验/不伪造 v1 tutorial"的分支，v1 行为完全不变。
        self.is_v2_adventure = bool(
            self.adventure is not None and not self.steps
            and (self.adventure.get("start_node_ids") or self.adventure.get("nodes"))
        )
        if self.is_v2_adventure:
            return
        if self.adventure is not None and start_step not in self.steps:
            raise CampaignIntentError("starter adventure start_step_id is invalid")
        for step_id, step in self.steps.items():
            if str(step.get("chapter_id") or "") not in self.chapters:
                raise CampaignIntentError(f"starter adventure step {step_id} has no chapter")
            for choice_id in step.get("choice_ids") or []:
                choice = self.choices.get(str(choice_id))
                if choice is None or str(choice.get("step_id") or "") != step_id:
                    raise CampaignIntentError(f"starter adventure choice {choice_id} is invalid")
        for choice_id, choice in self.choices.items():
            next_step = str(choice.get("next_step_id") or "")
            if next_step and next_step not in self.steps:
                raise CampaignIntentError(f"starter adventure choice {choice_id} has no next step")
            for outcome in choice.get("outcomes") or []:
                if not isinstance(outcome, dict) or outcome.get("entity_kind") not in ENTITY_KINDS:
                    raise CampaignIntentError(f"starter adventure choice {choice_id} has invalid outcome")

    def _initial_campaign(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "world_binding": {
                "world_id": "",
                "source": "game",
            },
            "adventure_binding": {
                "adventure_id": "",
                "world_id": "",
                "recommended_world_id": "",
                "compatibility": "not_selected",
                "scene_source": "world",
            },
            "session_zero": {
                "status": "not_started",
                "revision": 0,
                "agreement": None,
                "pending_agreement": None,
                "responses": {},
            },
            "automation": {"mode": ""},
            "proposals": {},
            "entities": {kind: {} for kind in sorted(ENTITY_KINDS)},
            "tutorial": {
                # v2：v1 tutorial 不适用（推进由 adventure_progress 权威决定）。
                "status": (
                    "not_applicable" if getattr(self, "is_v2_adventure", False)
                    else "not_started" if self.adventure is not None
                    else "unavailable"
                ),
                "adventure_id": "",
                "current_step_id": "",
                "history": [],
                "hints_used": {},
                "coach_enabled": True,
            },
            "party_decision": {
                "status": "none",
                "step_id": "",
                "choice_ids": [],
                "submitted": {},
            },
            "chapter_summaries": [],
        }

    def initialize_state(self, instance: Any) -> dict[str, Any]:
        state = instance.ruleset_state
        if not isinstance(state, dict):
            raise CampaignIntentError("ruleset_state must be an object")
        state.setdefault("state_schema_version", 1)
        state.setdefault("version", 0)
        state.setdefault("campaign", self._initial_campaign())
        campaign = state["campaign"]
        if not isinstance(campaign, dict) or int(campaign.get("schema_version", 0) or 0) != 1:
            raise CampaignIntentError("unsupported D&D 2024 campaign state schema")
        campaign.setdefault("session_zero", self._initial_campaign()["session_zero"])
        automation = campaign.setdefault("automation", {"mode": ""})
        if not isinstance(automation, dict):
            automation = {"mode": ""}
            campaign["automation"] = automation
        mode = str(automation.get("mode") or "")
        if mode not in AUTOMATION_MODES:
            automation["mode"] = (
                "auto" if bool(getattr(instance, "solo_mode", False)) else "assist"
            )
        world_binding = campaign.setdefault(
            "world_binding", deepcopy(self._initial_campaign()["world_binding"]),
        )
        if not isinstance(world_binding, dict):
            world_binding = deepcopy(self._initial_campaign()["world_binding"])
            campaign["world_binding"] = world_binding
        if not str(world_binding.get("world_id") or ""):
            world_binding["world_id"] = str(getattr(instance, "world_id", "") or "")
        world_binding.setdefault("source", "game")
        adventure_binding = campaign.setdefault(
            "adventure_binding", deepcopy(self._initial_campaign()["adventure_binding"]),
        )
        if not isinstance(adventure_binding, dict):
            adventure_binding = deepcopy(self._initial_campaign()["adventure_binding"])
            campaign["adventure_binding"] = adventure_binding
        adventure_binding.setdefault("adventure_id", "")
        adventure_binding.setdefault("world_id", "")
        adventure_binding.setdefault("recommended_world_id", "")
        adventure_binding.setdefault("compatibility", "not_selected")
        adventure_binding.setdefault("scene_source", "world")
        campaign.setdefault("proposals", {})
        campaign.setdefault("entities", {kind: {} for kind in sorted(ENTITY_KINDS)})
        campaign.setdefault("tutorial", self._initial_campaign()["tutorial"])
        party_decision = campaign.setdefault(
            "party_decision", deepcopy(self._initial_campaign()["party_decision"]),
        )
        if not isinstance(party_decision, dict):
            party_decision = deepcopy(self._initial_campaign()["party_decision"])
            campaign["party_decision"] = party_decision
        party_decision.setdefault("status", "none")
        party_decision.setdefault("step_id", "")
        party_decision.setdefault("choice_ids", [])
        party_decision.setdefault("submitted", {})
        if not isinstance(party_decision.get("choice_ids"), list):
            party_decision["choice_ids"] = []
        if not isinstance(party_decision.get("submitted"), dict):
            party_decision["submitted"] = {}
        campaign.setdefault("chapter_summaries", [])
        tutorial = campaign.get("tutorial")
        top_binding = dict(getattr(instance, "adventure_binding", {}) or {})
        if self.adventure is None:
            if isinstance(tutorial, dict):
                tutorial.update({
                    "status": "unavailable",
                    "adventure_id": "",
                    "current_step_id": "",
                    "history": [],
                    "hints_used": {},
                })
            adventure_binding.update({
                "adventure_id": "",
                "world_id": str(world_binding.get("world_id") or ""),
                "recommended_world_id": "",
                "compatibility": "not_selected",
                "scene_source": "world",
            })
        elif top_binding:
            bound_world = str(top_binding.get("world_id") or world_binding.get("world_id") or "")
            recommended_world = str(self.adventure.get("recommended_world_id") or "")
            adventure_binding.update({
                "adventure_id": str(top_binding.get("adventure_id") or self.adventure.get("id") or ""),
                "world_id": bound_world,
                "recommended_world_id": recommended_world,
                "compatibility": (
                    "compatible"
                    if not recommended_world or not bound_world or bound_world == recommended_world
                    else "review_required"
                ),
                "scene_source": "adventure",
                "version": str(top_binding.get("version") or ""),
                "content_digest": str(top_binding.get("content_digest") or ""),
            })
        return state

    @staticmethod
    def default_agreement() -> dict[str, Any]:
        return {
            "tone": "Heroic adventure with room for humor",
            "difficulty": "standard",
            "content_rating": "teen",
            "session_length_minutes": 120,
            "pvp_policy": "consent",
            "safety_tool": "pause_and_check",
            "lines": [],
            "veils": [],
            "table_rules": ["Share spotlight", "Pause when anyone asks"],
        }

    def available_intents(self, instance: Any, actor_id: str) -> list[dict[str, Any]]:
        state = self.initialize_state(instance)
        campaign = state["campaign"]
        version = int(state.get("version", 0) or 0)
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        is_gm = actor_id == gm_uid
        is_player = actor_id in instance.players
        if not (is_gm or is_player):
            return []
        actions: list[dict[str, Any]] = []
        session = campaign["session_zero"]
        tutorial = campaign["tutorial"]
        if (
            is_gm
            and bool(getattr(instance, "solo_mode", False))
            and session.get("status") == "not_started"
            and tutorial.get("status") in {"not_started", "unavailable"}
        ):
            actions.append({
                "type": "session_zero.quick_start",
                "label": (
                    "Use recommended settings and start the selected adventure"
                    if self.adventure is not None
                    else "Use recommended settings and start a sandbox campaign"
                ),
                "expected_version": version,
            })
        if is_gm:
            actions.append({
                "type": "session_zero.propose",
                "label": "Propose Session 0 agreement",
                "expected_version": version,
                "defaults": self.default_agreement(),
            })
        if session.get("status") == "pending" and is_player:
            response = session.get("responses", {}).get(actor_id, {})
            if response.get("response") != "accept":
                actions.append({
                    "type": "session_zero.respond",
                    "label": "Respond to Session 0 agreement",
                    "expected_version": version,
                    "options": ["accept", "request_changes"],
                })
        if is_gm and self._all_players_accepted(instance, session):
            actions.append({
                "type": "session_zero.lock",
                "label": "Lock Session 0 agreement",
                "expected_version": version,
            })
        if session.get("status") != "locked":
            return actions
        if is_gm:
            actions.append({
                "type": "automation.set",
                "label": "Set AI GM automation",
                "expected_version": version,
                "options": sorted(AUTOMATION_MODES),
                "current": str(campaign["automation"]["mode"]),
            })
            actions.append({
                "type": "campaign.propose",
                "label": "Propose campaign record",
                "expected_version": version,
                "entity_kinds": sorted(ENTITY_KINDS),
                "visibilities": sorted(VISIBILITIES),
            })
            pending = [
                deepcopy(item) for item in campaign.get("proposals", {}).values()
                if item.get("status") == "pending"
            ]
            if pending:
                actions.append({
                    "type": "campaign.proposal.resolve",
                    "label": "Confirm or reject campaign record",
                    "expected_version": version,
                    "options": ["confirm", "reject"],
                    "proposals": pending,
                })
        if self.adventure is not None and tutorial.get("status") == "not_started" and is_gm:
            actions.append({
                "type": "tutorial.start", "label": "Start guided adventure",
                "expected_version": version, "adventure_id": self.adventure["id"],
            })
        elif tutorial.get("status") == "active":
            step = self.steps.get(str(tutorial.get("current_step_id") or ""), {})
            party_required = self._party_decision_required(instance, state)
            if party_required and is_player:
                party = state["campaign"]["party_decision"]
                action = {
                    "type": "party_decision.submit", "label": "Submit party decision",
                    "expected_version": version,
                    "choice_ids": deepcopy(
                        party.get("choice_ids") or step.get("choice_ids") or []
                    ),
                }
                if str(party.get("status") or "") == "open":
                    action["step_id"] = str(party.get("step_id") or "")
                    action["submitted"] = deepcopy(party.get("submitted") or {})
                actions.append(action)
            if party_required and is_gm:
                party = state["campaign"]["party_decision"]
                actions.append({
                    "type": "party_decision.resolve", "label": "Resolve party decision",
                    "expected_version": version,
                    "choice_ids": deepcopy(
                        party.get("choice_ids") or step.get("choice_ids") or []
                    ),
                    "submitted": deepcopy(party.get("submitted") or {}),
                })
            if is_player and not party_required:
                actions.append({
                    "type": "tutorial.choose", "label": "Choose next step",
                    "expected_version": version,
                    "choice_ids": deepcopy(step.get("choice_ids") or []),
                    "requirement_met": self._requirement_met(state, step),
                })
            elif is_gm and party_required:
                # GM keeps a direct override for a stalled or exceptional table.
                actions.append({
                    "type": "tutorial.choose", "label": "Choose next step (GM override)",
                    "expected_version": version,
                    "choice_ids": deepcopy(step.get("choice_ids") or []),
                    "requirement_met": self._requirement_met(state, step),
                })
        return actions

    def validate_intent(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        try:
            self._validate(instance, intent)
        except CampaignIntentError as exc:
            return {"ok": False, "code": "INVALID_CAMPAIGN_INTENT", "error": str(exc)}
        return {"ok": True}

    def resolve_intent(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        del rng
        state = self.initialize_state(instance)
        intent_id = str(intent.get("intent_id") or "")
        prior = next(
            (item for item in instance.event_ledger if item.get("intent_id") == intent_id),
            None,
        )
        if prior is not None:
            expected = intent.get("expected_version")
            if (
                isinstance(expected, bool) or not isinstance(expected, int)
                or str(prior.get("batch_id") or "") != stable_batch_id(intent, expected)
            ):
                return {
                    "ok": False,
                    "code": "INTENT_ID_CONFLICT",
                    "error": "intent_id was already used for a different request",
                }
            return {"ok": True, "event_batch": deepcopy(prior), "replayed": True}
        validation = self.validate_intent(instance, intent)
        if not validation["ok"]:
            return validation
        intent_type = str(intent["type"])
        submitted_by = str(intent["submitted_by"])
        campaign = state["campaign"]
        events: list[dict[str, Any]] = [{
            "type": "intent.submitted",
            "intent_type": intent_type,
            "actor_id": "",
            "submitted_by": submitted_by,
        }]
        if intent_type == "session_zero.quick_start":
            revision = int(campaign["session_zero"].get("revision", 0) or 0) + 1
            events.extend([
                {
                    "type": "dnd2024.session_zero.proposed",
                    "revision": revision,
                    "agreement": self.default_agreement(),
                    "proposed_by": submitted_by,
                },
                {
                    "type": "dnd2024.session_zero.responded",
                    "user_id": submitted_by,
                    "response": "accept",
                    "comment": "recommended solo quick start",
                },
                {"type": "dnd2024.session_zero.locked", "locked_by": submitted_by},
            ])
            if self.adventure is not None:
                events.append({
                    "type": "dnd2024.tutorial.started",
                    "adventure_id": self.adventure["id"],
                    "start_step_id": self.adventure["start_step_id"],
                    **self._adventure_binding_event(instance),
                    "started_by": submitted_by,
                })
        elif intent_type == "session_zero.propose":
            events.append({
                "type": "dnd2024.session_zero.proposed",
                "revision": int(campaign["session_zero"].get("revision", 0) or 0) + 1,
                "agreement": self._normalize_agreement(intent.get("agreement")),
                "proposed_by": submitted_by,
            })
        elif intent_type == "session_zero.respond":
            events.append({
                "type": "dnd2024.session_zero.responded",
                "user_id": submitted_by,
                "response": str(intent["response"]),
                "comment": _text(intent.get("comment"), "comment", 500),
            })
        elif intent_type == "session_zero.lock":
            events.append({
                "type": "dnd2024.session_zero.locked", "locked_by": submitted_by,
            })
        elif intent_type == "campaign.propose":
            proposal = self._normalize_proposal(intent)
            proposal["proposal_id"] = f"proposal:{sha256(intent_id.encode()).hexdigest()[:20]}"
            proposal["proposed_by"] = submitted_by
            events.append({"type": "dnd2024.campaign.proposal_created", **proposal})
        elif intent_type == "campaign.proposal.resolve":
            proposal = deepcopy(campaign["proposals"][str(intent["proposal_id"])])
            option = str(intent["option"])
            events.append({
                "type": "dnd2024.campaign.proposal_resolved",
                "proposal_id": proposal["proposal_id"],
                "option": option,
                "resolved_by": submitted_by,
            })
            if option == "confirm":
                events.append({
                    "type": "dnd2024.campaign.entity_created",
                    "entity": {
                        "id": proposal["entity_id"],
                        "kind": proposal["kind"],
                        "title": proposal["title"],
                        "summary": proposal["summary"],
                        "visibility": proposal["visibility"],
                        "target_id": proposal["target_id"],
                        "source": "confirmed_proposal",
                        "proposal_id": proposal["proposal_id"],
                    },
                })
        elif intent_type == "automation.set":
            events.append({
                "type": "dnd2024.automation.configured",
                "mode": str(intent["mode"]),
                "configured_by": submitted_by,
            })
        elif intent_type == "tutorial.start":
            events.append({
                "type": "dnd2024.tutorial.started",
                "adventure_id": self.adventure["id"],
                "start_step_id": self.adventure["start_step_id"],
                **self._adventure_binding_event(instance),
                "started_by": submitted_by,
            })
        elif intent_type == "tutorial.choose":
            events.extend(self._tutorial_choice_events(state, str(intent["choice_id"]), submitted_by))
        elif intent_type == "party_decision.submit":
            events.extend(self._party_submit_events(state, str(intent["choice_id"]), submitted_by))
        elif intent_type == "party_decision.resolve":
            events.extend(self._party_resolve_events(state, intent, submitted_by))
        expected = int(intent["expected_version"])
        batch = {
            "batch_id": stable_batch_id(intent, expected),
            "intent_id": intent_id,
            "intent_type": intent_type,
            "expected_version": expected,
            "result_version": expected + 1,
            "events": events,
            "source_ref": "diceframe-original:dnd2024-campaign-runtime",
        }
        return {"ok": True, "event_batch": batch}

    def _party_decision_required(
        self, instance: Any, state: dict[str, Any] | None = None,
    ) -> bool:
        state = state if isinstance(state, dict) else self.initialize_state(instance)
        campaign = state.get("campaign") or {}
        party = campaign.get("party_decision") or {}
        if str(party.get("status") or "") == "open":
            return True
        if bool(getattr(instance, "solo_mode", False)) or len(instance.players) <= 1:
            return False
        tutorial = campaign.get("tutorial") or {}
        if tutorial.get("status") != "active":
            return False
        step = self.steps.get(str(tutorial.get("current_step_id") or ""))
        return bool(step and len(step.get("choice_ids") or []) > 1)

    def _party_submit_events(
        self, state: dict[str, Any], choice_id: str, submitted_by: str,
    ) -> list[dict[str, Any]]:
        campaign = state["campaign"]
        party = campaign["party_decision"]
        tutorial = campaign["tutorial"]
        step_id = str(tutorial.get("current_step_id") or "")
        step = self.steps[step_id]
        events: list[dict[str, Any]] = []
        if str(party.get("status") or "") != "open":
            events.append({
                "type": "dnd2024.party_decision.opened",
                "step_id": step_id,
                "choice_ids": [str(item) for item in step.get("choice_ids") or []],
            })
        events.append({
            "type": "dnd2024.party_decision.submitted",
            "step_id": step_id,
            "user_id": submitted_by,
            "choice_id": choice_id,
        })
        return events

    def _party_resolve_events(
        self, state: dict[str, Any], intent: dict[str, Any], submitted_by: str,
    ) -> list[dict[str, Any]]:
        campaign = state["campaign"]
        party = campaign["party_decision"]
        tutorial = campaign["tutorial"]
        step_id = str(tutorial.get("current_step_id") or "")
        step = self.steps[step_id]
        submitted = {
            str(uid): str(choice)
            for uid, choice in (party.get("submitted") or {}).items()
        }
        requested = str(intent.get("choice_id") or "")
        tally: dict[str, int] = {}
        for choice_id in submitted.values():
            tally[choice_id] = tally.get(choice_id, 0) + 1
        final_choice_id = requested
        if not final_choice_id:
            final_choice_id = max(
                sorted(tally), key=tally.__getitem__,
            )
        events: list[dict[str, Any]] = []
        if str(party.get("status") or "") != "open":
            events.append({
                "type": "dnd2024.party_decision.opened",
                "step_id": step_id,
                "choice_ids": [str(item) for item in step.get("choice_ids") or []],
            })
        events.append({
            "type": "dnd2024.party_decision.resolved",
            "step_id": step_id,
            "final_choice_id": final_choice_id,
            "submitted": submitted,
            "tally": tally,
            "resolved_by": submitted_by,
        })
        events.extend(self._tutorial_choice_events(state, final_choice_id, submitted_by))
        return events

    def apply_batch(self, instance: Any, batch: dict[str, Any]) -> dict[str, Any]:
        state = self.initialize_state(instance)
        snapshot = {
            "version": int(state.get("version", 0) or 0),
            "ruleset_state": deepcopy(state),
        }
        updated, ledger, duplicate = apply_event_batch(
            snapshot, instance.event_ledger, batch, self._reduce_event,
        )
        if not duplicate:
            next_state = updated["ruleset_state"]
            next_state["version"] = updated["version"]
            instance.ruleset_state = next_state
            instance.event_ledger = ledger
        return {
            "ok": True,
            "applied": not duplicate,
            "duplicate": duplicate,
            "state_version": int(instance.ruleset_state.get("version", 0) or 0),
            "event_batch": deepcopy(batch),
            "campaign": deepcopy(instance.ruleset_state.get("campaign") or {}),
        }

    def gameplay_view(
        self, instance: Any, viewer_id: str = "", viewer_is_gm: bool = False,
    ) -> dict[str, Any]:
        state = self.initialize_state(instance)
        campaign = deepcopy(state["campaign"])
        is_gm = viewer_is_gm or viewer_id == str(getattr(instance, "gm_uid", "") or "")
        campaign["proposals"] = [
            item for item in campaign.get("proposals", {}).values()
            if is_gm or item.get("visibility") == "public"
        ]
        campaign["proposals"].sort(key=lambda item: str(item.get("proposal_id") or ""))
        visible_entities: dict[str, list[dict[str, Any]]] = {}
        for kind in sorted(ENTITY_KINDS):
            values = campaign.get("entities", {}).get(kind, {})
            visible_entities[kind] = [
                item for item in values.values()
                if is_gm or item.get("visibility") == "public"
            ]
        campaign["entities"] = visible_entities
        tutorial = campaign["tutorial"]
        tutorial["adventure"] = self._adventure_view()
        step = self.steps.get(str(tutorial.get("current_step_id") or ""))
        tutorial["current_step"] = self._step_view(step) if step else None
        if step:
            tutorial["requirement_met"] = self._requirement_met(state, step)
        party = campaign.get("party_decision")
        if isinstance(party, dict) and str(party.get("status") or "") == "open":
            party_step = self.steps.get(str(party.get("step_id") or ""), step)
            projected_choices = []
            if party_step:
                projected_choices = self._step_view(party_step).get("choices", [])
            campaign["party_decision"] = {
                "status": "open",
                "step_id": str(party.get("step_id") or ""),
                "choices": projected_choices,
                "submitted": {
                    str(uid): str(choice)
                    for uid, choice in (party.get("submitted") or {}).items()
                },
                "submitted_count": len(party.get("submitted") or {}),
                "total_players": len(instance.players),
            }
        else:
            campaign.pop("party_decision", None)
        campaign["session_zero_defaults"] = self.default_agreement()
        return campaign

    def _adventure_binding_event(self, instance: Any) -> dict[str, Any]:
        if self.adventure is None:
            raise CampaignIntentError("no adventure package is bound to this game")
        world_id = str(getattr(instance, "world_id", "") or "")
        recommended = str(self.adventure.get("recommended_world_id") or "")
        return {
            "world_id": world_id,
            "recommended_world_id": recommended,
            "compatibility": (
                "compatible" if not recommended or not world_id or world_id == recommended
                else "review_required"
            ),
            "scene_source": "adventure",
        }

    def _validate(self, instance: Any, intent: dict[str, Any]) -> None:
        if not isinstance(intent, dict):
            raise CampaignIntentError("intent must be an object")
        intent_id = str(intent.get("intent_id") or "").strip()
        if not intent_id or len(intent_id) > 120:
            raise CampaignIntentError("intent_id is required and must be at most 120 characters")
        intent_type = str(intent.get("type") or "")
        if intent_type not in CAMPAIGN_INTENT_TYPES:
            raise CampaignIntentError("campaign intent type is not supported")
        expected = intent.get("expected_version")
        state = self.initialize_state(instance)
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise CampaignIntentError("expected_version must be a non-negative integer")
        if expected != int(state.get("version", 0) or 0):
            raise CampaignIntentError(
                f"state version conflict: expected {expected}, current {state.get('version', 0)}"
            )
        submitted_by = str(intent.get("submitted_by") or "")
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        is_gm = submitted_by == gm_uid
        is_player = submitted_by in instance.players
        if not (is_gm or is_player):
            raise CampaignIntentError("submitter is not a campaign participant")
        session = state["campaign"]["session_zero"]
        if intent_type in {
            "session_zero.quick_start", "session_zero.propose", "session_zero.lock", "campaign.propose",
            "campaign.proposal.resolve", "automation.set", "tutorial.start", "party_decision.resolve",
        } and not is_gm:
            raise CampaignIntentError("only the GM can perform this campaign operation")
        if intent_type == "session_zero.quick_start":
            if not bool(getattr(instance, "solo_mode", False)):
                raise CampaignIntentError("recommended quick start is only available in solo mode")
            tutorial = state["campaign"]["tutorial"]
            if (
                session.get("status") != "not_started"
                or tutorial.get("status") not in {"not_started", "unavailable"}
            ):
                raise CampaignIntentError("recommended quick start is no longer available")
        elif intent_type == "session_zero.propose":
            self._normalize_agreement(intent.get("agreement"))
        elif intent_type == "session_zero.respond":
            if session.get("status") != "pending" or not is_player:
                raise CampaignIntentError("there is no Session 0 proposal to answer")
            if intent.get("response") not in {"accept", "request_changes"}:
                raise CampaignIntentError("Session 0 response must be accept or request_changes")
            _text(intent.get("comment"), "comment", 500)
        elif intent_type == "session_zero.lock":
            if not self._all_players_accepted(instance, session):
                raise CampaignIntentError("every player must accept the current Session 0 revision")
        elif intent_type == "campaign.propose":
            if session.get("status") != "locked":
                raise CampaignIntentError("Session 0 must be locked before campaign records")
            self._normalize_proposal(intent)
        elif intent_type == "campaign.proposal.resolve":
            proposal = state["campaign"]["proposals"].get(str(intent.get("proposal_id") or ""))
            if not proposal or proposal.get("status") != "pending":
                raise CampaignIntentError("campaign proposal is not pending")
            if intent.get("option") not in {"confirm", "reject"}:
                raise CampaignIntentError("proposal option must be confirm or reject")
        elif intent_type == "automation.set":
            if session.get("status") != "locked":
                raise CampaignIntentError("Session 0 must be locked before automation is configured")
            if str(intent.get("mode") or "") not in AUTOMATION_MODES:
                raise CampaignIntentError("automation mode must be auto, assist, or manual")
        elif intent_type == "tutorial.start":
            if self.adventure is None:
                raise CampaignIntentError("no adventure package is bound to this game")
            if session.get("status") != "locked":
                raise CampaignIntentError("Session 0 must be locked before the tutorial")
            if state["campaign"]["tutorial"].get("status") != "not_started":
                raise CampaignIntentError("the tutorial has already started")
            if intent.get("adventure_id") not in {None, "", self.adventure["id"]}:
                raise CampaignIntentError("starter adventure is not available")
        elif intent_type == "tutorial.choose":
            tutorial = state["campaign"]["tutorial"]
            if tutorial.get("status") != "active" or not (is_player or is_gm):
                raise CampaignIntentError("the guided adventure is not active for this player")
            step = self.steps.get(str(tutorial.get("current_step_id") or ""))
            if step is None:
                raise CampaignIntentError("the guided adventure step is invalid")
            choice_id = str(intent.get("choice_id") or "")
            if choice_id not in step.get("choice_ids", []):
                raise CampaignIntentError("choice is not available for the current tutorial step")
            if not self._requirement_met(state, step):
                raise CampaignIntentError("complete the current tutorial objective first")
            if is_player and self._party_decision_required(instance, state):
                raise CampaignIntentError("multiplayer branch steps need the party decision flow")
        elif intent_type == "party_decision.submit":
            if not is_player:
                raise CampaignIntentError("only a player can submit a party decision")
            tutorial = state["campaign"]["tutorial"]
            if tutorial.get("status") != "active":
                raise CampaignIntentError("the guided adventure is not active")
            step = self.steps.get(str(tutorial.get("current_step_id") or ""))
            if step is None:
                raise CampaignIntentError("the guided adventure step is invalid")
            if not self._party_decision_required(instance, state):
                raise CampaignIntentError("the current step does not need a party decision")
            if not self._requirement_met(state, step):
                raise CampaignIntentError("complete the current tutorial objective first")
            choice_id = str(intent.get("choice_id") or "")
            if choice_id not in step.get("choice_ids", []):
                raise CampaignIntentError("choice is not available for the current tutorial step")
            party = state["campaign"]["party_decision"]
            if str(party.get("status") or "") == "open":
                if str(party.get("step_id") or "") != str(step.get("id") or ""):
                    raise CampaignIntentError("party decision is for a different tutorial step")
                if submitted_by in (party.get("submitted") or {}):
                    raise CampaignIntentError("this player has already submitted a party decision")
        elif intent_type == "party_decision.resolve":
            tutorial = state["campaign"]["tutorial"]
            if tutorial.get("status") != "active":
                raise CampaignIntentError("the guided adventure is not active")
            step = self.steps.get(str(tutorial.get("current_step_id") or ""))
            if step is None:
                raise CampaignIntentError("the guided adventure step is invalid")
            if not self._party_decision_required(instance, state):
                raise CampaignIntentError("the current step does not need a party decision")
            if not self._requirement_met(state, step):
                raise CampaignIntentError("complete the current tutorial objective first")
            party = state["campaign"]["party_decision"]
            if str(party.get("status") or "") == "open" and not party.get("submitted"):
                if len(instance.players) > 1:
                    raise CampaignIntentError("at least one player intent is required before resolving")
            choice_id = str(intent.get("choice_id") or "")
            if not choice_id and not (party.get("submitted") or {}):
                raise CampaignIntentError("a party choice is required before resolving")
            if choice_id and choice_id not in step.get("choice_ids", []):
                raise CampaignIntentError("choice is not available for the current tutorial step")

    @staticmethod
    def _all_players_accepted(instance: Any, session: dict[str, Any]) -> bool:
        if session.get("status") != "pending" or not session.get("pending_agreement"):
            return False
        players = list(instance.players)
        return bool(players) and all(
            session.get("responses", {}).get(uid, {}).get("response") == "accept"
            for uid in players
        )

    @staticmethod
    def _normalize_agreement(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise CampaignIntentError("agreement must be an object")
        difficulty = str(raw.get("difficulty") or "standard")
        rating = str(raw.get("content_rating") or "teen")
        pvp = str(raw.get("pvp_policy") or "consent")
        if difficulty not in DIFFICULTIES:
            raise CampaignIntentError("difficulty is invalid")
        if rating not in CONTENT_RATINGS:
            raise CampaignIntentError("content_rating is invalid")
        if pvp not in PVP_POLICIES:
            raise CampaignIntentError("pvp_policy is invalid")
        minutes = raw.get("session_length_minutes", 120)
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 30 <= minutes <= 480:
            raise CampaignIntentError("session_length_minutes must be between 30 and 480")
        safety_tool = str(raw.get("safety_tool") or "pause_and_check")
        if safety_tool != "pause_and_check":
            raise CampaignIntentError("safety_tool is invalid")
        return {
            "tone": _text(raw.get("tone"), "tone", 120, required=True),
            "difficulty": difficulty,
            "content_rating": rating,
            "session_length_minutes": minutes,
            "pvp_policy": pvp,
            "safety_tool": safety_tool,
            "lines": _text_list(raw.get("lines"), "lines"),
            "veils": _text_list(raw.get("veils"), "veils"),
            "table_rules": _text_list(raw.get("table_rules"), "table_rules"),
        }

    @staticmethod
    def _normalize_proposal(intent: dict[str, Any]) -> dict[str, Any]:
        kind = str(intent.get("kind") or "")
        visibility = str(intent.get("visibility") or "public")
        if kind not in ENTITY_KINDS:
            raise CampaignIntentError("campaign entity kind is invalid")
        if visibility not in VISIBILITIES:
            raise CampaignIntentError("campaign visibility is invalid")
        entity_id = str(intent.get("entity_id") or "").strip()
        if entity_id and not _SAFE_ID_RE.fullmatch(entity_id):
            raise CampaignIntentError("entity_id is invalid")
        if not entity_id:
            seed = f"{kind}:{intent.get('intent_id', '')}"
            entity_id = f"{kind}:{sha256(seed.encode()).hexdigest()[:16]}"
        return {
            "entity_id": entity_id,
            "kind": kind,
            "title": _text(intent.get("title"), "title", 120, required=True),
            "summary": _text(intent.get("summary"), "summary", 1000, required=True),
            "visibility": visibility,
            "target_id": _text(intent.get("target_id"), "target_id", 120),
            "status": "pending",
        }

    def _requirement_met(self, state: dict[str, Any], step: dict[str, Any]) -> bool:
        requirement = str(step.get("requires") or "none")
        if requirement == "none":
            return True
        if requirement == "combat_ended":
            campaign = state.get("campaign") or {}
            tutorial = campaign.get("tutorial") or {}
            encounter_id = story_encounter_instance_id(
                str(tutorial.get("adventure_id") or (self.adventure or {}).get("id") or ""),
                str(step.get("id") or ""),
            )
            combat = state.get("combat") or {}
            if (
                combat.get("status") == "ended"
                and combat.get("encounter_instance_id") == encounter_id
            ):
                return True
            return any(
                isinstance(item, dict) and item.get("encounter_instance_id") == encounter_id
                for item in state.get("combat_history") or []
            )
        return False

    def _tutorial_choice_events(
        self, state: dict[str, Any], choice_id: str, submitted_by: str,
    ) -> list[dict[str, Any]]:
        tutorial = state["campaign"]["tutorial"]
        current_step_id = str(tutorial["current_step_id"])
        step = self.steps[current_step_id]
        choice = self.choices[choice_id]
        next_step_id = str(choice.get("next_step_id") or "")
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.tutorial.choice_applied",
            "step_id": current_step_id,
            "chapter_id": str(step["chapter_id"]),
            "choice_id": choice_id,
            "next_step_id": next_step_id,
            "chosen_by": submitted_by,
        }]
        labels = self.adventure.get("tutorial") or {}
        entity_labels = labels.get("entities") if isinstance(labels, dict) else {}
        entity_labels = entity_labels if isinstance(entity_labels, dict) else {}
        for outcome in choice.get("outcomes") or []:
            entity_id = str(outcome["entity_id"])
            display = entity_labels.get(entity_id, {})
            display = display if isinstance(display, dict) else {}
            events.append({
                "type": "dnd2024.campaign.entity_created",
                "entity": {
                    "id": entity_id,
                    "kind": str(outcome["entity_kind"]),
                    "title": str(display.get("title") or entity_id),
                    "summary": str(display.get("summary") or ""),
                    "visibility": str(outcome.get("visibility") or "public"),
                    "target_id": str(outcome.get("target_id") or ""),
                    "status": str(outcome.get("status") or "active"),
                    "source": "starter_adventure",
                    "step_id": current_step_id,
                    "choice_id": choice_id,
                },
            })
        current_chapter = str(step["chapter_id"])
        next_chapter = (
            str(self.steps[next_step_id]["chapter_id"]) if next_step_id else ""
        )
        if not next_step_id or next_chapter != current_chapter:
            events.append(self._chapter_summary_event(tutorial, current_chapter, choice_id))
        if not next_step_id:
            events.append({
                "type": "dnd2024.tutorial.completed",
                "adventure_id": self.adventure["id"],
                "completed_by": submitted_by,
            })
        return events

    def _chapter_summary_event(
        self, tutorial: dict[str, Any], chapter_id: str, current_choice_id: str,
    ) -> dict[str, Any]:
        labels = self.adventure.get("tutorial") or {}
        chapters = labels.get("chapters") if isinstance(labels, dict) else {}
        choices = labels.get("choices") if isinstance(labels, dict) else {}
        chapters = chapters if isinstance(chapters, dict) else {}
        choices = choices if isinstance(choices, dict) else {}
        chapter_name = str(chapters.get(chapter_id, {}).get("name") or chapter_id)
        choice_ids = [
            str(item.get("choice_id") or "")
            for item in tutorial.get("history") or []
            if str(item.get("chapter_id") or "") == chapter_id
        ]
        choice_ids.append(current_choice_id)
        choice_names = [
            str(choices.get(choice_id, {}).get("label") or choice_id)
            for choice_id in choice_ids
        ]
        value = f"{chapter_name}: {' / '.join(choice_names)}"
        return {
            "type": "dnd2024.chapter.summarized",
            "summary_id": f"summary:{chapter_id}",
            "chapter_id": chapter_id,
            "chapter_name": chapter_name,
            "choice_ids": choice_ids,
            "summary": value,
            "memory": {
                "entity": str(self.adventure.get("tutorial", {}).get("name") or self.adventure["id"]),
                "relation": chapter_name,
                "value": value,
                "confidence": 1.0,
            },
        }

    def _adventure_view(self) -> dict[str, Any]:
        if self.adventure is None:
            return {}
        tutorial = self.adventure.get("tutorial") or {}
        return {
            "id": self.adventure["id"],
            "recommended_world_id": str(self.adventure.get("recommended_world_id") or ""),
            "name": str(tutorial.get("name") or self.adventure["id"]),
            "summary": str(tutorial.get("summary") or ""),
            "estimated_minutes": int(self.adventure.get("estimated_minutes", 90) or 90),
            "chapter_count": len(self.chapters),
        }

    def _step_view(self, step: dict[str, Any]) -> dict[str, Any]:
        tutorial = self.adventure.get("tutorial") or {}
        step_labels = tutorial.get("steps") if isinstance(tutorial, dict) else {}
        choice_labels = tutorial.get("choices") if isinstance(tutorial, dict) else {}
        step_labels = step_labels if isinstance(step_labels, dict) else {}
        choice_labels = choice_labels if isinstance(choice_labels, dict) else {}
        step_id = str(step["id"])
        text = step_labels.get(step_id, {})
        text = text if isinstance(text, dict) else {}
        choices = []
        for choice_id in step.get("choice_ids") or []:
            choice = self.choices[str(choice_id)]
            label = choice_labels.get(str(choice_id), {})
            label = label if isinstance(label, dict) else {}
            choices.append({
                "id": str(choice_id),
                "label": str(label.get("label") or choice_id),
                "description": str(label.get("description") or ""),
                "next_step_id": str(choice.get("next_step_id") or ""),
            })
        return {
            "id": step_id,
            "chapter_id": str(step["chapter_id"]),
            "title": str(text.get("title") or step_id),
            "narration": str(text.get("narration") or ""),
            "objective": str(text.get("objective") or ""),
            "hint": str(text.get("hint") or ""),
            "requires": str(step.get("requires") or "none"),
            "encounter_preset_id": str(step.get("encounter_preset_id") or ""),
            "scene_ref": str(step.get("scene_ref") or ""),
            "choices": choices,
        }

    @staticmethod
    def _reduce_event(snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        campaign = snapshot["ruleset_state"]["campaign"]
        event_type = str(event["type"])
        if event_type == "intent.submitted":
            return
        if event_type == "dnd2024.session_zero.proposed":
            session = campaign["session_zero"]
            session.update({
                "status": "pending",
                "revision": int(event["revision"]),
                "pending_agreement": deepcopy(event["agreement"]),
                "responses": {},
                "proposed_by": str(event["proposed_by"]),
            })
            return
        if event_type == "dnd2024.session_zero.responded":
            campaign["session_zero"].setdefault("responses", {})[str(event["user_id"])] = {
                "response": str(event["response"]), "comment": str(event.get("comment") or ""),
            }
            return
        if event_type == "dnd2024.session_zero.locked":
            session = campaign["session_zero"]
            if not session.get("pending_agreement"):
                raise EventBatchError("Session 0 agreement is missing")
            session["agreement"] = deepcopy(session["pending_agreement"])
            session["pending_agreement"] = None
            session["status"] = "locked"
            session["locked_by"] = str(event["locked_by"])
            campaign["tutorial"]["coach_enabled"] = bool(
                session["agreement"].get("coach_enabled", True)
            )
            return
        if event_type == "dnd2024.automation.configured":
            mode = str(event.get("mode") or "")
            if mode not in AUTOMATION_MODES:
                raise EventBatchError("D&D automation mode is invalid")
            campaign["automation"] = {
                "mode": mode,
                "configured_by": str(event.get("configured_by") or ""),
            }
            return
        if event_type == "dnd2024.campaign.proposal_created":
            proposal = {
                key: deepcopy(value) for key, value in event.items() if key != "type"
            }
            campaign["proposals"][str(proposal["proposal_id"])] = proposal
            return
        if event_type == "dnd2024.campaign.proposal_resolved":
            proposal = campaign["proposals"].get(str(event["proposal_id"]))
            if proposal is None or proposal.get("status") != "pending":
                raise EventBatchError("campaign proposal is not pending")
            proposal["status"] = "confirmed" if event["option"] == "confirm" else "rejected"
            proposal["resolved_by"] = str(event["resolved_by"])
            return
        if event_type == "dnd2024.campaign.entity_created":
            entity = deepcopy(event["entity"])
            kind = str(entity.get("kind") or "")
            if kind not in ENTITY_KINDS:
                raise EventBatchError("campaign entity kind is invalid")
            campaign["entities"].setdefault(kind, {})[str(entity["id"])] = entity
            return
        if event_type == "dnd2024.party_decision.opened":
            party = campaign.setdefault("party_decision", {})
            party.update({
                "status": "open",
                "step_id": str(event["step_id"]),
                "choice_ids": [str(item) for item in event.get("choice_ids") or []],
                "submitted": {},
            })
            return
        if event_type == "dnd2024.party_decision.submitted":
            party = campaign.setdefault("party_decision", {})
            if str(party.get("status") or "") != "open":
                raise EventBatchError("party decision is not open")
            if str(party.get("step_id") or "") != str(event["step_id"]):
                raise EventBatchError("party decision step does not match")
            submitted = party.setdefault("submitted", {})
            user_id = str(event["user_id"])
            if user_id in submitted:
                raise EventBatchError("player has already submitted a party decision")
            choice_id = str(event["choice_id"])
            if choice_id not in party.get("choice_ids") or user_id == "":
                raise EventBatchError("party decision submission is invalid")
            submitted[user_id] = choice_id
            return
        if event_type == "dnd2024.party_decision.resolved":
            party = campaign.setdefault("party_decision", {})
            if str(party.get("status") or "") != "open":
                raise EventBatchError("party decision is not open")
            if str(party.get("step_id") or "") != str(event["step_id"]):
                raise EventBatchError("party decision step does not match")
            party.update({
                "status": "none", "step_id": "", "choice_ids": [], "submitted": {},
            })
            return
        if event_type == "dnd2024.tutorial.started":
            tutorial = campaign["tutorial"]
            tutorial.update({
                "status": "active",
                "adventure_id": str(event["adventure_id"]),
                "current_step_id": str(event["start_step_id"]),
                "history": [],
                "hints_used": {},
                "started_by": str(event["started_by"]),
            })
            campaign["adventure_binding"] = {
                "adventure_id": str(event["adventure_id"]),
                "world_id": str(event.get("world_id") or ""),
                "recommended_world_id": str(event.get("recommended_world_id") or ""),
                "compatibility": str(event.get("compatibility") or "compatible"),
                "scene_source": str(event.get("scene_source") or "adventure"),
            }
            campaign["party_decision"] = {
                "status": "none", "step_id": "", "choice_ids": [], "submitted": {},
            }
            return
        if event_type == "dnd2024.tutorial.coach_configured":
            campaign["tutorial"]["coach_enabled"] = bool(event["enabled"])
            return
        if event_type == "dnd2024.tutorial.hint_requested":
            hints = campaign["tutorial"].setdefault("hints_used", {})
            step_id = str(event["step_id"])
            hints[step_id] = int(hints.get(step_id, 0) or 0) + 1
            return
        if event_type == "dnd2024.tutorial.choice_applied":
            tutorial = campaign["tutorial"]
            step_id = str(event["step_id"])
            tutorial.setdefault("history", []).append({
                "step_id": step_id,
                "chapter_id": str(event["chapter_id"]),
                "choice_id": str(event["choice_id"]),
                "chosen_by": str(event["chosen_by"]),
            })
            tutorial["current_step_id"] = str(event.get("next_step_id") or "")
            party = campaign.get("party_decision")
            if isinstance(party, dict) and str(party.get("step_id") or "") == step_id:
                party.update({
                    "status": "none", "step_id": "", "choice_ids": [], "submitted": {},
                })
            return
        if event_type == "dnd2024.chapter.summarized":
            summaries = campaign.setdefault("chapter_summaries", [])
            summaries[:] = [
                item for item in summaries if item.get("summary_id") != event["summary_id"]
            ]
            summaries.append({
                key: deepcopy(value) for key, value in event.items()
                if key not in {"type", "memory"}
            })
            return
        if event_type == "dnd2024.tutorial.completed":
            campaign["tutorial"]["status"] = "completed"
            campaign["tutorial"]["current_step_id"] = ""
            campaign["tutorial"]["completed_by"] = str(event["completed_by"])
            return
        raise EventBatchError(f"unsupported campaign event type: {event_type}")
