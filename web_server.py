from pathlib import Path

import asyncio
import json
import hmac
import logging
import os
import secrets as secrets_module
import sys
from datetime import datetime, timezone

from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent))
from src.common_factory import TRPGSubsystems, create_trpg_subsystems
from src.ai_providers import (
    is_provider_secret_key,
    normalize_ai_providers,
    provider_secret_key,
    resolve_provider,
)
from src.llm.client import ProviderConfig
from src.hub_client import HubClient
from src.network_proxy import effective_proxy_url, env_proxy_url, is_supported_proxy_url, mask_proxy_url
from src.plugin_host import PluginHost
from src.plugin_host.package_limits import MAX_PLUGIN_PACKAGE_BYTES
from src.template_catalog import sync_template_catalog
from src.tts import SpeechService
from src.asr import AsrService
from src.imagegen import ImageGenerationService
from src.webui.access_password import (
    consume_reset_password,
    hash_access_password,
    is_hashed_access_password,
    is_valid_access_password,
    mask_access_password,
    normalize_access_password,
    verify_access_password,
)
from src.webui.abuse_guard import ABUSE_GUARD_KEY, AbuseGuard, abuse_guard_middleware
from src.webui.api import WebAPI
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
from src.webui.routes._common import _get_api, _require_confirmed_request
from src.webui.routes.character_cards import register_character_cards
from src.webui.routes.avatars import register_avatars
from src.webui.routes.scene_images import register_scene_images
from src.webui.routes.maps import register_maps
from src.webui.routes.rules import register_rules
from src.webui.routes.worlds import register_worlds
from src.webui.routes.generation import register_generation
from src.webui.routes.games import register_games
from src.webui.routes.sse import register_sse
from src.webui.routes.memory import register_memory
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY, register_auth
from src.webui.routes.pages import add_response_security_headers, register_pages
from src.webui.login_audit import LOGIN_AUDIT_KEY, LoginAuditStore
from src.webui.routes.bot import register_bot
from src.webui.routes.plugins import register_plugins
from src.webui.routes.announcements import register_announcements
from src.webui.routes.hub import register_hub
from src.webui.routes.legal import register_legal
from src.webui.routes.assistant import register_assistant
from src.webui.routes.tunnel import register_tunnel
from src.webui.routes.system import register_system
from src.webui.routes.updater import register_updater
from src.webui.routes.speech import register_speech
from src.webui.routes.asr import register_asr
from src.webui.routes.generated_images import register_generated_images
from src.webui.services import updater as updater_svc
from src.webui.services import legal as legal_svc

logger = logging.getLogger("trpg")
logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

DEFAULT_NARRATIVE_MAX_TOKENS = 2048
GENERATION_DEFAULTS_VERSION = 5

