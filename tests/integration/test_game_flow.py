"""基础游戏流程 Golden Path：创建 → 行动 → 状态改变 → save → 重载 → 一致。

与 test_audit_checklist_completion 的区别：这里用**全新的 GameRegistry**
从磁盘重载（模拟进程重启），而不是复用同一对象清缓存。
"""

from __future__ import annotations

import pytest

from src.engine.modules import narrative_notes
from src.commands.game_handler import GameHandler
from src.engine.game_instance import GameRegistry
from src.lorebook.matcher import KeywordMatcher
from src.webui.api import WebAPI


@pytest.mark.asyncio
async def test_create_act_save_reload_keeps_state_consistent(game_env, create_two_player_game, monkeypatch):
    game_key, gm_uid, player_uid = await create_two_player_game()
    api = game_env["api"]
    registry = game_env["registry"]
    key = api._parse_key(game_key)

    monkeypatch.setattr("random.randint", lambda _low, _high: 1)
    game_env["llm"].responses.append(
        f"你们穿过大厅。\n---\nHP:{gm_uid}:-3\nSCENE:走廊\nQUEST:调查遗迹:active\nQUICK_ACTIONS:搜索|撤退"
    )

    inst = registry.get(key)
    await inst.add_action(gm_uid, "我稳住落石", selected_attribute="str")
    await inst.add_action(player_uid, "我搜索暗门", selected_attribute="dex")
    assert await inst.try_advance() is True
    narration, _ = await api._handler.process_round(inst)

    assert "走廊" in narrative_notes.scene(inst)
    assert inst.round_number == 2
    hp_after = inst.get_character_sheet(gm_uid)["hp"]

    # 存档后模拟进程重启：整条真实链路（registry + handler + api）从磁盘重建
    await registry.save(inst)
    fresh_registry = GameRegistry(game_env["saves_dir"])
    fresh_handler = GameHandler(
        registry=fresh_registry,
        llm_client=game_env["llm"],
        lorebook_matcher=KeywordMatcher(),
        lorebook_store=game_env["lorebook"],
        memory_store=None,
        prompts_dir=game_env["prompts_dir"],
        rules_dir=game_env["rules_dir"],
        worlds_dir=game_env["worlds_dir"],
    )
    fresh_api = WebAPI(
        registry=fresh_registry,
        lorebook=game_env["lorebook"],
        memory=None,
        rules_dir=game_env["rules_dir"],
        handler=fresh_handler,
        llm_client=game_env["llm"],
        worlds_dir=game_env["worlds_dir"],
    )
    reloaded = await fresh_registry.load(key)

    assert reloaded is not None
    assert reloaded.round_number == 2
    assert narrative_notes.scene(reloaded) == "走廊"
    assert reloaded.get_character_sheet(gm_uid)["hp"] == hp_after
    assert set(reloaded.players) == {gm_uid, player_uid}
    assert reloaded.quick_actions == ["搜索", "撤退"]
    assert narration  # 回合叙事真实产生
    # 重载后可以继续推进（状态机没有损坏）
    game_env["llm"].responses.append("剧情继续。\n---\nSCENE:深处")
    await reloaded.add_action(gm_uid, "继续前进", selected_attribute="con")
    await reloaded.add_action(player_uid, "跟上", selected_attribute="dex")
    assert await reloaded.try_advance() is True
    await fresh_api._handler.process_round(reloaded)
    assert narrative_notes.scene(reloaded) == "深处"
    assert reloaded.round_number == 3
