"""Enforce the dependency boundaries documented in ``docs/ARCHITECTURE_CN.md``.

The audit intentionally checks structural invariants that are cheap and stable:
core code cannot import the WebUI, service modules do not call sibling services,
the turn HTTP handlers stay thin, and the local Python import graph is acyclic.
"""

from __future__ import annotations

import ast
import importlib.util
from graphlib import CycleError, TopologicalSorter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CORE_PACKAGES = {
    "bots",
    "commands",
    "engine",
    "generation",
    "lorebook",
    "memory",
    "plugin_host",
    "rules",
}
TURN_HANDLERS = {"api_action", "api_luck_decision", "api_advance"}
FORBIDDEN_TURN_ATTRIBUTES = {"_reg", "_handler", "registry", "handler"}
INSTANCE_NAMES = {"inst", "instance"}
INSTANCE_STATE_FIELDS = {
    "action_queue",
    "gm_directives",
    "key_facts",
    "last_check",
    "last_checks",
    "log",
    "pending_actions",
    "pending_payments",
    "players",
    "private_log",
    "quick_actions",
    "summary",
}
MUTATING_METHODS = {"append", "clear", "extend", "insert", "pop", "remove", "setdefault", "update"}


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_import(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = module if is_package else module.rpartition(".")[0]
    target = "." * node.level + (node.module or "")
    try:
        return importlib.util.resolve_name(target, package)
    except (ImportError, ValueError):
        return ""


def _known_target(name: str, modules: set[str]) -> str | None:
    candidate = name
    while candidate:
        if candidate in modules:
            return candidate
        candidate = candidate.rpartition(".")[0]
    return None


def _imports(path: Path, module: str, modules: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    is_package = path.name == "__init__.py"
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import(module, is_package, node)
            names.append(base)
            if not node.module:
                names.extend(f"{base}.{alias.name}" for alias in node.names)
        for name in names:
            target = _known_target(name, modules)
            if target and target != module:
                found.add(target)
    return found


def _turn_handler_errors(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    errors: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) or node.name not in TURN_HANDLERS:
            continue
        used = {
            child.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Attribute) and child.attr in FORBIDDEN_TURN_ATTRIBUTES
        }
        if used:
            errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.name} accesses {sorted(used)}")
    return errors


def _instance_state_reference(node: ast.AST) -> tuple[str, str] | None:
    current = node
    while isinstance(current, ast.Subscript):
        current = current.value
    if not isinstance(current, ast.Attribute) or current.attr not in INSTANCE_STATE_FIELDS:
        return None
    if isinstance(current.value, ast.Name) and current.value.id in INSTANCE_NAMES:
        return current.value.id, current.attr
    return None


def _state_mutation_errors(path: Path) -> list[str]:
    if path == SRC / "engine" / "game_instance.py":
        return []
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    errors: list[str] = []
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, (ast.Assign, ast.Delete)):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            reference = _instance_state_reference(target)
            if reference:
                errors.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: direct {reference[0]}.{reference[1]} mutation"
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in MUTATING_METHODS
        ):
            reference = _instance_state_reference(node.func.value)
            if reference:
                errors.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: direct {reference[0]}.{reference[1]}.{node.func.attr}()"
                )
    return errors


def main() -> int:
    paths = sorted(SRC.rglob("*.py"))
    path_by_module = {_module_name(path): path for path in paths}
    modules = set(path_by_module)
    graph = {
        module: _imports(path, module, modules)
        for module, path in path_by_module.items()
    }
    errors: list[str] = []

    for module, dependencies in graph.items():
        parts = module.split(".")
        if len(parts) >= 2 and parts[0] == "src" and parts[1] in CORE_PACKAGES:
            for dependency in dependencies:
                if dependency == "src.webui" or dependency.startswith("src.webui."):
                    errors.append(f"core -> webui: {module} imports {dependency}")

        if module.startswith("src.webui.services.") and module != "src.webui.services._common":
            for dependency in dependencies:
                if (
                    dependency.startswith("src.webui.services.")
                    and dependency != "src.webui.services._common"
                ):
                    errors.append(f"service -> service: {module} imports {dependency}")

    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        cycle = " -> ".join(str(item) for item in (exc.args[1] if len(exc.args) > 1 else []))
        errors.append(f"import cycle: {cycle or exc}")

    errors.extend(_turn_handler_errors(SRC / "webui" / "routes" / "games.py"))
    for path in paths:
        errors.extend(_state_mutation_errors(path))

    print(f"Architecture audit: {len(modules)} modules, {sum(map(len, graph.values()))} local imports")
    if errors:
        for error in errors:
            print(f"  ERROR: {error}")
        return 1
    print("  OK: dependency boundaries and turn route facade are intact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
