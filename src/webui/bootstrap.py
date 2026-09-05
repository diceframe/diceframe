"""WebUI startup, background-task, and cleanup lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from pathlib import Path
import signal
from typing import Any

from aiohttp import web

from src.ai_providers import resolve_provider
from src.adventures import sync_adventure_catalog
from src.common_factory import TRPGSubsystems
from src.hub_client import HubClient
from src.plugin_host import PluginHost
from src.template_catalog import sync_template_catalog
from src.web_transport import ServerTransport
from src.webui.assistant_knowledge import prefetch_remote_indexes
from src.webui.host_credentials import HostCredentials
from src.webui.services import legal as legal_svc
from src.webui.services import updater as updater_svc


@dataclass(frozen=True)
class BootstrapPaths:
    root: Path
    data_dir: Path
    builtin_rules_dir: Path
    builtin_worlds_dir: Path
    builtin_adventures_dir: Path
    rules_dir: Path
    worlds_dir: Path
    adventures_dir: Path


@dataclass(frozen=True)
class BootstrapDependencies:
    paths: BootstrapPaths
    state: dict
    environ: Mapping[str, str]
    transport: ServerTransport
    generation_defaults_migrated: bool
    credentials: Callable[[], HostCredentials]
    save_config: Callable[[], None]
    build_subsystems: Callable[..., TRPGSubsystems]
    make_api: Callable[..., Any]
    activate_api_runtime: Callable[[TRPGSubsystems, Any], None]


class WebUIBootstrap:
    def __init__(
        self,
        dependencies: BootstrapDependencies,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.dependencies = dependencies
        self.logger = logger or logging.getLogger("trpg")

    async def periodic_save(self, app: web.Application) -> None:
        while True:
            await asyncio.sleep(60)
            subsystems: TRPGSubsystems | None = app.get("subsystems")
            if subsystems:
                try:
                    await subsystems.registry.save_all_active()
                except Exception:
                    self.logger.exception("定时保存失败")

    async def embed_pending_memories(self, app: web.Application) -> None:
        state = self.dependencies.state
        if not state.get("embedding_enabled", False):
            return
        subsystems: TRPGSubsystems | None = app.get("subsystems")
        if not subsystems or not subsystems.memory_store or not subsystems.memory_store.embedding_client:
            return
        try:
            for instance in subsystems.registry.list_all():
                count = await subsystems.memory_store.embed_all_pending(
                    str(instance.game_key)
                )
                if count:
                    self.logger.info(
                        "[Embedding] %s: backfilled %d pending memories",
                        instance.world_name,
                        count,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("Embedding backfill failed")

    async def on_startup(self, app: web.Application) -> None:
        dependencies = self.dependencies
        state = dependencies.state
        self.sync_builtin_templates()
        if dependencies.generation_defaults_migrated:
            dependencies.save_config()
            self.logger.warning("已迁移 generation 默认值到新版本配置")
        dependencies.credentials().initialize_access_password()
        dependencies.credentials().ensure_bot_token()
        subsystems = dependencies.build_subsystems()
        app["subsystems"] = subsystems
        hub_client = None
        try:
            legal_accepted = legal_svc.accepted(state)
            hub_client = HubClient(
                dependencies.paths.data_dir,
                telemetry_enabled=bool(state.get("hub_telemetry_enabled"))
                and legal_accepted,
                telemetry_choice_made=bool(
                    state.get("hub_telemetry_choice_made")
                )
                and legal_accepted,
            )
            await hub_client.start()
        except ValueError as exc:
            self.logger.warning("DiceFrame Hub 配置无效，已停用 Hub 接入：%s", exc)
        app["hub_client"] = hub_client

        async def on_plugin_stopped(plugin_id: str) -> None:
            api = app.get("api")
            if api is not None:
                api.release_tunnel_url(plugin_id)

        plugin_host = PluginHost(
            dependencies.paths.data_dir / "plugin-packages",
            dependencies.paths.data_dir / "plugins",
            builtin_dir=dependencies.paths.root / "plugins",
            base_env={
                "TRPG_API_BASE": dependencies.transport.endpoint.url("127.0.0.1")
            },
            on_plugin_stopped=on_plugin_stopped,
            hub_client=hub_client,
            ai_provider_resolver=lambda provider_id: resolve_provider(
                state, provider_id
            ),
        )
        self._migrate_portable_plugin_packages(plugin_host)
        plugin_host.discover()
        if "qq-napcat" in plugin_host.plugins:
            plugin_host.migrate_config(
                "qq-napcat",
                {
                    "enabled": state.get("qq_bot_enabled", False),
                    "host": state.get("napcat_host"),
                    "port": state.get("napcat_port"),
                    "token": state.get("napcat_token"),
                    "heartbeat_sec": state.get("napcat_heartbeat_sec"),
                    "reconnect_delay_sec": state.get(
                        "napcat_reconnect_delay_sec"
                    ),
                    "action_timeout_sec": state.get("napcat_action_timeout_sec"),
                    "reply_delay_min_sec": state.get("napcat_reply_delay_min_sec"),
                    "reply_delay_max_sec": state.get("napcat_reply_delay_max_sec"),
                    "command_dedup_window_sec": state.get(
                        "napcat_command_dedup_window_sec"
                    ),
                    "connection_id": state.get("napcat_connection_id"),
                    "chat_filter_enabled": state.get(
                        "napcat_chat_filter_enabled"
                    ),
                    "show_dropped_logs": state.get("napcat_show_dropped_logs"),
                    "group_list_mode": state.get("napcat_group_list_mode"),
                    "group_list": state.get("napcat_group_list"),
                    "private_list_mode": state.get("napcat_private_list_mode"),
                    "private_list": state.get("napcat_private_list"),
                    "blocked_users": state.get("napcat_blocked_users"),
                    "block_official_bots": state.get(
                        "napcat_block_official_bots"
                    ),
                },
            )
        app["plugin_host"] = plugin_host
        app["api"] = dependencies.make_api(
            subsystems,
            plugin_host,
            hub_client=hub_client,
        )
        dependencies.activate_api_runtime(subsystems, app["api"])
        app["updater"] = updater_svc.UpdaterService(
            updater_svc.UpdaterDependencies(
                data_dir=dependencies.paths.data_dir,
                root=dependencies.paths.root,
                mirrors=plugin_host.mirrors if plugin_host else None,
                check_updates=app["api"].check_updates,
            )
        )
        recovered = await subsystems.registry.recover_all()
        if recovered:
            self.logger.info("恢复了 %d 个存档", len(recovered))
            delivered = await app["api"].recover_economy_outboxes(recovered)
            if delivered:
                self.logger.info("恢复了 %d 个对局的经济外部效果", delivered)
        removed_templates = app["api"].cleanup_orphan_game_templates()
        if removed_templates:
            self.logger.info(
                "已清理 %d 个孤立的对局临时世界模板", removed_templates
            )
        await plugin_host.start_enabled()
        app["_embedding_backfill_task"] = asyncio.create_task(
            self.embed_pending_memories(app)
        )
        app["_save_task"] = asyncio.create_task(self.periodic_save(app))
        app["_assistant_docs_task"] = asyncio.create_task(
            prefetch_remote_indexes()
        )
        app["_certificate_renewal_task"] = asyncio.create_task(
            self.certificate_renewal_loop(app),
            name="certificate-renewal",
        )

    def sync_builtin_templates(self) -> None:
        paths = self.dependencies.paths
        rule_sync = sync_template_catalog(
            paths.builtin_rules_dir,
            paths.rules_dir,
            "rules",
        )
        world_sync = sync_template_catalog(
            paths.builtin_worlds_dir,
            paths.worlds_dir,
            "worlds",
        )
        adventure_sync = sync_adventure_catalog(
            paths.builtin_adventures_dir,
            paths.adventures_dir,
        )
        if any(rule_sync.values()) or any(world_sync.values()) or any(
            adventure_sync.values()
        ):
            self.logger.info(
                "模板目录已同步到 data: rules=%s worlds=%s adventures=%s",
                rule_sync,
                world_sync,
                adventure_sync,
            )

    def _migrate_portable_plugin_packages(self, plugin_host: PluginHost) -> None:
        install_root = str(
            self.dependencies.environ.get("TRPG_INSTALL_ROOT") or ""
        ).strip()
        if not install_root:
            return
        from src.webui.services.updater import _migrate_user_plugin_packages

        install_root_path = Path(install_root)
        sources = [install_root_path / "app" / "plugins"]
        sources.extend(install_root_path.glob("versions/*/app/plugins"))
        for source in sources:
            _migrate_user_plugin_packages(
                source,
                self.dependencies.paths.data_dir / "plugin-packages",
            )

    async def certificate_renewal_loop(self, app: web.Application) -> None:
        while True:
            try:
                service = app.get("security_transport")
                result = await service.renew_if_due() if service else None
                if result and result.get("status") == "failed":
                    self.logger.error(
                        "Let's Encrypt 证书续期失败：%s", result.get("error")
                    )
                elif result and result.get("status") == "missing":
                    self.logger.warning(
                        "Let's Encrypt 已配置但找不到当前证书，请在设置 → 安全重新申请"
                    )
                elif result and result.get("status") == "renewed":
                    self.logger.info("Let's Encrypt 证书续期成功，准备重启加载新证书")
                    control = app["runtime_control"]
                    if not control["restart_requested"]:
                        control["restart_requested"] = True
                        control["restart_task"] = asyncio.create_task(
                            self.restart_after_certificate_renewal()
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Let's Encrypt 证书续期检查失败")
            await asyncio.sleep(15 * 60)

    @staticmethod
    async def restart_after_certificate_renewal() -> None:
        await asyncio.sleep(0.5)
        signal.raise_signal(signal.SIGINT)

    async def on_cleanup(self, app: web.Application) -> None:
        plugin_host = app.get("plugin_host")
        if plugin_host:
            await plugin_host.cleanup()
        hub_client = app.get("hub_client")
        if hub_client:
            await hub_client.close()
        embed_task = app.get("_embedding_backfill_task")
        if embed_task:
            embed_task.cancel()
            try:
                await embed_task
            except asyncio.CancelledError:
                pass
        save_task = app.get("_save_task")
        if save_task:
            save_task.cancel()
        renewal_task = app.get("_certificate_renewal_task")
        if renewal_task:
            renewal_task.cancel()
            try:
                await renewal_task
            except asyncio.CancelledError:
                pass
        subsystems: TRPGSubsystems | None = app.get("subsystems")
        if not subsystems:
            return
        try:
            await subsystems.registry.save_all_active()
        except Exception:
            self.logger.exception("关闭前保存失败")
        if subsystems.llm_client:
            await subsystems.llm_client.close()
        if subsystems.memory_store and subsystems.memory_store.embedding_client:
            await subsystems.memory_store.embedding_client.close()
        subsystems.lorebook_store.close()
        subsystems.memory_store.close()
