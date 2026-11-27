"""Shrink-only baseline for core flow code that names capability modules (#515).

Master plan §7.1: deleting any capability module must leave a playable game
that only lacks that capability.  Today the generic create / round / snapshot /
projection paths call capability modules by name, so removing one breaks the
core flow.  This guard records those call sites per core file and module; it
must only shrink.  When a PR moves a module onto a lifecycle hook (or otherwise
stops the core flow from naming it), lower or delete the matching entries in
the same PR.

Every module under ``src/engine/modules`` is a capability unless it is listed
in ``KERNEL_MODULES``, so new modules are covered automatically.  A reference
is attribute access on an imported module (``media.scene_image``) or a name
imported from it (``from ...media import scene_image``).

``scripts/audit_module_pluggability.py`` measures the runtime side (which
core-flow tests fail with a module's API stubbed out); it is a report, not a
CI gate, because its numbers move whenever tests are added.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULES_PACKAGE = "src.engine.modules"
MODULES_DIR = "src/engine/modules"

# Turn, check, snapshot, binding and statistics state the core flow is built
# on; these are not required to be removable.
KERNEL_MODULES = frozenset({
    "adventure_runtime_state",
    "checks",
    "content_binding",
    "progression_state",
    "round_safety",
    "ruleset_runtime",
    "session_stats",
    # Credentials and export scrubbing must never disappear as an optional
    # capability: neutral defaults would open protected rooms or leak tokens.
    "room_access",
    # The transaction ledger is required for settlement and round reversal.
    # Optional economy presentation is separate from this authoritative state.
    "economy_state",
    # Settlement archives and preparation barriers remain authoritative even
    # when their UI/projection hooks are detached.
    "run_finale",
    "skill_growth",  # settled receipts must forbid historical double claims
    "resource_recovery",  # one-use qualifications remain authoritative without UI/hooks
    "game_config",  # difficulty, turn model and run identity configuration
})

# Generic game creation, round lifecycle, snapshot, persistence, projection
# and context paths.  game_state_codec is left out on purpose: encoding module
# slots is the storage contract every module keeps, not a capability call.
CORE_FLOW_FILES = frozenset({
    "src/commands/game_lifecycle.py",
    "src/commands/prompt_composer.py",
    "src/commands/round_actions.py",
    "src/commands/round_checks.py",
    "src/commands/round_effects.py",
    "src/commands/round_helpers.py",
    "src/commands/round_media.py",
    "src/commands/round_processor.py",
    "src/commands/swipe_generator.py",
    "src/engine/game_context_projector.py",
    "src/engine/game_instance.py",
    "src/engine/instance_lifecycle.py",
    "src/engine/persistence.py",
    "src/engine/round_recovery.py",
    "src/engine/round_snapshots.py",
    "src/engine/turn_state.py",
    "src/llm/context_builder.py",
    "src/webui/services/game_creation_phases.py",
    "src/webui/services/game_lifecycle.py",
    "src/webui/services/game_queries.py",
    "src/webui/services/logs.py",
    "src/webui/services/turns.py",
})

# Keys are core file + capability module.  Do not add entries or raise counts.
CORE_COUPLING: dict[tuple[str, str], int] = {
    # media.payload_container validates a raw save package (storage contract, like fresh/ensure).
    ("src/engine/persistence.py", "media"): 1,
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def module_names(root: Path) -> frozenset[str]:
    return frozenset(
        path.stem for path in (root / MODULES_DIR).glob("*.py") if path.stem != "__init__"
    )


def capability_modules(root: Path) -> frozenset[str]:
    return module_names(root) - KERNEL_MODULES


def _module_bindings(
    tree: ast.Module, capabilities: frozenset[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Local names bound to a capability module, and to names imported from one."""
    modules: dict[str, str] = {}
    members: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == MODULES_PACKAGE:
                for alias in node.names:
                    if alias.name in capabilities:
                        modules[alias.asname or alias.name] = alias.name
            elif node.module.startswith(MODULES_PACKAGE + "."):
                module = node.module.rsplit(".", 1)[1]
                if module in capabilities:
                    for alias in node.names:
                        members[alias.asname or alias.name] = module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not (alias.name.startswith(MODULES_PACKAGE + ".") and alias.asname):
                    continue
                module = alias.name.rsplit(".", 1)[1]
                if module in capabilities:
                    modules[alias.asname] = module
    return modules, members


def scan_core_coupling(
    root: Path, core_files: frozenset[str], capabilities: frozenset[str],
) -> Counter[tuple[str, str]]:
    observed: Counter[tuple[str, str]] = Counter()
    for relative in sorted(core_files):
        path = root / relative
        if not path.is_file():
            continue
        tree = _parse(path)
        modules, members = _module_bindings(tree, capabilities)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in modules
            ):
                observed[(relative, modules[node.value.id])] += 1
            elif isinstance(node, ast.Name) and node.id in members:
                observed[(relative, members[node.id])] += 1
    return observed


