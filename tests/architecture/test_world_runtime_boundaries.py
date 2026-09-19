"""World Runtime 边界守卫（WR-00 architecture guards，施工单 §93）。

World Runtime（当前 = ``world_state`` / ``world_events`` / ``world_legality``，
WR-01 起新增 ``src/engine/world/`` 包）是当前世界唯一 authority 的实现载体。
本文件用 AST 检查保护两条母方案（00_MASTER_PLAN §3 / §20）定死的边界：

1. World Runtime 自身保持在 engine 核心域内：不依赖 webui、具体 ruleset
   runtime、compat 适配层，也不感知 transport。
2. 没有被规划为写入口的域不得直接 import World Runtime 内部模块——
   ``adventures`` / ``rulesets`` / ``plugin_host`` / ``bots`` 必须通过
   engine / commands 层的显式 ops 路径触达世界， Adventure 物化（WR-09）
   也不例外。

检查方式与 test_dependencies 相同：解析 import AST（含函数内延迟导入），
而不是字符串 grep。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

# World Runtime 的实现文件（含 WR-01 起的 world/ 包）。
WORLD_MODULES = (
    "src.engine.world",
    "src.engine.world_state",
    "src.engine.world_events",
    "src.engine.world_legality",
)

WORLD_FILES = (
    "src/engine/world_state.py",
    "src/engine/world_events.py",
    "src/engine/world_legality.py",
)

# 母方案 authority map：这些域不允许直接触碰 World Runtime 实现。
# （webui / commands / llm / lorebook / migrations / engine 内部是当前既有的
# 合法调用方；本守卫冻结的是"新增越界"，不追溯现有路径。）
FORBIDDEN_WORLD_IMPORTERS = (
    "src.rulesets",
    "src.plugin_host",
    "src.bots",
)

# adventures 域的特殊语义（母方案 §25 gate / §9 source_ref）：允许 world_state
# 的**读取端**（fact_value / world_entities / ...），但写入口符号仍然禁止——
# Adventure 是定义层，物化必须走 engine 侧 materialization（WR-09）。
ADVENTURES_WORLD_READ_OK = "src.engine.world_state"
ADVENTURES_WORLD_WRITE_SYMBOLS = frozenset({"apply_world_ops", "apply_ops_to_state"})

# content_modules 允许复用 world.contracts 的 source 语法真值（纯校验词表，
# 母方案 §9），但同样禁止触碰世界写侧（MOD-05）。
CONTENT_MODULES_WRITE_SIDE_MODULES = (
    "src.engine.world_state",
    "src.engine.world_events",
    "src.engine.world_legality",
    "src.engine.world.ops",
    "src.engine.world.materialization",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    package = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                parts = [*base, node.module] if node.module else base
                name = ".".join(parts)
            else:
                name = node.module or ""
            if not name:
                continue
            modules.add(name)
            for alias in node.names:
                modules.add(f"{name}.{alias.name}")
    return modules


def _depends_on(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def test_world_runtime_stays_inside_the_engine_core_domain() -> None:
    world_files = sorted(SRC.rglob("world/*.py")) + [
        path for name in WORLD_FILES for path in [ROOT / name] if path.exists()
    ]
    assert world_files, "world runtime 文件应存在"
    violations: list[str] = []
    for path in world_files:
        for module in sorted(_imported_modules(path)):
            for banned in ("src.webui", "src.rulesets", "src.compat", "src.web_transport"):
                if _depends_on(module, banned):
                    violations.append(
                        f"{path.relative_to(ROOT)} imports {module} (forbidden: {banned})"
                    )
    assert not violations, "\n".join(violations)


def test_unplanned_domains_do_not_import_world_runtime_directly() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        dotted = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        importer = dotted.rsplit(".", 1)[0] if "." in dotted.rsplit(".", 1)[0] else dotted
        top_package = ".".join(dotted.split(".")[:2])
        if top_package not in FORBIDDEN_WORLD_IMPORTERS:
            continue
        for module in sorted(_imported_modules(path)):
            for world_module in WORLD_MODULES:
                if not _depends_on(module, world_module):
                    continue
                # world.contracts 是纯数据语法词表（source_ref / canonical id），
                # 与 game_state 契约同级：任何域都可复用，不构成写路径。
                if module == "src.engine.world.contracts" or module.startswith(
                    "src.engine.world.contracts."
                ):
                    continue
                if top_package == "src.adventures" and module == ADVENTURES_WORLD_READ_OK:
                    continue  # 读取端可用（由下方写符号检查兜底）
                violations.append(
                    f"{path.relative_to(ROOT)} imports {module} "
                    f"(forbidden: {top_package} must reach the world via engine ops)"
                )
        # adventures：world_state 读取端可用，但写入口符号禁止（WR-09 分工）。
        if top_package == "src.adventures":
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                if isinstance(node, ast.ImportFrom) and node.module == ADVENTURES_WORLD_READ_OK:
                    for alias in node.names:
                        if alias.name in ADVENTURES_WORLD_WRITE_SYMBOLS:
                            violations.append(
                                f"{path.relative_to(ROOT)} imports world write symbol "
                                f"{alias.name} (adventures must materialize via engine)"
                            )
    assert not violations, "\n".join(violations)


def test_content_modules_does_not_touch_world_write_side() -> None:
    """content_modules 只准用 contracts 语法真值，禁止触碰世界写侧（MOD-05）。"""

    violations: list[str] = []
    for path in sorted((ROOT / "src" / "content_modules").rglob("*.py")):
        for module in sorted(_imported_modules(path)):
            for world_module in CONTENT_MODULES_WRITE_SIDE_MODULES:
                if _depends_on(module, world_module):
                    violations.append(
                        f"{path.relative_to(ROOT)} imports {module} "
                        "(forbidden: content_modules must not touch the world write-side)"
                    )
    assert not violations, "\n".join(violations)
