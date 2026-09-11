"""Pure contracts shared by D&D campaign, combat, and UI projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


EncounterMode = Literal["blocked", "story", "sandbox"]
EncounterStatus = Literal["blocked", "pending", "active", "resolved"]


def story_encounter_instance_id(adventure_id: str, step_id: str) -> str:
    """Build the canonical identity for one story encounter occurrence."""
    adventure = str(adventure_id or "").strip()
    step = str(step_id or "").strip()
    return f"tutorial:{adventure}:{step}" if adventure and step else ""


@dataclass(frozen=True, slots=True)
class EncounterAccess:
    """Runtime-issued capability for starting an encounter.

    Combat owns mechanics; the runtime composition root owns whether the story
    currently grants access. Direct combat-engine tools must opt into sandbox
    access explicitly; the default is closed.
    """

    mode: EncounterMode = "blocked"
    status: EncounterStatus = "blocked"
    encounter_instance_id: str = ""
    encounter_preset_id: str = ""
    origin_step_id: str = ""
    adventure_id: str = ""
    # 活动冒险包存在，但当前剧情没有给出合法 canonical 遭遇绑定时为 True。
    # 这种状态必须对 GM 可解释，且不得静默降级为通用训练预设。
    unprepared: bool = False

    @property
    def can_start(self) -> bool:
        return self.mode in {"story", "sandbox"} and self.status == "pending"

    @classmethod
    def blocked(cls) -> "EncounterAccess":
        return cls()

    @classmethod
    def sandbox(cls) -> "EncounterAccess":
        return cls(mode="sandbox", status="pending")

    @classmethod
    def unbound_story(cls, adventure_id: str = "", origin_step_id: str = "") -> "EncounterAccess":
        """An active adventure that has no legal canonical encounter binding."""

        return cls(
            mode="blocked",
            status="blocked",
            adventure_id=str(adventure_id or ""),
            origin_step_id=str(origin_step_id or ""),
            unprepared=True,
        )


def resolve_story_encounter_access(
    instance: Any, campaign: dict[str, Any],
) -> EncounterAccess:
    """Bridge a campaign encounter step to authoritative combat access."""

    tutorial = campaign.get("tutorial") if isinstance(campaign, dict) else None
    tutorial = tutorial if isinstance(tutorial, dict) else {}
    step = tutorial.get("current_step")
    if (
        tutorial.get("status") != "active"
        or not isinstance(step, dict)
        or str(step.get("requires") or "") != "combat_ended"
    ):
        return EncounterAccess.blocked()

    step_id = str(step.get("id") or "")
    adventure = tutorial.get("adventure")
    adventure_id = str(
        tutorial.get("adventure_id")
        or (adventure.get("id") if isinstance(adventure, dict) else "")
        or campaign.get("adventure_binding", {}).get("adventure_id")
        or ""
    )
    encounter_id = story_encounter_instance_id(adventure_id, step_id)
    state = getattr(instance, "ruleset_state", {})
    combat = state.get("combat") if isinstance(state, dict) else None
    combat = combat if isinstance(combat, dict) else {}
    history = state.get("combat_history") if isinstance(state, dict) else []
    history = history if isinstance(history, list) else []
    resolved = (
        combat.get("encounter_instance_id") == encounter_id
        and combat.get("status") == "ended"
    ) or any(
        isinstance(item, dict) and item.get("encounter_instance_id") == encounter_id
        for item in history
    )
    if resolved:
        status: EncounterStatus = "resolved"
    elif combat.get("status") == "active" and combat.get("encounter_instance_id") == encounter_id:
        status = "active"
    elif combat.get("status") == "active":
        status = "blocked"
    else:
        status = "pending"
    return EncounterAccess(
        mode="story",
        status=status,
        encounter_instance_id=encounter_id,
        encounter_preset_id=str(step.get("encounter_preset_id") or ""),
        origin_step_id=step_id,
        adventure_id=adventure_id,
    )
