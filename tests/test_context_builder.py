"""上下文拼接器测试。"""

import logging

import pytest
from src.llm.context_builder import (
    _INVENTORY_STATE_LIMIT,
    _KEY_ITEMS_STATE_LIMIT,
    _compact_state_view,
    _context_total_len,
    _detect_max_chars, _estimate_tokens, _truncate, _format_history,
    _shrink_section, _shrink_to_window, build_context,
)


class TestDetectMaxChars:
    def test_deepseek(self):
        assert _detect_max_chars("deepseek") == 48640

    def test_qwen(self):
        assert _detect_max_chars("qwen") == 48640

    def test_gpt35(self):
        assert _detect_max_chars("gpt-3.5") == 16320

    def test_gpt4(self):
        assert _detect_max_chars("gpt-4") == 32640

    def test_claude(self):
        assert _detect_max_chars("claude") == 65536

    def test_unknown_model(self):
        assert _detect_max_chars("unknown-model") == 48000


class TestEstimateTokens:
    def test_empty(self):
        assert _estimate_tokens("") == 1

    def test_chinese(self):
        assert _estimate_tokens("你好世界") == 4

    def test_long_text(self):
        assert _estimate_tokens("a" * 1000) == 250


class TestTruncate:
    def test_no_truncation(self):
        assert _truncate("short", 100) == "short"

    def test_truncation(self):
        result = _truncate("very long text that exceeds limits", 15)
        assert len(result) <= 15
        assert result.endswith("...")


class TestCompactStateView:
    def test_compacts_inventory_and_key_items_but_keeps_equipment(self):
        state = {"players": {"u1": {"character_sheet": {
            "equipment": [{"name": "铁剑"}],
            "inventory": [{"name": f"物品{i}", "qty": 1} for i in range(30)],
            "key_items": [{"name": f"钥匙{i}"} for i in range(20)],
        }}}}

        _compact_state_view(state)

        sheet = state["players"]["u1"]["character_sheet"]
        assert len(sheet["inventory"]) == _INVENTORY_STATE_LIMIT
        assert sheet["inventory"][-1]["name"] == "物品29"
        assert "其余未列出" in sheet["inventory_note"]
        assert len(sheet["key_items"]) == _KEY_ITEMS_STATE_LIMIT
        assert sheet["key_items"][-1]["name"] == "钥匙19"
        assert "其余未列出" in sheet["key_items_note"]
        assert sheet["equipment"] == [{"name": "铁剑"}]

    def test_small_lists_are_counted_but_not_truncated(self):
        state = {"players": {"u1": {"character_sheet": {
            "inventory": [{"name": "火把", "qty": 1}],
            "key_items": [],
        }}}}

        _compact_state_view(state)

        sheet = state["players"]["u1"]["character_sheet"]
        assert sheet["inventory"] == [{"name": "火把", "qty": 1}]
        assert sheet["inventory_note"] == "共 1 件，列出最近 1 件"
        assert "key_items_note" not in sheet


class TestFormatHistory:
    def test_empty(self):
        assert _format_history([], 1000) == ""

    def test_single_entry(self):
        log = [{
            "round": 1,
            "actions": [{"text": "攻击哥布林"}],
            "gm_response": "你击中了哥布林！",
        }]
        result = _format_history(log, 1000)
        assert "攻击哥布林" in result
        assert "你击中了哥布林" in result
        assert "Round 1" in result

    def test_state_changes_are_kept_for_story_continuity(self):
        log = [{
            "round": 3,
            "actions": [{"text": "观察铜尺"}],
            "gm_response": "你发现铜尺上的刻痕。",
            "state_changes": ["战斗扩展：我测试 使用了 内力掌（老周妻 -12）"],
        }]
        result = _format_history(log, 1000)
        assert "状态变动" in result
        assert "老周妻 -12" in result

    def test_truncation_by_budget(self):
        log = [
            {
                "round": i,
                "actions": [{"text": f"行动内容{i}" * 20}],
                "gm_response": f"GM回答{i}" * 20,
            }
            for i in range(1, 6)
        ]
        result = _format_history(log, 500)
        # 应该只包含后面的几轮
        assert "Round 1" not in result or "Round 5" in result


