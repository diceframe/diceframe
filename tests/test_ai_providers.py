"""AI 服务商凭据库：归一化、secret 处理、引用解析与运行时重建。"""

from types import SimpleNamespace

import pytest

import web_server
from src.ai_providers import (
    is_provider_secret_key,
    normalize_ai_providers,
    provider_secret_key,
    resolve_provider,
    strip_dangling_provider_refs,
    strip_orphan_provider_secrets,
)
from src.webui.config_update import prepare_config_update, provider_runtime_changed


class _ConfigRequest:
    def __init__(self, body, app):
        self._body = body
        self.headers = {"X-TRPG-Confirm": "true"}
        self.app = app

    async def json(self):
        return self._body


def _provider(provider_id="sf", base_url="https://api.siliconflow.cn/v1", api_format="openai"):
    return {"id": provider_id, "name": provider_id, "base_url": base_url, "api_format": api_format}


def _state(**overrides):
    base = {"ai_providers": []}
    base.update(overrides)
    return base


# ---------- 纯函数 ----------

def test_normalize_ai_providers_drops_invalid_and_dedupes():
    out = normalize_ai_providers([
        {"id": "bad id", "base_url": "https://a.example"},
        {"id": "ok", "base_url": "https://first.example"},
        {"id": "ok", "base_url": "https://second.example"},
        "junk",
        5,
        None,
    ])

    assert [entry["id"] for entry in out] == ["ok"]
    assert out[0]["base_url"] == "https://first.example"
    assert out[0]["name"] == "ok"  # name 缺省回落 id


def test_normalize_ai_providers_normalizes_api_format():
    out = normalize_ai_providers([{"id": "a", "base_url": "u", "api_format": "Anthropic"}])

    assert out[0]["api_format"] == "anthropic"


def test_normalize_ai_providers_keeps_unique_model_catalog():
    out = normalize_ai_providers([{
        "id": "a",
        "models": [" model-b ", "model-a", "model-b", ""],
    }])

    assert out[0]["models"] == ["model-b", "model-a"]


def test_provider_secret_key_roundtrip():
    assert provider_secret_key("sf") == "ai_provider_key_sf"
    assert is_provider_secret_key("ai_provider_key_sf")
    assert not is_provider_secret_key("api_key")
    assert not is_provider_secret_key("ai_provider_key_bad id")
    assert not is_provider_secret_key("ai_provider_key_")


def test_resolve_provider_prefers_provider_credentials():
    config = _state(ai_providers=[_provider(api_format="anthropic")], **{provider_secret_key("sf"): "sk-sf"})

    resolved = resolve_provider(config, "sf")

    assert resolved == {"base_url": "https://api.siliconflow.cn/v1", "api_key": "sk-sf", "api_format": "anthropic"}
    assert resolve_provider(config, "") is None
    assert resolve_provider(config, "missing") is None


def test_strip_orphan_secrets_and_dangling_refs():
    config = _state(
        ai_providers=[_provider("a")],
        **{provider_secret_key("a"): "k1", provider_secret_key("b"): "k2"},
        llm_provider_ref="b",
        tts_provider_ref="a",
    )

    strip_orphan_provider_secrets(config)
    strip_dangling_provider_refs(config)

    assert provider_secret_key("a") in config
    assert provider_secret_key("b") not in config
    assert config["llm_provider_ref"] == ""
    assert config["tts_provider_ref"] == "a"


# ---------- 配置更新 ----------

def test_config_update_accepts_provider_library_and_refs():
    prepared = prepare_config_update(_state(), {
        "ai_providers": [_provider(api_format="anthropic")],
        provider_secret_key("sf"): "sk-new",
        "llm_provider_ref": "sf",
        "tts_provider_ref": "sf",
    })

    assert prepared.error == ""
    assert prepared.state["ai_providers"][0]["api_format"] == "anthropic"
    assert prepared.state[provider_secret_key("sf")] == "sk-new"
    assert prepared.state["llm_provider_ref"] == "sf"
    assert provider_runtime_changed(prepared.changed_keys)


def test_config_update_blank_provider_secret_keeps_old_value():
    prepared = prepare_config_update(
        _state(ai_providers=[_provider()], **{provider_secret_key("sf"): "sk-old"}),
        {provider_secret_key("sf"): ""},
    )

    assert prepared.error == ""
    assert prepared.state[provider_secret_key("sf")] == "sk-old"
    assert not provider_runtime_changed(prepared.changed_keys)


