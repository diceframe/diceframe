from __future__ import annotations

from types import SimpleNamespace

from src.commands.prompt_composer import PromptComposer
from src.commands.round_effects import apply_growth_rewards
from src.engine.game_instance import GameInstance


class _AdvancementRuntime:
    def __init__(self) -> None:
        self.applied_updates: list[dict] = []

    def narrative_advancement_prompt(self, instance, locale: str) -> str:
        return f"ADVANCEMENT:{locale}"

    def apply_narrative_advancement_rewards(self, instance, update: dict) -> list[str]:
        self.applied_updates.append(update)
        return ["ADVANCEMENT APPLIED"]


class _Registry:
    def __init__(self, runtime: _AdvancementRuntime) -> None:
        self.runtime = runtime

    def get(self, runtime_id: str, minimum_version: int = 1):
        assert runtime_id == "example:advancement"
        assert minimum_version == 2
        return self.runtime


class _ProgressionMustNotRun:
    def __getattr__(self, name: str):
        raise AssertionError(f"generic progression must not run: {name}")


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "advancement-boundary", "web_bot"))
    instance.ruleset_runtime = {"id": "example:advancement", "version": 2}
    return instance


def test_prompt_uses_runtime_advancement_capability(tmp_path, monkeypatch) -> None:
    prompts = tmp_path / "prompts"
    rules = tmp_path / "rules"
    prompts.mkdir()
    rules.mkdir()
    (prompts / "gm_system_zh.md").write_text("BASE", encoding="utf-8")
    import src.commands.prompt_composer as composer_module
    monkeypatch.setattr(composer_module, "_GM_PROMPT_CACHE", {})

    runtime = _AdvancementRuntime()
    prompt = PromptComposer(
        prompts, rules, ruleset_registry=_Registry(runtime),
    ).compose_gm_prompt(_instance())

    assert "ADVANCEMENT:zh-CN" in prompt


def test_growth_rewards_use_runtime_advancement_capability() -> None:
    runtime = _AdvancementRuntime()
    response = SimpleNamespace(narration="STORY")
    update = {"milestone_grants": ["all"]}

    messages = apply_growth_rewards(
        _instance(), update, response, None, _ProgressionMustNotRun(), runtime,
    )

    assert runtime.applied_updates == [update]
    assert response.narration == "STORY"
    assert messages == ["ADVANCEMENT APPLIED"]