DATA_DIR = Path(os.getenv("TRPG_DATA_DIR", str(Path(__file__).parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"
SECRETS_FILE = DATA_DIR / "secrets.json"
ACCESS_TOKEN_FILE = DATA_DIR / "access_token.txt"


def _quarantine_invalid_json(path: Path) -> Path | None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.stem}.corrupt-{timestamp}{path.suffix}")
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.corrupt-{timestamp}-{index}{path.suffix}")
        index += 1
    try:
        path.replace(candidate)
    except OSError:
        logger.exception("无法隔离损坏的配置文件: %s", path)
        return None
    return candidate


def _load_json_object(path: Path, label: str) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("JSON 根节点不是对象")
        return data
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        backup = _quarantine_invalid_json(path)
        if backup:
            logger.error("%s损坏，已保留为 %s：%s", label, backup, exc)
        else:
            logger.error("%s无法读取且未能隔离：%s", label, exc)
        return {}


# 各 token 字段的迁移规则：(字段名, 旧默认值集合, 新默认值)。
# 仅在配置值等于某个已知旧默认时提升，保留用户自定义值。
# 默认值历史上单调递增，缺失字段按最小旧默认补全后同样提升。
# character_gen_max_tokens 一直是 2048，无旧默认需提升，不在表中。
_TOKEN_FIELD_MIGRATIONS: tuple[tuple[str, frozenset[int], int], ...] = (
    ("narrative_max_tokens", frozenset({1024, 1536}), DEFAULT_NARRATIVE_MAX_TOKENS),
    ("analysis_max_tokens", frozenset({512}), 1024),
    ("summary_max_tokens", frozenset({400}), 1024),
    ("brief_max_tokens", frozenset({300}), 1024),
    ("text_gen_max_tokens", frozenset({400}), 1024),
)


def _migrate_generation_defaults(config: dict) -> bool:
    """一次性提升旧版默认生成额度，同时保留用户的自定义数值。"""
    try:
        version = int(config.get("generation_defaults_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= GENERATION_DEFAULTS_VERSION:
        return False

    for field, old_defaults, new_default in _TOKEN_FIELD_MIGRATIONS:
        missing = min(old_defaults)
        try:
            current = int(config.get(field, missing) or missing)
        except (TypeError, ValueError):
            current = missing
        if current in old_defaults:
            config[field] = new_default

    config["generation_defaults_version"] = GENERATION_DEFAULTS_VERSION
    return True


saved = _load_json_object(CONFIG_FILE, "主配置")
secrets = _load_json_object(SECRETS_FILE, "敏感配置")
_generation_defaults_migrated = _migrate_generation_defaults(saved)

# env > secrets.json（敏感配置只存 secrets.json）
API_KEY = (os.getenv("TRPG_LLM_API_KEY")
           or secrets.get("api_key")
           or "")
BASE_URL = (os.getenv("TRPG_LLM_BASE_URL")
            or saved.get("base_url", "https://api.deepseek.com/v1"))
MODEL = (os.getenv("TRPG_LLM_MODEL")
         or saved.get("model", "deepseek-v4-flash"))
API_FORMAT = (os.getenv("TRPG_LLM_API_FORMAT")
              or saved.get("api_format", "openai"))
PORT = int(os.getenv("TRPG_WEB_PORT") or saved.get("web_port", 18000))
HOST = os.getenv("TRPG_WEB_HOST") or saved.get("web_host", "0.0.0.0")
EMB_ENABLED = saved.get("embedding_enabled", False)
EMB_BASE_URL = saved.get("embedding_base_url", "")
EMB_MODEL = (os.getenv("TRPG_EMBEDDING_MODEL")
             or saved.get("embedding_model", "nomic-embed-text"))
EMB_API_KEY = (os.getenv("TRPG_EMBEDDING_API_KEY")
               or secrets.get("embedding_api_key")
               or "")
FALLBACK1_API_KEY = secrets.get("fallback1_api_key") or ""
FALLBACK2_API_KEY = secrets.get("fallback2_api_key") or ""
TTS_API_KEY = os.getenv("TRPG_TTS_API_KEY") or secrets.get("tts_api_key") or ""
ASR_API_KEY = os.getenv("TRPG_ASR_API_KEY") or secrets.get("asr_api_key") or ""
IMAGEGEN_API_KEY = os.getenv("TRPG_IMAGEGEN_API_KEY") or secrets.get("imagegen_api_key") or ""
ACCESS_TOKEN = next((
    password for password in (
        normalize_access_password(os.getenv("TRPG_ACCESS_TOKEN")),
        normalize_access_password(secrets.get("access_token")),
        normalize_access_password(saved.get("access_token")),
    ) if password
), "")
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
                           or saved.get("narrative_max_tokens", DEFAULT_NARRATIVE_MAX_TOKENS))
CHARACTER_GEN_MAX_TOKENS = int(os.getenv("TRPG_CHARACTER_GEN_MAX_TOKENS")
                               or saved.get("character_gen_max_tokens", 2048))
SUMMARY_MAX_TOKENS = int(os.getenv("TRPG_SUMMARY_MAX_TOKENS")
                         or saved.get("summary_max_tokens", 1024))
BRIEF_MAX_TOKENS = int(os.getenv("TRPG_BRIEF_MAX_TOKENS")
                       or saved.get("brief_max_tokens", 1024))
ANALYSIS_MAX_TOKENS = int(os.getenv("TRPG_ANALYSIS_MAX_TOKENS")
                          or saved.get("analysis_max_tokens", 1024))
TEXT_GEN_MAX_TOKENS = int(os.getenv("TRPG_TEXT_GEN_MAX_TOKENS")
                          or saved.get("text_gen_max_tokens", 1024))
_ENV_PROXY_URL = env_proxy_url()
_CONFIG_PROXY_URL = secrets.get("proxy_url") or saved.get("proxy_url", "")
PROXY_ENABLED = bool(saved.get("proxy_enabled", bool(_ENV_PROXY_URL)))
PROXY_URL = (os.getenv("TRPG_PROXY_URL")
             or _CONFIG_PROXY_URL
             or _ENV_PROXY_URL)

_migrated = _generation_defaults_migrated

_AI_PROVIDERS_DEFAULT = normalize_ai_providers(saved.get("ai_providers"))

STATE = {
    "generation_defaults_version": GENERATION_DEFAULTS_VERSION,
    "api_key": API_KEY, "base_url": BASE_URL, "model": MODEL, "api_format": API_FORMAT, "web_port": PORT,
    "ai_providers": _AI_PROVIDERS_DEFAULT,
    "llm_provider_ref": str(saved.get("llm_provider_ref", "")),
    "fallback1_provider_ref": str(saved.get("fallback1_provider_ref", "")),
    "fallback2_provider_ref": str(saved.get("fallback2_provider_ref", "")),
    "embedding_provider_ref": str(saved.get("embedding_provider_ref", "")),
    "tts_provider_ref": str(saved.get("tts_provider_ref", "")),
    "asr_provider_ref": str(saved.get("asr_provider_ref", "")),
    "imagegen_provider_ref": str(saved.get("imagegen_provider_ref", "")),
    **{key: str(value or "") for key, value in secrets.items() if is_provider_secret_key(key)},
    "embedding_enabled": EMB_ENABLED, "embedding_base_url": EMB_BASE_URL,
    "embedding_model": EMB_MODEL, "embedding_api_key": EMB_API_KEY,
    "fallback1_enabled": saved.get("fallback1_enabled", False),
    "fallback1_base_url": saved.get("fallback1_base_url", ""),
    "fallback1_model": saved.get("fallback1_model", ""),
    "fallback1_api_format": saved.get("fallback1_api_format", "openai"),
    "fallback1_api_key": FALLBACK1_API_KEY,
    "fallback2_enabled": saved.get("fallback2_enabled", False),
    "fallback2_base_url": saved.get("fallback2_base_url", ""),
    "fallback2_model": saved.get("fallback2_model", ""),
    "fallback2_api_format": saved.get("fallback2_api_format", "openai"),
    "fallback2_api_key": FALLBACK2_API_KEY,
    "tts_provider": str(os.getenv("TRPG_TTS_PROVIDER") or saved.get("tts_provider", "browser")),
    "tts_base_url": str(os.getenv("TRPG_TTS_BASE_URL") or saved.get("tts_base_url", "")),
    "tts_api_key": TTS_API_KEY,
    "tts_model": str(os.getenv("TRPG_TTS_MODEL") or saved.get("tts_model", "tts-1")),
    "tts_audio_format": str(os.getenv("TRPG_TTS_AUDIO_FORMAT") or saved.get("tts_audio_format", "mp3")),
    "tts_default_voice": str(os.getenv("TRPG_TTS_VOICE") or saved.get("tts_default_voice", "alloy")),
    "tts_gm_voice": str(saved.get("tts_gm_voice", "")),
    "tts_player_voice": str(saved.get("tts_player_voice", "")),
    "tts_timeout_seconds": float(saved.get("tts_timeout_seconds", 60)),
    "tts_cache_mb": int(saved.get("tts_cache_mb", 256)),
    "asr_provider": str(os.getenv("TRPG_ASR_PROVIDER") or saved.get("asr_provider", "disabled")),
    "asr_base_url": str(os.getenv("TRPG_ASR_BASE_URL") or saved.get("asr_base_url", "")),
    "asr_api_key": ASR_API_KEY,
    "asr_model": str(os.getenv("TRPG_ASR_MODEL") or saved.get("asr_model", "whisper-1")),
    "asr_timeout_seconds": float(saved.get("asr_timeout_seconds", 60)),
    "imagegen_enabled": bool(saved.get("imagegen_enabled", False)),
    "imagegen_auto_scene": bool(saved.get("imagegen_auto_scene", True)),
    "imagegen_provider": "openai-compatible",
    "imagegen_base_url": str(os.getenv("TRPG_IMAGEGEN_BASE_URL") or saved.get("imagegen_base_url", "")),
    "imagegen_api_key": IMAGEGEN_API_KEY,
    "imagegen_model": str(os.getenv("TRPG_IMAGEGEN_MODEL") or saved.get("imagegen_model", "")),
    "imagegen_square_size": str(saved.get("imagegen_square_size", "1024x1024")),
    "imagegen_landscape_size": str(saved.get("imagegen_landscape_size", "1792x1024")),
    "imagegen_quality": str(saved.get("imagegen_quality", "")),
    "imagegen_style_prefix": str(saved.get("imagegen_style_prefix", "")),
    "imagegen_timeout_seconds": float(saved.get("imagegen_timeout_seconds", 120)),
    "test_timeout_seconds": float(saved.get("test_timeout_seconds", 30)),
    "narrative_max_tokens": NARRATIVE_MAX_TOKENS,
    "character_gen_max_tokens": CHARACTER_GEN_MAX_TOKENS,
    "summary_max_tokens": SUMMARY_MAX_TOKENS,
    "brief_max_tokens": BRIEF_MAX_TOKENS,
    "analysis_max_tokens": ANALYSIS_MAX_TOKENS,
    "text_gen_max_tokens": TEXT_GEN_MAX_TOKENS,
    "access_token": ACCESS_TOKEN,
    "bot_token": BOT_TOKEN,
    "update_channel": saved.get("update_channel", "stable"),
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
    "hub_telemetry_enabled": bool(saved.get("hub_telemetry_enabled", False)),
    "hub_telemetry_choice_made": bool(saved.get("hub_telemetry_choice_made", False)),
    **legal_svc.persisted_acceptance_state(saved),
    "legal_privacy_acknowledged_version": saved.get("legal_privacy_acknowledged_version", ""),
}

ROOT = Path(__file__).parent
PROMPTS_DIR = ROOT / "prompts"
BUILTIN_RULES_DIR = ROOT / "templates" / "rules"
BUILTIN_WORLDS_DIR = ROOT / "templates" / "worlds"
RULES_DIR = DATA_DIR / "templates" / "rules"
WORLDS_DIR = DATA_DIR / "templates" / "worlds"
STATIC_V2_DIR = ROOT / "static-v2"

_rule_sync = sync_template_catalog(BUILTIN_RULES_DIR, RULES_DIR, "rules")
_world_sync = sync_template_catalog(BUILTIN_WORLDS_DIR, WORLDS_DIR, "worlds")
if any(_rule_sync.values()) or any(_world_sync.values()):
    logger.info("模板目录已同步到 data: rules=%s worlds=%s", _rule_sync, _world_sync)


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
              if k not in ("api_key", "embedding_api_key", "fallback1_api_key", "fallback2_api_key", "tts_api_key", "asr_api_key", "imagegen_api_key", "access_token", "bot_token", "napcat_token", "proxy_url")
              and not is_provider_secret_key(k)}
    public["ai_providers"] = [
        {**entry, "api_key": _mask_secret(STATE.get(provider_secret_key(entry["id"]), ""))}
        for entry in STATE.get("ai_providers", [])
    ]
    public["api_key"] = _mask_secret(STATE.get("api_key", ""))
    public["embedding_api_key"] = _mask_secret(STATE.get("embedding_api_key", ""))
    public["fallback1_api_key"] = _mask_secret(STATE.get("fallback1_api_key", ""))
    public["fallback2_api_key"] = _mask_secret(STATE.get("fallback2_api_key", ""))
    public["tts_api_key"] = _mask_secret(STATE.get("tts_api_key", ""))
    public["asr_api_key"] = _mask_secret(STATE.get("asr_api_key", ""))
    public["imagegen_api_key"] = _mask_secret(STATE.get("imagegen_api_key", ""))
    public["access_password"] = mask_access_password(STATE.get("access_token", ""))
    public["bot_token"] = _mask_secret(STATE.get("bot_token", ""))
    public["bot_token_source"] = "env" if os.getenv("TRPG_BOT_TOKEN") else "generated"
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
                     if k not in ("api_key", "embedding_api_key", "fallback1_api_key", "fallback2_api_key", "tts_api_key", "asr_api_key", "imagegen_api_key", "access_token", "bot_token", "napcat_token", "proxy_url", "qq_bot_running")
                     and not is_provider_secret_key(k)}
    _atomic_write_json(CONFIG_FILE, non_sensitive)
    sensitive = {k: v for k, v in STATE.items()
                 if k in ("api_key", "embedding_api_key", "fallback1_api_key", "fallback2_api_key", "tts_api_key", "asr_api_key", "imagegen_api_key", "access_token", "bot_token", "napcat_token", "proxy_url")
                 or is_provider_secret_key(k)}
    sensitive = {k: v for k, v in sensitive.items()
                 if not (k == "access_token" and os.getenv("TRPG_ACCESS_TOKEN"))}
    if any(v for v in sensitive.values()) or SECRETS_FILE.exists():
        _atomic_write_json(SECRETS_FILE, sensitive)


def _legacy_plugin_bot_token() -> str:
    """Read the pre-v1.2 QQ plugin token once so upgrades keep working."""
    legacy_file = DATA_DIR / "plugins" / "qq-napcat" / "secrets.json"
    if not legacy_file.exists():
        return ""
    try:
        data = json.loads(legacy_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        logger.warning("读取旧 QQ 插件 Bot Token 失败", exc_info=True)
        return ""
    return str(data.get("bot_token") or "").strip() if isinstance(data, dict) else ""


def _ensure_bot_token() -> str:
    """Keep one host-level Bot API token, independent from channel plugins."""
    current = str(STATE.get("bot_token") or "").strip()
    if current:
        return current
    import secrets as _secrets
    current = _legacy_plugin_bot_token() or _secrets.token_urlsafe(32)
    STATE["bot_token"] = current
    save_config()
    logger.info("已生成全局 Bot API Token；可在设置 → Bot API 中复制")
    return current


def _write_access_token_file(password: str) -> None:
    token_tmp = ACCESS_TOKEN_FILE.with_suffix(ACCESS_TOKEN_FILE.suffix + ".tmp")
    token_tmp.write_text(password + "\n", encoding="utf-8")
    token_tmp.replace(ACCESS_TOKEN_FILE)


def _delete_access_token_file() -> None:
    ACCESS_TOKEN_FILE.unlink(missing_ok=True)


def _read_access_token_file() -> str:
    try:
        return normalize_access_password(ACCESS_TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""


def _generate_initial_access_password() -> None:
    import secrets as _secrets
    generated_password = _secrets.token_urlsafe(18)
    STATE["access_token"] = hash_access_password(generated_password)
    save_config()
    _write_access_token_file(generated_password)
    print("\n" + "=" * 60, flush=True)
    print("  Initial access password: " + generated_password, flush=True)
    print("  Frontend will prompt for this on open.", flush=True)
    print("  It is also saved once to data/access_token.txt.", flush=True)
    print("  If forgotten later: create data/reset_access_password.txt and restart.", flush=True)
    print("=" * 60 + "\n", flush=True)


if _migrated:
    save_config()
    logger.warning("已迁移 generation 默认值到新版本配置")


def _build_subsystems(
    reuse: TRPGSubsystems | None = None,
    config: dict | None = None,
) -> TRPGSubsystems:
    runtime_config = STATE if config is None else config
    # 引用服务商时凭据以服务商为准（即使 key 为空也不回退内联，避免把 key 发给别家服务）；
    # 未引用则维持原内联配置与回退语义。
    main_provider = resolve_provider(runtime_config, runtime_config.get("llm_provider_ref", ""))
    if main_provider:
        main_base_url, main_api_key, main_api_format = (
            main_provider["base_url"], main_provider["api_key"], main_provider["api_format"])
    else:
        main_base_url = runtime_config["base_url"]
        main_api_key = runtime_config["api_key"]
        main_api_format = normalize_api_format(runtime_config.get("api_format"))
    providers = [ProviderConfig(provider_name="default", base_url=main_base_url,
                                api_key=main_api_key, model_name=runtime_config["model"],
                                api_format=main_api_format)]
    for idx in (1, 2):
        if not runtime_config.get(f"fallback{idx}_enabled"):
            continue
        fallback_provider = resolve_provider(runtime_config, runtime_config.get(f"fallback{idx}_provider_ref", ""))
        fallback_base_url = (fallback_provider["base_url"] if fallback_provider
                             else runtime_config.get(f"fallback{idx}_base_url", ""))
        fallback_model = runtime_config.get(f"fallback{idx}_model", "")
        if not (fallback_base_url and fallback_model):
            continue
        if fallback_provider:
            fallback_api_key = fallback_provider["api_key"]
            fallback_api_format = fallback_provider["api_format"]
        else:
            fallback_api_key = runtime_config.get(f"fallback{idx}_api_key") or main_api_key
            fallback_api_format = normalize_api_format(runtime_config.get(f"fallback{idx}_api_format"))
        providers.append(ProviderConfig(
            provider_name=f"fallback{idx}",
            base_url=fallback_base_url,
            api_key=fallback_api_key,
            model_name=fallback_model,
            api_format=fallback_api_format,
            fallback=True,
        ))
    embedding_provider = resolve_provider(runtime_config, runtime_config.get("embedding_provider_ref", ""))
    if embedding_provider:
        emb_base = embedding_provider["base_url"]
        emb_api_key = embedding_provider["api_key"]
    else:
        emb_base = runtime_config.get("embedding_base_url", "")
        emb_api_key = runtime_config.get("embedding_api_key") or main_api_key
    emb_enabled = runtime_config.get("embedding_enabled", False) and bool(emb_base)
    return create_trpg_subsystems(
        data_dir=DATA_DIR, prompts_dir=PROMPTS_DIR,
        rules_dir=RULES_DIR, worlds_dir=WORLDS_DIR,
        providers=providers, default_provider="default",
        embedding_enabled=emb_enabled,
        embedding_base_url=emb_base,
        embedding_api_key=emb_api_key,
        embedding_model=runtime_config.get("embedding_model", "nomic-embed-text"),
        embedding_max_input=int(runtime_config.get("embedding_max_input", 0)),
        proxy_url=effective_proxy_url(bool(runtime_config.get("proxy_enabled")), runtime_config.get("proxy_url", "")),
        narrative_max_tokens=int(runtime_config.get("narrative_max_tokens", DEFAULT_NARRATIVE_MAX_TOKENS)),
        character_gen_max_tokens=int(runtime_config.get("character_gen_max_tokens", 4096)),
        summary_max_tokens=int(runtime_config.get("summary_max_tokens", 1024)),
        brief_max_tokens=int(runtime_config.get("brief_max_tokens", 1024)),
        analysis_max_tokens=int(runtime_config.get("analysis_max_tokens", 1024)),
        reuse=reuse,
    )


def _config_with_resolved_api_refs(config: dict) -> dict:
    """Resolve shared provider references into capability-specific runtime keys."""
    resolved = dict(config)
    tts_provider = resolve_provider(config, config.get("tts_provider_ref", ""))
    if tts_provider:
        resolved["tts_base_url"] = tts_provider["base_url"]
        resolved["tts_api_key"] = tts_provider["api_key"]
    asr_provider = resolve_provider(config, config.get("asr_provider_ref", ""))
    if asr_provider:
        resolved["asr_base_url"] = asr_provider["base_url"]
        resolved["asr_api_key"] = asr_provider["api_key"]
    imagegen_provider = resolve_provider(config, config.get("imagegen_provider_ref", ""))
    if imagegen_provider and imagegen_provider.get("api_format") == "openai":
        resolved["imagegen_base_url"] = imagegen_provider["base_url"]
        resolved["imagegen_api_key"] = imagegen_provider["api_key"]
    return resolved


def _make_api(subsystems: TRPGSubsystems, plugin_host=None, config: dict | None = None, hub_client=None) -> WebAPI:
    runtime_config = STATE if config is None else config
    api_config = _config_with_resolved_api_refs(runtime_config)
    speech_service = SpeechService(
        api_config,
        DATA_DIR / "tts-cache",
        proxy_url=effective_proxy_url(
            bool(runtime_config.get("proxy_enabled")),
            runtime_config.get("proxy_url", ""),
        ),
    )
    asr_service = AsrService(
        api_config,
        proxy_url=effective_proxy_url(
            bool(runtime_config.get("proxy_enabled")),
            runtime_config.get("proxy_url", ""),
        ),
    )
    imagegen_service = ImageGenerationService(
        api_config,
        DATA_DIR / "generated-images",
        proxy_url=effective_proxy_url(
            bool(runtime_config.get("proxy_enabled")),
            runtime_config.get("proxy_url", ""),
        ),
    )
    api = WebAPI(
        registry=subsystems.registry, lorebook=subsystems.lorebook_store,
        memory=subsystems.memory_store, rules_dir=RULES_DIR,
        handler=subsystems.handler, llm_client=subsystems.llm_client,
        worlds_dir=WORLDS_DIR,
        character_gen_max_tokens=int(runtime_config.get("character_gen_max_tokens", 4096)),
        text_gen_max_tokens=int(runtime_config.get("text_gen_max_tokens", 1024)),
        plugin_host=plugin_host,
        hub_client=hub_client,
        speech_service=speech_service,
        asr_service=asr_service,
        imagegen_service=imagegen_service,
    )
    # 配置状态引用就地更新，始终指向最新值（更新频道等运行时配置）
    api._config_state = STATE
    api._content_cache_dir = DATA_DIR / "content-cache"
    # 持久化回调：service 层更新 public_base_url 后走标准写盘路径（见 services/tunnel.py）
    api._save_config = save_config
    return api


def _activate_api_runtime(subsystems: TRPGSubsystems, api: WebAPI) -> None:
    handler = getattr(subsystems, "handler", None)
    if handler is not None and hasattr(handler, "set_image_generation_service"):
        handler.set_image_generation_service(getattr(api, "_imagegen", None))


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
    reset_password = consume_reset_password(DATA_DIR)
    configured_env_password = normalize_access_password(os.getenv("TRPG_ACCESS_TOKEN"))
    stored_password = normalize_access_password(STATE.get("access_token"))
    STATE["access_token"] = stored_password
    if reset_password:
        STATE["access_token"] = hash_access_password(reset_password)
        save_config()
        _delete_access_token_file()
        logger.warning("访问密码已通过 data/reset_access_password.txt 重置，重置文件已删除。")
    elif not is_valid_access_password(stored_password):
        if stored_password:
            logger.warning("保存的访问密码凭证无效，将重新生成首次启动密码。")
        _generate_initial_access_password()
    elif not is_hashed_access_password(stored_password) and not configured_env_password:
        STATE["access_token"] = hash_access_password(stored_password)
        save_config()
        password_file_value = _read_access_token_file()
        if password_file_value and not hmac.compare_digest(password_file_value, stored_password):
            _delete_access_token_file()
            logger.warning("data/access_token.txt 与现有密码不一致，已删除过期文件。")
        logger.info("已将旧版明文访问密码迁移为安全凭证。")
    elif configured_env_password:
        logger.info("使用环境变量 TRPG_ACCESS_TOKEN 配置的访问密码。")
    else:
        password_file_value = _read_access_token_file()
        if password_file_value and not verify_access_password(password_file_value, stored_password):
            _delete_access_token_file()
            password_file_value = ""
            logger.warning("data/access_token.txt 与现有密码不一致，已删除过期文件。")
        if password_file_value:
            logger.info("已加载访问密码；首次启动密码仍可在 data/access_token.txt 查看。")
        else:
            logger.info("已加载访问密码安全凭证；忘记密码请使用 data/reset_access_password.txt 重置。")
    _ensure_bot_token()
    subsystems = _build_subsystems()
    app["subsystems"] = subsystems
    hub_client = None
    try:
        legal_accepted = legal_svc.accepted(STATE)
        hub_client = HubClient(
            DATA_DIR,
            telemetry_enabled=bool(STATE.get("hub_telemetry_enabled")) and legal_accepted,
            telemetry_choice_made=bool(STATE.get("hub_telemetry_choice_made")) and legal_accepted,
        )
        await hub_client.start()
    except ValueError as exc:
        logger.warning("DiceFrame Hub 配置无效，已停用 Hub 接入：%s", exc)
    app["hub_client"] = hub_client
    async def _on_plugin_stopped(plugin_id: str) -> None:
        # 插件被真正停止/卸载时，若它是当前隧道 publisher 则恢复 public_base_url（§3.5）。
        api = app.get("api")
        if api is not None:
            api.release_tunnel_url(plugin_id)

    plugin_host = PluginHost(
        DATA_DIR / "plugin-packages",
        DATA_DIR / "plugins",
        builtin_dir=ROOT / "plugins",
        base_env={"TRPG_API_BASE": f"http://127.0.0.1:{PORT}"},
        on_plugin_stopped=_on_plugin_stopped,
        hub_client=hub_client,
        ai_provider_resolver=lambda provider_id: resolve_provider(STATE, provider_id),
    )
    # 启动时补迁：旧布局便携版根 app/plugins/ 里可能还有用户插件（更新器迁移由旧版本
    # 执行，覆盖不到本次升级），新版本首次启动时由自己补搬一次到 data/plugin-packages/。
    install_root = os.getenv("TRPG_INSTALL_ROOT", "").strip()
    if install_root:
        from src.webui.services.updater import _migrate_user_plugin_packages
        install_root_path = Path(install_root)
        # 根目录旧布局 + versions 下各版本都可能残留用户插件（旧机制装进版本目录）
        sources = [install_root_path / "app" / "plugins"]
        sources.extend(install_root_path.glob("versions/*/app/plugins"))
        for source in sources:
            _migrate_user_plugin_packages(source, DATA_DIR / "plugin-packages")
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
        })
    app["plugin_host"] = plugin_host
    app["api"] = _make_api(subsystems, plugin_host, hub_client=hub_client)
    _activate_api_runtime(subsystems, app["api"])
    app["updater"] = updater_svc.UpdaterService(DATA_DIR, ROOT, plugin_host.mirrors if plugin_host else None)
    recovered = await subsystems.registry.recover_all()
    if recovered:
        logger.info("恢复了 %d 个存档", len(recovered))
    removed_templates = app["api"].cleanup_orphan_game_templates()
    if removed_templates:
        logger.info("已清理 %d 个孤立的对局临时世界模板", removed_templates)
    await plugin_host.start_enabled()
    app["_embedding_backfill_task"] = asyncio.create_task(_embed_pending_memories(app))
    app["_save_task"] = asyncio.create_task(_periodic_save(app))
    # 后台预热 DF 助手的远程文档索引（diceframe-content），失败静默回退内置索引
    from src.webui.assistant_knowledge import prefetch_remote_indexes
    app["_assistant_docs_task"] = asyncio.create_task(prefetch_remote_indexes())


async def on_cleanup(app: web.Application) -> None:
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
        configured_bot_token = str(STATE.get("bot_token") or "")
        global_authenticated = bool(configured_bot_token and hmac.compare_digest(bot_header, configured_bot_token))
        plugin_host = request.app.get("plugin_host")
        plugin_identity = plugin_host.authenticate_api_token(bot_header) if plugin_host else None
        if not global_authenticated and not plugin_identity:
            return web.json_response({"ok": False, "error": "Bot 服务未授权"}, status=401)
        request["bot_authenticated"] = True
        if plugin_identity:
            request["plugin_authenticated"] = plugin_identity
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
        detail = api.game_detail(game_key) if api else None
        if not detail:
            return web.json_response(
                {"ok": False, "error": "游戏不存在", "code": "GAME_NOT_FOUND"},
                status=404,
            )
        actor = str(request.headers.get("X-Bot-Actor") or "").strip()
        if not actor or not api or not api.bot_actor_allowed(game_key, actor):
            return web.json_response(
                {"ok": False, "error": "Bot 代表玩家无效", "code": "BOT_ACTOR_INVALID"},
                status=403,
            )
        if detail.get("player_access_open") is False and actor != detail.get("gm_uid"):
            return web.json_response({"ok": False, "error": "本局玩家入口已关闭"}, status=403)
        request["user_id"] = actor
        request["bot_actor"] = actor
        return await handler(request)

    if request.path.endswith("/sse") and request.query.get("ticket"):
        game_key = _bot_request_game_key(request)
        store = request.app.get("sse_tickets")
        ticket = store.consume(str(request.query.get("ticket") or ""), game_key) if store else None
        if not ticket:
            return web.json_response({"ok": False, "error": "SSE 票据无效或已过期"}, status=401)
        request["user_id"] = ticket.user_id
        request["sse_ticket_authenticated"] = True
        return await handler(request)

    token = normalize_access_password(STATE.get("access_token"))
    access_password_configured = is_valid_access_password(token)
    auth = request.headers.get("Authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    owner_authenticated = bool(access_password_configured and verify_access_password(bearer, token))
    request["owner_authenticated"] = owner_authenticated
    request[ACCESS_PASSWORD_CONFIGURED_KEY] = access_password_configured
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
    # /api/announcements 返回公开公告，登录页横幅与未登录访客可读取
    if request.method == "GET" and request.path == "/api/announcements":
        return await handler(request)
    if request.method == "GET" and request.path.startswith("/api/legal/"):
        return await handler(request)
    # 启动器在更新切换期间没有用户令牌，只读取版本和进程号。
    if request.method == "GET" and request.path == "/api/system/update/health":
        return await handler(request)
    # 登录验证本身必须允许未认证请求进入；handler 只返回验证结果并记审计。
    if request.method == "POST" and request.path == "/api/login":
        return await handler(request)
    # 仅保护 API 端点；HTML 页面和静态资源放行，由前端遇 401 跳 /login 处理登录
    if access_password_configured and request.path.startswith("/api/"):
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
        if request.method == "GET" and tail in {"characters", "character-cards", "log", "private-log", "multiplayer", "sse", "map", "player-context", "avatars", "scene-image", "map-background-asset", "generated-images"}:
            return uid or request.get("user_id", "")
        if request.method == "POST" and tail in {"players", "action", "sse-ticket", "avatars", "scene-image", "generated-images"}:
            return uid or request.get("user_id", "")
        if (
            request.method == "POST"
            and tail == "checks"
            and len(parts) >= 6
            and parts[5] == "luck"
        ):
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
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "配置请求必须是 JSON 对象"}, status=400)
    reload_lock = request.app.get("_config_reload_lock")
    if reload_lock is None:
        reload_lock = asyncio.Lock()
        request.app["_config_reload_lock"] = reload_lock
    async with reload_lock:
        return await _apply_config_update(request, body)


