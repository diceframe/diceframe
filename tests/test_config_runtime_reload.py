from types import SimpleNamespace

import pytest

import web_server
from src.common_factory import create_trpg_subsystems
from src.imagegen import ImageGenerationService
from src.llm.client import ProviderConfig


class _ConfigRequest:
    def __init__(self, body, app):
        self._body = body
        self.headers = {"X-TRPG-Confirm": "true"}
        self.app = app

    async def json(self):
        return self._body


class _Closable:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


def _runtime(registry=None, memory_store=None):
    return SimpleNamespace(
        registry=registry if registry is not None else object(),
        llm_client=_Closable(),
        lorebook_store=object(),
        lorebook_matcher=object(),
        memory_store=memory_store or SimpleNamespace(embedding_client=None),
        handler=object(),
    )


@pytest.mark.asyncio
async def test_saving_public_base_url_keeps_active_runtime(monkeypatch):
    runtime = _runtime()
    api = object()
    app = {"subsystems": runtime, "api": api, "plugin_host": None}
    monkeypatch.setattr(web_server, "save_config", lambda: None)
    monkeypatch.setattr(
        web_server,
        "_build_subsystems",
        lambda **kwargs: pytest.fail("分享地址不应重建运行时"),
    )
    monkeypatch.setitem(web_server.STATE, "proxy_enabled", False)
    monkeypatch.setitem(web_server.STATE, "proxy_url", "")

    response = await web_server.api_config_post(
        _ConfigRequest({"public_base_url": "https://table.example"}, app)
    )

    assert response.status == 200
    assert app["subsystems"] is runtime
    assert app["subsystems"].registry is runtime.registry
    assert app["api"] is api
    assert runtime.llm_client.closed == 0


@pytest.mark.asyncio
async def test_model_runtime_reload_reuses_live_stores_and_closes_old_client(monkeypatch, tmp_path):
    """通过真实 factory + API facade 验证热重载，而不是伪造 subsystem。"""
    original_state = dict(web_server.STATE)
    runtime_root = tmp_path / "runtime"
    for name in ("prompts", "rules", "worlds", "adventures"):
        (runtime_root / name).mkdir(parents=True)
    monkeypatch.setattr(web_server, "DATA_DIR", runtime_root / "data")
    monkeypatch.setattr(web_server, "PROMPTS_DIR", runtime_root / "prompts")
    monkeypatch.setattr(web_server, "RULES_DIR", runtime_root / "rules")
    monkeypatch.setattr(web_server, "WORLDS_DIR", runtime_root / "worlds")
    monkeypatch.setattr(web_server, "ADVENTURES_DIR", runtime_root / "adventures")
    monkeypatch.setattr(web_server, "save_config", lambda: None)

    baseline = dict(original_state)
    baseline.update({
        "ai_providers": [],
        "llm_provider_ref": "",
        "base_url": "https://api.example/v1",
        "api_key": "test-key",
        "model": "old-model",
        "proxy_enabled": False,
        "proxy_url": "",
        "embedding_enabled": False,
    })
    web_server.STATE.clear()
    web_server.STATE.update(baseline)
    old_runtime = web_server._build_subsystems(config=baseline)
    old_session = await old_runtime.llm_client._get_session()
    app = {
        "subsystems": old_runtime,
        "api": web_server._make_api(old_runtime, config=baseline),
        "plugin_host": None,
    }

    try:
        response = await web_server.api_config_post(
            _ConfigRequest({"model": "new-model"}, app)
        )

        assert response.status == 200
        new_runtime = app["subsystems"]
        assert new_runtime is not old_runtime
        assert new_runtime.registry is old_runtime.registry
        assert new_runtime.lorebook_store is old_runtime.lorebook_store
        assert new_runtime.memory_store is old_runtime.memory_store
        assert new_runtime.handler.registry is old_runtime.registry
        assert new_runtime.llm_client.providers["default"].model_name == "new-model"
        assert app["api"]._reg is old_runtime.registry
        assert old_session.closed is True
    finally:
        active_runtime = app["subsystems"]
        await active_runtime.llm_client.close()
        active_runtime.lorebook_store.close()
        active_runtime.memory_store.close()
        web_server.STATE.clear()
        web_server.STATE.update(original_state)