def test_config_update_deleting_provider_cleans_orphan_secret_and_ref():
    current = _state(
        ai_providers=[_provider("a"), _provider("b")],
        **{provider_secret_key("a"): "k1", provider_secret_key("b"): "k2"},
        llm_provider_ref="b",
    )

    prepared = prepare_config_update(current, {"ai_providers": [_provider("a")]})

    assert prepared.error == ""
    assert prepared.state[provider_secret_key("a")] == "k1"
    assert provider_secret_key("b") not in prepared.state
    assert prepared.state["llm_provider_ref"] == ""


def test_config_update_degrades_speech_engines_when_provider_deleted():
    current = _state(
        ai_providers=[_provider()],
        **{provider_secret_key("sf"): "k1"},
        tts_provider="openai-compatible", tts_provider_ref="sf", tts_base_url="", tts_model="tts-1",
        asr_provider="openai-compatible", asr_provider_ref="sf", asr_base_url="", asr_model="whisper-1",
    )

    prepared = prepare_config_update(current, {"ai_providers": []})

    assert prepared.error == ""
    # 引用悬空且无内联地址：回退零配置引擎，删除操作不被运行时校验卡死
    assert prepared.state["tts_provider"] == "browser"
    assert prepared.state["asr_provider"] == "disabled"


def test_config_update_keeps_speech_engine_when_inline_base_url_remains():
    current = _state(
        ai_providers=[_provider()],
        tts_provider="openai-compatible", tts_provider_ref="sf",
        tts_base_url="https://inline.example/v1", tts_model="tts-1",
    )

    prepared = prepare_config_update(current, {"ai_providers": []})

    assert prepared.error == ""
    assert prepared.state["tts_provider"] == "openai-compatible"
    assert prepared.state["tts_base_url"] == "https://inline.example/v1"


def test_config_update_invalid_provider_secret_key_ignored():
    prepared = prepare_config_update(_state(), {"ai_provider_key_bad id": "sk-x"})

    assert prepared.error == ""
    assert provider_runtime_changed(prepared.changed_keys) is False
    assert "ai_provider_key_bad id" not in prepared.state


# ---------- 运行时解析 ----------

