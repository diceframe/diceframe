"""Shape-shifting: one panel shape, one composition path (S0-F25)."""

from .panel import (
    POLICIES,
    POLYMORPH_POLICY,
    SLOTS,
    WILD_SHAPE_POLICY,
    FormPanelError,
    StatPanel,
    compose,
    cr_value,
    panel_from_statblock,
    panel_from_view,
)
from .known import (
    KnownFormsError,
    cr_cap,
    eligible_forms,
    replace_one,
    validate_selection,
)
from .state import (
    EFFECT_POLICIES,
    KNOWN_FORM_COUNT,
    NO_SPELLCASTING,
    RECOMMENDED_FORMS,
    FormState,
    FormStateError,
    assume_form,
    bootstrap_known_forms,
    known_form_count,
    learn_form,
    merged_refs_for,
    mode_for,
    restrictions,
    wild_shape_state,
)

__all__ = [
    "KnownFormsError", "cr_cap", "eligible_forms", "replace_one",
    "validate_selection",
    "EFFECT_POLICIES", "KNOWN_FORM_COUNT", "NO_SPELLCASTING", "POLICIES",
    "POLYMORPH_POLICY", "RECOMMENDED_FORMS", "SLOTS", "WILD_SHAPE_POLICY",
    "FormPanelError", "FormState", "FormStateError", "StatPanel",
    "assume_form", "bootstrap_known_forms", "compose", "cr_value",
    "known_form_count", "learn_form", "merged_refs_for", "mode_for",
    "panel_from_statblock", "panel_from_view", "restrictions",
    "wild_shape_state",
]
