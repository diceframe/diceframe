"""Shrink-only baseline for callers that still reach module state via GameInstance.

R7-R9 moved runtime state into ``src/engine/modules`` slots, but GameInstance
kept a compatibility property for each moved field.  Callers that read or write
``instance.<field>`` still couple to the aggregate, so a change to a module is
not yet isolated from them.  This guard records the remaining facade usage per
file and property; it must only shrink.  When a PR moves callers onto the
module API, lower or delete the matching entries in the same PR, and delete a
property once nothing outside the aggregate uses it.

A facade is any GameInstance property whose body references a module imported
from ``src.engine.modules``, so new facades are covered automatically.
Attribute access on a name bound by an import (``table_settings.solo_mode``)
is a module call, not facade usage, and is ignored; so is a method call such
as ``api.manual_roll_requests(...)``, because facades are properties.
``getattr/setattr/hasattr(x, "<facade>", ...)`` with a constant name counts.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GAME_INSTANCE = "src/engine/game_instance.py"
MODULES_PACKAGE = "src.engine.modules"
MODULES_DIR = "src/engine/modules/"
REFLECTIVE_ACCESSORS = frozenset({"getattr", "setattr", "hasattr"})

# Module-backed properties currently on GameInstance. Do not raise.
MAX_MODULE_FACADES = 1

# Keys are source file + property name, so edits inside a caller do not make
# the baseline brittle. Do not add entries or raise counts.
FACADE_USAGE: dict[tuple[str, str], int] = {
    ("src/engine/plot_tracker.py", "round_number"): 2,
    ("src/engine/progression.py", "round_number"): 3,
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def module_facades(root: Path) -> frozenset[str]:
    tree = _parse(root / GAME_INSTANCE)
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == MODULES_PACKAGE:
            modules.update(alias.asname or alias.name for alias in node.names)
    instance = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GameInstance"
    )
    return frozenset(
        node.name
        for node in instance.body
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(item, ast.Name) and item.id == "property" for item in node.decorator_list)
        and any(isinstance(item, ast.Name) and item.id in modules for item in ast.walk(node))
    )


def _import_bound_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def scan_facade_usage(root: Path, facades: frozenset[str]) -> Counter[tuple[str, str]]:
    observed: Counter[tuple[str, str]] = Counter()
    source_root = root / "src"
    if not source_root.is_dir():
        return observed
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative == GAME_INSTANCE or relative.startswith(MODULES_DIR):
            continue
        tree = _parse(path)
        imported = _import_bound_names(tree)
        # Facades are properties, so ``x.name(...)`` is some other object's method.
        called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        for node in ast.walk(tree):
            name = _reflective_facade_name(node, facades)
            if name is not None:
                observed[(relative, name)] += 1
                continue
            if not isinstance(node, ast.Attribute) or node.attr not in facades:
                continue
            if id(node) in called:
                continue
            if isinstance(node.value, ast.Name) and node.value.id in imported:
                continue
            observed[(relative, node.attr)] += 1
    return observed


def _reflective_facade_name(node: ast.AST, facades: frozenset[str]) -> str | None:
    """Return the facade named by ``getattr/setattr/hasattr(x, "<facade>", ...)``."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in REFLECTIVE_ACCESSORS
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in facades
    ):
        return None
    return str(node.args[1].value)


def assert_facade_usage_matches(
    observed: Counter[tuple[str, str]],
    baseline: dict[tuple[str, str], int],
) -> None:
    unknown = sorted(set(observed) - set(baseline))
    assert not unknown, (
        f"new GameInstance facade usage, call the owning module instead: {unknown}"
    )
    increased = {
        key: (count, baseline[key])
        for key, count in observed.items()
        if count > baseline[key]
    }
    assert not increased, f"GameInstance facade usage increased: {increased}"
    shrunk = {
        key: (observed.get(key, 0), count)
        for key, count in baseline.items()
        if observed.get(key, 0) < count
    }
    assert not shrunk, f"facade usage shrank, lower FACADE_USAGE to match: {shrunk}"


