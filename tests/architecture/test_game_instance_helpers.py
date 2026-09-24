"""GameInstance helper extraction boundary (AST-level).

保护 GameInstance 拆分出来的 helper 模块的依赖方向：

```text
game_instance  →  game_state / round_snapshots / turn_state /
                  round_recovery / instance_lifecycle
```

helper 模块**不得 runtime import** ``src.engine.game_instance``（GameInstance /
GameState 或该模块的任何成员）；``TYPE_CHECKING`` 下的 GameInstance 类型导入
是显式允许的。GameState 必须直接来自无依赖的 ``src.engine.game_state``。

与 test_dependencies 一样按 AST 解析 import（含函数内延迟导入），而不是对
源码做字符串匹配；重命名/注释变化不触发失败，真实依赖方向变化必须触发失败。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

HELPER_MODULES = (
    "src/engine/game_state.py",
    "src/engine/round_snapshots.py",
    "src/engine/turn_state.py",
    "src/engine/round_recovery.py",
    "src/engine/instance_lifecycle.py",
    "src/engine/progression.py",
    "src/engine/module_state.py",
    "src/engine/modules/lorebook_runtime.py",
)

FORBIDDEN_RUNTIME_MODULE = "src.engine.game_instance"


def _type_checking_guarded(tree: ast.AST) -> set[int]:
    """Return ids of every node nested inside ``if TYPE_CHECKING:`` blocks."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_guard = (
                (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
                or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
            )
            if is_guard:
                guarded.update(id(child) for child in ast.walk(node))
    return guarded


def test_helpers_do_not_runtime_import_game_instance() -> None:
    violations: list[str] = []
    for rel in HELPER_MODULES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        guarded = _type_checking_guarded(tree)
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == FORBIDDEN_RUNTIME_MODULE or alias.name.startswith(
                        FORBIDDEN_RUNTIME_MODULE + "."
                    ):
                        violations.append(f"{rel}: runtime import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == FORBIDDEN_RUNTIME_MODULE or module.startswith(
                    FORBIDDEN_RUNTIME_MODULE + "."
                ):
                    names = ", ".join(alias.name for alias in node.names)
                    violations.append(f"{rel}: runtime from-import {module} ({names})")
    assert not violations, "\n".join(violations)


def test_helpers_import_game_state_contract_directly() -> None:
    """helper 中所有 ``GameState`` 导入必须来自 ``src.engine.game_state`` 契约模块。

    反向断言：任何 ``from <其它模块> import GameState``（包括 game_instance 的
    re-export）都失败，防止未来绕开契约模块。
    """
    for rel in HELPER_MODULES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        guarded = _type_checking_guarded(tree)
        for node in ast.walk(tree):
            if id(node) in guarded or not isinstance(node, ast.ImportFrom):
                continue
            imported = {alias.name for alias in node.names}
            if "GameState" not in imported:
                continue
            assert node.module == "src.engine.game_state", (
                f"{rel}: GameState 只能从 src.engine.game_state 导入"
                f"（实际来自 {node.module or '相对导入'}）"
            )


def test_helpers_stay_inside_engine_domain_boundaries() -> None:
    """helper 属于 engine 域：不得依赖 webui / 具体 ruleset runtime。"""
    for rel in HELPER_MODULES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for name in modules:
                module = name
                for banned in ("src.webui", "src.rulesets.dnd2024", "src.compat"):
                    assert not (
                        module == banned or module.startswith(banned + ".")
                    ), f"{rel}: helper 不得依赖 {banned}"
