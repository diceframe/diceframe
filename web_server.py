from pathlib import Path

import asyncio
import json
import hmac
import logging
import os
import sys

from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent))
from src.common_factory import TRPGSubsystems, create_trpg_subsystems
from src.llm.client import ProviderConfig
from src.network_proxy import effective_proxy_url, env_proxy_url, is_supported_proxy_url, mask_proxy_url
from src.plugin_host import PluginHost
from src.webui.api import WebAPI
from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.routes.character_cards import register_character_cards
from src.webui.routes.rules import register_rules
from src.webui.routes.worlds import register_worlds
from src.webui.routes.generation import register_generation
from src.webui.routes.games import register_games
from src.webui.routes.sse import register_sse
from src.webui.routes.memory import register_memory
from src.webui.routes.auth import register_auth
from src.webui.routes.pages import register_pages
from src.webui.routes.bot import register_bot
from src.webui.routes.plugins import register_plugins

logger = logging.getLogger("trpg")
logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

DATA_DIR = Path(os.getenv("TRPG_DATA_DIR", str(Path(__file__).parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"
SECRETS_FILE = DATA_DIR / "secrets.json"
ACCESS_TOKEN_FILE = DATA_DIR / "access_token.txt"

saved = {}
if CONFIG_FILE.exists():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (json.JSONDecodeError, ValueError):
        pass

secrets = {}
if SECRETS_FILE.exists():
    try:
        with open(SECRETS_FILE, encoding="utf-8") as f:
            secrets = json.load(f)
    except (json.JSONDecodeError, ValueError):
        pass

# env > secrets.json > config.json（用于迁移）
API_KEY = (os.getenv("TRPG_LLM_API_KEY")
           or secrets.get("api_key")
           or saved.get("api_key", ""))
BASE_URL = (os.getenv("TRPG_LLM_BASE_URL")
            or saved.get("base_url", "https://api.deepseek.com/v1"))
MODEL = (os.getenv("TRPG_LLM_MODEL")
         or saved.get("model", "deepseek-chat"))
PORT = int(os.getenv("TRPG_WEB_PORT") or saved.get("web_port", 9876))
HOST = os.getenv("TRPG_WEB_HOST") or saved.get("web_host", "0.0.0.0")
EMB_ENABLED = saved.get("embedding_enabled", False)
EMB_BASE_URL = saved.get("embedding_base_url", "")
EMB_MODEL = (os.getenv("TRPG_EMBEDDING_MODEL")
             or saved.get("embedding_model", "nomic-embed-text"))
EMB_API_KEY = (os.getenv("TRPG_EMBEDDING_API_KEY")
               or secrets.get("embedding_api_key")
               or saved.get("embedding_api_key", ""))
FALLBACK1_API_KEY = secrets.get("fallback1_api_key") or saved.get("fallback1_api_key", "")
FALLBACK2_API_KEY = secrets.get("fallback2_api_key") or saved.get("fallback2_api_key", "")
ACCESS_TOKEN = (os.getenv("TRPG_ACCESS_TOKEN")
                or secrets.get("access_token")
                or saved.get("access_token", ""))
BOT_TOKEN = (os.getenv("TRPG_BOT_TOKEN")
             or secrets.get("bot_token")
             or saved.get("bot_token", ""))
NAPCAT_TOKEN = (os.getenv("NAPCAT_TOKEN")
                or secrets.get("napcat_token")
                or saved.get("napcat_token", ""))
NAPCAT_HOST = os.getenv("NAPCAT_HOST") or saved.get("napcat_host", "127.0.0.1")
NAPCAT_PORT = int(os.getenv("NAPCAT_PORT") or saved.get("napcat_port", 3001))
NAPCAT_HEARTBEAT_SEC = float(os.getenv("NAPCAT_HEARTBEAT_SEC") or saved.get("napcat_heartbeat_sec", 30))
NAPCAT_RECONNECT_DELAY_SEC = float(os.getenv("NAPCAT_RECONNECT_DELAY_SEC") or saved.get("napcat_reconnect_delay_sec", 5))
NAPCAT_ACTION_TIMEOUT_SEC = float(os.getenv("NAPCAT_ACTION_TIMEOUT_SEC") or saved.get("napcat_action_timeout_sec", 15))
NAPCAT_REPLY_DELAY_MIN_SEC = float(os.getenv("NAPCAT_REPLY_DELAY_MIN_SEC") or saved.get("napcat_reply_delay_min_sec", 0.8))
NAPCAT_REPLY_DELAY_MAX_SEC = float(os.getenv("NAPCAT_REPLY_DELAY_MAX_SEC") or saved.get("napcat_reply_delay_max_sec", 2.4))
NAPCAT_COMMAND_DEDUP_WINDOW_SEC = float(os.getenv("NAPCAT_COMMAND_DEDUP_WINDOW_SEC") or saved.get("napcat_command_dedup_window_sec", 6))
NAPCAT_CONNECTION_ID = os.getenv("NAPCAT_CONNECTION_ID") or str(saved.get("napcat_connection_id", ""))
NARRATIVE_MAX_TOKENS = int(os.getenv("TRPG_NARRATIVE_MAX_TOKENS")
                           or saved.get("narrative_max_tokens", 1024))
CHARACTER_GEN_MAX_TOKENS = int(os.getenv("TRPG_CHARACTER_GEN_MAX_TOKENS")
                               or saved.get("character_gen_max_tokens", 2048))
SUMMARY_MAX_TOKENS = int(os.getenv("TRPG_SUMMARY_MAX_TOKENS")
                         or saved.get("summary_max_tokens", 400))
BRIEF_MAX_TOKENS = int(os.getenv("TRPG_BRIEF_MAX_TOKENS")
                       or saved.get("brief_max_tokens", 300))
ANALYSIS_MAX_TOKENS = int(os.getenv("TRPG_ANALYSIS_MAX_TOKENS")
                          or saved.get("analysis_max_tokens", 512))
TEXT_GEN_MAX_TOKENS = int(os.getenv("TRPG_TEXT_GEN_MAX_TOKENS")
                          or saved.get("text_gen_max_tokens", 400))
_ENV_PROXY_URL = env_proxy_url()
_CONFIG_PROXY_URL = secrets.get("proxy_url") or saved.get("proxy_url", "")
PROXY_ENABLED = bool(saved.get("proxy_enabled", bool(_ENV_PROXY_URL)))
PROXY_URL = (os.getenv("TRPG_PROXY_URL")
             or _CONFIG_PROXY_URL
             or _ENV_PROXY_URL)

# 自动迁移：config.json 中的 api_key 迁移到 secrets.json
if saved.get("api_key") and not secrets.get("api_key"):
    secrets["api_key"] = saved.pop("api_key")
    _migrated = True
else:
    _migrated = False
if saved.get("embedding_api_key") and not secrets.get("embedding_api_key"):
    secrets["embedding_api_key"] = saved.pop("embedding_api_key")
    _migrated = True

STATE = {
    "api_key": API_KEY, "base_url": BASE_URL, "model": MODEL, "web_port": PORT,
    "embedding_enabled": EMB_ENABLED, "embedding_base_url": EMB_BASE_URL,
    "embedding_model": EMB_MODEL, "embedding_api_key": EMB_API_KEY,
    "fallback1_enabled": saved.get("fallback1_enabled", False),
    "fallback1_base_url": saved.get("fallback1_base_url", ""),
    "fallback1_model": saved.get("fallback1_model", ""),
    "fallback1_api_key": FALLBACK1_API_KEY,
    "fallback2_enabled": saved.get("fallback2_enabled", False),
    "fallback2_base_url": saved.get("fallback2_base_url", ""),
    "fallback2_model": saved.get("fallback2_model", ""),
    "fallback2_api_key": FALLBACK2_API_KEY,
    "narrative_max_tokens": NARRATIVE_MAX_TOKENS,
    "character_gen_max_tokens": CHARACTER_GEN_MAX_TOKENS,
    "summary_max_tokens": SUMMARY_MAX_TOKENS,
    "brief_max_tokens": BRIEF_MAX_TOKENS,
    "analysis_max_tokens": ANALYSIS_MAX_TOKENS,
    "text_gen_max_tokens": TEXT_GEN_MAX_TOKENS,
    "access_token": ACCESS_TOKEN,
    "bot_token": BOT_TOKEN,
    "qq_bot_enabled": bool(saved.get("qq_bot_enabled", False)),
    "qq_bot_running": False,
    "napcat_host": NAPCAT_HOST,
    "napcat_port": NAPCAT_PORT,
    "napcat_token": NAPCAT_TOKEN,
    "napcat_heartbeat_sec": NAPCAT_HEARTBEAT_SEC,
    "napcat_reconnect_delay_sec": NAPCAT_RECONNECT_DELAY_SEC,
    "napcat_action_timeout_sec": NAPCAT_ACTION_TIMEOUT_SEC,
    "napcat_reply_delay_min_sec": NAPCAT_REPLY_DELAY_MIN_SEC,
    "napcat_reply_delay_max_sec": NAPCAT_REPLY_DELAY_MAX_SEC,
    "napcat_command_dedup_window_sec": NAPCAT_COMMAND_DEDUP_WINDOW_SEC,
    "napcat_connection_id": NAPCAT_CONNECTION_ID,
    "napcat_chat_filter_enabled": bool(saved.get("napcat_chat_filter_enabled", False)),
    "napcat_show_dropped_logs": bool(saved.get("napcat_show_dropped_logs", False)),
    "napcat_group_list_mode": saved.get("napcat_group_list_mode", "whitelist"),
    "napcat_group_list": saved.get("napcat_group_list", []),
    "napcat_private_list_mode": saved.get("napcat_private_list_mode", "whitelist"),
    "napcat_private_list": saved.get("napcat_private_list", []),
    "napcat_blocked_users": saved.get("napcat_blocked_users", []),
    "napcat_block_official_bots": bool(saved.get("napcat_block_official_bots", True)),
    "proxy_enabled": PROXY_ENABLED,
    "proxy_url": PROXY_URL,
    "public_base_url": str(saved.get("public_base_url", "")),
}

ROOT = Path(__file__).parent
PROMPTS_DIR = ROOT / "prompts"
RULES_DIR = ROOT / "templates" / "rules"
WORLDS_DIR = ROOT / "templates" / "worlds"
STATIC_V2_DIR = ROOT / "static-v2"


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _mask_secret(value: str) -> dict:
    if not value:
        return {"configured": False, "masked": ""}
    return {"configured": True, "masked": f"***{value[-4:]}"}


def _public_config() -> dict:
    public = {k: v for k, v in STATE.items()
              if k not in ("api_key", "embedding_api_key", "fallback1_api_key", "fallback2_api_key", "access_token", "bot_token", "napcat_token", "proxy_url")}
    public["api_key"] = _mask_secret(STATE.get("api_key", ""))
    public["embedding_api_key"] = _mask_secret(STATE.get("embedding_api_key", ""))
    public["fallback1_api_key"] = _mask_secret(STATE.get("fallback1_api_key", ""))
    public["fallback2_api_key"] = _mask_secret(STATE.get("fallback2_api_key", ""))
    public["access_password"] = _mask_secret(STATE.get("access_token", ""))
    public["bot_token"] = _mask_secret(STATE.get("bot_token", ""))
    public["napcat_token"] = _mask_secret(STATE.get("napcat_token", ""))
    proxy_url = STATE.get("proxy_url", "")
    public["proxy_url"] = mask_proxy_url(proxy_url)
    if not STATE.get("proxy_enabled"):
        public["proxy_source"] = "disabled"
    elif _CONFIG_PROXY_URL or (STATE.get("proxy_url") and STATE.get("proxy_url") != _ENV_PROXY_URL):
        public["proxy_source"] = "config"
    elif _ENV_PROXY_URL:
        public["proxy_source"] = "env"
    else:
        public["proxy_source"] = "empty"
    public["proxy_supported"] = is_supported_proxy_url(effective_proxy_url(bool(STATE.get("proxy_enabled")), proxy_url))
    return public


def save_config():
    non_sensitive = {k: v for k, v in STATE.items()
                     if k not in ("api_key", "embedding_api_key", "fallback1_api_key", "fallback2_api_key", "access_token", "bot_token", "napcat_token", "proxy_url", "qq_bot_running")}
    _atomic_write_json(CONFIG_FILE, non_sensitive)
    sensitive = {k: v for k, v in STATE.items()
                 if k in ("api_key", "embedding_api_key", "fallback1_api_key", "fallback2_api_key", "access_token", "bot_token", "napcat_token", "proxy_url")}
    if any(v for v in sensitive.values()) or SECRETS_FILE.exists():
        _atomic_write_json(SECRETS_FILE, sensitive)

if _migrated:
    save_config()
    logger.warning("已自动迁移 API Key 到 secrets.json，config.json 中密钥已移除")


def _build_subsystems() -> TRPGSubsystems:
    providers = [ProviderConfig(provider_name="default", base_url=STATE["base_url"],
                                api_key=STATE["api_key"], model_name=STATE["model"])]
    for idx in (1, 2):
        if STATE.get(f"fallback{idx}_enabled") and STATE.get(f"fallback{idx}_base_url") and STATE.get(f"fallback{idx}_model"):
            providers.append(ProviderConfig(
                provider_name=f"fallback{idx}",
                base_url=STATE.get(f"fallback{idx}_base_url", ""),
                api_key=STATE.get(f"fallback{idx}_api_key") or STATE.get("api_key", ""),
                model_name=STATE.get(f"fallback{idx}_model", ""),
                fallback=True,
            ))
    emb_base = STATE.get("embedding_base_url", "")
    emb_enabled = STATE.get("embedding_enabled", False) and bool(emb_base)
    return create_trpg_subsystems(
        data_dir=DATA_DIR, prompts_dir=PROMPTS_DIR,
        rules_dir=RULES_DIR, worlds_dir=WORLDS_DIR,
        providers=providers, default_provider="default",
        embedding_enabled=emb_enabled,
        embedding_base_url=emb_base,
        embedding_api_key=STATE.get("embedding_api_key") or STATE.get("api_key", ""),
        embedding_model=STATE.get("embedding_model", "nomic-embed-text"),
        embedding_max_input=int(STATE.get("embedding_max_input", 0)),
        proxy_url=effective_proxy_url(bool(STATE.get("proxy_enabled")), STATE.get("proxy_url", "")),
        narrative_max_tokens=int(STATE.get("narrative_max_tokens", 1024)),
        character_gen_max_tokens=int(STATE.get("character_gen_max_tokens", 2048)),
        summary_max_tokens=int(STATE.get("summary_max_tokens", 400)),
        brief_max_tokens=int(STATE.get("brief_max_tokens", 300)),
        analysis_max_tokens=int(STATE.get("analysis_max_tokens", 512)),
    )


def _make_api(subsystems: TRPGSubsystems, plugin_host=None) -> WebAPI:
    return WebAPI(
        registry=subsystems.registry, lorebook=subsystems.lorebook_store,
        memory=subsystems.memory_store, rules_dir=RULES_DIR,
        handler=subsystems.handler, llm_client=subsystems.llm_client,
        worlds_dir=WORLDS_DIR,
        character_gen_max_tokens=int(STATE.get("character_gen_max_tokens", 2048)),
        text_gen_max_tokens=int(STATE.get("text_gen_max_tokens", 400)),
        plugin_host=plugin_host,
    )


async def _periodic_save(app: web.Application):
    """每 60 秒自动保存所有活跃对局，防崩溃丢档。"""
    while True:
        await asyncio.sleep(60)
        subs: TRPGSubsystems | None = app.get("subsystems")
        if subs:
            try:
                await subs.registry.save_all_active()
            except Exception:
                logger.exception("定时保存失败")

async def _embed_pending_memories(app: web.Application):
    """Backfill pending memory embeddings without blocking the WebUI listener."""
    if not (EMB_ENABLED and EMB_BASE_URL):
        return
    subsystems: TRPGSubsystems | None = app.get("subsystems")
    if not subsystems or not subsystems.memory_store:
        return
    try:
        for inst in subsystems.registry.list_all():
            count = await subsystems.memory_store.embed_all_pending(str(inst.game_key))
            if count:
                logger.info("[Embedding] %s: backfilled %d pending memories", inst.world_name, count)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Embedding backfill failed")

async def on_startup(app: web.Application) -> None:
    if not STATE.get("access_token"):
        import secrets as _secrets
        STATE["access_token"] = _secrets.token_urlsafe(18)
        print("\n" + "=" * 60, flush=True)
        print("  Access token (changes each restart): " + STATE["access_token"], flush=True)
        print("  Frontend will prompt for this on open.", flush=True)
        print("  For a fixed token: set env TRPG_ACCESS_TOKEN", flush=True)
        print("=" * 60 + "\n", flush=True)
    else:
        logger.info("使用配置的固定访问密码（来自 env 或 secrets.json）")
    token_tmp = ACCESS_TOKEN_FILE.with_suffix(ACCESS_TOKEN_FILE.suffix + ".tmp")
    token_tmp.write_text(STATE["access_token"] + "\n", encoding="utf-8")
    token_tmp.replace(ACCESS_TOKEN_FILE)
    logger.info("当前访问密码已保存到: %s", ACCESS_TOKEN_FILE)
    subsystems = _build_subsystems()
    app["subsystems"] = subsystems
    plugin_host = PluginHost(ROOT / "plugins", DATA_DIR / "plugins", base_env={
        "TRPG_API_BASE": f"http://127.0.0.1:{PORT}",
        "TRPG_BOT_DATA": str(DATA_DIR / "plugins" / "qq-napcat" / "runtime" / "sessions.json"),
    })
    plugin_host.discover()
    if "qq-napcat" in plugin_host.plugins:
        plugin_host.migrate_config("qq-napcat", {
            "enabled": STATE.get("qq_bot_enabled", False), "host": STATE.get("napcat_host"),
            "port": STATE.get("napcat_port"), "token": STATE.get("napcat_token"),
            "heartbeat_sec": STATE.get("napcat_heartbeat_sec"), "reconnect_delay_sec": STATE.get("napcat_reconnect_delay_sec"),
            "action_timeout_sec": STATE.get("napcat_action_timeout_sec"),
            "reply_delay_min_sec": STATE.get("napcat_reply_delay_min_sec"),
            "reply_delay_max_sec": STATE.get("napcat_reply_delay_max_sec"),
            "command_dedup_window_sec": STATE.get("napcat_command_dedup_window_sec"),
            "connection_id": STATE.get("napcat_connection_id"),
            "chat_filter_enabled": STATE.get("napcat_chat_filter_enabled"), "show_dropped_logs": STATE.get("napcat_show_dropped_logs"),
            "group_list_mode": STATE.get("napcat_group_list_mode"), "group_list": STATE.get("napcat_group_list"),
            "private_list_mode": STATE.get("napcat_private_list_mode"), "private_list": STATE.get("napcat_private_list"),
            "blocked_users": STATE.get("napcat_blocked_users"), "block_official_bots": STATE.get("napcat_block_official_bots"),
            "bot_token": STATE.get("bot_token"),
        })
    app["plugin_host"] = plugin_host
    app["api"] = _make_api(subsystems, plugin_host)
    await plugin_host.start_enabled()
    recovered = await subsystems.registry.recover_all()
    if recovered:
        logger.info("恢复了 %d 个存档", len(recovered))
    app["_embedding_backfill_task"] = asyncio.create_task(_embed_pending_memories(app))
    app["_save_task"] = asyncio.create_task(_periodic_save(app))


async def on_cleanup(app: web.Application) -> None:
    plugin_host = app.get("plugin_host")
    if plugin_host:
        await plugin_host.cleanup()
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
    subsystems: TRPGSubsystems | None = app.get("subsystems")
    if subsystems:
        try:
            await subsystems.registry.save_all_active()
        except Exception:
            logger.exception("关闭前保存失败")
        # 关闭复用的 HTTP session
        if subsystems.llm_client:
            await subsystems.llm_client.close()
        if subsystems.memory_store and subsystems.memory_store.embedding_client:
            await subsystems.memory_store.embedding_client.close()
        subsystems.lorebook_store.close()
        subsystems.memory_store.close()


@web.middleware
async def auth_middleware(request: web.Request, handler):
    bot_header = str(request.headers.get("X-Bot-Token") or "")
    if request.path.startswith("/api/bot/") or bot_header:
        plugin_host = request.app.get("plugin_host")
        qq_runtime = plugin_host.plugins.get("qq-napcat") if plugin_host else None
        qq_enabled = bool(qq_runtime.config.get("enabled")) if qq_runtime else bool(STATE.get("qq_bot_enabled"))
        if not qq_enabled:
            return web.json_response({"ok": False, "error": "QQ Bot 插件未启用"}, status=503)
        configured_bot_token = str(qq_runtime.secrets.get("bot_token") if qq_runtime else STATE.get("bot_token") or "")
        if not configured_bot_token or not hmac.compare_digest(bot_header, configured_bot_token):
            return web.json_response({"ok": False, "error": "Bot 服务未授权"}, status=401)
        request["bot_authenticated"] = True
        if request.path.startswith("/api/bot/"):
            return await handler(request)
        game_key = _bot_request_game_key(request)
        api = request.app.get("api")
        # 公开生成端点不代表玩家、不修改游戏；bot_token 已验证身份，放行。
        # 其余 game_key 为空的请求仍按“代表玩家无效”拒绝。
        if not game_key:
            if request.path in _BOT_PUBLIC_ENDPOINTS:
                return await handler(request)
            return web.json_response({"ok": False, "error": "Bot 代表玩家无效"}, status=403)
        actor = str(request.headers.get("X-Bot-Actor") or "").strip()
        if not actor or not api or not api.bot_actor_allowed(game_key, actor):
            return web.json_response({"ok": False, "error": "Bot 代表玩家无效"}, status=403)
        detail = api.game_detail(game_key) or {}
        if detail.get("player_access_open") is False and actor != detail.get("gm_uid"):
            return web.json_response({"ok": False, "error": "本局玩家入口已关闭"}, status=403)
        request["user_id"] = actor
        request["bot_actor"] = actor
        return await handler(request)

    token = STATE.get("access_token", "")
    auth = request.headers.get("Authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    qtoken = request.query.get("token", "")
    owner_authenticated = bool(token and (bearer == token or qtoken == token))
    request["owner_authenticated"] = owner_authenticated
    share_uid = _share_player_user_id(request)

    # 房间密码门：设了 room_password 的游戏，玩家端点需带有效 room_token。
    # owner 已认证（房主自己 / 预览）和 verify-room-password 入口放行。
    if _requires_room_token(share_uid, owner_authenticated, request.path):
        inst = _request_game_inst(request)
        if inst and inst.room_password and not _request_room_token_ok(inst, request):
            return web.json_response({"ok": False, "error": "需要房间密码", "needs_room_password": True}, status=403)

    # verify-room-password 是换取 room_token 的入口，玩家无任何凭证时也可访问
    if request.method == "POST" and request.path.endswith("/verify-room-password"):
        return await handler(request)

    # 显式玩家链接的身份仅作用于当前请求。房主凭密码打开时进入玩家预览，
    # 不修改其 Cookie；写操作还需要显式 delegate=1。
    if share_uid and request.query.get("user"):
        if not owner_authenticated and _player_access_is_closed(request):
            return web.json_response({"ok": False, "error": "本局玩家入口已关闭"}, status=403)
        viewer_uid = request.get("user_id", "")
        request["viewer_user_id"] = viewer_uid
        request["user_id"] = share_uid
        request["player_preview"] = bool(owner_authenticated and viewer_uid != share_uid)
        request["player_delegate"] = request.query.get("delegate", "") in {"1", "true", "yes"}
        return await handler(request)

    # /api/config 返回公开配置（敏感字段已 mask），玩家无 access_token 也可读取
    if request.method == "GET" and request.path == "/api/config":
        return await handler(request)
    # 仅保护 API 端点；HTML 页面和静态资源放行，由前端遇 401 跳 /login 处理登录
    if token and request.path.startswith("/api/"):
        if not owner_authenticated:
            if share_uid:
                if _player_access_is_closed(request):
                    return web.json_response({"ok": False, "error": "本局玩家入口已关闭"}, status=403)
                request["user_id"] = share_uid
                return await handler(request)
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
    return await handler(request)


# Bot 可不带 X-Bot-Actor 调用的公开端点：AI 生成接口不针对特定游戏、不代表玩家。
_BOT_PUBLIC_ENDPOINTS = frozenset({
    "/api/generate-character",
    "/api/generate-world",
    "/api/generate-text",
})


def _bot_request_game_key(request: web.Request) -> str:
    parts = [part for part in request.path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "games":
        return parts[2]
    return ""


def _request_game_inst(request: web.Request):
    gk = _bot_request_game_key(request)
    if not gk:
        return None
    api = request.app.get("api")
    subsystems = request.app.get("subsystems")
    if not api or not subsystems:
        return None
    return subsystems.registry.get(api._parse_key(gk))


def _requires_room_token(share_uid: str, owner_authenticated: bool, path: str) -> bool:
    if owner_authenticated or not share_uid:
        return False
    parts = [p for p in path.split("/") if p]
    if len(parts) < 4 or parts[3] == "verify-room-password":
        return False
    return True


def _request_room_token_ok(inst, request: web.Request) -> bool:
    token = str(request.query.get("room_token") or "")
    return bool(inst.room_token) and hmac.compare_digest(inst.room_token, token)


def _share_player_user_id(request: web.Request) -> str:
    """Allow player share links to use player-facing APIs without the GM password."""
    uid = str(request.query.get("user") or "").strip()
    share_mode = request.query.get("share", "") in {"1", "true", "yes"}
    if not uid and not share_mode:
        return ""
    parts = [p for p in request.path.split("/") if p]
    if len(parts) < 3 or parts[0] != "api" or parts[1] != "games":
        return ""
    if len(parts) == 3 and request.method == "GET":
        return uid or request.get("user_id", "")
    if len(parts) >= 4:
        tail = parts[3]
        if request.method == "GET" and tail in {"characters", "character-cards", "log", "private-log", "multiplayer", "sse", "map", "player-context"}:
            return uid or request.get("user_id", "")
        if request.method == "POST" and tail in {"players", "action"}:
            return uid or request.get("user_id", "")
        if request.method == "PUT" and tail == "character":
            return uid or request.get("user_id", "")
    return ""


def _player_access_is_closed(request: web.Request) -> bool:
    parts = [p for p in request.path.split("/") if p]
    if len(parts) < 3 or parts[0] != "api" or parts[1] != "games":
        return False
    api = request.app.get("api")
    subsystems = request.app.get("subsystems")
    if not api or not subsystems:
        return False
    try:
        inst = subsystems.registry.get(api._parse_key(parts[2]))
    except Exception:
        return False
    return bool(inst and not getattr(inst, "player_access_open", True))


async def api_config_get(request: web.Request) -> web.Response:
    return web.json_response(_public_config())


async def api_config_post(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    body = await request.json()
    for k in ("api_key", "base_url", "model", "web_port", "embedding_enabled",
              "embedding_base_url", "embedding_model", "embedding_api_key", "embedding_max_input",
              "fallback1_enabled", "fallback1_base_url", "fallback1_model", "fallback1_api_key",
              "fallback2_enabled", "fallback2_base_url", "fallback2_model", "fallback2_api_key",
              "narrative_max_tokens", "character_gen_max_tokens",
              "summary_max_tokens", "brief_max_tokens",
              "analysis_max_tokens", "text_gen_max_tokens",
              "proxy_enabled", "proxy_url", "public_base_url", "access_token", "bot_token",
              "qq_bot_enabled", "napcat_host", "napcat_port", "napcat_token",
              "napcat_heartbeat_sec", "napcat_reconnect_delay_sec", "napcat_action_timeout_sec",
              "napcat_reply_delay_min_sec", "napcat_reply_delay_max_sec", "napcat_command_dedup_window_sec",
              "napcat_connection_id", "napcat_chat_filter_enabled", "napcat_show_dropped_logs",
              "napcat_group_list_mode", "napcat_group_list", "napcat_private_list_mode",
              "napcat_private_list", "napcat_blocked_users", "napcat_block_official_bots"):
        if k in body:
            if k in ("api_key", "embedding_api_key", "fallback1_api_key", "fallback2_api_key", "access_token", "bot_token", "napcat_token") and not body[k]:
                continue
            if k.endswith("_max_tokens"):
                STATE[k] = max(1, int(body[k]))
            elif k == "proxy_url":
                proxy_url = str(body[k] or "").strip()
                if proxy_url and not is_supported_proxy_url(proxy_url):
                    return web.json_response({"ok": False, "error": "代理地址仅支持 http:// 或 https://"}, status=400)
                STATE[k] = proxy_url
            elif k == "proxy_enabled":
                STATE[k] = bool(body[k])
            elif k == "qq_bot_enabled":
                STATE[k] = bool(body[k])
            elif k in {"napcat_chat_filter_enabled", "napcat_show_dropped_logs", "napcat_block_official_bots"}:
                STATE[k] = bool(body[k])
            elif k in {"napcat_heartbeat_sec", "napcat_reconnect_delay_sec", "napcat_action_timeout_sec", "napcat_command_dedup_window_sec"}:
                value = float(body[k])
                if value <= 0:
                    return web.json_response({"ok": False, "error": "NapCat 时间参数必须大于 0"}, status=400)
                STATE[k] = value
            elif k in {"napcat_reply_delay_min_sec", "napcat_reply_delay_max_sec"}:
                value = float(body[k])
                if value < 0:
                    return web.json_response({"ok": False, "error": "NapCat 回复延迟不能小于 0"}, status=400)
                STATE[k] = value
            elif k in {"napcat_group_list_mode", "napcat_private_list_mode"}:
                STATE[k] = "blacklist" if body[k] == "blacklist" else "whitelist"
            elif k in {"napcat_group_list", "napcat_private_list", "napcat_blocked_users"}:
                values = body[k] if isinstance(body[k], list) else []
                STATE[k] = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
            elif k == "napcat_port":
                port = int(body[k])
                if not 1 <= port <= 65535:
                    return web.json_response({"ok": False, "error": "NapCat 端口无效"}, status=400)
                STATE[k] = port
            else:
                STATE[k] = body[k]
    active_proxy = effective_proxy_url(bool(STATE.get("proxy_enabled")), STATE.get("proxy_url", ""))
    if STATE.get("proxy_enabled") and not active_proxy:
        return web.json_response({"ok": False, "error": "已启用代理，但代理地址为空"}, status=400)
    if active_proxy and not is_supported_proxy_url(active_proxy):
        return web.json_response({"ok": False, "error": "代理地址仅支持 http:// 或 https://"}, status=400)
    if float(STATE.get("napcat_reply_delay_max_sec", 0)) < float(STATE.get("napcat_reply_delay_min_sec", 0)):
        return web.json_response({"ok": False, "error": "NapCat 回复延迟上限不能小于下限"}, status=400)
    save_config()

    bot_fields = {key for key in STATE if key.startswith("napcat_")} | {"bot_token", "qq_bot_enabled"}
    if set(body) & bot_fields:
        plugin_host = request.app.get("plugin_host")
        if plugin_host and "qq-napcat" in plugin_host.plugins:
            legacy_to_plugin = {
                "qq_bot_enabled":"enabled", "napcat_host":"host", "napcat_port":"port", "napcat_token":"token",
                "napcat_heartbeat_sec":"heartbeat_sec", "napcat_reconnect_delay_sec":"reconnect_delay_sec",
                "napcat_action_timeout_sec":"action_timeout_sec",
                "napcat_reply_delay_min_sec":"reply_delay_min_sec",
                "napcat_reply_delay_max_sec":"reply_delay_max_sec",
                "napcat_command_dedup_window_sec":"command_dedup_window_sec",
                "napcat_connection_id":"connection_id",
                "napcat_chat_filter_enabled":"chat_filter_enabled", "napcat_show_dropped_logs":"show_dropped_logs",
                "napcat_group_list_mode":"group_list_mode", "napcat_group_list":"group_list",
                "napcat_private_list_mode":"private_list_mode", "napcat_private_list":"private_list",
                "napcat_blocked_users":"blocked_users", "napcat_block_official_bots":"block_official_bots",
                "bot_token":"bot_token",
            }
            changes = {legacy_to_plugin[key]: value for key, value in body.items() if key in legacy_to_plugin}
            await plugin_host.update_config("qq-napcat", changes)

    # 修改访问密码不涉及模型运行时，避免无意义地重建并丢失当前内存中的活跃对局。
    if set(body).issubset({"access_token"} | bot_fields):
        return web.json_response({"ok": True, "access_password_changed": True})

    # 关闭旧 subsystems 的 HTTP session，防止泄漏
    old_subs = request.app.get("subsystems")
    if old_subs:
        if old_subs.llm_client:
            await old_subs.llm_client.close()
        if old_subs.memory_store and old_subs.memory_store.embedding_client:
            await old_subs.memory_store.embedding_client.close()
    subsystems = _build_subsystems()
    request.app["subsystems"] = subsystems
    request.app["api"] = _make_api(subsystems, request.app.get("plugin_host"))
    # 配置更新后，如果 embedding 已启用，立即补齐存量记忆的向量
    emb_now = STATE.get("embedding_enabled", False) and bool(STATE.get("embedding_base_url", ""))
    if emb_now:
        try:
            count = await subsystems.memory_store.embed_all_pending()
            if count:
                logger.info("[Embedding] 配置更新后补齐 %d 条向量记忆", count)
        except Exception:
            logger.warning("配置更新后 embedding 补齐失败", exc_info=True)
    return web.json_response({"ok": True})


def _is_safe_external_url(url: str) -> bool:
    """防 SSRF：要求 http(s)，禁云元数据/私网/回环；保留 127.0.0.1 与 localhost 供本地 ollama。"""
    if not url or not url.startswith(("http://", "https://")):
        return False
    from urllib.parse import urlparse
    import ipaddress
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_reserved:
        return False
    return True


async def api_test_connection(request: web.Request) -> web.Response:
    body = await request.json()
    base_url = body.get("base_url", STATE.get("base_url", ""))
    if not _is_safe_external_url(base_url):
        return web.json_response({"ok": False, "error": "base_url 非法或不允许"}, status=400)
    proxy_url = _proxy_from_test_body(body)
    if proxy_url and not is_supported_proxy_url(proxy_url):
        return web.json_response({"ok": False, "error": "代理地址仅支持 http:// 或 https://"}, status=400)
    result = await _get_api(request).test_connection(
        base_url=base_url,
        api_key=body.get("api_key") or STATE.get("api_key", ""),
        model=body.get("model", STATE.get("model", "")),
        proxy_url=proxy_url,
    )
    return web.json_response(result)


def _proxy_from_test_body(body: dict) -> str:
    if "proxy_enabled" not in body and "proxy_url" not in body:
        return effective_proxy_url(bool(STATE.get("proxy_enabled")), STATE.get("proxy_url", ""))
    enabled = bool(body.get("proxy_enabled"))
    proxy_url = str(body.get("proxy_url") or "").strip()
    if not proxy_url:
        proxy_url = STATE.get("proxy_url", "")
    return effective_proxy_url(enabled, proxy_url)


async def api_test_embedding(request: web.Request) -> web.Response:
    body = await request.json()
    base_url = body.get("base_url", "")
    api_key = body.get("api_key") or STATE.get("embedding_api_key") or STATE.get("api_key", "")
    model = body.get("model", "nomic-embed-text")
    if not _is_safe_external_url(base_url):
        return web.json_response({"ok": False, "error": "Base URL 非法或不允许"})
    from src.memory.embedding import EmbeddingClient
    import time
    proxy_url = _proxy_from_test_body(body)
    if proxy_url and not is_supported_proxy_url(proxy_url):
        return web.json_response({"ok": False, "error": "代理地址仅支持 http:// 或 https://"}, status=400)
    client = EmbeddingClient(
        base_url, api_key, model,
        proxy_url=proxy_url,
    )
    start = time.time()
    try:
        emb = await client.embed("测试")
        elapsed = round(time.time() - start, 2)
        if emb and len(emb) > 0:
            return web.json_response({"ok": True, "dimension": len(emb), "elapsed": elapsed})
        return web.json_response({"ok": False, "error": "Embedding API 返回异常", "elapsed": elapsed})
    finally:
        await client.close()


async def api_test_proxy(request: web.Request) -> web.Response:
    body = await request.json()
    enabled = bool(body.get("proxy_enabled", STATE.get("proxy_enabled", False)))
    proxy_url = str(body.get("proxy_url", STATE.get("proxy_url", "")) or "").strip()
    proxy = effective_proxy_url(enabled, proxy_url)
    if enabled and not proxy:
        return web.json_response({"ok": False, "error": "已启用代理，但代理地址为空"}, status=400)
    if proxy and not is_supported_proxy_url(proxy):
        return web.json_response({"ok": False, "error": "代理地址仅支持 http:// 或 https://"}, status=400)
    url = str(STATE.get("base_url") or "").strip().rstrip("/")
    if not _is_safe_external_url(url):
        return web.json_response({"ok": False, "error": "请先配置有效的模型服务地址"}, status=400)
    import aiohttp
    import time
    start = time.time()
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession() as session:
            request_kwargs = {"proxy": proxy} if proxy else {}
            async with session.get(url, timeout=timeout, **request_kwargs) as resp:
                text = await resp.text()
                elapsed = round(time.time() - start, 2)
                # 401/403/404 也说明网络链路已连通；这里只测试连接，不校验 API Key。
                if resp.status < 500:
                    return web.json_response({
                        "ok": True,
                        "status": resp.status,
                        "elapsed": elapsed,
                        "proxy": mask_proxy_url(proxy),
                    })
                return web.json_response({
                    "ok": False,
                    "error": f"HTTP {resp.status}: {text[:160]}",
                    "elapsed": elapsed,
                    "proxy": mask_proxy_url(proxy),
                })
    except Exception as exc:
        logger.exception("test-connection 异常")
        return web.json_response({
            "ok": False,
            "error": "连接异常，请查看服务器日志",
            "elapsed": round(time.time() - start, 2),
            "proxy": mask_proxy_url(proxy),
        })


app = web.Application()
app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

from src.webui.connection_pool import ConnectionPool
from src.webui.session import SessionManager, session_middleware

app.middlewares.append(session_middleware)
app.middlewares.append(auth_middleware)
app["session_manager"] = SessionManager(DATA_DIR)
app["connection_pool"] = ConnectionPool()
app["static_v2_dir"] = STATIC_V2_DIR

def register_routes(application: web.Application) -> None:
    """集中注册所有路由，按域分组。"""
    # 页面
    register_pages(application)
    # auth/session
    register_auth(application)
    # games
    register_games(application)
    register_bot(application)
    register_plugins(application)
    # worlds / lorebook
    register_worlds(application)
    # rules
    register_rules(application)
    # character cards
    register_character_cards(application)
    # config / test
    application.router.add_get("/api/config", api_config_get)
    application.router.add_post("/api/config", api_config_post)
    application.router.add_post("/api/test-connection", api_test_connection)
    application.router.add_post("/api/test-embedding", api_test_embedding)
    application.router.add_post("/api/test-proxy", api_test_proxy)
    # generation
    register_generation(application)
    # SSE / stream
    register_sse(application)
    # memory
    register_memory(application)


register_routes(app)

if __name__ == "__main__":
    print(f"DiceFrame WebUI: http://127.0.0.1:{PORT}  (host={HOST})")
    if not API_KEY:
        print("请在 WebUI 设置页填写 API Key")
    web.run_app(app, host=HOST, port=PORT)
