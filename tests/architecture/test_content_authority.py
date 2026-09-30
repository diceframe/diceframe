"""Shrink-only baseline for the remaining legacy lorebook world callers.

PR A does not pretend that the old ``list_entries(world_id)`` surface is
already gone.  It records the current debt so later Track C PRs can remove
callers one at a time without permitting a new runtime dependency on the
legacy world projection.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Keys are source file + enclosing function, rather than line numbers, so
# harmless edits inside a legacy caller do not make the baseline brittle.
LEGACY_WORLD_LIST_ENTRIES: dict[tuple[str, str], int] = {
    ("src/commands/round_actions.py", "initialize_puzzles_from_lorebook"): 1,
    ("src/lorebook/retrieval.py", "ensure_world"): 1,
    ("src/webui/services/characters.py", "list_characters"): 1,
    ("src/webui/services/game_creation_phases.py", "copy_lorebook_entries"): 1,
    ("src/webui/services/knowledge.py", "preview"): 1,
    ("src/webui/services/plugins.py", "export_content_pack"): 1,
    ("src/webui/services/worlds.py", "_user_template_from_lore"): 1,
    ("src/webui/services/worlds.py", "list_entries"): 1,
    ("src/webui/services/worlds.py", "import_entries"): 1,
    ("src/webui/services/worlds.py", "_prepare_entry"): 1,
    ("src/webui/services/worlds.py", "generate_lorebook_entries"): 1,
    ("src/webui/services/worlds.py", "_sync_user_template_lorebook"): 1,
}


def _references_world_id(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Name) and "world" in item.id.lower()
        or isinstance(item, ast.Attribute) and item.attr == "world_id"
        for item in ast.walk(node)
    )


class _WorldListEntriesVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self.stack: list[str] = []
        self.hits: Counter[tuple[str, str]] = Counter()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "list_entries"
            and node.args
            and _references_world_id(node.args[0])
        ):
            key = (self.path.relative_to(self.root).as_posix(), ".".join(self.stack) or "<module>")
            self.hits[key] += 1
        self.generic_visit(node)


def scan_legacy_world_list_entries(root: Path) -> Counter[tuple[str, str]]:
    observed: Counter[tuple[str, str]] = Counter()
    source_root = root / "src"
    if not source_root.is_dir():
        return observed
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        visitor = _WorldListEntriesVisitor(path, root)
        visitor.visit(tree)
        observed.update(visitor.hits)
    return observed


def assert_shrink_only(
    observed: Counter[tuple[str, str]],
    baseline: dict[tuple[str, str], int],
) -> None:
    unknown = sorted(set(observed) - set(baseline))
    assert not unknown, f"new legacy world list_entries callers: {unknown}"
    increased = {
        key: (count, baseline[key])
        for key, count in observed.items()
        if count > baseline[key]
    }
    assert not increased, f"legacy world list_entries caller count increased: {increased}"


def test_legacy_world_list_entries_match_shrink_only_baseline() -> None:
    assert_shrink_only(scan_legacy_world_list_entries(ROOT), LEGACY_WORLD_LIST_ENTRIES)


def test_new_legacy_world_list_entries_caller_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "src" / "webui" / "services" / "new_service.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def read(lorebook, world_id):\n"
        "    return lorebook.list_entries(world_id)\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="new legacy world"):
        assert_shrink_only(scan_legacy_world_list_entries(tmp_path), LEGACY_WORLD_LIST_ENTRIES)


def test_removed_legacy_world_list_entries_caller_is_allowed() -> None:
    observed = Counter(LEGACY_WORLD_LIST_ENTRIES)
    observed.pop(("src/webui/services/worlds.py", "list_entries"))
    assert_shrink_only(observed, LEGACY_WORLD_LIST_ENTRIES)
