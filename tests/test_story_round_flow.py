"""Character dialogue survives the real generation, log and branch boundaries."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.commands.round_llm import call_llm_with_tag_retry
from src.engine.game_instance import GameInstance
from src.engine.modules import storytelling
from src.engine.story import parse_story, story_text
from src.llm.client import LLMResponse
from src.webui.services import logs


def game():
    instance = GameInstance(game_key=("web", "story-flow", "bot"))
    storytelling.replace_mode(instance, "dialogue")
    return instance


def wire(text="晚上好。", speaker="new:traveler"):
    return json.dumps({"kind": "dialogue", "speaker": speaker, "name": "旅人", "text": text}, ensure_ascii=False) + "\n---\nNONE"


class Model:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def call(self, **kwargs):
        return self.response()

    def response(self):
        content = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return LLMResponse(content=content, narration=content.split("---")[0], total_tokens=10,
                           state_update=None, memory_delta=None, info_asymmetry=None,
                           plot_update=None, is_narration_only=False, provider_used="story-test")

    async def call_stream(self, on_delta=None, **kwargs):
        response = self.response()
        for offset in range(0, len(response.content), 9):
            await on_delta(response.content[offset:offset + 9])
        return response


class Sink:
    def __init__(self):
        self.text = ""
        self.stories = []
        self.resets = 0

    async def __call__(self, text):
        self.text += text

    async def story_update(self, story):
        self.stories.append(deepcopy(story))

    async def reset(self):
        self.text = ""
        self.stories = []
        self.resets += 1


@pytest.mark.asyncio
async def test_real_llm_flow_returns_plain_text_and_structured_story_without_extra_call():
    instance, sink = game(), Sink()
    model = Model([wire()])
    response, _ = await call_llm_with_tag_retry(model, instance, "GM", "context", "narrative", "", 1024, on_delta=sink, on_reset=sink.reset)
    assert response.narration == "旅人：晚上好。"
    assert response.story["segments"][0]["kind"] == "dialogue"
    assert sink.text == response.narration
    assert sink.stories[-1] == response.story
    assert model.calls == 1
    assert "new:" not in sink.text
    assert "NONE" not in sink.text


@pytest.mark.asyncio
async def test_bad_story_format_retries_then_degrades_to_public_narration():
    instance, sink = game(), Sink()
    model = Model(["普通正文。\n---\nNONE", wire()])
    response, _ = await call_llm_with_tag_retry(model, instance, "GM", "context", "narrative", "", 1024, on_delta=sink, on_reset=sink.reset)
    assert response.story["segments"][0]["kind"] == "dialogue"
    assert model.calls == 2 and sink.resets >= 1
    assert sink.text == response.narration
    fallback = Model(["普通正文。\n---\nNONE"])
    response, _ = await call_llm_with_tag_retry(fallback, instance, "GM", "context", "narrative", "", 1024)
    assert response.story["segments"][0]["kind"] == "narration"
    assert response.narration == "普通正文。"
    assert fallback.calls == 2


@pytest.mark.asyncio
async def test_regular_multi_beat_story_is_kept_whole_without_resetting_the_stream():
    # Replies longer than 500 characters used to be cut to 400 mid-sentence,
    # silently dropping the trailing beats that narrated the check result.
    instance, sink = game(), Sink()
    rows = [
        {"kind": "narration", "text": "投稿登记员把台灯拧亮了些，从抽屉里摸出一只黄铜放大镜。他没有急着说话，先把稿纸举到灯下，"
                                      "沿着页边一寸一寸地看过去，又用指腹在墨迹上轻轻压了压，看它会不会晕开。"},
        {"kind": "narration", "text": "窗外的雾又厚了一层，编辑部里只剩钟摆和暖气片的声音。他把你的原稿和另一份旧稿并排摊在柜台上，"
                                      "两份纸对着光比了半天，眉头一点点松开来。他又取过一份旧档案，逐行核对署名和年份，"
                                      "翻页的动作很慢，像是怕惊动纸上的什么东西。"},
        {"kind": "narration", "text": "他把信封翻过来，对着窗。里面那张白纸还在原处，纸上一个墨点也没有。街上的钟敲了四点整，"
                                      "雾散后窗玻璃上落着一层灰白的天光。柜台上那摞邮件被穿堂风吹得掀起一角。"},
        {"kind": "narration", "text": "学院街二十二号门厅里，伊迪丝的手一直插在围裙口袋里，那团灰布被她捏出棱角。"},
        {"kind": "narration", "text": "门厅里的座钟走着，钟摆声一下一下敲在耳朵里。你没有急着上楼，先看了一眼门边的衣帽架，"
                                      "上面那件灰呢外套的袖口磨得发亮。"},
        {"kind": "dialogue", "speaker": "new:edith", "name": "伊迪丝·霍尔西",
         "text": "门开的时候，一股干气先冲出来。他就那么仰在床上，一只手垂在床沿外头。"},
        {"kind": "narration", "text": "图书馆使用检定成功：赵启明一眼认出，纸上的署名年份与档案里那篇退稿的笔迹完全一致，"
                                      "这是同一个抄写员的手笔。他把这个发现指给登记员看，对方点了点头。"},
        {"kind": "dialogue", "speaker": "new:clerk", "name": "投稿登记员",
         "text": "你眼睛挺尖。这份东西不是他自己写的，是有人照着他的手笔誊的。"},
    ]
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n---\nQUICK_ACTIONS:追问登记员|收好原稿离开"
    expected = story_text(parse_story(instance, content.split("---")[0]))
    assert 500 < len(expected) < 1500 and expected.endswith("照着他的手笔誊的。")
    # The check result sits past the old 400-character cut and used to vanish with it.
    assert expected.index("图书馆使用检定成功") > 400
    response, data = await call_llm_with_tag_retry(
        Model([content]), instance, "GM", "context", "narrative", "", 1024, on_delta=sink, on_reset=sink.reset)
    assert response.narration == expected
    assert len(response.story["segments"]) == len(rows)
    assert data["quick_actions"] == ["追问登记员", "收好原稿离开"]
    assert sink.resets == 0 and sink.text == expected
    assert sink.stories[-1] == response.story


@pytest.mark.asyncio
async def test_runaway_story_is_bounded_whole_without_flattening_speakers():
    instance = game()
    model = Model([wire("很长的台词。" * 400)])
    response, data = await call_llm_with_tag_retry(model, instance, "GM", "context", "narrative", "", 1024)
    assert response.story["segments"][0]["kind"] == "dialogue"
    assert len(response.narration) <= 1500 and response.narration.endswith("。")
    assert response.narration == story_text(response.story)
    assert data["quick_actions"] == []


@pytest.mark.asyncio
async def test_round_save_reload_and_swipe_keep_matching_story():
    instance = game()
    first = parse_story(instance, wire().split("---")[0])
    second = parse_story(instance, wire("请进。").split("---")[0], revision=1)
    await instance.finish_judgment(story_text(first), story=first)
    round_number = instance.log[-1]["round"]
    await instance.finish_judgment_with_swipe(story_text(second), round_number, story=second)
    assert instance.log[-1]["story"] == second
    assert instance.log[-1]["swipe_stories"] == [first, second]
    restored = GameInstance.from_dict(instance.to_dict())
    assert restored.log[-1]["story"] == second
    assert await restored.switch_swipe(round_number, 0)
    assert restored.log[-1]["story"] == first
    assert restored.log[-1]["gm_response"] == story_text(first)
    await restored.finish_judgment_with_swipe("旧格式分支", round_number)
    assert "story" not in restored.log[-1]
    assert await restored.switch_swipe(round_number, 1)
    assert restored.log[-1]["story"] == second


def test_player_log_projects_safe_story_and_avatar_overrides():
    instance = game()
    story = parse_story(instance, wire().split("---")[0])
    ref = story["segments"][0]["speaker_id"]
    storytelling.set_portrait(instance, ref, {"kind": "builtin", "id": "freeform_fantasy:1"})
    story["segments"][0]["source_ref"] = "secret_traitor_001"
    instance.log.append({"round": 1, "gm_response": story_text(story), "story": story, "swipe_stories": [story]})
    before = deepcopy(instance.to_dict())
    dependencies = logs.LogDependencies(registry={instance.game_key: instance}, parse_game_key=lambda _: instance.game_key)
    result = logs.get_log(dependencies, "story")
    segment = result["log"][0]["story"]["segments"][0]
    assert segment["portrait"]["id"] == "freeform_fantasy:1"
    assert "source_ref" not in segment
    assert "swipe_stories" not in result["log"][0]
    assert "secret_traitor" not in json.dumps(result)
    assert instance.to_dict() == before
