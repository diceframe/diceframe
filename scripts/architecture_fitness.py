"""Architecture debt scanners used by CI fitness-function tests.

The scanners deliberately operate on dependency-bearing syntax rather than
source snapshots.  Existing debt is recorded by the tests as an explicit
allowlist; new files, dependency modes, concrete runtime identifiers, event
namespaces, or concrete frontend imports therefore fail the architecture gate.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True, order=True)
class DependencyDebt:
    """One stable dependency-boundary violation discovered in production code."""

    path: str
    kind: str
    target: str


_BACKEND_GENERIC_DIRECTORIES = (
    "src/commands",
    "src/engine",
    "src/llm",
    "src/webui/services",
)
_PYTHON_SUFFIX = ".py"
_FRONTEND_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx", ".vue"})
_CONCRETE_RULESET_MODULE = "src.rulesets.dnd2024"
_CONCRETE_RUNTIME_ID = "core:dnd2024"
_CONCRETE_EVENT_PREFIX = "dnd2024."
_FRONTEND_SOURCE_ROOT = "frontend-v2/src"
_CONCRETE_FRONTEND_OWNER = "frontend-v2/src/features/rulesets/dnd2024"
# This registry is the composition root that maps runtime identities to their
# concrete, lazily loaded frontend implementations.  No other generic source
# file is exempt from the concrete-ruleset import boundary.
_FRONTEND_CONCRETE_IMPORT_EXEMPTIONS = frozenset(
    {"frontend-v2/src/features/rulesets/registry.ts"}
)

# These patterns only inspect module specifiers in static import/export
# declarations and dynamic import() expressions.  They do not match arbitrary
# strings or UI copy in Vue/TypeScript source.
_STATIC_IMPORT = re.compile(
    r"(?:^|[;\n])\s*(?:import|export)\s+(?:[^'\"\n]*?\s+from\s+)?['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
_DYNAMIC_IMPORT = re.compile(r"\bimport\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _python_files(root: Path, directories: Iterable[str]) -> Iterable[Path]:
    for directory in directories:
        base = root / directory
        if base.exists():
            yield from sorted(base.rglob(f"*{_PYTHON_SUFFIX}"))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _function_arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterable[ast.arg]:
    yield from node.args.posonlyargs
    yield from node.args.args
    yield from node.args.kwonlyargs
    if node.args.vararg is not None:
        yield node.args.vararg
    if node.args.kwarg is not None:
        yield node.args.kwarg


def _python_package_parts(root: Path, path: Path) -> tuple[str, ...]:
    return path.relative_to(root).with_suffix("").parts[:-1]


def _resolve_import_from(root: Path, path: Path, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = _python_package_parts(root, path)
    parents_to_drop = node.level - 1
    if parents_to_drop > len(package):
        return ""
    base = package[: len(package) - parents_to_drop]
    if node.module:
        base = (*base, *node.module.split("."))
    return ".".join(base)


def _import_targets(
    root: Path,
    path: Path,
    node: ast.Import | ast.ImportFrom,
) -> Iterable[str]:
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
        return
    base = _resolve_import_from(root, path, node)
    if base:
        yield base
    for alias in node.names:
        if alias.name == "*":
            continue
        yield f"{base}.{alias.name}" if base else alias.name


def _webapi_annotation_names(
    root: Path,
    path: Path,
    tree: ast.Module,
) -> tuple[set[str], set[str]]:
    type_names = {"WebAPI"}
    module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "src.webui.api":
                    module_names.add(alias.asname or alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        base = _resolve_import_from(root, path, node)
        for alias in node.names:
            imported = f"{base}.{alias.name}" if base else alias.name
            bound = alias.asname or alias.name
            if imported == "src.webui.api.WebAPI":
                type_names.add(bound)
            elif imported == "src.webui.api":
                module_names.add(bound)
    return type_names, module_names


def _attribute_name(node: ast.Attribute) -> str:
    parts = [node.attr]
    value: ast.expr = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _mentions_webapi(
    annotation: ast.expr | None,
    type_names: set[str],
    module_names: set[str],
) -> bool:
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in type_names:
            return True
        if isinstance(node, ast.Attribute) and node.attr == "WebAPI":
            qualified = _attribute_name(node)
            if qualified == "src.webui.api.WebAPI" or any(
                qualified == f"{module}.WebAPI" for module in module_names
            ):
                return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            reference = node.value.strip()
            if reference in type_names or reference == "src.webui.api.WebAPI" or any(
                reference == f"{module}.WebAPI" for module in module_names
            ):
                return True
            try:
                parsed = ast.parse(reference, mode="eval").body
            except SyntaxError:
                continue
            if not isinstance(parsed, ast.Constant) and _mentions_webapi(
                parsed,
                type_names,
                module_names,
            ):
                return True
    return False


def scan_service_locator_debt(root: Path) -> set[DependencyDebt]:
    """Find services that receive or reach back through the WebAPI facade."""

    found: set[DependencyDebt] = set()
    for path in _python_files(root, ("src/webui/services",)):
        relative = _relative(root, path)
        tree = _tree(path)
        webapi_type_names, webapi_module_names = _webapi_annotation_names(
            root, path, tree,
        )
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if any(
                    target == "src.webui.api"
                    or target.startswith("src.webui.api.")
                    for target in _import_targets(root, path, node)
                ):
                    found.add(
                        DependencyDebt(relative, "webapi_import", "src.webui.api")
                    )

            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            facade_arguments = {
                argument.arg: argument
                for argument in _function_arguments(node)
                if _mentions_webapi(
                    argument.annotation,
                    webapi_type_names,
                    webapi_module_names,
                )
            }
            if not facade_arguments:
                continue
            found.add(DependencyDebt(relative, "webapi_parameter", "WebAPI"))

            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Attribute)
                    and isinstance(child.value, ast.Name)
                    and child.value.id in facade_arguments
                    and child.attr.startswith("_")
                    and not child.attr.startswith("__")
                ):
                    found.add(
                        DependencyDebt(
                            relative,
                            "private_facade_access",
                            "WebAPI._*",
                        )
                    )
                elif (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id in facade_arguments
                    and not child.func.attr.startswith("_")
                ):
                    found.add(
                        DependencyDebt(
                            relative,
                            "facade_service_call",
                            "WebAPI.<service>()",
                        )
                    )
    return found


def scan_backend_concrete_ruleset_debt(root: Path) -> set[DependencyDebt]:
    """Find concrete D&D knowledge in generic backend production paths."""

    found: set[DependencyDebt] = set()
    for path in _python_files(root, _BACKEND_GENERIC_DIRECTORIES):
        relative = _relative(root, path)
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in _import_targets(root, path, node):
                    if module == _CONCRETE_RULESET_MODULE or module.startswith(
                        f"{_CONCRETE_RULESET_MODULE}."
                    ):
                        found.add(DependencyDebt(relative, "concrete_import", module))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value == _CONCRETE_RUNTIME_ID:
                    found.add(DependencyDebt(relative, "runtime_id", node.value))
                elif node.value.startswith(_CONCRETE_EVENT_PREFIX):
                    found.add(DependencyDebt(relative, "event_type", node.value))
    return found


# The D&D 2024 combat package legitimately owns its own mechanics.  What the
# baseline records is how many places name a *concrete* feature or switch: a new
# class feature that adds another branch is a reviewed decision, not an
# accident.  See docs/internal issue GAP-43 and ADR-0004.
_COMBAT_PACKAGE = "src/rulesets/dnd2024/combat"
_FEATURE_COMPARISON_NAMES = frozenset({"feature_id", "granted_by"})
_FEATURE_LITERAL_CALLS = frozenset({"has_feature", "feature_value"})


def _string_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def scan_combat_feature_coupling(root: Path) -> set[DependencyDebt]:
    """Find per-feature branches and per-action switches in a ruleset's combat package.

    Three shapes are recorded, all keyed by file and symbol:

    - ``kind_branch``: a comparison against ``<something>.kind``,
    - ``feature_branch``: a comparison against a feature id or ``granted_by``,
    - ``feature_literal``: ``has_feature`` / ``feature_value`` naming a concrete
      feature,
    - ``intent_switch``: a key read off an ``intent`` (``intent.get("x")`` or
      ``intent["x"]``).  Boolean flags on intents are the pattern GAP-43 wants
      retired; this makes every new one visible.
    """

    found: set[DependencyDebt] = set()
    package = root / _COMBAT_PACKAGE
    if not package.is_dir():
        return found
    for path in sorted(package.glob(f"*{_PYTHON_SUFFIX}")):
        relative = _relative(root, path)
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Compare):
                for op, comparator in zip(node.ops, node.comparators):
                    if not isinstance(op, (ast.Eq, ast.NotEq)):
                        continue
                    for left, right in ((node.left, comparator), (comparator, node.left)):
                        value = _string_literal(right)
                        if value is None:
                            continue
                        if isinstance(left, ast.Attribute) and left.attr == "kind":
                            found.add(DependencyDebt(relative, "kind_branch", value))
                        elif (
                            isinstance(left, ast.Attribute)
                            and left.attr in _FEATURE_COMPARISON_NAMES
                        ) or (
                            isinstance(left, ast.Name)
                            and left.id in _FEATURE_COMPARISON_NAMES
                        ):
                            found.add(DependencyDebt(relative, "feature_branch", value))
            elif isinstance(node, ast.Call):
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr == "get" and node.args:
                    base = func.value
                    if isinstance(base, ast.Name) and base.id == "intent":
                        value = _string_literal(node.args[0])
                        if value is not None:
                            found.add(DependencyDebt(relative, "intent_switch", value))
                elif func.attr in _FEATURE_LITERAL_CALLS and len(node.args) >= 2:
                    value = _string_literal(node.args[1])
                    if value is not None:
                        found.add(
                            DependencyDebt(relative, "feature_literal", f"{func.attr}:{value}")
                        )
            elif isinstance(node, ast.Subscript):
                base = node.value
                if isinstance(base, ast.Name) and base.id == "intent":
                    value = _string_literal(node.slice)
                    if value is not None:
                        found.add(DependencyDebt(relative, "intent_switch", value))
    return found


def _frontend_module_specifiers(source: str) -> Iterable[str]:
    for pattern in (_STATIC_IMPORT, _DYNAMIC_IMPORT):
        yield from (match.group(1) for match in pattern.finditer(source))


def _resolve_frontend_import(root: Path, path: Path, module: str) -> str | None:
    normalized = module.replace("\\", "/").split("?", 1)[0].split("#", 1)[0]
    source_root = root / _FRONTEND_SOURCE_ROOT
    if normalized == "@":
        target = source_root
    elif normalized.startswith("@/"):
        target = source_root / normalized[2:]
    elif normalized.startswith("."):
        target = path.parent / normalized
    else:
        return None
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def scan_frontend_concrete_ruleset_debt(root: Path) -> set[DependencyDebt]:
    """Find concrete D&D imports outside the D&D-owned frontend feature."""

    source_root = root / _FRONTEND_SOURCE_ROOT
    found: set[DependencyDebt] = set()
    if not source_root.exists():
        return found
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.suffix not in _FRONTEND_SUFFIXES:
            continue
        relative = _relative(root, path)
        if relative == _CONCRETE_FRONTEND_OWNER or relative.startswith(
            f"{_CONCRETE_FRONTEND_OWNER}/"
        ):
            continue
        if relative in _FRONTEND_CONCRETE_IMPORT_EXEMPTIONS:
            continue
        source = path.read_text(encoding="utf-8-sig")
        for module in _frontend_module_specifiers(source):
            resolved = _resolve_frontend_import(root, path, module)
            if resolved == _CONCRETE_FRONTEND_OWNER or (
                resolved is not None
                and resolved.startswith(f"{_CONCRETE_FRONTEND_OWNER}/")
            ):
                found.add(DependencyDebt(relative, "concrete_import", resolved))
    return found


def assert_debt_matches_allowlist(
    actual: set[DependencyDebt],
    allowed: set[DependencyDebt] | frozenset[DependencyDebt],
    *,
    boundary: str,
) -> None:
    """Require the allowlist to exactly describe current debt.

    Exact matching makes stale entries fail after a migration, forcing the
    allowlist to shrink.  Unlisted entries fail immediately when debt grows.
    """

    unexpected = sorted(actual - allowed)
    stale = sorted(allowed - actual)
    if not unexpected and not stale:
        return
    details = [f"{boundary} debt baseline changed:"]
    details.extend(f"  unexpected: {item}" for item in unexpected)
    details.extend(f"  stale allowlist entry: {item}" for item in stale)
    raise AssertionError("\n".join(details))


def debt_by_path(debt: Iterable[DependencyDebt]) -> Mapping[str, tuple[DependencyDebt, ...]]:
    """Group debt for concise diagnostics and release/report summaries."""

    grouped: dict[str, list[DependencyDebt]] = {}
    for item in sorted(debt):
        grouped.setdefault(item.path, []).append(item)
    return {path: tuple(items) for path, items in grouped.items()}
