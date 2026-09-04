"""WebUI 后端 API -- 为六个管理页面提供 JSON 数据接口。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

from src.engine.character_utils import calc_hp_from_rule, get_rule_attr_config, make_default_character, parse_tavern_card, roll_attributes
from src.engine.game_instance import GameRegistry
from src.engine.memory_outbox import pending_memory_deliveries, pending_memory_reversals
from src.lorebook.store import LorebookStore
from src.adventures import AdventureBundleLoader
from src.memory.delta import MemoryStore
from src.rules.rule_system import RuleSystem
from src.rules.loader import RuleBundleLoader
from src.rulesets.builtin import (
    build_default_ruleset_registry,
    default_adventure_runtime_requirement,
)
from src.rulesets.registry import RulesetRuntimeRegistry
from src.engine.world_template import load_world_template
from src.webui.services import adventures, asr, avatars, bot_access, bot_extensions, character_cards, characters, content, content_pack_maps, game_controls, game_lifecycle, game_master, game_media, game_packages, game_queries, generated_images, generation, knowledge, kp_questions, logs, map_backgrounds, maps, tavern, turns, worlds, rules, ruleset_advancement, ruleset_builder, ruleset_gameplay, ruleset_rest, plugins, scene_images, speech, system, tunnel, announcements, assistant, hub, legal
from src.webui.services import ruleset_characters
from src.webui.services import memory as memory_service
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
        return await self._legal.document(document_name, language)

    async def fetch_public_content_text(
        self,
        path: str,
        *,
        force_refresh: bool = False,
        allow_cached: bool = True,
    ) -> str:
        return await self._content.fetch_text(
            path,
            force_refresh=force_refresh,
            allow_cached=allow_cached,
        )

    def public_content_disk_age(self, path: str) -> float | None:
        """公共内容磁盘缓存文件的年龄（秒）；无缓存返回 None。"""
        return self._content.disk_cache_age(path)

    async def fetch_public_content_json(
        self,
        path: str,
        *,
        force_refresh: bool = False,
        allow_cached: bool = True,
    ) -> dict[str, Any] | None:
        return await self._content.fetch_json(
            path,
            force_refresh=force_refresh,
            allow_cached=allow_cached,
        )

    async def current_legal_documents(self) -> dict[str, Any]:
        return await self._legal.current_documents()

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
                 adventures_dir: Path | None = None,
                 character_gen_max_tokens: int = 2048,
                 text_gen_max_tokens: int = 1024, plugin_host=None, hub_client=None,
                 speech_service=None, asr_service=None, imagegen_service=None,
                 ruleset_registry: RulesetRuntimeRegistry | None = None,
                 content_cache_dir: Path | None = None,
                 config_state: dict | None = None,
                 save_config: Callable[[], None] | None = None):
        self._reg = registry
        self._lore = lorebook
        self._mem = memory
        self._rules_dir = rules_dir
        self._handler = handler
        self._config_state = config_state if config_state is not None else {}
        self._save_config = save_config or (lambda: None)
        handler_rulesets = getattr(handler, "ruleset_registry", None)
        self._ruleset_registry: RulesetRuntimeRegistry = (
            ruleset_registry or handler_rulesets or build_default_ruleset_registry()
        )
        legacy_world_loader = getattr(handler, "_load_world_template", None)
        self._game_query_dependencies = game_queries.GameQueryDependencies(
            list_instances=self._reg.list_all,
            get_instance=self._reg.get,
            parse_game_key=_parse_game_key,
            load_world_template=(
                legacy_world_loader if callable(legacy_world_loader) else None
            ),
            load_rule_for_game=self._load_rule_for_game,
            ruleset_registry=self._ruleset_registry,
        )
        self._llm_client = llm_client
        self._worlds_dir = worlds_dir or (Path(__file__).parent.parent.parent / "templates" / "worlds")
        self._builtin_adventures_dir = (
            Path(__file__).parent.parent.parent / "templates" / "adventures"
        ).resolve()
        self._adventure_loader = AdventureBundleLoader(
            adventures_dir or self._builtin_adventures_dir
        )
        self._character_cards_path = self._reg.save_dir.parent / "character_cards.json"
        self._character_dependencies = characters.CharacterDependencies(
            games=characters.CharacterGameDependencies(
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
                save_instance=self._reg.save,
            ),
            rules=characters.CharacterRuleDependencies(
                rules_dir=self._rules_dir,
                load_rule_by_id=self._load_rule_by_id,
                load_rule_for_game=self._load_rule_for_game,
                ruleset_registry=self._ruleset_registry,
            ),
            assets=characters.CharacterAssetDependencies(
                lorebook=self._lore,
                load_world_template=self._load_world_template,
                avatar_file=lambda asset_id: self.avatar_file(asset_id),
                generated_image_file=lambda asset_id: self.generated_image_file(
                    asset_id,
                ),
            ),
            save_character_card=lambda character: character_cards.save_character_card(
                self._character_card_dependencies, character,
            ),
            apply_economy_effects=(
                handler.commit_deferred_economy_effects
                if handler is not None
                and callable(getattr(handler, "commit_deferred_economy_effects", None))
                else None
            ),
            schedule_economy_scene_image=(
                handler.schedule_deferred_economy_scene_image
                if handler is not None
                and callable(
                    getattr(handler, "schedule_deferred_economy_scene_image", None)
                )
                else None
            ),
            apply_economy_memory=(
                self._mem.apply_economy_delta if self._mem is not None else None
            ),
            reverse_economy_memory=(
                self._mem.reverse_economy_delta if self._mem is not None else None
            ),
        )
        self._ruleset_character_dependencies = (
            ruleset_characters.RulesetCharacterDependencies(
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
                save_instance=self._reg.save,
                load_rule_by_id=self._load_rule_by_id,
                load_rule_for_game=self._load_rule_for_game,
                ruleset_registry=self._ruleset_registry,
                read_cards=lambda: character_cards._read_cards(
                    self._character_card_dependencies,
                ),
                write_cards=lambda cards: character_cards._write_cards(
                    self._character_card_dependencies, cards,
                ),
                validate_portrait=lambda portrait: characters._validated_portrait(
                    self._character_dependencies.assets, portrait,
                ),
            )
        )
        self._character_card_dependencies = character_cards.CharacterCardDependencies(
            cards_path=self._character_cards_path,
            ruleset_card_metadata=lambda card: ruleset_characters.runtime_metadata_for_card(
                self._ruleset_character_dependencies, card,
            ),
            normalize_ruleset_card=lambda card: ruleset_characters.normalize_character_card_blueprint(
                self._ruleset_character_dependencies, card,
            ),
            is_ruleset_card=lambda card: ruleset_characters.card_has_rules_aware_lifecycle(
                self._ruleset_character_dependencies, card,
            ),
            lorebook=self._lore,
            rebuild_lorebook_index=self._rebuild_lorebook_index,
        )
        self._ruleset_advancement_dependencies = (
            ruleset_advancement.RulesetAdvancementDependencies(
                load_rule_by_id=self._load_rule_by_id,
                ruleset_registry=self._ruleset_registry,
            )
        )
        self._card_advancement_dependencies = (
            ruleset_advancement.CardAdvancementDependencies(
                read_cards=lambda: character_cards._read_cards(
                    self._character_card_dependencies,
                ),
                write_cards=lambda cards: character_cards._write_cards(
                    self._character_card_dependencies, cards,
                ),
                load_rule_by_id=self._load_rule_by_id,
                runtime_for_card=lambda card: ruleset_characters.runtime_for_card(
                    self._ruleset_character_dependencies, card,
                ),
            )
        )
        self._live_advancement_dependencies = (
            ruleset_advancement.LiveAdvancementDependencies(
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
                save_instance=self._reg.save,
                load_rule_for_game=self._load_rule_for_game,
                ruleset_registry=self._ruleset_registry,
            )
        )
        self._ruleset_rest_dependencies = ruleset_rest.RulesetRestDependencies(
            load_rule_by_id=self._load_rule_by_id,
            ruleset_registry=self._ruleset_registry,
        )
        self._live_ruleset_rest_dependencies = ruleset_rest.LiveRulesetRestDependencies(
            get_instance=self._reg.get,
            parse_game_key=_parse_game_key,
            save_instance=self._reg.save,
            load_rule_for_game=self._load_rule_for_game,
            ruleset_registry=self._ruleset_registry,
        )
        self._content = content.PublicContentService(content_cache_dir)
        self._legal = legal.LegalService(
            legal.LegalDependencies(
                fetch_public_json=self.fetch_public_content_json,
                fetch_public_text=self.fetch_public_content_text,
            )
        )
        self._avatars = avatars.AvatarService(self._reg.save_dir.parent / "avatars")
        self._scene_images = scene_images.SceneImageService(
            scene_images.SceneImageDependencies(
                images_dir=self._reg.save_dir.parent / "scene-images",
                load_world_template=self._load_world_template,
                get_rule_template=self.get_rule_template,
                generated_image_file=lambda asset_id: self.generated_image_file(asset_id),
                plugin_asset_path=self.plugin_asset_path,
            )
        )
        self._map_backgrounds = map_backgrounds.MapBackgroundService(
            self._reg.save_dir.parent / "map-backgrounds",
            lambda asset_id: self.generated_image_file(asset_id),
        )
        self._content_map_dependencies = content_pack_maps.ContentMapDependencies(
            resolve_background_file=self.resolve_map_background_file,
        )
        self._memory_service = memory_service.MemoryService(
            memory_service.MemoryDependencies(
                repository=self._mem,
                parse_game_key=_parse_game_key,
                get_instance=self._reg.get,
            )
        )
        self._bot_access = bot_access.BotAccessService(
            bot_access.BotAccessDependencies(
                registry=self._reg,
                parse_game_key=_parse_game_key,
            )
        )
        self._game_logs = logs.GameLogService(
            logs.LogDependencies(
                registry=self._reg,
                parse_game_key=_parse_game_key,
            )
        )
        self._tavern_import = tavern.TavernImportService(
            tavern.TavernImportDependencies(
                lorebook=self._lore,
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
                rebuild_lorebook_index=self._rebuild_lorebook_index,
            )
        )
        self._game_packages = game_packages.GamePackageService(
            game_packages.GamePackageDependencies(
                parse_game_key=_parse_game_key,
                get_instance=self._reg.get,
                state_path_for=self._reg.save_package_state_path,
                import_save_zip=self._reg.import_save_zip,
                resolve_scene_image_file=self.resolve_scene_image_file,
                resolve_map_background_file=self.resolve_map_background_file,
                save_scene_image_upload=self.save_scene_image_upload,
                save_map_background_upload=self.save_map_background_upload,
            )
        )
        self._game_controls = game_controls.GameControlService(
            game_controls.GameControlDependencies(
                parse_game_key=_parse_game_key,
                get_instance=self._reg.get,
                save_instance=self._reg.save,
                load_rule=self._load_rule_for_game,
            )
        )
        self._game_master = game_master.GameMasterService(
            game_master.GameMasterDependencies(
                parse_game_key=_parse_game_key,
                get_instance=self._reg.get,
                save_instance=self._reg.save,
                load_rule=self._load_rule_for_game,
                generate_recap=(
                    self._handler.generate_story_recap
                    if self._handler is not None
                    else None
                ),
                drain_economy_outbox=self._drain_economy_outbox,
            )
        )
        self._game_media = game_media.GameMediaService(
            game_media.GameMediaDependencies(
                parse_game_key=_parse_game_key,
                get_instance=self._reg.get,
                save_instance=self._reg.save,
                load_world_template=(
                    self._load_world_template if self._worlds_dir else None
                ),
                get_lore_world=(
                    self._lore.get_world if self._lore is not None else None
                ),
                refresh_lorebook_index=(
                    self._refresh_game_lorebook_index
                    if self._handler is not None
                    else None
                ),
                resolve_rule_id=self._project_game_rule_id,
                resolve_default_scene_image=self.resolve_default_scene_image,
                materialize_scene_image=self.materialize_scene_image,
            )
        )
        self.character_gen_max_tokens = character_gen_max_tokens
        self.text_gen_max_tokens = text_gen_max_tokens
        self._plugins = plugin_host
        self._plugin_host_dependencies = plugins.PluginHostDependencies(
            plugin_host=self._plugins,
        )
        self._world_dependencies = worlds.WorldDependencies(
            lorebook=self._lore,
            worlds_dir=self._worlds_dir,
            plugin_host=self._plugins,
            llm_client=self._llm_client,
            character_gen_max_tokens=self.character_gen_max_tokens,
            invalidate_lorebook_index=(
                self._handler.invalidate_matcher_for_world
                if self._handler is not None
                else None
            ),
            list_instances=self._reg.list_all,
        )
        self._plugin_content_dependencies = plugins.PluginContentDependencies(
            plugin_host=self._plugins,
            store=plugins.PluginContentStoreDependencies(
                lorebook=self._lore,
                list_games=self._reg.list_all,
                list_character_cards=lambda: character_cards.list_character_cards(
                    self._character_card_dependencies,
                ),
                save_character_card=lambda card: character_cards.save_character_card(
                    self._character_card_dependencies, card,
                ),
                delete_character_card=lambda card_id: character_cards.delete_character_card(
                    self._character_card_dependencies, card_id,
                ),
                save_entry=lambda entry: worlds.save_entry(
                    self._world_dependencies, entry,
                ),
            ),
            portraits=plugins.PluginPortraitDependencies(
                plugin_asset_path=lambda plugin_id, relative_path: plugins.plugin_asset_path(
                    self._plugin_host_dependencies, plugin_id, relative_path,
                ),
                avatar_file=lambda asset_id: self._avatars.file(asset_id),
                generated_image_file=lambda asset_id: self._generated_images.image_file(
                    asset_id,
                ),
                save_avatar_upload=lambda file_data, file_name: self._avatars.save_upload(
                    file_data, file_name,
                ),
            ),
        )
        self._plugin_lifecycle_dependencies = plugins.PluginLifecycleDependencies(
            plugin_host=self._plugins,
            content=self._plugin_content_dependencies,
        )
        self._plugin_export_dependencies = plugins.PluginExportDependencies(
            plugin_host=self._plugins,
            lorebook=self._lore,
            rules_dir=self._rules_dir,
            list_character_cards=lambda: character_cards.list_character_cards(
                self._character_card_dependencies,
            ),
            media=plugins.PluginExportMediaDependencies(
                package_scene_image=lambda reference, files: self._scene_images.package(
                    reference, files,
                ),
                package_content_map=lambda *args, **kwargs: content_pack_maps.package_content_map(
                    self._content_map_dependencies, *args, **kwargs,
                ),
                avatar_file=lambda asset_id: self._avatars.file(asset_id),
                generated_image_file=lambda asset_id: self._generated_images.image_file(
                    asset_id,
                ),
                plugin_asset_path=lambda plugin_id, relative_path: plugins.plugin_asset_path(
                    self._plugin_host_dependencies, plugin_id, relative_path,
                ),
            ),
        )
        self._adventure_dependencies = adventures.AdventureDependencies(
            adventure_loader=self._adventure_loader,
            list_instances=self._reg.list_all,
            load_rule_by_id=self._load_rule_by_id,
            ruleset_registry=self._ruleset_registry,
            default_runtime_requirement=default_adventure_runtime_requirement,
            builtin_adventures_dir=self._builtin_adventures_dir,
        )
        self._ruleset_gameplay_dependencies = (
            ruleset_gameplay.RulesetGameplayDependencies(
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
                load_rule_for_game=self._load_rule_for_game,
                ruleset_registry=self._ruleset_registry,
                resolve_adventure_binding=lambda adventure_id, runtime, world_id, language: adventures.resolve_binding_for_runtime(
                    self._adventure_dependencies,
                    adventure_id,
                    runtime,
                    world_id,
                    language,
                ),
                save_instance=self._reg.save,
                apply_memory_delta=(
                    self._mem.apply_delta if self._mem is not None else None
                ),
            )
        )
        self._turn_dependencies = turns.TurnDependencies(
            get_instance=self._reg.get,
            parse_game_key=_parse_game_key,
            ruleset_registry=self._ruleset_registry,
            load_rule_for_game=self._load_rule_for_game,
            prepare_round_checks_ai=getattr(
                self._handler, "prepare_round_checks_ai", None,
            ),
            prepare_round_checks=getattr(
                self._handler, "prepare_round_checks", None,
            ),
            resolve_pending_dice=self.resolve_pending_dice_for_game,
            roll_for_game=self.roll_for_game,
            save_instance=self._reg.save,
            process_round=getattr(self._handler, "process_round", None),
            resolve_luck_decision=self.resolve_luck_decision,
            decline_pending_luck=self.decline_pending_luck,
            drain_economy_outbox=self._drain_economy_outbox,
            economy_auto_reward_settings=self.economy_auto_reward_settings,
            resolve_reward=self.resolve_reward_as_gm,
        )
        self._map_dependencies = maps.MapDependencies(
            get_instance=self._reg.get,
            parse_game_key=_parse_game_key,
            list_lore_entries=self._lore.list_entries,
            list_map_assets=(
                plugin_host.list_map_assets
                if plugin_host is not None
                else lambda _world_id: {
                    "maps": [],
                    "locations": [],
                    "icons": [],
                    "scenes": [],
                }
            ),
            validate_background_selection=self.validate_map_background_selection,
            save_instance=self._reg.save,
            load_world_template=self._load_world_template,
            map_background_file=self.map_background_file,
            generated_image_file=self.generated_image_file,
        )
        self._assistant_dependencies = assistant.AssistantDependencies(
            list_plugins=self.list_plugins,
            llm_configuration_error=self._llm_configuration_error,
            llm_client=self._llm_client,
            data_dir=self._reg.save_dir.parent,
            text_gen_max_tokens=self.text_gen_max_tokens,
        )
        self._connection_dependencies = generation.ConnectionDependencies(
            llm_client=self._llm_client,
            config_state=lambda: getattr(self, "_config_state", {}) or {},
        )
        self._generation_dependencies = generation.GenerationDependencies(
            llm_client=self._llm_client,
            llm_configuration_error=self._llm_configuration_error,
            worlds_dir=self._worlds_dir,
            lorebook_store=self._lore,
            rules_dir=self._rules_dir,
            registry=self._reg,
            parse_game_key=_parse_game_key,
            get_instance=self._reg.get,
            load_rule_by_id=self._load_rule_by_id,
            load_rule_for_game=self._load_rule_for_game,
            character_gen_max_tokens=self.character_gen_max_tokens,
            text_gen_max_tokens=self.text_gen_max_tokens,
        )
        self._rule_dependencies = rules.RuleDependencies(
            rules_dir=self._rules_dir,
            ruleset_registry=self._ruleset_registry,
            plugin_host=plugin_host,
        )
        self._ruleset_builder_dependencies = ruleset_builder.RulesetBuilderDependencies(
            load_rule=self._load_rule_by_id,
            ruleset_registry=self._ruleset_registry,
        )
        self._tunnel_dependencies = tunnel.TunnelDependencies(
            config_state=lambda: getattr(self, "_config_state", {}),
            save_config=lambda: getattr(self, "_save_config")(),
            list_plugins=self.list_plugins,
        )
        self._bot_extensions = bot_extensions.BotExtensionService(
            bot_extensions.BotExtensionDependencies(plugin_host=plugin_host)
        )
        self._hub = hub_client
        self._hub_service = hub.HubService(
            hub.HubDependencies(
                client=hub_client,
                plugin_host=plugin_host,
                config_state=lambda: getattr(self, "_config_state", {}),
                save_config=lambda: getattr(self, "_save_config")(),
                current_legal_documents=self.current_legal_documents,
                legal_bundle_version=self.legal_bundle_version,
                legal_acceptance_payload=self.legal_acceptance_payload,
                legal_accepted=self.legal_accepted,
                record_legal_acceptance=self.record_legal_acceptance,
            )
        )
        self._speech = speech_service
        self._web_speech = speech.WebSpeechService(
            speech.SpeechDependencies(
                backend=speech_service,
                plugin_host=plugin_host,
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
            )
        )
        self._asr = asr_service
        self._imagegen = imagegen_service
        self._system = system.SystemService(
            system.SystemDependencies(
                data_dir=self._reg.save_dir.parent,
                config_state=lambda: getattr(self, "_config_state", {}) or {},
                proxy_url=lambda: str(
                    getattr(self._llm_client, "proxy_url", "") or ""
                ),
                mirrors=lambda: getattr(self._plugins, "mirrors", None),
            )
        )
        self._announcements = announcements.AnnouncementService(
            announcements.AnnouncementDependencies(
                fetch_public_text=self.fetch_public_content_text,
                disk_cache_age=self.public_content_disk_age,
            )
        )
        self._web_asr = asr.WebAsrService(
            asr.AsrDependencies(
                backend=self._asr,
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
            )
        )
        self._lore_preview = knowledge.LorePreviewService(
            knowledge.LorePreviewDependencies(
                lorebook=self._lore,
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
            )
        )
        kp_answerer = (
            getattr(self._handler, "answer_kp_question", None)
            if self._handler is not None
            else None
        )
        self._kp_questions = kp_questions.KPQuestionService(
            kp_questions.KPQuestionDependencies(
                registry=self._reg,
                parse_game_key=_parse_game_key,
                answer_question=kp_answerer if callable(kp_answerer) else None,
            )
        )
        self._generated_images = generated_images.GeneratedImageService(
            generated_images.GeneratedImageDependencies(
                imagegen=imagegen_service,
                get_instance=self.get_game_instance,
                update_map_background=self.update_map_background,
            )
        )
        self._game_lifecycle = game_lifecycle.GameLifecycleService(
            game_lifecycle.GameLifecycleDependencies(
                registry=self._reg,
                handler=self._handler,
                rulesets=self._ruleset_registry,
                lorebook=self._lore,
                worlds_dir=self._worlds_dir,
                rules_dir=self._rules_dir,
                parse_game_key=_parse_game_key,
                llm_configuration_error=self._llm_configuration_error,
                load_rule_by_id=self._load_rule_by_id,
                resolve_adventure_binding=lambda adventure_id, runtime, world_id, language: adventures.resolve_binding_for_runtime(
                    self._adventure_dependencies,
                    adventure_id,
                    runtime,
                    world_id,
                    language,
                ),
                resolve_default_scene_image=self.resolve_default_scene_image,
                materialize_scene_image=self.materialize_scene_image,
                validate_map_background=self.validate_map_background_selection,
                create_player=lambda *args, **kwargs: self.create_player(*args, **kwargs),
                cleanup_orphan_game_templates=self.cleanup_orphan_game_templates,
                refresh_lorebook_index=self._refresh_game_lorebook_index,
                project_rule_id=lambda instance: game_queries.projected_rule_id(
                    self._game_query_dependencies,
                    instance,
                ),
                clean_public_narration=game_queries.clean_public_narration,
            )
        )
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
        return plugins.list_plugins(self._plugin_host_dependencies)

    async def get_official_announcement(self, language: str = "zh-CN") -> dict[str, Any]:
        return await self._announcements.fetch(language)

    async def hub_preferences(self, language: str = "zh-CN") -> dict[str, Any]:
        return await self._hub_service.preferences(language)

    async def update_hub_preferences(
        self,
        telemetry_enabled: bool,
        legal_acceptance: dict[str, Any] | None = None,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        return await self._hub_service.update_preferences(
            telemetry_enabled, legal_acceptance, language,
        )

    async def delete_hub_identity(self) -> dict[str, Any]:
        return await self._hub_service.delete_identity()

    async def create_rendezvous_room(self, peer_count: int) -> dict[str, Any]:
        return await self._hub_service.create_rendezvous_room(peer_count)

    async def rendezvous_config(self) -> dict[str, Any]:
        return await self._hub_service.rendezvous_config()

    async def hub_plugin_detail(self, plugin_id: str) -> dict[str, Any]:
        return await self._hub_service.plugin_detail(plugin_id)

    async def hub_plugin_readme(self, plugin_id: str) -> dict[str, Any]:
        return await self._hub_service.plugin_readme(plugin_id)

    async def hub_plugin_ratings(self, plugin_id: str) -> dict[str, Any]:
        return await self._hub_service.plugin_ratings(plugin_id)

    async def set_hub_plugin_like(self, plugin_id: str, liked: bool) -> dict[str, Any]:
        return await self._hub_service.set_plugin_like(plugin_id, liked)

    async def set_hub_plugin_rating(
        self, plugin_id: str, stars: int | None, tags: list[str] | None = None
    ) -> dict[str, Any]:
        return await self._hub_service.set_plugin_rating(plugin_id, stars, tags)

    async def assistant_chat(self, response, messages: list[dict], language: str = "zh-CN") -> None:
        return await assistant.chat_stream(
            self._assistant_dependencies,
            response,
            messages,
            language,
        )

    def list_plugin_types(self) -> dict[str, Any]:
        return plugins.list_plugin_types()

    def list_speech_voices(self) -> dict[str, Any]:
        return self._web_speech.list_voices()

    def list_personal_speech_profiles(self) -> dict[str, Any]:
        return self._web_speech.list_personal_profiles()

    def save_personal_speech_profile(
        self,
        profile_id: str,
        values: dict[str, Any],
        *,
        file_data: str = "",
        file_name: str = "",
    ) -> dict[str, Any]:
        return self._web_speech.save_personal_profile(
            profile_id,
            values,
            file_data=file_data,
            file_name=file_name,
        )

    def delete_personal_speech_profile(self, profile_id: str) -> dict[str, Any]:
        return self._web_speech.delete_personal_profile(profile_id)

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
        return await self._web_speech.synthesize(
            game_key, user_id, text, voice, language, speed, owner,
        )

    async def test_speech(
        self,
        text: str,
        voice: str = "",
        language: str = "zh-CN",
        speed: float = 1.0,
    ):
        return await self._web_speech.test_synthesis(
            text, voice, language, speed,
        )

    async def transcribe_speech(
        self,
        game_key: str,
        user_id: str,
        audio: bytes,
        content_type: str,
        language: str = "",
        owner: bool = False,
    ):
        return await self._web_asr.transcribe(
            game_key, user_id, audio, content_type, language, owner,
        )

    async def test_transcription(self, audio: bytes, content_type: str, language: str = ""):
        return await self._web_asr.test_transcription(
            audio, content_type, language,
        )

    async def rescan_plugins(self) -> dict[str, Any]:
        return await plugins.rescan_plugins(self._plugin_host_dependencies)

    def publish_tunnel_url(self, plugin_id: str, url: str) -> dict[str, Any]:
        return tunnel.publish_tunnel_url(self._tunnel_dependencies, plugin_id, url)

    def release_tunnel_url(self, plugin_id: str) -> dict[str, Any]:
        return tunnel.release_tunnel_url(self._tunnel_dependencies, plugin_id)

    def tunnel_status(self) -> dict[str, Any]:
        return tunnel.tunnel_status(self._tunnel_dependencies)

    def authenticate_plugin_token(self, token: str) -> dict[str, Any] | None:
        """校验插件进程内部 Token，返回插件身份（复用 plugin_host）。"""
        if not self._plugins:
            return None
        return self._plugins.authenticate_api_token(token)

    def plugin_detail(self, plugin_id: str) -> dict[str, Any]:
        return plugins.plugin_detail(self._plugin_host_dependencies, plugin_id)

    def read_plugin_docs(self, plugin_id: str) -> dict[str, Any]:
        return plugins.read_plugin_docs(self._plugin_host_dependencies, plugin_id)

    async def update_plugin_config(self, plugin_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        return await plugins.update_plugin_config(
            self._plugin_lifecycle_dependencies, plugin_id, changes,
        )

    async def control_plugin(self, plugin_id: str, action: str) -> dict[str, Any]:
        return await plugins.control_plugin(
            self._plugin_lifecycle_dependencies, plugin_id, action,
        )

    async def install_plugin(self, payload: bytes, overwrite: bool = False) -> dict[str, Any]:
        return await plugins.install_plugin(
            self._plugin_lifecycle_dependencies, payload, overwrite,
        )

    async def list_plugin_marketplace(self) -> dict[str, Any]:
        return await plugins.list_plugin_marketplace(
            self._plugin_host_dependencies,
        )

    async def install_marketplace_plugin(self, plugin_id: str, overwrite: bool = False) -> dict[str, Any]:
        return await plugins.install_marketplace_plugin(
            self._plugin_lifecycle_dependencies, plugin_id, overwrite,
        )

    async def update_marketplace_plugin(self, plugin_id: str) -> dict[str, Any]:
        return await plugins.update_marketplace_plugin(
            self._plugin_host_dependencies, plugin_id,
        )

    async def uninstall_plugin(self, plugin_id: str, delete_data: bool = False) -> dict[str, Any]:
        return await plugins.uninstall_plugin(
            self._plugin_lifecycle_dependencies, plugin_id, delete_data,
        )

    def list_plugin_mirrors(self) -> dict[str, Any]:
        return plugins.list_plugin_mirrors(self._plugin_host_dependencies)

    def add_plugin_mirror(self, data: dict[str, Any]) -> dict[str, Any]:
        return plugins.add_plugin_mirror(self._plugin_host_dependencies, data)

    def update_plugin_mirror(self, mirror_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return plugins.update_plugin_mirror(
            self._plugin_host_dependencies, mirror_id, data,
        )

    def delete_plugin_mirror(self, mirror_id: str) -> dict[str, Any]:
        return plugins.delete_plugin_mirror(
            self._plugin_host_dependencies, mirror_id,
        )

    async def test_plugin_mirror(self, mirror_id: str = "") -> dict[str, Any]:
        return await plugins.test_plugin_mirror(
            self._plugin_host_dependencies, mirror_id,
        )

    def clear_plugin_card_cache(self, plugin_id: str) -> dict[str, Any]:
        return plugins.clear_plugin_card_cache(
            self._plugin_host_dependencies, plugin_id,
        )

    def list_plugin_contributions(self, kind: str = "") -> dict[str, Any]:
        return plugins.list_plugin_contributions(
            self._plugin_host_dependencies, kind,
        )

    def list_plugin_themes(self) -> dict[str, Any]:
        return plugins.list_plugin_themes(self._plugin_host_dependencies)

    def list_plugin_tools(self) -> dict[str, Any]:
        return plugins.list_plugin_tools(self._plugin_host_dependencies)

    async def invoke_plugin_tool(
        self,
        plugin_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await plugins.invoke_plugin_tool(
            self._plugin_host_dependencies,
            plugin_id,
            tool_name,
            arguments,
            context,
        )

    def list_plugin_content(self, kind: str = "", world_id: str = "", rule_id: str = "", language: str = "") -> dict[str, Any]:
        return plugins.list_plugin_content(
            self._plugin_host_dependencies,
            kind,
            world_id,
            rule_id,
            language,
        )

    def sync_plugin_lorebooks(self) -> dict[str, Any]:
        """同步已启用插件的世界模板世界书到世界书库（幂等）。"""
        return plugins.sync_plugin_lorebooks(self._plugin_content_dependencies)

    def cleanup_plugin_lorebook(self, plugin_id: str) -> dict[str, Any]:
        """删除某插件灌入的、未被用户改动的世界书条目。"""
        return plugins.cleanup_plugin_lorebook(
            self._plugin_content_dependencies, plugin_id,
        )

    def import_plugin_content(
        self,
        kind: str,
        resource_id: str,
        plugin_id: str = "",
        target_world_id: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return plugins.import_plugin_content(
            self._plugin_content_dependencies,
            kind,
            resource_id,
            plugin_id,
            target_world_id,
            overwrite,
        )

    def import_all_plugin_content(self, plugin_id: str, target_world_id: str = "") -> dict[str, Any]:
        return plugins.import_all_plugin_content(
            self._plugin_content_dependencies, plugin_id, target_world_id,
        )

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
        language: str = "",
    ) -> dict[str, Any]:
        return plugins.export_content_pack(
            self._plugin_export_dependencies,
            plugin_id, name, version, description, world_id, card_ids, rule_id, flat,
            include_portraits, include_scene_images, world_scene_image, rule_scene_image,
            include_map, map_background, map_icons, language,
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
            self._content_map_dependencies,
            plugin_id,
            pack_name,
            world,
            entries,
            files,
            background_selection=background_selection,
            icon_uploads=icon_uploads,
        )

    def plugin_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        return plugins.plugin_asset_path(
            self._plugin_host_dependencies, plugin_id, relative_path,
        )

    async def check_updates(self, include_prerelease: bool | None = None) -> dict[str, Any]:
        return await self._system.check_updates(include_prerelease)

    def runtime_log_status(self) -> dict[str, Any]:
        return self._system.runtime_log_status()

    def clear_runtime_logs(self) -> dict[str, Any]:
        return self._system.clear_runtime_logs()

    def export_runtime_logs(self) -> tuple[bytes, int]:
        return self._system.export_runtime_logs()

    def _load_world_template(self, world_id: str, language: str = "") -> dict[str, Any] | None:
        """按 world_id 读取世界模板；不存在或非法时返回 None。"""
        if not self._worlds_dir:
            return None
        data = load_world_template(self._worlds_dir, world_id, language)
        if data:
            return data
        if self._plugins:
            return self._plugins.load_world_template(world_id, language)
        return None

    def _load_rule_for_game(self, inst) -> RuleSystem | None:
        """优先按存档自身规则加载；旧存档缺失时回退世界默认规则。"""
        if not inst.world_id or not self._worlds_dir:
            return None
        language = getattr(inst, "language", "") or ""
        world_data = self._load_world_template(inst.world_id, language)
        if not world_data:
            return None
        language = language or world_data.get("active_locale") or world_data.get("language", "")
        rule_id = str(
            getattr(inst, "rule_id", "")
            or world_data.get("default_rule")
            or "freeform_fantasy"
        )
        return self._load_rule_by_id(rule_id, language)

    def _project_game_rule_id(self, instance) -> str:
        return game_queries.projected_rule_id(
            self._game_query_dependencies,
            instance,
        )

    def _load_rule_by_id(self, rule_id: str, language: str = "") -> RuleSystem | None:
        rule_id = (rule_id or "").strip()
        if not rule_id or not rules.is_valid_rule_id(rule_id):
            return None
        core_path = self._rules_dir / f"{rule_id}.json"
        if core_path.exists():
            return RuleSystem(RuleBundleLoader().load_rule(self._rules_dir, rule_id, language))
        if self._plugins:
            item = self._plugins.contributions.find("rule", rule_id)
            if item:
                localized = self._plugins.load_rule_template(
                    rule_id, language, plugin_id=item.plugin_id,
                )
                if localized:
                    return RuleSystem(localized)
                return RuleSystem.load(item.path)
        legacy_path = RuleSystem.path_for(self._rules_dir, rule_id, language)
        return RuleSystem.load(legacy_path) if legacy_path.exists() else None

    # ---- 游戏总览 ----

    def list_games(self) -> dict[str, Any]:
        return game_queries.list_games(self._game_query_dependencies)

    def game_detail(self, game_key: str, viewer_uid: str = "") -> dict[str, Any] | None:
        return game_queries.game_detail(self._game_query_dependencies, game_key, viewer_uid)

    def get_game_instance(self, game_key: str):
        """Resolve a public game key without exposing registry/parser internals."""

        return self._reg.get(_parse_game_key(game_key))

    async def save_game_instance(self, instance) -> None:
        """Persist an already-authorized aggregate through the application facade."""

        await self._reg.save(instance)

    async def generate_game_swipe(self, instance, round_number: int) -> str:
        """Generate an alternate narration without exposing the command handler."""

        return await self._handler.generate_swipe(instance, round_number)

    def saved_game_access(self, game_key: str) -> dict[str, Any]:
        return self._game_packages.saved_game_access(game_key)

    def export_game_package(self, game_key: str) -> dict[str, Any]:
        return self._game_packages.export_game_package(game_key)

    async def import_game_package(self, payload: bytes) -> dict[str, Any]:
        return await self._game_packages.import_game_package(payload)

    def delete_game(self, game_key: str) -> dict[str, Any]:
        return self._game_lifecycle.delete_game(game_key)

    async def get_bot_bind_token(self, game_key: str, rotate: bool = False) -> dict[str, Any]:
        return await self._bot_access.get_bind_token(game_key, rotate)

    async def verify_bot_bind_game(self, game_key: str, bind_token: str) -> dict[str, Any]:
        return await self._bot_access.verify_bind_game(game_key, bind_token)

    def bot_actor_allowed(self, game_key: str, user_id: str) -> bool:
        return self._bot_access.actor_allowed(game_key, user_id)

    def bot_extension_capabilities(self) -> dict[str, Any]:
        return self._bot_extensions.capabilities()

    async def apply_bot_extensions(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._bot_extensions.apply(stage, payload)

    def bot_extension_asset_path(self, plugin_id: str, relative_path: str) -> Path:
        return self._bot_extensions.asset_path(plugin_id, relative_path)

    def bot_bridge_card_path(self, name: str) -> Path:
        return self._bot_extensions.bridge_card_path(name)

    def multiplayer_status(self, game_key: str) -> dict[str, Any]:
        return game_queries.multiplayer_status(
            self._game_query_dependencies,
            game_key,
        )

    def player_context(
        self, *, preview: bool = False, delegate: bool = False, user_id: str = "",
    ) -> dict[str, Any]:
        return game_queries.player_context(
            preview=preview, delegate=delegate, user_id=user_id,
        )

    async def set_player_away(self, game_key: str, user_id: str, away: bool) -> dict[str, Any]:
        return await self._game_controls.set_player_away(game_key, user_id, away)

    async def set_player_access(self, game_key: str, open_access: bool) -> dict[str, Any]:
        return await self._game_controls.set_player_access(game_key, open_access)

    def check_request_for_action(
        self,
        game_key: str,
        user_id: str,
        text: str,
        selected_attribute: str = "",
        selected_skill: str = "",
        target_text: str = "",
    ) -> dict[str, Any] | None:
        return self._game_controls.check_request_for_action(
            game_key, user_id, text, selected_attribute, selected_skill, target_text
        )

    def roll_for_game(self, game_key: str) -> dict[str, Any]:
        return self._game_controls.roll_for_game(game_key)

    async def resolve_pending_dice_for_game(self, game_key: str, user_id: str = "", source: str = "system") -> dict[str, Any]:
        return await self._game_controls.resolve_pending_dice(game_key, user_id, source)

    async def resolve_luck_decision(self, game_key: str, check_id: str, actor_uid: str, spend: bool) -> dict[str, Any]:
        return await self._game_controls.resolve_luck_decision(
            game_key, check_id, actor_uid, spend,
        )

    async def decline_pending_luck(self, game_key: str) -> dict[str, Any]:
        return await self._game_controls.decline_pending_luck(game_key)

    async def submit_action(self, game_key: str, actor_uid: str, text: str, **kwargs) -> turns.TurnResult:
        return await turns.submit_action(
            self._turn_dependencies,
            game_key,
            actor_uid,
            text,
            **kwargs,
        )

    async def ask_kp_question(
        self,
        game_key: str,
        actor_uid: str,
        question: str,
        visibility: Literal["private", "party"] = "private",
    ) -> kp_questions.KPQuestionResult:
        return await self._kp_questions.ask(
            game_key, actor_uid, question, visibility,
        )

    async def resolve_luck_and_continue(
        self,
        game_key: str,
        check_id: str,
        actor_uid: str,
        spend: bool,
        **kwargs,
    ) -> turns.TurnResult:
        return await turns.resolve_luck_and_continue(
            self._turn_dependencies,
            game_key,
            check_id,
            actor_uid,
            spend,
            **kwargs,
        )

    async def advance_turn(self, game_key: str, actor_uid: str, **kwargs) -> turns.TurnResult:
        return await turns.advance_round(
            self._turn_dependencies,
            game_key,
            actor_uid,
            **kwargs,
        )

    def private_log(self, game_key: str) -> dict[str, Any]:
        return game_queries.private_log(self._game_query_dependencies, game_key)

    def private_log_for_user(self, game_key: str, user_id: str) -> dict[str, Any]:
        return game_queries.private_log_for_user(
            self._game_query_dependencies,
            game_key,
            user_id,
        )

    def table_talk(self, game_key: str) -> dict[str, Any]:
        return game_queries.table_talk(self._game_query_dependencies, game_key)

    def game_health(self, game_key: str, include_resolved: bool = False) -> dict[str, Any]:
        return game_queries.game_health(
            self._game_query_dependencies,
            game_key,
            include_resolved,
        )

    async def set_solo_mode(self, game_key: str, solo: bool) -> dict[str, Any]:
        return await self._game_controls.set_solo_mode(game_key, solo)

    async def set_narrative_perspective(
        self, game_key: str, perspective: str,
    ) -> dict[str, Any]:
        return await self._game_controls.set_narrative_perspective(
            game_key, perspective,
        )

    async def mark_game_health_event(
        self,
        game_key: str,
        event_id: str,
        *,
        resolved: bool = False,
        ignored: bool = False,
    ) -> dict[str, Any]:
        return await self._game_controls.mark_health_event(
            game_key, event_id, resolved=resolved, ignored=ignored,
        )

    async def gm_command(self, game_key: str, command: str, mode: str = "note") -> dict[str, Any]:
        return await self._game_master.command(game_key, command, mode)

    async def rollback_round(self, game_key: str) -> dict[str, Any]:
        return await self._game_master.rollback_round(game_key)

    async def generate_story_recap(self, game_key: str) -> dict[str, Any]:
        return await self._game_master.generate_story_recap(game_key)

    async def gm_private_message(self, game_key: str, user_id: str, text: str) -> dict[str, Any]:
        return await self._game_master.private_message(game_key, user_id, text)

    # ---- 角色卡库 ----

    def list_character_cards(self) -> dict[str, Any]:
        return character_cards.list_character_cards(self._character_card_dependencies)

    def save_character_card(self, character: dict) -> dict[str, Any]:
        return character_cards.save_character_card(
            self._character_card_dependencies, character,
        )

    def update_character_card(self, card_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return character_cards.update_character_card(
            self._character_card_dependencies, card_id, patch,
        )

    def delete_character_card(self, card_id: str) -> dict[str, Any]:
        return character_cards.delete_character_card(
            self._character_card_dependencies, card_id,
        )

    async def import_character_card(self, file_data: str = "", file_name: str = "card.json",
                                    target: str = "character_card", world_id: str = "") -> dict[str, Any]:
        return await character_cards.import_character_card(
            self._character_card_dependencies,
            file_data,
            file_name,
            target,
            world_id,
        )

    def export_character_cards(self, card_ids: list[str]) -> dict[str, Any]:
        return character_cards.export_character_cards(
            self._character_card_dependencies, card_ids,
        )

    def update_ruleset_character_card_profile(
        self, card_id: str, patch: dict[str, Any],
    ) -> dict[str, Any]:
        return ruleset_characters.update_character_card_profile(
            self._ruleset_character_dependencies, card_id, patch,
        )

    def preview_character_card_advancement(
        self, card_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return ruleset_advancement.preview_card(
            self._card_advancement_dependencies, card_id, body,
        )

    def apply_character_card_advancement(
        self, card_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return ruleset_advancement.apply_card(
            self._card_advancement_dependencies, card_id, body,
        )

    # ---- 世界编辑器 ----

    def list_worlds(self) -> dict[str, Any]:
        # 确保已启用插件的世界模板世界书已同步（幂等）
        if self._plugins:
            try:
                plugins.sync_plugin_lorebooks(self._plugin_content_dependencies)
            except Exception:
                logger.warning("list_worlds 同步插件世界书失败，已跳过", exc_info=True)
        return worlds.list_worlds(self._world_dependencies)

    def create_world(self, name: str, description: str = "", language: str = "") -> dict[str, Any]:
        return worlds.create_world(
            self._world_dependencies, name, description, language,
        )

    def clone_world_from_template(self, template_id: str, name: str = "") -> dict[str, Any]:
        return worlds.clone_world_from_template(
            self._world_dependencies, template_id, name,
        )

    def update_world_gm_style(self, world_id: str, raw: dict | None = None) -> dict[str, Any]:
        return worlds.update_world_gm_style(
            self._world_dependencies, world_id, raw,
        )

    def set_user_world_scene_image(self, world_id: str, scene_image: dict | None = None) -> dict[str, Any]:
        return worlds.set_user_world_scene_image(
            self._world_dependencies, world_id, scene_image,
        )

    def list_entries(self, world_id: str, entry_type: str | None = None) -> dict[str, Any]:
        return worlds.list_entries(
            self._world_dependencies, world_id, entry_type,
        )

    def search_entries(self, world_id: str, keyword: str) -> dict[str, Any]:
        return worlds.search_entries(self._world_dependencies, world_id, keyword)

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        return worlds.get_entry(self._world_dependencies, entry_id)

    def save_entry(self, entry: dict) -> dict[str, Any]:
        return worlds.save_entry(self._world_dependencies, entry)

    def import_entries(self, world_id: str, entries: list) -> dict[str, Any]:
        return worlds.import_entries(self._world_dependencies, world_id, entries)

    async def generate_lorebook_entries(self, world_id: str, prompt: str, language: str = "") -> dict[str, Any]:
        return await worlds.generate_lorebook_entries(
            self._world_dependencies, world_id, prompt, language,
        )

    def update_entry(self, entry_id: str, updates: dict) -> dict[str, Any]:
        return worlds.update_entry(self._world_dependencies, entry_id, updates)

    def delete_entry(self, entry_id: str) -> dict[str, Any]:
        return worlds.delete_entry(self._world_dependencies, entry_id)

    def delete_world(self, world_id: str) -> dict[str, Any]:
        return worlds.delete_world(self._world_dependencies, world_id)

    def preview_lore_visibility(
        self,
        world_id: str,
        viewer: str,
        game_key: str | None = None,
    ) -> dict[str, Any]:
        return self._lore_preview.preview(world_id, viewer, game_key)

    def _rebuild_lorebook_index(self, world_id: str) -> None:
        worlds.rebuild_lorebook_index(self._world_dependencies, world_id)

    def _refresh_game_lorebook_index(self, world_id: str) -> None:
        self._handler._last_matcher_world_id = None
        self._rebuild_lorebook_index(world_id)

    # ---- 角色管理 ----

    def list_characters(self, game_key: str) -> dict[str, Any]:
        return characters.list_characters(self._character_dependencies, game_key)

    def character_schema(self, rule_id: str, language: str = "") -> dict[str, Any]:
        return characters.character_schema(
            self._character_dependencies, rule_id, language,
        )

    def get_character(self, game_key: str, user_id: str) -> dict[str, Any] | None:
        return characters.get_character(
            self._character_dependencies, game_key, user_id,
        )

    async def update_character(self, game_key: str, user_id: str, updates: dict) -> dict[str, Any]:
        return await characters.update_character(
            self._character_dependencies, game_key, user_id, updates,
        )

    async def update_ruleset_character_profile(
        self, game_key: str, user_id: str, patch: dict[str, Any],
    ) -> dict[str, Any]:
        return await ruleset_characters.update_live_character_profile(
            self._ruleset_character_dependencies, game_key, user_id, patch,
        )

    async def adopt_ruleset_character_card(
        self, game_key: str, user_id: str, card_id: str,
    ) -> dict[str, Any]:
        return await ruleset_characters.adopt_character_card(
            self._ruleset_character_dependencies, game_key, user_id, card_id,
        )

    def preview_live_character_advancement(
        self, game_key: str, user_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return ruleset_advancement.preview_live(
            self._live_advancement_dependencies, game_key, user_id, body,
        )

    async def apply_live_character_advancement(
        self, game_key: str, user_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return await ruleset_advancement.apply_live(
            self._live_advancement_dependencies, game_key, user_id, body,
        )

    def live_advancement_status(self, game_key: str) -> dict[str, Any]:
        return ruleset_advancement.live_status(
            self._live_advancement_dependencies, game_key,
        )

    async def control_live_advancement(
        self, game_key: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return await ruleset_advancement.control_live(
            self._live_advancement_dependencies, game_key, body,
        )

    async def update_npc_portrait(self, game_key: str, npc_id: str, portrait: Any) -> dict[str, Any]:
        return await characters.update_npc_portrait(
            self._character_dependencies, game_key, npc_id, portrait,
        )

    async def resolve_payment(self, game_key: str, payment_id: str, accepted: bool, session_uid: str = "") -> dict[str, Any]:
        return await characters.resolve_payment(
            self._character_dependencies,
            game_key,
            payment_id,
            accepted,
            session_uid,
        )

    async def resolve_reward_as_gm(self, game_key: str, payment_id: str, session_uid: str = "") -> dict[str, Any]:
        """Settle one narrative reward through the standard payment-confirm path.

        Used by the round-completion auto-settler: the authority chain
        (ledger, deferred effects, save) is the manual confirmation path —
        only the GM click is removed for qualifying rewards.
        """

        return await self.resolve_payment(game_key, payment_id, True, session_uid)

    def economy_auto_reward_settings(self) -> tuple[bool, int]:
        """Live economy auto-reward switch and gold cap from runtime config."""

        state = self._config_state if isinstance(self._config_state, dict) else {}
        return (
            bool(state.get("economy_auto_reward_enabled", True)),
            # 与 runtime_config 默认保持一致：放宽到 10000，配置可覆盖。
            max(1, int(state.get("economy_auto_reward_gold_cap", 10000) or 10000)),
        )

    async def create_payment_proposal(
        self,
        game_key: str,
        *,
        payer_uid: str,
        amount: int,
        reason: str = "",
        recipient_uid: str = "",
        items: list[str] | None = None,
    ) -> dict[str, Any]:
        return await characters.create_payment_proposal(
            self._character_dependencies,
            game_key,
            payer_uid=payer_uid,
            amount=amount,
            reason=reason,
            recipient_uid=recipient_uid,
            items=items,
        )

    async def _drain_economy_outbox(self, instance: Any) -> bool:
        return await characters.drain_economy_outbox(
            self._character_dependencies, instance,
        )

    async def drain_economy_outbox(self, game_key: str) -> bool:
        instance = self.get_game_instance(game_key)
        if instance is None:
            return False
        return await self._drain_economy_outbox(instance)

    async def recover_economy_outboxes(self, instances: list[Any]) -> int:
        recovered = 0
        for instance in instances:
            had_pending = bool(
                pending_memory_deliveries(instance)
                or pending_memory_reversals(instance)
            )
            if had_pending and await self._drain_economy_outbox(instance):
                recovered += 1
        return recovered

    async def delete_character(self, game_key: str, user_id: str) -> dict[str, Any]:
        return await characters.delete_character(
            self._character_dependencies, game_key, user_id,
        )

    async def create_player(self, game_key: str, character: dict,
                           force_uid: str = "", assign_new_id: bool = False) -> dict[str, Any]:
        return await characters.create_player(
            self._character_dependencies,
            game_key,
            character,
            force_uid,
            assign_new_id,
        )

    def save_avatar_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        return self._avatars.save_upload(file_data, file_name)

    def avatar_file(self, asset_id: str) -> Path | None:
        return self._avatars.file(asset_id)

    def list_user_avatars(self) -> dict[str, Any]:
        return self._avatars.list_user_avatars()

    def delete_avatar(self, asset_id: str) -> dict[str, Any]:
        return self._avatars.delete(asset_id)

    def image_generation_status(self) -> dict[str, Any]:
        return self._generated_images.public_config()

    async def generate_generated_image(self, **request: Any) -> dict[str, Any]:
        return await self._generated_images.generate_image(**request)

    def list_game_generated_images(
        self, game_key: str, user_id: str, *, purpose: str = "",
    ) -> list[dict[str, Any]]:
        return self._generated_images.list_game_images(
            game_key, user_id, purpose=purpose,
        )

    async def use_generated_image_as_map_background(
        self, game_key: str, user_id: str, asset_id: str,
    ) -> dict[str, Any]:
        return await self._generated_images.use_as_map_background(
            game_key, user_id, asset_id,
        )

    def generated_image_file(self, asset_id: str) -> Path | None:
        return self._generated_images.image_file(asset_id)

    def save_scene_image_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        return self._scene_images.save_upload(file_data, file_name)

    def scene_image_file(self, asset_id: str) -> Path | None:
        return self._scene_images.file(asset_id)

    def validate_scene_image_ref(self, reference: Any) -> dict[str, str]:
        return self._scene_images.validate(reference)

    def resolve_default_scene_image(self, world_id: str = "", rule_id: str = "") -> dict[str, str]:
        return self._scene_images.resolve_default(world_id, rule_id)

    def materialize_scene_image(self, reference: Any) -> dict[str, str]:
        return self._scene_images.materialize(reference)

    def resolve_scene_image_file(self, reference: Any) -> Path | None:
        return self._scene_images.resolve_file(reference)

    def package_scene_image(
        self,
        reference: Any,
        files: dict[str, str | bytes],
    ) -> dict[str, str] | None:
        return self._scene_images.package(reference, files)

    def save_map_background_upload(self, file_data: str, file_name: str = "") -> dict[str, Any]:
        return self._map_backgrounds.save_upload(file_data, file_name)

    def map_background_file(self, asset_id: str) -> Path | None:
        return self._map_backgrounds.file(asset_id)

    def validate_map_background_selection(self, selection: Any) -> dict[str, str]:
        return self._map_backgrounds.validate(selection)

    def resolve_map_background_file(self, selection: Any) -> Path | None:
        return self._map_backgrounds.resolve_file(selection)

    # ---- 剧情日志 ----

    def get_log(
        self,
        game_key: str,
        page: int = 1,
        per_page: int = 50,
        include_internal: bool = False,
    ) -> dict[str, Any]:
        return self._game_logs.get_log(
            game_key, page, per_page, include_internal,
        )

    def get_statistics(self, game_key: str) -> dict[str, Any]:
        return self._game_logs.get_statistics(game_key)

    # ---- 规则配置 ----

    def list_rules(self, language: str = "") -> dict[str, Any]:
        return rules.list_rules(self._rule_dependencies, language)

    def save_custom_rule(self, data: dict[str, Any]) -> dict[str, Any]:
        return rules.save_custom_rule(self._rule_dependencies, data)

    def get_rule_template(self, rule_id: str, language: str = "") -> dict[str, Any]:
        return rules.get_rule_template(self._rule_dependencies, rule_id, language)

    def update_custom_rule(self, rule_id: str, template: dict[str, Any]) -> dict[str, Any]:
        return rules.update_custom_rule(
            self._rule_dependencies,
            rule_id,
            template,
        )

    def delete_custom_rule(self, rule_id: str) -> dict[str, Any]:
        return rules.delete_custom_rule(self._rule_dependencies, rule_id)

    def ruleset_experience(self, rule_id: str, language: str = "") -> dict[str, Any]:
        return ruleset_builder.experience(
            self._ruleset_builder_dependencies, rule_id, language
        )

    def ruleset_builder_choices(
        self, rule_id: str, draft: Any, language: str = "",
    ) -> dict[str, Any]:
        return ruleset_builder.choices(
            self._ruleset_builder_dependencies, rule_id, draft, language
        )

    def ruleset_builder_validate(
        self, rule_id: str, draft: Any, language: str = "",
    ) -> dict[str, Any]:
        return ruleset_builder.validate(
            self._ruleset_builder_dependencies, rule_id, draft, language
        )

    def ruleset_builder_derive(
        self, rule_id: str, draft: Any, language: str = "",
    ) -> dict[str, Any]:
        return ruleset_builder.derive(
            self._ruleset_builder_dependencies, rule_id, draft, language
        )

    def ruleset_builder_finalize(
        self, rule_id: str, draft: Any, language: str = "",
    ) -> dict[str, Any]:
        return ruleset_builder.finalize(
            self._ruleset_builder_dependencies, rule_id, draft, language
        )

    def ruleset_progression(
        self, rule_id: str, class_ref: str, start_level: int = 1,
        end_level: int = 20, language: str = "",
    ) -> dict[str, Any]:
        return ruleset_advancement.progression(
            self._ruleset_advancement_dependencies,
            rule_id, class_ref, start_level, end_level, language,
        )

    def ruleset_advancement_preview(
        self, rule_id: str, body: dict[str, Any], language: str = "",
    ) -> dict[str, Any]:
        return ruleset_advancement.preview(
            self._ruleset_advancement_dependencies, rule_id, body, language,
        )

    def ruleset_advancement_apply(
        self, rule_id: str, body: dict[str, Any], language: str = "",
    ) -> dict[str, Any]:
        return ruleset_advancement.apply(
            self._ruleset_advancement_dependencies, rule_id, body, language,
        )

    def ruleset_rest_resolve(
        self, rule_id: str, body: dict[str, Any], language: str = "",
    ) -> dict[str, Any]:
        return ruleset_rest.resolve(
            self._ruleset_rest_dependencies, rule_id, body, language,
        )

    async def ruleset_rest_resolve_live(
        self, game_key: str, user_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return await ruleset_rest.resolve_live(
            self._live_ruleset_rest_dependencies, game_key, user_id, body,
        )

    async def ruleset_rest_resolve_live_party(
        self, game_key: str, user_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return await ruleset_rest.resolve_live_party(
            self._live_ruleset_rest_dependencies, game_key, user_id, body,
        )

    async def ruleset_available_actions(
        self, game_key: str, requester_id: str, requester_is_gm: bool = False,
    ) -> dict[str, Any]:
        return await ruleset_gameplay.available_actions(
            self._ruleset_gameplay_dependencies,
            game_key,
            requester_id,
            requester_is_gm,
        )

    async def ruleset_submit_intent(
        self, game_key: str, requester_id: str, requester_is_gm: bool,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return await ruleset_gameplay.submit_intent(
            self._ruleset_gameplay_dependencies,
            game_key,
            requester_id,
            requester_is_gm,
            body,
        )

    # ---- 世界模板 ----

    def list_adventures(
        self, rule_id: str = "", world_id: str = "", language: str = "",
    ) -> dict[str, Any]:
        return adventures.list_adventures(
            self._adventure_dependencies,
            rule_id,
            world_id,
            language,
        )

    def adventure_detail(self, adventure_id: str, language: str = "") -> dict[str, Any]:
        return adventures.adventure_detail(
            self._adventure_dependencies,
            adventure_id,
            language,
        )

    def copy_adventure(
        self, adventure_id: str, body: dict[str, Any], language: str = "",
    ) -> dict[str, Any]:
        return adventures.copy_adventure(
            self._adventure_dependencies,
            adventure_id,
            body,
            language,
        )

    def create_adventure(
        self, body: dict[str, Any], language: str = "",
    ) -> dict[str, Any]:
        return adventures.create_adventure(
            self._adventure_dependencies,
            body,
            language,
        )

    def update_adventure(
        self, adventure_id: str, body: dict[str, Any], language: str = "",
    ) -> dict[str, Any]:
        return adventures.update_adventure(
            self._adventure_dependencies,
            adventure_id,
            body,
            language,
        )

    def delete_adventure(self, adventure_id: str) -> dict[str, Any]:
        return adventures.delete_adventure(
            self._adventure_dependencies,
            adventure_id,
        )

    def export_adventure(self, adventure_id: str) -> tuple[str, bytes]:
        return adventures.export_adventure(
            self._adventure_dependencies,
            adventure_id,
        )

    def import_adventure(
        self, payload: bytes, directory_id: str = "",
    ) -> dict[str, Any]:
        return adventures.import_adventure(
            self._adventure_dependencies,
            payload,
            directory_id,
        )

    def list_world_templates(self, language: str = "") -> dict[str, Any]:
        # 确保已启用插件的世界模板世界书已同步（幂等）
        if self._plugins:
            try:
                plugins.sync_plugin_lorebooks(self._plugin_content_dependencies)
            except Exception:
                logger.warning("list_world_templates 同步插件世界书失败，已跳过", exc_info=True)
        return worlds.list_world_templates(self._world_dependencies, language)

    def cleanup_orphan_game_templates(self, world_id: str = "") -> int:
        return worlds.cleanup_orphan_game_templates(
            self._world_dependencies, world_id,
        )

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
                           map_background: dict[str, Any] | None = None,
                           adventure_id: str = "",
                           narrative_perspective: str = "auto",
                           advancement_mode: str = "milestone",
                           advancement_authority: str = "ai_gm") -> dict[str, Any]:
        return await self._game_lifecycle.create_game(
            world_id=world_id, game_name=game_name, group_name=group_name,
            rule_id=rule_id, solo=solo, lorebook_world_id=lorebook_world_id,
            difficulty=difficulty, description=description,
            create_lorebook=create_lorebook, blank_lorebook=blank_lorebook,
            source_world_id=source_world_id, players=players,
            custom_world=custom_world, gm_uid=gm_uid,
            room_password=room_password, language=language,
            scene_image=scene_image, map_background=map_background,
            adventure_id=adventure_id,
            narrative_perspective=narrative_perspective,
            advancement_mode=advancement_mode,
            advancement_authority=advancement_authority,
        )

    # ---- 重开引用码 ----

    async def reset_game(self, game_key: str) -> dict[str, Any]:
        return await self._game_lifecycle.reset_game(game_key)

    async def restart_game(self, game_key: str) -> dict[str, Any]:
        return await self._game_lifecycle.restart_game(game_key)

    async def switch_world(self, game_key: str, world_id: str) -> dict[str, Any]:
        return await self._game_media.switch_world(game_key, world_id)

    async def create_from_seed(self, seed_code: str, solo: bool = False,
                               players: list[dict] | None = None,
                               gm_uid: str = "",
                               language: str = "",
                               scene_image: dict[str, Any] | None = None,
                               narrative_perspective: str = "") -> dict[str, Any]:
        return await self._game_lifecycle.create_from_seed(
            seed_code=seed_code, solo=solo, players=players, gm_uid=gm_uid,
            language=language, scene_image=scene_image,
            narrative_perspective=narrative_perspective,
        )

    async def update_scene_image(
        self,
        game_key: str,
        reference: dict[str, Any] | None = None,
        *,
        use_default: bool = False,
    ) -> dict[str, Any]:
        return await self._game_media.update_scene_image(
            game_key, reference, use_default=use_default,
        )

    async def update_map_background(
        self,
        game_key: str,
        selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await maps.update_map_background(
            self._map_dependencies,
            game_key,
            selection,
        )

    # ---- AI 生成 ----

    async def test_connection(self, base_url: str, api_key: str,
                              model: str, proxy_url: str = "",
                              api_format: str = "openai") -> dict[str, Any]:
        return await generation.test_connection(
            self._connection_dependencies,
            base_url,
            api_key,
            model,
            proxy_url,
            api_format,
        )

    async def list_models(self, base_url: str, api_key: str,
                          proxy_url: str = "", api_format: str = "openai") -> dict[str, Any]:
        return await generation.list_models(
            self._connection_dependencies,
            base_url,
            api_key,
            proxy_url,
            api_format,
        )

    async def generate_world(self, prompt: str, rule_id: str = "", language: str = "") -> dict[str, Any]:
        return await generation.generate_world(
            self._generation_dependencies,
            prompt,
            rule_id,
            language,
        )

    async def generate_rule(self, prompt: str, source_rule_id: str = "", language: str = "") -> dict[str, Any]:
        return await generation.generate_rule(
            self._generation_dependencies,
            prompt,
            source_rule_id,
            language,
        )

    async def generate_character(self, prompt: str, game_key: str = "", rule_id: str = "", language: str = "") -> dict[str, Any]:
        return await generation.generate_character(
            self._generation_dependencies,
            prompt,
            game_key,
            rule_id,
            language,
        )

    async def generate_text(self, prompt: str, system_hint: str = "", language: str = "") -> dict[str, Any]:
        return await generation.generate_text(
            self._generation_dependencies,
            prompt,
            system_hint,
            language,
        )

    # ---- 内存 ----

    def list_memories(self, game_key: str, keyword: str = "",
                      limit: int = 20, offset: int = 0) -> dict[str, Any]:
        return self._memory_service.list(game_key, keyword, limit, offset)

    async def update_memory(self, game_key: str, entry_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        return await self._memory_service.update(game_key, entry_id, updates)

    async def delete_memory(self, game_key: str, entry_id: int) -> dict[str, Any]:
        return await self._memory_service.delete(game_key, entry_id)

    async def import_tavern_card(self, file_path: str = "", file_data: str = "",
                                 file_name: str = "card.png", game_key: str = "") -> dict[str, Any]:
        return await self._tavern_import.import_card(
            file_path, file_data, file_name, game_key,
        )

    # ----

    def get_map_locations(self, game_key: str) -> dict[str, Any]:
        return maps.get_map_locations(self._map_dependencies, game_key)

    def map_background_asset(self, game_key: str, asset_id: str) -> Path | None:
        return maps.map_background_asset(
            self._map_dependencies,
            game_key,
            asset_id,
        )

    @staticmethod
    def _parse_key(game_key: str) -> tuple:
        return _parse_game_key(game_key)
