"""Game creation, reset, restart, and deletion transactions."""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import time
from typing import Any

from src.commands.game_lifecycle import RunInitializationError
from src.engine.game_instance import GameState
from src.engine.modules import adventure_runtime_state, content_binding, progression_state, room_access, table_settings
from src.content_modules.refs import ContentRef, ContentRefError, parse_content_ref
from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.engine.narrative_perspective import validate_narrative_perspective
from src.content.gm_style import normalize_gm_style_override
from src.rulesets.contracts import LiveAdvancementPolicyRuntime

from src.webui.services._common import _GAME_KEY_SEP, _is_safe_world_id
from src.webui.services import game_creation_phases, game_seed_lifecycle
from src.webui.game_lifecycle_context import (
    CreationPhase,
    CreationTransaction,
    GameLifecycleDependencies,
    _start_created_game,
)

logger = logging.getLogger("trpg")


def _saved_world_id(
    dependencies: GameLifecycleDependencies, game_key: tuple[str, ...]
) -> str:
    save_path = dependencies.registry.save_package_state_path(game_key)
    for path in (save_path, save_path.with_name("state.backup.json")):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("读取待删除存档的世界 ID 失败: %s", path, exc_info=True)
            continue
        return str(data.get("world_id") or "")
    return ""


def delete_game(
    dependencies: GameLifecycleDependencies, game_key: str
) -> dict[str, Any]:
    """Delete one save and release its game-scoped template when no save uses it."""
    parsed_key = dependencies.parse_game_key(game_key)
    instance = dependencies.registry.get(parsed_key)
    save_dir = dependencies.registry.save_package_state_path(parsed_key).parent
    if not instance and not save_dir.exists():
        return {"ok": False, "error": "存档目录不存在"}
    world_id = str(getattr(instance, "world_id", "") or "") or _saved_world_id(
        dependencies, parsed_key
    )
    game_scope = _GAME_KEY_SEP.join(str(part) for part in parsed_key)
    lorebook = getattr(dependencies, "lorebook", None)
    if lorebook is None and dependencies.handler is not None:
        lorebook = getattr(dependencies.handler, "lorebook", None)
    # Game bindings are owned by the save lifecycle.  Remove only the exact
    # game scope; world/global bindings remain user content and survive.
    if lorebook is not None and hasattr(lorebook, "list_bindings"):
        for binding in list(lorebook.list_bindings(scope_kind="game", scope_id=game_scope)):
            try:
                lorebook.delete_binding(str(binding.get("id") or ""))
            except Exception:
                logger.warning("删除游戏内容绑定失败: %s", binding.get("id"), exc_info=True)
    try:
        shutil.rmtree(save_dir)
    except Exception as exc:
        logger.warning("删除存档目录失败: %s", save_dir, exc_info=True)
        return {"ok": False, "error": f"删除存档目录失败: {exc}"}
    dependencies.registry.remove(parsed_key)
    removed_templates = (
        dependencies.cleanup_orphan_game_templates(world_id) if world_id else 0
    )
    return {
        "ok": True,
        "world_template_removed": bool(removed_templates),
    }


