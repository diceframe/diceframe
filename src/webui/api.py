"""WebUI 后端 API -- 为六个管理页面提供 JSON 数据接口。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from src.engine.character_utils import calc_hp_from_rule, get_rule_attr_config, make_default_character, parse_tavern_card, roll_attributes
from src.engine.game_instance import GameRegistry
from src.lorebook.store import LorebookStore
from src.memory.delta import MemoryStore
from src.rules.rule_system import RuleSystem
from src.engine.world_template import load_world_template
from src.webui.services import asr, avatars, bot_access, bot_extensions, character_cards, characters, content, content_pack_maps, generation, games, logs, map_backgrounds, maps, memory, tavern, turns, worlds, rules, plugins, scene_images, speech, system, tunnel, announcements, assistant, hub, legal
from src.webui.services._common import _parse_game_key, _is_safe_world_id

logger = logging.getLogger("trpg")


def can_modify_character(session_uid: str, target_uid: str, gm_uid: str, owner: bool = False) -> bool:
    """角色卡更新/删除权限：仅本人、该局 GM 或已登录 owner 可改，空身份拒绝。"""
    if not session_uid:
        return False
    return session_uid == target_uid or session_uid == gm_uid or owner


class WebAPI:
    """WebUI 数据接口，供前端页面调用。

    方法签名保持简单，便于通过 HTTP/WebSocket 暴露。
    所有返回值为 JSON 可序列化的字典。
    """

    async def legal_document(self, document_name: str, language: str) -> dict[str, Any]:
        return await legal.document(self, document_name, language)

    async def fetch_public_content_text(
        self,
        path: str,
        *,
        force_refresh: bool = False,
        allow_cached: bool = True,
    ) -> str:
        return await content.fetch_text(
            self,
            path,
            force_refresh=force_refresh,
            allow_cached=allow_cached,
        )

    def public_content_disk_age(self, path: str) -> float | None:
        """公共内容磁盘缓存文件的年龄（秒）；无缓存返回 None。"""
        return content.disk_cache_age_seconds(self, path)

    async def fetch_public_content_json(
        self,
        path: str,
        *,
        force_refresh: bool = False,
        allow_cached: bool = True,
    ) -> dict[str, Any] | None:
        return await content.fetch_json(
            self,
            path,
            force_refresh=force_refresh,
            allow_cached=allow_cached,
        )

    async def current_legal_documents(self) -> dict[str, Any]:
        return await legal.current_documents(self)

    def legal_acceptance_payload(
        self,
        documents: dict[str, Any],
        language: str,
    ) -> dict[str, dict[str, str]]:
        return legal.acceptance_payload(documents, language)

    def legal_accepted(
        self,
        state: dict[str, Any],
        documents: dict[str, Any] | None = None,
    ) -> bool:
        return legal.accepted(state, documents)

    def record_legal_acceptance(
        self,
        state: dict[str, Any],
        *,
        acceptance: dict[str, Any],
        documents: dict[str, Any],
        accepted_at: str,
    ) -> None:
        return legal.record_acceptance(
            state,
            acceptance=acceptance,
            documents=documents,
            accepted_at=accepted_at,
        )

    def legal_bundle_version(self, documents: dict[str, Any]) -> str:
        return legal.bundle_version(documents)

    def __init__(self, registry: GameRegistry, lorebook: LorebookStore,
                 memory: MemoryStore, rules_dir: Path,
                 handler=None, llm_client=None, worlds_dir: Path | None = None,
                 character_gen_max_tokens: int = 2048,
                 text_gen_max_tokens: int = 1024, plugin_host=None, hub_client=None,
                 speech_service=None, asr_service=None, imagegen_service=None):
        self._reg = registry
        self._lore = lorebook
        self._mem = memory
        self._rules_dir = rules_dir
        self._handler = handler
        self._llm_client = llm_client
        self._worlds_dir = worlds_dir or (Path(__file__).parent.parent.parent / "templates" / "worlds")
        self._character_cards_path = self._reg.save_dir.parent / "character_cards.json"
        self._avatars_dir = self._reg.save_dir.parent / "avatars"
        self._scene_images_dir = self._reg.save_dir.parent / "scene-images"
        self._map_backgrounds_dir = self._reg.save_dir.parent / "map-backgrounds"
        self.character_gen_max_tokens = character_gen_max_tokens
        self.text_gen_max_tokens = text_gen_max_tokens
        self._plugins = plugin_host
        self._hub = hub_client
        self._speech = speech_service
        self._asr = asr_service
        self._imagegen = imagegen_service
        if self._plugins and self._handler and hasattr(self._handler, "set_plugin_host"):
            self._handler.set_plugin_host(self._plugins)


    # ---- 跨域共享辅助 ----

    def _llm_configuration_status(self) -> dict[str, Any]:
        """检查当前主模型配置；兼容不暴露 providers 的测试或第三方客户端。"""
        client = self._llm_client
        if client is None:
            return {"ready": False, "missing": ["client"], "provider": ""}

        providers = getattr(client, "providers", None)
        if not isinstance(providers, dict):
            return {
                "ready": True,
                "missing": [],
                "provider": str(getattr(client, "default", "") or ""),
            }

        default = str(getattr(client, "default", "") or "")
        provider = providers.get(default)
        if provider is None and providers:
            provider = next(iter(providers.values()))
        if provider is None:
            return {"ready": False, "missing": ["provider"], "provider": default}

        missing: list[str] = []
        if not str(getattr(provider, "base_url", "") or "").strip():
            missing.append("base_url")
        if not str(getattr(provider, "model_name", "") or "").strip():
            missing.append("model")
        if not str(getattr(provider, "api_key", "") or "").strip():
            missing.append("api_key")
        return {
            "ready": not missing,
            "missing": missing,
            "provider": str(getattr(provider, "provider_name", default) or default),
        }

    def _llm_configuration_error(self, language: str = "zh-CN") -> dict[str, Any] | None:
        """为所有依赖主模型的 WebUI 域生成一致的未配置错误。"""
        status = self._llm_configuration_status()
        if status["ready"]:
            return None
        english = str(language or "").lower().startswith("en")
        message = (
            "The model API is not configured. Open Settings and fill in the API key, "
            "base URL, and model before continuing."
            if english
            else "尚未配置模型 API，请先前往设置页填写 API Key、Base URL 和模型。"
        )
        return {
            "ok": False,
            "error_code": "llm_not_configured",
            "error": message,
            "missing": status["missing"],
        }

    # ---- 规则辅助 ----

    def list_plugins(self) -> dict[str, Any]:
        return plugins.list_plugins(self)

    async def get_official_announcement(self, language: str = "zh-CN") -> dict[str, Any]:
        return await announcements.fetch_official_announcement(self, language)

    async def hub_preferences(self, language: str = "zh-CN") -> dict[str, Any]:
        return await hub.preferences(self, language)

    async def update_hub_preferences(
        self,
        telemetry_enabled: bool,
        legal_acceptance: dict[str, Any] | None = None,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        return await hub.update_preferences(self, telemetry_enabled, legal_acceptance, language)

    async def delete_hub_identity(self) -> dict[str, Any]:
        return await hub.delete_identity(self)

    async def create_rendezvous_room(self, peer_count: int) -> dict[str, Any]:
        return await hub.create_rendezvous_room(self, peer_count)

    async def rendezvous_config(self) -> dict[str, Any]:
        return await hub.rendezvous_config(self)

    async def hub_plugin_detail(self, plugin_id: str) -> dict[str, Any]:
        return await hub.plugin_detail(self, plugin_id)

    async def hub_plugin_readme(self, plugin_id: str) -> dict[str, Any]:
        return await hub.plugin_readme(self, plugin_id)

    async def hub_plugin_ratings(self, plugin_id: str) -> dict[str, Any]:
        return await hub.plugin_ratings(self, plugin_id)

    async def set_hub_plugin_like(self, plugin_id: str, liked: bool) -> dict[str, Any]:
        return await hub.set_plugin_like(self, plugin_id, liked)

    async def set_hub_plugin_rating(
        self, plugin_id: str, stars: int | None, tags: list[str] | None = None
    ) -> dict[str, Any]:
        return await hub.set_plugin_rating(self, plugin_id, stars, tags)

    async def assistant_chat(self, response, messages: list[dict], language: str = "zh-CN") -> None:
        return await assistant.chat_stream(self, response, messages, language)

    def list_plugin_types(self) -> dict[str, Any]:
        return plugins.list_plugin_types(self)

    def list_speech_voices(self) -> dict[str, Any]:
        return speech.list_voices(self)

    def list_personal_speech_profiles(self) -> dict[str, Any]:
        return speech.list_personal_profiles(self)

    def save_personal_speech_profile(
        self,
        profile_id: str,
        values: dict[str, Any],
        *,
        file_data: str = "",
        file_name: str = "",
    ) -> dict[str, Any]:
        return speech.save_personal_profile(
            self,
            profile_id,
            values,
            file_data=file_data,
            file_name=file_name,
        )

    def delete_personal_speech_profile(self, profile_id: str) -> dict[str, Any]:
        return speech.delete_personal_profile(self, profile_id)

    async def synthesize_speech(
        self,
        game_key: str,
        user_id: str,
        text: str,
        voice: str = "",
        language: str = "zh-CN",
        speed: float = 1.0,
        owner: bool = False,
    ):
        return await speech.synthesize(self, game_key, user_id, text, voice, language, speed, owner=owner)

    async def test_speech(
        self,
        text: str,
        voice: str = "",
        language: str = "zh-CN",
        speed: float = 1.0,
    ):
        return await speech.test_synthesis(self, text, voice, language, speed)

    async def transcribe_speech(
        self,
        game_key: str,
        user_id: str,
        audio: bytes,
        content_type: str,
        language: str = "",
        owner: bool = False,
    ):
        return await asr.transcribe(self, game_key, user_id, audio, content_type, language, owner=owner)

    async def test_transcription(self, audio: bytes, content_type: str, language: str = ""):
        return await asr.test_transcription(self, audio, content_type, language)

    async def rescan_plugins(self) -> dict[str, Any]:
        return await plugins.rescan_plugins(self)

    def publish_tunnel_url(self, plugin_id: str, url: str) -> dict[str, Any]:
        return tunnel.publish_tunnel_url(self, plugin_id, url)

    def release_tunnel_url(self, plugin_id: str) -> dict[str, Any]:
        return tunnel.release_tunnel_url(self, plugin_id)

    def tunnel_status(self) -> dict[str, Any]:
        return tunnel.tunnel_status(self)

    def authenticate_plugin_token(self, token: str) -> dict[str, Any] | None:
        """校验插件进程内部 Token，返回插件身份（复用 plugin_host）。"""
        if not self._plugins:
            return None
        return self._plugins.authenticate_api_token(token)

    def plugin_detail(self, plugin_id: str) -> dict[str, Any]:
        return plugins.plugin_detail(self, plugin_id)

    def read_plugin_docs(self, plugin_id: str) -> dict[str, Any]:
        return plugins.read_plugin_docs(self, plugin_id)

    async def update_plugin_config(self, plugin_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        return await plugins.update_plugin_config(self, plugin_id, changes)

    async def control_plugin(self, plugin_id: str, action: str) -> dict[str, Any]:
        return await plugins.control_plugin(self, plugin_id, action)

    async def install_plugin(self, payload: bytes, overwrite: bool = False) -> dict[str, Any]:
        return await plugins.install_plugin(self, payload, overwrite)

    async def list_plugin_marketplace(self) -> dict[str, Any]:
        return await plugins.list_plugin_marketplace(self)

    async def install_marketplace_plugin(self, plugin_id: str, overwrite: bool = False) -> dict[str, Any]:
        return await plugins.install_marketplace_plugin(self, plugin_id, overwrite)

    async def update_marketplace_plugin(self, plugin_id: str) -> dict[str, Any]:
        return await plugins.update_marketplace_plugin(self, plugin_id)

    async def uninstall_plugin(self, plugin_id: str, delete_data: bool = False) -> dict[str, Any]:
        return await plugins.uninstall_plugin(self, plugin_id, delete_data)

    def list_plugin_mirrors(self) -> dict[str, Any]:
        return plugins.list_plugin_mirrors(self)

    def add_plugin_mirror(self, data: dict[str, Any]) -> dict[str, Any]:
        return plugins.add_plugin_mirror(self, data)

    def update_plugin_mirror(self, mirror_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return plugins.update_plugin_mirror(self, mirror_id, data)

    def delete_plugin_mirror(self, mirror_id: str) -> dict[str, Any]:
        return plugins.delete_plugin_mirror(self, mirror_id)

    async def test_plugin_mirror(self, mirror_id: str = "") -> dict[str, Any]:
        return await plugins.test_plugin_mirror(self, mirror_id)

    def clear_plugin_card_cache(self, plugin_id: str) -> dict[str, Any]:
        return plugins.clear_plugin_card_cache(self, plugin_id)

    def list_plugin_contributions(self, kind: str = "") -> dict[str, Any]:
        return plugins.list_plugin_contributions(self, kind)

    def list_plugin_themes(self) -> dict[str, Any]:
        return plugins.list_plugin_themes(self)

    def list_plugin_tools(self) -> dict[str, Any]:
        return plugins.list_plugin_tools(self)

    async def invoke_plugin_tool(
        self,
        plugin_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await plugins.invoke_plugin_tool(self, plugin_id, tool_name, arguments, context)

    def list_plugin_content(self, kind: str = "", world_id: str = "", rule_id: str = "") -> dict[str, Any]:
        return plugins.list_plugin_content(self, kind, world_id, rule_id)

    def sync_plugin_lorebooks(self) -> dict[str, Any]:
        """同步已启用插件的世界模板世界书到世界书库（幂等）。"""
        return plugins.sync_plugin_lorebooks(self)

    def cleanup_plugin_lorebook(self, plugin_id: str) -> dict[str, Any]:
        """删除某插件灌入的、未被用户改动的世界书条目。"""
        return plugins.cleanup_plugin_lorebook(self, plugin_id)

    def import_plugin_content(
        self,
        kind: str,
        resource_id: str,
        plugin_id: str = "",
        target_world_id: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return plugins.import_plugin_content(self, kind, resource_id, plugin_id, target_world_id, overwrite)

    def import_all_plugin_content(self, plugin_id: str, target_world_id: str = "") -> dict[str, Any]:
        return plugins.import_all_plugin_content(self, plugin_id, target_world_id)

    def export_content_pack(
        self,
        plugin_id: str,
        name: str,
        version: str,
        description: str,
        world_id: str = "",
        card_ids: list[str] | None = None,
        rule_id: str = "",
        flat: bool = False,
        include_portraits: bool = True,
        include_scene_images: bool = True,
        world_scene_image: dict[str, Any] | None = None,
        rule_scene_image: dict[str, Any] | None = None,
        include_map: bool = True,
        map_background: dict[str, Any] | None = None,
        map_icons: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return plugins.export_content_pack(
            self, plugin_id, name, version, description, world_id, card_ids, rule_id, flat,
            include_portraits, include_scene_images, world_scene_image, rule_scene_image,
            include_map, map_background, map_icons,
        )

    def package_content_map(
        self,
        plugin_id: str,
        pack_name: str,
        world: dict[str, Any],
        entries: list[dict[str, Any]],
        files: dict[str, str | bytes],
        *,
        background_selection: dict[str, Any] | None = None,
        icon_uploads: list[dict[str, Any]] | None = None,
    ):
        """Package map contributions through the WebAPI cross-domain facade."""
        return content_pack_maps.package_content_map(
            self,
            plugin_id,
            pack_name,
            world,
            entries,
            files,
            background_selection=background_selection,
            icon_uploads=icon_uploads,
        )

    def plugin_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        return plugins.plugin_asset_path(self, plugin_id, relative_path)

    async def check_updates(self, include_prerelease: bool | None = None) -> dict[str, Any]:
        return await system.check_updates(self, include_prerelease)

    def _load_world_template(self, world_id: str) -> dict[str, Any] | None:
        """按 world_id 读取世界模板；不存在或非法时返回 None。"""
        if not self._worlds_dir:
            return None
        data = load_world_template(self._worlds_dir, world_id)
        if data:
            return data
        if self._plugins:
            return self._plugins.load_world_template(world_id)
        return None

    def _load_rule_for_game(self, inst) -> RuleSystem | None:
        """优先按存档自身规则加载；旧存档缺失时回退世界默认规则。"""
        if not inst.world_id or not self._worlds_dir:
            return None
        world_data = self._load_world_template(inst.world_id)
        if not world_data:
            return None
        language = getattr(inst, "language", "") or world_data.get("language", "")
        rule_id = str(
            getattr(inst, "rule_id", "")
            or world_data.get("default_rule")
            or "freeform_fantasy"
        )
        return self._load_rule_by_id(rule_id, language)

    def _load_rule_by_id(self, rule_id: str, language: str = "") -> RuleSystem | None:
        rule_id = (rule_id or "").strip()
        if not rule_id or not rules.is_valid_rule_id(rule_id):
            return None
        rule_path = RuleSystem.path_for(self._rules_dir, rule_id, language)
        if not rule_path.exists() and self._plugins:
            plugin_path = self._plugins.contribution_path("rule", rule_id)
            if plugin_path:
                rule_path = plugin_path
        if not rule_path.exists():
            return None
        return RuleSystem.load(rule_path)

    # ---- 游戏总览 ----

    def list_games(self) -> dict[str, Any]:
        return games.list_games(self)

    def game_detail(self, game_key: str) -> dict[str, Any] | None:
        return games.game_detail(self, game_key)

    def delete_game(self, game_key: str) -> dict[str, Any]:
        return games.delete_game(self, game_key)

    async def get_bot_bind_token(self, game_key: str, rotate: bool = False) -> dict[str, Any]:
        return await bot_access.get_bind_token(self, game_key, rotate)

    async def verify_bot_bind_game(self, game_key: str, bind_token: str) -> dict[str, Any]:
        return await bot_access.verify_bind_game(self, game_key, bind_token)

    def bot_actor_allowed(self, game_key: str, user_id: str) -> bool:
        return bot_access.actor_allowed(self, game_key, user_id)

    def bot_extension_capabilities(self) -> dict[str, Any]:
        return bot_extensions.capabilities(self)

    async def apply_bot_extensions(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await bot_extensions.apply(self, stage, payload)

    def bot_extension_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        return bot_extensions.asset_path(self, plugin_id, relative_path)

    def bot_bridge_card_path(self, name: str) -> Path:
        return bot_extensions.bridge_card_path(self, name)

    def multiplayer_status(self, game_key: str) -> dict[str, Any]:
        return games.multiplayer_status(self, game_key)

    async def set_player_away(self, game_key: str, user_id: str, away: bool) -> dict[str, Any]:
        return await games.set_player_away(self, game_key, user_id, away)

    async def set_player_access(self, game_key: str, open_access: bool) -> dict[str, Any]:
        return await games.set_player_access(self, game_key, open_access)

    def check_request_for_action(
        self,
        game_key: str,
        user_id: str,
        text: str,
        selected_attribute: str = "",
        selected_skill: str = "",
        target_text: str = "",
    ) -> dict[str, Any] | None:
        return games.check_request_for_action(
            self, game_key, user_id, text, selected_attribute, selected_skill, target_text
        )

    def roll_for_game(self, game_key: str) -> dict[str, Any]:
        return games.roll_for_game(self, game_key)

    async def resolve_pending_dice_for_game(self, game_key: str, user_id: str = "", source: str = "system") -> dict[str, Any]:
        return await games.resolve_pending_dice_for_game(self, game_key, user_id, source)

    async def resolve_luck_decision(self, game_key: str, check_id: str, actor_uid: str, spend: bool) -> dict[str, Any]:
        return await games.resolve_luck_decision(self, game_key, check_id, actor_uid, spend)

    async def decline_pending_luck(self, game_key: str) -> dict[str, Any]:
        return await games.decline_pending_luck(self, game_key)

    async def submit_action(self, game_key: str, actor_uid: str, text: str, **kwargs) -> turns.TurnResult:
        return await turns.submit_action(self, game_key, actor_uid, text, **kwargs)

    async def resolve_luck_and_continue(
        self,
        game_key: str,
        check_id: str,
        actor_uid: str,
        spend: bool,
        **kwargs,
    ) -> turns.TurnResult:
        return await turns.resolve_luck_and_continue(
            self, game_key, check_id, actor_uid, spend, **kwargs
        )

    async def advance_turn(self, game_key: str, actor_uid: str, **kwargs) -> turns.TurnResult:
        return await turns.advance_round(self, game_key, actor_uid, **kwargs)

    def private_log(self, game_key: str) -> dict[str, Any]:
        return games.private_log(self, game_key)

    def private_log_for_user(self, game_key: str, user_id: str) -> dict[str, Any]:
        return games.private_log_for_user(self, game_key, user_id)

    def game_health(self, game_key: str, include_resolved: bool = False) -> dict[str, Any]:
        return games.game_health(self, game_key, include_resolved)

    async def set_solo_mode(self, game_key: str, solo: bool) -> dict[str, Any]:
        return await games.set_solo_mode(self, game_key, solo)

    async def mark_game_health_event(
        self,
        game_key: str,
        event_id: str,
        *,
        resolved: bool = False,
        ignored: bool = False,
    ) -> dict[str, Any]:
        return await games.mark_game_health_event(self, game_key, event_id, resolved=resolved, ignored=ignored)

    async def gm_command(self, game_key: str, command: str, mode: str = "note") -> dict[str, Any]:
        return await games.gm_command(self, game_key, command, mode)

    async def rollback_round(self, game_key: str) -> dict[str, Any]:
        return await games.rollback_round(self, game_key)

    async def generate_story_recap(self, game_key: str) -> dict[str, Any]:
        return await games.generate_story_recap(self, game_key)

    async def gm_private_message(self, game_key: str, user_id: str, text: str) -> dict[str, Any]:
        return await games.gm_private_message(self, game_key, user_id, text)

    # ---- 角色卡库 ----

    def list_character_cards(self) -> dict[str, Any]:
        return character_cards.list_character_cards(self)

    def save_character_card(self, character: dict) -> dict[str, Any]:
        return character_cards.save_character_card(self, character)

    def update_character_card(self, card_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return character_cards.update_character_card(self, card_id, patch)

    def delete_character_card(self, card_id: str) -> dict[str, Any]:
        return character_cards.delete_character_card(self, card_id)

    async def import_character_card(self, file_data: str = "", file_name: str = "card.json",
                                    target: str = "character_card", world_id: str = "") -> dict[str, Any]:
        return await character_cards.import_character_card(self, file_data, file_name, target, world_id)

    def export_character_cards(self, card_ids: list[str]) -> dict[str, Any]:
        return character_cards.export_character_cards(self, card_ids)

    # ---- 世界编辑器 ----

    def list_worlds(self) -> dict[str, Any]:
        # 确保已启用插件的世界模板世界书已同步（幂等）
        if self._plugins:
            try:
                plugins.sync_plugin_lorebooks(self)
            except Exception:
                logger.warning("list_worlds 同步插件世界书失败，已跳过", exc_info=True)
        return worlds.list_worlds(self)

    def create_world(self, name: str, description: str = "", language: str = "") -> dict[str, Any]:
        return worlds.create_world(self, name, description, language)

    def list_entries(self, world_id: str, entry_type: str | None = None) -> dict[str, Any]:
        return worlds.list_entries(self, world_id, entry_type)

    def search_entries(self, world_id: str, keyword: str) -> dict[str, Any]:
        return worlds.search_entries(self, world_id, keyword)

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        return worlds.get_entry(self, entry_id)

    def save_entry(self, entry: dict) -> dict[str, Any]:
        return worlds.save_entry(self, entry)

    async def generate_lorebook_entries(self, world_id: str, prompt: str, language: str = "") -> dict[str, Any]:
        return await worlds.generate_lorebook_entries(self, world_id, prompt, language)

    def update_entry(self, entry_id: str, updates: dict) -> dict[str, Any]:
        return worlds.update_entry(self, entry_id, updates)

    def delete_entry(self, entry_id: str) -> dict[str, Any]:
        return worlds.delete_entry(self, entry_id)

    def delete_world(self, world_id: str) -> dict[str, Any]:
        return worlds.delete_world(self, world_id)

    def _rebuild_lorebook_index(self, world_id: str) -> None:
        worlds.rebuild_lorebook_index(self, world_id)

    # ---- 角色管理 ----

    def list_characters(self, game_key: str) -> dict[str, Any]:
        return characters.list_characters(self, game_key)

    def character_schema(self, rule_id: str, language: str = "") -> dict[str, Any]:
        return characters.character_schema(self, rule_id, language)

    def get_character(self, game_key: str, user_id: str) -> dict[str, Any] | None:
        return characters.get_character(self, game_key, user_id)

    async def update_character(self, game_key: str, user_id: str, updates: dict) -> dict[str, Any]:
        return await characters.update_character(self, game_key, user_id, updates)

    async def update_npc_portrait(self, game_key: str, npc_id: str, portrait: Any) -> dict[str, Any]:
        return await characters.update_npc_portrait(self, game_key, npc_id, portrait)

    async def resolve_payment(self, game_key: str, payment_id: str, accepted: bool, session_uid: str = "") -> dict[str, Any]:
        return await characters.resolve_payment(self, game_key, payment_id, accepted, session_uid)

    async def delete_character(self, game_key: str, user_id: str) -> dict[str, Any]:
        return await characters.delete_character(self, game_key, user_id)

    async def create_player(self, game_key: str, character: dict,
                           force_uid: str = "", assign_new_id: bool = False) -> dict[str, Any]:
        return await characters.create_player(self, game_key, character, force_uid, assign_new_id)

    def save_avatar_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        return avatars.save_avatar_upload(self, file_data, file_name)

    def avatar_file(self, asset_id: str) -> Path | None:
        return avatars.avatar_file(self, asset_id)

    def list_user_avatars(self) -> dict[str, Any]:
        return avatars.list_user_avatars(self)

    def delete_avatar(self, asset_id: str) -> dict[str, Any]:
        return avatars.delete_avatar(self, asset_id)

    def generated_image_file(self, asset_id: str) -> Path | None:
        if self._imagegen is None:
            return None
        return self._imagegen.assets.file(asset_id)

    def save_scene_image_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        return scene_images.save_scene_image_upload(self, file_data, file_name)

    def scene_image_file(self, asset_id: str) -> Path | None:
        return scene_images.scene_image_file(self, asset_id)

    def validate_scene_image_ref(self, reference: Any) -> dict[str, str]:
        return scene_images.validate_scene_image_ref(self, reference)

    def resolve_default_scene_image(self, world_id: str = "", rule_id: str = "") -> dict[str, str]:
        return scene_images.resolve_default_scene_image(self, world_id, rule_id)

    def materialize_scene_image(self, reference: Any) -> dict[str, str]:
        return scene_images.materialize_scene_image(self, reference)

    def resolve_scene_image_file(self, reference: Any) -> Path | None:
        return scene_images.resolve_scene_image_file(self, reference)

    def package_scene_image(
        self,
        reference: Any,
        files: dict[str, str | bytes],
    ) -> dict[str, str] | None:
        return scene_images.package_scene_image(self, reference, files)

    def save_map_background_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        return map_backgrounds.save_map_background_upload(self, file_data, file_name)

    def map_background_file(self, asset_id: str) -> Path | None:
        return map_backgrounds.map_background_file(self, asset_id)

    def validate_map_background_selection(self, selection: Any) -> dict[str, str]:
        return map_backgrounds.validate_map_background_selection(self, selection)

    def resolve_map_background_file(self, selection: Any) -> Path | None:
        return map_backgrounds.resolve_map_background_file(self, selection)

    # ---- 剧情日志 ----

    def get_log(
        self,
        game_key: str,
        page: int = 1,
        per_page: int = 50,
        include_internal: bool = False,
    ) -> dict[str, Any]:
        return logs.get_log(self, game_key, page, per_page, include_internal)

    def get_statistics(self, game_key: str) -> dict[str, Any]:
        return logs.get_statistics(self, game_key)

    # ---- 规则配置 ----

    def list_rules(self) -> dict[str, Any]:
        return rules.list_rules(self)

    def save_custom_rule(self, data: dict[str, Any]) -> dict[str, Any]:
        return rules.save_custom_rule(self, data)

    def get_rule_template(self, rule_id: str) -> dict[str, Any]:
        return rules.get_rule_template(self, rule_id)

    def update_custom_rule(self, rule_id: str, template: dict[str, Any]) -> dict[str, Any]:
        return rules.update_custom_rule(self, rule_id, template)

    def delete_custom_rule(self, rule_id: str) -> dict[str, Any]:
        return rules.delete_custom_rule(self, rule_id)

    # ---- 世界模板 ----

    def list_world_templates(self) -> dict[str, Any]:
        # 确保已启用插件的世界模板世界书已同步（幂等）
        if self._plugins:
            try:
                plugins.sync_plugin_lorebooks(self)
            except Exception:
                logger.warning("list_world_templates 同步插件世界书失败，已跳过", exc_info=True)
        return worlds.list_world_templates(self)

    def cleanup_orphan_game_templates(self, world_id: str = "") -> int:
        return worlds.cleanup_orphan_game_templates(self, world_id)

    # ---- 创建游戏 ----

    async def create_game(self, world_id: str, game_name: str = "",
                           group_name: str = "Web端", rule_id: str = "",
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
                           room_password: str = "",
                           language: str = "",
                           scene_image: dict[str, Any] | None = None,
                           map_background: dict[str, Any] | None = None) -> dict[str, Any]:
        return await games.create_game(self, world_id, game_name, group_name, rule_id,
                                       solo, lorebook_world_id, difficulty, description,
                                       create_lorebook, blank_lorebook, source_world_id,
                                       players, custom_world, gm_uid, room_password,
                                       language, scene_image, map_background)

    # ---- 重开引用码 ----

    async def reset_game(self, game_key: str) -> dict[str, Any]:
        return await games.reset_game(self, game_key)

    async def restart_game(self, game_key: str) -> dict[str, Any]:
        return await games.restart_game(self, game_key)

    async def switch_world(self, game_key: str, world_id: str) -> dict[str, Any]:
        return await games.switch_world(self, game_key, world_id)

    async def create_from_seed(self, seed_code: str, solo: bool = False,
                               players: list[dict] | None = None,
                               gm_uid: str = "",
                               language: str = "",
                               scene_image: dict[str, Any] | None = None) -> dict[str, Any]:
        return await games.create_from_seed(self, seed_code, solo, players, gm_uid, language, scene_image)

    async def update_scene_image(
        self,
        game_key: str,
        reference: dict[str, Any] | None = None,
        *,
        use_default: bool = False,
    ) -> dict[str, Any]:
        return await games.update_scene_image(self, game_key, reference, use_default=use_default)

    async def update_map_background(
        self,
        game_key: str,
        selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await maps.update_map_background(self, game_key, selection)

    # ---- AI 生成 ----

    async def test_connection(self, base_url: str, api_key: str,
                              model: str, proxy_url: str = "",
                              api_format: str = "openai") -> dict[str, Any]:
        return await generation.test_connection(self, base_url, api_key, model, proxy_url, api_format)

    async def list_models(self, base_url: str, api_key: str,
                          proxy_url: str = "", api_format: str = "openai") -> dict[str, Any]:
        return await generation.list_models(self, base_url, api_key, proxy_url, api_format)

    async def generate_world(self, prompt: str, rule_id: str = "", language: str = "") -> dict[str, Any]:
        return await generation.generate_world(self, prompt, rule_id, language)

    async def generate_rule(self, prompt: str, source_rule_id: str = "", language: str = "") -> dict[str, Any]:
        return await generation.generate_rule(self, prompt, source_rule_id, language)

    async def generate_character(self, prompt: str, game_key: str = "", rule_id: str = "", language: str = "") -> dict[str, Any]:
        return await generation.generate_character(self, prompt, game_key, rule_id, language)

    async def generate_text(self, prompt: str, system_hint: str = "", language: str = "") -> dict[str, Any]:
        return await generation.generate_text(self, prompt, system_hint, language)

    # ---- 内存 ----

    def list_memories(self, game_key: str, keyword: str = "",
                      limit: int = 20, offset: int = 0) -> dict[str, Any]:
        return memory.list_memories(self, game_key, keyword, limit, offset)

    async def update_memory(self, game_key: str, entry_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        return await memory.update_memory(self, game_key, entry_id, updates)

    async def delete_memory(self, game_key: str, entry_id: int) -> dict[str, Any]:
        return await memory.delete_memory(self, game_key, entry_id)

    async def import_tavern_card(self, file_path: str = "", file_data: str = "",
                                 file_name: str = "card.png", game_key: str = "") -> dict[str, Any]:
        return await tavern.import_tavern_card(self, file_path, file_data, file_name, game_key)

    # ----

    def get_map_locations(self, game_key: str) -> dict[str, Any]:
        return maps.get_map_locations(self, game_key)

    def map_background_asset(self, game_key: str, asset_id: str) -> Path | None:
        return maps.map_background_asset(self, game_key, asset_id)

    @staticmethod
    def _parse_key(game_key: str) -> tuple:
        return _parse_game_key(game_key)
