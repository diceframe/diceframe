"""GAL 轨道 P5：叙事应用链泛化（narrative_application）的直接单测。

`apply_parsed_data_to_response` 的 streak / 降级行为由
tests/test_round_llm_streaming.py 经 re-export 覆盖；此处钉住：
1) 新家直连的行为等价；2) 链外 context_label 路径；
3) `apply_narrative_effects` 尾部链的落地与开关。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.commands import narrative_application
from src.commands.narrative_application import (
    apply_narrative_effects,
    apply_parsed_data_to_response,
)
from src.engine import module_hooks
from src.engine.game_instance import GameInstance


def make_instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "narrative-app", "bot"), gm_uid="gm")
    instance.put_player("actor", {
        "user_id": "actor",
        "character_name": "actor",
        "character_sheet": {"hp": 10, "max_hp": 10, "xp": 0},
    })
    return instance


def test_success_path_lands_parsed_fields_and_resets_streak():
    inst = make_instance()
    inst.set_tag_failure_streak(2)
    response = SimpleNamespace(
        narration="n", content="n",
        state_update=None, memory_delta=None, info_asymmetry=None, plot_update=None,
        is_narration_only=False,
    )
    data = {
        "state_update": {"players": {"actor": {}}},
        "memory_delta": {"add": [], "update": [], "forget": []},
        "info_asymmetry": {},
        "plot_update": {"quests": [], "relations": [], "decisions": []},
    }
    apply_parsed_data_to_response(inst, response, data)
    assert response.is_narration_only is False
    assert inst._tag_fail_streak == 0
    assert response.state_update is data["state_update"]
    assert response.plot_update is data["plot_update"]


def test_out_of_round_context_label_replaces_round_number_in_logs(caplog):
    """链外调用（如节点激活叙事）传 context_label 时，日志上下文用 label 标识。"""
    import logging

    inst = make_instance()
    inst.set_tag_failure_streak(0)
    response = SimpleNamespace(
        state_update=None, memory_delta=None, info_asymmetry=None, plot_update=None,
    )
    with caplog.at_level(logging.WARNING, logger="trpg"):
        apply_parsed_data_to_response(
            inst, response, {}, context_label="scene-transition",
        )
    assert any("round=scene-transition" in r.message for r in caplog.records)
    assert not any(
        str(progression_state_round(inst)) in r.message and "scene-transition" not in r.message
        for r in caplog.records
    )
    assert response.is_narration_only is True
    assert response.state_update == {}


def progression_state_round(inst) -> int:
    from src.engine.modules import progression_state

    return progression_state.round_value(inst)


class FakeMemoryStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.embedding_client = None

    async def apply_delta(self, namespace: str, delta: dict, round_no) -> None:
        self.calls.append((namespace, delta))

    async def flush_pending_embeddings(self) -> None:
        self.calls.append(("flush", {}))


def test_tail_chain_applies_quick_actions_memory_and_private_messages():
    inst = make_instance()
    response = SimpleNamespace(
        memory_delta={"add": [{"text": "k"}], "update": [], "forget": []},
        info_asymmetry={"actor": "你注意到门后有脚步声。"},
        plot_update={"quests": [], "relations": [], "decisions": []},
    )
    data = {"quick_actions": ["观察", "询问"]}
    store = FakeMemoryStore()

    notices = _run(
        inst, data, response,
        apply_growth=False, memory_store=store,
    )

    assert notices == []
    assert module_hooks.read(inst, module_hooks.QUICK_ACTIONS) == ["观察", "询问"]
    assert len(store.calls) == 1  # memory_delta 落库；embedding_client=None 不冲刷
    namespace, delta = store.calls[0]
    assert namespace == inst.memory_namespace
    assert delta is response.memory_delta
    private_log = module_hooks.read_value(inst, module_hooks.PRIVATE_LOG)
    assert private_log["actor"][-1]["text"] == "你注意到门后有脚步声。"


def test_tail_chain_growth_rewards_adds_base_xp():
    inst = make_instance()
    progression = SimpleNamespace(try_level_up=lambda _inst, _uid: ["actor 升级！"])
    response = SimpleNamespace(
        memory_delta={}, info_asymmetry={},
        plot_update={"quests": [], "relations": [], "decisions": []},
    )
    data = {"xp_rewards": {"actor": 5}, "quick_actions": []}

    notices = _run(
        inst, data, response,
        rule=None, progression=progression, runtime=None,  # rule=None → xp_level
        apply_quick_actions=False, memory_store=None,
    )

    # include_base 默认 True：基础 10 + 叙事 5。
    assert inst.get_character_sheet("actor")["xp"] == 15
    assert notices == ["actor 升级！"]


def test_tail_chain_quick_actions_can_be_disabled():
    inst = make_instance()
    response = SimpleNamespace(memory_delta={}, info_asymmetry={}, plot_update={})
    data = {"quick_actions": ["不该出现"]}
    _run(inst, data, response, apply_growth=False, apply_quick_actions=False)
    assert module_hooks.read(inst, module_hooks.QUICK_ACTIONS) == []


def _run(inst, data, response, **kwargs):
    import asyncio
    return asyncio.run(apply_narrative_effects(inst, data, response, **kwargs))


def test_round_llm_reexport_delegates_to_narrative_application():
    from src.commands import round_llm

    inst = make_instance()
    inst.set_tag_failure_streak(1)
    response = SimpleNamespace(
        state_update=None, memory_delta=None, info_asymmetry=None, plot_update=None,
        is_narration_only=False,
    )
    round_llm.apply_parsed_data_to_response(inst, response, {})
    assert response.is_narration_only is True
    assert inst._tag_fail_streak == 2
    assert narrative_application.apply_parsed_data_to_response.__module__ == (
        "src.commands.narrative_application"
    )