@pytest.mark.asyncio
async def test_model_request_timeout_reload_rebuilds_model_runtime(monkeypatch):
    old_runtime = _runtime()
    new_runtime = _runtime(registry=old_runtime.registry, memory_store=old_runtime.memory_store)
    app = {"subsystems": old_runtime, "api": object(), "plugin_host": None}
    new_api = object()

    def build(*, reuse=None, config=None):
        assert reuse is old_runtime
        assert config["model_request_timeout_seconds"] == 240
        return new_runtime

    monkeypatch.setattr(web_server, "save_config", lambda: None)
    monkeypatch.setattr(web_server, "_build_subsystems", build)
    monkeypatch.setattr(web_server, "_make_api", lambda runtime, plugin_host=None, config=None: new_api)
    monkeypatch.setitem(web_server.STATE, "proxy_enabled", False)
    monkeypatch.setitem(web_server.STATE, "proxy_url", "")

    response = await web_server.api_config_post(
        _ConfigRequest({"model_request_timeout_seconds": 240}, app)
    )

    assert response.status == 200
    assert app["subsystems"] is new_runtime
    assert app["subsystems"].registry is old_runtime.registry
    assert app["api"] is new_api
    assert old_runtime.llm_client.closed == 1


@pytest.mark.asyncio
async def test_api_token_limit_reload_rebuilds_api_without_replacing_subsystems(monkeypatch):
    runtime = _runtime()
    app = {"subsystems": runtime, "api": object(), "plugin_host": None}
    new_api = object()
    monkeypatch.setattr(web_server, "save_config", lambda: None)
    monkeypatch.setattr(
        web_server,
        "_build_subsystems",
        lambda **kwargs: pytest.fail("API 生成上限不应重建游戏子系统"),
    )
    monkeypatch.setattr(web_server, "_make_api", lambda subsystems, plugin_host=None, config=None: new_api)
    monkeypatch.setitem(web_server.STATE, "proxy_enabled", False)
    monkeypatch.setitem(web_server.STATE, "proxy_url", "")

    response = await web_server.api_config_post(
        _ConfigRequest({"text_gen_max_tokens": 2048}, app)
    )

    assert response.status == 200
    assert app["subsystems"] is runtime
    assert app["api"] is new_api
    assert runtime.llm_client.closed == 0


@pytest.mark.asyncio
async def test_tts_config_reload_rebuilds_only_api_facade(monkeypatch):
    runtime = _runtime()
    old_api = object()
    new_api = object()
    app = {"subsystems": runtime, "api": old_api, "plugin_host": None}
    monkeypatch.setattr(web_server, "save_config", lambda: None)
    monkeypatch.setattr(
        web_server,
        "_build_subsystems",
        lambda **kwargs: pytest.fail("TTS 配置不应重建游戏子系统"),
    )

    def make_api(subsystems, plugin_host=None, config=None):
        assert subsystems is runtime
        assert config["tts_provider"] == "openai-compatible"
        assert config["tts_provider_ref"] == "local"
        assert "tts_base_url" not in config
        return new_api

    monkeypatch.setattr(web_server, "_make_api", make_api)
    monkeypatch.setitem(web_server.STATE, "tts_provider", "browser")
    monkeypatch.setitem(web_server.STATE, "ai_providers", [{
        "id": "local", "name": "Local", "base_url": "http://127.0.0.1:8880/v1", "api_format": "openai",
    }])
    monkeypatch.setitem(web_server.STATE, "proxy_enabled", False)
    monkeypatch.setitem(web_server.STATE, "proxy_url", "")

    response = await web_server.api_config_post(_ConfigRequest({
        "tts_provider": "openai-compatible",
        "tts_provider_ref": "local",
    }, app))

    assert response.status == 200
    assert app["subsystems"] is runtime
    assert app["api"] is new_api
    assert runtime.llm_client.closed == 0


