"""AI 判定 + 骰子 Golden Path（方案 §13.3）。

玩家行动 → 检定规划 → 服务端掷骰 → 结算 → 结构化检定入状态。
LLM 是脚本替身；骰子与结算管线真实执行。核心断言：骰值来自服务端
随机源（可被固定种子复现），而不是 LLM 文本。
"""

from __future__ import annotations

import pytest
from src.engine.modules import narrative_notes
from src.engine.modules import checks as checks_module


VALID_D20_VERDICTS = {"大成功", "成功", "失败", "大失败"}


@pytest.mark.asyncio
async def test_player_action_flows_through_server_side_check_pipeline(
    game_env, create_two_player_game, monkeypatch
):
    game_key, gm_uid, player_uid = await create_two_player_game()
    api, registry, llm = game_env["api"], game_env["registry"], game_env["llm"]

    monkeypatch.setattr("random.randint", lambda _low, _high: 13)
    llm.responses.append("你们仔细搜索了大厅。\n---\nSCENE:大厅\nQUICK_ACTIONS:继续搜索|离开")

    inst = registry.get(api._parse_key(game_key))
    await inst.add_action(player_uid, "我调查书架上的暗格", selected_attribute="dex", selected_skill="侦查")
    await inst.add_action(gm_uid, "我在门口警戒", selected_attribute="str")
    assert await inst.try_advance() is True
    await api._handler.process_round(inst)

    checks = [dict(c) for c in checks_module.last_checks(inst)]
    assert checks, "玩家行动应产生服务端检定"
    mine = [c for c in checks if c.get("actor_uid") == player_uid]
    assert mine, "检定应归属到行动玩家（actor 不串）"
    check = mine[0]
    assert check["dice"] == "d20"
    assert check["roll"] == 13  # 服务端随机源权威：固定种子可复现
    assert check["verdict"] in VALID_D20_VERDICTS
    # 回合推进与公开叙事正常产生
    assert inst.round_number == 2
    assert narrative_notes.scene(inst) == "大厅"
