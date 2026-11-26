"""Scene art and storyboard drafting for a round that has already been narrated.

``RoundProcessor`` owns the round lifecycle; this module owns the media work
that runs beside it: the duplicate throttle that keeps one game to one
in-flight scene image, the background generation and storyboard-inference
tasks, and the task bookkeeping keys that make a repeated round a no-op.

Every entry point takes an explicit :class:`SceneMediaContext` rather than the
processor, so nothing here can reach back into round state it does not own.
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.commands.round_helpers import log_round
from src.engine import module_hooks
from src.engine.game_instance import GameInstance
from src.engine.modules import progression_state
from src.imagegen import (
    ImageGenerationError,
    ImageGenerationRequest,
    StoryboardInferenceError,
    game_image_owner_id,
    infer_scene_panels,
    normalize_scene_panels,
    public_character_appearances,
    storyboard_layout,
    storyboard_source_revision,
    storyboard_panel_metadata,
)
from src.imagegen.game_context import capture_storyboard_context

logger = logging.getLogger("trpg")


@dataclass(frozen=True, slots=True)
class SceneMediaContext:
    """The processor state the media pipeline is allowed to touch.

    ``service`` is read again on every call because the composition root can
    swap or clear the image-generation service at any time; the two task dicts
    are the processor's own, shared by reference so cancellation and the
    one-task-per-game throttle keep working exactly as before.
    """

    service: Any | None
    scene_tasks: dict[tuple[tuple[str, ...], str], asyncio.Task]
    draft_tasks: dict[tuple[tuple[str, ...], str, int, int, str], asyncio.Task]
    llm_client: Any
    registry: Any


def _last_scene_image_signature(instance: GameInstance) -> tuple[str, tuple[tuple[Any, ...], ...]]:
    """Return the latest prompt/panel signature for duplicate-generation throttling."""
    for entry in reversed(instance.log):
        record = entry.get("scene_image")
        if isinstance(record, dict) and record.get("status") == "ready":
            panels, _ = normalize_scene_panels(
                record.get("panels"), merge_same_location=False,
            )
            panel_key = tuple(
                (tuple(panel.get("participants") or []), panel.get("location", ""), panel.get("description", ""))
                for panel in panels
            )
            return str(record.get("prompt") or ""), panel_key
    return "", ()


def _scene_storyboard_payload(data: dict) -> tuple[str, list[dict[str, Any]], int]:
    """Return a shared prompt plus normalized public panels for this round."""
    raw_panels = data.get("scene_panels")
    panels, compressed_count = normalize_scene_panels(
        raw_panels, merge_same_location=False,
    )
    if not panels:
        return "", [], compressed_count
    global_prompt = str(data.get("scene_image_prompt") or "").strip()
    # The image service is the single owner of provider prompt composition.
    # Keep this value as the ordinary/global prompt so it cannot be composed twice.
    return global_prompt, panels, compressed_count


def maybe_schedule_scene_image(media: SceneMediaContext, instance: GameInstance, data: dict) -> asyncio.Task | None:
    """Schedule scene art only when the GM emits an explicit SCENE_IMAGE."""
    service = media.service
    if service is None or not service.available or not service.auto_scene:
        return None
    prompt = str(data.get("scene_image_prompt") or "").strip()
    completed_round = int(progression_state.round_value(instance)) - 1
    if completed_round < 0:
        return None
    _, panels, compressed_count = _scene_storyboard_payload(data)
    if not prompt:
        return None
    # Explicit image directives still use the normal duplicate throttle.
    return schedule_scene_image(media, 
        instance,
        prompt,
        completed_round,
        force=False,
        panels=panels,
        compressed_count=compressed_count,
    )


def maybe_schedule_storyboard_draft(
    media: SceneMediaContext,
    instance: GameInstance,
    data: dict,
) -> asyncio.Task | None:
    """Fill a missing GM storyboard in the background without generating art.

    ``SCENE_IMAGE`` remains the only automatic image trigger.  Rounds that
    carry that tag are handled by the image task, which already performs a
    bounded storyboard inference; this draft-only path therefore skips
    them to avoid duplicate model calls.
    """
    service = media.service
    if service is None or not bool(getattr(service, "auto_storyboard", False)):
        return None
    # Skip only when the real automatic image task will handle the same
    # inference.  If automatic images are disabled/unavailable, a
    # SCENE_IMAGE tag must not prevent the manual storyboard draft from
    # being prepared.
    if (
        str(data.get("scene_image_prompt") or "").strip()
        and bool(getattr(service, "available", False))
        and bool(getattr(service, "auto_scene", False))
    ):
        return None
    declared, _ = normalize_scene_panels(
        data.get("scene_panels"), merge_same_location=False,
    )
    if declared:
        return None

    completed_round = int(progression_state.round_value(instance)) - 1
    if completed_round < 0:
        return None
    entry = next(
        (
            item for item in reversed(instance.log)
            if log_round(item, -1) == completed_round
            and str(item.get("gm_response") or "").strip()
        ),
        None,
    )
    if entry is None:
        return None

    game_key = instance.game_key
    expected_run_id = instance.run_id
    expected_swipe = int(entry.get("current_swipe") or 0)
    source_revision = storyboard_source_revision(entry)
    task_key = (
        game_key,
        expected_run_id,
        completed_round,
        expected_swipe,
        source_revision,
    )
    existing = media.draft_tasks.get(task_key)
    if existing is not None and not existing.done():
        return None

    storyboard_context = capture_storyboard_context(instance)
    task = asyncio.create_task(infer_storyboard_draft_background(media, 
        game_key=game_key,
        expected_run_id=expected_run_id,
        round_number=completed_round,
        expected_swipe=expected_swipe,
        source_revision=source_revision,
        narration=str(entry.get("gm_response") or ""),
        actions=deepcopy(entry.get("actions") or []),
        current_scene=storyboard_context["current_scene"],
        players=storyboard_context["players"],
    ))
    media.draft_tasks[task_key] = task
    task.add_done_callback(
        # The default argument binds this task's key; mypy cannot infer the
        # parameter types of a lambda in this position.
        lambda completed, key=task_key: (  # type: ignore[misc]
            media.draft_tasks.pop(key, None)
            if media.draft_tasks.get(key) is completed
            else None
        )
    )
    return task


async def infer_storyboard_draft_background(
    media: SceneMediaContext,
    *,
    game_key: tuple[str, ...],
    expected_run_id: str,
    round_number: int,
    expected_swipe: int,
    source_revision: str,
    narration: str,
    actions: Any,
    current_scene: str,
    players: Any,
) -> None:
    """Infer and persist one version-bound public storyboard draft."""
    try:
        panels, _compressed = await infer_scene_panels(
            media.llm_client,
            narration=narration,
            actions=actions,
            current_scene=current_scene,
            players=players,
            global_prompt="",
            declared_panels=[],
            # This is an optional UI prefill.  Unlike automatic image
            # generation it may safely fall back to a public single-panel
            # draft when the model is unavailable.
            strict=False,
        )
        panels, _ = normalize_scene_panels(
            panels, merge_same_location=False,
        )
        if not panels:
            logger.warning(
                "GM 漏掉分镜且后台分析未返回草稿 "
                "(game=%s round=%d swipe=%d)",
                game_key,
                round_number,
                expected_swipe,
            )
            return

        current = media.registry.get(game_key)
        if current is None or current.run_id != expected_run_id:
            return
        entry = next(
            (
                item for item in reversed(current.log)
                if log_round(item, -1) == round_number
                and str(item.get("gm_response") or "").strip()
            ),
            None,
        )
        if (
            entry is None
            or int(entry.get("current_swipe") or 0) != expected_swipe
            or storyboard_source_revision(entry) != source_revision
        ):
            return
        # A GM panel or a user-applied panel may have appeared while the
        # model call was in flight. Never overwrite that newer draft.
        existing, _ = normalize_scene_panels(
            entry.get("scene_panels"), merge_same_location=False,
        )
        if existing:
            return

        old_panels = deepcopy(entry.get("scene_panels"))
        old_meta = deepcopy(entry.get("scene_panel_meta"))
        entry["scene_panels"] = deepcopy(panels)
        draft_revision = storyboard_source_revision(entry)
        entry["scene_panel_meta"] = storyboard_panel_metadata(
            panels,
            narration=narration,
            actions=actions,
            current_scene=current_scene,
            source_revision=draft_revision,
        )
        try:
            await media.registry.save(current)
        except Exception:
            # A save may have waited behind another writer.  Only roll
            # back our own fields when the target entry is still the
            # exact revision we attempted to persist; otherwise a newer
            # swipe/round writer owns the current value.
            if storyboard_source_revision(entry) == draft_revision:
                if old_panels is None:
                    entry.pop("scene_panels", None)
                else:
                    entry["scene_panels"] = old_panels
                if old_meta is None:
                    entry.pop("scene_panel_meta", None)
                else:
                    entry["scene_panel_meta"] = old_meta
            logger.exception(
                "GM 漏掉分镜的后台草稿保存失败 "
                "(game=%s round=%d swipe=%d)",
                game_key,
                round_number,
                expected_swipe,
            )
            return
        logger.info(
            "GM 漏掉分镜，后台草稿已保存 "
            "(game=%s round=%d swipe=%d panels=%d)",
            game_key,
            round_number,
            expected_swipe,
            len(panels),
        )
    except Exception:
        # Draft inference is optional and must never fail the completed
        # narrative turn.
        logger.exception(
            "GM 漏掉分镜的后台分析失败 "
            "(game=%s round=%d swipe=%d)",
            game_key,
            round_number,
            expected_swipe,
        )


def schedule_opening_scene_image(media: SceneMediaContext, instance: GameInstance) -> asyncio.Task | None:
    """Schedule an opening image only when round zero explicitly requested one."""
    opening: dict[str, Any] = next(
        (
            item for item in reversed(instance.log)
            if log_round(item, -1) == 0
            and str(item.get("gm_response") or "").strip()
        ),
        {},
    )
    prompt = str(opening.get("scene_image_prompt") or "").strip()
    panels, compressed_count = normalize_scene_panels(
        opening.get("scene_panels"), merge_same_location=False,
    )
    return (
        schedule_scene_image(media, 
            instance,
            prompt,
            0,
            force=True,
            panels=panels,
            compressed_count=compressed_count,
        )
        if prompt else None
    )


def schedule_deferred_scene_image(
    media: SceneMediaContext,
    instance: GameInstance,
    payload: dict[str, Any],
) -> asyncio.Task | None:
    """Public boundary for a deferred prompt whose settlement is persisted."""

    return maybe_schedule_scene_image(media, instance, payload)


def schedule_scene_image(
    media: SceneMediaContext,
    instance: GameInstance,
    prompt: str,
    completed_round: int,
    *,
    force: bool = False,
    panels: list[dict[str, Any]] | None = None,
    compressed_count: int = 0,
) -> asyncio.Task | None:
    """为指定回合调度一次场景图生成（叙事已推送，生图在后台进行）。"""
    service = media.service
    prompt = str(prompt or "").strip()
    normalized_panels, removed = normalize_scene_panels(
        panels, merge_same_location=False,
    )
    character_appearances = public_character_appearances(
        getattr(instance, "players", {}),
    )
    if service is None or not service.available or not service.auto_scene or not prompt:
        return None
    compressed_count = max(0, int(compressed_count or 0)) + removed
    panel_key = tuple(
        (tuple(panel.get("participants") or []), panel.get("location", ""), panel.get("description", ""))
        for panel in normalized_panels
    )
    if not force:
        last_prompt, last_panel_key = _last_scene_image_signature(instance)
        if prompt == last_prompt and (
            not normalized_panels or panel_key == last_panel_key
        ):
            return None
    game_key = instance.game_key
    expected_run_id = instance.run_id
    task_key = (game_key, expected_run_id)
    existing = media.scene_tasks.get(task_key)
    if existing is not None and not existing.done():
        return None
    task = asyncio.create_task(
        generate_scene_image_background(media, 
            game_key, expected_run_id, completed_round, prompt,
            normalized_panels, compressed_count, character_appearances,
            str(module_hooks.narrative_view(instance)["scene"] or ""),
        )
    )
    media.scene_tasks[task_key] = task
    task.add_done_callback(
        # The default argument binds this task's key; mypy cannot infer the
        # parameter types of a lambda in this position.
        lambda completed, key=task_key: (  # type: ignore[misc]
            media.scene_tasks.pop(key, None)
            if media.scene_tasks.get(key) is completed
            else None
        )
    )
    return task


async def generate_scene_image_background(
    media: SceneMediaContext,
    game_key: tuple[str, ...],
    expected_run_id: str,
    round_number: int,
    prompt: str,
    panels: list[dict[str, Any]] | None = None,
    compressed_count: int = 0,
    character_appearances: dict[str, str] | None = None,
    current_scene: str = "",
) -> None:
    try:
        current = media.registry.get(game_key)
        if current is None or current.run_id != expected_run_id:
            return
        if not any(
            log_round(item, -1) == round_number
            and str(item.get("gm_response") or "").strip()
            for item in current.log
        ):
            return  # 该回合已被回滚删除，放弃本次生图
        if media.service is None:
            return
        entry = next(
            (
                item for item in reversed(current.log)
                if log_round(item, -1) == round_number
                and str(item.get("gm_response") or "").strip()
            ),
            None,
        )
        if entry is None:
            return
        source_revision = storyboard_source_revision(entry)
        inferred_from_backend = False
        if panels:
            inferred_panels, inferred_compressed = normalize_scene_panels(
                panels, merge_same_location=False,
            )
        # Real image services always expose the explicit setting (default
        # false).  Keep a true compatibility default for lightweight
        # injected test/extension services that predate this attribute.
        elif bool(getattr(media.service, "auto_storyboard", True)):
            try:
                inferred_panels, inferred_compressed = await infer_scene_panels(
                    media.llm_client,
                    narration=str(entry.get("gm_response") or ""),
                    actions=entry.get("actions") or [],
                    current_scene=current_scene or str(module_hooks.narrative_view(current)["scene"] or ""),
                    players=getattr(current, "players", {}),
                    global_prompt=prompt,
                    declared_panels=panels,
                    strict=(hasattr(media.service, "auto_storyboard")
                            and bool(getattr(media.service, "auto_storyboard", False))),
                )
                # A backend-generated draft must be available to the
                # manual image dialog even when the image request later
                # fails.  Only real image services opt into this
                # persistence; lightweight compatibility test services
                # without the setting retain their old behavior.
                inferred_from_backend = bool(
                    hasattr(media.service, "auto_storyboard")
                    and getattr(media.service, "auto_storyboard", False)
                    and inferred_panels
                )
            except StoryboardInferenceError as exc:
                logger.warning(
                    "自动场景图分镜分析失败，跳过本次生图 (game=%s round=%d): %s",
                    game_key,
                    round_number,
                    exc,
                )
                return
        else:
            inferred_panels, inferred_compressed = [], 0
        panels = inferred_panels
        compressed_count = (
            max(0, int(compressed_count or 0))
            + max(0, int(inferred_compressed or 0))
        )

        if inferred_from_backend:
            # The inference call yielded while another round/swipe may
            # have changed the public source.  Re-resolve the live entry
            # before writing the draft so an old analysis cannot attach
            # itself to a newer narrative.
            current = media.registry.get(game_key)
            if current is None or current.run_id != expected_run_id:
                return
            entry = next(
                (
                    item for item in reversed(current.log)
                    if log_round(item, -1) == round_number
                    and str(item.get("gm_response") or "").strip()
                ),
                None,
            )
            if entry is None or storyboard_source_revision(entry) != source_revision:
                return
            old_panels = deepcopy(entry.get("scene_panels"))
            old_meta = deepcopy(entry.get("scene_panel_meta"))
            entry["scene_panels"] = deepcopy(panels)
            draft_revision = storyboard_source_revision(entry)
            entry["scene_panel_meta"] = storyboard_panel_metadata(
                panels,
                narration=str(entry.get("gm_response") or ""),
                actions=entry.get("actions") or [],
                current_scene=current_scene or capture_storyboard_context(current)["current_scene"],
                source_revision=draft_revision,
            )
            try:
                await media.registry.save(current)
            except Exception:
                if old_panels is None:
                    entry.pop("scene_panels", None)
                else:
                    entry["scene_panels"] = old_panels
                if old_meta is None:
                    entry.pop("scene_panel_meta", None)
                else:
                    entry["scene_panel_meta"] = old_meta
                logger.exception(
                    "自动场景图分镜草稿保存失败，跳过本次生图 (game=%s round=%d)",
                    game_key,
                    round_number,
                )
                return
            # scene_panels is part of the source revision.  Use the new
            # revision for the image request and the post-request stale
            # check, otherwise a successful inference would be rejected
            # as stale as soon as it was persisted.
            source_revision = draft_revision
            logger.info(
                "自动场景图分镜草稿已保存 (game=%s round=%d panels=%d)",
                game_key,
                round_number,
                len(panels),
            )
        context: dict[str, Any] = {
            "round": round_number,
            "run_id": expected_run_id,
            "source_revision": source_revision,
            "scene": current_scene or str(module_hooks.narrative_view(current)["scene"] or ""),
            "narration": str(entry.get("gm_response") or "")[:1600],
            "actions": str(entry.get("actions") or "")[:1200],
            "panels": panels,
        }
        if panels:
            context["storyboard"] = {
                "panels": panels,
                "compressed_count": max(0, int(compressed_count or 0)),
            }
            if character_appearances:
                context["storyboard"]["character_appearances"] = dict(
                    character_appearances,
                )
        try:
            result = await media.service.generate(ImageGenerationRequest(
                prompt=prompt,
                purpose="scene",
                owner_type="game",
                owner_id=game_image_owner_id(game_key),
                aspect_ratio="16:9",
                context=context,
            ))
        except ImageGenerationError as exc:
            logger.warning(
                "自动场景图图像服务失败 (game=%s round=%d panels=%d): %s",
                game_key,
                round_number,
                len(panels),
                exc,
            )
            return
        # 重开/重置可能发生在生图 await 期间。旧任务不得写入新一局，
        # 同一局的 swipe 也可能已经删除或替换目标回合。
        current = media.registry.get(game_key)
        if current is None or current.run_id != expected_run_id:
            return
        entry = next(
            (
                item for item in reversed(current.log)
                if log_round(item, -1) == round_number
                and str(item.get("gm_response") or "").strip()
            ),
            None,
        )
        if entry is None:
            return  # 该回合已被回滚删除，放弃本次生图
        if storyboard_source_revision(entry) != source_revision:
            return  # 公开剧情已变化，旧任务不得覆盖新版本
        reference = {"kind": "generated", "asset_id": result.asset_id}
        old_scene_image = deepcopy(entry.get("scene_image"))
        old_top_scene_image = deepcopy(module_hooks.read_value(current, module_hooks.SCENE_IMAGE) or {})
        current.set_scene_image(reference)
        entry["scene_image"] = {
            "reference": reference,
            "generation_id": result.generation_id,
            "prompt": prompt,
            "revised_prompt": result.revised_prompt,
            "status": "ready",
            "swipe_index": int(entry.get("current_swipe") or 0),
        }
        if panels:
            entry["scene_image"].update({
                "layout": storyboard_layout(len(panels)),
                "panels": panels,
                "compressed_count": max(0, int(compressed_count or 0)),
            })
            entry["scene_panel_meta"] = storyboard_panel_metadata(
                panels,
                narration=str(entry.get("gm_response") or ""),
                actions=entry.get("actions") or [],
                current_scene=current_scene or str(module_hooks.narrative_view(current)["scene"] or ""),
                source_revision=source_revision,
            )
        try:
            await media.registry.save(current)
        except Exception:
            if old_scene_image is None:
                entry.pop("scene_image", None)
            else:
                entry["scene_image"] = old_scene_image
            current.set_scene_image(old_top_scene_image)
            raise
        logger.info("场景图已生成 (round=%d, asset=%s)", round_number, result.asset_id)
    except ImageGenerationError as exc:
        logger.warning("场景图生成失败 (round=%d): %s", round_number, exc)
    except Exception:
        logger.exception("场景图后台任务异常 (round=%d)", round_number)
