"""AI 服务商凭据库：集中保存各 AI 服务的接入信息，供多个能力引用。

服务商元数据（名称/Base URL/api 格式）是普通配置，API Key 以
`ai_provider_key_<id>` 的扁平 secret 形式存储，随现有 secrets.json 管线落盘。
各能力（LLM 主/备、embedding、TTS、ASR、生图）通过 `*_provider_ref` 引用服务商；
旧能力级直填配置与环境入口不受支持；引用缺失时能力保持未配置。
"""

from __future__ import annotations

import re
from typing import Any

PROVIDER_SECRET_KEY_PREFIX = "ai_provider_key_"
_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MODEL_CAPABILITIES = frozenset({"chat", "image", "embedding", "tts", "asr"})

# 引用键 → 能力侧的运行时重建分组（与 config_update 的两个 frozenset 对应）
PROVIDER_REF_KEYS = frozenset({
    "llm_provider_ref", "fallback1_provider_ref", "fallback2_provider_ref",
    "embedding_provider_ref", "tts_provider_ref", "asr_provider_ref", "imagegen_provider_ref",
})

# 已移除的公开配置入口。只用于明确拒绝请求和过滤残留，不参与运行时解析。
UNSUPPORTED_AI_CONFIG_KEYS = frozenset({
    "base_url", "api_key", "api_format",
    "fallback1_base_url", "fallback1_api_key", "fallback1_api_format",
    "fallback2_base_url", "fallback2_api_key", "fallback2_api_format",
    "embedding_base_url", "embedding_api_key",
    "tts_base_url", "tts_api_key", "asr_base_url", "asr_api_key",
    "imagegen_base_url", "imagegen_api_key",
})

# 路由键 -> 该路由要求的模型能力。只对明确的手动覆盖做协调，
# 启发式识别可能误判，不能因为它改变就悄悄清空用户已有配置。
MODEL_CAPABILITY_ROUTES = (
    ("llm_provider_ref", "model", "chat", "主模型"),
    ("fallback1_provider_ref", "fallback1_model", "chat", "备用模型 1"),
    ("fallback2_provider_ref", "fallback2_model", "chat", "备用模型 2"),
    ("embedding_provider_ref", "embedding_model", "embedding", "向量模型"),
    ("tts_provider_ref", "tts_model", "tts", "语音合成模型"),
    ("asr_provider_ref", "asr_model", "asr", "语音识别模型"),
    ("imagegen_provider_ref", "imagegen_model", "image", "图像生成模型"),
)


def is_valid_provider_id(provider_id: str) -> bool:
    return bool(_PROVIDER_ID_PATTERN.match(provider_id or ""))


def provider_secret_key(provider_id: str) -> str:
    return f"{PROVIDER_SECRET_KEY_PREFIX}{provider_id}"


def is_provider_secret_key(key: str) -> bool:
    if not key.startswith(PROVIDER_SECRET_KEY_PREFIX):
        return False
    return is_valid_provider_id(key[len(PROVIDER_SECRET_KEY_PREFIX):])


def _normalize_models(raw: Any) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        model = str(item or "").strip()[:160]
        if not model or model in seen:
            continue
        seen.add(model)
        models.append(model)
        if len(models) >= 300:
            break
    return models


def normalize_ai_providers(raw: Any) -> list[dict[str, Any]]:
    """把请求里的服务商列表归一为 [{id, name, base_url, api_format, models?}]。

    非法条目直接丢弃；id 重复时保留首个。
    """
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("id") or "").strip()
        if not is_valid_provider_id(provider_id) or provider_id in seen_ids:
            continue
        seen_ids.add(provider_id)
        entry: dict[str, Any] = {
            "id": provider_id,
            "name": str(item.get("name") or "").strip()[:64] or provider_id,
            "base_url": str(item.get("base_url") or "").strip(),
            "api_format": "anthropic" if str(item.get("api_format") or "").strip().lower() == "anthropic" else "openai",
        }
        models = _normalize_models(item.get("models"))
        if models:
            entry["models"] = models
        raw_capabilities = item.get("model_capabilities")
        if isinstance(raw_capabilities, dict) and models:
            model_names = set(models)
            capabilities = {
                str(model): str(capability).strip().lower()
                for model, capability in raw_capabilities.items()
                if str(model) in model_names
                and str(capability).strip().lower() in _MODEL_CAPABILITIES
            }
            if capabilities:
                entry["model_capabilities"] = capabilities
        entries.append(entry)
    return entries


def resolve_provider(config: dict[str, Any], ref: str) -> dict[str, str] | None:
    """按引用 id 解析服务商凭据；找不到（引用为空/已删除）返回 None。"""
    ref = str(ref or "").strip()
    if not ref:
        return None
    for entry in config.get("ai_providers") or []:
        if isinstance(entry, dict) and str(entry.get("id") or "") == ref:
            api_key = str(config.get(provider_secret_key(ref)) or "").strip()
            return {
                "base_url": str(entry.get("base_url") or "").strip(),
                "api_key": api_key,
                "api_format": "anthropic" if str(entry.get("api_format") or "").lower() == "anthropic" else "openai",
            }
    return None


def is_llm_config_ready(config: dict[str, Any]) -> bool:
    """主模型需要有效引用、地址和模型；本地服务商可以不提供 API Key。"""
    provider = resolve_provider(config, config.get("llm_provider_ref", ""))
    return bool(provider and provider["base_url"] and str(config.get("model") or "").strip())


def provider_secret_keys_for(providers: list[dict[str, Any]]) -> set[str]:
    return {provider_secret_key(entry["id"]) for entry in providers}


def strip_orphan_provider_secrets(candidate: dict[str, Any]) -> None:
    """删除已不在服务商列表中的孤儿 secret，避免 secrets.json 残留无用 key。"""
    keep = provider_secret_keys_for(candidate.get("ai_providers") or [])
    for key in [k for k in candidate if is_provider_secret_key(k)]:
        if key not in keep:
            del candidate[key]


def strip_dangling_provider_refs(candidate: dict[str, Any]) -> None:
    """把指向已删除服务商的引用键清空，使对应能力保持未配置。"""
    known = {str(entry.get("id") or "") for entry in candidate.get("ai_providers") or []}
    for key in PROVIDER_REF_KEYS:
        ref = str(candidate.get(key) or "").strip()
        if ref and ref not in known:
            candidate[key] = ""


def reconcile_model_capability_routes(candidate: dict[str, Any]) -> list[str]:
    """清理显式能力覆盖与路由用途不一致的旧绑定。

    仅检查 provider 目录中存在的模型和显式 ``model_capabilities``，因此普通
    的自动识别结果不会改变既有配置。返回被解除的路由名称供 API 提示用户。
    """
    providers = {
        str(entry.get("id") or ""): entry
        for entry in candidate.get("ai_providers") or []
        if isinstance(entry, dict) and str(entry.get("id") or "")
    }
    cleared: list[str] = []
    for ref_key, model_key, required, label in MODEL_CAPABILITY_ROUTES:
        ref = str(candidate.get(ref_key) or "").strip()
        model = str(candidate.get(model_key) or "").strip()
        provider = providers.get(ref)
        if not ref or not model or not provider:
            continue
        models = provider.get("models")
        if not isinstance(models, list) or model not in models:
            continue
        capabilities = provider.get("model_capabilities")
        override = capabilities.get(model) if isinstance(capabilities, dict) else None
        if not override or override == required:
            continue
        # A matching override is valid. Any other explicit override is stale.
        candidate[ref_key] = ""
        candidate[model_key] = ""
        cleared.append(label)
    return cleared
