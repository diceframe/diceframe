"""SCENE_IMAGE parsing, automatic generation, and throttling."""

from __future__ import annotations

import asyncio

import pytest

from src.commands.round_processor import RoundProcessor
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance
from src.imagegen import ImageGenerationError, ImageGenerationResult


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
    assert registry.instance.scene_image == {"kind": "generated", "asset_id": "a" * 64}
    request = service.requests[0]
    assert request.prompt == "harbor at dusk"
    assert request.purpose == "scene"
    assert request.owner_type == "game"
    assert request.owner_id == "web:room:test"
    assert request.aspect_ratio == "16:9"
    assert request.context == {"round": 3}
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
        {"scene_image_prompt": "harbor at dusk", "state_update": {"scene_change": "雾港码头"}},
    )
    assert task is not None
    task.cancel()

    service.auto_scene = False
    assert processor._maybe_schedule_scene_image(
        registry.instance,
        {"scene_image_prompt": "new scene"},
    ) is None

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