async def _apply_config_update(request: web.Request, body: dict) -> web.Response:
    prepared = prepare_config_update(STATE, body)
    if prepared.error:
        return web.json_response({"ok": False, "error": prepared.error}, status=400)
    access_password_changed = prepared.access_password_changed
    changed_keys = prepared.changed_keys
    model_runtime_changed = bool(changed_keys & MODEL_RUNTIME_CONFIG_KEYS) or provider_runtime_changed(changed_keys)
    api_runtime_changed = bool(changed_keys & API_RUNTIME_CONFIG_KEYS) or provider_runtime_changed(changed_keys)
    old_subs = request.app.get("subsystems")
    plugin_host = request.app.get("plugin_host")
    old_embedding = (
        old_subs.memory_store.embedding_client
        if old_subs is not None and old_subs.memory_store is not None
        else None
    )
    subsystems = old_subs
    new_api = request.app.get("api")
    try:
        # 先用候选配置完整构建，成功后才提交 STATE 和磁盘配置。
        if model_runtime_changed:
            subsystems = _build_subsystems(reuse=old_subs, config=prepared.state)
            new_api = _make_api(subsystems, plugin_host, config=prepared.state)
        elif api_runtime_changed and old_subs is not None:
            new_api = _make_api(old_subs, plugin_host, config=prepared.state)
    except Exception as exc:
        if old_subs is not None and old_subs.memory_store is not None:
            old_subs.memory_store.embedding_client = old_embedding
        if subsystems is not None and subsystems is not old_subs:
            if subsystems.llm_client:
                await subsystems.llm_client.close()
            new_embedding = getattr(subsystems.memory_store, "embedding_client", None)
            if new_embedding is not None and new_embedding is not old_embedding:
                await new_embedding.close()
        logger.exception("配置更新后的运行时重建失败")
        return web.json_response(
            {"ok": False, "error": f"运行时重载失败，配置未保存：{exc}"},
            status=500,
        )

    previous_state = dict(STATE)
    STATE.clear()
    STATE.update(prepared.state)
    try:
        # save_config 保留既有无参约定；同步写盘失败时立即恢复内存状态。
        save_config()
    except Exception as exc:
        STATE.clear()
        STATE.update(previous_state)
        if old_subs is not None and old_subs.memory_store is not None:
            old_subs.memory_store.embedding_client = old_embedding
        if subsystems is not None and subsystems is not old_subs:
            if subsystems.llm_client:
                await subsystems.llm_client.close()
            candidate_embedding = getattr(subsystems.memory_store, "embedding_client", None)
            if candidate_embedding is not None and candidate_embedding is not old_embedding:
                await candidate_embedding.close()
        logger.exception("保存候选配置失败")
        return web.json_response({"ok": False, "error": f"配置保存失败：{exc}"}, status=500)

    if access_password_changed:
        _delete_access_token_file()

    plugin_warning = ""
    plugin_changes = bot_plugin_changes(body, STATE)
    if plugin_changes and plugin_host and "qq-napcat" in plugin_host.plugins:
        try:
            await plugin_host.update_config("qq-napcat", plugin_changes)
        except Exception as exc:
            plugin_warning = f"NapCat 插件配置同步失败：{exc}"
            logger.exception("NapCat 插件配置同步失败")

    if plugin_host and ("ai_providers" in changed_keys or provider_runtime_changed(changed_keys)):
        try:
            await plugin_host.restart_ai_provider_consumers()
        except Exception as exc:
            provider_warning = f"AI 服务商插件重启失败：{exc}"
            plugin_warning = f"{plugin_warning}；{provider_warning}" if plugin_warning else provider_warning
            logger.exception("AI 服务商插件重启失败")

    if model_runtime_changed and subsystems is not None:
        _activate_api_runtime(subsystems, new_api)
        request.app["subsystems"] = subsystems
        request.app["api"] = new_api
    elif api_runtime_changed and new_api is not None:
        _activate_api_runtime(old_subs, new_api)
        request.app["api"] = new_api

    if model_runtime_changed and old_subs is not None and subsystems is not None:
        if old_subs.llm_client and old_subs.llm_client is not subsystems.llm_client:
            try:
                await old_subs.llm_client.close()
            except Exception:
                logger.warning("关闭旧模型客户端失败", exc_info=True)
        new_embedding = getattr(subsystems.memory_store, "embedding_client", None)
        if old_embedding is not None and old_embedding is not new_embedding:
            try:
                await old_embedding.close()
            except Exception:
                logger.warning("关闭旧 Embedding 客户端失败", exc_info=True)
    # 配置更新后，如果 embedding 已启用，立即补齐存量记忆的向量
    emb_now = STATE.get("embedding_enabled", False) and bool(
        STATE.get("embedding_base_url", "") or resolve_provider(STATE, STATE.get("embedding_provider_ref", "")))
    if model_runtime_changed and emb_now and subsystems is not None:
        try:
            count = await subsystems.memory_store.embed_all_pending()
            if count:
                logger.info("[Embedding] 配置更新后补齐 %d 条向量记忆", count)
        except Exception:
            logger.warning("配置更新后 embedding 补齐失败", exc_info=True)
    payload = {"ok": True, "access_password_changed": access_password_changed}
    if plugin_warning:
        payload["warning"] = plugin_warning
    return web.json_response(payload)