def test_game_instance_facade_usage_matches_shrink_only_baseline() -> None:
    assert_facade_usage_matches(scan_facade_usage(ROOT, module_facades(ROOT)), FACADE_USAGE)


def test_module_facade_count_does_not_grow() -> None:
    count = len(module_facades(ROOT))
    assert count <= MAX_MODULE_FACADES, (
        f"GameInstance has {count} module-backed properties (maximum {MAX_MODULE_FACADES})"
    )


def test_new_facade_caller_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "src" / "webui" / "services" / "new_service.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def read(instance):\n"
        "    return instance.economy\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="new GameInstance facade usage"):
        assert_facade_usage_matches(
            scan_facade_usage(tmp_path, frozenset({"economy"})), FACADE_USAGE,
        )


def test_module_attribute_access_is_not_facade_usage(tmp_path: Path) -> None:
    source = tmp_path / "src" / "webui" / "services" / "new_service.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from src.engine.modules import table_settings\n"
        "def read(instance):\n"
        "    return table_settings.solo_mode(instance)\n",
        encoding="utf-8",
    )
    assert not scan_facade_usage(tmp_path, frozenset({"solo_mode"}))


def test_removed_facade_caller_requires_baseline_update() -> None:
    observed = Counter(FACADE_USAGE)
    key = next(iter(FACADE_USAGE))
    observed.pop(key)
    with pytest.raises(AssertionError, match="lower FACADE_USAGE"):
        assert_facade_usage_matches(observed, FACADE_USAGE)


def test_reflective_facade_access_is_counted(tmp_path: Path) -> None:
    source = tmp_path / "src" / "webui" / "services" / "new_service.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def read(instance):\n"
        "    setattr(instance, 'economy', {})\n"
        "    return getattr(instance, 'economy', {})\n",
        encoding="utf-8",
    )
    assert scan_facade_usage(tmp_path, frozenset({"economy"})) == Counter(
        {("src/webui/services/new_service.py", "economy"): 2}
    )


def test_method_call_with_facade_name_is_not_facade_usage(tmp_path: Path) -> None:
    source = tmp_path / "src" / "webui" / "routes" / "new_route.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def route(api, game_id):\n"
        "    return api.manual_roll_requests(game_id)\n",
        encoding="utf-8",
    )
    assert not scan_facade_usage(tmp_path, frozenset({"manual_roll_requests"}))


def test_retired_facades_are_not_redefined() -> None:
    from src.engine.game_instance import RETIRED_MODULE_FACADES

    assert not RETIRED_MODULE_FACADES & module_facades(ROOT)


def test_retired_facade_write_fails_instead_of_shadowing() -> None:
    from src.engine.game_instance import RETIRED_MODULE_FACADES, GameInstance

    instance = GameInstance(game_key=("web", "retired", "u"))
    for name in sorted(RETIRED_MODULE_FACADES):
        with pytest.raises(AttributeError, match="was removed"):
            setattr(instance, name, {})
        assert name not in vars(instance)


ROUND4_MISC_RETIRED = frozenset({
    "adventure_progress", "away_control_policy", "death_save_outcomes", "gm_directives",
    "health_events", "health_status", "last_overreach", "last_state_update",
    "last_token_budget_bump", "last_world_events", "last_world_legality",
    "lorebook_timed_state", "pending_combat_results", "play_mode", "quick_actions",
    "round_entity_snapshot", "round_start_snapshot",
})


def test_round4_misc_facades_are_gone() -> None:
    from src.engine.game_instance import RETIRED_MODULE_FACADES, GameInstance

    assert ROUND4_MISC_RETIRED <= RETIRED_MODULE_FACADES
    instance = GameInstance(game_key=("web", "retired-misc", "u"))
    for name in sorted(ROUND4_MISC_RETIRED):
        assert not hasattr(GameInstance, name), name
        assert not hasattr(instance, name), name
