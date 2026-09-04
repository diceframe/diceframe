"""Load, expose, and persist WebUI runtime configuration.

The loader is the single place that applies the historical precedence rules:
environment variables override secrets, which override ordinary config values.
The state remains a dictionary because many released settings are intentionally
dynamic; the typed boundary covers paths and startup-only metadata instead of
pretending every historical key is already strongly typed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from src.ai_providers import (
    is_provider_secret_key,
    normalize_ai_providers,
    provider_secret_key,
)
from src.migrations.config import (
    DEFAULT_NARRATIVE_MAX_TOKENS,
    GENERATION_DEFAULTS_VERSION,
    migrate_generation_defaults,
)
from src.network_proxy import (
    effective_proxy_url,
    is_supported_proxy_url,
    mask_proxy_url,
)
from src.web_transport import ServerTransport, build_server_transport, parse_web_transport
from src.webui.access_password import mask_access_password, normalize_access_password
from src.webui.cors import normalize_cors_origins, parse_cors_origins
from src.webui.services import legal as legal_svc


_SECRET_KEYS = frozenset(
    {
        "api_key",
        "embedding_api_key",
        "fallback1_api_key",
        "fallback2_api_key",
        "tts_api_key",
        "asr_api_key",
        "imagegen_api_key",
        "access_token",
        "bot_token",
        "napcat_token",
        "proxy_url",
    }
)


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    data_dir: Path
    config_file: Path
    secrets_file: Path
    access_token_file: Path
    prompts_dir: Path
    builtin_rules_dir: Path
    builtin_worlds_dir: Path
    builtin_adventures_dir: Path
    rules_dir: Path
    worlds_dir: Path
    adventures_dir: Path
    static_v2_dir: Path

    @classmethod
    def from_root(
        cls,
        root: Path,
        environ: Mapping[str, str],
    ) -> RuntimePaths:
        data_dir = Path(environ.get("TRPG_DATA_DIR") or root / "data")
        return cls(
            root=root,
            data_dir=data_dir,
            config_file=data_dir / "config.json",
            secrets_file=data_dir / "secrets.json",
            access_token_file=data_dir / "access_token.txt",
            prompts_dir=root / "prompts",
            builtin_rules_dir=root / "templates" / "rules",
            builtin_worlds_dir=root / "templates" / "worlds",
            builtin_adventures_dir=root / "templates" / "adventures",
            rules_dir=data_dir / "templates" / "rules",
            worlds_dir=data_dir / "templates" / "worlds",
            adventures_dir=data_dir / "templates" / "adventures",
            static_v2_dir=root / "static-v2",
        )


@dataclass
class RuntimeConfig:
    paths: RuntimePaths
    state: dict[str, Any]
    saved: dict[str, Any]
    secrets: dict[str, Any]
    host: str
    port: int
    transport: ServerTransport
    cors_env_value: str
    cors_config_value: str
    cors_origins: frozenset[str]
    env_proxy_url: str
    config_proxy_url: str
    generation_defaults_migrated: bool


class ConfigStore:
    def __init__(
        self,
        paths: RuntimePaths,
        environ: Mapping[str, str],
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.paths = paths
        self.environ = environ
        self.logger = logger or logging.getLogger("trpg")

    def quarantine_invalid_json(self, path: Path) -> Path | None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        candidate = path.with_name(f"{path.stem}.corrupt-{timestamp}{path.suffix}")
        index = 1
        while candidate.exists():
            candidate = path.with_name(
                f"{path.stem}.corrupt-{timestamp}-{index}{path.suffix}"
            )
            index += 1
        try:
            path.replace(candidate)
        except OSError:
            self.logger.exception("无法隔离损坏的配置文件: %s", path)
            return None
        return candidate

    def load_json_object(self, path: Path, label: str) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, dict):
                raise ValueError("JSON 根节点不是对象")
            return data
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            backup = self.quarantine_invalid_json(path)
            if backup:
                self.logger.error("%s损坏，已保留为 %s：%s", label, backup, exc)
            else:
                self.logger.error("%s无法读取且未能隔离：%s", label, exc)
            return {}

    def load(self) -> RuntimeConfig:
        self.paths.data_dir.mkdir(parents=True, exist_ok=True)
        saved = self.load_json_object(self.paths.config_file, "主配置")
        secret_values = self.load_json_object(self.paths.secrets_file, "敏感配置")
        generation_defaults_migrated = migrate_generation_defaults(saved)
        env = self.environ

        api_key = env.get("TRPG_LLM_API_KEY") or secret_values.get("api_key") or ""
        base_url = env.get("TRPG_LLM_BASE_URL") or saved.get(
            "base_url", "https://api.deepseek.com/v1"
        )
        model = env.get("TRPG_LLM_MODEL") or saved.get("model", "deepseek-v4-flash")
        api_format = env.get("TRPG_LLM_API_FORMAT") or saved.get(
            "api_format", "openai"
        )
        port = int(env.get("TRPG_WEB_PORT") or saved.get("web_port", 18000))
        host = str(env.get("TRPG_WEB_HOST") or saved.get("web_host", "0.0.0.0"))
        transport_config = parse_web_transport(saved.get("web_transport"), env)
        transport = build_server_transport(
            transport_config,
            self.paths.data_dir,
            port,
        )
        cors_env_value = str(env.get("TRPG_WEB_CORS_ORIGINS") or "").strip()
        cors_config_value = cors_env_value or str(saved.get("web_cors_origins") or "")
        embedding_enabled = saved.get("embedding_enabled", False)
        embedding_base_url = saved.get("embedding_base_url", "")
        embedding_model = env.get("TRPG_EMBEDDING_MODEL") or saved.get(
            "embedding_model", "nomic-embed-text"
        )
        embedding_api_key = (
            env.get("TRPG_EMBEDDING_API_KEY")
            or secret_values.get("embedding_api_key")
            or ""
        )
        access_token = next(
            (
                password
                for password in (
                    normalize_access_password(env.get("TRPG_ACCESS_TOKEN")),
                    normalize_access_password(secret_values.get("access_token")),
                    normalize_access_password(saved.get("access_token")),
                )
                if password
            ),
            "",
        )
        env_proxy_url = self._env_proxy_url(env)
        config_proxy_url = secret_values.get("proxy_url") or saved.get(
            "proxy_url", ""
        )
        proxy_enabled = bool(saved.get("proxy_enabled", bool(env_proxy_url)))
        proxy_url = env.get("TRPG_PROXY_URL") or config_proxy_url or env_proxy_url

        state: dict[str, Any] = {
            "generation_defaults_version": GENERATION_DEFAULTS_VERSION,
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "api_format": api_format,
            "web_port": port,
            "web_cors_origins": normalize_cors_origins(cors_config_value),
            "ai_providers": normalize_ai_providers(saved.get("ai_providers")),
            "llm_provider_ref": str(saved.get("llm_provider_ref", "")),
            "fallback1_provider_ref": str(saved.get("fallback1_provider_ref", "")),
            "fallback2_provider_ref": str(saved.get("fallback2_provider_ref", "")),
            "embedding_provider_ref": str(saved.get("embedding_provider_ref", "")),
            "tts_provider_ref": str(saved.get("tts_provider_ref", "")),
            "asr_provider_ref": str(saved.get("asr_provider_ref", "")),
            "imagegen_provider_ref": str(saved.get("imagegen_provider_ref", "")),
            **{
                key: str(value or "")
                for key, value in secret_values.items()
                if is_provider_secret_key(key)
            },
            "embedding_enabled": embedding_enabled,
            "embedding_base_url": embedding_base_url,
            "embedding_model": embedding_model,
            "embedding_api_key": embedding_api_key,
            "fallback1_enabled": saved.get("fallback1_enabled", False),
            "fallback1_base_url": saved.get("fallback1_base_url", ""),
            "fallback1_model": saved.get("fallback1_model", ""),
            "fallback1_api_format": saved.get("fallback1_api_format", "openai"),
            "fallback1_api_key": secret_values.get("fallback1_api_key") or "",
            "fallback2_enabled": saved.get("fallback2_enabled", False),
            "fallback2_base_url": saved.get("fallback2_base_url", ""),
            "fallback2_model": saved.get("fallback2_model", ""),
            "fallback2_api_format": saved.get("fallback2_api_format", "openai"),
            "fallback2_api_key": secret_values.get("fallback2_api_key") or "",
            "tts_provider": str(
                env.get("TRPG_TTS_PROVIDER") or saved.get("tts_provider", "browser")
            ),
            "tts_base_url": str(
                env.get("TRPG_TTS_BASE_URL") or saved.get("tts_base_url", "")
            ),
            "tts_api_key": env.get("TRPG_TTS_API_KEY")
            or secret_values.get("tts_api_key")
            or "",
            "tts_model": str(
                env.get("TRPG_TTS_MODEL") or saved.get("tts_model", "tts-1")
            ),
            "tts_audio_format": str(
                env.get("TRPG_TTS_AUDIO_FORMAT")
                or saved.get("tts_audio_format", "mp3")
            ),
            "tts_default_voice": str(
                env.get("TRPG_TTS_VOICE") or saved.get("tts_default_voice", "alloy")
            ),
            "tts_gm_voice": str(saved.get("tts_gm_voice", "")),
            "tts_player_voice": str(saved.get("tts_player_voice", "")),
            "tts_timeout_seconds": float(saved.get("tts_timeout_seconds", 60)),
            "tts_cache_mb": int(saved.get("tts_cache_mb", 256)),
            "asr_provider": str(
                env.get("TRPG_ASR_PROVIDER") or saved.get("asr_provider", "disabled")
            ),
            "asr_base_url": str(
                env.get("TRPG_ASR_BASE_URL") or saved.get("asr_base_url", "")
            ),
            "asr_api_key": env.get("TRPG_ASR_API_KEY")
            or secret_values.get("asr_api_key")
            or "",
            "asr_model": str(
                env.get("TRPG_ASR_MODEL") or saved.get("asr_model", "whisper-1")
            ),
            "asr_timeout_seconds": float(saved.get("asr_timeout_seconds", 60)),
            "imagegen_enabled": bool(saved.get("imagegen_enabled", False)),
            "imagegen_auto_scene": bool(saved.get("imagegen_auto_scene", True)),
            "imagegen_provider": str(
                saved.get("imagegen_provider") or "openai-compatible"
            ),
            "imagegen_base_url": str(
                env.get("TRPG_IMAGEGEN_BASE_URL")
                or saved.get("imagegen_base_url", "")
            ),
            "imagegen_api_key": env.get("TRPG_IMAGEGEN_API_KEY")
            or secret_values.get("imagegen_api_key")
            or "",
            "imagegen_model": str(
                env.get("TRPG_IMAGEGEN_MODEL") or saved.get("imagegen_model", "")
            ),
            "imagegen_square_size": str(
                saved.get("imagegen_square_size", "1024x1024")
            ),
            "imagegen_landscape_size": str(
                saved.get("imagegen_landscape_size", "1792x1024")
            ),
            "imagegen_quality": str(saved.get("imagegen_quality", "")),
            "imagegen_style_prefix": str(saved.get("imagegen_style_prefix", "")),
            "imagegen_timeout_seconds": float(
                saved.get("imagegen_timeout_seconds", 120)
            ),
            "test_timeout_seconds": float(saved.get("test_timeout_seconds", 30)),
            "economy_auto_reward_enabled": bool(
                saved.get("economy_auto_reward_enabled", True)
            ),
            "economy_auto_reward_gold_cap": int(
                saved.get("economy_auto_reward_gold_cap", 50)
            ),
            "model_request_timeout_seconds": float(
                env.get("TRPG_MODEL_REQUEST_TIMEOUT_SECONDS")
                or saved.get("model_request_timeout_seconds", 120)
            ),
            "narrative_max_tokens": int(
                env.get("TRPG_NARRATIVE_MAX_TOKENS")
                or saved.get(
                    "narrative_max_tokens", DEFAULT_NARRATIVE_MAX_TOKENS
                )
            ),
            "character_gen_max_tokens": int(
                env.get("TRPG_CHARACTER_GEN_MAX_TOKENS")
                or saved.get("character_gen_max_tokens", 2048)
            ),
            "summary_max_tokens": int(
                env.get("TRPG_SUMMARY_MAX_TOKENS")
                or saved.get("summary_max_tokens", 1024)
            ),
            "brief_max_tokens": int(
                env.get("TRPG_BRIEF_MAX_TOKENS")
                or saved.get("brief_max_tokens", 1024)
            ),
            "analysis_max_tokens": int(
                env.get("TRPG_ANALYSIS_MAX_TOKENS")
                or saved.get("analysis_max_tokens", 1024)
            ),
            "text_gen_max_tokens": int(
                env.get("TRPG_TEXT_GEN_MAX_TOKENS")
                or saved.get("text_gen_max_tokens", 1024)
            ),
            "access_token": access_token,
            "bot_token": env.get("TRPG_BOT_TOKEN")
            or secret_values.get("bot_token")
            or saved.get("bot_token", ""),
            "update_channel": saved.get("update_channel", "stable"),
            "qq_bot_enabled": bool(saved.get("qq_bot_enabled", False)),
            "qq_bot_running": False,
            "napcat_host": env.get("NAPCAT_HOST")
            or saved.get("napcat_host", "127.0.0.1"),
            "napcat_port": int(
                env.get("NAPCAT_PORT") or saved.get("napcat_port", 3001)
            ),
            "napcat_token": env.get("NAPCAT_TOKEN")
            or secret_values.get("napcat_token")
            or saved.get("napcat_token", ""),
            "napcat_heartbeat_sec": float(
                env.get("NAPCAT_HEARTBEAT_SEC")
                or saved.get("napcat_heartbeat_sec", 30)
            ),
            "napcat_reconnect_delay_sec": float(
                env.get("NAPCAT_RECONNECT_DELAY_SEC")
                or saved.get("napcat_reconnect_delay_sec", 5)
            ),
            "napcat_action_timeout_sec": float(
                env.get("NAPCAT_ACTION_TIMEOUT_SEC")
                or saved.get("napcat_action_timeout_sec", 15)
            ),
            "napcat_reply_delay_min_sec": float(
                env.get("NAPCAT_REPLY_DELAY_MIN_SEC")
                or saved.get("napcat_reply_delay_min_sec", 0.8)
            ),
            "napcat_reply_delay_max_sec": float(
                env.get("NAPCAT_REPLY_DELAY_MAX_SEC")
                or saved.get("napcat_reply_delay_max_sec", 2.4)
            ),
            "napcat_command_dedup_window_sec": float(
                env.get("NAPCAT_COMMAND_DEDUP_WINDOW_SEC")
                or saved.get("napcat_command_dedup_window_sec", 6)
            ),
            "napcat_connection_id": env.get("NAPCAT_CONNECTION_ID")
            or str(saved.get("napcat_connection_id", "")),
            "napcat_chat_filter_enabled": bool(
                saved.get("napcat_chat_filter_enabled", False)
            ),
            "napcat_show_dropped_logs": bool(
                saved.get("napcat_show_dropped_logs", False)
            ),
            "napcat_group_list_mode": saved.get(
                "napcat_group_list_mode", "whitelist"
            ),
            "napcat_group_list": saved.get("napcat_group_list", []),
            "napcat_private_list_mode": saved.get(
                "napcat_private_list_mode", "whitelist"
            ),
            "napcat_private_list": saved.get("napcat_private_list", []),
            "napcat_blocked_users": saved.get("napcat_blocked_users", []),
            "napcat_block_official_bots": bool(
                saved.get("napcat_block_official_bots", True)
            ),
            "proxy_enabled": proxy_enabled,
            "proxy_url": proxy_url,
            "public_base_url": str(saved.get("public_base_url", "")),
            "hub_telemetry_enabled": bool(
                saved.get("hub_telemetry_enabled", False)
            ),
            "hub_telemetry_choice_made": bool(
                saved.get("hub_telemetry_choice_made", False)
            ),
            **legal_svc.persisted_acceptance_state(saved),
            "legal_privacy_acknowledged_version": saved.get(
                "legal_privacy_acknowledged_version", ""
            ),
            "web_transport": dict(saved.get("web_transport") or {}),
        }
        return RuntimeConfig(
            paths=self.paths,
            state=state,
            saved=saved,
            secrets=secret_values,
            host=host,
            port=port,
            transport=transport,
            cors_env_value=cors_env_value,
            cors_config_value=cors_config_value,
            cors_origins=parse_cors_origins(cors_config_value),
            env_proxy_url=env_proxy_url,
            config_proxy_url=str(config_proxy_url or ""),
            generation_defaults_migrated=generation_defaults_migrated,
        )

    def public_view(self, runtime: RuntimeConfig) -> dict[str, Any]:
        state = runtime.state
        public = {
            key: value
            for key, value in state.items()
            if key not in _SECRET_KEYS
            and key != "web_transport"
            and not is_provider_secret_key(key)
        }
        public["ai_providers"] = [
            {
                **entry,
                "api_key": self.mask_secret(
                    state.get(provider_secret_key(entry["id"]), "")
                ),
            }
            for entry in state.get("ai_providers", [])
        ]
        for key in (
            "api_key",
            "embedding_api_key",
            "fallback1_api_key",
            "fallback2_api_key",
            "tts_api_key",
            "asr_api_key",
            "imagegen_api_key",
            "bot_token",
            "napcat_token",
        ):
            public[key] = self.mask_secret(str(state.get(key, "")))
        public["access_password"] = mask_access_password(state.get("access_token", ""))
        public["bot_token_source"] = (
            "env" if self.environ.get("TRPG_BOT_TOKEN") else "generated"
        )
        public["web_cors_origins_source"] = (
            "env" if runtime.cors_env_value else "config"
        )
        proxy_url = str(state.get("proxy_url", ""))
        public["proxy_url"] = mask_proxy_url(proxy_url)
        if not state.get("proxy_enabled"):
            public["proxy_source"] = "disabled"
        elif runtime.config_proxy_url or (
            proxy_url and proxy_url != runtime.env_proxy_url
        ):
            public["proxy_source"] = "config"
        elif runtime.env_proxy_url:
            public["proxy_source"] = "env"
        else:
            public["proxy_source"] = "empty"
        public["proxy_supported"] = is_supported_proxy_url(
            effective_proxy_url(bool(state.get("proxy_enabled")), proxy_url)
        )
        return public

    def save(self, state: Mapping[str, Any]) -> None:
        non_sensitive = {
            key: value
            for key, value in state.items()
            if key not in _SECRET_KEYS
            and key != "qq_bot_running"
            and not is_provider_secret_key(key)
        }
        self.atomic_write_json(self.paths.config_file, non_sensitive)
        sensitive = {
            key: value
            for key, value in state.items()
            if key in _SECRET_KEYS or is_provider_secret_key(key)
        }
        if self.environ.get("TRPG_ACCESS_TOKEN"):
            sensitive.pop("access_token", None)
        if any(sensitive.values()) or self.paths.secrets_file.exists():
            self.atomic_write_json(self.paths.secrets_file, sensitive)

    @staticmethod
    def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def mask_secret(value: str) -> dict[str, Any]:
        if not value:
            return {"configured": False, "masked": ""}
        return {"configured": True, "masked": f"***{value[-4:]}"}

    @staticmethod
    def _env_proxy_url(environ: Mapping[str, str]) -> str:
        for name in (
            "TRPG_PROXY_URL",
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
        ):
            value = str(environ.get(name) or "").strip()
            if value:
                return value
        return ""
