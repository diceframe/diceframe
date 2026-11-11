"""World binding and scene media transactions for existing games."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.compat.callbacks import load_world_template as load_world_template_compat
from src.content_modules.refs import ContentRefError
from src.engine.module_state import ModuleStateError
from src.engine.modules import content_binding
from src.webui.services._common import _is_safe_world_id, resolve_default_scene_image
from src.localization.messages import t

GameKey = tuple[str, ...]


def _failure(code: str, message: str) -> dict[str, Any]:
    # ``error_code`` is what the frontend localizes; ``code`` matches the
    # authoritative-writer services that report the same conditions.
    return {"ok": False, "error_code": code, "code": code, "error": message}


@dataclass(frozen=True)
class GameMediaDependencies:
    parse_game_key: Callable[[str], GameKey]
    get_instance: Callable[[GameKey], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]
    # ``(world_id, language)``; a one-argument loader is still accepted.
    load_world_template: Callable[..., dict[str, Any] | None] | None
    get_lore_world: Callable[[str], dict[str, Any] | None] | None
    refresh_lorebook_index: Callable[[str], None] | None
    resolve_rule_id: Callable[[Any], str]
    resolve_default_scene_image: Callable[[str, str], dict[str, str]]
    materialize_scene_image: Callable[[Any], dict[str, str]]


class GameMediaService:
    """World and scene changes with explicit template, lore, and save boundaries."""

    def __init__(self, dependencies: GameMediaDependencies) -> None:
        self._dependencies = dependencies

    def _instance(self, game_key: str) -> Any | None:
        return self._dependencies.get_instance(
            self._dependencies.parse_game_key(game_key)
        )

    def _world_display_name(self, world_id: str, language: str = "") -> str | None:
        """Display name of a world (template first, then lorebook world).

        ``language`` selects the template's locale overlay, exactly as the
        world-templates listing does; ``""`` is the canonical name.  Returns
        ``None`` when the world does not exist; raises when the template cannot
        be loaded.  Without a template loader the id is used.
        """

        if self._dependencies.load_world_template is None:
            return world_id
        world_data = load_world_template_compat(
            self._dependencies.load_world_template, world_id, language,
        )
        if world_data:
            return str(world_data.get("world_name", world_id) or world_id)
        if self._dependencies.get_lore_world is not None:
            world = self._dependencies.get_lore_world(world_id)
            if world:
                return str(world.get("name", world_id) or world_id)
        return None

    def _default_titles(self, world_id: str, language: str) -> set[str]:
        """Titles that only name the world, i.e. were never chosen by the user.

        Creation defaults the title to ``game_name or world_id`` and the web
        create form sends the world's display name, localized to the game's
        language, when the name is left blank.  Both the canonical and the
        localized names therefore count.
        """

        titles = {"", str(world_id or "")}
        if not world_id:
            return titles
        for locale in {"", language}:
            try:
                name = self._world_display_name(str(world_id), locale)
            except Exception:
                name = None
            if name:
                titles.add(name)
        return titles

    async def switch_world(
        self, game_key: str, world_id: str,
    ) -> dict[str, Any]:
        """Switch the associated world book while retaining characters and progress."""

        instance = self._instance(game_key)
        if not instance:
            return _failure("GAME_NOT_FOUND", "游戏不存在")
        if not _is_safe_world_id(world_id):
            return _failure("INVALID_WORLD_ID", t("webui.services.game_media.world_id_missing_or_invalid"))
        binding = dict(getattr(instance, "adventure_binding", {}) or {})
        if binding and str(binding.get("world_id") or "") != world_id:
            return _failure(
                "ADVENTURE_WORLD_LOCKED",
                t("webui.services.game_media.fixed_world_adventure"),
            )
        language = str(getattr(instance, "language", "") or "")
        try:
            world_name = self._world_display_name(world_id, language)
        except Exception as exc:
            return _failure("WORLD_LOAD_FAILED", t("webui.services.game_media.world_load_failed", exc=exc))
        if world_name is None:
            return _failure("WORLD_NOT_FOUND", t("webui.services.game_media.world_missing", world_id=world_id))
        async with instance.authoritative_write() as write_entered, instance._lock:
            if not write_entered:
                return _failure("REWRITE_IN_PROGRESS", t("webui.services.game_media.rewriting_wait_and_retry"))
            if self._instance(game_key) is not instance:
                return _failure("STALE_RUN", t("webui.services.game_media.game_restarted_refresh_and_retry"))
            if instance._process_lock.locked():
                return _failure("ROUND_PROCESSING", t("webui.services.game_media.round_processing_retry_later"))
            # world_name is the game's title, shown as the table heading.
            # Switching the world book must not rename a user-titled game; a
            # title that merely names the old world (its id or display name,
            # the creation defaults) follows the newly selected world.
            title = str(instance.world_name or "")
            if title in self._default_titles(str(instance.world_id or ""), language):
                title = world_name
            # world_id and content_binding.world_ref are the same World
            # identity; move them together or not at all.
            try:
                content_binding.set_world_ref(instance, {
                    "source_kind": "world",
                    "source_id": world_id,
                    "kind": "world",
                    "id": world_id,
                    "digest": "",
                })
            except (ContentRefError, ModuleStateError) as exc:
                return _failure("INVALID_WORLD_REF", str(exc))
            instance.set_world(world_id, title)
            if self._dependencies.refresh_lorebook_index is not None:
                self._dependencies.refresh_lorebook_index(world_id)
            await self._dependencies.save_instance(instance)
            return {
                "ok": True,
                "world_id": instance.world_id,
                "world_name": instance.world_name,
                "world_display_name": world_name,
            }

    async def update_scene_image(
        self,
        game_key: str,
        reference: dict[str, Any] | None = None,
        *,
        use_default: bool = False,
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        try:
            if use_default:
                rule_id = self._dependencies.resolve_rule_id(instance)
                instance.rule_id = rule_id
                selected = resolve_default_scene_image(
                    self._dependencies.resolve_default_scene_image,
                    str(instance.world_id or ""),
                    rule_id,
                    game_key,
                )
            else:
                selected = reference
            if not selected:
                return {
                    "ok": False,
                    "error": t("webui.services.game_media.choose_adventure_cover_or_default"),
                }
            materialized = self._dependencies.materialize_scene_image(selected)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        instance.set_scene_image(materialized)
        await self._dependencies.save_instance(instance)
        return {"ok": True, "scene_image": materialized}
