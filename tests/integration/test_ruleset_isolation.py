"""Ruleset isolation Golden Path（方案 §13.7）。

同一运行时里并存 d20 与 d100 两局游戏：各自的检定使用自己规则的
骰制，回合与玩家状态互不串扰。
"""

from __future__ import annotations

import pytest
from src.engine.modules import narrative_notes
from src.engine.modules import checks


async def _play_one_round(env, game_key, gm_uid, player_uid, narration: str) -> None:
    api, registry, llm = env["api"], env["registry"], env["llm"]
    llm.responses.append(narration)
    inst = registry.get(api._parse_key(game_key))
    await inst.add_action(player_uid, "我仔细观察周围", selected_attribute="dex", selected_skill="侦查")
    await inst.add_action(gm_uid, "我保持警惕", selected_attribute="str")
    assert await inst.try_advance() is True
    await api._handler.process_round(inst)


@pytest.mark.asyncio
async def test_d20_and_d100_games_use_their_own_dice_systems_without_crossing(
    game_env, create_two_player_game, monkeypatch
):
    monkeypatch.setattr("random.randint", lambda _low, _high: 13)

    d20_key, d20_gm, d20_player = await create_two_player_game()

    api = game_env["api"]
    created = await api.create_game(
        "itest_world_coc",
        "d100 对局",
        solo=False,
        gm_uid="coc_gm",
        players=[
            {
                "character_name": "调查员甲",
                "race": "人类",
                "class": "调查员",
                "attributes": {"str": 50, "dex": 60, "con": 55},
                "gold": 20,
            },
            {
                "character_name": "调查员乙",
                "race": "人类",
                "class": "调查员",
                "attributes": {"str": 45, "dex": 65, "con": 50},
                "gold": 20,
            },
        ],
    )
    assert created["ok"] is True
    d100_key = created["game_key"]
    d100_gm = created["players"][0]["user_id"]
    d100_player = created["players"][1]["user_id"]

    await _play_one_round(game_env, d20_key, d20_gm, d20_player, "d20 局推进。\n---\nSCENE:大厅")
    await _play_one_round(game_env, d100_key, d100_gm, d100_player, "d100 局推进。\n---\nSCENE:门厅")

    registry = game_env["registry"]
    d20_inst = registry.get(api._parse_key(d20_key))
    d100_inst = registry.get(api._parse_key(d100_key))

    d20_checks = [dict(c) for c in checks.last_checks(d20_inst) if c.get("actor_uid") == d20_player]
    d100_checks = [dict(c) for c in checks.last_checks(d100_inst) if c.get("actor_uid") == d100_player]
    assert d20_checks and all(c["dice"] == "d20" for c in d20_checks)
    assert d100_checks and all(c["dice"] == "d100" for c in d100_checks)

    # 两局状态互不串扰
    assert d20_inst.round_number == 2 and d100_inst.round_number == 2
    assert narrative_notes.scene(d20_inst) == "大厅" and narrative_notes.scene(d100_inst) == "门厅"
    assert d100_player not in d20_inst.players
    assert d20_player not in d100_inst.players
    assert d20_inst.rule_id != d100_inst.rule_id
