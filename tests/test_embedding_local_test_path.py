"""向量测试按来源分流（2026-11-12 计划 §5 补遗）。

回归背景：local 模式下 embedding_provider_ref 为空，旧的测试路径必然报
「Base URL 非法或不允许」。修复后 local 来源走本地推理测试路径。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from src.webui.config_controller import ConfigController


def _controller(state: dict) -> ConfigController:
    controller = object.__new__(ConfigController)
    controller.dependencies = SimpleNamespace(state=state)
    return controller


def _request(models_dir: Path) -> SimpleNamespace:
    async def _json() -> dict:
        return {}

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