def test_build_subsystems_resolves_llm_and_embedding_refs(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(web_server, "create_trpg_subsystems", fake_create)
    config = _state(
        base_url="https://inline.example/v1", api_key="sk-inline", model="m1", api_format="openai",
        ai_providers=[_provider(base_url="https://sf.example/v1", api_format="anthropic")],
        **{provider_secret_key("sf"): "sk-sf"},
        llm_provider_ref="sf",
        embedding_provider_ref="sf",
        embedding_enabled=True,
        embedding_model="bge-m3",
        proxy_enabled=False, proxy_url="",
    )

    web_server._build_subsystems(config=config)

    main = captured["providers"][0]
    assert main.base_url == "https://sf.example/v1"
    assert main.api_key == "sk-sf"
    assert main.api_format == "anthropic"
    assert captured["embedding_base_url"] == "https://sf.example/v1"
    assert captured["embedding_api_key"] == "sk-sf"


def test_build_subsystems_keeps_inline_fallback_when_ref_empty(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(web_server, "create_trpg_subsystems", fake_create)
    config = _state(
        base_url="https://inline.example/v1", api_key="sk-inline", model="m1", api_format="openai",
        fallback1_enabled=True, fallback1_base_url="https://fb.example/v1", fallback1_model="fb-model",
        fallback1_api_key="",  # 回退主 key 的既有语义
        embedding_enabled=False,
        proxy_enabled=False, proxy_url="",
    )

    web_server._build_subsystems(config=config)

    fallback = captured["providers"][1]
    assert fallback.api_key == "sk-inline"


def test_build_subsystems_fallback_ref_does_not_leak_inline_key(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(web_server, "create_trpg_subsystems", fake_create)
    config = _state(
        base_url="https://inline.example/v1", api_key="sk-inline", model="m1", api_format="openai",
        ai_providers=[_provider(base_url="https://fb.example/v1")],
        **{provider_secret_key("sf"): ""},
        fallback1_enabled=True, fallback1_provider_ref="sf", fallback1_model="fb-model",
        fallback1_base_url="", fallback1_api_key="sk-fb-inline",
        embedding_enabled=False,
        proxy_enabled=False, proxy_url="",
    )

    web_server._build_subsystems(config=config)

    fallback = captured["providers"][1]
    assert fallback.base_url == "https://fb.example/v1"
    assert fallback.api_key == ""  # 服务商 key 未配置时不回退别家内联 key


def test_make_api_resolves_tts_and_asr_refs(monkeypatch):
    captured = {}

    class FakeSpeechService:
        def __init__(self, config, cache_dir, proxy_url=""):
            captured["speech"] = config

    class FakeAsrService:
        def __init__(self, config, proxy_url=""):
            captured["asr"] = config

    class FakeWebAPI:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(web_server, "SpeechService", FakeSpeechService)
    monkeypatch.setattr(web_server, "AsrService", FakeAsrService)
    monkeypatch.setattr(web_server, "WebAPI", FakeWebAPI)
    config = _state(
        proxy_enabled=False, proxy_url="",
        tts_provider="openai-compatible", tts_base_url="", tts_api_key="", tts_provider_ref="sf",
        asr_provider="openai-compatible", asr_base_url="", asr_api_key="", asr_provider_ref="sf",
        ai_providers=[_provider()],
        **{provider_secret_key("sf"): "sk-sf"},
    )

    web_server._make_api(
        SimpleNamespace(registry=object(), lorebook_store=object(), memory_store=object(), handler=object(), llm_client=object()),
        config=config,
    )

    assert captured["speech"]["tts_base_url"] == "https://api.siliconflow.cn/v1"
    assert captured["speech"]["tts_api_key"] == "sk-sf"
    assert captured["asr"]["asr_base_url"] == "https://api.siliconflow.cn/v1"
    assert captured["asr"]["asr_api_key"] == "sk-sf"


def test_public_config_masks_provider_secrets(monkeypatch):
    monkeypatch.setitem(web_server.STATE, "ai_providers", [_provider()])
    monkeypatch.setitem(web_server.STATE, provider_secret_key("sf"), "sk-secret1234")

    public = web_server._public_config()

    entry = public["ai_providers"][0]
    assert entry["api_key"] == {"configured": True, "masked": "***1234"}
    assert provider_secret_key("sf") not in public


def test_save_config_splits_provider_secrets(monkeypatch):
    written = {}

    def fake_write(path, data):
        written[path.name] = data

    monkeypatch.setattr(web_server, "_atomic_write_json", fake_write)
    monkeypatch.setitem(web_server.STATE, "ai_providers", [_provider()])
    monkeypatch.setitem(web_server.STATE, provider_secret_key("sf"), "sk-x")

    web_server.save_config()

    assert written["config.json"]["ai_providers"][0]["id"] == "sf"
    assert provider_secret_key("sf") not in written["config.json"]
    assert written["secrets.json"][provider_secret_key("sf")] == "sk-x"


def _runtime():
    return SimpleNamespace(
        registry=object(),
        llm_client=SimpleNamespace(closed=0, close=lambda: None),
        lorebook_store=object(),
        memory_store=SimpleNamespace(embedding_client=None),
        handler=object(),
    )


@pytest.mark.asyncio
async def test_provider_secret_change_rebuilds_model_and_api_runtimes(monkeypatch):
    old_runtime = _runtime()
    new_runtime = _runtime()
    built_configs = []
    app = {"subsystems": old_runtime, "api": object(), "plugin_host": None}

    def fake_build(*, reuse=None, config=None):
        built_configs.append(config)
        return new_runtime

    def fake_make_api(subsystems, plugin_host=None, config=None, hub_client=None):
        return object()

    async def noop_close():
        return None

    monkeypatch.setattr(web_server, "_build_subsystems", fake_build)
    monkeypatch.setattr(web_server, "_make_api", fake_make_api)
    monkeypatch.setattr(web_server, "save_config", lambda: None)
    monkeypatch.setitem(web_server.STATE, "ai_providers", [_provider()])
    monkeypatch.setitem(web_server.STATE, "embedding_enabled", False)
    old_runtime.llm_client.close = noop_close

    response = await web_server.api_config_post(
        _ConfigRequest({provider_secret_key("sf"): "sk-rotated"}, app),
    )

    assert response.status == 200
    assert built_configs and built_configs[0][provider_secret_key("sf")] == "sk-rotated"
    assert app["subsystems"] is new_runtime
