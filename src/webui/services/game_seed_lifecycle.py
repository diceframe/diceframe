"""Create a new game save from an existing seed reference."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.engine.module_state import ModuleStateError
from src.engine.modules import adventure_runtime_state, content_binding, progression_state, table_settings
from src.engine.narrative_perspective import validate_narrative_perspective
from src.migrations import migrate_instance
from src.rulesets.contracts import LiveAdvancementPolicyRuntime
from src.webui.services._common import _GAME_KEY_SEP
from src.webui.services import game_creation_phases
from src.webui.game_lifecycle_context import (
    CreationPhase,
    CreationTransaction,
    GameLifecycleDependencies,
    _start_created_game,
)

logger = logging.getLogger("trpg")


def _instance_rule_id(dependencies: GameLifecycleDependencies, inst: Any) -> str:
    """Resolve and explicitly attach a rule ID in state-changing workflows."""

    rule_id = dependencies.project_rule_id(inst)
    inst.rule_id = rule_id
    return rule_id


async def create_from_seed(
    dependencies: GameLifecycleDependencies,
    seed_code: str,
    solo: bool = False,
    players: list[dict] | None = None,
    gm_uid: str = "",
    language: str = "",
    scene_image: dict[str, Any] | None = None,
    narrative_perspective: str = "",
) -> dict[str, Any]:
    if not dependencies.handler or not dependencies.registry:
        return {"ok": False, "error": "系统未就绪"}
    if config_error := dependencies.llm_configuration_error(language):
        return config_error
    target_inst = None
    for inst in dependencies.registry.list_all():
        if table_settings.seed_code(inst) == seed_code:
            target_inst = inst
            break
    if not target_inst:
        return {
            "ok": False,
            "error": f"未找到重开引用码 '{seed_code}' 对应的游戏，请确认原存档仍然存在",
        }
    if not players:
        return {"ok": False, "error": "请至少创建或选择 1 名队伍角色"}
    world_id = target_inst.world_id or "default_fantasy"
    world_name = target_inst.world_name
    resolved_language = normalize_language(
        language or getattr(target_inst, "language", DEFAULT_LANGUAGE)
    )
    rule_id = _instance_rule_id(dependencies, target_inst)
    resolved_narrative_perspective = (
        narrative_perspective
        or table_settings.narrative_perspective(target_inst)
        or "auto"
    )
    try:
        normalized_narrative_perspective = validate_narrative_perspective(
            resolved_narrative_perspective,
        )
    except ValueError:
        return {"ok": False, "error": "叙事视角设置无效"}

    # A seed restart is a new save, but it must keep the original save's rule
    # identity.  Professional sheets receive the same all-or-nothing preflight
    # as the normal create path before the new instance is registered.
    try:
        selected_rule = dependencies.load_rule_by_id(rule_id, resolved_language)
        runtime = (
            dependencies.rulesets.resolve(selected_rule.template)
            if selected_rule is not None
            else None
        )
        target_adventure_binding = dict(
            getattr(target_inst, "adventure_binding", {}) or {}
        )
        if target_adventure_binding:
            current_binding = dependencies.resolve_adventure_binding(
                str(target_adventure_binding.get("adventure_id") or ""),
                runtime,
                world_id,
                resolved_language,
            )
            migrated = migrate_instance(target_inst, adventure_expected=current_binding)
            if migrated is None:
                raise ValueError("bound adventure package is missing or has changed")
            if migrated:
                await dependencies.registry.save(target_inst)
            target_adventure_binding = dict(target_inst.adventure_binding)
        if runtime and runtime.capabilities.character_builder == "professional":
            players = [
                runtime.normalize_character_submission(
                    selected_rule,
                    character,
                    resolved_language,
                )
                for character in players
            ]
    except ValueError as exc:
        if dict(getattr(target_inst, "adventure_binding", {}) or {}):
            return {
                "ok": False,
                "error_code": "INCOMPATIBLE_ADVENTURE",
                "error": str(exc),
            }
        return {
            "ok": False,
            "error_code": "INVALID_PROFESSIONAL_CHARACTER",
            "error": str(exc),
        }
    try:
        selected_scene_image = dependencies.materialize_scene_image(
            scene_image
            if scene_image
            else (
                getattr(target_inst, "scene_image", None)
                or dependencies.resolve_default_scene_image(world_id, rule_id)
            )
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    # Read the original save's source-aware World ref before registering the
    # new instance, so an unreadable slot cannot leave a half-created save.
    try:
        inherited_world_ref = content_binding.world_ref(target_inst) or {
            "source_kind": "world",
            "source_id": world_id,
            "kind": "world",
            "id": world_id,
            "digest": "",
        }
    except ModuleStateError as exc:
        return {"ok": False, "error_code": "INVALID_WORLD_REF", "error": str(exc)}
    # Same for the original save's play mode: an unsupported adventure_runtime
    # slot is rejected before anything is registered.
    adventure_runtime_state.require_writable(target_inst)
    source_play_mode = str(adventure_runtime_state.play_mode(target_inst) or "")

    unique_id = f"{world_id}_{time.time_ns()}"
    game_key = ("web", unique_id, "web_bot")
    transaction = CreationTransaction(dependencies, game_key, world_id)

    try:
        instance = await dependencies.handler.create_game(
            game_key,
            world_id=world_id,
            world_name=world_name,
            group_name="Web端",
            seed_code=seed_code,
            difficulty=table_settings.difficulty(target_inst),
            rule_id=rule_id,
            language=resolved_language,
        )
    except Exception:
        transaction.rollback()
        logger.exception("按引用码创建游戏实例失败: %s", game_key)
        return {
            "ok": False,
            "error_code": "GAME_CREATE_FAILED",
            "error": "重开失败，未留下半成品存档，请重试。",
        }
    transaction.advance(CreationPhase.INSTANCE_REGISTERED)
    binding_error = game_creation_phases.bind_ruleset_runtime(
        transaction, instance, runtime, selected_rule, resolved_language,
    )
    if binding_error is not None:
        return binding_error
    instance.configure_session(
        solo_mode=solo,
        narrative_perspective=normalized_narrative_perspective,
    )
    if isinstance(runtime, LiveAdvancementPolicyRuntime):
        inherited_policy = runtime.live_advancement_policy(target_inst)
        runtime.configure_live_advancement(
            instance,
            str(inherited_policy.get("mode") or "milestone"),
            str(inherited_policy.get("authority") or "ai_gm"),
        )
    if not instance.bind_adventure(target_adventure_binding):
        transaction.rollback()
        return {
            "ok": False,
            "error_code": "INVALID_ADVENTURE_BINDING",
            "error": "原存档的冒险绑定无效，未留下半成品存档。",
        }
    content_error = game_creation_phases.bind_content_refs(
        transaction,
        instance,
        inherited_world_ref,
    )
    if content_error is not None:
        return content_error
    # A seed restart is the same kind of game: keep its play mode (old saves
    # without one derive it from the binding, as the save migration does) and
    # run the same Adventure v2 initialization step as normal creation.
    adventure_runtime_state.replace_play_mode(instance, source_play_mode or (
        "adventure" if target_adventure_binding.get("adventure_id") else "free"
    ))
    if callable(getattr(dependencies, "initialize_adventure_run", None)):
        try:
            dependencies.initialize_adventure_run(instance)
        except Exception as exc:
            transaction.rollback()
            logger.exception("按引用码初始化冒险运行时失败，已回滚: %s", game_key)
            return {
                "ok": False,
                "error_code": "ADVENTURE_RUNTIME_INIT_FAILED",
                "error": f"冒险初始化失败，未留下半成品存档：{exc}",
            }
    instance.set_scene_image(selected_scene_image)
    instance.set_map_background(dict(getattr(target_inst, "map_background", {}) or {}))
    transaction.advance(CreationPhase.INSTANCE_CONFIGURED)
    created_players, player_error = await game_creation_phases.create_players(
        dependencies,
        transaction,
        list(players or []),
        gm_uid,
        exception_error="重开角色创建失败，未留下半成品存档，请重试。",
        log_context="按引用码创建角色失败，已回滚",
    )
    if player_error is not None:
        return player_error
    transaction.advance(CreationPhase.PLAYERS_CREATED)
    try:
        narration = await _start_created_game(dependencies, instance, runtime)
    except Exception:
        transaction.rollback()
        logger.exception("按引用码生成开场失败，已回滚: %s", game_key)
        return {
            "ok": False,
            "error_code": "GAME_CREATE_FAILED",
            "error": "重开生成开场失败，未留下半成品存档，请检查模型设置后重试。",
        }
    transaction.advance(CreationPhase.OPENING_STARTED)
    world_name = instance.world_name

    # 与 create_game 一致：首个成功创建的角色拥有 GM 身份。
    instance.configure_session(
        gm_uid=created_players[0]["user_id"] if created_players else ""
    )
    try:
        await transaction.commit(instance)
    except Exception:
        transaction.rollback()
        logger.exception("保存重开游戏失败，已回滚: %s", game_key)
        return {
            "ok": False,
            "error_code": "GAME_CREATE_FAILED",
            "error": "保存重开游戏失败，未留下半成品存档，请重试。",
        }

    return {
        "ok": True,
        "game_key": _GAME_KEY_SEP.join(game_key),
        "world_id": instance.world_id,
        "world_name": world_name,
        "language": normalize_language(instance.language),
        "narration": narration,
        "players": created_players,
        "seed_code": seed_code,
        "round_number": progression_state.round_value(instance),
        "state": instance.state.value,
        "adventure_binding": dict(instance.adventure_binding),
    }