class TestShrinkSection:
    def test_truncates_non_history(self):
        result = _shrink_section("x" * 100, 40, drop_oldest_rounds=False)
        assert len(result) <= 60
        assert result.endswith("...")

    def test_history_keeps_latest_rounds_with_structure(self):
        heading = "【对话历史】"
        rounds = [f"[Round {i}]\n玩家: 行动{i}\nGM: 回复{i}" for i in range(1, 10)]
        text = heading + "\n" + "\n\n".join(rounds)
        result = _shrink_section(text, len(text) // 2, drop_oldest_rounds=True)
        assert result.startswith(heading)
        assert "Round 9" in result   # 最新轮保留
        assert "Round 1" not in result  # 最旧轮被丢
        assert len(result) <= len(text) // 2 + 1
        # 每个保留的轮次块都完整（含结尾 GM 行），未被切半
        body = result.split("\n", 1)[1]
        for block in body.split("\n\n"):
            assert "GM:" in block


class TestShrinkToWindow:
    def test_shrinks_low_priority_first(self):
        hist = "【对话历史】\n" + "\n\n".join(
            f"[Round {i}]\n玩家: 行动{i}\nGM: 回复{i}" for i in range(1, 40)
        )
        parts = [
            "【游戏状态】\n" + "状态" * 200,
            "【世界观知识】\n" + "设定" * 200,
            "【已确认事项】\n" + "事项、" * 200,
            hist,
        ]
        sec_idx = {"state": 0, "lorebook": 1, "confirmed": 2, "history": 3}
        _shrink_to_window(parts, sec_idx, max_total=1600)
        assert _context_total_len(parts) <= 1600
        # 历史（最低优先级）被收缩，最新轮保留
        assert parts[3].startswith("【对话历史】")
        assert "Round 39" in parts[3]
        # 更高优先级的段未被触碰（历史一轮收缩就吸收完溢出）
        assert parts[0] == "【游戏状态】\n" + "状态" * 200
        assert parts[2] == "【已确认事项】\n" + "事项、" * 200


class DummyInstance:
    game_key = ("web", "dummy", "bot")
    summary = {}
    key_facts = []
    confirmed_items = []
    log = []

    def to_llm_view(self):
        return {
            "world_name": "测试世界",
            "round_number": 1,
            "scene": "测试场景",
            "players": {},
        }


@pytest.mark.asyncio
async def test_build_context_does_not_duplicate_system_prompt():
    context = await build_context(
        DummyInstance(),
        gm_prompt_filled="GM_SYSTEM_SENTINEL：你是测试 GM。",
        lorebook_entries=[{"type": "location", "name": "青石镇", "content": "镇外有一座旧祠。"}],
        player_message="我去旧祠看看。",
        provider_name="deepseek",
    )
    assert "GM_SYSTEM_SENTINEL" not in context
    assert "【游戏状态】" in context
    assert "【世界观知识】" in context
    assert "【玩家发言】" in context


@pytest.mark.asyncio
async def test_build_context_includes_authoritative_combat_events_after_player_block():
    instance = DummyInstance()
    instance.language = "zh-CN"
    context = await build_context(
        instance,
        gm_prompt_filled="你是测试 GM。",
        lorebook_entries=[],
        player_message="我继续观察现场。",
        provider_name="deepseek",
        authoritative_events_text=(
            "【已结算战斗事实·必须接续】\n"
            "[{\"intent_id\":\"i-1\",\"events\":[{\"type\":\"combat.damage_applied\",\"applied\":12}]}]"
        ),
    )
    assert "已结算战斗事实·必须接续" in context
    assert '"intent_id":"i-1"' in context
    assert context.index("已结算战斗事实·必须接续") > context.index("【玩家发言】")


@pytest.mark.asyncio
async def test_build_context_exposes_authoritative_economy_decisions():
    instance = DummyInstance()
    instance.language = "zh-CN"
    instance.economy = {
        "outcomes": [{
            "proposal_id": "pay_declined",
            "kind": "payment",
            "payer_uid": "hero",
            "recipient_uid": "merchant",
            "amount": 10,
            "reason": "进城费用",
            "status": "declined",
            "effects_status": "discarded",
            "visibility": "party",
            "round": 3,
        }],
        "proposals": [{
            "id": "pay_pending",
            "kind": "payment",
            "payer_uid": "hero",
            "recipient_uid": "innkeeper",
            "amount": 5,
            "reason": "住宿费用",
            "status": "pending",
            "visibility": "party",
            "round": 4,
        }, {
            "id": "purchase_pending",
            "kind": "purchase",
            "payer_uid": "hero",
            "recipient_uid": "hero",
            "approval_policy": "payer",
            "rewards": [{"name": "药水"}],
            "status": "pending",
            "visibility": "private",
            "round": 4,
        }],
    }

    context = await build_context(
        instance,
        gm_prompt_filled="你是测试 GM。",
        lorebook_entries=[],
        player_message="我接下来做什么？",
        provider_name="deepseek",
    )

    assert "pay_declined" in context
    assert '"status": "declined"' in context
    assert '"effects_status": "discarded"' in context
    assert "pay_pending" in context
    assert '"status": "pending"' in context
    assert "以下服务端记录覆盖此前叙事" in context
    assert "不得再次提出同一交易" in context
    assert "商品尚未拥有且不可使用" in context


@pytest.mark.asyncio
async def test_build_context_enforces_window_with_extreme_inputs(caplog, monkeypatch):
    """极端配置（海量已确认事项/世界书 + 超长玩家消息）下，上下文仍不超窗。"""
    monkeypatch.setenv("TRPG_MAX_CONTEXT_CHARS", "3000")
    instance = DummyInstance()
    instance.confirmed_items = [f"已确认事项{i}" * 20 for i in range(200)]
    instance.log = [
        {
            "round": i,
            "actions": [{"text": f"行动 {i}：前往村口寻找线索。"}],
            "gm_response": f"第{i}轮 GM 回复：你沿小路走去，夜色中传来低语。" * 3,
        }
        for i in range(1, 31)
    ]
    with caplog.at_level(logging.WARNING, logger="trpg"):
        context = await build_context(
            instance,
            gm_prompt_filled="你是测试 GM，负责推动剧情。" * 30,
            lorebook_entries=[
                {"type": "location", "name": f"地点{i}", "content": "旧祠深处埋着石碑。" * 20}
                for i in range(1, 60)
            ],
            player_message="我" * 1500,
            provider_name="deepseek",
        )
    assert len(context) <= 3000
    assert "【玩家发言】" in context
    assert "【已确认事项】" in context
    # 收尾收缩确已触发
    assert "触发收尾收缩" in caplog.text
