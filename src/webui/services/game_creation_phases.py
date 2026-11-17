"""Reusable mutation phases for game creation transactions."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.content_modules.refs import ContentRefError
from src.engine.modules import content_binding, room_access
from src.engine.player_control import (
    CONTROL_MODES,
    DEFAULT_CONTROL_MODE,
    GM_SEAT_CONTROL_MODES,
    PlayerControlError,
    get_control,
    set_control,
)
from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import RulesetRuntime, VersionedStateRuntime
from src.webui.services._common import _GAME_KEY_SEP
from src.webui.game_lifecycle_context import (
    CreationTransaction,
    GameLifecycleDependencies,
)
from src.content_modules.projection import entries_for_world
from src.localization.messages import t

logger = logging.getLogger("trpg")


def bind_ruleset_runtime(
    transaction: CreationTransaction,
    instance: Any,
    runtime: RulesetRuntime | None,
    rule: RuleSystem | None,
    language: str,
) -> dict[str, Any] | None:
    """Bind versioned state before configuration; compensate on failure."""

    if runtime is None or not runtime.capabilities.versioned_state:
        return None
    try:
        if not isinstance(runtime, VersionedStateRuntime):
            raise ValueError(t("webui.services.game_creation_phases.runtime_no_versioned_binding"))
        binding = runtime.game_binding(rule, language)
        # The instance only validates the binding's shape, so prove it names
        # the runtime that was actually resolved for this rule.
        if str(binding.get("runtime_id") or "") != runtime.runtime_id:
            raise ValueError(t("webui.services.game_creation_phases.runtime_binding_identity_mismatch"))
        if not instance.bind_ruleset_runtime(binding):
            raise ValueError(t("webui.services.game_creation_phases.runtime_binding_invalid"))
    except Exception:
        transaction.rollback()
        logger.exception("创建游戏规则运行时绑定失败，已回滚: %s", transaction.game_key)
        return {
            "ok": False,
            "error_code": "RULESET_BINDING_FAILED",
            "error": t("webui.services.game_creation_phases.runtime_binding_failed"),
        }
    return None


def bind_content_refs(
    transaction: CreationTransaction,
    instance: Any,
    world_ref: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist the source-aware World ref; compensate on failure.

    Shared by normal and seed creation so both persist the same binding
    semantics.  ``world_ref`` keeps the caller's source identity and digest.
    Adventure identity is owned by ``GameInstance.adventure_binding`` alone.
    """

    try:
        content_binding.set_world_ref(instance, world_ref)
    except ContentRefError as exc:
        transaction.rollback()
        return {"ok": False, "error_code": "INVALID_WORLD_REF", "error": str(exc)}
    return None


