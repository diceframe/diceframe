"""Architecture contracts for gameplay packages (ADR-0010 §2/§6, ADR-0012).

- 每种玩法是 ``src/gameplays/<mode>`` 下的独立包，通过 ``GameplayMode`` 契约
  声明自己；只有 ``src/gameplays/builtin.py`` 可以 import 具体玩法包，generic
  path 只经注册表（``contracts`` / ``builtin``）接触玩法。
- 具体玩法字面量（``gal``）只允许出现在自己的包与组合根里。
- 玩法包之间互不 import；gal 包不得依赖跑团专属机制（回合管线、检定、战斗、
  经济、具体 ruleset），共用能力须先抽到共享底座。
- bot bridge 与平台插件不得引用具体玩法、``play_mode`` 或玩法注册表；
  bot surface 只在绑定准入点执行，未知模式 fail closed 为 ``none``。
"""

from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path

import pytest

from src.gameplays import contracts
from src.gameplays.builtin import DEFAULT_GAMEPLAYS, build_default_gameplay_registry

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
GAMEPLAYS = SRC / "gameplays"
_SHARED_GAMEPLAY_MODULES = {"src.gameplays", "src.gameplays.contracts", "src.gameplays.builtin"}
_GAL_TOKEN = re.compile(r"\bgal\b")

# Tabletop-game mechanics a non-tabletop gameplay package must not reach into.
_TABLETOP_ONLY_PREFIXES = (
    "src.gameplays.trpg",
    "src.commands",
    "src.rulesets",
    "src.rules",
    "src.engine.economy",
    "src.engine.combat_narrative",
    "src.engine.modules.ai_seats",
    "src.engine.modules.check_modes",
    "src.engine.modules.check_policy",
    "src.engine.modules.check_presentation",
    "src.engine.modules.check_reveals",
    "src.engine.modules.checks",
    "src.engine.modules.combat_extension_state",
    "src.engine.modules.economy_state",
    "src.engine.modules.health",
    "src.engine.modules.legacy_combat",
    "src.engine.modules.resource_recovery",
    "src.engine.modules.skill_growth",
)


def _py_sources(*roots: Path):
    for root in roots:
        yield from sorted(root.rglob("*.py"))


def _dotted_module(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_modules(path: Path) -> set[str]:
    with warnings.catch_warnings():
        # Existing sources contain unrelated invalid escapes in docstrings.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    package = _dotted_module(path.parent).split(".")
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                name = ".".join([*base, node.module] if node.module else base)
            else:
                name = node.module or ""
            if not name:
                continue
            modules.add(name)
            modules.update(f"{name}.{alias.name}" for alias in node.names)
    return modules


def _gameplay_package(module: str) -> str | None:
    """``src.gameplays.gal.x`` -> ``src.gameplays.gal``; shared modules -> None."""
    parts = module.split(".")
    if parts[:2] != ["src", "gameplays"] or len(parts) < 3:
        return None
    package = ".".join(parts[:3])
    return None if package in _SHARED_GAMEPLAY_MODULES else package


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def test_every_mode_declares_a_valid_bot_surface() -> None:
    assert DEFAULT_GAMEPLAYS.mode_ids() == {"free", "adventure", "gal"}
    for mode_id in DEFAULT_GAMEPLAYS.mode_ids():
        assert DEFAULT_GAMEPLAYS.bot_surface(mode_id) in contracts.BOT_SURFACES
    assert DEFAULT_GAMEPLAYS.bot_surface("gal") == contracts.BOT_SURFACE_READ_ONLY
    assert DEFAULT_GAMEPLAYS.bot_surface("") == contracts.BOT_SURFACE_NONE
    assert DEFAULT_GAMEPLAYS.bot_surface("werewolf") == contracts.BOT_SURFACE_NONE


@pytest.mark.parametrize(
    "mode_id,surface",
    [("", "full"), ("Gal", "full"), ("has-dash", "full"), ("ok", "everything")],
)
def test_registry_rejects_invalid_declarations(mode_id: str, surface: str) -> None:
    with pytest.raises(ValueError):
        contracts.GameplayRegistry([contracts.GameplayMode(mode_id, surface, lambda _request: None)])


def test_registry_rejects_duplicate_modes() -> None:
    registry = build_default_gameplay_registry()
    with pytest.raises(ValueError):
        registry.register(contracts.GameplayMode("gal", "none", lambda _request: None))


def test_contracts_stay_free_of_concrete_and_platform_imports() -> None:
    for module in _imported_modules(GAMEPLAYS / "contracts.py"):
        assert not module.startswith("src."), module


def test_only_the_composition_root_imports_concrete_gameplay_packages() -> None:
    builtin = (GAMEPLAYS / "builtin.py").resolve()
    offenders: list[str] = []
    for path in _py_sources(SRC):
        if path.resolve() == builtin:
            continue
        own = _gameplay_package(_dotted_module(path))
        for module in _imported_modules(path):
            target = _gameplay_package(module)
            if target is not None and target != own:
                offenders.append(f"{path.relative_to(ROOT)} -> {module}")
    assert not offenders


def test_gal_package_does_not_reach_into_tabletop_mechanics() -> None:
    offenders: list[str] = []
    for path in _py_sources(GAMEPLAYS / "gal"):
        for module in _imported_modules(path):
            if any(_matches(module, prefix) for prefix in _TABLETOP_ONLY_PREFIXES):
                offenders.append(f"{path.relative_to(ROOT)} -> {module}")
    assert not offenders


def test_specific_mode_literals_stay_in_their_package() -> None:
    allowed_roots = (GAMEPLAYS / "gal").resolve()
    builtin = (GAMEPLAYS / "builtin.py").resolve()
    offenders: list[Path] = []
    for path in _py_sources(SRC):
        resolved = path.resolve()
        if resolved == builtin or resolved.is_relative_to(allowed_roots):
            continue
        if _GAL_TOKEN.search(path.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(path.relative_to(ROOT))
    assert not offenders


def test_transports_do_not_reference_play_modes() -> None:
    offenders: list[Path] = []
    for path in _py_sources(SRC / "bots", ROOT / "plugins"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if (
            "play_mode" in text
            or "adventure_runtime_state" in text
            or "gameplays" in text
            or _GAL_TOKEN.search(text)
        ):
            offenders.append(path.relative_to(ROOT))
    assert not offenders
