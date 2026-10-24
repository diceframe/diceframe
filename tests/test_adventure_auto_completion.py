"""GAL 轨道 P6：叙事者节点完成提议的引擎权威结算 + 激活过渡叙事钩。

契约：
1) 规则未声明 ``adventure_progression`` → advisory-only 流程零变化；
2) 声明启用后，结算只经由标准完成事务（gates / world ops / ending 冻结 /
   奖励出口）确认；被拒的提议即丢弃，路线保持锁定（fail closed）；
3) 每次结算至多一次权威确认（链式上限）；
4) 过渡叙事为纯文本，失败降级为无叙事，绝不回滚完成本身。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.commands import adventure_effects
from src.commands.adventure_effects import settle_adventure_completion
from src.rules.adventure_progression_config import parse_adventure_progression_config
from src.rules.rule_system import RuleSystem
from tests.test_adventure_v2_runtime import _deps, _install, _instance


# ---- 配置解析 ----------------------------------------------------------------


def test_config_missing_means_off():
    assert parse_adventure_progression_config(None) is None


def test_config_empty_block_fails_closed():
    with pytest.raises(ValueError, match="adventure_progression."):
        parse_adventure_progression_config({})


def test_config_rejects_unknown_fields_and_non_bools():
    with pytest.raises(ValueError, match="unknown field"):
        parse_adventure_progression_config({"auto_confirm_completion": True, "extra": 1})
    with pytest.raises(ValueError, match="must be a boolean"):
        parse_adventure_progression_config({"auto_confirm_completion": "yes", "activation_narration": True})


def test_gal_rule_declares_progression_and_others_stay_off(tmp_path):
    gal = RuleSystem.load("templates/rules/gal_narrative.json")
    assert gal.adventure_progression is not None
    assert gal.adventure_progression.auto_confirm_completion is True
    assert gal.adventure_progression.activation_narration is True
    for rule_id in ("tavern_free", "freeform_coc", "dnd2024_srd"):
        rule = RuleSystem.load(f"templates/rules/{rule_id}.json")
        assert rule.adventure_progression is None, rule_id


# ---- 全栈 fixture ------------------------------------------------------------

QUEST_GRAPH = {
    "id": "v2_quest",
    "format": "diceframe:adventure-graph-v2",
    "chapters": [{"id": "ch1", "name": "第一章"}],
    "nodes": [
        {
            "id": "start", "type": "scene", "chapter_id": "ch1",
            "transitions": [{"to": "mid"}],
            "on_complete": [],
        },
        {
            "id": "mid", "type": "scene", "chapter_id": "ch1",
            "transitions": [{"to": "finale"}],
            "on_complete": [],
        },
        {"id": "finale", "type": "ending", "chapter_id": "ch1", "on_complete": []},
    ],
    "start_node_ids": ["start"],
}


def _settled_game(tmp_path, *, config=None):
    from src.webui.services import adventure_runtime

    resolver = _install(tmp_path, QUEST_GRAPH)
    instance = _instance(resolver)
    runtime_deps = _deps(resolver)
    adventure_runtime.initialize_adventure_run(runtime_deps, instance)
    # evidence round：一条已提交、带叙事文本的回合日志。
    instance.log.append({
        "round": 1, "gm_response": "第一幕结束，少女在夕阳下说出真相。", "actions": [],
    })

    async def confirm(node_id: str) -> dict:
        return adventure_runtime.complete_adventure_node(
            runtime_deps, instance, node_id, actor_uid="narrator-auto",
        )

    return instance, confirm


def _suggest(instance, node_id: str, *, reason: str = "证据充分") -> None:
    from src.engine.modules.adventure_runtime_state import record_completion_suggestion

    assert record_completion_suggestion(instance, {
        "format_version": 1, "node_id": node_id,
        "reason": reason, "evidence_rounds": [1],
    }) is True


def _suggestions(instance) -> list[dict]:
    from src.engine.modules.adventure_runtime_state import progress

    return progress(instance).get("completion_suggestions") or []


ON = SimpleNamespace(auto_confirm_completion=True, activation_narration=True)
OFF = None


# ---- settle 契约 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_config_the_advisory_flow_is_unchanged(tmp_path):
    from src.engine.modules.adventure_runtime_state import progress

    instance, confirm = _settled_game(tmp_path)
    _suggest(instance, "start")
    before = [dict(row) for row in _suggestions(instance)]

    result = await settle_adventure_completion(instance, config=OFF, confirm=confirm)

    assert result is None
    assert _suggestions(instance) == before
    assert "start" in progress(instance)["active_nodes"]  # 未被确认


@pytest.mark.asyncio
async def test_confirmed_through_the_standard_completion_transaction(tmp_path):
    from src.engine.modules.adventure_runtime_state import progress

    instance, confirm = _settled_game(tmp_path, config=ON)
    _suggest(instance, "start")
    calls: list[str] = []

    async def narration(result, activated):
        calls.append(result["node_id"])
        return "夜幕降临，故事翻开了新的一页。"

    settlement = await settle_adventure_completion(
        instance, config=ON, confirm=confirm, narration=narration,
    )

    assert settlement is not None
    assert settlement.node_id == "start"
    assert settlement.activated_nodes == ["mid"]
    assert settlement.transition_narration == "夜幕降临，故事翻开了新的一页。"
    assert calls == ["start"]
    progress_value = progress(instance)
    assert "start" in progress_value["completed_nodes"]
    assert "mid" in progress_value["active_nodes"]
    assert _suggestions(instance) == []  # 完成即清除


@pytest.mark.asyncio
async def test_rejected_proposal_is_dropped_and_route_stays_locked(tmp_path):
    instance, confirm = _settled_game(tmp_path, config=ON)
    # 绕过 record 的 active 校验，直接塞一条指向不存在节点的提议：
    # settle 的契约是"confirm 拒绝 → 丢弃"，不假设上游校验完备。
    from src.engine.modules.adventure_runtime_state import progress

    progress(instance).setdefault("completion_suggestions", []).append({
        "proposal_id": "p1", "node_id": "ghost", "reason": "x", "evidence_rounds": [1],
    })

    settlement = await settle_adventure_completion(instance, config=ON, confirm=confirm)

    assert settlement is None
    assert _suggestions(instance) == []  # ghost 被丢弃
    assert "start" in progress(instance)["active_nodes"]  # 路线未被误推进


@pytest.mark.asyncio
async def test_one_confirmation_per_pass_with_multiple_suggestions(tmp_path):
    instance, confirm = _settled_game(tmp_path, config=ON)
    from src.engine.modules.adventure_runtime_state import progress

    # 第一条失败（ghost），第二条成功（start）：先丢弃失败者，再确认成功者。
    progress(instance).setdefault("completion_suggestions", []).extend([
        {"proposal_id": "p1", "node_id": "ghost", "reason": "x", "evidence_rounds": [1]},
    ])
    _suggest(instance, "start")

    settlement = await settle_adventure_completion(instance, config=ON, confirm=confirm)

    assert settlement is not None and settlement.node_id == "start"
    assert _suggestions(instance) == []


@pytest.mark.asyncio
async def test_ending_freeze_is_inherited_from_p4(tmp_path):
    from src.webui.services import adventure_runtime

    instance, confirm = _settled_game(tmp_path, config=ON)
    # GM 路径完成全部节点直至结局（finale 是 ending 节点），进度冻结。
    runtime_deps = _deps(_install(tmp_path, QUEST_GRAPH))
    adventure_runtime.complete_adventure_node(runtime_deps, instance, "start")
    adventure_runtime.complete_adventure_node(runtime_deps, instance, "mid")
    adventure_runtime.complete_adventure_node(runtime_deps, instance, "finale")

    # 冻结后的任何提议都必须失败并丢弃（P4 的 ending 冻结由 settle 继承）：
    # 塞一条早已完成、不可再推进的节点提议。
    from src.engine.modules.adventure_runtime_state import progress as read_progress

    assert read_progress(instance)["active_nodes"] == []
    read_progress(instance).setdefault("completion_suggestions", []).append({
        "proposal_id": "p2", "node_id": "start", "reason": "x", "evidence_rounds": [1],
    })
    settlement = await settle_adventure_completion(instance, config=ON, confirm=confirm)
    assert settlement is None
    assert _suggestions(instance) == []


@pytest.mark.asyncio
async def test_narration_failure_degrades_to_empty_transition(tmp_path):
    instance, confirm = _settled_game(tmp_path, config=ON)
    _suggest(instance, "start")

    async def broken_narration(result, activated):
        raise RuntimeError("llm down")

    settlement = await settle_adventure_completion(
        instance, config=ON, confirm=confirm, narration=broken_narration,
    )
    assert settlement is not None
    assert settlement.node_id == "start"  # 完成保留
    assert settlement.transition_narration == ""  # 叙事降级为空


@pytest.mark.asyncio
async def test_narration_skipped_when_disabled(tmp_path):
    instance, confirm = _settled_game(tmp_path, config=ON)
    _suggest(instance, "start")
    called = []

    async def narration(result, activated):
        called.append(result["node_id"])
        return "不该被调用"

    config = SimpleNamespace(auto_confirm_completion=True, activation_narration=False)
    settlement = await settle_adventure_completion(
        instance, config=config, confirm=confirm, narration=narration,
    )
    assert settlement is not None and settlement.transition_narration == ""
    assert called == []


# ---- turns 编排 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_turns_settle_appends_transition_to_latest_round(tmp_path):
    from src.engine.modules import progression_state
    from src.webui.services import turns

    instance, _ = _settled_game(tmp_path)
    latest = int(progression_state.round_value(instance)) - 1
    instance.log.append({
        "round": latest, "gm_response": "刚完成的回合。", "actions": [], "state_changes": [],
    })
    saved: list = []

    async def save(inst):
        saved.append(inst)

    dependencies = SimpleNamespace(
        settle_adventure_completion=None,  # 依赖未配置 → no-op 分支先行验证
        save_instance=save,
    )
    await turns._settle_adventure_completion(dependencies, instance, "test-key")
    assert saved == []  # 未配置：零副作用

    dependencies = SimpleNamespace(
        settle_adventure_completion=_fake_settle("start", ["mid"], "新的一页。"),
        save_instance=save,
    )
    await turns._settle_adventure_completion(dependencies, instance, "test-key")
    assert len(saved) == 1
    entry = next(e for e in instance.log if e.get("round") == latest)
    assert entry["state_changes"][-1] == "新的一页。"


def _fake_settle(node_id, activated, transition):
    async def settle(instance):
        return {
            "node_id": node_id, "activated_nodes": activated,
            "transition_narration": transition,
        }
    return settle


# ---- 场景过渡叙事 ------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrator_without_llm_returns_empty():
    from src.commands.scene_transition import SceneTransitionNarrator

    narrator = SceneTransitionNarrator(None, None)
    text = await narrator.narrate(SimpleNamespace(language="zh-CN"), completed_node_id="start")
    assert text == ""


@pytest.mark.asyncio
async def test_narrator_sanitizes_protocol_leakage():
    from src.commands.scene_transition import SceneTransitionNarrator

    class FakeLLM:
        async def call(self, system, context, **kwargs):
            return SimpleNamespace(narration="夕阳落下。\n---\nSTATE: hack", content="")

    class FakeComposer:
        def scene_context_text(self, instance):
            return "【当前场景】mid"

        def load_gm_prompt(self, language="zh-CN"):
            return "SYSTEM"

    narrator = SceneTransitionNarrator(FakeLLM(), FakeComposer())
    text = await narrator.narrate(
        SimpleNamespace(language="zh-CN"), completed_node_id="start",
    )
    assert "夕阳落下" in text
    assert "STATE" not in text and "---" not in text
