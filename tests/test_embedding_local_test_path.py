"""向量测试按来源分流（2026-11-12 计划 §5 补遗）。

回归背景：local 模式下 embedding_provider_ref 为空，旧的测试路径必然报
「Base URL 非法或不允许」。修复后 local 来源走本地推理测试路径。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

from src.memory.local_embedding import LocalModelError
from src.webui.config_controller import ConfigController


def _controller(state: dict) -> ConfigController:
    return ConfigController(SimpleNamespace(state=state, cors_env_value=""))


def _request(models_dir: Path, body: dict | None = None) -> SimpleNamespace:
    async def _json() -> dict:
        return body or {}

    return SimpleNamespace(
        app={"subsystems": SimpleNamespace(memory_store=SimpleNamespace(db_path=str(models_dir / "memory.db")))},
        json=_json,
    )


def _body(response: web.Response) -> dict:
    return json.loads(response.text)


def test_local_source_without_model_selection_returns_actionable_error(tmp_path):
    controller = _controller({"embedding_source": "local", "embedding_local_model_id": ""})
    response = asyncio.run(controller._test_embedding_local(_request(tmp_path)))
    payload = _body(response)
    assert payload["ok"] is False
    assert payload["source"] == "local"
    assert "请先选择" in payload["error"]


def test_local_source_with_broken_model_returns_readable_load_error(tmp_path):
    controller = _controller({
        "embedding_source": "local",
        "embedding_local_model_id": "missing-model",
    })
    response = asyncio.run(controller._test_embedding_local(_request(tmp_path)))
    payload = _body(response)
    assert payload["ok"] is False
    assert payload["source"] == "local"
    assert "本地模型加载失败" in payload["error"]


def test_external_source_keeps_existing_base_url_guard():
    """external 分支行为不变：空 provider/base_url 仍拒绝（旧报错文案保留）。"""

    controller = _controller({"embedding_source": "external"})
    response = asyncio.run(controller.test_embedding(_request(Path("."))))
    payload = _body(response)
    assert payload["ok"] is False
    assert payload["error"] == "Base URL 非法或不允许"


def test_unsaved_local_source_draft_uses_local_test_path(tmp_path):
    controller = _controller({"embedding_source": "external"})
    response = asyncio.run(controller.test_embedding(_request(
        tmp_path,
        {"embedding_source": "local", "embedding_local_model_id": "missing-model"},
    )))
    payload = _body(response)
    assert payload["source"] == "local"
    assert "本地模型加载失败" in payload["error"]


@pytest.mark.asyncio
async def test_failed_local_activation_leaves_existing_embedding_configuration_intact(monkeypatch, tmp_path):
    from src.webui import config_controller

    state = {"embedding_enabled": True, "embedding_source": "external", "embedding_provider_ref": "provider-a"}
    controller = _controller(state)
    previous = dict(state)

    async def fail_probe(models_dir, model_id):
        assert models_dir == tmp_path / "models"
        assert model_id == "broken-model"
        raise LocalModelError("inference failed")

    monkeypatch.setattr(config_controller, "open_verified_local_embedding", fail_probe)
    response = await controller.apply_values(_request(tmp_path).app, {
        "embedding_enabled": True, "embedding_source": "local", "embedding_local_model_id": "broken-model",
    })

    assert response.status == 422
    assert _body(response)["error"] == "inference failed"
    assert state == previous


@pytest.mark.asyncio
@pytest.mark.parametrize("save_fails", [False, True])
async def test_activate_same_model_uses_the_verified_backend_and_rejects_stale_retries(
    monkeypatch, tmp_path, save_fails,
):
    from src import common_factory
    from src.memory import local_embedding
    from src.webui.composition import RuntimeComposition, RuntimePaths

    state = {
        "model": "chat", "embedding_enabled": True, "embedding_source": "local",
        "embedding_local_model_id": "demo",
    }
    instances = []

    class Backend:
        def __init__(self, models_dir, model_id):
            assert models_dir == tmp_path / "models"
            self.model_id = model_id
            self.embed = AsyncMock(return_value=[1.0, 0.0])
            self.close = AsyncMock()
            instances.append(self)

    monkeypatch.setattr(local_embedding, "LocalOnnxEmbeddingBackend", Backend)
    monkeypatch.setattr(common_factory, "GameHandler", lambda **kwargs: object())
    store = SimpleNamespace(db_path=tmp_path / "memory.db", embedding_client=None)
    subsystems = SimpleNamespace(
        registry=object(), ruleset_registry=object(), lorebook_store=object(),
        lorebook_matcher=object(), memory_store=store,
    )
    saved = []

    def save():
        if save_fails:
            raise OSError("disk unavailable")
        saved.append(dict(state))

    composition = RuntimeComposition(
        paths=RuntimePaths(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path),
        state=state, save_config=save,
    )
    controller = ConfigController(SimpleNamespace(
        state=state, cors_env_value="", build_subsystems=composition.build_subsystems,
        make_api=lambda *args, **kwargs: None, activate_api_runtime=lambda *args: None,
        save_config=save,
    ))
    monkeypatch.setattr(controller, "schedule_embedding_rebuild", lambda *_: None)
    revision = controller._embedding_config_revision
    request = _request(tmp_path, {"expected_revision": revision})
    request.app["subsystems"] = subsystems
    request.match_info = {"model_id": "demo"}

    result = await controller.activate_local_embedding(request)

    assert len(instances) == 1
    verified = instances[0]
    verified.embed.assert_awaited_once()
    if save_fails:
        assert result.status == 500
        assert store.embedding_client is None and not saved
        assert controller._embedding_config_revision == revision
        verified.close.assert_awaited_once()
    else:
        assert result.status == 200 and saved
        assert request.app["subsystems"].memory_store.embedding_client is verified
        verified.close.assert_not_awaited()
        stale = await controller.activate_local_embedding(request)
        assert stale.status == 409
        assert _body(stale)["error_code"] == "EMBEDDING_SETTINGS_CHANGED"
        assert len(instances) == 1
        current_revision = controller._embedding_config_revision
        disabled = await controller.apply_values(request.app, {"embedding_enabled": False})
        assert disabled.status == 200
        assert not state["embedding_enabled"] and store.embedding_client is None
        verified.close.assert_awaited_once()
        pending = _request(tmp_path, {"expected_revision": current_revision})
        pending.app = request.app
        pending.match_info = request.match_info
        stale = await controller.activate_local_embedding(pending)
        assert stale.status == 409 and len(instances) == 1
        await request.app["subsystems"].llm_client.close()
