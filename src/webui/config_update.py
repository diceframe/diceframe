"""运行时配置更新的纯校验与归一化。

先在副本上完成全部校验，再由 composition root 一次性提交，避免无效请求
把全局 STATE 留在半更新状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.network_proxy import effective_proxy_url, is_supported_proxy_url
from src.ai_providers import (
    PROVIDER_REF_KEYS,
    is_provider_secret_key,
    normalize_ai_providers,
    reconcile_model_capability_routes,
    strip_dangling_provider_refs,
    strip_orphan_provider_secrets,
)
from src.asr.contracts import SUPPORTED_ASR_PROVIDER_IDS
from src.imagegen.contracts import IMAGE_PROVIDER_IDS
from src.tts.contracts import SUPPORTED_PROVIDER_IDS
from src.webui.access_password import hash_access_password
from src.webui.cors import invalid_cors_origins, normalize_cors_origins

SECRET_CONFIG_KEYS = frozenset({
    "api_key", "embedding_api_key", "fallback1_api_key", "fallback2_api_key",
    "access_token", "bot_token", "napcat_token", "tts_api_key", "asr_api_key", "imagegen_api_key",
})
STRING_CONFIG_KEYS = frozenset({
    "base_url", "model", "embedding_base_url", "embedding_model", "web_cors_origins",
    "fallback1_base_url", "fallback1_model", "fallback2_base_url", "fallback2_model",
    "public_base_url", "napcat_host", "napcat_connection_id",
    "tts_base_url", "tts_model", "tts_default_voice", "tts_gm_voice", "tts_player_voice",
    "asr_base_url", "asr_model",
    "imagegen_base_url", "imagegen_model", "imagegen_square_size",
    "imagegen_landscape_size", "imagegen_quality", "imagegen_style_prefix",
    *PROVIDER_REF_KEYS,
})
API_FORMAT_KEYS = frozenset({"api_format", "fallback1_api_format", "fallback2_api_format"})
CONFIG_KEYS = (
    "api_key", "base_url", "model", "api_format", "web_port", "web_cors_origins", "embedding_enabled",
    "embedding_base_url", "embedding_model", "embedding_api_key", "embedding_max_input",
    "fallback1_enabled", "fallback1_base_url", "fallback1_model", "fallback1_api_format", "fallback1_api_key",
    "fallback2_enabled", "fallback2_base_url", "fallback2_model", "fallback2_api_format", "fallback2_api_key",
    "narrative_max_tokens", "character_gen_max_tokens", "summary_max_tokens", "brief_max_tokens",
    "analysis_max_tokens", "text_gen_max_tokens", "proxy_enabled", "proxy_url", "public_base_url", "access_token",
    "qq_bot_enabled", "napcat_host", "napcat_port", "napcat_token", "napcat_heartbeat_sec",
    "napcat_reconnect_delay_sec", "napcat_action_timeout_sec", "napcat_reply_delay_min_sec",
    "napcat_reply_delay_max_sec", "napcat_command_dedup_window_sec", "napcat_connection_id",
    "napcat_chat_filter_enabled", "napcat_show_dropped_logs", "napcat_group_list_mode", "napcat_group_list",
    "napcat_private_list_mode", "napcat_private_list", "napcat_blocked_users", "napcat_block_official_bots",
    "update_channel",
    "tts_provider", "tts_base_url", "tts_api_key", "tts_model", "tts_audio_format",
    "tts_default_voice", "tts_gm_voice", "tts_player_voice", "tts_timeout_seconds", "tts_cache_mb",
    "asr_provider", "asr_base_url", "asr_api_key", "asr_model", "asr_timeout_seconds",
    "imagegen_enabled", "imagegen_auto_scene", "imagegen_provider", "imagegen_base_url",
    "imagegen_api_key", "imagegen_model", "imagegen_square_size", "imagegen_landscape_size",
    "imagegen_quality", "imagegen_style_prefix", "imagegen_timeout_seconds", "test_timeout_seconds",
    "economy_auto_reward_enabled", "economy_auto_reward_gold_cap",
    "model_request_timeout_seconds",
    "ai_providers", *PROVIDER_REF_KEYS,
)
MODEL_RUNTIME_CONFIG_KEYS = frozenset({
    "api_key", "base_url", "model", "api_format",
    "embedding_enabled", "embedding_base_url", "embedding_model", "embedding_api_key", "embedding_max_input",
    "fallback1_enabled", "fallback1_base_url", "fallback1_model", "fallback1_api_format", "fallback1_api_key",
    "fallback2_enabled", "fallback2_base_url", "fallback2_model", "fallback2_api_format", "fallback2_api_key",
    "narrative_max_tokens", "summary_max_tokens", "brief_max_tokens", "analysis_max_tokens",
    "model_request_timeout_seconds",
    "proxy_enabled", "proxy_url",
    "ai_providers", "llm_provider_ref", "fallback1_provider_ref", "fallback2_provider_ref",
    "embedding_provider_ref",
})
API_RUNTIME_CONFIG_KEYS = frozenset({
    "character_gen_max_tokens", "text_gen_max_tokens",
    "tts_provider", "tts_base_url", "tts_api_key", "tts_model", "tts_audio_format",
    "tts_default_voice", "tts_gm_voice", "tts_player_voice", "tts_timeout_seconds", "tts_cache_mb",
    "asr_provider", "asr_base_url", "asr_api_key", "asr_model", "asr_timeout_seconds",
    "imagegen_enabled", "imagegen_auto_scene", "imagegen_provider", "imagegen_base_url",
    "imagegen_api_key", "imagegen_model", "imagegen_square_size", "imagegen_landscape_size",
    "imagegen_quality", "imagegen_style_prefix", "imagegen_timeout_seconds",
    "ai_providers", "tts_provider_ref", "asr_provider_ref", "imagegen_provider_ref",
})


def connection_test_timeout(config: dict[str, Any]) -> float:
    try:
        value = float(config.get("test_timeout_seconds", 30))
    except (TypeError, ValueError):
        value = 30
    return max(5.0, min(value, 300.0))


def provider_runtime_changed(changed_keys: frozenset[str]) -> bool:
    """服务商库或其 secret 有变动：影响所有引用它的能力，两组运行时都要重建。"""
    return any(is_provider_secret_key(key) for key in changed_keys)


def _degrade_speech_engines_lost_provider(candidate: dict[str, Any], refs_before: dict[str, str]) -> None:
    """服务商被删除导致 TTS/ASR 引用悬空且无内联地址时，回退到零配置引擎。

    否则 Speech/Asr 服务构造校验会失败，把"删除服务商"整个操作卡死。
    仅在引用因删除被清空的场景触发；手动开启引擎但不填地址仍走原有报错。
    """
    tts_ref_cleared = bool(refs_before.get("tts_provider_ref")) and not str(candidate.get("tts_provider_ref") or "").strip()
    if (tts_ref_cleared
            and candidate.get("tts_provider") == "openai-compatible"
            and not str(candidate.get("tts_base_url") or "").strip()):
        candidate["tts_provider"] = "browser"
    asr_ref_cleared = bool(refs_before.get("asr_provider_ref")) and not str(candidate.get("asr_provider_ref") or "").strip()
    if (asr_ref_cleared
            and candidate.get("asr_provider") == "openai-compatible"
            and not str(candidate.get("asr_base_url") or "").strip()):
        candidate["asr_provider"] = "disabled"

    imagegen_ref_cleared = bool(refs_before.get("imagegen_provider_ref")) and not str(
        candidate.get("imagegen_provider_ref") or ""
    ).strip()
    if imagegen_ref_cleared and not str(candidate.get("imagegen_base_url") or "").strip():
        candidate["imagegen_enabled"] = False
BOT_CONFIG_MAP = {
    "qq_bot_enabled": "enabled",
    "napcat_host": "host",
    "napcat_port": "port",
    "napcat_token": "token",
    "napcat_heartbeat_sec": "heartbeat_sec",
    "napcat_reconnect_delay_sec": "reconnect_delay_sec",
    "napcat_action_timeout_sec": "action_timeout_sec",
    "napcat_reply_delay_min_sec": "reply_delay_min_sec",
    "napcat_reply_delay_max_sec": "reply_delay_max_sec",
    "napcat_command_dedup_window_sec": "command_dedup_window_sec",
    "napcat_connection_id": "connection_id",
    "napcat_chat_filter_enabled": "chat_filter_enabled",
    "napcat_show_dropped_logs": "show_dropped_logs",
    "napcat_group_list_mode": "group_list_mode",
    "napcat_group_list": "group_list",
    "napcat_private_list_mode": "private_list_mode",
    "napcat_private_list": "private_list",
    "napcat_blocked_users": "blocked_users",
    "napcat_block_official_bots": "block_official_bots",
}


@dataclass(frozen=True)
class PreparedConfigUpdate:
    state: dict[str, Any]
    changed_keys: frozenset[str]
    access_password_changed: bool
    error: str = ""
    warnings: tuple[str, ...] = ()


def clean_text_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_api_format(value: Any) -> str:
    return "anthropic" if clean_text_value(value).lower() == "anthropic" else "openai"


def prepare_config_update(current: dict[str, Any], body: dict[str, Any]) -> PreparedConfigUpdate:
    candidate = dict(current)
    changed_keys = frozenset(set(body).intersection(CONFIG_KEYS)
                             | {key for key in body
                                if is_provider_secret_key(key) and clean_text_value(body[key])})
    access_password_changed = bool(clean_text_value(body.get("access_token")))
    # 服务商 secret 是动态键（ai_provider_key_<id>），不在 CONFIG_KEYS 白名单内，
    # 与固定 secret 同语义：非空才写入，空值保留旧值。
    for key in body:
        if is_provider_secret_key(key):
            value = clean_text_value(body[key])
            if value:
                candidate[key] = value
    try:
        for key in CONFIG_KEYS:
            if key not in body:
                continue
            raw = body[key]
            if key in SECRET_CONFIG_KEYS:
                value = clean_text_value(raw)
                if value:
                    candidate[key] = hash_access_password(value) if key == "access_token" else value
                continue
            if key in API_FORMAT_KEYS:
                candidate[key] = normalize_api_format(raw)
            elif key == "web_cors_origins":
                invalid = invalid_cors_origins(raw)
                if invalid:
                    return PreparedConfigUpdate(
                        candidate,
                        changed_keys,
                        access_password_changed,
                        "跨域来源必须是完整的 http(s) Origin，不能使用 *、路径或查询参数",
                    )
                candidate[key] = normalize_cors_origins(raw)
            elif key == "ai_providers":
                candidate[key] = normalize_ai_providers(raw)
            elif key in STRING_CONFIG_KEYS:
                candidate[key] = clean_text_value(raw)
            elif key.endswith("_max_tokens"):
                candidate[key] = max(1, int(raw))
            elif key == "proxy_url":
                proxy_url = str(raw or "").strip()
                if proxy_url and not is_supported_proxy_url(proxy_url):
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "代理地址仅支持 http:// 或 https://")
                candidate[key] = proxy_url
            elif key in {
                "proxy_enabled", "qq_bot_enabled", "napcat_chat_filter_enabled",
                "napcat_show_dropped_logs", "napcat_block_official_bots",
                "imagegen_enabled", "imagegen_auto_scene",
                "economy_auto_reward_enabled",
            }:
                candidate[key] = bool(raw)
            elif key == "economy_auto_reward_gold_cap":
                cap_value = int(raw)
                if cap_value < 1:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "奖励自动结算金币上限必须大于 0")
                candidate[key] = cap_value
            elif key in {
                "napcat_heartbeat_sec", "napcat_reconnect_delay_sec",
                "napcat_action_timeout_sec", "napcat_command_dedup_window_sec",
            }:
                numeric_value = float(raw)
                if numeric_value <= 0:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "NapCat 时间参数必须大于 0")
                candidate[key] = numeric_value
            elif key in {"napcat_reply_delay_min_sec", "napcat_reply_delay_max_sec"}:
                delay_value = float(raw)
                if delay_value < 0:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "NapCat 回复延迟不能小于 0")
                candidate[key] = delay_value
            elif key in {"napcat_group_list_mode", "napcat_private_list_mode"}:
                candidate[key] = "blacklist" if raw == "blacklist" else "whitelist"
            elif key in {"napcat_group_list", "napcat_private_list", "napcat_blocked_users"}:
                values = raw if isinstance(raw, list) else []
                candidate[key] = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
            elif key == "update_channel":
                candidate[key] = "preview" if str(raw).strip() == "preview" else "stable"
            elif key == "tts_provider":
                provider = str(raw or "").strip()
                if provider not in SUPPORTED_PROVIDER_IDS:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "TTS Provider 无效")
                candidate[key] = provider
            elif key == "tts_audio_format":
                audio_format = str(raw or "").strip().lower()
                if audio_format not in {"mp3", "opus", "aac", "flac", "wav", "pcm"}:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "TTS 音频格式无效")
                candidate[key] = audio_format
            elif key == "tts_timeout_seconds":
                timeout = float(raw)
                if not 5 <= timeout <= 300:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "TTS 超时必须在 5–300 秒之间")
                candidate[key] = timeout
            elif key == "tts_cache_mb":
                cache_mb = int(raw)
                if not 16 <= cache_mb <= 2048:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "TTS 缓存必须在 16–2048 MB 之间")
                candidate[key] = cache_mb
            elif key == "asr_provider":
                provider = str(raw or "").strip()
                if provider not in SUPPORTED_ASR_PROVIDER_IDS:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "ASR Provider 无效")
                candidate[key] = provider
            elif key == "asr_timeout_seconds":
                timeout = float(raw)
                if not 5 <= timeout <= 300:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "ASR 超时必须在 5–300 秒之间")
                candidate[key] = timeout
            elif key == "imagegen_provider":
                provider = str(raw or "").strip()
                if provider not in IMAGE_PROVIDER_IDS:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "图像生成 Provider 无效")
                candidate[key] = provider
            elif key == "imagegen_timeout_seconds":
                timeout = float(raw)
                if not 5 <= timeout <= 300:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "图像生成超时必须在 5–300 秒之间")
                candidate[key] = timeout
            elif key == "test_timeout_seconds":
                timeout = float(raw)
                if not 5 <= timeout <= 300:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "连接测试超时必须在 5–300 秒之间")
                candidate[key] = timeout
            elif key == "model_request_timeout_seconds":
                timeout = float(raw)
                if not 10 <= timeout <= 600:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "模型请求超时必须在 10–600 秒之间")
                candidate[key] = timeout
            elif key == "napcat_port":
                port = int(raw)
                if not 1 <= port <= 65535:
                    return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "NapCat 端口无效")
                candidate[key] = port
            else:
                candidate[key] = raw
    except (TypeError, ValueError):
        return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "配置字段格式无效")

    # 服务商删除后同步清理孤儿 secret 与悬空引用，保持 STATE 自洽。
    refs_before = {key: str(candidate.get(key) or "").strip() for key in PROVIDER_REF_KEYS}
    strip_orphan_provider_secrets(candidate)
    strip_dangling_provider_refs(candidate)
    _degrade_speech_engines_lost_provider(candidate, refs_before)
    cleared_model_routes = reconcile_model_capability_routes(candidate)

    imagegen_ref = str(candidate.get("imagegen_provider_ref") or "").strip()
    if imagegen_ref:
        imagegen_provider = next(
            (entry for entry in candidate.get("ai_providers") or [] if entry.get("id") == imagegen_ref),
            None,
        )
        if imagegen_provider and imagegen_provider.get("api_format") != "openai":
            return PreparedConfigUpdate(
                candidate,
                changed_keys,
                access_password_changed,
                "图像生成仅支持 OpenAI 兼容服务商",
            )

    active_proxy = effective_proxy_url(bool(candidate.get("proxy_enabled")), candidate.get("proxy_url", ""))
    if candidate.get("proxy_enabled") and not active_proxy:
        return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "已启用代理，但代理地址为空")
    if active_proxy and not is_supported_proxy_url(active_proxy):
        return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "代理地址仅支持 http:// 或 https://")
    if float(candidate.get("napcat_reply_delay_max_sec", 0)) < float(candidate.get("napcat_reply_delay_min_sec", 0)):
        return PreparedConfigUpdate(candidate, changed_keys, access_password_changed, "NapCat 回复延迟上限不能小于下限")
    return PreparedConfigUpdate(
        candidate,
        changed_keys,
        access_password_changed,
        warnings=tuple(
            f"{label}已解除：所选模型的手动能力不是该用途所需类型"
            for label in cleared_model_routes
        ),
    )


def bot_plugin_changes(body: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {
        plugin_key: state[legacy_key]
        for legacy_key, plugin_key in BOT_CONFIG_MAP.items()
        if legacy_key in body and legacy_key in state
    }
