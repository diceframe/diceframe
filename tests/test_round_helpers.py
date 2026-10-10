"""should_multi_step 的触发边界：只接受当前确实存在的复杂条件。

回归《最终功能落地体检报告》9.9 追加：累计的历史 NPC 数（instance.npcs 包含
全部历史 NPC）不代表当前局势复杂，不得再以 len(instance.npcs) >= 4 触发多步分析。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.modules import table_settings
from src.commands.round_helpers import should_multi_step
from src.commands.round_llm import append_multistep_analysis
from src.engine.game_instance import GameInstance
from src.engine.puzzle import PuzzleInstance, PuzzleManager, PuzzleState


def _make_instance(
    *,
    entry_point: str = "web",
    npcs: dict | None = None,
    scene: str = "",
    puzzles: list[str] | None = None,
) -> GameInstance:
    instance = GameInstance(game_key=("web", "test", "bot"))
    table_settings.replace_entry_point(instance, entry_point)
    instance.npcs = npcs or {}
    instance.scene = scene
    if puzzles:
        manager = PuzzleManager()
        for puzzle_id in puzzles:
            manager.add_puzzle(
                PuzzleInstance(puzzle_id=puzzle_id, name=puzzle_id, state=PuzzleState.ACTIVE)
            )
        instance.puzzle_manager = manager
    return instance


def _five_historical_npcs() -> dict:
    return {
        f"npc{i}": {"name": f"npc{i}", "character_name": f"历史NPC{i}"}
        for i in range(5)
    }


def test_simple_action_with_five_cumulative_npcs_does_not_trigger() -> None:
    """累计 5 个 NPC 但当前是普通查看行动，不得触发多步分析。"""
    instance = _make_instance(npcs=_five_historical_npcs())
    assert should_multi_step(instance, "我观察四周，看看有什么线索。") is False


def test_combat_intent_triggers() -> None:
    instance = _make_instance(npcs=_five_historical_npcs())
    assert should_multi_step(instance, "我拔出剑，冲向敌人攻击！") is True


def test_active_puzzle_triggers() -> None:
    instance = _make_instance(npcs=_five_historical_npcs(), puzzles=["p1"])
    assert should_multi_step(instance, "我检查一下门上的锁。") is True


def test_explicit_decision_triggers() -> None:
    instance = _make_instance()
    assert should_multi_step(instance, "我选择是继续前进还是原路返回。") is True


def test_non_web_entry_never_triggers() -> None:
    """非 Web 入口即使战斗或谜题也绝不触发。"""
    instance = _make_instance(entry_point="plugin", npcs=_five_historical_npcs())
    assert should_multi_step(instance, "我拔出剑，冲向敌人攻击！") is False


def test_multiple_npcs_named_in_current_scene_triggers() -> None:
    """当前场景文本中出现 ≥2 名活跃 NPC 名字时触发。"""
    instance = _make_instance(
        npcs={
            "a": {"name": "a", "character_name": "艾琳"},
            "b": {"name": "b", "character_name": "罗格"},
        },
        scene="艾琳和罗格站在门口对峙，气氛紧张。",
    )
    assert should_multi_step(instance, "我推门而入。") is True


def test_single_scene_npc_does_not_trigger() -> None:
    """当前场景只有 1 名 NPC 时不算“多名活跃 NPC”，不触发。"""
    instance = _make_instance(
        npcs={
            "a": {"name": "a", "character_name": "艾琳"},
            "b": {"name": "b", "character_name": "罗格"},
        },
        scene="艾琳独自站在门口。",
    )
    assert should_multi_step(instance, "我推门而入。") is False


class _FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, **kwargs) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(content='{"situation":"当前局势复杂"}')


@pytest.mark.asyncio
async def test_multistep_skipped_action_does_not_call_model() -> None:
    """简单行动 + 累计 5 NPC：不调用局势分析模型，上下文原样返回。"""
    llm = _FakeLLM()
    instance = _make_instance(npcs=_five_historical_npcs())
    result = await append_multistep_analysis(
        llm, instance, "GM", "ctx", "我观察四周，看看有什么线索。", 512
    )
    assert llm.calls == 0
    assert result == "ctx"


@pytest.mark.asyncio
async def test_multistep_triggered_action_logs_stage_elapsed(caplog) -> None:
    """战斗行动：局势分析被触发并记录阶段耗时，不写任何密钥或提示词。"""
    llm = _FakeLLM()
    instance = _make_instance(npcs=_five_historical_npcs())
    with caplog.at_level("INFO", logger="trpg"):
        result = await append_multistep_analysis(
            llm, instance, "GM", "ctx", "我拔出剑，冲向敌人攻击！", 512
        )
    assert llm.calls == 1
    assert "局势分析（内部参考）" in result
    # 日志不得包含 API Key / 完整私密提示词。
    for record in caplog.records:
        assert "API Key" not in record.message
        assert "ctx" not in record.message
