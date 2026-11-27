"""Fleet Step's relaxation is declared by the granting feature, not by a name.

SRD: "When you take a Bonus Action other than Step of the Wind, you can also
use Step of the Wind immediately after that Bonus Action."  The fact that
matters is *which capability is relaxed*, and it used to live in three
`== "step_of_the_wind"` comparisons spread across the projection, the grant
event, and the forgery gate.  It now lives in the fleet_step declaration
(`free_step_after_bonus_action_for`), so the code asks the data instead of
remembering a name.
"""

from __future__ import annotations

from pathlib import Path

from src.rulesets.dnd2024.features.combat import relaxed_bonus_action_targets
from src.rulesets.dnd2024.features.models import Dnd2024ClassFeatureCatalog
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
ROOT = Path(__file__).resolve().parents[2]


class _Owned:
    """The two fields the relaxation reader looks at."""

    def __init__(self, feature_id: str, parameters: dict) -> None:
        self.id = feature_id
        self.parameters = parameters


def test_the_real_catalog_relaxes_the_step_it_names() -> None:
    bundle = Dnd2024Runtime().load_bundle(LOCALE)
    catalog = Dnd2024ClassFeatureCatalog.from_bundle(bundle)
    owned = [
        definition
        for definition in catalog.features_by_key.values()
        if definition.id in {"fleet_step", "step_of_the_wind", "patient_defense"}
    ]
    assert relaxed_bonus_action_targets(owned) == {"step_of_the_wind": "fleet_step"}


def test_the_relaxation_follows_the_declaration_not_the_name() -> None:
    # 把同一条特性的参数指到别的能力：映射跟着变，说明读者不看能力名。
    owned = [_Owned("fleet_step", {"free_step_after_bonus_action_for": "patient_defense"})]
    assert relaxed_bonus_action_targets(owned) == {"patient_defense": "fleet_step"}


def test_a_feature_without_the_parameter_relaxes_nothing() -> None:
    owned = [_Owned("fleet_step", {}), _Owned("step_of_the_wind", {})]
    assert relaxed_bonus_action_targets(owned) == {}


def test_no_combat_module_branches_on_the_capability_name_any_more() -> None:
    """A name comparison here would be a fourth place to keep in sync."""

    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "src" / "rulesets" / "dnd2024" / "combat").rglob("*.py")
        if '"step_of_the_wind"' in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders
