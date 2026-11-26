"""Debt baselines for dependency directions that are being retired gradually.

The allowlists describe the P0 repository state. They must only shrink: the
scanner rejects both new debt and stale entries left behind after a migration.
Synthetic regressions use ``tmp_path`` so tests never touch runtime data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.architecture_fitness import (
    DependencyDebt,
    assert_debt_matches_allowlist,
    assert_size_hotspots_match_baseline,
    scan_backend_concrete_ruleset_debt,
    scan_combat_feature_coupling,
    scan_file_size_hotspots,
    scan_frontend_concrete_ruleset_debt,
    scan_service_locator_debt,
)
from combat_coupling_baseline import (  # noqa: E402 - sibling baseline module
    COMBAT_COUPLING_ALLOWLIST,
    DECLARED_INTENT_TYPES,
)
from file_size_baseline import SIZE_BASELINE  # noqa: E402 - sibling baseline module

ROOT = Path(__file__).resolve().parents[2]

# Historical service-locator debt. Do not add entries. Remove a file or
# category in the same PR that removes the corresponding dependency.
SERVICE_LOCATOR_ALLOWLIST: frozenset[DependencyDebt] = frozenset()

# Historical concrete-ruleset knowledge in otherwise generic backend modules.
# Each entry is a dependency semantic, not a line/function/source snapshot.
BACKEND_CONCRETE_RULESET_ALLOWLIST: frozenset[DependencyDebt] = frozenset()

# Historical direct imports outside the D&D-owned frontend feature directory.
FRONTEND_CONCRETE_RULESET_ALLOWLIST: frozenset[DependencyDebt] = frozenset()


def test_service_locator_debt_matches_shrink_only_baseline() -> None:
    assert_debt_matches_allowlist(
        scan_service_locator_debt(ROOT),
        SERVICE_LOCATOR_ALLOWLIST,
        boundary="services -> WebAPI",
    )


def test_backend_concrete_ruleset_debt_matches_shrink_only_baseline() -> None:
    assert_debt_matches_allowlist(
        scan_backend_concrete_ruleset_debt(ROOT),
        BACKEND_CONCRETE_RULESET_ALLOWLIST,
        boundary="generic backend -> dnd2024",
    )


def test_frontend_concrete_ruleset_debt_matches_shrink_only_baseline() -> None:
    assert_debt_matches_allowlist(
        scan_frontend_concrete_ruleset_debt(ROOT),
        FRONTEND_CONCRETE_RULESET_ALLOWLIST,
        boundary="generic frontend -> dnd2024",
    )


def test_new_service_locator_dependency_is_rejected(tmp_path: Path) -> None:
    service = tmp_path / "src/webui/services/new_service.py"
    service.parent.mkdir(parents=True)
    service.write_text(
        "from ..api import WebAPI as Facade\n"
        "def run(web_api: Facade):\n"
        "    return web_api._reg.list_all()\n",
        encoding="utf-8",
    )

    debt = scan_service_locator_debt(tmp_path)
    assert DependencyDebt(
        "src/webui/services/new_service.py",
        "webapi_parameter",
        "WebAPI",
    ) in debt
    assert DependencyDebt(
        "src/webui/services/new_service.py",
        "private_facade_access",
        "WebAPI._*",
    ) in debt
    with pytest.raises(AssertionError):
        assert_debt_matches_allowlist(
            debt, set(), boundary="services -> WebAPI"
        )


def test_stale_allowlist_entry_is_rejected() -> None:
    stale = {
        DependencyDebt(
            "src/webui/services/removed.py",
            "webapi_parameter",
            "WebAPI",
        )
    }

    with pytest.raises(AssertionError):
        assert_debt_matches_allowlist(
            set(),
            stale,
            boundary="synthetic stale baseline",
        )


def test_new_generic_backend_concrete_ruleset_import_is_rejected(tmp_path: Path) -> None:
    module = tmp_path / "src/engine/new_runtime_bridge.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from ..rulesets.dnd2024.runtime import Dnd2024Runtime\n",
        encoding="utf-8",
    )

    debt = scan_backend_concrete_ruleset_debt(tmp_path)
    assert DependencyDebt(
        "src/engine/new_runtime_bridge.py",
        "concrete_import",
        "src.rulesets.dnd2024.runtime",
    ) in debt
    with pytest.raises(AssertionError):
        assert_debt_matches_allowlist(
            debt,
            set(),
            boundary="generic backend -> dnd2024",
        )


def test_package_init_relative_ruleset_import_is_rejected(tmp_path: Path) -> None:
    module = tmp_path / "src/engine/__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from ..rulesets.dnd2024.runtime import Dnd2024Runtime\n",
        encoding="utf-8",
    )

    debt = scan_backend_concrete_ruleset_debt(tmp_path)
    assert DependencyDebt(
        "src/engine/__init__.py",
        "concrete_import",
        "src.rulesets.dnd2024.runtime",
    ) in debt


def test_new_generic_frontend_concrete_ruleset_import_is_rejected(tmp_path: Path) -> None:
    component = tmp_path / "frontend-v2/src/features/rulesets/NewPanel.vue"
    component.parent.mkdir(parents=True)
    component.write_text(
        '<script setup lang="ts">\n'
        "import { submitRulesetIntent } from './dnd2024/api'\n"
        "import CombatPanel from '@/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue'\n"
        "</script>\n",
        encoding="utf-8",
    )

    debt = scan_frontend_concrete_ruleset_debt(tmp_path)
    assert DependencyDebt(
        "frontend-v2/src/features/rulesets/NewPanel.vue",
        "concrete_import",
        "frontend-v2/src/features/rulesets/dnd2024/api",
    ) in debt
    assert DependencyDebt(
        "frontend-v2/src/features/rulesets/NewPanel.vue",
        "concrete_import",
        "frontend-v2/src/features/rulesets/dnd2024/combat/Dnd2024CombatPanel.vue",
    ) in debt
    with pytest.raises(AssertionError):
        assert_debt_matches_allowlist(
            debt,
            set(),
            boundary="generic frontend -> dnd2024",
        )


def test_combat_feature_coupling_matches_shrink_only_baseline() -> None:
    """每新增一个具体特性分支或意图开关，都必须是显式登记的评审决定（GAP-43）。"""

    assert_debt_matches_allowlist(
        scan_combat_feature_coupling(ROOT),
        COMBAT_COUPLING_ALLOWLIST,
        boundary="dnd2024 combat -> concrete feature",
    )


def test_declared_intent_types_match_the_baseline() -> None:
    from src.rulesets.dnd2024.combat.primitives import INTENT_TYPES

    assert INTENT_TYPES == DECLARED_INTENT_TYPES


def test_a_new_feature_branch_and_intent_switch_are_rejected(tmp_path: Path) -> None:
    module = tmp_path / "src/rulesets/dnd2024/combat/resolution.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "def resolve(engine, character, action, intent, row):\n"
        "    if action.kind == 'brand_new_feature':\n"
        "        return True\n"
        "    # The same branch, spelled the way a dict row is read.\n"
        "    if row.get('kind') == 'brand_new_row_kind':\n"
        "        return True\n"
        "    if row['kind'] == 'brand_new_subscript_kind':\n"
        "        return True\n"
        "    if engine.features.has_feature(character, 'brand_new_feature'):\n"
        "        return True\n"
        "    if intent.get('brand_new_switch'):\n"
        "        return True\n"
        "    return intent['brand_new_subscript']\n",
        encoding="utf-8",
    )

    debt = scan_combat_feature_coupling(tmp_path)

    for expected in (
        DependencyDebt(
            "src/rulesets/dnd2024/combat/resolution.py",
            "kind_branch", "brand_new_feature",
        ),
        DependencyDebt(
            "src/rulesets/dnd2024/combat/resolution.py",
            "kind_branch", "brand_new_row_kind",
        ),
        DependencyDebt(
            "src/rulesets/dnd2024/combat/resolution.py",
            "kind_branch", "brand_new_subscript_kind",
        ),
        DependencyDebt(
            "src/rulesets/dnd2024/combat/resolution.py",
            "feature_literal", "has_feature:brand_new_feature",
        ),
        DependencyDebt(
            "src/rulesets/dnd2024/combat/resolution.py",
            "intent_switch", "brand_new_switch",
        ),
        DependencyDebt(
            "src/rulesets/dnd2024/combat/resolution.py",
            "intent_switch", "brand_new_subscript",
        ),
    ):
        assert expected in debt, expected
    with pytest.raises(AssertionError):
        assert_debt_matches_allowlist(
            debt,
            set(),
            boundary="dnd2024 combat -> concrete feature",
        )


def test_an_actor_kind_comparison_is_not_feature_coupling(tmp_path: Path) -> None:
    module = tmp_path / "src/rulesets/dnd2024/combat/resolution.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "def resolve(actor, row):\n"
        "    if actor.get('kind') == 'player':\n"
        "        return row['kind'] == 'enemy'\n",
        encoding="utf-8",
    )

    assert scan_combat_feature_coupling(tmp_path) == set()


def test_known_size_hotspots_do_not_grow() -> None:
    """A hotspot may drift a little, never jump; the baseline ratchets down."""

    assert_size_hotspots_match_baseline(
        scan_file_size_hotspots(ROOT),
        SIZE_BASELINE,
        boundary="active-logic source files above 1500 lines",
    )


def _sized(root: Path, relative: str, lines: int) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n" * lines, encoding="utf-8")


def test_size_ratchet_tracks_hotspots_only_and_moves_one_way(tmp_path: Path) -> None:
    _sized(tmp_path, "src/hot.py", 1600)
    _sized(tmp_path, "src/ordinary.py", 1500)
    _sized(tmp_path, "frontend-v2/src/features/View.vue", 1501)
    # Translation dictionaries and wire-type declarations are not active logic.
    _sized(tmp_path, "frontend-v2/src/i18n/messages/en.ts", 3000)
    _sized(tmp_path, "frontend-v2/src/api/types.ts", 2000)

    hotspots = scan_file_size_hotspots(tmp_path)
    assert hotspots == {
        "src/hot.py": 1600, "frontend-v2/src/features/View.vue": 1501,
    }
    recorded = dict(hotspots)

    def ratchet() -> None:
        assert_size_hotspots_match_baseline(
            scan_file_size_hotspots(tmp_path), recorded,
            boundary="synthetic size ratchet",
        )

    ratchet()
    # An ordinary edit in either direction never touches the baseline.
    _sized(tmp_path, "src/hot.py", 1700)
    ratchet()
    _sized(tmp_path, "src/hot.py", 1501)
    ratchet()

    with pytest.raises(AssertionError, match="grew"):
        _sized(tmp_path, "src/hot.py", 1701)
        ratchet()
    with pytest.raises(AssertionError, match="shrunk"):
        _sized(tmp_path, "src/hot.py", 1699)
        recorded["src/hot.py"] = 1800
        ratchet()
    with pytest.raises(AssertionError, match="stale entry"):
        _sized(tmp_path, "src/hot.py", 1500)
        ratchet()
    with pytest.raises(AssertionError, match="new hotspot"):
        _sized(tmp_path, "src/ordinary.py", 1501)
        ratchet()
