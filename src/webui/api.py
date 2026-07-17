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
from src.webui.services import bot_access, character_cards, characters, generation, games, logs, maps, memory, tavern, worlds, rules, plugins, system
from src.webui.services._common import _parse_game_key, _is_safe_world_id

logger = logging.getLogger("trpg")


def can_modify_character(session_uid: str, target_uid: str, gm_uid: str) -> bool:
    """角色卡更新/删除权限：仅本人或该局 GM 可改，空身份拒绝。"""
    return bool(session_uid) and (session_uid == target_uid or session_uid == gm_uid)


class WebAPI:
    """WebUI 数据接口，供前端页面调用。

    方法签名保持简单，便于通过 HTTP/WebSocket 暴露。
    所有返回值为 JSON 可序列化的字典。
    """

    def __init__(self, registry: GameRegistry, lorebook: LorebookStore,
                 memory: MemoryStore, rules_dir: Path,
                 handler=None, llm_client=None, worlds_dir: Path | None = None,
                 character_gen_max_tokens: int = 2048,
                 text_gen_max_tokens: int = 400, plugin_host=None):
        self._reg = registry
        self._lore = lorebook
        self._mem = memory
        self._rules_dir = rules_dir
        self._handler = handler
        self._llm_client = llm_client
        self._worlds_dir = worlds_dir or (Path(__file__).parent.parent.parent / "templates" / "worlds")
        self._character_cards_path = self._reg.save_dir.parent / "character_cards.json"
        self.character_gen_max_tokens = character_gen_max_tokens
        self.text_gen_max_tokens = text_gen_max_tokens
        self._plugins = plugin_host
        if self._plugins and self._handler and hasattr(self._handler, "set_plugin_host"):
            self._handler.set_plugin_host(self._plugins)

    # ---- 规则辅助 ----

    def list_plugins(self) -> dict[str, Any]:
        return plugins.list_plugins(self)

    def plugin_detail(self, plugin_id: str) -> dict[str, Any]:
        return plugins.plugin_detail(self, plugin_id)

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

    def list_plugin_content(self, kind: str = "", world_id: str = "", rule_id: str = "") -> dict[str, Any]:
        return plugins.list_plugin_content(self, kind, world_id, rule_id)

    def import_plugin_content(
        self,
        kind: str,
        resource_id: str,
        plugin_id: str = "",
        target_world_id: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return plugins.import_plugin_content(self, kind, resource_id, plugin_id, target_world_id, overwrite)

    def plugin_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        return plugins.plugin_asset_path(self, plugin_id, relative_path)

    async def check_updates(self, include_prerelease: bool = False) -> dict[str, Any]:
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
        """从游戏实例关联的世界模板加载规则系统。"""
        if not inst.world_id or not self._worlds_dir:
            return None
        world_data = self._load_world_template(inst.world_id)
        if not world_data:
            return None
        language = getattr(inst, "language", "") or world_data.get("language", "")
        return self._load_rule_by_id(str(world_data.get("default_rule") or "freeform_fantasy"), language)

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

    async def get_bot_bind_token(self, game_key: str, rotate: bool = False) -> dict[str, Any]:
        return await bot_access.get_bind_token(self, game_key, rotate)

    async def verify_bot_bind_game(self, game_key: str, bind_token: str) -> dict[str, Any]:
        return await bot_access.verify_bind_game(self, game_key, bind_token)

    def bot_actor_allowed(self, game_key: str, user_id: str) -> bool:
        return bot_access.actor_allowed(self, game_key, user_id)

    def multiplayer_status(self, game_key: str) -> dict[str, Any]:
        return games.multiplayer_status(self, game_key)

    async def set_player_away(self, game_key: str, user_id: str, away: bool) -> dict[str, Any]:
        return await games.set_player_away(self, game_key, user_id, away)

    async def set_player_access(self, game_key: str, open_access: bool) -> dict[str, Any]:
        return await games.set_player_access(self, game_key, open_access)

    def roll_for_game(self, game_key: str) -> dict[str, Any]:
        return games.roll_for_game(self, game_key)

    async def resolve_pending_dice_for_game(self, game_key: str, user_id: str = "", source: str = "system") -> dict[str, Any]:
        return await games.resolve_pending_dice_for_game(self, game_key, user_id, source)

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

    async def import_character_card(self, file_data: str = "", file_name: str = "card.json") -> dict[str, Any]:
        return await character_cards.import_character_card(self, file_data, file_name)

    # ---- 世界编辑器 ----

    def list_worlds(self) -> dict[str, Any]:
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

    def get_character(self, game_key: str, user_id: str) -> dict[str, Any] | None:
        return characters.get_character(self, game_key, user_id)

    async def update_character(self, game_key: str, user_id: str, updates: dict) -> dict[str, Any]:
        return await characters.update_character(self, game_key, user_id, updates)

    async def resolve_payment(self, game_key: str, payment_id: str, accepted: bool, session_uid: str = "") -> dict[str, Any]:
        return await characters.resolve_payment(self, game_key, payment_id, accepted, session_uid)

    async def delete_character(self, game_key: str, user_id: str) -> dict[str, Any]:
        return await characters.delete_character(self, game_key, user_id)

    async def create_player(self, game_key: str, character: dict,
                           force_uid: str = "", assign_new_id: bool = False) -> dict[str, Any]:
        return await characters.create_player(self, game_key, character, force_uid, assign_new_id)

    # ---- 剧情日志 ----

    def get_log(self, game_key: str, page: int = 1, per_page: int = 50) -> dict[str, Any]:
        return logs.get_log(self, game_key, page, per_page)

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
        return worlds.list_world_templates(self)

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
                           language: str = "") -> dict[str, Any]:
        return await games.create_game(self, world_id, game_name, group_name, rule_id,
                                       solo, lorebook_world_id, difficulty, description,
                                       create_lorebook, blank_lorebook, source_world_id,
                                       players, custom_world, gm_uid, room_password,
                                       language)

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
                               language: str = "") -> dict[str, Any]:
        return await games.create_from_seed(self, seed_code, solo, players, gm_uid, language)

    # ---- AI 生成 ----

    async def test_connection(self, base_url: str, api_key: str,
                              model: str, proxy_url: str = "",
                              api_format: str = "openai") -> dict[str, Any]:
        return await generation.test_connection(self, base_url, api_key, model, proxy_url, api_format)

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

    @staticmethod
    def _parse_key(game_key: str) -> tuple:
        return _parse_game_key(game_key)
