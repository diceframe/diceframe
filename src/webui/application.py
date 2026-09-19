"""aiohttp application factory and route composition."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
import secrets
from typing import Any

from aiohttp import web

from src.plugin_host.package_limits import MAX_PLUGIN_PACKAGE_BYTES
from src.web_transport import ServerTransport
from src.webui.abuse_guard import ABUSE_GUARD_KEY, AbuseGuard, abuse_guard_middleware
from src.webui.connection_pool import ConnectionPool
from src.webui.cors import cors_middleware, cors_response_prepare
from src.webui.errors import error_code_middleware
from src.webui.device_tokens import DEVICE_TOKENS_KEY, DeviceTokenStore
from src.webui.login_audit import LOGIN_AUDIT_KEY, LoginAuditStore
from src.webui.pairing import PairingService
from src.webui.routes.adventures import register_adventures
from src.webui.routes.announcements import register_announcements
from src.webui.routes.asr import register_asr
from src.webui.routes.assistant import register_assistant
from src.webui.routes.auth import register_auth
from src.webui.routes.avatars import register_avatars
from src.webui.routes.bot import register_bot
from src.webui.routes.character_cards import register_character_cards
from src.webui.routes.games import register_games
from src.webui.routes.generated_images import register_generated_images
from src.webui.routes.generation import register_generation
from src.webui.routes.hub import register_hub
from src.webui.routes.legal import register_legal
from src.webui.routes.maps import register_maps
from src.webui.routes.memory import register_memory
from src.webui.routes.modules import register_modules
from src.webui.routes.pages import add_response_security_headers, register_pages
from src.webui.routes.pairing import PAIRING_SERVICE_KEY, register_pairing
from src.webui.routes.plugins import register_plugins
from src.webui.routes.rules import register_rules
from src.webui.routes.scene_images import register_scene_images
from src.webui.routes.security import register_security
from src.webui.routes.speech import register_speech
from src.webui.routes.sse import register_sse
from src.webui.routes.system import register_system
from src.webui.routes.tunnel import register_tunnel
from src.webui.routes.updater import register_updater
from src.webui.routes.worlds import register_worlds
from src.webui.services.security import SecurityTransportService
from src.webui.session import SessionManager, session_middleware
from src.webui.sse_ticket import SseTicketStore


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
LifecycleHandler = Callable[[web.Application], Awaitable[None]]


@dataclass(frozen=True)
class ApplicationDependencies:
    data_dir: Path
    static_v2_dir: Path
    cors_origins: frozenset[str]
    transport: ServerTransport
    config_state: dict[str, Any]
    save_config: Callable[[], None]
    on_startup: LifecycleHandler
    on_cleanup: LifecycleHandler
    auth_middleware: Any
    config_get: Handler
    config_post: Handler
    bot_token_post: Handler
    provider_models_post: Handler
    test_connection: Handler
    test_embedding: Handler
    test_proxy: Handler


def create_app(dependencies: ApplicationDependencies) -> web.Application:
    """Construct a complete WebUI application without starting a listener."""
    application = web.Application(
        client_max_size=MAX_PLUGIN_PACKAGE_BYTES + 1024 * 1024
    )
    application.on_startup.append(dependencies.on_startup)
    application.on_cleanup.append(dependencies.on_cleanup)
    application.middlewares.append(cors_middleware)
    application.on_response_prepare.append(cors_response_prepare)
    application.middlewares.append(session_middleware)
    application.middlewares.append(abuse_guard_middleware)
    application.middlewares.append(dependencies.auth_middleware)
    application.middlewares.append(error_code_middleware)
    application.on_response_prepare.append(add_response_security_headers)
    application["_config_reload_lock"] = asyncio.Lock()
    application["session_manager"] = SessionManager(dependencies.data_dir)
    application[ABUSE_GUARD_KEY] = AbuseGuard()
    application[LOGIN_AUDIT_KEY] = LoginAuditStore(dependencies.data_dir)
    application[DEVICE_TOKENS_KEY] = DeviceTokenStore(dependencies.data_dir)
    application[PAIRING_SERVICE_KEY] = PairingService(
        application[DEVICE_TOKENS_KEY],
        audit=application[LOGIN_AUDIT_KEY],
    )
    application["connection_pool"] = ConnectionPool()
    application["sse_tickets"] = SseTicketStore()
    application["static_v2_dir"] = dependencies.static_v2_dir
    application["cors_origins"] = dependencies.cors_origins
    application["runtime_control"] = {
        "boot_id": secrets.token_hex(8),
        "restart_requested": False,
        "restart_task": None,
    }
    application["web_transport"] = dependencies.transport
    application["security_transport"] = SecurityTransportService(
        dependencies.config_state,
        dependencies.save_config,
        dependencies.data_dir,
        dependencies.transport,
    )
    register_routes(application, dependencies)
    return application


def register_routes(
    application: web.Application,
    dependencies: ApplicationDependencies,
) -> None:
    """Register all HTTP routes by owning domain."""
    register_pages(application)
    register_auth(application)
    register_pairing(application)
    register_games(application)
    register_bot(application)
    register_plugins(application)
    register_security(application)
    register_announcements(application)
    register_hub(application)
    register_legal(application)
    register_assistant(application)
    register_tunnel(application)
    register_system(application)
    register_updater(application)
    register_speech(application)
    register_asr(application)
    register_generated_images(application)
    register_worlds(application)
    register_rules(application)
    register_adventures(application)
    register_modules(application)
    register_character_cards(application)
    register_avatars(application)
    register_scene_images(application)
    register_maps(application)
    application.router.add_get("/api/config", dependencies.config_get)
    application.router.add_post("/api/config", dependencies.config_post)
    application.router.add_post(
        "/api/config/bot-token", dependencies.bot_token_post
    )
    application.router.add_post(
        "/api/config/providers/models", dependencies.provider_models_post
    )
    application.router.add_post(
        "/api/test-connection", dependencies.test_connection
    )
    application.router.add_post("/api/test-embedding", dependencies.test_embedding)
    application.router.add_post("/api/test-proxy", dependencies.test_proxy)
    register_generation(application)
    register_sse(application)
    register_memory(application)
