"""Runtime module dependency, ownership and aggregate-size boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
MODULES = SRC / "engine" / "modules"
MAX_TOP_LEVEL_FIELDS = 91

CONTROL_WRITERS = {
    SRC / "engine" / "player_control.py",
    SRC / "migrations" / "instance.py",
    # This writes the creation response dict, not the instance's seat state.
    SRC / "webui" / "services" / "game_creation_phases.py",
}


# Direct property assignment ownership only: this does not police aliases,
# subscript mutation, clear/pop, or generic setattr/aggregate replacement.
COMBAT_WRITERS = {
    SRC / "webui" / "services" / "combat_extension.py",
    SRC / "engine" / "round_snapshots.py",
    MODULES / "combat_extension_state.py",
    SRC / "migrations" / "instance.py",
    # Existing lifecycle owner replaces current and clears snapshots on reset.
    SRC / "engine" / "instance_lifecycle.py",
    # Existing rollback/abort owner clears current on invalid restoration.
    SRC / "engine" / "round_recovery.py",
    # Existing historical rewrite owner has the same fail-closed fallback.
    SRC / "commands" / "swipe_generator.py",
}


def _combat_property_writes(path: Path, tree: ast.AST) -> list[int]:
    if path in COMBAT_WRITERS:
        return []
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.attr in {"combat_extension", "combat_extension_round_snapshots"}
    ]


def test_only_combat_owners_assign_compatibility_properties() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line in _combat_property_writes(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: combat property write outside owner")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    "other.combat_extension = {}",
    "self.combat_extension_round_snapshots: dict = {}",
    "other.combat_extension |= {}",
    "other.combat_extension, (x, self.combat_extension_round_snapshots) = values",
    "del other.combat_extension_round_snapshots",
])
def test_combat_guard_rejects_direct_writes_including_game_instance(source) -> None:
    tree = ast.parse(source)
    assert _combat_property_writes(SRC / "engine" / "game_instance.py", tree)
    assert _combat_property_writes(SRC / "webui" / "routes" / "outsider.py", tree)
    for owner in COMBAT_WRITERS:
        assert not _combat_property_writes(owner, tree)


def _runtime_nodes(node: ast.AST):
    """Ignore type-only bodies, but still inspect runtime else branches."""
    yield node
    if isinstance(node, ast.If) and (
        isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        or isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
    ):
        for child in node.orelse:
            yield from _runtime_nodes(child)
        return
    for child in ast.iter_child_nodes(node):
        yield from _runtime_nodes(child)


def test_runtime_modules_do_not_import_game_instance() -> None:
    violations: list[str] = []
    paths = [SRC / "engine" / "module_state.py", *sorted(MODULES.glob("*.py"))]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in _runtime_nodes(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    parts = list(path.relative_to(ROOT).with_suffix("").parts[:-1])
                    module = ".".join(parts[:len(parts) - node.level + 1] + ([module] if module else []))
                names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            if any(name == "src.engine.game_instance" or name.startswith("src.engine.game_instance.") for name in names):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: runtime GameInstance import")
    assert not violations, "\n".join(violations)


def test_only_module_owners_write_module_slots() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.parent == MODULES or path == SRC / "migrations" / "instance.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            # Store/Del also covers annotated/augmented assignments and nested
            # indexing, so a second subscript cannot bypass slot ownership.
            if not isinstance(node, ast.Subscript) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            value = node.value
            while isinstance(value, ast.Subscript):
                value = value.value
            if isinstance(value, ast.Attribute) and value.attr == "modules":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: module slot write outside owner")
    assert not violations, "\n".join(violations)


def test_only_player_control_owner_writes_seat_controls() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in CONTROL_WRITERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            target = node
            while isinstance(target, ast.Subscript):
                key = target.slice
                if (
                    isinstance(key, ast.Constant) and key.value == "control"
                    or isinstance(key, ast.Name) and key.id == "CONTROL_KEY"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: seat control write outside owner")
                    break
                target = target.value
    assert not violations, "\n".join(violations)


def test_only_economy_owners_write_ledger_keys() -> None:
    writers = {
        SRC / "engine" / "economy.py",
        MODULES / "economy_state.py",
        SRC / "migrations" / "instance.py",
        # Known direct outbox writer; converge on an owner API after R2.
        SRC / "engine" / "memory_outbox.py",
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in writers:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            value = node.value
            while isinstance(value, ast.Subscript):
                value = value.value
            if isinstance(value, ast.Attribute) and value.attr == "economy":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: economy key write outside owner")
    assert not violations, "\n".join(violations)


def _imports(path: Path, tree: ast.AST):
    """Resolve ordinary, from-parent and relative imports, including local ones."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                parts = list(path.relative_to(ROOT).with_suffix("").parts[:-1])
                module = ".".join(parts[:len(parts) - node.level + 1] + ([module] if module else []))
            for alias in node.names:
                yield node.lineno, f"{module}.{alias.name}"