def assert_core_coupling_matches(
    observed: Counter[tuple[str, str]],
    baseline: dict[tuple[str, str], int],
) -> None:
    unknown = sorted(set(observed) - set(baseline))
    assert not unknown, (
        "core flow now names a capability module; register a lifecycle hook "
        f"or keep the call inside the capability instead: {unknown}"
    )
    increased = {
        key: (count, baseline[key])
        for key, count in observed.items()
        if count > baseline[key]
    }
    assert not increased, f"core flow coupling to capability modules increased: {increased}"
    shrunk = {
        key: (observed.get(key, 0), count)
        for key, count in baseline.items()
        if observed.get(key, 0) < count
    }
    assert not shrunk, f"core flow coupling shrank, lower CORE_COUPLING to match: {shrunk}"


def test_core_flow_coupling_matches_shrink_only_baseline() -> None:
    assert_core_coupling_matches(
        scan_core_coupling(ROOT, CORE_FLOW_FILES, capability_modules(ROOT)), CORE_COUPLING,
    )


def test_core_flow_files_and_kernel_modules_exist() -> None:
    missing = sorted(name for name in CORE_FLOW_FILES if not (ROOT / name).is_file())
    assert not missing, f"core flow file moved or renamed, update CORE_FLOW_FILES: {missing}"
    unknown = sorted(KERNEL_MODULES - module_names(ROOT))
    assert not unknown, f"KERNEL_MODULES names a module that no longer exists: {unknown}"


def test_baseline_only_names_core_files_and_capabilities() -> None:
    capabilities = capability_modules(ROOT)
    stray = sorted(
        key for key in CORE_COUPLING
        if key[0] not in CORE_FLOW_FILES or key[1] not in capabilities
    )
    assert not stray, f"CORE_COUPLING entries outside the guarded scope: {stray}"


def _hook_registrations(tree: ast.Module) -> list[int]:
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "module_hooks"
    ]


def test_only_modules_register_lifecycle_hooks() -> None:
    """The core flow calls hook points; only capability modules attach to them."""
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{line}"
        for path in sorted((ROOT / "src").rglob("*.py"))
        if not path.relative_to(ROOT).as_posix().startswith(MODULES_DIR + "/")
        for line in _hook_registrations(_parse(path))
    ]
    assert not offenders, f"module_hooks.register outside src/engine/modules: {offenders}"


def test_hook_registry_names_no_module() -> None:
    tree = _parse(ROOT / "src" / "engine" / "module_hooks.py")
    imports = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    ]
    assert not [name for name in imports if name and name.startswith("src.")], imports


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_scan_counts_module_attributes_and_imported_names(tmp_path: Path) -> None:
    _write(tmp_path, "src/engine/core.py", (
        "from src.engine.modules import media, checks\n"
        "from src.engine.modules.health import record_health_event as record\n"
        "import src.engine.modules.world_reports as reports\n"
        "def run(instance):\n"
        "    media.scene_image(instance)\n"
        "    checks.last_check(instance)\n"
        "    record(instance, 'x')\n"
        "    return reports.last_world_events(instance)\n"
    ))
    observed = scan_core_coupling(
        tmp_path, frozenset({"src/engine/core.py"}),
        frozenset({"media", "health", "world_reports"}),
    )
    assert observed == Counter({
        ("src/engine/core.py", "media"): 1,
        ("src/engine/core.py", "health"): 1,
        ("src/engine/core.py", "world_reports"): 1,
    })


def test_scan_ignores_files_outside_the_core_flow(tmp_path: Path) -> None:
    _write(tmp_path, "src/webui/services/media_service.py", (
        "from src.engine.modules import media\n"
        "def read(instance):\n"
        "    return media.scene_image(instance)\n"
    ))
    assert not scan_core_coupling(
        tmp_path, frozenset({"src/engine/core.py"}), frozenset({"media"}),
    )


def test_new_core_coupling_is_rejected() -> None:
    observed = Counter(CORE_COUPLING)
    observed[("src/engine/game_instance.py", "brand_new_capability")] += 1
    with pytest.raises(AssertionError, match="register a lifecycle hook"):
        assert_core_coupling_matches(observed, CORE_COUPLING)


def test_removed_core_coupling_requires_baseline_update() -> None:
    observed = Counter(CORE_COUPLING)
    key = next(iter(CORE_COUPLING))
    observed[key] -= 1
    with pytest.raises(AssertionError, match="lower CORE_COUPLING"):
        assert_core_coupling_matches(observed, CORE_COUPLING)