def materialize_world(
    dependencies: GameLifecycleDependencies,
    transaction: CreationTransaction,
    *,
    custom_world: bool,
    create_lorebook: bool,
    blank_lorebook: bool,
    source_world_id: str,
    world_id: str,
    world_name: str,
    description: str,
    language: str,
    rule_id: str,
    difficulty: str,
    scene_image: dict[str, Any] | None,
    default_scene_image: dict[str, str],
    default_scene_images: list[dict[str, str]] | None = None,
) -> None:
    """Materialize optional lorebook/template state before instance creation."""

    if not (custom_world or create_lorebook):
        return
    lorebook = dependencies.lorebook
    if lorebook and not lorebook.get_world(world_id):
        lorebook.create_world(
            world_id,
            world_name,
            description=description or "",
            language=language,
        )
        logger.info("已创建自定义世界书: %s", world_id)
    if not dependencies.worlds_dir:
        return
    template_path = dependencies.worlds_dir / f"{world_id}.json"
    if template_path.exists():
        return

    resolved_rule = rule_id or "freeform_fantasy"
    base_template: dict[str, Any] = {}
    if blank_lorebook and source_world_id:
        source_path = dependencies.worlds_dir / f"{source_world_id}.json"
        if source_path.exists():
            try:
                base_template = json.loads(source_path.read_text(encoding="utf-8"))
                resolved_rule = rule_id or base_template.get(
                    "default_rule",
                    resolved_rule,
                )
            except Exception:
                logger.exception("读取空白世界书来源模板失败: %s", source_world_id)

    categories: dict[str, list[str]] = base_template.get("item_categories", {})
    try:
        rule_path = RuleSystem.path_for(dependencies.rules_dir, resolved_rule)
        if not categories and rule_path.exists():
            categories = RuleSystem.load(rule_path).item_categories
    except Exception:
        logger.exception("读取规则 item_categories 失败: %s", resolved_rule)

    resolved_description = description or base_template.get("description", "")
    template = {
        "world_id": world_id,
        "world_name": world_name,
        "custom": True,
        "description": resolved_description,
        "world_setting": description
        or base_template.get("world_setting", resolved_description),
        "starter_scene": base_template.get(
            "starter_scene",
            description[:120] if description else "",
        ),
        "suggested_difficulty": difficulty,
        "language": language,
        "default_rule": resolved_rule,
        "starter_lorebook": [],
    }
    pool = list(default_scene_images or [])
    # The create page may send the concrete built-in member it previewed so
    # the first save is WYSIWYG.  Keep the full default pool on a newly
    # materialized custom world in that case; uploaded/generated references
    # remain explicit single-cover overrides.
    builtin_default_selection = (
        isinstance(scene_image, dict)
        and scene_image.get("kind") == "builtin"
        and len(pool) > 1
    )
    if not scene_image or builtin_default_selection:
        if len(pool) > 1:
            template["scene_images"] = [
                dependencies.materialize_scene_image(reference)
                for reference in pool
            ]
            template["scene_image"] = (
                dependencies.materialize_scene_image(scene_image)
                if builtin_default_selection
                else template["scene_images"][0]
            )
        else:
            template["scene_image"] = dependencies.materialize_scene_image(
                scene_image if builtin_default_selection else default_scene_image
            )
    if create_lorebook and not custom_world:
        template["_diceframe_managed"] = "game"
        template["_diceframe_owner_game"] = _GAME_KEY_SEP.join(
            transaction.game_key,
        )
    if categories:
        template["item_categories"] = categories
    dependencies.worlds_dir.mkdir(parents=True, exist_ok=True)
    template_path.write_text(
        json.dumps(template, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("已写入自定义世界模板: %s (rule=%s)", template_path, resolved_rule)


def copy_lorebook_entries(
    dependencies: GameLifecycleDependencies,
    *,
    source_world_id: str,
    world_id: str,
    world_name: str,
    language: str,
    game_key: tuple[str, ...] | None = None,
    canonical: bool = False,
) -> str | None:
    """Bind a requested external Book to the new game; legacy stores still copy."""

    lorebook = dependencies.lorebook
    if not source_world_id or source_world_id == world_id or lorebook is None:
        return None
    bind_lorebook = getattr(lorebook, "bind_lorebook", None)
    if callable(bind_lorebook) and game_key and canonical:
        source_book_id = (
            lorebook.primary_world_book_id(source_world_id)
            if hasattr(lorebook, "primary_world_book_id")
            else f"world:{source_world_id}"
        )
        if not lorebook.get_lorebook(source_book_id):
            return None
        scope_id = _GAME_KEY_SEP.join(str(part) for part in game_key)
        binding_id = f"binding:game:{scope_id}:lorebook:{source_book_id}"
        bind_lorebook({
            "id": binding_id,
            "book_id": source_book_id,
            "scope_kind": "game",
            "scope_id": scope_id,
            "role": "runtime",
            "order": 110,
        })
        return binding_id
    entries = entries_for_world(lorebook, source_world_id)
    if not entries:
        return None
    if not lorebook.get_world(world_id):
        source_world = lorebook.get_world(source_world_id) or {}
        lorebook.create_world(
            world_id,
            world_name,
            description=f"来自 {source_world_id}",
            language=source_world.get("language", language),
        )
    for entry in entries:
        copied = dict(entry)
        copied["id"] = f"{world_id}_{entry['id']}"
        copied["world_id"] = world_id
        existing = lorebook.get_entry(copied["id"])
        if existing and existing.get("world_id") == world_id:
            continue
        lorebook.add_entry(copied)
    dependencies.refresh_lorebook_index(world_id)
    return None


async def create_players(
    dependencies: GameLifecycleDependencies,
    transaction: CreationTransaction,
    players: list[dict],
    gm_uid: str,
    *,
    exception_error: str,
    log_context: str,
    unclaimed_control_default: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Create the party as one compensated phase and bind its first GM seat."""

    first = players[0] if players and isinstance(players[0], dict) else None
    gm_seat_control = (
        _requested_control(first, unclaimed_control_default) if first is not None else ""
    )
    if gm_seat_control in CONTROL_MODES and gm_seat_control not in GM_SEAT_CONTROL_MODES:
        # 第一张卡就是 GM 席位：它绑定房主会话、永远拿不到分享凭证，没有人能认领。
        # 建成「等待认领」只会得到一个连房主都提交不了行动的死席位，所以在创建任何
        # 角色之前就拒绝，而不是悄悄改成别的控制方式。
        transaction.rollback()
        return [], {
            "ok": False,
            "error_code": "GM_SEAT_UNCLAIMED",
            "error": t("webui.services.game_creation_phases.first_seat_cannot_be_unclaimed"),
        }
    created_players: list[dict[str, Any]] = []
    public_key = _GAME_KEY_SEP.join(transaction.game_key)
    for index, character in enumerate(players):
        try:
            if index == 0 and gm_uid:
                created = await dependencies.create_player(
                    public_key,
                    character,
                    force_uid=gm_uid,
                )
            else:
                created = await dependencies.create_player(
                    public_key,
                    character,
                    assign_new_id=True,
                )
        except Exception:
            transaction.rollback()
            logger.exception("%s: %s", log_context, transaction.game_key)
            return [], {
                "ok": False,
                "error_code": "GAME_CREATE_FAILED",
                "error": exception_error,
            }
        if not created.get("ok"):
            transaction.rollback()
            return [], {
                "ok": False,
                "error": f"创建角色失败: {created.get('error', '未知错误')}",
            }
        requested = _requested_control(character, unclaimed_control_default)
        instance = dependencies.registry.get(transaction.game_key)
        uid = str(created.get("user_id") or "")
        if instance is None or uid not in getattr(instance, "players", {}):
            transaction.rollback()
            return [], {
                "ok": False,
                "error_code": "GAME_CREATE_FAILED",
                "error": exception_error,
            }
        if requested != DEFAULT_CONTROL_MODE:
            try:
                # 开房时的控制方式同样走唯一写入口；任何未在权威契约里登记的模式
                # 都在这里 fail closed，而不是把角色悄悄建成默认值。
                set_control(instance, uid, requested)
            except PlayerControlError as exc:
                transaction.rollback()
                return [], {
                    "ok": False,
                    "error_code": "INVALID_PLAYER_CONTROL",
                    "error": str(exc),
                }
        if index == 0:
            # The first created seat becomes the GM seat (configure_session);
            # the GM seat never holds a share credential.
            room_access.revoke_seat_token(instance, uid)
            created.pop("seat_token", None)
        # 每张卡都回填控制记录（含默认 human），前端不必再猜自己拿到的是什么。
        created["control"] = get_control(instance, uid)
        created_players.append(created)
    return created_players, None


def _requested_control(character: dict, unclaimed_control_default: str) -> str:
    """Resolve one creation card's controller.

    Precedence: an explicit per-card choice wins; otherwise the room-level
    default for the cards the creator did not decide; otherwise the legacy
    behaviour (``human``, i.e. every seat today's tables already get), so an old
    client that sends no control information at all is unaffected.
    """

    explicit = str((character or {}).get("control") or "").strip()
    if explicit:
        return explicit
    fallback = str(unclaimed_control_default or "").strip()
    return fallback or DEFAULT_CONTROL_MODE