async def create_game(
    dependencies: GameLifecycleDependencies,
    world_id: str,
    game_name: str = "",
    group_name: str = "Web端",
    rule_id: str = "",
    solo: bool = False,
    lorebook_world_id: str = "",
    difficulty: str = "标准",
    description: str = "",
    create_lorebook: bool = False,
    blank_lorebook: bool = False,
    source_world_id: str = "",
    players: list[dict] | None = None,
    custom_world: bool = False,
    gm_uid: str = "",
    room_password: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    scene_image: dict[str, Any] | None = None,
    map_background: dict[str, Any] | None = None,
    adventure_id: str = "",
    adventure_source_kind: str = "",
    adventure_source_id: str = "",
    play_mode: str = "",
    narrative_perspective: str = "auto",
    gm_style_override: dict[str, Any] | None = None,
    advancement_mode: str = "milestone",
    advancement_authority: str = "ai_gm",
    unclaimed_control_default: str = "",
    world_ref: dict[str, Any] | None = None,
    book_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not dependencies.handler or not dependencies.registry:
        return {"ok": False, "error": "系统未就绪"}
    if config_error := dependencies.llm_configuration_error(language):
        return config_error
    parsed_world_ref: ContentRef | None = None
    if world_ref is not None:
        try:
            parsed_world_ref = parse_content_ref(
                world_ref,
                default_source=f"world:{world_id}",
            )
            if parsed_world_ref.kind != "world":
                raise ContentRefError("world_ref must reference kind=world")
            if str(world_id or "") and str(world_id) != parsed_world_ref.id:
                raise ContentRefError("world_id disagrees with world_ref")
            world_id = parsed_world_ref.id
        except ContentRefError as exc:
            return {"ok": False, "error_code": "INVALID_WORLD_REF", "error": str(exc)}
    if not _is_safe_world_id(world_id):
        return {"ok": False, "error": "非法 world_id"}
    if source_world_id and not _is_safe_world_id(source_world_id):
        return {"ok": False, "error": "非法 source_world_id"}
    normalized_book_bindings: list[dict[str, Any]] = []
    if book_bindings is not None:
        if not isinstance(book_bindings, list):
            return {"ok": False, "error_code": "INVALID_BOOK_BINDINGS", "error": "book_bindings must be a list"}
        try:
            for raw_binding in book_bindings:
                if not isinstance(raw_binding, dict):
                    raise ContentRefError("book binding must be an object")
                raw_ref = raw_binding.get("ref", raw_binding)
                ref = parse_content_ref(raw_ref, default_source=f"world:{world_id}")
                if ref.kind != "lorebook":
                    raise ContentRefError("book binding must reference kind=lorebook")
                normalized_book_bindings.append({
                    "ref": ref,
                    "role": str(raw_binding.get("role") or "runtime"),
                    "order": int(raw_binding.get("order", 110)),
                })
        except (ContentRefError, TypeError, ValueError) as exc:
            return {"ok": False, "error_code": "INVALID_BOOK_BINDINGS", "error": str(exc)}
        if normalized_book_bindings and not lorebook_world_id:
            first_ref = normalized_book_bindings[0]["ref"]
            if first_ref.source_kind == "world":
                lorebook_world_id = first_ref.source_id
    if not players:
        return {"ok": False, "error": "请至少创建或选择 1 名队伍角色"}
    try:
        normalized_narrative_perspective = validate_narrative_perspective(
            narrative_perspective
        )
        normalized_gm_style = normalize_gm_style_override(gm_style_override)
    except ValueError:
        return {"ok": False, "error": "叙事设置无效"}
    normalized_play_mode = str(play_mode or "").strip().casefold()
    if not normalized_play_mode:
        normalized_play_mode = "adventure" if str(adventure_id or "").strip() else "free"
    if normalized_play_mode not in {"free", "adventure"}:
        return {"ok": False, "error": "玩法模式无效"}
    if normalized_play_mode == "adventure" and not str(adventure_id or "").strip():
        return {"ok": False, "error": "冒险包剧情模式必须选择冒险包"}
    if normalized_play_mode == "free" and str(adventure_id or "").strip():
        return {"ok": False, "error": "标准自由对局不能绑定冒险包"}

    try:
        default_scene_image = dependencies.resolve_default_scene_image(
            source_world_id or world_id, rule_id
        )
        selected_scene_image = dependencies.materialize_scene_image(
            scene_image if scene_image else default_scene_image
        )
        selected_map_background = dependencies.validate_map_background(map_background)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    unique_id = f"{world_id}_{time.time_ns()}"
    game_key = ("web", unique_id, "web_bot")

    instance = dependencies.registry.get(game_key)
    if instance and instance.state not in (GameState.CREATED, GameState.ENDED):
        return {"ok": False, "error": "该世界已有进行中的游戏"}
    transaction = CreationTransaction(dependencies, game_key, world_id)
    resolved_world_name = game_name or world_id
    resolved_language = normalize_language(language)

    # Reject an invalid password before creating a registry entry or a
    # game-scoped world template, so validation cannot leave a phantom game.
    generated_password: str | None = None
    if room_password is None:
        if not solo:
            generated_password = secrets.token_urlsafe(6)
            room_password = generated_password
    elif room_password == "":
        room_password = ""
    else:
        room_password = str(room_password)
        try:
            room_access.validate_new_room_password(room_password)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    # Professional sheets are validated as a complete batch before any game,
    # player, card, or save mutation. create_player repeats normalization at the
    # final storage boundary so callers cannot bypass this preflight.
    try:
        selected_rule = dependencies.load_rule_by_id(rule_id, resolved_language)
        runtime = (
            dependencies.rulesets.resolve(selected_rule.template)
            if selected_rule is not None
            else None
        )
        adventure_binding = dependencies.resolve_adventure_binding(
            adventure_id,
            runtime,
            world_id,
            resolved_language,
            str(adventure_source_kind or ""),
            str(adventure_source_id or ""),
        )
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
        error_code = (
            "INCOMPATIBLE_ADVENTURE"
            if str(adventure_id or "").strip()
            else "INVALID_PROFESSIONAL_CHARACTER"
        )
        return {
            "ok": False,
            "error_code": error_code,
            "error": str(exc),
        }

    game_creation_phases.materialize_world(
        dependencies,
        transaction,
        custom_world=custom_world,
        create_lorebook=create_lorebook,
        blank_lorebook=blank_lorebook,
        source_world_id=source_world_id,
        world_id=world_id,
        world_name=resolved_world_name,
        description=description,
        language=resolved_language,
        rule_id=rule_id,
        difficulty=difficulty,
        scene_image=scene_image,
        default_scene_image=default_scene_image,
    )
    transaction.advance(CreationPhase.WORLD_MATERIALIZED)

    try:
        instance = await dependencies.handler.create_game(
            game_key,
            world_id=world_id,
            world_name=resolved_world_name,
            group_name=group_name,
            rule_id=rule_id,
            language=resolved_language,
        )
    except Exception:
        transaction.rollback()
        logger.exception("创建游戏实例失败: %s", game_key)
        return {
            "ok": False,
            "error_code": "GAME_CREATE_FAILED",
            "error": "创建游戏失败，未留下半成品存档，请重试。",
        }
    transaction.advance(CreationPhase.INSTANCE_REGISTERED)
    binding_error = game_creation_phases.bind_ruleset_runtime(
        transaction, instance, runtime, selected_rule, resolved_language,
    )
    if binding_error is not None:
        return binding_error
    instance.set_difficulty(difficulty)
    if not instance.bind_adventure(adventure_binding):
        transaction.rollback()
        return {
            "ok": False,
            "error_code": "INVALID_ADVENTURE_BINDING",
            "error": "冒险包绑定无效，未留下半成品存档。",
        }
    content_error = game_creation_phases.bind_content_refs(
        transaction,
        instance,
        parsed_world_ref.to_portable_dict() if parsed_world_ref is not None else {
            "source_kind": "world",
            "source_id": world_id,
            "kind": "world",
            "id": world_id,
            "digest": "",
        },
    )
    if content_error is not None:
        return content_error
    adventure_runtime_state.replace_play_mode(instance, normalized_play_mode)
    # FIX-04 §6.5/§6.6：v2 冒险在同一创建事务里初始化进度并原子物化世界种子；
    # 失败即整体回滚（不留下 partial save / partial world）。
    if callable(getattr(dependencies, "initialize_adventure_run", None)):
        try:
            dependencies.initialize_adventure_run(instance)
        except Exception as exc:
            transaction.rollback()
            logger.exception("初始化冒险运行时失败，已回滚: %s", game_key)
            return {
                "ok": False,
                "error_code": "ADVENTURE_RUNTIME_INIT_FAILED",
                "error": f"冒险初始化失败，未留下半成品存档：{exc}",
            }
    instance.set_scene_image(selected_scene_image)
    instance.set_map_background(selected_map_background)
    # 房间密码三态：字段缺失(None) 且 多人局 → 生成随机密码回显（安全默认，
    # 防止 GM 以为设了密码实际开放）；显式空串 "" → 明确开放；非空 → 加密并校验长度。
    instance.configure_session(
        solo_mode=solo,
        entry_point="web",
        room_password=room_password or "",
        narrative_perspective=normalized_narrative_perspective,
    )
    instance.set_gm_style_override(normalized_gm_style)
    if isinstance(runtime, LiveAdvancementPolicyRuntime):
        try:
            runtime.configure_live_advancement(
                instance,
                advancement_mode,
                advancement_authority,
            )
        except ValueError as exc:
            transaction.rollback()
            return {
                "ok": False,
                "error_code": "INVALID_ADVANCEMENT_POLICY",
                "error": str(exc),
            }
    transaction.advance(CreationPhase.INSTANCE_CONFIGURED)

    try:
        lorebook_binding_id = game_creation_phases.copy_lorebook_entries(
            dependencies,
            source_world_id=lorebook_world_id if book_bindings is None else "",
            world_id=world_id,
            world_name=resolved_world_name,
            language=resolved_language,
            game_key=game_key,
            canonical=book_bindings is not None,
        )
        if lorebook_binding_id:
            transaction.track_binding(lorebook_binding_id)
            source_book_id = (
                dependencies.lorebook.primary_world_book_id(lorebook_world_id)
                if dependencies.lorebook is not None
                and hasattr(dependencies.lorebook, "primary_world_book_id")
                else f"world:{lorebook_world_id}"
            )
            content_binding.add_book_ref(instance, {
                "source_kind": "world",
                "source_id": lorebook_world_id,
                "kind": "lorebook",
                "id": source_book_id,
                "digest": "",
            })
        if normalized_book_bindings:
            store = dependencies.lorebook
            if store is None or not hasattr(store, "bind_lorebook"):
                transaction.rollback()
                return {"ok": False, "error_code": "BOOK_BINDING_UNAVAILABLE", "error": "canonical lorebook store is unavailable"}
            game_scope = _GAME_KEY_SEP.join(str(part) for part in game_key)
            for item in normalized_book_bindings:
                ref = item["ref"]
                book_id = (
                    store.primary_world_book_id(ref.source_id)
                    if ref.source_kind == "world" and hasattr(store, "primary_world_book_id")
                    else ref.id
                )
                if not store.get_lorebook(book_id):
                    transaction.rollback()
                    return {"ok": False, "error_code": "BOOK_NOT_FOUND", "error": book_id}
                binding_id = f"binding:game:{game_scope}:lorebook:{book_id}"
                store.bind_lorebook({
                    "id": binding_id,
                    "book_id": book_id,
                    "scope_kind": "game",
                    "scope_id": game_scope,
                    "role": item["role"],
                    "order": item["order"],
                })
                transaction.track_binding(binding_id)
                content_binding.add_book_ref(instance, {
                    "source_kind": ref.source_kind,
                    "source_id": ref.source_id,
                    "kind": "lorebook",
                    "id": book_id,
                    "digest": ref.digest,
                })
    except Exception:
        # Book bindings live outside the save directory; any failure here must
        # still compensate them (tracked bindings) and the registered instance.
        transaction.rollback()
        logger.exception("创建游戏内容绑定失败，已回滚: %s", game_key)
        return {
            "ok": False,
            "error_code": "BOOK_BINDING_FAILED",
            "error": "世界书绑定失败，未留下半成品存档。",
        }
    created_players, player_error = await game_creation_phases.create_players(
        dependencies,
        transaction,
        list(players or []),
        gm_uid,
        exception_error="创建角色失败，未留下半成品存档，请重试。",
        log_context="创建游戏角色失败，已回滚",
        unclaimed_control_default=unclaimed_control_default,
    )
    if player_error is not None:
        return player_error
    transaction.advance(CreationPhase.PLAYERS_CREATED)

    try:
        narration = await _start_created_game(dependencies, instance, runtime)
    except Exception:
        transaction.rollback()
        logger.exception("生成游戏开场失败，已回滚: %s", game_key)
        return {
            "ok": False,
            "error_code": "GAME_CREATE_FAILED",
            "error": "生成开场失败，未留下半成品存档，请检查模型设置后重试。",
        }
    transaction.advance(CreationPhase.OPENING_STARTED)
    world_name = instance.world_name

    # GM 严格绑定成功创建的第一个角色；没有角色就没有 GM。
    instance.configure_session(
        gm_uid=created_players[0]["user_id"] if created_players else ""
    )
    try:
        await transaction.commit(instance)
    except Exception:
        transaction.rollback()
        logger.exception("保存新游戏失败，已回滚: %s", game_key)
        return {
            "ok": False,
            "error_code": "GAME_CREATE_FAILED",
            "error": "保存新游戏失败，未留下半成品存档，请重试。",
        }

    return {
        "ok": True,
        "game_key": _GAME_KEY_SEP.join(game_key),
        "world_id": instance.world_id,
        "world_name": world_name,
        "generated_password": generated_password,
        "language": normalize_language(instance.language),
        "narration": narration,
        "players": created_players,
        "round_number": progression_state.round_value(instance),
        "state": instance.state.value,
        "seed_code": table_settings.seed_code(instance),
        "adventure_binding": dict(instance.adventure_binding),
    }


def _run_init_failure(exc: RunInitializationError) -> dict[str, Any]:
    label = "冒险" if exc.code == "ADVENTURE_RUNTIME_INIT_FAILED" else "规则运行时"
    return {
        "ok": False,
        "error_code": exc.code,
        "error": f"{label}初始化失败，原对局保持不变：{exc}",
    }


async def reset_game(
    dependencies: GameLifecycleDependencies, game_key: str
) -> dict[str, Any]:
    inst = dependencies.registry.get(dependencies.parse_game_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if not dependencies.handler:
        return {"ok": False, "error": "系统未就绪"}
    try:
        inst = await dependencies.handler.reset_game(inst)
    except RunInitializationError as exc:
        # Raised before the registry swap: the previous run stays current.
        logger.warning("新 run 初始化失败，已保留原对局: %s", exc, exc_info=True)
        return _run_init_failure(exc)
    return {
        "ok": True,
        "narration": dependencies.clean_public_narration(
            inst.log[-1].get("gm_response", "") if inst.log else ""
        ),
        "seed_code": table_settings.seed_code(inst),
    }


async def restart_game(
    dependencies: GameLifecycleDependencies, game_key: str
) -> dict[str, Any]:
    """重开世界：保留角色卡，重置剧情/场景。"""
    inst = dependencies.registry.get(dependencies.parse_game_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if not dependencies.handler:
        return {"ok": False, "error": "系统未就绪"}
    if not inst.players:
        return {
            "ok": False,
            "error": "当前游戏没有角色，无法重开；请先创建角色或重新开局",
        }
    try:
        inst = await dependencies.handler.restart_game(inst)
    except RunInitializationError as exc:
        # Raised before the registry swap: the previous run stays current.
        logger.warning("新 run 初始化失败，已保留原对局: %s", exc, exc_info=True)
        return _run_init_failure(exc)
    return {
        "ok": True,
        "narration": dependencies.clean_public_narration(
            inst.log[-1].get("gm_response", "") if inst.log else ""
        ),
        "seed_code": table_settings.seed_code(inst),
    }


class GameLifecycleService:
    """Lifecycle transaction facade bound to one explicit dependency set."""

    def __init__(self, dependencies: GameLifecycleDependencies) -> None:
        self._dependencies = dependencies

    def delete_game(self, game_key: str) -> dict[str, Any]:
        return delete_game(self._dependencies, game_key)

    async def create_game(self, **request: Any) -> dict[str, Any]:
        return await create_game(self._dependencies, **request)

    async def reset_game(self, game_key: str) -> dict[str, Any]:
        return await reset_game(self._dependencies, game_key)

    async def restart_game(self, game_key: str) -> dict[str, Any]:
        return await restart_game(self._dependencies, game_key)

    async def create_from_seed(self, **request: Any) -> dict[str, Any]:
        return await game_seed_lifecycle.create_from_seed(self._dependencies, **request)
