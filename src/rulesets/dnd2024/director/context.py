"""Build a bounded Director context from the canonical game instance."""

from __future__ import annotations

from typing import Any

from src.engine.modules import narrative_notes, ruleset_runtime

from .contracts import DirectorContext


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def build_director_context(instance: Any, campaign: dict[str, Any] | None = None) -> DirectorContext:
    """Copy only facts needed for a proposal; never expose mutable state."""

    state = ruleset_runtime.state(instance)
    state = state if isinstance(state, dict) else {}
    combat = state.get("combat") if isinstance(state.get("combat"), dict) else {}
    campaign = campaign if isinstance(campaign, dict) else {}
    tutorial = campaign.get("tutorial") if isinstance(campaign.get("tutorial"), dict) else {}
    step = tutorial.get("current_step") if isinstance(tutorial.get("current_step"), dict) else {}
    actions: list[dict[str, str]] = []
    for index, action in enumerate(getattr(instance, "action_queue", []) or []):
        if not isinstance(action, dict):
            continue
        uid = _text(action.get("user_id"), 120)
        text = _text(action.get("text"), 500)
        if not uid or not text:
            continue
        actions.append({"id": f"action:{index}", "user_id": uid, "text": text})
    return DirectorContext(
        scene=_text(narrative_notes.scene(instance), 200),
        world_id=_text(getattr(instance, "world_id", ""), 120),
        actions=tuple(actions),
        combat_status=_text(combat.get("status"), 40) or "none",
        campaign_status=_text(tutorial.get("status"), 40),
        tutorial_step_id=_text(step.get("id"), 120),
        tutorial_choice_count=len(step.get("choices") or []) if isinstance(step.get("choices"), list) else 0,
        party_size=len(getattr(instance, "players", {}) or {}),
        encounter_preset_id=_text(step.get("encounter_preset_id"), 120),
    )
