"""AI 服务商凭据库：集中保存各 AI 服务的接入信息，供多个能力引用。

服务商元数据（名称/Base URL/api 格式）是普通配置，API Key 以
`ai_provider_key_<id>` 的扁平 secret 形式存储，随现有 secrets.json 管线落盘。
各能力（LLM 主/备、embedding、TTS、ASR）通过 `*_provider_ref` 引用服务商；
引用为空时回退既有的内联配置键，保证旧配置零改动。
"""

from __future__ import annotations

import re
from typing import Any

PROVIDER_SECRET_KEY_PREFIX = "ai_provider_key_"
_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 引用键 → 能力侧的运行时重建分组（与 config_update 的两个 frozenset 对应）
PROVIDER_REF_KEYS = frozenset({
    "llm_provider_ref", "fallback1_provider_ref", "fallback2_provider_ref",
    "embedding_provider_ref", "tts_provider_ref", "asr_provider_ref",
})


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


def provider_secret_keys_for(providers: list[dict[str, Any]]) -> set[str]:
    return {provider_secret_key(entry["id"]) for entry in providers}


def strip_orphan_provider_secrets(candidate: dict[str, Any]) -> None:
    """删除已不在服务商列表中的孤儿 secret，避免 secrets.json 残留无用 key。"""
    keep = provider_secret_keys_for(candidate.get("ai_providers") or [])
    for key in [k for k in candidate if is_provider_secret_key(k)]:
        if key not in keep:
            del candidate[key]


def strip_dangling_provider_refs(candidate: dict[str, Any]) -> None:
    """把指向已删除服务商的引用键清空，运行时回退内联配置。"""
    known = {str(entry.get("id") or "") for entry in candidate.get("ai_providers") or []}
    for key in PROVIDER_REF_KEYS:
        ref = str(candidate.get(key) or "").strip()
        if ref and ref not in known:
            candidate[key] = ""
