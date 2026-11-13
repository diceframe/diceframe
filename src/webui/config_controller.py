"""HTTP controller for runtime settings and provider connection tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import ipaddress
import logging
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from src.ai_providers import resolve_provider
from src.common_factory import close_model_clients
from src.memory.embedding import EmbeddingClient
from src.memory.embedding_profile import profile_for_client
from src.memory.local_embedding import list_installed_models
from src.memory.model_catalog import LocalModelManager, ModelCatalogError
from src.network_proxy import (
    effective_proxy_url,
    is_supported_proxy_url,
    mask_proxy_url,
)
from src.webui.config_update import (
    API_RUNTIME_CONFIG_KEYS,
    MODEL_RUNTIME_CONFIG_KEYS,
    bot_plugin_changes,
    clean_text_value,
    connection_test_timeout,
    normalize_api_format,
    prepare_config_update,
    provider_runtime_changed,
)
from src.webui.access_password import (
    is_valid_access_password,
    normalize_access_password,
)
from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.routes.pairing import PAIRING_SERVICE_KEY
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY
from src.webui.runtime_config import ConfigStore
from src.webui.server_identity import SERVER_IDENTITY_UNAVAILABLE, server_instance_id
from src.webui.services.assistant_settings_tools import (
    SettingsToolCallbacks,
    build_settings_actions,
    config_digest,
)


@dataclass(frozen=True)
class ConfigControllerDependencies:
    state: dict
    environ: Mapping[str, str]
    cors_env_value: str
    public_config: Callable[[], dict]
    save_config: Callable[[], None]
    ensure_bot_token: Callable[[], str]
    delete_access_token_file: Callable[[], None]
    build_subsystems: Callable[..., Any]
    make_api: Callable[..., Any]
    activate_api_runtime: Callable[[Any, Any], None]


class ConfigController:
    def __init__(
        self,
        dependencies: ConfigControllerDependencies,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.dependencies = dependencies
        self.logger = logger or logging.getLogger("trpg")

    async def get(self, request: web.Request) -> web.Response:
        public = self.dependencies.public_config()
        if request.get("owner_authenticated", False):
            subsystems = request.app.get("subsystems")
            memory_store = getattr(subsystems, "memory_store", None)
            models_dir = (
                Path(memory_store.db_path).parent / "models"
                if memory_store is not None else Path("data") / "models"
            )
            public["embedding_local_models"] = list_installed_models(models_dir)
            try:
                public["embedding_model_catalog"] = LocalModelManager(
                    models_dir,
                    proxy_url=effective_proxy_url(
                        bool(self.dependencies.state.get("proxy_enabled")),
                        self.dependencies.state.get("proxy_url", ""),
                    ),
                ).catalog()
            except ModelCatalogError as exc:
                public["embedding_model_catalog"] = []
                self.logger.warning("本地 embedding 模型目录无效：%s", exc)
            if memory_store is not None:
                public["embedding_status"] = memory_store.embedding_status(
                    profile=(
                        profile_for_client(memory_store.embedding_client)
                        if memory_store.embedding_client is not None else None
                    )
                )
                rebuild_task = request.app.get("_embedding_backfill_task")
                if rebuild_task is not None and not rebuild_task.done():
                    public["embedding_status"]["state"] = "rebuilding"
        # Defaults can contain private Keeper instructions; anonymous bootstrap
        # clients and invited seats must not receive the owner profile.
        if not request.get("owner_authenticated", False) and request.get(ACCESS_PASSWORD_CONFIGURED_KEY) is not False:
            public.pop("game_defaults", None)
        # The instance id keys a client's content-sync mapping. Only the owner
        # syncs with the library, so anonymous probes do not get a stable
        # identifier for the server.
        if request.get("owner_authenticated", False) or (
            request.get(ACCESS_PASSWORD_CONFIGURED_KEY) is False
        ):
            instance_id = server_instance_id(request.app)
            if instance_id:
                public["server_instance_id"] = instance_id
            else:
                # Explicit, so a sync client can tell "unavailable" from an
                # older server that never sent the field.
                public["server_identity_error"] = SERVER_IDENTITY_UNAVAILABLE
        return web.json_response(public)

    async def post(self, request: web.Request) -> web.Response:
        denied = _require_confirmed_request(request)
        if denied is not None:
            return denied
        body = await request.json()
        if not isinstance(body, dict):
            return web.json_response(
                {"ok": False, "error": "配置请求必须是 JSON 对象"},
                status=400,
            )
        reload_lock = request.app.get("_config_reload_lock")
        if reload_lock is None:
            reload_lock = asyncio.Lock()
            request.app["_config_reload_lock"] = reload_lock
        async with reload_lock:
            return await self.apply_update(request, body)

    async def apply_update(
        self,
        request: web.Request,
        body: dict,
    ) -> web.Response:
        """HTTP adapter; the application path is exposed by ``apply_values``."""
        return await self.apply_values(request.app, body)

    def attach_agent_actions(self, app: Any, api: Any) -> None:
        """Attach settings actions to an API that exposes the public registry.

        This is intentionally capability based: older API objects without
        ``register_application_actions`` simply skip the optional integration.
        The apply callback re-enters the same reload lock and the same
        application path used by the HTTP settings route.
        """
        register = getattr(api, "register_application_actions", None)
        if not callable(register):
            return

        async def apply_config(changes: dict[str, Any]) -> dict[str, Any]:
            lock = app.get("_config_reload_lock")
            if lock is None:
                lock = asyncio.Lock()
                app["_config_reload_lock"] = lock
            async with lock:
                response = await self.apply_values(app, changes)
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("配置更新返回无效响应") from exc
            if response.status >= 400 or not payload.get("ok"):
                raise ValueError(str(payload.get("error") or "配置更新失败"))
            return payload

        async def apply_config_guarded(changes: dict[str, Any], expected_digest: str) -> dict[str, Any]:
            lock = app.get("_config_reload_lock")
            if lock is None:
                lock = asyncio.Lock()
                app["_config_reload_lock"] = lock
            async with lock:
                if expected_digest != config_digest(self.dependencies.state):
                    raise ValueError("Settings changed; re-read settings and plan again")
                response = await self.apply_values(app, changes)
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("配置更新返回无效响应") from exc
            if response.status >= 400 or not payload.get("ok"):
                raise ValueError(str(payload.get("error") or "配置更新失败"))
            return payload

        callbacks = SettingsToolCallbacks(
            read_config=lambda: self.dependencies.state,
            apply_config=apply_config,
            apply_config_guarded=apply_config_guarded,
            list_plugins=getattr(api, "list_plugins", None),
            plugin_detail=getattr(api, "plugin_detail", None),
        )
        register(build_settings_actions(callbacks))

    async def apply_values(
        self,
        app: Any,
        body: dict,
    ) -> web.Response:
        dependencies = self.dependencies
        state = dependencies.state
        if "web_cors_origins" in body and dependencies.cors_env_value:
            return web.json_response(
                {
                    "ok": False,
                    "error": (
                        "TRPG_WEB_CORS_ORIGINS 已由环境变量接管，"
                        "请修改 .env 后重启后端"
                    ),
                },
                status=409,
            )
        prepared = prepare_config_update(state, body)
        if prepared.error:
            return web.json_response(
                {"ok": False, "error": prepared.error},
                status=400,
            )
        access_password_changed = prepared.access_password_changed
        changed_keys = prepared.changed_keys
        model_runtime_changed = bool(
            changed_keys & MODEL_RUNTIME_CONFIG_KEYS
        ) or provider_runtime_changed(changed_keys)
        api_runtime_changed = bool(
            changed_keys & API_RUNTIME_CONFIG_KEYS
        ) or provider_runtime_changed(changed_keys)
        old_subsystems = app.get("subsystems")
        plugin_host = app.get("plugin_host")
        old_embedding = (
            old_subsystems.memory_store.embedding_client
            if old_subsystems is not None
            and old_subsystems.memory_store is not None
            else None
        )
        subsystems = old_subsystems
        new_api = app.get("api")
        try:
            if model_runtime_changed:
                subsystems = dependencies.build_subsystems(
                    reuse=old_subsystems,
                    config=prepared.state,
                )
                new_api = dependencies.make_api(
                    subsystems,
                    plugin_host,
                    config=prepared.state,
                    hub_client=app.get("hub_client"),
                )
            elif api_runtime_changed and old_subsystems is not None:
                new_api = dependencies.make_api(
                    old_subsystems,
                    plugin_host,
                    config=prepared.state,
                    hub_client=app.get("hub_client"),
                )
        except Exception as exc:
            if old_subsystems is not None and old_subsystems.memory_store is not None:
                old_subsystems.memory_store.embedding_client = old_embedding
            await self._close_candidate_runtime(
                subsystems,
                old_subsystems,
                old_embedding,
            )
            self.logger.exception("配置更新后的运行时重建失败")
            return web.json_response(
                {
                    "ok": False,
                    "error": f"运行时重载失败，配置未保存：{exc}",
                },
                status=500,
            )

        previous_state = dict(state)
        state.clear()
        state.update(prepared.state)
        try:
            dependencies.save_config()
        except Exception as exc:
            state.clear()
            state.update(previous_state)
            if old_subsystems is not None and old_subsystems.memory_store is not None:
                old_subsystems.memory_store.embedding_client = old_embedding
            await self._close_candidate_runtime(
                subsystems,
                old_subsystems,
                old_embedding,
            )
            self.logger.exception("保存候选配置失败")
            return web.json_response(
                {"ok": False, "error": f"配置保存失败：{exc}"},
                status=500,
            )

        if "web_cors_origins" in changed_keys:
            from src.webui.cors import parse_cors_origins

            app["cors_origins"] = parse_cors_origins(
                state.get("web_cors_origins", "")
            )
        if access_password_changed:
            dependencies.delete_access_token_file()
            was_open = not is_valid_access_password(
                normalize_access_password(previous_state.get("access_token"))
            )
            pairing = app.get(PAIRING_SERVICE_KEY)
            if was_open and pairing is not None:
                # 免密期签发的设备令牌是「谁连得上谁就是 owner」，第一次设置
                # 访问密码必须把它们一起收回，否则锁门这一步等于没做。
                revoked = pairing.revoke_all()
                if revoked:
                    self.logger.warning(
                        "设置访问密码：已吊销 %d 台免密期配对设备", revoked,
                    )

        plugin_warning = ""
        plugin_changes = bot_plugin_changes(body, state)
        if plugin_changes and plugin_host and "qq-napcat" in plugin_host.plugins:
            try:
                await plugin_host.update_config("qq-napcat", plugin_changes)
            except Exception as exc:
                plugin_warning = f"NapCat 插件配置同步失败：{exc}"
                self.logger.exception("NapCat 插件配置同步失败")
        if plugin_host and (
            "ai_providers" in changed_keys
            or provider_runtime_changed(changed_keys)
        ):
            try:
                await plugin_host.restart_ai_provider_consumers()
            except Exception as exc:
                provider_warning = f"AI 服务商插件重启失败：{exc}"
                plugin_warning = (
                    f"{plugin_warning}；{provider_warning}"
                    if plugin_warning
                    else provider_warning
                )
                self.logger.exception("AI 服务商插件重启失败")

        if model_runtime_changed and subsystems is not None:
            dependencies.activate_api_runtime(subsystems, new_api)
            app["subsystems"] = subsystems
            app["api"] = new_api
        elif api_runtime_changed and new_api is not None:
            dependencies.activate_api_runtime(old_subsystems, new_api)
            app["api"] = new_api

        if new_api is not None and new_api is not app.get("api"):
            # Kept for hosts whose activation callback does not replace app.api.
            self.attach_agent_actions(app, new_api)
        elif new_api is not None and (model_runtime_changed or api_runtime_changed):
            self.attach_agent_actions(app, new_api)

        if (
            model_runtime_changed
            and old_subsystems is not None
            and subsystems is not None
        ):
            await close_model_clients(old_subsystems, keep=subsystems)
            new_embedding = getattr(
                subsystems.memory_store,
                "embedding_client",
                None,
            )
            if old_embedding is not None and old_embedding is not new_embedding:
                try:
                    await old_embedding.close()
                except Exception:
                    self.logger.warning(
                        "关闭旧 Embedding 客户端失败",
                        exc_info=True,
                    )
        embedding_source = str(state.get("embedding_source") or "external")
        embedding_now = bool(state.get("embedding_enabled")) and (
            embedding_source == "local"
            or bool(resolve_provider(state, state.get("embedding_provider_ref", "")))
        )
        if model_runtime_changed and embedding_now and subsystems is not None:
            self.schedule_embedding_rebuild(app, subsystems)
        payload: dict[str, Any] = {
            "ok": True,
            "access_password_changed": access_password_changed,
        }
        if prepared.warnings:
            payload["warnings"] = list(prepared.warnings)
        if plugin_warning:
            payload["warning"] = plugin_warning
        return web.json_response(payload)

    def schedule_embedding_rebuild(self, app: Any, subsystems: Any) -> None:
        """Start a cancellable, non-blocking rebuild for the active profile."""

        previous = app.get("_embedding_backfill_task")
        if previous is not None and not previous.done():
            previous.cancel()
        app["_embedding_backfill_task"] = asyncio.create_task(
            self._run_embedding_rebuild(subsystems), name="embedding-profile-rebuild",
        )

    async def _run_embedding_rebuild(self, subsystems: Any) -> None:
        try:
            while True:
                count = await subsystems.memory_store.embed_all_pending()
                if not count:
                    return
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.warning("后台 embedding profile 重建失败", exc_info=True)

    @staticmethod
    async def _close_candidate_runtime(
        candidate,
        previous,
        previous_embedding,
    ) -> None:
        if candidate is None or candidate is previous:
            return
        await close_model_clients(candidate, keep=previous)
        candidate_embedding = getattr(
            candidate.memory_store,
            "embedding_client",
            None,
        )
        if candidate_embedding is not None and candidate_embedding is not previous_embedding:
            await candidate_embedding.close()

    async def bot_token_post(self, request: web.Request) -> web.Response:
        denied = _require_confirmed_request(request)
        if denied is not None:
            return denied
        body = await request.json()
        action = str(body.get("action") or "reveal").strip().lower()
        if action not in {"reveal", "regenerate"}:
            return web.json_response(
                {"ok": False, "error": "不支持的 Bot Token 操作"},
                status=400,
            )
        regenerated = action == "regenerate"
        if regenerated:
            if self.dependencies.environ.get("TRPG_BOT_TOKEN"):
                return web.json_response(
                    {
                        "ok": False,
                        "error": (
                            "Bot API Token 由环境变量 TRPG_BOT_TOKEN 管理，"
                            "请修改环境变量后重启"
                        ),
                    },
                    status=409,
                )
            token = secrets.token_urlsafe(32)
            self.dependencies.state["bot_token"] = token
            self.dependencies.save_config()
        else:
            token = self.dependencies.ensure_bot_token()
        return web.json_response(
            {
                "ok": True,
                "token": token,
                "masked": ConfigStore.mask_secret(token)["masked"],
                "regenerated": regenerated,
            }
        )

    @staticmethod
    def is_safe_external_url(url: str) -> bool:
        if not url or not url.startswith(("http://", "https://")):
            return False
        host = (urlparse(url).hostname or "").lower()
        if host in ("localhost", "127.0.0.1"):
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return True
        return not (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_unspecified
            or address.is_reserved
        )

    async def test_connection(self, request: web.Request) -> web.Response:
        body = await request.json()
        state = self.dependencies.state
        provider = resolve_provider(state, str(body.get("provider_id") or ""))
        if provider:
            base_url = clean_text_value(body.get("base_url")) or provider["base_url"]
            api_key = clean_text_value(body.get("api_key")) or provider["api_key"]
            api_format = normalize_api_format(
                body.get("api_format") or provider["api_format"]
            )
        else:
            base_url = clean_text_value(body.get("base_url"))
            api_key = clean_text_value(body.get("api_key"))
            api_format = normalize_api_format(body.get("api_format"))
        if not self.is_safe_external_url(base_url):
            return web.json_response(
                {"ok": False, "error": "base_url 非法或不允许"},
                status=400,
            )
        proxy_url = self.proxy_from_test_body(body)
        if proxy_url and not is_supported_proxy_url(proxy_url):
            return web.json_response(
                {"ok": False, "error": "代理地址仅支持 http:// 或 https://"},
                status=400,
            )
        result = await _get_api(request).test_connection(
            base_url=base_url,
            api_key=api_key,
            model=clean_text_value(body.get("model")) or state.get("model", ""),
            proxy_url=proxy_url,
            api_format=api_format,
        )
        return web.json_response(result)

    async def provider_models_post(self, request: web.Request) -> web.Response:
        body = await request.json()
        state = self.dependencies.state
        provider = resolve_provider(state, str(body.get("provider_id") or ""))
        if provider:
            base_url = clean_text_value(body.get("base_url")) or provider["base_url"]
            api_key = clean_text_value(body.get("api_key")) or provider["api_key"]
            api_format = normalize_api_format(
                body.get("api_format") or provider["api_format"]
            )
        else:
            base_url = clean_text_value(body.get("base_url"))
            api_key = clean_text_value(body.get("api_key"))
            api_format = normalize_api_format(body.get("api_format"))
        if not self.is_safe_external_url(base_url):
            return web.json_response(
                {
                    "ok": False,
                    "error": "base_url 非法或不允许",
                    "models": [],
                },
                status=400,
            )
        proxy_url = self.proxy_from_test_body(body)
        if proxy_url and not is_supported_proxy_url(proxy_url):
            return web.json_response(
                {
                    "ok": False,
                    "error": "代理地址仅支持 http:// 或 https://",
                    "models": [],
                },
                status=400,
            )
        result = await _get_api(request).list_models(
            base_url=base_url,
            api_key=api_key,
            proxy_url=proxy_url,
            api_format=api_format,
        )
        return web.json_response(result)

    def proxy_from_test_body(self, body: dict) -> str:
        state = self.dependencies.state
        if "proxy_enabled" not in body and "proxy_url" not in body:
            return effective_proxy_url(
                bool(state.get("proxy_enabled")),
                state.get("proxy_url", ""),
            )
        enabled = bool(body.get("proxy_enabled"))
        proxy_url = str(body.get("proxy_url") or "").strip()
        if not proxy_url:
            proxy_url = state.get("proxy_url", "")
        return effective_proxy_url(enabled, proxy_url)

    async def test_embedding(self, request: web.Request) -> web.Response:
        body = await request.json()
        state = self.dependencies.state
        # 2026-11-12 计划 §5：按来源分流测试。本地来源此前没有测试路径——
        # embedding_provider_ref 为空导致必然报「Base URL 非法或不允许」。
        if str(state.get("embedding_source") or "external") == "local":
            return await self._test_embedding_local(request)
        provider = resolve_provider(state, str(body.get("provider_id") or ""))
        if provider:
            base_url = clean_text_value(body.get("base_url")) or provider["base_url"]
            api_key = clean_text_value(body.get("api_key")) or provider["api_key"]
        else:
            base_url = clean_text_value(body.get("base_url"))
            api_key = clean_text_value(body.get("api_key"))
        model = clean_text_value(body.get("model")) or "nomic-embed-text"
        if not self.is_safe_external_url(base_url):
            return web.json_response(
                {"ok": False, "error": "Base URL 非法或不允许"}
            )
        proxy_url = self.proxy_from_test_body(body)
        if proxy_url and not is_supported_proxy_url(proxy_url):
            return web.json_response(
                {"ok": False, "error": "代理地址仅支持 http:// 或 https://"},
                status=400,
            )
        client = EmbeddingClient(
            base_url,
            api_key,
            model,
            proxy_url=proxy_url,
            timeout_seconds=connection_test_timeout(state),
        )
        start = time.time()
        try:
            embedding = await client.embed("测试")
            elapsed = round(time.time() - start, 2)
            if embedding and len(embedding) > 0:
                return web.json_response(
                    {
                        "ok": True,
                        "dimension": len(embedding),
                        "elapsed": elapsed,
                    }
                )
            return web.json_response(
                {
                    "ok": False,
                    "error": "Embedding API 返回异常",
                    "elapsed": elapsed,
                }
            )
        finally:
            await client.close()

    async def _test_embedding_local(self, request: web.Request) -> web.Response:
        """本地来源的向量测试：真实加载 ONNX 并跑一次推理，回报维数与耗时。"""

        state = self.dependencies.state
        model_id = str(state.get("embedding_local_model_id") or "").strip()
        if not model_id:
            return web.json_response(
                {"ok": False, "source": "local", "error": "请先选择要测试的本地模型"},
            )
        subsystems = request.app.get("subsystems")
        memory_store = getattr(subsystems, "memory_store", None)
        models_dir = (
            Path(memory_store.db_path).parent / "models"
            if memory_store is not None else Path("data") / "models"
        )
        from src.memory.local_embedding import LocalModelError, LocalOnnxEmbeddingBackend

        start = time.time()
        try:
            backend = LocalOnnxEmbeddingBackend(models_dir, model_id)
        except LocalModelError as exc:
            return web.json_response(
                {"ok": False, "source": "local", "error": f"本地模型加载失败：{exc}"},
            )
        try:
            embedding = await backend.embed("测试")
            elapsed = round(time.time() - start, 2)
            if embedding and len(embedding) > 0:
                return web.json_response(
                    {
                        "ok": True,
                        "source": "local",
                        "model": model_id,
                        "dimension": len(embedding),
                        "elapsed": elapsed,
                    }
                )
            return web.json_response(
                {"ok": False, "source": "local", "error": "本地模型返回异常", "elapsed": elapsed},
            )
        finally:
            await backend.close()

    async def test_proxy(self, request: web.Request) -> web.Response:
        body = await request.json()
        state = self.dependencies.state
        enabled = bool(body.get("proxy_enabled", state.get("proxy_enabled", False)))
        proxy_url = str(
            body.get("proxy_url", state.get("proxy_url", "")) or ""
        ).strip()
        proxy = effective_proxy_url(enabled, proxy_url)
        if enabled and not proxy:
            return web.json_response(
                {"ok": False, "error": "已启用代理，但代理地址为空"},
                status=400,
            )
        if proxy and not is_supported_proxy_url(proxy):
            return web.json_response(
                {"ok": False, "error": "代理地址仅支持 http:// 或 https://"},
                status=400,
            )
        provider = resolve_provider(state, state.get("llm_provider_ref", ""))
        url = str(provider["base_url"] if provider else "").strip().rstrip("/")
        if not self.is_safe_external_url(url):
            return web.json_response(
                {"ok": False, "error": "请先配置有效的模型服务地址"},
                status=400,
            )
        start = time.time()
        try:
            timeout = aiohttp.ClientTimeout(total=connection_test_timeout(state))
            async with aiohttp.ClientSession() as session:
                request_kwargs = {"proxy": proxy} if proxy else {}
                async with session.get(
                    url,
                    timeout=timeout,
                    **request_kwargs,
                ) as response:
                    text = await response.text()
                    elapsed = round(time.time() - start, 2)
                    if response.status < 500:
                        return web.json_response(
                            {
                                "ok": True,
                                "status": response.status,
                                "elapsed": elapsed,
                                "proxy": mask_proxy_url(proxy),
                            }
                        )
                    return web.json_response(
                        {
                            "ok": False,
                            "error": f"HTTP {response.status}: {text[:160]}",
                            "elapsed": elapsed,
                            "proxy": mask_proxy_url(proxy),
                        }
                    )
        except Exception:
            self.logger.exception("test-connection 异常")
            return web.json_response(
                {
                    "ok": False,
                    "error": "连接异常，请查看服务器日志",
                    "elapsed": round(time.time() - start, 2),
                    "proxy": mask_proxy_url(proxy),
                }
            )
