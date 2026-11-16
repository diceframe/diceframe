"""命刻计数器的存档往返与键冲突防御。

`to_dict` 一直在写 `clock_counter`，但 `from_dict` 曾只恢复 `quest_counter`：
存档重载后计数器归零，新建命刻会生成 `clock_1` 并静默覆盖已恢复的同名命刻
（FU 限制同时 3 个未完成命刻，碰撞概率高）。这里锁定往返等价与让位行为。
"""
from __future__ import annotations

from src.engine.plot_clocks import create_clock
from src.engine.plot_tracker import PlotTracker


def _tracker_with_two_clocks() -> PlotTracker:
    tracker = PlotTracker()
    first = create_clock(tracker, "熔炉暴动", 6, 1)
    second = create_clock(tracker, "追兵逼近", 8, 2)
    assert sorted((first["clock_id"], second["clock_id"])) == ["clock_1", "clock_2"]
    return tracker


def test_clock_counter_survives_a_save_load_round_trip() -> None:
    tracker = _tracker_with_two_clocks()
    reloaded = PlotTracker.from_dict(tracker.to_dict())
    assert reloaded._clock_counter == tracker._clock_counter == 2
    assert set(reloaded.clocks) == {"clock_1", "clock_2"}
    # 重载后新建命刻必须拿到新键，而不是覆盖 clock_1。
    third = create_clock(reloaded, "暗影仪式", 4, 3)
    assert third["clock_id"] == "clock_3"
    assert set(reloaded.clocks) == {"clock_1", "clock_2", "clock_3"}


def test_legacy_save_without_counter_still_reloads_above_existing_keys() -> None:
    tracker = _tracker_with_two_clocks()
    payload = tracker.to_dict()
    # 旧存档没有 clock_counter 字段：不能归零后与存量键碰撞。
    payload.pop("clock_counter")
    reloaded = PlotTracker.from_dict(payload)
    assert reloaded._clock_counter >= 2
    fresh = create_clock(reloaded, "旧存档后的新命刻", 4, 4)
    assert fresh["clock_id"] not in {"clock_1", "clock_2"}
    assert reloaded.clocks["clock_1"].title == "熔炉暴动"


def test_llm_tag_upsert_after_reload_allocates_a_free_clock_key() -> None:
    tracker = _tracker_with_two_clocks()
    reloaded = PlotTracker.from_dict(tracker.to_dict())
    # 模拟 LLM 标签 upsert 路径（apply_update 的 clocks 段）对未知命刻的补建。
    applied = reloaded.apply_update(
        {"clocks": [{"title": "夜袭准备", "segments": 6, "filled": 1}]}, round_number=5,
    )
    assert "新命刻: 夜袭准备" in "\n".join(applied)
    assert reloaded.clocks["clock_1"].title == "熔炉暴动"
    assert reloaded.clocks["clock_2"].title == "追兵逼近"
    assert any(c.title == "夜袭准备" for c in reloaded.clocks.values())
