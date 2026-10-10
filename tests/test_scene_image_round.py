"""SCENE_IMAGE parsing, automatic generation, and throttling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.engine.modules import narrative_notes
from src.commands.round_processor import RoundProcessor
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance
from src.imagegen import ImageGenerationError, ImageGenerationResult
from src.engine.modules import media


def test_scene_image_tag_parses_into_prompt():
    data = parse_tag_state(
        "正文。\n---\nSCENE:雾港码头\nSCENE_IMAGE:misty harbor town at dusk, oil painting style\nQUEST:寻找船长:active\n"
    )
    assert data["state_update"]["scene_change"] == "雾港码头"
    assert data["scene_image_prompt"] == "misty harbor town at dusk, oil painting style"


def test_scene_image_tag_truncated_to_limit():
    data = parse_tag_state(f"正文。\n---\nSCENE_IMAGE:{'x' * 500}")
    assert len(data["scene_image_prompt"]) == 300


def test_scene_image_tag_absent_by_default():
    data = parse_tag_state("正文。\n---\nNONE")
    assert data["scene_image_prompt"] == ""


def test_scene_tag_alone_does_not_request_image():
    data = parse_tag_state("正文。\n---\nSCENE:雾港码头\n")
    assert data["state_update"]["scene_change"] == "雾港码头"
    assert data["scene_image_prompt"] == ""


class _FakeImageGenerationService:
    available = True
    auto_scene = True

    def __init__(self, *, error: str = ""):
        self.error = error
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.error:
            raise ImageGenerationError(self.error)
        return ImageGenerationResult(
            generation_id="1" * 32,
            asset_id="a" * 64,
            purpose=request.purpose,
            prompt=request.prompt,
            revised_prompt="",
            provider="openai-compatible",
            model="image-model",
            created_at="2026-08-22T00:00:00+00:00",
        )


class _FakeRegistry:
    def __init__(self):
        self.saved = []

    def get(self, game_key):
        return self.instance

    async def save(self, instance):
        self.saved.append(instance.game_key)


def _processor(registry, service) -> RoundProcessor:
    processor = RoundProcessor(
        registry, None, None, None, None, None, None, None, None, None, None,
        lambda _: None, lambda _: None, 1, 1, 1,
    )
    processor.set_image_generation_service(service)
    return processor


def _instance_with_log(scene_image=None) -> GameInstance:
    instance = GameInstance(game_key=("web", "room", "test"))
    instance.log.append({"round": 3, "gm_response": "narration", "scene_image": scene_image or {}})
    return instance


@pytest.mark.asyncio
async def test_schedule_scene_image_updates_log_and_generated_reference():
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()
    service = _FakeImageGenerationService()
    processor = _processor(registry, service)

    task = processor.schedule_scene_image(registry.instance, "harbor at dusk", 3)
    assert task is not None
    await task

    entry = registry.instance.log[-1]
    assert entry["scene_image"]["status"] == "ready"
    assert entry["scene_image"]["generation_id"] == "1" * 32
    assert media.scene_image(registry.instance) == {"kind": "generated", "asset_id": "a" * 64}
    request = service.requests[0]
    assert request.prompt == "harbor at dusk"
    assert request.purpose == "scene"
    assert request.owner_type == "game"
    assert request.owner_id == "web:room:test"
    assert request.aspect_ratio == "16:9"
    assert request.context["round"] == 3
    assert request.context["run_id"] == registry.instance.run_id
    assert request.context["scene"] == narrative_notes.scene(registry.instance)
    assert "narration" in request.context
    assert "panels" in request.context
    assert registry.saved == [registry.instance.game_key]


@pytest.mark.asyncio
async def test_schedule_scene_image_skips_when_round_rolled_back():
    registry = _FakeRegistry()
    instance = _instance_with_log()
    instance.log.clear()
    registry.instance = instance
    service = _FakeImageGenerationService()
    processor = _processor(registry, service)

    task = processor.schedule_scene_image(instance, "harbor at dusk", 3)
    assert task is not None
    await task

    assert service.requests == []
    assert registry.saved == []


@pytest.mark.asyncio
async def test_scene_image_generation_failure_does_not_crash_round():
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()
    processor = _processor(registry, _FakeImageGenerationService(error="upstream down"))

    task = processor.schedule_scene_image(registry.instance, "harbor at dusk", 3)
    assert task is not None
    await task

    assert registry.instance.log[-1].get("scene_image") in (None, {})
    assert registry.saved == []


@pytest.mark.asyncio
async def test_maybe_schedule_applies_prompt_and_feature_throttles():
    registry = _FakeRegistry()
    registry.instance = _instance_with_log(scene_image={
        "status": "ready",
        "prompt": "harbor at dusk",
        "reference": {"kind": "generated", "asset_id": "b" * 64},
    })
    registry.instance.round_number = 4
    service = _FakeImageGenerationService()
    processor = _processor(registry, service)

    task = processor._maybe_schedule_scene_image(
        registry.instance,
        {"scene_image_prompt": "harbor at dusk", "state_update": {}},
    )
    assert task is None

    task = processor._maybe_schedule_scene_image(
        registry.instance,
        {"scene_image_prompt": "harbor at dusk", "state_update": {}},
    )
    assert task is None

    # A normal SCENE transition is not an image directive.  The legacy
    # SCENE_IMAGE protocol remains the default trigger.
    assert processor._maybe_schedule_scene_image(
        registry.instance,
        {"scene_image_prompt": "", "scene_panels": [], "state_update": {"scene_change": "雾港码头"}},
    ) is None

    task = processor._maybe_schedule_scene_image(
        registry.instance,
        {"scene_image_prompt": "new harbor view", "state_update": {"scene_change": "雾港码头"}},
    )
    assert task is not None
    task.cancel()

    service.auto_scene = False
    assert processor._maybe_schedule_scene_image(
        registry.instance,
        {"scene_image_prompt": "new scene"},
    ) is None


@pytest.mark.asyncio
async def test_maybe_schedule_requires_scene_image_tag_by_default():
    """普通 SCENE 或单独 SCENE_PANEL 不应改变原版自动触发契约。"""
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()
    registry.instance.round_number = 4
    service = _FakeImageGenerationService()
    processor = _processor(registry, service)

    assert processor._maybe_schedule_scene_image(
        registry.instance,
        {"state_update": {"scene_change": "雾港码头"}},
    ) is None
    assert processor._maybe_schedule_scene_image(
        registry.instance,
        {
            "scene_panels": [
                {"participants": "Alice", "location": "码头", "description": "雾中守望"},
            ],
            "state_update": {"scene_change": "雾港码头"},
        },
    ) is None
    assert service.requests == []


@pytest.mark.asyncio
async def test_scene_image_tag_triggers_and_scene_panels_are_attached_data():
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()
    registry.instance.round_number = 4
    service = _FakeImageGenerationService()
    processor = _processor(registry, service)

    task = processor._maybe_schedule_scene_image(
        registry.instance,
        {
            "scene_image_prompt": "harbor at dusk",
            "scene_panels": [
                {"participants": "Alice", "location": "码头", "description": "雾中守望"},
            ],
            "state_update": {"scene_change": "雾港码头"},
        },
    )
    assert task is not None
    await task
    assert len(service.requests) == 1
    request = service.requests[0]
    assert request.prompt == "harbor at dusk"
    assert request.context["storyboard"]["panels"][0]["location"] == "码头"


@pytest.mark.asyncio
async def test_declared_scene_panels_remain_authoritative_when_auto_storyboard_disabled():
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()
    registry.instance.round_number = 4
    service = _FakeImageGenerationService()
    service.auto_storyboard = False
    processor = _processor(registry, service)

    task = processor._maybe_schedule_scene_image(
        registry.instance,
        {
            "scene_image_prompt": "harbor at dusk",
            "scene_panels": [
                {"participants": "Alice", "location": "码头", "description": "雾中守望"},
            ],
        },
    )
    assert task is not None
    await task
    assert service.requests[0].context["storyboard"]["panels"] == [
        {"participants": ["Alice"], "location": "码头", "description": "雾中守望"},
    ]


@pytest.mark.asyncio
async def test_automatic_scene_image_recovers_multilocation_storyboard_from_single_model_result():
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()
    registry.instance.log[-1]["gm_response"] = (
        "情緒在寄宿屋门链前逼问米勒太太；与此同时，"
        "樱羽艾玛与tsn钻进塔底船肋；随后，托马斯在残骸中滑倒；"
        "阿尔比娜退出后院，回到寄宿屋前厅。"
    )
    registry.instance.players = {
        "emotion": {"character_name": "情緒"},
        "sakura": {"character_name": "樱羽艾玛"},
        "tsn": {"character_name": "tsn"},
        "thomas": {"character_name": "托马斯"},
        "albina": {"character_name": "阿尔比娜"},
    }
    service = _FakeImageGenerationService()
    processor = _processor(registry, service)

    class _SinglePanelLlm:
        async def call(self, *_args, **_kwargs):
            return SimpleNamespace(content=(
                '{"panels":[{"participants":[],"location":"寄宿屋",'
                '"description":"合并场景","evidence_ids":["n1"]}]}'
            ))

    processor.llm_client = _SinglePanelLlm()
    task = processor.schedule_scene_image(registry.instance, "雨夜调查", 3)
    assert task is not None
    await task

    panels = service.requests[0].context["storyboard"]["panels"]
    assert len(panels) >= 3
    assert any("塔底" in panel["location"] for panel in panels)
    assert any("前厅" in panel["location"] for panel in panels)
    assert registry.instance.log[-1]["scene_image"]["layout"] != "single"


@pytest.mark.asyncio
async def test_opening_scene_panels_without_scene_image_do_not_trigger():
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()
    registry.instance.log[-1]["round"] = 0
    registry.instance.log[-1]["scene_panels"] = [
        {"participants": "Alice", "location": "码头", "description": "雾中守望"},
    ]
    registry.instance.round_number = 1
    service = _FakeImageGenerationService()
    processor = _processor(registry, service)

    assert processor.schedule_opening_scene_image(registry.instance) is None
    assert service.requests == []

    processor.set_image_generation_service(None)
    assert processor._maybe_schedule_scene_image(
        registry.instance,
        {"scene_image_prompt": "new scene"},
    ) is None


@pytest.mark.asyncio
async def test_in_flight_image_blocks_new_request_for_same_game():
    slow_release = asyncio.Event()
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()

    class _SlowService(_FakeImageGenerationService):
        async def generate(self, request):
            await slow_release.wait()
            return await super().generate(request)

    processor = _processor(registry, _SlowService())
    first = processor.schedule_scene_image(registry.instance, "first", 3)
    assert first is not None
    second = processor.schedule_scene_image(registry.instance, "second", 3, force=True)
    assert second is None
    slow_release.set()
    await first


@pytest.mark.asyncio
async def test_in_flight_image_from_previous_run_cannot_mutate_restarted_game():
    slow_release = asyncio.Event()
    registry = _FakeRegistry()
    previous = _instance_with_log()
    registry.instance = previous

    class _SlowService(_FakeImageGenerationService):
        async def generate(self, request):
            await slow_release.wait()
            return await super().generate(request)

    processor = _processor(registry, _SlowService())
    stale_task = processor.schedule_scene_image(previous, "old run", 3)
    assert stale_task is not None
    await asyncio.sleep(0)

    restarted = _instance_with_log()
    registry.instance = restarted
    slow_release.set()
    await stale_task

    assert media.scene_image(restarted) == {}
    assert restarted.log[-1]["scene_image"] == {}
    assert registry.saved == []
