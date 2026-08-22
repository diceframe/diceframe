"""SCENE_IMAGE 标签链路：标签解析、回合后台调度与节流。"""

from __future__ import annotations

import asyncio

import pytest

from src.commands.round_processor import RoundProcessor
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance
from src.imagegen import ImageGenError


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


class _FakeGenerator:
    def __init__(self, *, result=None, error: str = ""):
        self.result = result or {
            "reference": {"kind": "upload", "asset_id": "a" * 64},
            "asset_id": "a" * 64,
            "revised_prompt": "",
        }
        self.error = error
        self.prompts = []

    def available(self):
        return True

    async def generate(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise ImageGenError(self.error)
        return dict(self.result)


class _FakeRegistry:
    def __init__(self):
        self.saved = []

    def get(self, game_key):
        return self.instance

    async def save(self, instance):
        self.saved.append(instance.game_key)


def _processor(registry, generator) -> RoundProcessor:
    processor = RoundProcessor(
        registry, None, None, None, None, None, None, None, None, None, None,
        lambda _: None, lambda _: None, 1, 1, 1,
    )
    processor.set_scene_image_generator(generator)
    return processor


def _instance_with_log(scene_image=None) -> GameInstance:
    instance = GameInstance(game_key=("web", "room", "test"))
    instance.log.append({"round": 3, "gm_response": "narration", "scene_image": scene_image or {}})
    return instance


@pytest.mark.asyncio
async def test_schedule_scene_image_updates_log_and_scene_image():
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()
    generator = _FakeGenerator()
    processor = _processor(registry, generator)

    task = processor.schedule_scene_image(registry.instance, "harbor at dusk", 3)
    assert task is not None
    await task

    entry = registry.instance.log[-1]
    assert entry["scene_image"]["status"] == "ready"
    assert entry["scene_image"]["prompt"] == "harbor at dusk"
    assert registry.instance.scene_image == {"kind": "upload", "asset_id": "a" * 64}
    assert registry.saved == [registry.instance.game_key]


@pytest.mark.asyncio
async def test_schedule_scene_image_skips_when_round_rolled_back():
    registry = _FakeRegistry()
    instance = _instance_with_log()
    instance.log.clear()  # 该回合已被回滚删除
    registry.instance = instance
    generator = _FakeGenerator()
    processor = _processor(registry, generator)

    task = processor.schedule_scene_image(instance, "harbor at dusk", 3)
    assert task is not None
    await task

    assert generator.prompts == []
    assert registry.saved == []


@pytest.mark.asyncio
async def test_scene_image_generation_failure_does_not_crash_round():
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()
    processor = _processor(registry, _FakeGenerator(error="upstream down"))

    task = processor.schedule_scene_image(registry.instance, "harbor at dusk", 3)
    await task  # 不抛异常，仅记日志

    assert registry.instance.log[-1].get("scene_image") in (None, {})
    assert registry.saved == []


@pytest.mark.asyncio
async def test_maybe_schedule_applies_prompt_throttle():
    registry = _FakeRegistry()
    # 上一张图与本次描述相同且本回合无场景切换 → 不调度
    registry.instance = _instance_with_log(scene_image={
        "status": "ready", "prompt": "harbor at dusk",
        "reference": {"kind": "upload", "asset_id": "b" * 64},
    })
    registry.instance.round_number = 4
    generator = _FakeGenerator()
    processor = _processor(registry, generator)

    task = processor._maybe_schedule_scene_image(
        registry.instance, {"scene_image_prompt": "harbor at dusk", "state_update": {}},
    )
    assert task is None

    # 同描述但本回合发生场景切换 → 调度
    task = processor._maybe_schedule_scene_image(
        registry.instance,
        {"scene_image_prompt": "harbor at dusk", "state_update": {"scene_change": "雾港码头"}},
    )
    assert task is not None
    task.cancel()

    # 无生成器 → 零开销跳过
    processor.set_scene_image_generator(None)
    assert processor._maybe_schedule_scene_image(
        registry.instance, {"scene_image_prompt": "harbor at dusk"},
    ) is None


@pytest.mark.asyncio
async def test_in_flight_image_blocks_new_request_for_same_game():
    slow_release = asyncio.Event()
    registry = _FakeRegistry()
    registry.instance = _instance_with_log()

    class _SlowGenerator(_FakeGenerator):
        async def generate(self, prompt):
            await slow_release.wait()
            return await super().generate(prompt)

    processor = _processor(registry, _SlowGenerator())
    first = processor.schedule_scene_image(registry.instance, "first", 3)
    assert first is not None
    second = processor.schedule_scene_image(registry.instance, "second", 3, force=True)
    assert second is None
    slow_release.set()
    await first
