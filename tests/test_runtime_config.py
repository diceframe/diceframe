"""Runtime configuration precedence and persistence contracts."""

from __future__ import annotations

import json

from src.ai_providers import provider_secret_key
from src.webui.runtime_config import ConfigStore, RuntimePaths


def _store(tmp_path, environ=None):
    environment = dict(environ or {})
    paths = RuntimePaths.from_root(tmp_path, environment)
    return ConfigStore(paths, environment), paths


def test_web_listen_topology_reads_env_and_config(tmp_path):
    """多地址与附加 HTTP/HTTPS 端口：env 优先，且不改变 TRPG_WEB_HOST 语义。"""

    store, paths = _store(
        tmp_path,
        {
            "TRPG_WEB_PORT": "19001",
            "TRPG_WEB_HOST": "0.0.0.0",
            "TRPG_WEB_HOSTS": "::,127.0.0.1,",
            "TRPG_WEB_HTTP_PORT": "19080",
            "TRPG_WEB_HTTPS_PORT": "not-a-port",
        },
    )
    paths.data_dir.mkdir(parents=True)
    paths.config_file.write_text(
        json.dumps({"web_hosts": "10.0.0.9", "web_http_port": 9000, "web_https_port": 9443}),
        encoding="utf-8",
    )

    runtime = store.load()

    # env 覆盖配置：hosts 为主地址 + 追加地址；非法 https 端口被忽略。
    assert runtime.host == "0.0.0.0"
    assert runtime.hosts == ("0.0.0.0", "::", "127.0.0.1")
    assert runtime.http_port == 19080
    assert runtime.https_port is None
    assert runtime.state["web_hosts"] == "0.0.0.0,::,127.0.0.1"
    assert runtime.state["web_http_port"] == 19080
    assert runtime.state["web_https_port"] == ""


def test_web_listen_topology_falls_back_to_saved_config(tmp_path):
    store, paths = _store(tmp_path)
    paths.data_dir.mkdir(parents=True)
    paths.config_file.write_text(
        json.dumps(
            {
                "web_host": "127.0.0.1",
                "web_hosts": "10.0.0.9",
                "web_http_port": 9000,
                "web_https_port": 9443,
            }
        ),
        encoding="utf-8",
    )

    runtime = store.load()

    assert runtime.hosts == ("127.0.0.1", "10.0.0.9")
    assert runtime.http_port == 9000
    assert runtime.https_port == 9443


def test_invalid_extra_ports_warn_instead_of_disappearing(tmp_path, caplog):
    """非法端口必须在读取配置时就告警：planner 只拿到 None，看不到原始值。"""

    store, _paths = _store(
        tmp_path, {"TRPG_WEB_HTTP_PORT": "abc", "TRPG_WEB_HTTPS_PORT": "70000"}
    )

    with caplog.at_level("WARNING"):
        runtime = store.load()

    assert runtime.http_port is None
    assert runtime.https_port is None
    messages = [record.getMessage() for record in caplog.records]
    assert any("TRPG_WEB_HTTP_PORT='abc'" in message for message in messages)
    assert any("TRPG_WEB_HTTPS_PORT='70000'" in message for message in messages)


def test_internal_loopback_follows_ipv6_only_listen_addresses(tmp_path):
    """纯 IPv6 监听时插件 API 基址要指向 [::1]，否则插件连不上。"""

    ipv4_store, _paths = _store(tmp_path / "v4", {"TRPG_WEB_HOST": "0.0.0.0"})
    assert ipv4_store.load().transport.endpoint.url() == "http://127.0.0.1:18000"

    ipv6_store, _paths = _store(
        tmp_path / "v6", {"TRPG_WEB_HOST": "::", "TRPG_WEB_PORT": "19001"}
    )
    assert ipv6_store.load().transport.endpoint.url() == "http://[::1]:19001"


def test_legacy_ai_environment_and_files_are_ignored_but_web_env_has_priority(tmp_path):
    store, paths = _store(
        tmp_path,
        {
            "TRPG_DATA_DIR": str(tmp_path / "runtime-data"),
            "TRPG_LLM_API_KEY": "env-key",
            "TRPG_LLM_BASE_URL": "https://env.example/v1",
            "TRPG_WEB_PORT": "19001",
            "TRPG_WEB_CORS_ORIGINS": "https://ui.example",
        },
    )
    paths.data_dir.mkdir(parents=True)
    paths.config_file.write_text(
        json.dumps(
            {
                "base_url": "https://config.example/v1",
                "web_port": 18000,
                "web_cors_origins": "https://ignored.example",
            }
        ),
        encoding="utf-8",
    )
    paths.secrets_file.write_text(
        json.dumps({"api_key": "secret-key"}),
        encoding="utf-8",
    )

    runtime = store.load()

    assert "api_key" not in runtime.state
    assert "base_url" not in runtime.state
    assert runtime.state["ai_providers"] == []
    assert json.loads(paths.secrets_file.read_text(encoding="utf-8"))["api_key"] == "secret-key"
    assert runtime.port == 19001
    assert runtime.state["web_port"] == 19001
    assert runtime.cors_origins == frozenset({"https://ui.example"})
    assert runtime.state["web_cors_origins"] == "https://ui.example"


def test_save_splits_public_and_provider_secrets(tmp_path):
    store, paths = _store(tmp_path)
    runtime = store.load()
    secret_key = provider_secret_key("custom")
    runtime.state.update(
        {
            "model": "example-model",
            "api_key": "sk-main",
            secret_key: "sk-provider",
            "qq_bot_running": True,
        }
    )

    store.save(runtime.state)

    public = json.loads(paths.config_file.read_text(encoding="utf-8"))
    secrets = json.loads(paths.secrets_file.read_text(encoding="utf-8"))
    assert public["model"] == "example-model"
    assert "api_key" not in public
    assert secret_key not in public
    assert "qq_bot_running" not in public
    assert "api_key" not in secrets
    assert secrets[secret_key] == "sk-provider"


def test_environment_access_password_is_not_persisted(tmp_path):
    store, paths = _store(tmp_path, {"TRPG_ACCESS_TOKEN": "owner-password"})
    runtime = store.load()

    store.save(runtime.state)

    if paths.secrets_file.exists():
        secrets = json.loads(paths.secrets_file.read_text(encoding="utf-8"))
        assert "access_token" not in secrets


def test_invalid_json_is_quarantined(tmp_path):
    store, paths = _store(tmp_path)
    paths.data_dir.mkdir(parents=True)
    paths.config_file.write_text("{invalid", encoding="utf-8")

    runtime = store.load()

    assert runtime.saved["generation_defaults_version"] >= 1
    assert not paths.config_file.exists()
    backups = list(paths.data_dir.glob("config.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{invalid"


def test_public_view_masks_secrets_and_reports_sources(tmp_path):
    store, _paths = _store(
        tmp_path,
        {
            "TRPG_BOT_TOKEN": "bot-secret",
            "HTTPS_PROXY": "http://user:password@proxy.example:8080",
        },
    )
    runtime = store.load()
    runtime.state["ai_providers"] = [{"id": "local", "name": "Local", "base_url": "http://localhost:8000/v1"}]
    runtime.state[provider_secret_key("local")] = "sk-12345678"
    runtime.state["api_key"] = "unsupported-secret"

    public = store.public_view(runtime)

    assert "api_key" not in public
    assert public["ai_providers"][0]["api_key"] == {"configured": True, "masked": "***5678"}
    assert public["bot_token_source"] == "env"
    assert public["proxy_source"] == "env"
    assert "password" not in public["proxy_url"]
