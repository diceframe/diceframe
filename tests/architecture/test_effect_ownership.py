"""S0-EFFECT1 P5：一个 kind 只应有一份"真实现"。

注册表把 30 个 kind 派发到实现上，但实现散在两处：`features/` 里是 Protocol
声明（空体 `...`），`combat/` 里是真代码。两者同名，读代码时很容易把声明当成
实现。这条守卫把"至多一份真实现"钉成机器可查的规则，并允许声明继续存在。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src/rulesets/dnd2024"


def _registered_kinds() -> list[str]:
    source = (ROOT / "features/kinds.py").read_text(encoding="utf-8")
    return re.findall(r'^\s{4}"([a-z0-9_]+)":', source, re.M)


def _definitions(name: str) -> list[tuple[str, bool]]:
    """(相对路径, 是否空体声明) 列表。"""

    found: list[tuple[str, bool]] = []
    for path in ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - 语法错误由 ruff 负责
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in {name, f"_{name}_events"}:
                body = [stmt for stmt in node.body if not isinstance(stmt, ast.Expr)
                        or not isinstance(stmt.value, ast.Constant)]
                stub = all(isinstance(s, ast.Expr) for s in body)
                found.append((str(path.relative_to(ROOT)), stub))
    return found


#: 存量：13 个 kind 在 `combat/` 有实现、`features/` 里也有非空体。收窄见 §6.12；
#: 这是一份**只减不增**的名单——新增一条就失败，删掉一条也要同步删这里。
KNOWN_DUPLICATES = {
    "resistance_all", "quivering_palm_end", "holy_nimbus", "dragon_wings",
    "intimidating_presence", "innate_sorcery", "bardic_grant", "divine_spark",
    "turn_undead", "abjure_foes", "preserve_life", "lay_on_hands", "divine_sense",
}


def test_no_new_duplicate_implementation_appears() -> None:
    duplicates: list[str] = []
    for kind in _registered_kinds():
        real = [path for path, stub in _definitions(kind) if not stub]
        if len(real) > 1 and kind not in KNOWN_DUPLICATES:
            duplicates.append(f"{kind}: {', '.join(real)}")
    assert not duplicates, "新增了重复实现（见 §6.12）：" + "; ".join(duplicates)


def test_the_duplicate_list_only_shrinks() -> None:
    """名单是"只减不增"：每搬走一个就要从名单里删掉，否则它会变成永久豁免。"""

    live = {
        kind for kind in _registered_kinds()
        if len([path for path, stub in _definitions(kind) if not stub]) > 1
    }
    stale = sorted(KNOWN_DUPLICATES - live)
    assert not stale, "这些已经不再重复，请从名单删除：" + ", ".join(stale)
