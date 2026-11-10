"""权限与隐私隔离的真实链路 integration 测试。

契约：
- 玩家 A 的私有信息不进入玩家 B 的可见面（私有消息仅按 uid 投递）。
- 公开面（叙事、日志视图）不携带私有内容。
- GM 悄悄话服务只接受真实玩家，且仅投递给目标玩家。
- 隔离在存档/重载后保持不变。
"""

from __future__ import annotations

import json

import pytest
from src.engine.modules import private_channels

SECRET = "只有乙能看见的暗门机关"


async def _run_one_round(env, game_key, gm_uid, player_uid, scripted: str) -> str:
    api, registry = env["api"], env["registry"]
    env["llm"].responses.append(scripted)
    inst = registry.get(api._parse_key(game_key))
    await inst.add_action(gm_uid, "我观察四周", selected_attribute="str")
    await inst.add_action(player_uid, "我搜索暗门", selected_attribute="dex")
    assert await inst.try_advance() is True
    narration, _private = await api._handler.process_round(inst)
    return narration


@pytest.mark.asyncio
async def test_private_message_reaches_only_its_owner(game_env, create_two_player_game, monkeypatch):
    game_key, gm_uid, player_uid = await create_two_player_game()
    monkeypatch.setattr("random.randint", lambda _low, _high: 1)

    narration = await _run_one_round(
        game_env, game_key, gm_uid, player_uid,
        f"乙发现了暗门。\n---\nSCENE:走廊\nPRIVATE:{player_uid}:{SECRET}\nQUICK_ACTIONS:搜索|撤退",
    )

    inst = game_env["registry"].get(game_env["api"]._parse_key(game_key))
    # 私有消息只投递给属主
    assert private_channels.private_log(inst)[player_uid][-1]["text"] == SECRET
    assert not private_channels.private_log(inst).get(gm_uid)
    # 公开叙事不携带私有内容
    assert SECRET not in narration
    # 日志视图（玩家可见面）不泄露私有内容
    assert SECRET not in json.dumps(game_env["api"].get_log(game_key), ensure_ascii=False)


@pytest.mark.asyncio
async def test_private_isolation_survives_save_and_reload(game_env, create_two_player_game, monkeypatch):
    game_key, gm_uid, player_uid = await create_two_player_game()
    monkeypatch.setattr("random.randint", lambda _low, _high: 1)

    await _run_one_round(
        game_env, game_key, gm_uid, player_uid,
        f"乙发现了暗门。\n---\nSCENE:走廊\nPRIVATE:{player_uid}:{SECRET}",
    )

    api, registry = game_env["api"], game_env["registry"]
    inst = registry.get(api._parse_key(game_key))
    await registry.save(inst)
    registry._instances.clear()
    reloaded = await registry.load(api._parse_key(game_key))

    assert reloaded is not None
    assert private_channels.private_log(reloaded)[player_uid][-1]["text"] == SECRET
    assert not private_channels.private_log(reloaded).get(gm_uid)


@pytest.mark.asyncio
async def test_gm_whisper_is_player_scoped_and_rejects_outsiders(game_env, create_two_player_game):
    game_key, _gm_uid, player_uid = await create_two_player_game()
    api = game_env["api"]

    ok = await api.gm_private_message(game_key, player_uid, "GM 对乙的悄悄话")
    assert ok["ok"] is True

    rejected = await api.gm_private_message(
        game_key, "ghost_uid", "不存在的玩家",
    )
    assert rejected["ok"] is False

    inst = game_env["registry"].get(api._parse_key(game_key))
    assert private_channels.private_log(inst)[player_uid][-1]["text"] == "GM 对乙的悄悄话"
    assert all(
        "GM 对乙的悄悄话" not in json.dumps(msgs, ensure_ascii=False)
        for uid, msgs in private_channels.private_log(inst).items()
        if uid != player_uid
    )


@pytest.mark.asyncio
async def test_seat_joined_after_last_round_keeps_permissions_after_reload(game_env, create_two_player_game, monkeypatch):
    """#714：上一轮结算后才加入的玩家，存档重载后仍是席位，行动不按 outsider 拒绝。"""
    game_key, gm_uid, player_uid = await create_two_player_game()
    monkeypatch.setattr("random.randint", lambda _low, _high: 10)
    await _run_one_round(game_env, game_key, gm_uid, player_uid, "两人推开了大门。")

    api, registry = game_env["api"], game_env["registry"]
    joined = await api.create_player(game_key, {
        "character_name": "丙",
        "race": "人类",
        "class": "冒险者",
        "attributes": {"str": 11, "dex": 11, "con": 11},
        "gold": 30,
    })
    assert joined["ok"] is True
    late_uid = joined["user_id"]

    inst = registry.get(api._parse_key(game_key))
    await registry.save(inst)
    registry._instances.clear()
    reloaded = await registry.load(api._parse_key(game_key))

    assert reloaded is not None
    assert late_uid in reloaded.players
    result = await api.submit_action(game_key, late_uid, "丙跟上队伍", selected_attribute="dex")
    assert result["status"] == 200, result
    assert any(action.get("user_id") == late_uid for action in reloaded.action_queue)
