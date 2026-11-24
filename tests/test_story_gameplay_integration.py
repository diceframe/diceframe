"""Exercise real opening, turn, rewrite and public API boundaries with a fake model."""
from __future__ import annotations

import json

import pytest

from src.engine.game_instance import GameState
from src.llm.client import LLMResponse

pytest_plugins = ["tests.webapi_harness"]


def response(text, speaker="new:innkeeper"):
    content = json.dumps({"kind": "narration", "text": "雨打着窗。"}, ensure_ascii=False) + "\n"
    content += json.dumps({"kind": "dialogue", "speaker": speaker, "name": "老板", "text": text}, ensure_ascii=False)
    content += "\n---\nNONE"
    return LLMResponse(content=content, narration=content.split("---")[0],
                       state_update=None, memory_delta=None, info_asymmetry=None,
                       plot_update=None, total_tokens=10, is_narration_only=False, provider_used="fake")


@pytest.mark.asyncio
async def test_opening_turn_swipe_save_reload_and_player_projection(web_api, monkeypatch):
    api, _, registry, model, _ = web_api

    async def opening(**kwargs):
        assert "Character dialogue presentation" in kwargs["system_prompt"]
        return response("欢迎光临。")

    monkeypatch.setattr(model, "call", opening)
    created = await api.create_game(
        "template_world", "story-integration", story_mode="dialogue",
        players=[{"character_name": "玩家", "class": "游侠", "attributes": {"str": 12}}],
    )
    assert created["ok"] is True
    key = created["game_key"]
    instance = api.get_game_instance(key)
    assert instance.log[0]["story"]["segments"][1]["text"] == "欢迎光临。"
    assert instance.log[0]["gm_response"] == "雨打着窗。\n\n老板：欢迎光临。"
    speaker = instance.log[0]["story"]["segments"][1]["speaker_id"]

    async def next_turn(**kwargs):
        assert speaker in kwargs["system_prompt"]
        return response("有什么需要？", speaker)

    monkeypatch.setattr(model, "call", next_turn)
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.action_queue = [{"user_id": next(iter(instance.players)), "text": "打听消息"}]
    narration, _ = await api._handler.process_round(instance)
    assert "有什么需要？" in narration
    assert "new:" not in narration and '"kind"' not in narration
    assert instance.log[-1]["story"]["segments"][1]["speaker_id"] == speaker
    target_round = instance.log[-1]["round"]

    async def rewritten(**kwargs):
        return response("先喝杯热茶吧。", speaker)

    monkeypatch.setattr(model, "call", rewritten)
    rewritten_text = await api._handler.generate_swipe(instance, target_round)
    assert "先喝杯热茶吧。" in rewritten_text
    current = registry.get(instance.game_key)
    assert current.log[-1]["story"]["segments"][1]["text"] == "先喝杯热茶吧。"
    assert len(current.log[-1]["swipe_stories"]) == 2
    assert await current.switch_swipe(target_round, 0)
    assert current.log[-1]["story"]["segments"][1]["text"] == "有什么需要？"
    await registry.save(current)
    registry._instances.clear()
    restored = await registry.load(current.game_key)
    assert restored.log[-1]["story"]["segments"][1]["text"] == "有什么需要？"
    player_log = api.get_log(key, include_internal=False)
    assert "story" in player_log["log"][-1]
    assert "swipe_stories" not in player_log["log"][-1]


@pytest.mark.asyncio
async def test_opening_turn_and_swipe_keep_a_long_story_whole(web_api, monkeypatch):
    # The opening, an ordinary turn and a rewrite share one ceiling. A reply that
    # only exceeds the old 500/400-character cut must survive intact in all three.
    api, _, registry, model, _ = web_api
    line = "雨打着窗，他把信封翻过来，对着灯又看了一遍。" * 24
    assert len(line) > 500
    speaker = ["new:innkeeper"]

    async def long_reply(**kwargs):
        return response(line, speaker[0])

    monkeypatch.setattr(model, "call", long_reply)
    created = await api.create_game(
        "template_world", "story-long", story_mode="dialogue",
        players=[{"character_name": "玩家", "class": "游侠", "attributes": {"str": 12}}],
    )
    assert created["ok"] is True
    key = created["game_key"]
    instance = api.get_game_instance(key)
    assert instance.log[0]["story"]["segments"][1]["text"] == line
    speaker[0] = instance.log[0]["story"]["segments"][1]["speaker_id"]

    instance.state = GameState.ACTIVE_JUDGMENT
    instance.action_queue = [{"user_id": next(iter(instance.players)), "text": "打听消息"}]
    await api._handler.process_round(instance)
    assert instance.log[-1]["story"]["segments"][1]["text"] == line
    target_round = instance.log[-1]["round"]

    assert line in await api._handler.generate_swipe(instance, target_round)
    assert registry.get(instance.game_key).log[-1]["story"]["segments"][1]["text"] == line