@pytest.mark.asyncio
async def test_imagegen_can_be_enabled_before_provider_and_model_are_selected(
    monkeypatch,
    tmp_path,
):
    runtime = _runtime()
    old_api = object()
    new_api = object()
    app = {"subsystems": runtime, "api": old_api, "plugin_host": None}
    saved_states = []
    monkeypatch.setattr(
        web_server,
        "save_config",
        lambda: saved_states.append(dict(web_server.STATE)),
    )
    monkeypatch.setattr(
        web_server,
        "_build_subsystems",
        lambda **kwargs: pytest.fail("图像生成配置不应重建游戏子系统"),
    )
    monkeypatch.setitem(web_server.STATE, "imagegen_enabled", False)
    monkeypatch.setitem(web_server.STATE, "imagegen_provider_ref", "")
    monkeypatch.setitem(web_server.STATE, "imagegen_base_url", "")
    monkeypatch.setitem(web_server.STATE, "imagegen_model", "")

    def make_api(subsystems, plugin_host=None, config=None):
        assert subsystems is runtime
        service = ImageGenerationService(config, tmp_path / "generated-images")
        assert service.enabled is True
        assert service.available is False
        return new_api

    monkeypatch.setattr(web_server, "_make_api", make_api)

    response = await web_server.api_config_post(
        _ConfigRequest({"imagegen_enabled": True}, app)
    )

    assert response.status == 200
    assert web_server.STATE["imagegen_enabled"] is True
    assert saved_states[-1]["imagegen_enabled"] is True
    assert app["subsystems"] is runtime
    assert app["api"] is new_api
    assert runtime.llm_client.closed == 0


@pytest.mark.asyncio
async def test_failed_runtime_reload_keeps_old_runtime_available(monkeypatch):
    old_runtime = _runtime()
    old_api = object()
    app = {"subsystems": old_runtime, "api": old_api, "plugin_host": None}
    old_model = web_server.STATE["model"]
    monkeypatch.setattr(web_server, "save_config", lambda: None)
    monkeypatch.setattr(
        web_server,
        "_build_subsystems",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("broken runtime")),
    )
    monkeypatch.setitem(web_server.STATE, "proxy_enabled", False)
    monkeypatch.setitem(web_server.STATE, "proxy_url", "")

    response = await web_server.api_config_post(
        _ConfigRequest({"model": "broken-model"}, app)
    )

    assert response.status == 500
    assert app["subsystems"] is old_runtime
    assert app["api"] is old_api
    assert old_runtime.llm_client.closed == 0
    assert web_server.STATE["model"] == old_model


@pytest.mark.asyncio
async def test_invalid_config_update_is_transactional(monkeypatch):
    runtime = _runtime()
    app = {"subsystems": runtime, "api": object(), "plugin_host": None}
    previous_model = web_server.STATE["model"]
    monkeypatch.setattr(
        web_server,
        "save_config",
        lambda: pytest.fail("无效配置不应落盘"),
    )

    response = await web_server.api_config_post(
        _ConfigRequest({"model": "must-not-commit", "napcat_port": 70000}, app)
    )

    assert response.status == 400
    assert web_server.STATE["model"] == previous_model
    assert app["subsystems"] is runtime


@pytest.mark.asyncio
async def test_common_factory_reuses_live_registry_and_database_stores(tmp_path):
    providers = [
        ProviderConfig(
            provider_name="default",
            base_url="https://api.example",
            api_key="test-key",
            model_name="test-model",
        )
    ]
    kwargs = {
        "data_dir": tmp_path / "data",
        "prompts_dir": tmp_path / "prompts",
        "rules_dir": tmp_path / "rules",
        "worlds_dir": tmp_path / "worlds",
        "providers": providers,
        "default_provider": "default",
    }
    first = create_trpg_subsystems(**kwargs)
    second = create_trpg_subsystems(**kwargs, reuse=first)

    try:
        assert second.registry is first.registry
        assert second.lorebook_store is first.lorebook_store
        assert second.memory_store is first.memory_store
        assert second.lorebook_matcher is first.lorebook_matcher
        assert second.handler.registry is first.registry
    finally:
        await first.llm_client.close()
        await second.llm_client.close()
        first.lorebook_store.close()
        first.memory_store.close()
