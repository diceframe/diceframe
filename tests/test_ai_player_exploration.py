"""AI-hosted player seats in ordinary (exploration) rounds (AI teammate plan, PR3).

PR1 made *who plays a seat* an explicit persisted record, and PR2 made that
record authoritative: an AI or unclaimed seat never blocks the human ready
barrier and never accepts a human submission.  This file locks down the other
half: an ``ai`` seat now actually produces a *player* action in a normal round.

Behaviour under test:

* AI actions are generated only after the human gate is satisfied (all active
  humans ready, or the single solo human has submitted) and before the round
  advances; a pending human blocks them entirely;
* one LLM call per AI seat, sequentially in sorted uid order, each seeing the
  public actions already declared this round -- including earlier AI ones;
* the prompt is a *player-safe* context: the AI seat's own sheet and the public
  story, never GM-only world truth, GM directives, or another player's private
  log (an explicit regression check reads the prompt string the model received);
* the system prompt explicitly requires *role-playing* the character on that
  sheet -- identity, personality, values, goals, relationships, known clues,
  private perceptions, current body and resources -- rather than playing the
  tactically optimal move, and forbids inventing a persona the sheet lacks
  (B11/B15).  The sheet itself reaches the model through the player-safe context,
  so the tests assert on *prompt and context construction*, never on real LLM
  output;
* the emitted text is plain prose appended through the same canonical seam
  humans use (``GameInstance.add_action``), so the existing Check Planner runs on
  it and the AI seat's own sheet supplies its attributes and modifier;
* a control handover while the call is in flight discards the result, and a
  provider error / unusable output only skips that seat (``AI_ACTION_SKIPPED``);
* a repeated pass for the same (run, round, seat) never writes a second action;
* unclaimed and human seats are never filled, and a pure-human table behaves
  exactly as it did before this PR.

The tests drive the real service (``turns.submit_action``), the real command
layer (``commands.ai_player.fill_ai_player_actions``) and the real Check Planner
with a fake LLM client that captures every prompt it is handed.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from src.commands.ai_player import (
    AI_ACTION_SOURCE,
    DISCARD_MARKER,
    SKIP_MARKER,
    build_ai_player_prompt,
    fill_ai_player_actions,
)
from src.commands.check_planner import plan_round_checks
from src.engine.checks import resolve_check_request
from src.engine.game_instance import GameInstance, GameState
from src.engine.modules import table_settings
from src.engine.player_control import get_control, set_control
from src.engine.world_state import apply_world_ops
from src.rules.rule_system import RuleSystem
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services.turns import TurnDependencies, submit_action
from src.engine.modules import private_channels, session_stats

HUMAN_SHEET = {"hp": 10, "max_hp": 10, "attributes": {"str": 14, "dex": 10}}
AI_SHEET = {"hp": 8, "max_hp": 8, "attributes": {"str": 8, "dex": 16}}

# 一个"胆小、讨厌教会、习惯回避正面冲突"的角色卡。人设只来自现有
# ``character_sheet``：本 PR 不新增任何 persona 存储（B12）。
PERSONA_SHEET = {
    "hp": 8,
    "max_hp": 8,
    "attributes": {"str": 8, "dex": 16},
    "background": "PERSONA-BACKGROUND-3f8a：出生在曾被教会烧毁的村子。",
    "personality": "PERSONA-TRAIT-9c1d：胆小，讨厌教会，习惯回避正面冲突。",
    "ideals": "PERSONA-IDEAL-5b7e：只想活着看到下一个春天。",
    "bonds": "PERSONA-BOND-2d4f：欠阿岚一次救命之恩。",
    "goals": "PERSONA-GOAL-8e6a：找到教会纵火的证据，但绝不跟人正面冲突。",
}

# 「扮演角色」约束必须真的进了 system prompt；逐语言锁定关键句（B11/B15）。
PERSONA_PROMPT_MARKERS = {
    "zh-CN": ("不是在替玩家做战术最优决策", "角色卡", "不要编造角色卡中不存在"),
    "en": ("role-playing this character", "character sheet", "Never invent a persona"),
    "ja": ("演じているのであって", "キャラクターシート", "捏造しないこと"),
    "de": ("Du spielst diese Figur", "Charakterbogen", "Erfinde keine Persönlichkeit"),
}
# 强化人设不得削弱既有的边界：不替别人行动、不决定结果、不做 GM 叙述（B14）。
BOUNDARY_PROMPT_MARKERS = {
    "zh-CN": ("不替其他角色行动、说话或决定结果",),
    "en": ("Do not act, speak or decide for other characters",),
    "ja": ("他キャラクターの行動・発言・結果を代行せず",),
    "de": ("Handle, sprich und entscheide nicht für andere Figuren",),
}


def make_instance(
    *,
    humans: tuple[str, ...] = ("h1",),
    ai: tuple[str, ...] = (),
    unclaimed: tuple[str, ...] = (),
    solo: bool = False,
) -> GameInstance:
    instance = GameInstance(game_key=("web", "ai-player", "bot"), rule_id="test")
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    table_settings.replace_solo_mode(instance, solo)
    for uid in humans:
        # 走真实席位写入路径：控制记录由聚合保证存在。
        instance.put_player(uid, {
            "user_id": uid,
            "character_name": f"真人{uid}",
            "character_sheet": dict(HUMAN_SHEET, attributes=dict(HUMAN_SHEET["attributes"])),
        })
    for uid in (*ai, *unclaimed):
        instance.put_player(uid, {
            "user_id": uid,
            "character_name": f"AI{uid}",
            "character_sheet": dict(AI_SHEET, attributes=dict(AI_SHEET["attributes"])),
        })
    for uid in ai:
        set_control(instance, uid, "ai")
    for uid in unclaimed:
        set_control(instance, uid, "unclaimed")
    return instance


class FakePlayerLLM:
    """Fake provider that records every prompt it receives.

    ``fail_indexes`` raises a provider error for that call index, ``empty_indexes``
    returns unusable output (whitespace only), and ``on_call`` runs *during* the
    call so a test can simulate a control handover mid-flight.
    """

    def __init__(
        self,
        *,
        replies: list[str] | None = None,
        fail_indexes: tuple[int, ...] = (),
        empty_indexes: tuple[int, ...] = (),
        on_call: Any = None,
    ) -> None:
        self.default = "fake"
        self.replies = replies
        self.fail_indexes = set(fail_indexes)
        self.empty_indexes = set(empty_indexes)
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    async def call(self, system_prompt: str, user_message: str, **kwargs: Any) -> Any:
        index = len(self.calls)
        self.calls.append({
            "system": system_prompt, "user": user_message, "kwargs": kwargs,
        })
        if self.on_call is not None:
            self.on_call(index)
        if index in self.fail_indexes:
            raise RuntimeError("provider down")
        if index in self.empty_indexes:
            text = "   "
        elif self.replies is not None:
            text = self.replies[index] if index < len(self.replies) else ""
        else:
            text = f"我执行编号{index}的任务"
        return SimpleNamespace(
            narration=text, content=text, total_tokens=13, provider_used="fake",
        )

    def prompt(self, index: int) -> str:
        return f"{self.calls[index]['system']}\n{self.calls[index]['user']}"


async def _unused(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("该依赖不应被调用")


async def _fake_process_round(_instance: GameInstance, **_kwargs: Any) -> tuple[str, Any]:
    return "本轮叙事", None


def make_dependencies(
    instance: GameInstance,
    *,
    llm_client: FakePlayerLLM,
    process_round: Any = _fake_process_round,
    prepare_round_checks_ai: Any = None,
) -> TurnDependencies:
    """Real service dependencies with a real command-layer AI fill wired in."""

    async def save_instance(_instance: GameInstance) -> None:
        return None

    async def fill(target: GameInstance, **kwargs: Any) -> list[dict[str, Any]]:
        return await fill_ai_player_actions(target, llm_client=llm_client, **kwargs)

    return TurnDependencies(
        get_instance=lambda _key: instance,
        parse_game_key=lambda _key: ("web", "ai-player", "bot"),
        ruleset_registry=RulesetRuntimeRegistry(),
        load_rule_for_game=lambda _instance: None,
        prepare_round_checks_ai=prepare_round_checks_ai,
        prepare_round_checks=None,
        resolve_pending_dice=_unused,
        roll_for_game=lambda _key: {"ok": False},
        save_instance=save_instance,
        process_round=process_round,
        resolve_luck_decision=_unused,
        decline_pending_luck=_unused,
        fill_ai_player_actions=fill,
    )


def ai_actions(instance: GameInstance, uid: str) -> list[dict[str, Any]]:
    return [action for action in instance.action_queue if action.get("user_id") == uid]


def queue_uids(instance: GameInstance) -> list[str]:
    return [str(action.get("user_id") or "") for action in instance.action_queue]


def make_rule() -> RuleSystem:
    return RuleSystem({
        "rule_id": "test",
        "name": "Test",
        "dice_system": "d20",
        "mechanics": "dnd5e_core",
        "attributes": [
            {"key": "str", "name": "力量", "name_en": "Strength"},
            {"key": "dex", "name": "敏捷", "name_en": "Dexterity"},
        ],
        "dc_table": {"easy": 8, "normal": 12, "hard": 16},
    })


# ---- 1. 时机：真人交了才轮到 AI --------------------------------------------


@pytest.mark.asyncio
async def test_ai_action_appears_only_after_the_human_submits() -> None:
    instance = make_instance(humans=("h1",), ai=("a1",))
    llm = FakePlayerLLM(replies=["我去检查教堂后门。"])
    dependencies = make_dependencies(instance, llm_client=llm)

    # 真人还没提交：闸门未满足，一次模型调用都不该发生。
    assert instance.human_actions_ready() is False
    assert await fill_ai_player_actions(instance, llm_client=llm) == []
    assert llm.calls == []
    assert instance.action_queue == []

    result = await submit_action(dependencies, "game", "h1", "我点亮提灯。")

    assert result["status"] == 200
    assert result["payload"]["advanced"] is True
    assert queue_uids(instance) == ["h1", "a1"]
    action = ai_actions(instance, "a1")[0]
    assert action["text"] == "我去检查教堂后门。"
    assert action["source"] == AI_ACTION_SOURCE
    assert action["metadata"] == {
        "source": AI_ACTION_SOURCE,
        "control_revision": get_control(instance, "a1")["revision"],
        "generated_for_round": 1,
    }
    assert len(llm.calls) == 1


# ---- 2. 每个 AI 席位一次调用、顺序稳定、看得到本轮已公开的行动 ----------------


@pytest.mark.asyncio
async def test_each_ai_seat_calls_once_in_sorted_uid_order() -> None:
    instance = make_instance(humans=("h1",), ai=("a3", "a1", "a2"))
    llm = FakePlayerLLM()
    dependencies = make_dependencies(instance, llm_client=llm)

    result = await submit_action(dependencies, "game", "h1", "我点亮提灯。")

    assert result["payload"]["advanced"] is True
    assert queue_uids(instance) == ["h1", "a1", "a2", "a3"]
    assert len(llm.calls) == 3
    for index, uid in enumerate(("a1", "a2", "a3")):
        # 每个席位只看见自己那一份身份，且收到的是普通文本生成路径。
        assert f"AI{uid}" in llm.prompt(index)
        assert llm.calls[index]["kwargs"]["max_tokens"] > 0
        assert ai_actions(instance, uid)[0]["text"] == f"我执行编号{index}的任务"
    # 同一轮的公开行动对后续席位可见（含同一遍里更早生成的 AI 行动）。
    assert "我执行编号0的任务" in llm.prompt(1)
    assert "我执行编号0的任务" in llm.prompt(2)
    assert "我执行编号1的任务" in llm.prompt(2)
    # token 计入既有记账，而不是第二套计费。
    assert session_stats.total_llm_calls(instance) == 3
    assert session_stats.total_tokens(instance) == 39


# ---- 3. 真人未交齐：什么都不生成 --------------------------------------------


@pytest.mark.asyncio
async def test_nothing_is_generated_while_a_human_is_still_pending() -> None:
    instance = make_instance(humans=("h1", "h2"), ai=("a1", "a2"))
    llm = FakePlayerLLM()
    dependencies = make_dependencies(instance, llm_client=llm)

    first = await submit_action(dependencies, "game", "h1", "我点亮提灯。")

    assert first["payload"]["advanced"] is False
    assert llm.calls == []
    assert queue_uids(instance) == ["h1"]
    assert instance.state == GameState.ACTIVE_ACTION

    second = await submit_action(dependencies, "game", "h2", "我检查窗户。")

    assert second["payload"]["advanced"] is True
    assert queue_uids(instance) == ["h1", "h2", "a1", "a2"]
    assert len(llm.calls) == 2


# ---- 4. 未认领席位不产生行动 ------------------------------------------------


@pytest.mark.asyncio
async def test_unclaimed_seats_never_produce_actions() -> None:
    instance = make_instance(humans=("h1",), ai=("a1",), unclaimed=("u1", "u2"))
    llm = FakePlayerLLM()
    dependencies = make_dependencies(instance, llm_client=llm)

    result = await submit_action(dependencies, "game", "h1", "我点亮提灯。")

    assert result["payload"]["advanced"] is True
    assert queue_uids(instance) == ["h1", "a1"]
    assert len(llm.calls) == 1


# ---- 5. solo：真人提交后补 AI，然后自动推进 ---------------------------------


@pytest.mark.asyncio
async def test_solo_mode_fills_ai_actions_then_auto_advances() -> None:
    instance = make_instance(humans=("h1",), ai=("a1", "a2"), solo=True)
    llm = FakePlayerLLM()
    dependencies = make_dependencies(instance, llm_client=llm)

    assert table_settings.solo_mode(instance) is True
    result = await submit_action(dependencies, "game", "h1", "我点亮提灯。")

    assert result["payload"]["advanced"] is True
    assert instance.state == GameState.ACTIVE_JUDGMENT
    assert queue_uids(instance) == ["h1", "a1", "a2"]
    assert result["payload"]["narration"] == "本轮叙事"


# ---- 6. AI 席位的检定使用它自己的角色卡 --------------------------------------


@pytest.mark.asyncio
async def test_ai_check_uses_the_ai_seats_own_character_sheet() -> None:
    instance = make_instance(humans=("h1",), ai=("a1",), solo=True)
    instance.players["h1"]["character_sheet"]["attributes"] = {"str": 20, "dex": 4}
    instance.players["a1"]["character_sheet"]["attributes"] = {"str": 6, "dex": 18}
    rule = make_rule()
    llm = FakePlayerLLM(replies=["我翻看任务板。"])

    class CheckTool:
        async def call_tools(self, _system: str, user: str, **_kwargs: Any) -> Any:
            assert '"player_id":"a1"' in user
            return SimpleNamespace(
                tool_calls=[{"name": "dice_checks", "arguments": {"checks": [
                    {"player": "a1", "attribute": "dex", "target": 12},
                ]}}],
                total_tokens=7, provider_used="fake", native_tools=True,
            )

    dependencies = make_dependencies(instance, llm_client=llm)
    result = await submit_action(dependencies, "game", "h1", "我在大厅坐着休息。")
    assert result["payload"]["advanced"] is True

    # 真实 Check Planner 在真实行动队列上运行。
    # 注意 plan_round_checks 的第二个返回值是调用元数据 dict，不是错误列表。
    planned, metadata = await plan_round_checks(instance, rule, CheckTool())
    assert "tool_call_unavailable" not in (metadata.get("errors") or [])
    assert len(planned) == 1
    action, request = planned[0]
    assert request["actor_uid"] == "a1"
    assert request["attribute"] == "dex"

    action["check_request"] = request
    action["dice_value"] = 10
    action["dice_rolls"] = [10]
    check = resolve_check_request(instance, action, rule)
    assert check is not None
    assert check["actor_uid"] == "a1"
    # 修正值来自 AI 席位自己的角色卡（dex 18），不是真人的力量 20。
    assert check["modifier"] == rule.attribute_modifier(18)
    assert check["modifier"] != rule.attribute_modifier(20)


# ---- 7. 飞行中交接控制权：丢弃结果 ------------------------------------------


@pytest.mark.asyncio
async def test_control_handover_mid_flight_discards_the_result(caplog) -> None:
    instance = make_instance(humans=("h1",), ai=("a1",), solo=True)
    llm = FakePlayerLLM(replies=["我去撬开侧门。"])

    def hand_over(_index: int) -> None:
        # 模型还在飞的时候，真人把这个席位接管了（revision 提升）。
        set_control(instance, "a1", "human")

    llm.on_call = hand_over
    dependencies = make_dependencies(instance, llm_client=llm)

    with caplog.at_level(logging.WARNING, logger="trpg"):
        result = await submit_action(dependencies, "game", "h1", "我点亮提灯。")

    assert result["payload"]["advanced"] is True
    assert queue_uids(instance) == ["h1"]
    assert ai_actions(instance, "a1") == []
    assert DISCARD_MARKER in caplog.text
    assert "control_changed" in caplog.text


# ---- 8. 失败隔离：标记后继续 ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["provider_error", "unusable_output"])
async def test_provider_failure_skips_one_seat_and_keeps_the_round(
    mode: str, caplog,
) -> None:
    instance = make_instance(humans=("h1",), ai=("a1", "a2"), solo=True)
    llm = FakePlayerLLM(
        fail_indexes=(0,) if mode == "provider_error" else (),
        empty_indexes=(0,) if mode == "unusable_output" else (),
    )
    dependencies = make_dependencies(instance, llm_client=llm)

    with caplog.at_level(logging.WARNING, logger="trpg"):
        result = await submit_action(dependencies, "game", "h1", "我点亮提灯。")

    assert result["payload"]["advanced"] is True
    assert instance.state == GameState.ACTIVE_JUDGMENT
    # 失败席位没有行动，但其余 AI 席位与真人都不受影响。
    assert queue_uids(instance) == ["h1", "a2"]
    assert ai_actions(instance, "a2")[0]["metadata"]["generated_for_round"] == 1
    assert len(llm.calls) == 2
    assert SKIP_MARKER in caplog.text


# ---- 9. 幂等：(run, round, uid) 至多一条 AI 行动 -----------------------------


@pytest.mark.asyncio
async def test_repeated_fill_appends_only_one_action() -> None:
    instance = make_instance(humans=("h1",), ai=("a1",), solo=True)
    llm = FakePlayerLLM()
    await instance.add_action("h1", "我点亮提灯。")

    first = await fill_ai_player_actions(instance, llm_client=llm)
    second = await fill_ai_player_actions(instance, llm_client=llm)

    assert [record["status"] for record in first] == ["added"]
    assert [record["status"] for record in second] == ["duplicate"]
    assert len(llm.calls) == 1


# ---- 9b. 原子性：复核与写入必须在同一个 authority boundary --------------------


@pytest.mark.asyncio
async def test_the_recheck_and_the_commit_are_one_critical_section(monkeypatch) -> None:
    """Case B：锁死真正的 TOCTOU，而不是只测「LLM 返回之前改控制权」。

    做法：在复核**通过**的那一刻，往事件循环里排一个「立刻把席位改成 human」的
    回调。如果复核与提交之间存在任何 ``await``，事件循环会先跑那个回调，提交就会
    落在一个人人控制的席位上。

    因此可靠的断言是顺序：先 append，后 flip。同时断言复核确实发生在持锁状态
    ——旧实现（先在外面 ``_stale_reason``、再另调 ``add_action``）两条都不满足。
    """

    import asyncio

    instance = make_instance(humans=("h1",), ai=("a1",), solo=True)
    llm = FakePlayerLLM()
    await instance.add_action("h1", "我点亮提灯。")

    events: list[str] = []
    lock_held_during_recheck: list[bool] = []

    original_reason = GameInstance.ai_player_action_stale_reason
    original_add = GameInstance._add_action_locked

    def flip() -> None:
        events.append("flip")
        set_control(instance, "a1", "human")

    def spy_reason(self: GameInstance, uid: str, **kwargs: Any) -> str:
        reason = original_reason(self, uid, **kwargs)
        if not reason:
            lock_held_during_recheck.append(self._lock.locked())
            asyncio.get_running_loop().call_soon(flip)
        return reason

    def spy_add(self: GameInstance, uid: str, text: str, **kwargs: Any) -> bool:
        if uid == "a1":
            events.append("append")
        return original_add(self, uid, text, **kwargs)

    monkeypatch.setattr(GameInstance, "ai_player_action_stale_reason", spy_reason)
    monkeypatch.setattr(GameInstance, "_add_action_locked", spy_add)

    records = await fill_ai_player_actions(instance, llm_client=llm)
    # 让出一次事件循环，给那个被排队的控制权变更执行的机会。
    await asyncio.sleep(0)

    # 复核发生在持锁状态下，且提交先于那个被排队的控制权变更。
    assert lock_held_during_recheck == [True]
    assert events == ["append", "flip"]
    assert [record["status"] for record in records] == ["added"]


@pytest.mark.asyncio
async def test_a_control_change_between_llm_and_commit_writes_nothing() -> None:
    """Case B 的行为面：LLM 已返回、控制权已易主时，一个字节都不写。"""

    instance = make_instance(humans=("h1",), ai=("a1",), solo=True)
    await instance.add_action("h1", "我点亮提灯。")
    before = [dict(action) for action in instance.action_queue]

    # LLM 返回后立刻把席位交给真人：复核必须看到 human 并拒绝写入。
    def hand_over(_index: int) -> None:
        set_control(instance, "a1", "human")

    llm = FakePlayerLLM(on_call=hand_over)

    records = await fill_ai_player_actions(instance, llm_client=llm)

    assert [record["status"] for record in records] == ["discarded"]
    assert records[0]["reason"] == "control_changed"
    assert instance.action_queue == before
    assert ai_actions(instance, "a1") == []


@pytest.mark.asyncio
async def test_two_concurrent_fills_commit_exactly_one_action() -> None:
    """Case C：两个并发的补行动请求，同一 (run, round, uid) 只能落一条。

    两个请求都会各自调用一次 LLM（去重预检查在锁外，这是刻意的：不能为了省一次
    模型调用而让模型请求占住 writer/state lock）。真正的保证在提交处：复核与
    去重同在一个 boundary 内，因此后到的那个必然看到 duplicate。
    """

    import asyncio

    instance = make_instance(humans=("h1",), ai=("a1",), solo=True)
    await instance.add_action("h1", "我点亮提灯。")

    class SlowPlayerLLM(FakePlayerLLM):
        async def call(self, system_prompt: str, user_message: str, **kwargs: Any) -> Any:
            # 让出一次事件循环，强制两个 fill 真正交错。
            await asyncio.sleep(0)
            return await super().call(system_prompt, user_message, **kwargs)

    first_llm = SlowPlayerLLM()
    second_llm = SlowPlayerLLM()

    results = await asyncio.gather(
        fill_ai_player_actions(instance, llm_client=first_llm),
        fill_ai_player_actions(instance, llm_client=second_llm),
    )

    # 只断言"最终只有一条"不够：``add_action`` 对同一 uid 是替换语义，所以即使
    # 两个请求都提交，最终也可能只剩一条（后写覆盖先写）。真正要锁死的是**只有
    # 一个**请求认为自己成功提交了，另一个必须在提交边界内看到 duplicate。
    statuses = sorted(record["status"] for batch in results for record in batch)
    assert statuses == ["added", "duplicate"]


@pytest.mark.asyncio
async def test_a_seat_claimed_by_a_human_mid_flight_closes_the_human_gate() -> None:
    """LLM 飞行期间另一个 AI 席位被真人接管 → 取消提交。

    AI 补行动的前提是"真人已经全部行动完成"。模型调用在锁外，所以它返回时桌面
    可能已经变了：多出一个尚未提交的 active human。此时若仍写入，就违反了
    「AI 只在真人全部行动之后才行动」这条核心契约。
    """

    instance = make_instance(humans=("h1",), ai=("a1", "a2"), solo=True)
    await instance.add_action("h1", "我点亮提灯。")
    assert instance.human_actions_ready() is True

    def hand_over_a2(index: int) -> None:
        if index == 0:
            # 只有第一个模型调用（a1）在飞；期间 a2 被真人接管且尚未行动。
            set_control(instance, "a2", "human")

    records = await fill_ai_player_actions(
        instance, llm_client=FakePlayerLLM(on_call=hand_over_a2),
    )

    assert instance.human_actions_ready() is False
    assert records[0]["status"] == "discarded"
    assert records[0]["reason"] == "human_gate_changed"
    assert ai_actions(instance, "a1") == []


@pytest.mark.asyncio
async def test_an_away_human_returning_mid_flight_closes_the_human_gate() -> None:
    """暂离的真人回来（重新成为 active human 且未提交）同样取消提交。"""

    instance = make_instance(humans=("h1", "h2"), ai=("a1",), solo=True)
    await instance.set_player_away("h2", True)
    await instance.add_action("h1", "我点亮提灯。")
    # h2 暂离时不阻塞，所以此刻闸门是开的。
    assert instance.human_actions_ready() is True

    def bring_h2_back(index: int) -> None:
        if index == 0:
            instance.away_players.discard("h2")

    records = await fill_ai_player_actions(
        instance, llm_client=FakePlayerLLM(on_call=bring_h2_back),
    )

    assert instance.human_actions_ready() is False
    assert records[0]["status"] == "discarded"
    assert records[0]["reason"] == "human_gate_changed"
    assert ai_actions(instance, "a1") == []


@pytest.mark.asyncio
async def test_an_open_human_gate_still_commits() -> None:
    """反向对照：闸门仍然开着时正常提交，证明上面两条不是"永远拒绝"。"""

    instance = make_instance(humans=("h1",), ai=("a1",), solo=True)
    await instance.add_action("h1", "我点亮提灯。")

    records = await fill_ai_player_actions(instance, llm_client=FakePlayerLLM())

    assert records[0]["status"] == "added"


@pytest.mark.asyncio
async def test_repeated_service_call_does_not_generate_a_second_action() -> None:
    instance = make_instance(humans=("h1", "h2"), ai=("a1",))
    llm = FakePlayerLLM()
    dependencies = make_dependencies(instance, llm_client=llm)

    await submit_action(dependencies, "game", "h1", "我点亮提灯。")
    advanced = await submit_action(dependencies, "game", "h2", "我检查窗户。")
    retry = await submit_action(dependencies, "game", "h1", "我点亮提灯。")

    assert advanced["payload"]["advanced"] is True
    assert retry["status"] == 409
    assert len(llm.calls) == 1


# ---- 10. 感知边界：GM 隐藏真相绝不进入 AI prompt -----------------------------


@pytest.mark.asyncio
async def test_gm_hidden_truth_never_reaches_the_ai_prompt() -> None:
    instance = make_instance(humans=("h1",), ai=("a1",), solo=True)
    instance.players["a1"]["character_name"] = "侦探阿澈"
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "bridge:old.open", "value": True},
        {"op": "set_fact", "key": "secret:cult.leader", "value": "GM-SECRET-VALUE-7f2a",
         "visibility": "gm"},
    ])
    instance.gm_directives = [{
        "id": "d1", "text": "DIRECTIVE-TOKEN-9c1", "target_round": 1,
    }]
    private_channels.replace_private_log(instance, {
        "h1": [{"round": 1, "text": "OTHER-PRIVATE-1a2"}],
        "a1": [{"round": 1, "text": "OWN-PRIVATE-4d5"}],
    })
    llm = FakePlayerLLM(replies=["我搜查船坞。"])
    dependencies = make_dependencies(instance, llm_client=llm)

    result = await submit_action(dependencies, "game", "h1", "我点亮提灯。")
    assert result["payload"]["advanced"] is True
    assert len(llm.calls) == 1

    prompt = llm.prompt(0)
    # AI 席位自己的角色卡与公开世界真相确实在 prompt 里（说明检查有意义）。
    assert "侦探阿澈" in prompt
    assert "bridge:old.open" in prompt
    assert "OWN-PRIVATE-4d5" in prompt
    # GM 私有世界事实的 key 与 value 都不出现。
    assert "secret:cult.leader" not in prompt
    assert "GM-SECRET-VALUE-7f2a" not in prompt
    # GM 私密指令与其他玩家的私密记录同样不出现。
    assert "DIRECTIVE-TOKEN-9c1" not in prompt
    assert "OTHER-PRIVATE-1a2" not in prompt
    # prompt 里出现 "GM" 只可能是「你不是本局的 GM」这类边界说明，这不是泄漏；
    # 真正要禁的是 GM 专属数据段本身出现在模型输入里。
    assert "gm_directives" not in prompt.casefold()
    assert "gm_only" not in prompt.casefold()
    assert '"visibility":"gm"' not in prompt.casefold().replace(" ", "")


# ---- 11/12. 真人席位与纯真人桌面不受影响 ------------------------------------


@pytest.mark.asyncio
async def test_human_controlled_seats_are_never_filled() -> None:
    instance = make_instance(humans=("h1", "h2"), ai=("a1",))
    llm = FakePlayerLLM()
    dependencies = make_dependencies(instance, llm_client=llm)

    await submit_action(dependencies, "game", "h1", "我点亮提灯。")
    result = await submit_action(dependencies, "game", "h2", "我检查窗户。")

    assert result["payload"]["advanced"] is True
    assert queue_uids(instance) == ["h1", "h2", "a1"]
    assert len(llm.calls) == 1
    assert "AIa1" in llm.prompt(0)
    assert "AIh1" not in llm.prompt(0) and "AIh2" not in llm.prompt(0)
    for uid in ("h1", "h2"):
        action = instance.action_queue[[
            str(item.get("user_id") or "") for item in instance.action_queue
        ].index(uid)]
        assert action["metadata"] if False else "metadata" not in action
        assert action["source"] == ""


@pytest.mark.asyncio
async def test_pure_human_table_behaves_exactly_as_before() -> None:
    instance = make_instance(humans=("h1", "h2"))
    llm = FakePlayerLLM()
    dependencies = make_dependencies(instance, llm_client=llm)

    first = await submit_action(dependencies, "game", "h1", "我点亮提灯。")
    status = instance.multiplayer_status()

    assert first["payload"]["advanced"] is False
    assert first["payload"]["multiplayer"]["waiting_players"] == [
        {"user_id": "h2", "character_name": "真人h2"},
    ]
    assert status["ai_players"] == [] and status["unclaimed_players"] == []
    assert status["ai_count"] == 0 and status["unclaimed_count"] == 0
    assert instance.human_actions_ready() is False

    second = await submit_action(dependencies, "game", "h2", "我检查窗户。")

    assert second["payload"]["advanced"] is True
    assert queue_uids(instance) == ["h1", "h2"]
    assert llm.calls == []
    assert all("metadata" not in action for action in instance.action_queue)
    # ready barrier 语义不变：两个真人都就绪，没有 AI/未认领席位参与。
    assert instance.all_alive_ready() is True
    assert instance.active_human_players == {"h1", "h2"}


# ---- 13. 人设约束必须真的进入 prompt（B11 / B15）-----------------------------


@pytest.mark.parametrize("language", sorted(PERSONA_PROMPT_MARKERS))
def test_persona_constraint_is_in_the_system_prompt_for_every_locale(
    language: str,
) -> None:
    """「按角色卡扮演」是硬性约束，四语言都必须出现在 system prompt 里。"""

    instance = make_instance(humans=("h1",), ai=("a1",))
    instance.language = language

    prompt = build_ai_player_prompt(instance, "a1")

    for marker in PERSONA_PROMPT_MARKERS[language]:
        assert marker in prompt, f"{language} system prompt 缺少人设约束: {marker!r}"
    # 人设强化不能突破"只代表自己这个角色"的边界。
    for marker in BOUNDARY_PROMPT_MARKERS[language]:
        assert marker in prompt, f"{language} system prompt 丢了角色边界: {marker!r}"


@pytest.mark.asyncio
async def test_persona_constraint_and_character_sheet_reach_the_model() -> None:
    """人设约束进 system prompt，角色卡进 player-safe context（B15）。

    不断言真实模型输出——单元测试只锁定"约束被送入 prompt、角色卡被送入
    context"，这才是不依赖服务商的契约。
    """

    instance = make_instance(humans=("h1",), ai=("a1",), solo=True)
    instance.language = "zh-CN"
    instance.players["a1"]["character_name"] = "提莫"
    sheet = instance.players["a1"]["character_sheet"]
    sheet.update(PERSONA_SHEET)
    sheet["attributes"] = dict(PERSONA_SHEET["attributes"])
    # 另一个角色的角色卡绝不能出现在这个席位的 context 里（B13）。
    instance.players["h1"]["character_sheet"]["personality"] = "OTHER-SHEET-7a3c"
    await instance.add_action("h1", "我点亮提灯。")
    llm = FakePlayerLLM(replies=["我缩到墙角，等他们走远了再动。"])

    records = await fill_ai_player_actions(instance, llm_client=llm)

    assert [record["status"] for record in records] == ["added"]
    assert len(llm.calls) == 1
    system_prompt = llm.calls[0]["system"]
    context = llm.calls[0]["user"]

    # 1) system prompt 明确要求遵循角色卡、按角色扮演。
    assert "扮演角色" in system_prompt
    assert "角色卡" in system_prompt
    # 2) player-safe context 确实把该席位自己的 character_sheet 送了进去。
    assert '"character_sheet"' in context
    for field in ("background", "personality", "ideals", "bonds", "goals"):
        assert PERSONA_SHEET[field] in context, f"角色卡字段 {field} 没有进入 context"
    assert "提莫" in context
    # 3) 别人的角色卡依然不可见。
    assert "OTHER-SHEET-7a3c" not in context


# ---- 全 AI 桌（没有任何真人席位）------------------------------------------
#
# 单人局把房主自己设为 AI 托管后，``active_human_players`` 为空：
# ``human_actions_ready()`` 因为没有真人席位恒为 False，而该席位的人工提交会被
# ``submission_block`` 以 PLAYER_AI_CONTROLLED 拒绝。若闸门只认前者，这一桌就
# 「没人能提交、AI 也不补行动」，永远发不出内容。这四条锁住修正后的语义。


@pytest.mark.asyncio
async def test_all_ai_table_fills_actions_without_any_human_seat() -> None:
    instance = make_instance(humans=(), ai=("a1",), solo=True)
    llm = FakePlayerLLM()

    results = await fill_ai_player_actions(instance, llm_client=llm)

    assert [record["status"] for record in results] == ["added"]
    assert len(llm.calls) == 1
    assert ai_actions(instance, "a1")


@pytest.mark.asyncio
async def test_unclaimed_only_table_is_never_filled() -> None:
    """只有未认领席位的桌子不属于 AI，兜底不得把它唤醒。"""

    instance = make_instance(humans=(), unclaimed=("u1",))
    llm = FakePlayerLLM()

    assert await fill_ai_player_actions(instance, llm_client=llm) == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_a_pending_human_still_blocks_every_ai_seat() -> None:
    """有真人但未交齐时，AI 依旧完全不行动（不与未提交的真人并行）。"""

    instance = make_instance(humans=("h1",), ai=("a1",))
    llm = FakePlayerLLM()

    assert await fill_ai_player_actions(instance, llm_client=llm) == []
    assert llm.calls == []


def test_ai_fill_gate_matrix() -> None:
    from src.commands.ai_player import _ai_fill_gate_open

    # 全 AI 桌：允许（本次修复）
    assert _ai_fill_gate_open(make_instance(humans=(), ai=("a1",))) is True
    # 只有 unclaimed：不允许
    assert _ai_fill_gate_open(make_instance(humans=(), unclaimed=("u1",))) is False
    # 有真人未交齐：不允许
    assert _ai_fill_gate_open(make_instance(humans=("h1",), ai=("a1",))) is False
    # 真人交齐：允许（原有语义）。直接置 ready 集合，避免在此处引入异步写入。
    ready = make_instance(humans=("h1",), ai=("a1",))
    ready.ready_players.add("h1")
    assert _ai_fill_gate_open(ready) is True


# ---- 真实 Web 入口（turns service）----------------------------------------
#
# 上面那些测试直接调用命令层 ``fill_ai_player_actions``。这一组必须证明
# **真实服务入口**也能把全 AI 桌送进同一个闸门：曾经的旧闸门
# （``turns._fill_ai_player_actions`` 与 ``resume_after_control_change`` 各自的
# ``human_actions_ready()`` 提前 return）会在命令层之前就把全 AI 桌挡掉。


@pytest.mark.asyncio
async def test_solo_owner_switching_to_ai_hosting_resumes_and_writes_an_action() -> None:
    from src.webui.services.turns import resume_after_control_change

    instance = make_instance(humans=("h1",), solo=True)
    llm = FakePlayerLLM()
    dependencies = make_dependencies(instance, llm_client=llm)

    # 房主把自己设为 AI 托管：active_human_players 变空。
    set_control(instance, "h1", "ai")
    assert instance.active_human_players == set()

    result = await resume_after_control_change(
        dependencies, "web:ai-player:bot", seat_uid="h1",
    )

    assert len(ai_actions(instance, "h1")) == 1, "控制权变更后 AI 必须真正写入行动"
    assert len(llm.calls) == 1
    assert result["payload"]["resumed"] is True
