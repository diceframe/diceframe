"""Application composition for DiceFrame's WebUI runtime.

This module owns construction of model/storage subsystems and the WebAPI facade.
It deliberately does not read environment variables, start listeners, or mutate
the aiohttp application lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.ai_providers import resolve_provider
from src.asr import AsrService
from src.common_factory import TRPGSubsystems, create_trpg_subsystems
from src.imagegen import ImageGenerationService
from src.llm.client import ProviderConfig
from src.migrations.config import DEFAULT_NARRATIVE_MAX_TOKENS
from src.network_proxy import effective_proxy_url
from src.tts import SpeechService
from src.webui.api import WebAPI


@dataclass(frozen=True)
class RuntimePaths:
    """Filesystem locations required to assemble the WebUI runtime."""

    data_dir: Path
    prompts_dir: Path
    rules_dir: Path
    worlds_dir: Path
    adventures_dir: Path


@dataclass(frozen=True)
class RuntimeFactories:
    """Replaceable constructors used by the composition root."""

    create_subsystems: Callable[..., TRPGSubsystems] = create_trpg_subsystems
    create_web_api: Callable[..., WebAPI] = WebAPI
    create_speech: Callable[..., Any] = SpeechService
    create_asr: Callable[..., Any] = AsrService
    create_imagegen: Callable[..., Any] = ImageGenerationService


class RuntimeComposition:
    """Assemble replaceable runtime components from explicit dependencies."""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        state: dict,
        save_config: Callable[[], None],
        factories: RuntimeFactories | None = None,
    ) -> None:
        self.paths = paths
        self.state = state
        self.save_config = save_config
        self.factories = factories or RuntimeFactories()

    def build_subsystems(
        self,
        reuse: TRPGSubsystems | None = None,
        config: dict | None = None,
    ) -> TRPGSubsystems:
        runtime_config = self.state if config is None else config
        main_provider = resolve_provider(
            runtime_config,
            runtime_config.get("llm_provider_ref", ""),
        )
        if main_provider:
            main_base_url = main_provider["base_url"]
            main_api_key = main_provider["api_key"]
            main_api_format = main_provider["api_format"]
        else:
            main_base_url = ""
            main_api_key = ""
            main_api_format = "openai"
        providers = [
            ProviderConfig(
                provider_name="default",
                base_url=main_base_url,
                api_key=main_api_key,
                model_name=runtime_config["model"],
                api_format=main_api_format,
            )
        ]
        for index in (1, 2):
            if not runtime_config.get(f"fallback{index}_enabled"):
                continue
            fallback_provider = resolve_provider(
                runtime_config,
                runtime_config.get(f"fallback{index}_provider_ref", ""),
            )
            if not fallback_provider:
                continue
            fallback_base_url = fallback_provider["base_url"]
            fallback_model = runtime_config.get(f"fallback{index}_model", "")
            if not (fallback_base_url and fallback_model):
                continue
            fallback_api_key = fallback_provider["api_key"]
            fallback_api_format = fallback_provider["api_format"]
            providers.append(
                ProviderConfig(
                    provider_name=f"fallback{index}",
                    base_url=fallback_base_url,
                    api_key=fallback_api_key,
                    model_name=fallback_model,
                    api_format=fallback_api_format,
                    fallback=True,
                )
            )

        embedding_provider = resolve_provider(
            runtime_config,
            runtime_config.get("embedding_provider_ref", ""),
        )
        if embedding_provider:
            embedding_base_url = embedding_provider["base_url"]
            embedding_api_key = embedding_provider["api_key"]
        else:
            embedding_base_url = ""
            embedding_api_key = ""
        embedding_enabled = bool(
            runtime_config.get("embedding_enabled", False) and embedding_base_url
        )
        return self.factories.create_subsystems(
            data_dir=self.paths.data_dir,
            prompts_dir=self.paths.prompts_dir,
            rules_dir=self.paths.rules_dir,
            worlds_dir=self.paths.worlds_dir,
            adventures_dir=self.paths.adventures_dir,
            providers=providers,
            default_provider="default",
            embedding_enabled=embedding_enabled,
            embedding_base_url=embedding_base_url,
            embedding_api_key=embedding_api_key,
            embedding_model=runtime_config.get(
                "embedding_model", "nomic-embed-text"
            ),
            embedding_max_input=int(
                runtime_config.get("embedding_max_input", 0)
            ),
            proxy_url=effective_proxy_url(
                bool(runtime_config.get("proxy_enabled")),
                runtime_config.get("proxy_url", ""),
            ),
            narrative_max_tokens=int(
                runtime_config.get(
                    "narrative_max_tokens", DEFAULT_NARRATIVE_MAX_TOKENS
                )
            ),
            character_gen_max_tokens=int(
                runtime_config.get("character_gen_max_tokens", 4096)
            ),
            summary_max_tokens=int(runtime_config.get("summary_max_tokens", 1024)),
            brief_max_tokens=int(runtime_config.get("brief_max_tokens", 1024)),
            analysis_max_tokens=int(runtime_config.get("analysis_max_tokens", 1024)),
            model_request_timeout_seconds=float(
                runtime_config.get("model_request_timeout_seconds", 120)
            ),
            reuse=reuse,
        )

    @staticmethod
    def config_with_resolved_api_refs(config: dict) -> dict:
        """Resolve shared providers into capability-specific runtime keys."""
        resolved = dict(config)
        # Always overwrite these internal fields: residual public settings are never credentials.
        for capability in ("tts", "asr", "imagegen"):
            provider = resolve_provider(config, config.get(f"{capability}_provider_ref", ""))
            if capability == "imagegen" and provider and provider["api_format"] != "openai":
                provider = None
            resolved[f"{capability}_base_url"] = provider["base_url"] if provider else ""
            resolved[f"{capability}_api_key"] = provider["api_key"] if provider else ""
            if provider and provider["base_url"]:
                continue
            # Unconfigured installations must still start so the owner can configure them.
            if capability == "tts" and config.get("tts_provider") in {"openai-compatible", "gpt-sovits"}:
                resolved["tts_provider"] = "browser"
            elif capability == "asr":
                resolved["asr_provider"] = "disabled"
            elif capability == "imagegen":
                resolved["imagegen_enabled"] = False
        return resolved

    def make_api(
        self,
        subsystems: TRPGSubsystems,
        plugin_host: Any = None,
        config: dict | None = None,
        hub_client: Any = None,
    ) -> WebAPI:
        runtime_config = self.state if config is None else config
        api_config = self.config_with_resolved_api_refs(runtime_config)
        proxy_url = effective_proxy_url(
            bool(runtime_config.get("proxy_enabled")),
            runtime_config.get("proxy_url", ""),
        )
        speech_service = self.factories.create_speech(
            api_config,
            self.paths.data_dir / "tts-cache",
            proxy_url=proxy_url,
        )
        asr_service = self.factories.create_asr(api_config, proxy_url=proxy_url)
        imagegen_service = self.factories.create_imagegen(
            api_config,
            self.paths.data_dir / "generated-images",
            proxy_url=proxy_url,
        )
        return self.factories.create_web_api(
            registry=subsystems.registry,
            lorebook=subsystems.lorebook_store,
            memory=subsystems.memory_store,
            rules_dir=self.paths.rules_dir,
            handler=subsystems.handler,
            llm_client=subsystems.llm_client,
            worlds_dir=self.paths.worlds_dir,
            adventures_dir=self.paths.adventures_dir,
            character_gen_max_tokens=int(
                runtime_config.get("character_gen_max_tokens", 4096)
            ),
            text_gen_max_tokens=int(runtime_config.get("text_gen_max_tokens", 1024)),
            plugin_host=plugin_host,
            hub_client=hub_client,
            speech_service=speech_service,
            asr_service=asr_service,
            imagegen_service=imagegen_service,
            ruleset_registry=getattr(subsystems, "ruleset_registry", None),
            content_cache_dir=self.paths.data_dir / "content-cache",
            config_state=self.state,
            save_config=self.save_config,
        )

    @staticmethod
    def activate_api_runtime(subsystems: TRPGSubsystems, api: WebAPI) -> None:
        handler = getattr(subsystems, "handler", None)
        if handler is not None and hasattr(handler, "set_image_generation_service"):
            handler.set_image_generation_service(getattr(api, "_imagegen", None))
