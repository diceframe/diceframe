"""Shared storyboard normalization and deterministic composition tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.imagegen.storyboards import (
    StoryboardInferenceError,
    build_storyboard_prompt,
    infer_scene_panels,
    normalize_scene_panels,
    public_character_appearances,
    storyboard_layout,
)


def test_normalization_deduplicates_and_counts_only_overflow() -> None:
    panels, compressed = normalize_scene_panels([
        {"participants": "alice", "location": "车站", "description": "雨夜月台"},
        {"participants": "alice", "location": "车站", "description": "雨夜月台"},
        {"participants": "bob", "location": "地下室", "description": "血祭坛"},
        {"participants": "cara", "location": "钟楼", "description": "齿轮"},
        {"participants": "dan", "location": "码头", "description": "浓雾"},
        {"participants": "eve", "location": "森林", "description": "小径"},
        {},
        "invalid",
    ])

    assert [panel["location"] for panel in panels] == ["车站", "地下室", "钟楼", "码头", "森林"]
    assert compressed == 0


def test_layouts_and_prompt_preserve_panel_order() -> None:
    panels = [
        {"participants": ["alice"], "location": "A", "description": "first"},
        {"participants": ["bob"], "location": "B", "description": "second"},
        {"participants": [], "location": "C", "description": "third"},
    ]
    prompt, metadata = build_storyboard_prompt(panels)
    assert storyboard_layout(1) == "single"
    assert storyboard_layout(2) == "two-panel"
    assert storyboard_layout(3) == "three-panel"
    assert storyboard_layout(4) == "four-panel"
    assert storyboard_layout(5) == "five-panel"
    assert storyboard_layout(6) == "six-panel"
    assert storyboard_layout(7) == "six-panel"
    assert metadata["layout"] == "three-panel"
    assert prompt.index("Panel 1") < prompt.index("Panel 2") < prompt.index("Panel 3")
    assert "A" in prompt and "B" in prompt and "C" in prompt


def test_same_location_panels_merge_participants_and_descriptions() -> None:
    panels, compressed = normalize_scene_panels([
        {"participants": ["alice"], "location": "废弃车站", "description": "站在月台"},
        {"participants": ["bob"], "location": "废弃车站。", "description": "检查时刻表"},
    ])

    assert compressed == 0
    assert len(panels) == 1
    assert panels[0]["participants"] == ["alice", "bob"]
    assert "站在月台" in panels[0]["description"]
    assert "检查时刻表" in panels[0]["description"]


def test_single_panel_prompt_forbids_model_generated_splits() -> None:
    prompt, metadata = build_storyboard_prompt([
        {"participants": ["alice", "bob"], "location": "车站", "description": "两人在月台会合"},
    ])

    assert metadata["layout"] == "single"
    assert "single frame" in prompt
    assert "Do not create panels" in prompt
    assert "comic storyboard composition" not in prompt
    assert "gutters between panels" not in prompt


def test_same_location_authoritative_panels_remain_separate_in_prompt() -> None:
    prompt, metadata = build_storyboard_prompt([
        {"participants": ["alice"], "location": "后院", "description": "打开门链"},
        {"participants": ["alice"], "location": "后院", "description": "发现暗号"},
    ])

    assert metadata["layout"] == "two-panel"
    assert len(metadata["panels"]) == 2
    assert "Panel 1" in prompt and "Panel 2" in prompt
    assert "software will not add or enforce any divider color or line" in prompt


class _PanelLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def call(self, system_prompt, user_message, **kwargs):
        self.calls.append((system_prompt, user_message, kwargs))
        return SimpleNamespace(content=self.content)


class _SequencePanelLLM:
    def __init__(self, *contents: str):
        self.contents = list(contents)
        self.calls = []

    async def call(self, system_prompt, user_message, **kwargs):
        self.calls.append((system_prompt, user_message, kwargs))
        content = self.contents[min(len(self.calls) - 1, len(self.contents) - 1)]
        return SimpleNamespace(content=content)


@pytest.mark.asyncio
async def test_ai_infers_distinct_public_locations_and_exact_player_ids() -> None:
    llm = _PanelLLM(
        '{"panels":['
        '{"participants":["Alice"],"location":"废弃车站","description":"Alice站在雨夜月台","evidence_ids":["n1"]},'
        '{"participants":["bob"],"location":"钟楼地下室","description":"Bob检查石室祭坛","evidence_ids":["n2"]}'
        '],"compressed_count":0}'
    )
    panels, compressed = await infer_scene_panels(
        llm,
        narration="Alice留在废弃车站；与此同时，Bob进入钟楼地下室。",
        actions=[
            {"user_id": "alice", "text": "留在月台观察"},
            {"user_id": "bob", "text": "进入地下室"},
        ],
        current_scene="废弃车站",
        players={
            "alice": {"character_name": "Alice", "private_log": "不可发送"},
            "bob": {"character_name": "Bob"},
        },
        global_prompt="公开画面",
    )

    assert [panel["location"] for panel in panels] == ["废弃车站", "钟楼地下室"]
    assert panels[0]["participants"] == ["alice"]
    assert panels[1]["participants"] == ["bob"]
    assert compressed == 0


@pytest.mark.asyncio
async def test_requested_panel_count_is_part_of_analysis_not_post_slice() -> None:
    llm = _PanelLLM(
        '{"panels":['
        '{"participants":["alice"],"location":"废弃车站","description":"Alice观察月台","evidence_ids":["n1"]},'
        '{"participants":["bob"],"location":"钟楼地下室","description":"Bob检查祭坛","evidence_ids":["n2"]},'
        '{"participants":["alice"],"location":"废弃车站","description":"Alice发现脚印","evidence_ids":["n1"]}'
        '],"compressed_count":0}'
    )
    panels, _ = await infer_scene_panels(
        llm,
        narration="Alice留在废弃车站；与此同时，Bob进入钟楼地下室。Alice发现脚印。",
        actions=[],
        current_scene="废弃车站",
        players={"alice": {"character_name": "Alice"}, "bob": {"character_name": "Bob"}},
        requested_panel_count=3,
    )
    assert len(panels) == 3
    assert "exactly 3 panels" in llm.calls[0][0]
    assert "不可发送" not in llm.calls[0][1]


@pytest.mark.asyncio
async def test_requested_count_replans_existing_declared_panels() -> None:
    llm = _SequencePanelLLM(
        '{"panels":['
        '{"participants":["alice"],"location":"后院","description":"审问","evidence_ids":["n1"]},'
        '{"participants":["bob"],"location":"塔底","description":"搜索","evidence_ids":["n2"]},'
        '{"participants":["alice"],"location":"前厅","description":"会合","evidence_ids":["n3"]}]}',
        '{"panels":['
        '{"participants":["alice"],"location":"后院","description":"审问开门","evidence_ids":["n1"]},'
        '{"participants":["alice"],"location":"后院","description":"得到线索","evidence_ids":["n1"]},'
        '{"participants":["bob"],"location":"塔底","description":"进入残骸","evidence_ids":["n2"]},'
        '{"participants":["bob"],"location":"塔底","description":"发生滑倒","evidence_ids":["n2"]},'
        '{"participants":["alice"],"location":"前厅","description":"前厅会合","evidence_ids":["n3"]},'
        '{"participants":["alice"],"location":"前厅","description":"听见写字声","evidence_ids":["n3"]}]}',
    )
    panels, _ = await infer_scene_panels(
        llm,
        narration="Alice在后院审问并得到线索；Bob在塔底搜索并滑倒；Alice回到前厅会合。",
        actions=[], current_scene="后院",
        players={"alice": {"character_name": "Alice"}, "bob": {"character_name": "Bob"},
                 "narrator": {"character_name": "Narrator"}},
        declared_panels=[{"participants": ["alice"], "location": "后院", "description": "旧稿"}],
        requested_panel_count=6,
    )
    assert len(llm.calls) == 2
    assert len(panels) == 6


@pytest.mark.asyncio
async def test_requested_count_rejects_a_second_short_result() -> None:
    three_panels = (
        '{"panels":['
        '{"participants":["alice"],"location":"后院","description":"审问","evidence_ids":["n1"]},'
        '{"participants":["bob"],"location":"塔底","description":"搜索","evidence_ids":["n2"]},'
        '{"participants":["alice"],"location":"前厅","description":"会合","evidence_ids":["n3"]}]}'
    )
    llm = _SequencePanelLLM(three_panels, three_panels)

    with pytest.raises(StoryboardInferenceError, match="要求 6 格，实际 3 格"):
        await infer_scene_panels(
            llm,
            narration="Alice在后院审问；Bob在塔底搜索；Alice回到前厅会合。",
            actions=[], current_scene="后院",
            players={"alice": {"character_name": "Alice"}, "bob": {"character_name": "Bob"}},
            requested_panel_count=6,
        )

    assert len(llm.calls) == 2
    assert '"requested_panel_count":6' in llm.calls[0][1]
    assert "first pass produced 3 valid panels" in llm.calls[1][1]


def test_six_panel_prompt_keeps_exact_count_without_forcing_a_grid() -> None:
    panels = [
        {"participants": [], "location": f"地点{i}", "description": "很长的画面描述" * 80}
        for i in range(1, 7)
    ]
    prompt, metadata = build_storyboard_prompt(panels, max_chars=900)

    assert metadata["layout"] == "six-panel"
    assert "exactly 6 distinct panels" in prompt
    assert "exactly 6 regions" in prompt
    assert "3 x 2" not in prompt
    assert len(prompt) <= 900
    for index in range(1, 7):
        assert f"Panel {index}" in prompt


def test_three_panel_prompt_leaves_geometry_to_the_visual_model() -> None:
    prompt, _ = build_storyboard_prompt([
        {"participants": [], "location": "A", "description": "一"},
        {"participants": [], "location": "B", "description": "二"},
        {"participants": [], "location": "C", "description": "三"},
    ])

    assert "adaptive arrangement" in prompt
    assert "freely choose the geometry" in prompt
    assert "one row" not in prompt


@pytest.mark.asyncio
async def test_ai_splits_merged_locations_when_each_has_public_evidence() -> None:
    llm = _PanelLLM(
        '{"panels":['
        '{"participants":["观者"],"location":"砖墙暗道","description":"观者进入暗道","evidence_ids":["n1"]},'
        '{"participants":["情緒","阿尔比娜"],"location":"站前窄巷","description":"两人在巷口会合","evidence_ids":["n2"]}'
        ']}'
    )
    panels, _ = await infer_scene_panels(
        llm,
        narration="观者沿砖墙暗道向前。站前窄巷里，情緒与阿尔比娜已赶到巷口。",
        actions=[],
        current_scene="阿卡姆车站",
        players={
            "watcher": {"character_name": "观者"},
            "emotion": {"character_name": "情緒"},
            "albina": {"character_name": "阿尔比娜"},
        },
    )

    assert [panel["location"] for panel in panels] == ["砖墙暗道", "站前窄巷"]
    assert panels[0]["participants"] == ["watcher"]
    assert panels[1]["participants"] == ["emotion", "albina"]


@pytest.mark.asyncio
async def test_ai_review_splits_independent_key_beats_at_same_location() -> None:
    llm = _SequencePanelLLM(
        '{"panels":[{"participants":["alice"],"location":"后院",'
        '"description":"队伍在后院行动","evidence_ids":["n1"]}]}',
        '{"panels":['
        '{"participants":["alice"],"location":"后院","description":"打开门链","evidence_ids":["n1"]},'
        '{"participants":["alice"],"location":"后院","description":"发现暗号","evidence_ids":["n2"]}'
        ']}'
    )
    panels, _ = await infer_scene_panels(
        llm,
        narration="爱丽丝在后院打开门链。随后，她发现门上的暗号。",
        actions=[],
        current_scene="后院",
        players={"alice": {"character_name": "爱丽丝"}, "bob": {"character_name": "鲍勃"}},
    )

    assert len(llm.calls) == 2
    assert len(panels) == 2
    assert [panel["description"] for panel in panels] == ["打开门链", "发现暗号"]


@pytest.mark.asyncio
async def test_explicit_same_location_panels_remain_separate() -> None:
    panels, compressed = await infer_scene_panels(
        None,
        narration="后院发生两次关键动作。",
        actions=[],
        current_scene="后院",
        players={"alice": {"character_name": "Alice"}, "bob": {"character_name": "Bob"}},
        declared_panels=[
            {"participants": ["alice"], "location": "后院", "description": "打开门链"},
            {"participants": ["bob"], "location": "后院", "description": "发现暗号"},
        ],
    )
    assert compressed == 0
    assert [panel["description"] for panel in panels] == ["打开门链", "发现暗号"]


@pytest.mark.asyncio
async def test_force_single_skips_automatic_storyboard_inference() -> None:
    llm = _PanelLLM(
        '{"panels":['
        '{"participants":["alice"],"location":"后院","description":"打开门链","evidence_ids":["n1"]},'
        '{"participants":["bob"],"location":"塔底","description":"检查残骸","evidence_ids":["n2"]}'
        ']}'
    )
    panels, compressed = await infer_scene_panels(
        llm,
        narration="Alice 在后院打开门链。与此同时，Bob 在塔底检查残骸。",
        actions=[],
        current_scene="后院",
        players={"alice": {"character_name": "Alice"}, "bob": {"character_name": "Bob"}},
        force_single=True,
    )

    assert len(panels) == 1
    assert compressed == 0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_ai_rejects_unknown_evidence_or_participant() -> None:
    llm = _PanelLLM(
        '{"panels":[{"participants":["ghost"],"location":"不存在的地下室",'
        '"description":"幻觉","evidence_ids":["missing"]}]}'
    )
    panels, compressed = await infer_scene_panels(
        llm,
        narration="Alice 和 Bob 都在车站大厅。",
        actions=[],
        current_scene="车站大厅",
        players={"alice": {"character_name": "Alice"}, "bob": {"character_name": "Bob"}},
    )

    assert len(panels) == 1
    assert panels[0]["location"] == "车站大厅"
    assert compressed == 0


@pytest.mark.asyncio
async def test_ai_keeps_six_valid_key_beats_and_counts_overflow() -> None:
    panels = [
        {
            "participants": ["alice"],
            "location": f"房间{i}",
            "description": f"关键动作{i}",
            "evidence_ids": [f"n{i}"],
        }
        for i in range(1, 8)
    ]
    llm = _PanelLLM(json.dumps({"panels": panels, "compressed_count": 0}, ensure_ascii=False))
    narration = "".join(f"房间{i}发生关键动作{i}。" for i in range(1, 8))
    result, compressed = await infer_scene_panels(
        llm,
        narration=narration,
        actions=[],
        current_scene="房间1",
        players={"alice": {"character_name": "Alice"}, "bob": {"character_name": "Bob"}},
    )

    assert len(result) == 6
    assert compressed == 1


@pytest.mark.asyncio
async def test_ai_uncertain_result_stays_single_scene() -> None:
    llm = _PanelLLM(
        '{"panels":[{"participants":[],"location":"车站大厅",'
        '"description":"队伍在大厅调查"}]}'
    )
    panels, compressed = await infer_scene_panels(
        llm,
        narration="队伍在大厅分别查看门窗和柜台。",
        actions=[],
        current_scene="车站大厅",
        players={"alice": {}, "bob": {}},
    )

    assert len(panels) == 1
    assert panels[0]["participants"] == ["alice", "bob"]
    assert compressed == 0


@pytest.mark.asyncio
async def test_single_scene_fallback_does_not_treat_unknown_people_as_the_whole_party() -> None:
    panels, _ = await infer_scene_panels(
        None,
        narration="米勒太太独自在厨房整理旧簿子。",
        actions=[],
        current_scene="厨房",
        players={
            "alice": {"character_name": "爱丽丝"},
            "bob": {"character_name": "鲍勃"},
        },
    )

    assert panels[0]["participants"] == []


@pytest.mark.asyncio
async def test_ai_deterministic_fallback_splits_long_multilocation_narration() -> None:
    llm = _SequencePanelLLM(
        '{"panels":[{"participants":[],"location":"门口","description":"合并画面","evidence_ids":["n1"]}]}',
        '{"panels":[{"participants":[],"location":"门口","description":"仍然合并","evidence_ids":["n1"]}]}',
    )
    panels, _ = await infer_scene_panels(
        llm,
        narration=(
            '门口的老妇人交出簿子；与此同时，塔底的队伍钻入船肋；'
            '随后，托马斯在残骸中滑倒；阿尔比娜回到寄宿屋前厅。'
        ),
        actions=[],
        current_scene='寄宿屋',
        players={
            'emotion': {'character_name': '情緒'},
            'sakura': {'character_name': '樱羽艾玛'},
            'tsn': {'character_name': 'tsn'},
            'thomas': {'character_name': '托马斯'},
            'albina': {'character_name': '阿尔比娜'},
        },
    )
    assert len(panels) >= 3
    assert any('塔底' in panel['location'] for panel in panels)
    assert any('前厅' in panel['location'] for panel in panels)


@pytest.mark.asyncio
async def test_ai_multi_panel_hallucination_without_location_evidence_is_rejected() -> None:
    llm = _PanelLLM(
        '{"panels":['
        '{"participants":["alice"],"location":"车站大厅","description":"查看门窗"},'
        '{"participants":["bob"],"location":"不存在的地下室","description":"检查祭坛"}'
        ']}'
    )
    panels, compressed = await infer_scene_panels(
        llm,
        narration="Alice 和 Bob 都在车站大厅，分别查看门窗和柜台。",
        actions=[],
        current_scene="车站大厅",
        players={"alice": {}, "bob": {}},
    )

    assert len(panels) == 1
    assert panels[0]["location"] == "车站大厅"
    assert compressed == 0


def test_public_character_appearance_is_scoped_and_prompt_is_panel_specific() -> None:
    players = {
        "alice": {
            "character_name": "艾琳",
            "character_sheet": {
                "race": "人类",
                "class": "调查员",
                "appearance": "黑色短发，戴圆框眼镜，穿深色风衣。",
                "private_log": "不要把这段秘密送给图片模型。",
            },
        },
        "bob": {
            "character_name": "布鲁",
            "character_sheet": {
                "race": "改造人",
                "class": "艺术家",
                "background": "白金色尖刺重甲，左臂是锯子。",
            },
        },
    }
    appearances = public_character_appearances(players)
    assert "黑色短发" in appearances["alice"]
    assert "秘密" not in appearances["alice"]
    assert "白金色尖刺重甲" in appearances["bob"]

    prompt, _ = build_storyboard_prompt([
        {"participants": ["alice"], "location": "车站", "description": "雨夜月台"},
        {"participants": ["bob"], "location": "地下室", "description": "石室祭坛"},
    ], character_appearances=appearances)
    first = prompt.index("黑色短发")
    second = prompt.index("白金色尖刺重甲")
    assert first < second
    assert prompt.index("Panel 1") < first < prompt.index("Panel 2")
    assert prompt.count("黑色短发") == 1
    assert "subjects 艾琳" in prompt
    assert "subjects 布鲁" in prompt
    assert "subjects alice" not in prompt


def test_empty_participants_do_not_expand_to_the_shared_party() -> None:
    prompt, _ = build_storyboard_prompt([
        {"participants": [], "location": "厨房", "description": "米勒太太说出线索"},
    ], character_appearances={"alice": "艾琳 (appearance: 黑发)"})

    assert "only people explicitly named in this panel description" in prompt
    assert "艾琳" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("count", range(1, 7))
async def test_fixed_counts_bypass_automatic_density_and_fallback(count):
    raw = [{
        "participants": ["alice"], "location": "后院",
        "description": f"Alice打开门链后的反应{index}", "evidence_ids": ["n1"],
    } for index in range(count)]
    llm = _PanelLLM(json.dumps({"panels": raw}))
    panels, _ = await infer_scene_panels(
        llm, narration="Alice在后院打开门链。与此同时，Bob在塔底搜索。Alice随后回到前厅。",
        actions=[], current_scene="后院",
        players={"alice": {"character_name": "Alice", "private_log": "SECRET"}, "bob": {}},
        requested_panel_count=count,
    )
    assert len(panels) == count
    assert len(llm.calls) == 1
    system, payload, options = llm.calls[0]
    assert f"exactly {count} panels" in system
    assert "Use 2-4" not in system
    assert "only for unusually dense" not in system
    assert json.loads(payload)["requested_panel_count"] == count
    assert "SECRET" not in payload
    assert options["max_tokens"] >= 300 + count * 360


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["too_many", "empty", "duplicate", "no_evidence"])
async def test_fixed_count_rejects_invalid_six_even_after_correction(kind):
    raw = [{
        "participants": ["alice"], "location": "后院",
        "description": f"动作{index}", "evidence_ids": ["n1"],
    } for index in range(7 if kind == "too_many" else 6)]
    if kind == "empty":
        raw[-1]["description"] = ""
    if kind == "duplicate":
        raw[-1] = raw[0].copy()
    if kind == "no_evidence":
        raw[-1].pop("evidence_ids")
    llm = _PanelLLM(json.dumps({"panels": raw}))
    with pytest.raises(StoryboardInferenceError, match="要求 6 格"):
        await infer_scene_panels(
            llm, narration="Alice在后院打开门链并发现暗号。",
            actions=[], current_scene="后院", players={"alice": {"character_name": "Alice"}},
            requested_panel_count=6,
        )
    assert len(llm.calls) == 2


def test_tight_budget_keeps_every_location_and_subject_or_reports_error():
    panels = [{
        "participants": [f"hero-{index}"], "location": f"place-{index}",
        "description": "A meaningful action with a visible result. " * 20,
    } for index in range(6)]
    prompt, _ = build_storyboard_prompt(panels, max_chars=900)
    for index in range(6):
        assert f"hero-{index}" in prompt and f"place-{index}" in prompt
    assert len(prompt) <= 900
    with pytest.raises(StoryboardInferenceError, match="预算不足"):
        build_storyboard_prompt(panels, max_chars=256)