async def api_bot_token_post(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    body = await request.json()
    action = str(body.get("action") or "reveal").strip().lower()
    if action not in {"reveal", "regenerate"}:
        return web.json_response({"ok": False, "error": "不支持的 Bot Token 操作"}, status=400)

    regenerated = action == "regenerate"
    if regenerated:
        if os.getenv("TRPG_BOT_TOKEN"):
            return web.json_response({
                "ok": False,
                "error": "Bot API Token 由环境变量 TRPG_BOT_TOKEN 管理，请修改环境变量后重启",
            }, status=409)
        import secrets as _secrets
        token = _secrets.token_urlsafe(32)
        STATE["bot_token"] = token
        save_config()
    else:
        token = _ensure_bot_token()

    return web.json_response({
        "ok": True,
        "token": token,
        "masked": _mask_secret(token)["masked"],
        "regenerated": regenerated,
    })


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
    # provider_id 指定服务商时，凭据默认取自服务商库（明文 key 只存在于服务端）；
    # body 里的显式明文输入仍然优先，供未保存前直接测试。
    provider = resolve_provider(STATE, str(body.get("provider_id") or ""))
    if provider:
        base_url = clean_text_value(body.get("base_url")) or provider["base_url"]
        api_key = clean_text_value(body.get("api_key")) or provider["api_key"]
        api_format = normalize_api_format(body.get("api_format") or provider["api_format"])
    else:
        base_url = clean_text_value(body.get("base_url")) or STATE.get("base_url", "")
        api_key = clean_text_value(body.get("api_key")) or STATE.get("api_key", "")
        api_format = normalize_api_format(body.get("api_format") or STATE.get("api_format"))
    if not _is_safe_external_url(base_url):
        return web.json_response({"ok": False, "error": "base_url 非法或不允许"}, status=400)
    proxy_url = _proxy_from_test_body(body)
    if proxy_url and not is_supported_proxy_url(proxy_url):
        return web.json_response({"ok": False, "error": "代理地址仅支持 http:// 或 https://"}, status=400)
    result = await _get_api(request).test_connection(
        base_url=base_url,
        api_key=api_key,
        model=clean_text_value(body.get("model")) or STATE.get("model", ""),
        proxy_url=proxy_url,
        api_format=api_format,
    )
    return web.json_response(result)


async def api_config_provider_models_post(request: web.Request) -> web.Response:
    body = await request.json()
    provider = resolve_provider(STATE, str(body.get("provider_id") or ""))
    if provider:
        base_url = clean_text_value(body.get("base_url")) or provider["base_url"]
        api_key = clean_text_value(body.get("api_key")) or provider["api_key"]
        api_format = normalize_api_format(body.get("api_format") or provider["api_format"])
    else:
        base_url = clean_text_value(body.get("base_url"))
        api_key = clean_text_value(body.get("api_key"))
        api_format = normalize_api_format(body.get("api_format"))
    if not _is_safe_external_url(base_url):
        return web.json_response({"ok": False, "error": "base_url 非法或不允许", "models": []}, status=400)
    proxy_url = _proxy_from_test_body(body)
    if proxy_url and not is_supported_proxy_url(proxy_url):
        return web.json_response({"ok": False, "error": "代理地址仅支持 http:// 或 https://", "models": []}, status=400)
    result = await _get_api(request).list_models(
        base_url=base_url,
        api_key=api_key,
        proxy_url=proxy_url,
        api_format=api_format,
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
    provider = resolve_provider(STATE, str(body.get("provider_id") or ""))
    if provider:
        base_url = clean_text_value(body.get("base_url")) or provider["base_url"]
        api_key = clean_text_value(body.get("api_key")) or provider["api_key"]
    else:
        base_url = clean_text_value(body.get("base_url"))
        api_key = clean_text_value(body.get("api_key")) or STATE.get("embedding_api_key") or STATE.get("api_key", "")
    model = clean_text_value(body.get("model")) or "nomic-embed-text"
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
        timeout_seconds=connection_test_timeout(STATE),
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
        timeout = aiohttp.ClientTimeout(total=connection_test_timeout(STATE))
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


app = web.Application(client_max_size=MAX_PLUGIN_PACKAGE_BYTES + 1024 * 1024)
app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

from src.webui.connection_pool import ConnectionPool
from src.webui.session import SessionManager, session_middleware
from src.webui.sse_ticket import SseTicketStore
from src.webui.errors import error_code_middleware

app.middlewares.append(session_middleware)
app.middlewares.append(abuse_guard_middleware)
app.middlewares.append(auth_middleware)
app.middlewares.append(error_code_middleware)
app.on_response_prepare.append(add_response_security_headers)
app["_config_reload_lock"] = asyncio.Lock()
app["session_manager"] = SessionManager(DATA_DIR)
app[ABUSE_GUARD_KEY] = AbuseGuard()
app[LOGIN_AUDIT_KEY] = LoginAuditStore(DATA_DIR)
app["connection_pool"] = ConnectionPool()
app["sse_tickets"] = SseTicketStore()
app["static_v2_dir"] = STATIC_V2_DIR
app["runtime_control"] = {
        "boot_id": secrets_module.token_hex(8),
    "restart_requested": False,
    "restart_task": None,
}

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
    # worlds / lorebook
    register_worlds(application)
    # rules
    register_rules(application)
    # character cards
    register_character_cards(application)
    # character portraits
    register_avatars(application)
    # adventure scene images
    register_scene_images(application)
    register_maps(application)
    # config / test
    application.router.add_get("/api/config", api_config_get)
    application.router.add_post("/api/config", api_config_post)
    application.router.add_post("/api/config/bot-token", api_bot_token_post)
    application.router.add_post("/api/config/providers/models", api_config_provider_models_post)
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
    if app["runtime_control"]["restart_requested"]:
        logger.info("DiceFrame 清理完成，正在重新启动")
        os.execv(sys.executable, [sys.executable, *sys.argv])
