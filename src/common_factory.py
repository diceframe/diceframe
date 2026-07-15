"""TRPG 子系统工厂 —— plugin.py 和 web_server.py 共用初始化逻辑。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from src.commands.game_handler import GameHandler
from src.engine.game_instance import GameRegistry
from src.llm.client import LLMClient, ProviderConfig
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.store import LorebookStore
from src.memory.delta import MemoryStore

logger = logging.getLogger("trpg")


@dataclass
class TRPGSubsystems:
    registry: GameRegistry
    llm_client: LLMClient
    lorebook_store: LorebookStore
    lorebook_matcher: KeywordMatcher
    memory_store: MemoryStore
    handler: GameHandler


def create_trpg_subsystems(
    data_dir: Path,
    prompts_dir: Path,
    rules_dir: Path,
    worlds_dir: Path,
    providers: list[ProviderConfig],
    default_provider: str,
    *,
    embedding_enabled: bool = False,
    embedding_base_url: str = "",
    embedding_api_key: str = "",
    embedding_model: str = "",
    embedding_max_input: int = 0,
    proxy_url: str = "",
    auto_recover: bool = False,
    narrative_max_tokens: int = 1024,
    character_gen_max_tokens: int = 2048,
    summary_max_tokens: int = 400,
    brief_max_tokens: int = 300,
    analysis_max_tokens: int = 512,
) -> TRPGSubsystems:
    save_dir = data_dir / "saves"

    registry = GameRegistry(save_dir)

    llm_client = LLMClient(providers=providers, default=default_provider, proxy_url=proxy_url)

    lorebook_store = LorebookStore(data_dir / "lorebook.db")
    lorebook_store.open()
    lorebook_matcher = KeywordMatcher()

    memory_store = MemoryStore(data_dir / "memory.db")
    memory_store.open()

    if embedding_enabled and embedding_base_url:
        from src.memory.embedding import EmbeddingClient
        emb_key = embedding_api_key or providers[0].api_key if providers else ""
        memory_store.embedding_client = EmbeddingClient(
            embedding_base_url, emb_key, embedding_model or "nomic-embed-text",
            max_input_override=embedding_max_input,
            proxy_url=proxy_url,
        )

    handler = GameHandler(
        registry=registry,
        llm_client=llm_client,
        lorebook_matcher=lorebook_matcher,
        lorebook_store=lorebook_store,
        memory_store=memory_store,
        prompts_dir=prompts_dir,
        rules_dir=rules_dir,
        worlds_dir=worlds_dir,
        narrative_max_tokens=narrative_max_tokens,
        summary_max_tokens=summary_max_tokens,
        brief_max_tokens=brief_max_tokens,
        analysis_max_tokens=analysis_max_tokens,
    )

    return TRPGSubsystems(
        registry=registry,
        llm_client=llm_client,
        lorebook_store=lorebook_store,
        lorebook_matcher=lorebook_matcher,
        memory_store=memory_store,
        handler=handler,
    )
