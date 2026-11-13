"""TRPG 子系统工厂 —— WebUI 和插件宿主共用初始化逻辑。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.commands.game_handler import GameHandler
from src.engine.game_instance import GameRegistry
from src.llm.client import LLMClient, ProviderConfig
from src.lorebook.bootstrap import seed_builtin_worlds
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.store import LorebookStore
from src.migrations.world_identity import migrate_legacy_world_ids
from src.memory.delta import MemoryStore
from src.rulesets.builtin import build_default_ruleset_registry
from src.rulesets.registry import RulesetRuntimeRegistry

logger = logging.getLogger("trpg")


@dataclass
class TRPGSubsystems:
    registry: GameRegistry
    llm_client: LLMClient
    lorebook_store: LorebookStore
    lorebook_matcher: KeywordMatcher
    memory_store: MemoryStore
    ruleset_registry: RulesetRuntimeRegistry
    handler: GameHandler
    check_llm_client: LLMClient | None = None


async def close_model_clients(runtime: Any, *, keep: Any = None) -> None:
    """Close each owned model client once; keep clients reused by a new runtime."""
    retained = {id(getattr(keep, name, None)) for name in ("llm_client", "check_llm_client")}
    closed = set()
    for name in ("llm_client", "check_llm_client"):
        client = getattr(runtime, name, None)
        if client is None or id(client) in closed or id(client) in retained:
            continue
        closed.add(id(client))
        try:
            await client.close()
        except Exception:
            logger.warning("关闭模型客户端失败: %s", name, exc_info=True)


def create_trpg_subsystems(
    data_dir: Path,
    prompts_dir: Path,
    rules_dir: Path,
    worlds_dir: Path,
    providers: list[ProviderConfig],
    default_provider: str,
    *,
    adventures_dir: Path | None = None,
    embedding_enabled: bool = False,
    embedding_source: str = "external",
    embedding_local_model_id: str = "",
    embedding_base_url: str = "",
    embedding_api_key: str = "",
    embedding_model: str = "",
    embedding_max_input: int = 0,
    proxy_url: str = "",
    auto_recover: bool = False,
    narrative_max_tokens: int = 4096,
    character_gen_max_tokens: int = 4096,
    summary_max_tokens: int = 4096,
    brief_max_tokens: int = 4096,
    analysis_max_tokens: int = 4096,
    model_request_timeout_seconds: float = 120,
    generation_options: dict[str, Any] | None = None,
    context_recent_rounds: int = 0,
    check_model_enabled: bool = False,
    check_provider: ProviderConfig | None = None,
    reuse: TRPGSubsystems | None = None,
) -> TRPGSubsystems:
    if reuse is None:
        converted = migrate_legacy_world_ids(data_dir, worlds_dir)
        if converted:
            logger.info('已迁移 %d 个旧世界来源标识 / Upgraded legacy world identities', len(converted))
    save_dir = data_dir / "saves"

    registry = reuse.registry if reuse is not None else GameRegistry(save_dir)

    llm_client = LLMClient(
        providers=providers,
        default=default_provider,
        proxy_url=proxy_url,
        request_timeout_seconds=model_request_timeout_seconds,
        generation_options=generation_options,
    )
    check_llm_client = (
        LLMClient(
            providers=[check_provider], default=check_provider.provider_name,
            proxy_url=proxy_url, request_timeout_seconds=model_request_timeout_seconds,
        )
        if check_model_enabled and check_provider is not None else None
    )

    if reuse is not None:
        lorebook_store = reuse.lorebook_store
        lorebook_matcher = reuse.lorebook_matcher
        memory_store = reuse.memory_store
    else:
        lorebook_store = LorebookStore(data_dir / "lorebook.db")
        lorebook_store.open()
        seed_builtin_worlds(lorebook_store, worlds_dir)
        lorebook_matcher = KeywordMatcher()

        memory_store = MemoryStore(data_dir / "memory.db")
        memory_store.open()

    embedding_client = None
    if embedding_enabled and embedding_source == "local":
        from src.memory.local_embedding import LocalOnnxEmbeddingBackend

        embedding_client = LocalOnnxEmbeddingBackend(
            data_dir / "models", embedding_local_model_id,
        )
    elif embedding_enabled and embedding_base_url:
        from src.memory.embedding import EmbeddingClient
        emb_key = embedding_api_key
        embedding_client = EmbeddingClient(
            embedding_base_url, emb_key, embedding_model or "nomic-embed-text",
            max_input_override=embedding_max_input,
            proxy_url=proxy_url,
        )

    ruleset_registry = build_default_ruleset_registry(adventures_dir)
    handler = GameHandler(
        registry=registry,
        llm_client=llm_client,
        lorebook_matcher=lorebook_matcher,
        lorebook_store=lorebook_store,
        memory_store=memory_store,
        prompts_dir=prompts_dir,
        rules_dir=rules_dir,
        worlds_dir=worlds_dir,
        ruleset_registry=ruleset_registry,
        narrative_max_tokens=narrative_max_tokens,
        summary_max_tokens=summary_max_tokens,
        brief_max_tokens=brief_max_tokens,
        analysis_max_tokens=analysis_max_tokens,
        context_recent_rounds=context_recent_rounds,
        check_model_enabled=check_model_enabled,
        check_llm_client=check_llm_client,
    )
    # 仅在新运行时已经完整构建后切换 embedding 客户端，避免构建失败时
    # 破坏仍在服务请求的旧运行时。GameRegistry 与数据库连接始终复用。
    memory_store.embedding_client = embedding_client

    return TRPGSubsystems(
        registry=registry,
        llm_client=llm_client,
        lorebook_store=lorebook_store,
        lorebook_matcher=lorebook_matcher,
        memory_store=memory_store,
        ruleset_registry=ruleset_registry,
        handler=handler,
        check_llm_client=check_llm_client,
    )
