"""Shape-shifting: one panel shape, one composition path (S0-F25)."""

from .panel import (
    POLICIES,
    POLYMORPH_POLICY,
    SLOTS,
    WILD_SHAPE_POLICY,
    FormPanelError,
    StatPanel,
    compose,
    panel_from_statblock,
    panel_from_view,
)
from .state import (
    EFFECT_POLICIES,
    NO_SPELLCASTING,
    FormState,
    FormStateError,
    assume_form,
    restrictions,
    wild_shape_state,
)

__all__ = [
    "EFFECT_POLICIES", "NO_SPELLCASTING", "POLICIES", "POLYMORPH_POLICY",
    "SLOTS", "WILD_SHAPE_POLICY", "FormPanelError", "FormState",
    "FormStateError", "StatPanel", "assume_form", "compose",
    "panel_from_statblock", "panel_from_view", "restrictions",
    "wild_shape_state",
]
