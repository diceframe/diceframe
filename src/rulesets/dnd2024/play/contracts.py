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


def v2_encounter_instance_id(adventure_id: str, node_id: str) -> str:
    """Canonical identity for one Adventure v2 story encounter occurrence."""

    adventure = str(adventure_id or "").strip()
    node = str(node_id or "").strip()
    return f"adventure:{adventure}:{node}" if adventure and node else ""


def resolve_v2_encounter_access(
    instance: Any, adventure: Any, progress: Any,
) -> EncounterAccess:
    """FIX-04 §6.9：从 Adventure v2 进度推导剧情遭遇访问权。

    v2 的权威提示是**当前 active 节点**声明的 ``encounter_ref``（
    ``encounter_profile:<id>``）；预设立即 ``_preset`` 能查到的 canonical id。
    没有 active 遭遇的节点时返回 ``blocked()``（自由玩/沙盒由调用方另行决定）。

    结算状态与 v1 同一套约定：该 encounter instance 出现在 combat（ended）或
    history 里 → resolved；正在进行 → active；否则 pending。
    """

    if not isinstance(adventure, dict) or not isinstance(progress, dict):
        return EncounterAccess.blocked()
    active = [str(item) for item in progress.get("active_nodes") or []]
    if not active:
        return EncounterAccess.blocked()
    nodes = {
        str(node.get("id") or ""): node
        for node in adventure.get("nodes") or []
        if isinstance(node, dict)
    }
    adventure_id = str(adventure.get("id") or "")
    for node_id in active:
        node = nodes.get(node_id)
        if node is None:
            continue
        raw_ref = str(node.get("encounter_ref") or "")
        if ":" not in raw_ref:
            continue
        kind, _, preset_id = raw_ref.partition(":")
        if kind != "encounter_profile" or not preset_id:
            continue
        encounter_id = v2_encounter_instance_id(
            str(progress.get("adventure_id") or adventure_id), node_id,
        )
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
        elif (
            combat.get("status") == "active"
            and combat.get("encounter_instance_id") == encounter_id
        ):
            status = "active"
        elif combat.get("status") == "active":
            status = "blocked"
        else:
            status = "pending"
        return EncounterAccess(
            mode="story",
            status=status,
            encounter_instance_id=encounter_id,
            encounter_preset_id=preset_id,
            origin_step_id=node_id,
            adventure_id=adventure_id,
        )
    return EncounterAccess.blocked()


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
