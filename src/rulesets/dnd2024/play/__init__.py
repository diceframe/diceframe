"""Explicit campaign-to-combat integration boundary for D&D 2024."""

from .contracts import (
    EncounterAccess,
    resolve_story_encounter_access,
    resolve_v2_encounter_access,
    story_encounter_instance_id,
    v2_encounter_instance_id,
)
from .timeline import is_public_story_milestone, public_timeline_projection

__all__ = [
    "EncounterAccess",
    "resolve_story_encounter_access",
    "resolve_v2_encounter_access",
    "story_encounter_instance_id",
    "v2_encounter_instance_id",
    "is_public_story_milestone",
    "public_timeline_projection",
]
