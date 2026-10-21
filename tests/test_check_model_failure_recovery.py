"""Regression coverage for dedicated check-model failures."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.commands.round_processor import CheckModelNotConfigured
from src.engine.game_instance import GameState
from src.llm.client import LLMToolCallError
from src.webui.services.turns import submit_action
from test_turn_service import FakeApi, FakeInstance
from webapi_harness import web_api  # noqa: F401  # pytest fixture


@pytest.mark.asyncio
async def test_dedicated_provider_failure_does_not_use_offline_check_fallback(
    web_api, monkeypatch,
) -> None:
    """A failed dedicated provider must surface as configuration failure."""
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Check provider failure",
        players=[{"character_name": "Hero", "attributes": {"str": 12}}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.action_queue = [{"user_id": uid, "text": "我调查石门"}]

    processor = api._handler._round_processor
    processor.check_model_enabled = True
    processor.check_llm_client = object()

    async def provider_failure(*_args, **_kwargs):
        raise LLMToolCallError("provider unavailable", frozenset({503}))

    monkeypatch.setattr("src.commands.round_processor.plan_round_checks", provider_failure)

    def offline_fallback_must_not_run(*_args, **_kwargs):
        raise AssertionError("dedicated check-model failure used offline fallback")

    monkeypatch.setattr(processor, "prepare_round_checks", offline_fallback_must_not_run)

    with pytest.raises(CheckModelNotConfigured):
        await processor.prepare_round_checks_ai(instance)

    assert instance.state == GameState.ACTIVE_JUDGMENT


@pytest.mark.asyncio
async def test_direct_process_round_rolls_back_dedicated_provider_failure(
    web_api, monkeypatch,
) -> None:
    """Luck-timeout/CLI callers also leave judgment safely after a provider error."""
    api, _lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world",
        "Direct check provider failure",
        players=[{"character_name": "Hero", "attributes": {"str": 12}}],
    )
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.action_queue = [{"user_id": uid, "text": "我调查石门"}]
    processor = api._handler._round_processor
    processor.check_model_enabled = True
    processor.check_llm_client = object()

    async def provider_failure(*_args, **_kwargs):
        raise LLMToolCallError("provider unavailable", frozenset({503}))

    monkeypatch.setattr("src.commands.round_processor.plan_round_checks", provider_failure)

    with pytest.raises(CheckModelNotConfigured) as rejected:
        await processor.process_round(instance)

    assert rejected.value.rolled_back is True
    assert instance.state == GameState.ACTIVE_ACTION


@pytest.mark.asyncio
async def test_check_model_error_is_not_reported_as_retryable_when_rollback_fails() -> None:
    """A failed abort/save must produce an explicit recovery error."""
    instance = FakeInstance()
    instance.try_advance_result = True
    instance.flip_state_on_advance = True
    api = FakeApi(instance)

    async def unconfigured(_instance):
        raise CheckModelNotConfigured("dedicated check model unavailable")

    async def save_failure(_instance):
        raise OSError("save unavailable")

    dependencies = replace(
        api.dependencies,
        prepare_round_checks_ai=unconfigured,
        save_instance=save_failure,
    )

    result = await submit_action(dependencies, "game", "gm", "调查石门")

    assert result["status"] == 500
    assert result["payload"]["error_code"] == "ROUND_ROLLBACK_FAILED"
    assert result["payload"]["advanced"] is False
    assert instance.state == GameState.ACTIVE_ACTION
