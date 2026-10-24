"""Architecture contracts for play modes and their bot surface declaration.

轨道 P0（ADR-0007 §2 / §6）守卫：

- 玩法模式的 transport 能力是模式模块里的一份数据声明（``MODE_BOT_SURFACES``），
  绑定准入（``bot_access.verify_bind_game``）是唯一执行点；bot bridge 与平台
  插件不得引用具体玩法、``play_mode`` 或模式注册表。
- 具体玩法字面量（``gal``）只允许出现在模式模块与创建校验里，generic path
  不得出现具体玩法分支。
- 每个已知模式必须声明合法 bot surface；未知模式 fail closed 为 ``none``。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.engine.modules import adventure_runtime_state

ROOT = Path(__file__).resolve().parents[2]
_GAL_TOKEN = re.compile(r"\bgal\b")


def _py_sources(*roots: Path):
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            yield path


def test_every_play_mode_declares_a_valid_bot_surface() -> None:
    assert set(adventure_runtime_state.MODE_BOT_SURFACES) == set(adventure_runtime_state.PLAY_MODES)
    assert set(adventure_runtime_state.MODE_BOT_SURFACES.values()) <= set(
        adventure_runtime_state.BOT_SURFACES
    )
    assert adventure_runtime_state.bot_surface("gal") == adventure_runtime_state.BOT_SURFACE_READ_ONLY
    assert adventure_runtime_state.bot_surface("") == adventure_runtime_state.BOT_SURFACE_NONE
    assert adventure_runtime_state.bot_surface("werewolf") == adventure_runtime_state.BOT_SURFACE_NONE


def test_transports_do_not_reference_play_modes() -> None:
    offenders: list[Path] = []
    for path in _py_sources(ROOT / "src" / "bots", ROOT / "plugins"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "play_mode" in text or "adventure_runtime_state" in text or _GAL_TOKEN.search(text):
            offenders.append(path.relative_to(ROOT))
    assert not offenders


def test_specific_mode_literals_stay_in_the_mode_module_and_creation() -> None:
    allowed = {
        (ROOT / "src" / "engine" / "modules" / "adventure_runtime_state.py").resolve(),
        (ROOT / "src" / "webui" / "services" / "game_lifecycle.py").resolve(),
    }
    offenders: list[Path] = []
    for path in _py_sources(ROOT / "src"):
        if path.resolve() in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _GAL_TOKEN.search(text):
            offenders.append(path.relative_to(ROOT))
    assert not offenders


def test_narrator_completion_settlement_keeps_one_writer_path() -> None:
    """轨道 P6：叙事者提议的节点完成只有一条结算 writer 链。

    ``settle_adventure_completion`` 只允许出现在：settle 本体（adventure_effects）、
    回合后编排（turns 的依赖声明）、wiring（api）与既有实现 / GM 命令面
    （adventure_runtime、adventure_commands）。任何新调用方都意味着绕过
    gates 权威或 GM 命令面，必须先扩这条守卫的白名单并说明理由。
    """
    allowed = {
        "src/commands/adventure_effects.py",
        "src/webui/services/turns.py",
        "src/webui/api.py",
        "src/webui/services/adventure_runtime.py",
        "src/webui/services/adventure_commands.py",
    }
    offenders: list[str] = []
    for path in _py_sources(ROOT / "src"):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "settle_adventure_completion" in text and rel not in allowed:
            offenders.append(rel)
    assert not offenders


def test_only_the_gal_skin_declares_adventure_progression() -> None:
    """``adventure_progression`` 能力声明只属于声明它的玩法皮；跑团皮零变化。"""
    import json

    rules_dir = ROOT / "templates" / "rules"
    offenders: list[str] = []
    for path in sorted(rules_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (
            isinstance(data, dict)
            and "adventure_progression" in data
            and path.name != "gal_narrative.json"
        ):
            offenders.append(path.name)
    assert not offenders
