"""Story presentation is reusable, public data, independent of game mechanics."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.story import (
    StoryFormatError, StoryStream, parse_story, public_story, speaker_catalog,
    story_prompt, story_text, trim_story,
)


def game() -> GameInstance:
    instance = GameInstance(game_key=("web", "story", "bot"), world_id="test")
    instance.npcs["secret_traitor_001"] = {
        "name": "艾琳", "portrait": {"kind": "builtin", "id": "freeform_fantasy:2"},
        "secret": "隐藏真相",
    }
    return instance


def wire(*rows: dict) -> str:
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def test_known_character_uses_opaque_identity_and_existing_portrait():
    instance = game()
    before = deepcopy(instance.to_dict())
    ref = next(iter(speaker_catalog(instance)))
    story = parse_story(instance, wire(
        {"kind": "narration", "text": "酒馆安静下来。"},
        {"kind": "dialogue", "speaker": ref, "name": "艾琳", "text": "你来了。"},
    ))
    assert [segment["kind"] for segment in story["segments"]] == ["narration", "dialogue"]
    assert story["segments"][1]["speaker_id"] == ref
    assert story["segments"][1]["portrait"] == instance.npcs["secret_traitor_001"]["portrait"]
    assert story_text(story) == "酒馆安静下来。\n\n艾琳：你来了。"
    assert "secret_traitor" not in json.dumps(story)
    assert instance.to_dict() == before


def test_new_speakers_have_stable_distinct_server_ids_and_can_be_reused():
    instance = game()
    raw = wire(
        {"kind": "dialogue", "speaker": "new:guard1", "name": "守卫", "text": "站住。"},
        {"kind": "dialogue", "speaker": "new:guard2", "name": "守卫", "text": "放他们过去。"},
        {"kind": "dialogue", "speaker": "new:guard1", "name": "守卫", "text": "好吧。"},
    )
    story = parse_story(instance, raw, round_number=1, revision=0)
    ids = [item["speaker_id"] for item in story["segments"]]
    assert ids[0] == ids[2] and ids[0] != ids[1]
    assert parse_story(instance, raw, round_number=1, revision=0) == story
    assert parse_story(instance, raw, round_number=1, revision=1)["segments"][0]["speaker_id"] != ids[0]
    instance.log.append({"round": 1, "gm_response": story_text(story), "story": story})
    assert ids[0] in speaker_catalog(instance)
    followup = parse_story(instance, wire({"kind": "dialogue", "speaker": ids[0], "name": "守卫", "text": "快走。"}))
    assert followup["segments"][0]["speaker_id"] == ids[0]


def test_hidden_identity_does_not_inherit_true_portrait_or_name():
    instance = game()
    ref = next(iter(speaker_catalog(instance)))
    story = parse_story(instance, wire({"kind": "dialogue", "speaker": ref, "name": "蒙面人", "text": "别回头。"}))
    public = public_story(instance, story)
    assert public["segments"][0]["speaker_name"] == "蒙面人"
    assert "portrait" not in public["segments"][0]
    assert "艾琳" not in json.dumps(public, ensure_ascii=False)
    assert public["segments"][0]["speaker_id"] != ref


def test_model_cannot_assign_one_new_handle_to_two_named_people():
    instance = game()
    raw = wire(
        {"kind": "dialogue", "speaker": "new:one", "name": "艾琳", "text": "你好。"},
        {"kind": "dialogue", "speaker": "new:one", "name": "老板", "text": "晚上好。"},
    )
    with pytest.raises(StoryFormatError):
        parse_story(instance, raw, strict=True)
    fallback = parse_story(instance, raw)
    assert fallback["segments"][1]["kind"] == "narration"


def test_invalid_model_identity_never_becomes_a_player_or_npc():
    instance = game()
    before = deepcopy(instance.to_dict())
    malformed = wire({"kind": "dialogue", "speaker": "pc:owner", "name": "玩家", "text": "我同意付款。", "hp": 999})
    with pytest.raises(StoryFormatError):
        parse_story(instance, malformed, strict=True)
    story = parse_story(instance, malformed)
    assert all(item["kind"] == "narration" for item in story["segments"])
    assert "pc:owner" not in story_text(story)
    assert instance.to_dict() == before


def test_public_projection_whitelists_nested_fields_and_rejects_future_versions():
    instance = game()
    story = parse_story(instance, wire({"kind": "dialogue", "speaker": "new:person", "name": "旅人", "text": "你好。"}))
    story["secret"] = "秘密"
    story["segments"][0]["source_ref"] = "secret_traitor_001"
    story["segments"][0]["portrait"] = {"kind": "upload", "asset_id": "avatar", "private_path": "C:/secret"}
    public = public_story(instance, story)
    assert "secret" not in json.dumps(public)
    assert "private_path" not in json.dumps(public)
    assert public_story(instance, {"version": 99, "segments": story["segments"]}) is None


def test_pretty_json_fallback_never_displays_raw_metadata_lines():
    instance = game()
    raw = json.dumps({"kind": "dialogue", "speaker": "new:traveler", "name": "旅人", "text": "你好。",
                      "private_note": "secret_traitor_001"}, ensure_ascii=False, indent=2)
    story = parse_story(instance, raw)
    assert story_text(story) == "旅人：你好。"
    assert "secret_traitor_001" not in json.dumps(story)
    broken = raw[:-15]
    assert "secret_traitor_001" not in json.dumps(parse_story(instance, broken))


def test_old_prose_falls_back_without_guessing_speakers_and_prompt_is_bounded():
    instance = game()
    story = parse_story(instance, '艾琳说：“你好。”\n随后老板走来。')
    assert len(story["segments"]) == 1
    assert story["segments"][0]["kind"] == "narration"
    prompt = story_prompt(instance)
    assert "new:" in prompt and "secret_traitor_001" not in prompt


def test_trim_drops_whole_trailing_beats_and_keeps_speaker_attribution():
    instance = game()
    story = parse_story(instance, wire(
        {"kind": "narration", "text": "雨落下。"},
        {"kind": "dialogue", "speaker": "new:1", "name": "艾琳", "text": "跟我来。"},
        {"kind": "dialogue", "speaker": "new:1", "name": "艾琳", "text": "很长的台词。" * 100},
        {"kind": "narration", "text": "门在身后关上。"},
    ))
    trimmed = trim_story(story, 80)
    # Kept beats are a verbatim prefix: nothing is rewritten mid-sentence.
    assert trimmed["segments"] == story["segments"][:2]
    assert story_text(trimmed) == "雨落下。\n\n艾琳：跟我来。"
    assert trim_story(story, 627) == story
    assert trim_story(story, 626)["segments"] == story["segments"][:3]


def test_trim_cuts_an_oversized_first_beat_at_a_sentence_end():
    instance = game()
    text = "他说：“走吧。”" + "雨还在下。" * 100
    story = parse_story(instance, wire({"kind": "dialogue", "speaker": "new:1", "name": "艾琳", "text": text}))

    cut = trim_story(story, 15)["segments"][0]
    assert cut["speaker_name"] == "艾琳" and cut["text"] == "他说：“走吧。”"

    aligned = trim_story(story, 26)["segments"][0]
    assert aligned["text"] == "他说：“走吧。”" + "雨还在下。" * 3

    english = parse_story(instance, wire({"kind": "narration", "text": "The rain keeps falling. The wind rises. " * 20}))
    assert trim_story(english, 30)["segments"][0]["text"] == "The rain keeps falling."

    unbroken = parse_story(instance, wire({"kind": "narration", "text": "雨还在下" * 100}))
    marked = trim_story(unbroken, 21)
    assert marked["segments"][0]["text"] == "雨还在下" * 5 + "…"
    assert len(story_text(marked)) <= 21


def test_bound_story_response_only_shortens_runaway_stories():
    from types import SimpleNamespace
    from src.commands.story_output import bound_story_response, story_limit

    instance = game()

    def reply(*rows):
        story = public_story(instance, parse_story(instance, wire(*rows)))
        text = story_text(story)
        return SimpleNamespace(story=story, narration=text, content=text + "\n---\nQUICK_ACTIONS:追问|离开")

    limit = story_limit("zh-CN")
    # Longer than the old 500-character trigger, with the check result in the last beat.
    regular = reply({"kind": "narration", "text": "雨落下。"},
                    {"kind": "dialogue", "speaker": "new:1", "name": "艾琳", "text": "很长的台词。" * 90},
                    {"kind": "narration", "text": "图书馆使用检定成功：赵启明认出纸上的笔迹与退稿一致。"})
    assert len(regular.narration) > 500
    data = {"quick_actions": ["追问", "离开"]}
    before = deepcopy(data)
    assert bound_story_response(regular, data, limit=limit) is False
    assert regular.narration.endswith("笔迹与退稿一致。") and data == before

    runaway = reply({"kind": "dialogue", "speaker": "new:1", "name": "艾琳", "text": "很长的台词。" * 400})
    assert bound_story_response(runaway, data, limit=limit) is True
    assert len(runaway.narration) <= limit and runaway.narration.endswith("。")
    assert runaway.story["segments"][0]["speaker_name"] == "艾琳"
    assert data["quick_actions"] == []
    assert runaway.content == runaway.narration + "\n---\nQUICK_ACTIONS:追问|离开"


def test_story_limit_scales_by_language_and_falls_back():
    from src.commands.story_output import story_limit
    assert story_limit("zh-CN") == 1500
    assert story_limit("EN_us") == 3600
    assert story_limit("ru") == 3600
    assert story_limit("") == story_limit("zh-CN")


def test_story_capability_detaches_without_changing_core_gameplay():
    from src.engine import module_hooks
    from src.engine.story import read_story_mode
    instance = game()
    from src.engine.modules import storytelling
    storytelling.replace_mode(instance, "dialogue")
    before = deepcopy(instance.to_dict())
    hooks = module_hooks.unregister_owner(storytelling.MODULE_NAME)
    try:
        assert read_story_mode(instance) == "prose"
        assert module_hooks.read(instance, module_hooks.GM_PROMPT_SECTIONS) == []
        module_hooks.record(instance, module_hooks.STORY_MODE, "prose")
        assert instance.to_dict() == before
    finally:
        for point, handler in hooks.items():
            module_hooks.register(point, storytelling.MODULE_NAME, handler)


@pytest.mark.asyncio
async def test_stream_buffers_split_json_and_never_forwards_private_model_fields():
    instance = game()
    texts, updates = [], []

    async def on_delta(text):
        texts.append(text)

    async def on_story(story):
        updates.append(deepcopy(story))

    stream = StoryStream(instance, on_delta, on_story=on_story)
    raw = wire(
        {"kind": "narration", "text": "门开了。"},
        {"kind": "dialogue", "speaker": "new:1", "name": "旅人", "text": "晚上好。", "private": "秘密"},
    )
    for offset in range(0, len(raw), 7):
        await stream.feed(raw[offset:offset + 7])
    await stream.flush()
    assert "".join(texts) == story_text(updates[-1])
    assert "秘密" not in json.dumps(updates, ensure_ascii=False)
    assert "new:" not in "".join(texts)
    assert updates[-1] == parse_story(instance, raw)
