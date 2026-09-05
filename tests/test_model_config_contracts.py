"""Provider routing persistence and entrypoint contracts, isolated from user data."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.ai_providers import is_llm_config_ready, provider_secret_key, resolve_provider
from src.webui.config_update import prepare_config_update
from src.webui.runtime_config import ConfigStore, RuntimePaths


def _provider(provider_id="local"):
    return {
        "id": provider_id, "name": provider_id,
        "base_url": "http://localhost:8000/v1", "api_format": "openai",
        "models": ["chat", "embed", "speech", "transcribe", "image"],
        "model_capabilities": {
            "chat": "chat", "embed": "embedding", "speech": "tts",
            "transcribe": "asr", "image": "image",
        },
    }


@pytest.mark.parametrize("overrides,ready", [
    ({}, True),
    ({"ai_provider_key_local": "sk-test"}, True),
    ({"llm_provider_ref": ""}, False),
    ({"llm_provider_ref": "missing"}, False),
    ({"model": "   "}, False),
    ({"ai_providers": []}, False),
    ({"ai_providers": [{**_provider(), "base_url": " "}]}, False),
])
def test_main_model_readiness_requires_route_endpoint_and_model(overrides, ready):
    config = {
        "ai_providers": [_provider()], "llm_provider_ref": "local", "model": "chat",
        "base_url": "https://legacy.example/v1", "api_key": "legacy-key",
        **overrides,
    }
    assert is_llm_config_ready(config) is ready


def test_all_model_routes_and_credentials_roundtrip(tmp_path):
    store = ConfigStore(RuntimePaths.from_root(tmp_path, {}), {})
    update = {
        "ai_providers": [_provider(), _provider("cloud")],
        provider_secret_key("cloud"): "sk-cloud-roundtrip",
        "llm_provider_ref": "cloud", "model": "chat",
        "fallback1_enabled": True, "fallback1_provider_ref": "local", "fallback1_model": "chat",
        "fallback2_enabled": True, "fallback2_provider_ref": "cloud", "fallback2_model": "chat",
        "embedding_enabled": True, "embedding_provider_ref": "local",
        "embedding_model": "embed", "embedding_max_input": 12345,
        "tts_provider": "openai-compatible", "tts_provider_ref": "cloud",
        "tts_model": "speech", "tts_default_voice": "nova",
        "tts_audio_format": "wav", "tts_gm_voice": "onyx", "tts_player_voice": "alloy",
        "tts_timeout_seconds": 75, "tts_cache_mb": 512,
        "asr_provider": "openai-compatible", "asr_provider_ref": "local",
        "asr_model": "transcribe", "asr_timeout_seconds": 80,
        "imagegen_enabled": True, "imagegen_auto_scene": False,
        "imagegen_provider": "openai-compatible", "imagegen_provider_ref": "cloud",
        "imagegen_model": "image", "imagegen_square_size": "512x512",
        "imagegen_landscape_size": "1536x1024", "imagegen_quality": "high",
        "imagegen_style_prefix": "watercolor", "imagegen_timeout_seconds": 150,
        "proxy_enabled": True, "proxy_url": "http://localhost:7890",
    }
    prepared = prepare_config_update(store.load().state, update)
    assert not prepared.error
    store.save(prepared.state)

    loaded = store.load()
    for key, value in update.items():
        assert loaded.state[key] == value, key
    assert is_llm_config_ready(loaded.state)
    assert resolve_provider(loaded.state, "local")["api_key"] == ""
    assert resolve_provider(loaded.state, "cloud")["api_key"] == "sk-cloud-roundtrip"
    public = store.public_view(loaded)
    assert public["ai_providers"][0]["api_key"]["configured"] is False
    assert public["ai_providers"][1]["api_key"]["configured"] is True
    assert "sk-cloud-roundtrip" not in json.dumps(public)
    assert "sk-cloud-roundtrip" not in store.paths.config_file.read_text(encoding="utf-8")
    assert json.loads(store.paths.secrets_file.read_text(encoding="utf-8"))[provider_secret_key("cloud")] == "sk-cloud-roundtrip"


@pytest.mark.parametrize("mode,voice", [("browser", "alloy"), ("edge-tts", "zh-CN-XiaoxiaoNeural")])
def test_reference_free_speech_roundtrip(tmp_path, mode, voice):
    store = ConfigStore(RuntimePaths.from_root(tmp_path, {}), {})
    prepared = prepare_config_update(store.load().state, {
        "ai_providers": [], "tts_provider": mode, "tts_provider_ref": "",
        "tts_default_voice": voice, "asr_provider": "disabled", "asr_provider_ref": "",
    })
    assert not prepared.error
    store.save(prepared.state)
    reloaded = store.load().state
    assert reloaded["ai_providers"] == []
    assert reloaded["tts_provider"] == mode
    assert reloaded["tts_default_voice"] == voice
    assert reloaded["asr_provider"] == "disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_cli_passes_effective_proxy_to_local_keyless_model(monkeypatch, tmp_path, enabled):
    from scripts.dev import run_cli_game

    monkeypatch.setattr(run_cli_game, "ROOT", tmp_path)
    monkeypatch.setattr(run_cli_game, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(run_cli_game.os, "environ", {})
    store = ConfigStore(RuntimePaths.from_root(tmp_path, {}), {})
    state = store.load().state
    state.update({
        "ai_providers": [_provider()], "llm_provider_ref": "local", "model": "chat",
        "proxy_enabled": enabled, "proxy_url": "http://localhost:7890",
    })
    store.save(state)

    class StopAfterClient(Exception):
        pass

    client = Mock(side_effect=StopAfterClient)
    monkeypatch.setattr(run_cli_game, "LLMClient", client)
    with pytest.raises(StopAfterClient):
        await run_cli_game.main()
    kwargs = client.call_args.kwargs
    assert kwargs["proxy_url"] == ("http://localhost:7890" if enabled else "")
    assert kwargs["providers"][0].base_url == "http://localhost:8000/v1"
    assert kwargs["providers"][0].api_key == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_web_composition_passes_proxy_and_keyless_embedding_settings(monkeypatch, tmp_path, enabled):
    import src.common_factory as factory
    from src.webui.composition import RuntimeComposition, RuntimePaths as CompositionPaths

    # Reuse inert stores so actual client construction never opens databases or sends requests.
    reuse = SimpleNamespace(
        registry=object(), lorebook_store=object(), lorebook_matcher=object(),
        memory_store=SimpleNamespace(embedding_client=None),
    )
    monkeypatch.setattr(factory, "GameHandler", Mock())
    monkeypatch.setattr(factory, "build_default_ruleset_registry", Mock())
    config = {
        "ai_providers": [_provider()], "llm_provider_ref": "local", "model": "chat",
        "embedding_enabled": True, "embedding_provider_ref": "local", "embedding_model": "embed",
        "embedding_max_input": 12345, "proxy_enabled": enabled, "proxy_url": "http://localhost:7890",
    }
    composition = RuntimeComposition(
        paths=CompositionPaths(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path),
        state=config, save_config=lambda: None,
    )
    runtime = composition.build_subsystems(reuse=reuse)
    try:
        embedding = runtime.memory_store.embedding_client
        assert embedding is not None
        assert embedding.base_url == "http://localhost:8000/v1"
        assert embedding.api_key == ""
        assert embedding.model == "embed"
        assert embedding.max_input_chars == 12345
        assert embedding.proxy_url == runtime.llm_client.proxy_url == ("http://localhost:7890" if enabled else "")
    finally:
        await runtime.llm_client.close()
        await runtime.memory_store.embedding_client.close()