def _under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def test_action_gate_does_not_import_transport_commands_or_rulesets() -> None:
    path = SRC / "engine" / "action_gate.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    violations = [
        f"{path.relative_to(ROOT)}:{line}: forbidden import {name}"
        for line, name in _imports(path, tree)
        if any(_under(name, prefix) for prefix in ("src.webui", "src.commands", "src.rulesets"))
    ]
    assert not violations, "\n".join(violations)


def test_engine_does_not_import_commands() -> None:
    # Existing economy effect application imports this reward normalizer locally.
    # Keep that exact debt visible; R4-a does not move economy/item application.
    exceptions = {(SRC / "engine" / "economy.py", "src.commands.state_items.normalized_reward_entries")}
    violations: list[str] = []
    for path in sorted((SRC / "engine").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line, name in _imports(path, tree):
            if _under(name, "src.commands") and (path, name) not in exceptions:
                violations.append(f"{path.relative_to(ROOT)}:{line}: engine imports {name}")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source, prefix", [
    ("import src.commands.ai_player as ai", "src.commands"),
    ("from src import commands", "src.commands"),
    ("from ..commands import ai_player", "src.commands"),
    ("def helper():\n    from src.commands.state_items import normalized_reward_entries", "src.commands"),
    ("from src.webui.services import turns", "src.webui"),
    ("from .. import rulesets", "src.rulesets"),
    ("if TYPE_CHECKING:\n    import src.rulesets.contracts", "src.rulesets"),
])
def test_gate_dependency_guard_resolves_import_forms(source, prefix) -> None:
    path = SRC / "engine" / "action_gate.py"
    assert any(_under(name, prefix) for _, name in _imports(path, ast.parse(source)))


def _round_counter_writes(path: Path, tree: ast.AST) -> list[int]:
    # Field declarations and constructor keywords (e.g. DecisionEntry's independent
    # round_number) are not runtime attribute writes. No file-wide exceptions.
    # Like the other ownership guards, this does not track aliases or setattr.
    if path == SRC / "engine" / "progression.py":
        return []
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.attr == "round_number"
    ]


def test_only_progression_writes_round_counter() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line in _round_counter_writes(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: round counter write outside progression")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    "instance.round_number = 3",
    "self.round_number += 1",
    "instance.round_number: int = 3",
    "other.round_number, (x, self.round_number) = values",
    "del instance.round_number",
])
def test_round_counter_guard_rejects_attribute_writes(source) -> None:
    tree = ast.parse(source)
    for path in ("engine/game_instance.py", "engine/plot_tracker.py", "rulesets/automation.py"):
        assert _round_counter_writes(SRC / path, tree)
    assert not _round_counter_writes(SRC / "engine/progression.py", tree)


def test_round_counter_guard_allows_independent_fields_and_construction() -> None:
    tree = ast.parse("class Decision:\n    round_number: int = 0\nd = Decision(round_number=3)")
    assert not _round_counter_writes(SRC / "engine/plot_tracker.py", tree)


def test_progression_does_not_import_transport_commands_or_rulesets() -> None:
    path = SRC / "engine" / "progression.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    violations = [
        f"{path.relative_to(ROOT)}:{line}: forbidden import {name}"
        for line, name in _imports(path, tree)
        if any(_under(name, prefix) for prefix in ("src.webui", "src.commands", "src.rulesets"))
    ]
    assert not violations, "\n".join(violations)


def test_game_instance_top_level_field_count_does_not_grow() -> None:
    path = SRC / "engine" / "game_instance.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    instance = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "GameInstance")
    count = sum(isinstance(node, ast.AnnAssign) for node in instance.body)
    assert count <= MAX_TOP_LEVEL_FIELDS, f"GameInstance has {count} fields (maximum {MAX_TOP_LEVEL_FIELDS})"
