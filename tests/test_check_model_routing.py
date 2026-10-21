from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.common_factory import create_trpg_subsystems
from src.llm.client import ProviderConfig
from src.webui.composition import RuntimeComposition, RuntimeFactories, RuntimePaths
from src.webui.config_update import prepare_config_update
from src.webui.config_controller import ConfigController


def _state():
    return {
        "ai_providers": [{"id": "local", "base_url": "http://localhost:8000/v1", "models": ["main", "judge"], "api_format": "openai"}],
        "llm_provider_ref": "local", "model": "main", "check_model_enabled": True,
        "check_provider_ref": "local", "check_model": "judge", "context_recent_rounds": 12,
        "llm_generation_options": {"temperature": .4},
    }


def test_composition_resolves_check_model_only_from_shared_provider(tmp_path):
    paths = RuntimePaths(*(tmp_path / name for name in ("data", "prompts", "rules", "worlds", "adventures")))
    composition = RuntimeComposition(paths=paths, state=_state(), save_config=lambda: None, factories=RuntimeFactories(create_subsystems=lambda **kwargs: kwargs))
    candidate = composition.build_subsystems()
    assert candidate["check_provider"].model_name == "judge"
    assert candidate["check_provider"].base_url == "http://localhost:8000/v1"
    assert candidate["context_recent_rounds"] == 12
    assert candidate["generation_options"] == {"temperature": .4}
    missing = {**_state(), "check_provider_ref": "missing", "check_base_url": "https://old.invalid", "check_api_key": "old"}
    candidate = composition.build_subsystems(config=missing)
    assert candidate["check_model_enabled"] is True
    assert candidate["check_provider"] is None


def test_deleting_provider_clears_check_route_credentials_and_disables_dedicated_model():
    state = {**_state(), "ai_provider_key_local": "synthetic"}
    updated = prepare_config_update(state, {"ai_providers": []})
    assert not updated.error
    assert updated.state["check_provider_ref"] == ""
    assert not updated.state["check_model_enabled"]
    assert "ai_provider_key_local" not in updated.state


@pytest.mark.asyncio
async def test_factory_owns_dedicated_client_and_passes_it_to_planning(tmp_path, monkeypatch):
    from src.engine.game_instance import GameInstance, GameState
    selected = []
    async def plan(_instance, _rule, client):
        selected.append(client)
        return [], {"available": True, "skipped": True}
    monkeypatch.setattr("src.commands.round_processor.plan_round_checks", plan)
    for name in ("prompts", "rules", "worlds"):
        (tmp_path / name).mkdir()
    runtime = create_trpg_subsystems(
        tmp_path / "data", tmp_path / "prompts", tmp_path / "rules", tmp_path / "worlds",
        [ProviderConfig("default", "http://localhost:8000/v1", "", "main")], "default",
        check_model_enabled=True, check_provider=ProviderConfig("check", "http://localhost:8000/v1", "", "judge"), context_recent_rounds=12,
    )
    try:
        assert runtime.check_llm_client is not runtime.llm_client
        assert runtime.handler._prompt.context_recent_rounds == 12
        inst = GameInstance(game_key=("web", "room", "bot"))
        inst.state = GameState.ACTIVE_JUDGMENT
        runtime.handler._round_processor.registry = SimpleNamespace(get=lambda _key: inst)
        await runtime.handler._round_processor.prepare_round_checks_ai(inst)
        assert selected == [runtime.check_llm_client]
        inst.reset_round_checks()
        runtime.handler._round_processor.check_llm_client = None
        before = inst.to_dict()
        selected.clear()
        with pytest.raises(RuntimeError, match="独立检定模型未配置"):
            await runtime.handler._round_processor.prepare_round_checks_ai(inst)
        assert selected == []
        assert inst.to_dict() == before
    finally:
        await runtime.llm_client.close()
        if getattr(runtime, "check_llm_client", None):
            await runtime.check_llm_client.close()
        runtime.lorebook_store.close()
        runtime.memory_store.close()


@pytest.mark.asyncio
async def test_failed_runtime_candidate_closes_both_clients():
    main, check = SimpleNamespace(close=AsyncMock()), SimpleNamespace(close=AsyncMock())
    candidate = SimpleNamespace(llm_client=main, check_llm_client=check, memory_store=SimpleNamespace(embedding_client=None))
    await ConfigController._close_candidate_runtime(candidate, None, None)
    main.close.assert_awaited_once()
    check.close.assert_awaited_once()


def test_config_update_rejects_enabling_check_model_without_provider_ref():
    """启用独立检定但从未选择服务商：整体校验拒绝保存，提示可操作。"""
    state = {**_state(), "check_provider_ref": "", "check_model": ""}
    updated = prepare_config_update(state, {"check_model_enabled": True})
    assert updated.error
    assert "独立检定模型" in updated.error
    # 拒绝时不落半更新状态：开关保持提交值，由调用方整体丢弃。
    assert updated.state["check_model_enabled"] is True


def test_config_update_rejects_check_model_with_empty_model_name():
    """服务商存在但模型名被清空：同样拒绝保存。"""
    updated = prepare_config_update(_state(), {"check_model": ""})
    assert updated.error
    assert "独立检定模型" in updated.error
    assert updated.state["check_model_enabled"] is True


def test_config_update_self_heals_dangling_check_ref():
    """引用指向已删除服务商（旧配置残留）：任意保存都会清引用并自动关闭开关。

    这条走既有的引用失效降级路径；拒绝报错只针对"开启且补不出有效组合"的
    提交（见上面两个用例）。磁盘上的坏配置在运行时由 composition 诊断日志
    与回合服务的 CHECK_MODEL_NOT_CONFIGURED 兜底。
    """
    state = {**_state(), "check_provider_ref": "gone"}
    updated = prepare_config_update(state, {"check_model": "judge"})
    assert not updated.error
    assert updated.state["check_provider_ref"] == ""
    assert not updated.state["check_model_enabled"]


def test_config_update_accepts_complete_check_model_state():
    """合法组合正常保存，不受校验影响。"""
    updated = prepare_config_update(_state(), {"check_model_enabled": True})
    assert not updated.error
    assert updated.state["check_model_enabled"] is True
    assert updated.state["check_provider_ref"] == "local"
    assert updated.state["check_model"] == "judge"


def test_config_update_keeps_autodisable_on_provider_removal():
    """同一次保存里删除服务商：既有降级逻辑自动关闭开关，不报错。"""
    state = {**_state(), "ai_provider_key_local": "synthetic"}
    updated = prepare_config_update(state, {"ai_providers": []})
    assert not updated.error
    assert not updated.state["check_model_enabled"]
