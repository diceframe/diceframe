"""Immediate AI takeover on a control change (AI hosting plan, PR B).

Before this PR a GM handing a seat to the AI changed one persisted record and
nothing else happened: the server only produced the seat's action the next time
*somebody else* submitted something.  This file locks down the fix -- a control
change wakes up the one canonical progression boundary that already exists:

* exploration -- ``fill_ai_player_actions`` -> ``try_advance`` -> ``_prepare_checks``
  -> ``_process_round`` (``src/webui/services/turns.py``), never a second
  pipeline and never a faked human action;
* authoritative combat -- the existing deterministic ``AutomaticIntentRuntime``
  ladder (``next_automatic_intent`` -> ``resolve_intent`` -> ``apply_event_batch``),
  with no LLM rule authority at all.

Behaviour under test:

* case 1 -- the last pending human is handed to the AI while everybody else has
  already declared: an AI action is generated and the round advances immediately,
  without anybody submitting anything;
* case 2 -- another human is still pending: nothing is generated and nothing
  advances; a seat that already declared is never overwritten by the AI;
* case 3 -- the seat is taken back by a human while the model call is in flight:
  the stale AI output is discarded (the existing control-revision race guard);
* case 4 -- a repeated ``set ai`` in the same round never yields a second AI
  action;
* combat case 1 -- the current actor is a human PC: ``set control ai`` makes the
  server play that turn through the authoritative chain and the actor advances;
* combat case 2 -- the current actor is somebody else: nothing is seized;
* persistence -- the control change *and* whatever the takeover produced reach a
  persisted snapshot, and a failed combat takeover rolls back instead of
  half-committing.

Every test drives the real ``GameControlService`` with the real
``turns.resume_after_control_change`` and the real
``ruleset_gameplay.resume_authoritative_combat``; only the LLM client and the
round processor are substitutes (the substitute round processor also saves, like
the real ``RoundProcessor`` does at its commit point).
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from src.commands.ai_player import AI_ACTION_SOURCE, DISCARD_MARKER, fill_ai_player_actions
from src.engine import economy as economy_module
from src.engine.game_instance import GameInstance, GameState
from src.engine.player_control import control_mode, get_control, set_control
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services import ruleset_gameplay
from src.webui.services.game_controls import (
    GameControlDependencies,
    GameControlService,
)
from src.webui.services.turns import (
    TurnDependencies,
    resume_after_control_change,
    submit_action,
)
from src.engine.modules import ruleset_runtime, table_settings

GAME_KEY = "web|ai-takeover|bot"
GAME_KEY_PARTS = ("web", "ai-takeover", "bot")

HUMAN_SHEET = {"hp": 10, "max_hp": 10, "attributes": {"str": 14, "dex": 10}}
AI_SHEET = {"hp": 8, "max_hp": 8, "attributes": {"str": 8, "dex": 16}}


# ---- fakes ------------------------------------------------------------------


class FakePlayerLLM:
    """Fake provider that records prompts and can act while a call is in flight."""

    def __init__(self, *, replies: list[str] | None = None, on_call: Any = None) -> None:
        self.default = "fake"
        self.replies = replies
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    async def call(self, system_prompt: str, user_message: str, **kwargs: Any) -> Any:
        index = len(self.calls)
        self.calls.append({
            "system": system_prompt, "user": user_message, "kwargs": kwargs,
        })
        if self.on_call is not None:
            self.on_call(index)
        text = (
            self.replies[index]
            if self.replies is not None and index < len(self.replies)
            else f"我执行编号{index}的任务"
        )
        return SimpleNamespace(
            narration=text, content=text, total_tokens=13, provider_used="fake",
        )


class BrokenPlayerLLM(FakePlayerLLM):
    """Provider that always fails, to lock the ``AI_ACTION_SKIPPED`` semantics."""

    async def call(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("provider down")


class SaveRecorder:
    """Records every persisted snapshot so persistence can be asserted."""

    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []

    async def __call__(self, instance: GameInstance) -> None:
        self.snapshots.append(instance.to_dict())

    @property
    def last(self) -> dict[str, Any]:
        assert self.snapshots, "没有任何落盘"
        return self.snapshots[-1]


async def _unused(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("该依赖不应被调用")


async def _no_binding(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise AssertionError("测试实例没有 adventure binding")


# ---- fixtures ---------------------------------------------------------------


def make_instance(
    *,
    humans: tuple[str, ...] = ("h1",),
    ai: tuple[str, ...] = (),
    solo: bool = False,
) -> GameInstance:
    instance = GameInstance(game_key=GAME_KEY_PARTS, rule_id="test")
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    table_settings.replace_solo_mode(instance, solo)
    for uid in (*humans, *ai):
        sheet = HUMAN_SHEET if uid in humans else AI_SHEET
        instance.put_player(uid, {
            "user_id": uid,
            "character_name": f"角色{uid}",
            "character_sheet": dict(sheet, attributes=dict(sheet["attributes"])),
        })
    for uid in ai:
        set_control(instance, uid, "ai")
    return instance


class TakeoverFixture:
    """The three real services wired exactly like ``src/webui/api.py``."""

    def __init__(
        self,
        instance: GameInstance,
        llm: FakePlayerLLM,
        save: SaveRecorder,
        runtime: Dnd2024Runtime | None = None,
    ) -> None:
        self.instance = instance
        self.llm = llm
        self.save = save

        async def fill(target: GameInstance, **kwargs: Any) -> list[dict[str, Any]]:
            return await fill_ai_player_actions(target, llm_client=llm, **kwargs)

        async def process_round(_instance: GameInstance, **_kwargs: Any) -> tuple[str, Any]:
            # 真实 RoundProcessor 在提交点落盘；替身必须保留这个契约，
            # 否则"推进后是否落盘"的断言会变成假阳性。
            await save(_instance)
            return "本轮叙事", None

        registry = RulesetRuntimeRegistry([runtime] if runtime is not None else [])
        rule = (
            SimpleNamespace(template={"runtime": {
                "id": runtime.runtime_id, "minimum_version": runtime.runtime_version,
            }})
            if runtime is not None
            else None
        )

        def load_rule(_instance: GameInstance) -> Any:
            return rule

        gameplay_dependencies = ruleset_gameplay.RulesetGameplayDependencies(
            get_instance=lambda _key: instance,
            parse_game_key=lambda _key: GAME_KEY_PARTS,
            load_rule_for_game=load_rule,
            ruleset_registry=registry,
            resolve_adventure_binding=_no_binding,
            save_instance=save,
            apply_memory_delta=None,
            resolve_llm_client=None,
        )
        self.turns = TurnDependencies(
            get_instance=lambda _key: instance,
            parse_game_key=lambda _key: GAME_KEY_PARTS,
            ruleset_registry=registry,
            load_rule_for_game=load_rule,
            prepare_round_checks_ai=None,
            prepare_round_checks=None,
            resolve_pending_dice=_unused,
            roll_for_game=lambda _key: {"ok": False},
            save_instance=save,
            process_round=process_round,
            resolve_luck_decision=_unused,
            decline_pending_luck=_unused,
            fill_ai_player_actions=fill,
            resume_authoritative_combat=lambda game_key, seat_uid: (
                ruleset_gameplay.resume_authoritative_combat(
                    gameplay_dependencies, game_key, seat_uid,
                )
            ),
        )
        self.controls = GameControlService(GameControlDependencies(
            parse_game_key=lambda _key: GAME_KEY_PARTS,
            get_instance=lambda _key: instance,
            save_instance=save,
            load_rule=load_rule,
            resume_after_control_change=lambda game_key, seat_uid: (
                resume_after_control_change(
                    self.turns, game_key, seat_uid=seat_uid,
                )
            ),
        ))


def queue_uids(instance: GameInstance) -> list[str]:
    return [str(action.get("user_id") or "") for action in instance.action_queue]


def ai_actions(instance: GameInstance, uid: str) -> list[dict[str, Any]]:
    return [
        action for action in instance.action_queue
        if str(action.get("user_id") or "") == uid
        and isinstance(action.get("metadata"), dict)
        and str(action["metadata"].get("source") or "") == AI_ACTION_SOURCE
    ]


def saved_actions(snapshot: dict[str, Any], uid: str) -> list[dict[str, Any]]:
    return [
        action for action in snapshot["action_queue"]
        if str(action.get("user_id") or "") == uid
    ]


# ---- B16 case 1: 最后一个未行动的真人交给 AI，立即继续 ------------------------


@pytest.mark.asyncio
async def test_last_pending_human_handed_to_ai_resumes_immediately() -> None:
    instance = make_instance(humans=("h1", "h2"))
    await instance.add_action("h1", "我点亮提灯。")
    assert instance.human_actions_ready() is False  # h2 还没交
    fixture = TakeoverFixture(instance, FakePlayerLLM(
        replies=["我缩到墙角，等他们走远再动。"],
    ), SaveRecorder())

    result = await fixture.controls.set_player_control(GAME_KEY, "h2", "ai")

    assert result["ok"] is True
    assert control_mode(instance, "h2") == "ai"
    # 切换之后真人一侧立刻交齐：AI 行动被生成、本轮被推进。
    assert result["resume"]["resumed"] is True
    assert result["resume"]["advanced"] is True
    assert len(fixture.llm.calls) == 1
    assert queue_uids(instance) == ["h1", "h2"]
    assert ai_actions(instance, "h2")[0]["text"] == "我缩到墙角，等他们走远再动。"
    assert instance.state == GameState.ACTIVE_JUDGMENT
    assert result["resume"]["narration"] == "本轮叙事"
    # 没有任何伪造的空行动：队列里每一条都是真实文本。
    assert all(str(action.get("text") or "").strip() for action in instance.action_queue)
    # 提示词走的是既有 player-safe 通道（不是第二条 AI pipeline）。
    assert "角色h2" in fixture.llm.calls[0]["user"]


# ---- B16 case 2: 仍有真人未行动，不抢跑 --------------------------------------


@pytest.mark.asyncio
async def test_takeover_does_not_run_ahead_of_a_pending_human() -> None:
    instance = make_instance(humans=("h1", "h2"))
    await instance.add_action("h2", "我检查窗户。")
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder())

    result = await fixture.controls.set_player_control(GAME_KEY, "h2", "ai")

    assert result["ok"] is True
    assert result["resume"]["resumed"] is False
    assert result["resume"]["reason"] == "human_gate_open"
    assert fixture.llm.calls == []
    assert queue_uids(instance) == ["h2"]
    assert instance.state == GameState.ACTIVE_ACTION
    assert instance.human_actions_ready() is False  # h1 仍然在等
    # 剩下的真人照常提交，本轮正常推进。
    submitted = await submit_action(fixture.turns, GAME_KEY, "h1", "我点亮提灯。")
    assert submitted["payload"]["advanced"] is True
    assert queue_uids(instance) == ["h2", "h1"]


@pytest.mark.asyncio
async def test_a_seat_that_already_declared_is_never_overwritten_by_the_ai() -> None:
    """真人先出手、GM 之后才把席位交给 AI：补行动只补还没行动的席位。

    这条比施工单多锁一层：控制权一变就接管之后，"已经行动过的席位被设为
    AI"变得非常容易发生，而补行动对同一 uid 是**替换**语义——若不做这个
    判断，真人的声明会被 AI 生成文本覆盖掉。
    """

    instance = make_instance(humans=("h1", "h2"))
    await instance.add_action("h2", "我检查窗户。")
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder())
    # h1 还没动 → 闸门关着，切换本身不产生任何行动。
    await fixture.controls.set_player_control(GAME_KEY, "h2", "ai")
    assert control_mode(instance, "h2") == "ai"
    assert fixture.llm.calls == []

    submitted = await submit_action(fixture.turns, GAME_KEY, "h1", "我点亮提灯。")

    assert submitted["payload"]["advanced"] is True
    assert fixture.llm.calls == []
    assert queue_uids(instance) == ["h2", "h1"]
    texts = [str(action.get("text") or "") for action in instance.action_queue]
    assert "我检查窗户。" in texts
    assert ai_actions(instance, "h2") == []


# ---- B16 case 3: 飞行中切回真人 → 旧 AI 输出 discard --------------------------


@pytest.mark.asyncio
async def test_taking_the_seat_back_mid_flight_discards_the_ai_output(caplog) -> None:
    instance = make_instance(humans=("h1", "h2"))
    await instance.add_action("h1", "我点亮提灯。")
    llm = FakePlayerLLM(
        replies=["我去撬开侧门。"],
        on_call=lambda _index: set_control(instance, "h2", "human"),
    )
    fixture = TakeoverFixture(instance, llm, SaveRecorder())

    with caplog.at_level(logging.WARNING, logger="trpg"):
        result = await fixture.controls.set_player_control(GAME_KEY, "h2", "ai")

    assert result["ok"] is True  # 控制权本身仍然成功
    assert instance.state == GameState.ACTIVE_ACTION
    assert ai_actions(instance, "h2") == []
    assert queue_uids(instance) == ["h1"]
    assert DISCARD_MARKER in caplog.text
    assert "control_changed" in caplog.text
    # 席位被真人接管且尚未行动 → 本轮绝不能推进：这次调用补了 0 条行动，
    # 而且闸门在调用过程中被重新关上，所以结果是"没有可推进的回合"。
    assert result["resume"]["resumed"] is False
    assert result["resume"]["reason"] == "not_advanceable"


# ---- B16 case 4: 同一 round 重复 set ai → 只有一条 AI 行动 ---------------------


@pytest.mark.asyncio
async def test_repeated_set_ai_in_the_same_round_adds_only_one_action(
    monkeypatch,
) -> None:
    """本轮被待确认的经济提案挡住时，同一 round 的重复 set ai 只能有一次调用。

    闸门开着而本轮不推进（``has_blocking_economy_decision``）是"同一 round
    重复 set ai"唯一可达的现实路径：补行动已经写入，round 仍停在行动阶段。
    """

    instance = make_instance(humans=("h1", "h2"))
    await instance.add_action("h1", "我点亮提灯。")
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder())
    monkeypatch.setattr(
        economy_module, "has_blocking_economy_decision",
        lambda _instance, **_kwargs: True,
    )

    first = await fixture.controls.set_player_control(GAME_KEY, "h2", "ai")
    second = await fixture.controls.set_player_control(GAME_KEY, "h2", "ai")

    assert first["ok"] is True and second["ok"] is True
    # 补行动写入了，但本轮没有推进。
    assert first["resume"]["resumed"] is False
    assert first["resume"]["reason"] == "not_advanceable"
    assert len(ai_actions(instance, "h2")) == 1
    # 第二次 set ai 是控制权 no-op：不再调用模型、不产生第二条 AI 行动。
    assert second["control"]["revision"] == first["control"]["revision"]
    assert len(fixture.llm.calls) == 1
    assert len(ai_actions(instance, "h2")) == 1
    # B18：补了行动但本轮没推进时，那条行动也必须落盘。
    assert saved_actions(fixture.save.last, "h2")
    assert fixture.save.last["players"]["h2"]["control"]["mode"] == "ai"


# ---- B18: 控制权与自动推进的结果都落盘 ---------------------------------------


@pytest.mark.asyncio
async def test_the_control_change_and_the_generated_action_are_both_saved() -> None:
    instance = make_instance(humans=("h1", "h2"))
    await instance.add_action("h1", "我点亮提灯。")
    fixture = TakeoverFixture(instance, FakePlayerLLM(replies=["我跟上队伍。"]), SaveRecorder())

    await fixture.controls.set_player_control(GAME_KEY, "h2", "ai")

    assert fixture.save.last["players"]["h2"]["control"] == {
        "mode": "ai", "revision": 1, "temporary": False, "resume_mode": None,
    }
    assert saved_actions(fixture.save.last, "h2")


# ---- 暂离托管同样是 human→ai -------------------------------------------------


@pytest.mark.asyncio
async def test_away_takeover_resumes_immediately() -> None:
    instance = make_instance(humans=("h1", "h2"))
    instance.away_control_policy = "ai_takeover"
    await instance.add_action("h1", "我点亮提灯。")
    fixture = TakeoverFixture(instance, FakePlayerLLM(replies=["我先找个掩体。"]), SaveRecorder())

    result = await fixture.controls.set_player_away(GAME_KEY, "h2", True)

    assert result["ok"] is True
    assert get_control(instance, "h2")["temporary"] is True
    assert result["resume"]["resumed"] is True
    assert len(ai_actions(instance, "h2")) == 1


@pytest.mark.asyncio
async def test_pause_away_never_resumes() -> None:
    """默认 pause 不改控制权，因此也不会触发即时接管。"""

    instance = make_instance(humans=("h1", "h2"))
    await instance.add_action("h1", "我点亮提灯。")
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder())

    result = await fixture.controls.set_player_away(GAME_KEY, "h2", True)

    assert result["ok"] is True
    assert "resume" not in result
    assert fixture.llm.calls == []
    assert instance.state == GameState.ACTIVE_ACTION


@pytest.mark.asyncio
async def test_switching_a_seat_back_to_human_never_resumes() -> None:
    """AI → human 只是停止托管：不会生成行动，也不会替刚回来的真人推进。"""

    instance = make_instance(humans=("h1",), ai=("a1",))
    await instance.add_action("h1", "我点亮提灯。")
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder())

    result = await fixture.controls.set_player_control(GAME_KEY, "a1", "human")

    assert result["ok"] is True
    assert control_mode(instance, "a1") == "human"
    assert "resume" not in result
    assert fixture.llm.calls == []
    assert instance.state == GameState.ACTIVE_ACTION


@pytest.mark.asyncio
async def test_away_takeover_that_cannot_resume_is_refused_while_the_round_is_busy() -> None:
    """进行中的回合里控制权切换仍然被拒——即时接管没有放松安全边界。"""

    instance = make_instance(humans=("h1", "h2"))
    instance.away_control_policy = "ai_takeover"
    instance.state = GameState.ACTIVE_JUDGMENT
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder())

    result = await fixture.controls.set_player_away(GAME_KEY, "h2", True)

    assert result["ok"] is False
    assert result["error_code"] == "CONTROL_CHANGE_BUSY"
    assert control_mode(instance, "h2") == "human"


# ---- B19: provider 失败不阻塞、也不伪造推进 ----------------------------------


@pytest.mark.asyncio
async def test_provider_failure_keeps_the_control_change_and_never_fakes_an_action(
    caplog,
) -> None:
    """供应商失败只跳过该席位（``AI_ACTION_SKIPPED``），不崩、也不伪造行动。"""

    instance = make_instance(humans=("h1", "h2"))
    await instance.add_action("h1", "我点亮提灯。")
    fixture = TakeoverFixture(instance, BrokenPlayerLLM(), SaveRecorder())

    with caplog.at_level(logging.WARNING, logger="trpg"):
        result = await fixture.controls.set_player_control(GAME_KEY, "h2", "ai")

    assert result["ok"] is True
    assert control_mode(instance, "h2") == "ai"
    assert "AI_ACTION_SKIPPED" in caplog.text
    # 一个字节都没写：失败的席位没有行动，也没有被顶替成空行动。
    assert ai_actions(instance, "h2") == []
    assert queue_uids(instance) == ["h1"]
    # 沿用既有探索语义：真人一侧已经交齐，本轮照常推进，服务端不崩。
    assert result["resume"]["resumed"] is True
    assert result["resume"]["advanced"] is True
    # UI 仍然看得到"这个席位托管着、但本轮没有行动"。
    status = instance.multiplayer_status()
    assert [player["user_id"] for player in status["ai_players"]] == ["h2"]
    assert "h2" not in {item["user_id"] for item in status["submitted_actions"]}


# ---- B17: D&D 权威战斗的即时接管 ---------------------------------------------


@dataclass
class SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum
        return value


def _character(runtime: Dnd2024Runtime, preset_id: str, name: str) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": name},
    )


def _goblin() -> dict:
    return {
        "id": "goblin-1", "name": "Goblin", "hp": 18, "max_hp": 18, "armor_class": 12,
        "speed": 30, "position": 5, "initiative_modifier": 2,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _combat_setup() -> tuple[Dnd2024Runtime, Dnd2024CombatEngine, GameInstance]:
    """Three-seat table: gm (human PC), ai1 (AI-hosted) and u1 (unclaimed)."""

    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=GAME_KEY_PARTS, rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    instance.state = GameState.ACTIVE_ACTION
    gm = _character(runtime, "lucky_scout", "GM")
    instance.players["gm"] = {"character_name": "GM", "character_sheet": gm}
    ai = _character(runtime, "stalwart_guardian", "Aria")
    ai["ruleset_character"]["equipment"]["item_refs"] = ["item:greatsword"]
    instance.players["ai1"] = {"character_name": "Aria", "character_sheet": ai}
    assert instance.bind_ruleset_runtime(gm["rule_binding"])
    set_control(instance, "ai1", "ai")
    extra = _character(runtime, "stalwart_guardian", "Nobody")
    instance.players["u1"] = {"character_name": "Nobody", "character_sheet": extra}
    set_control(instance, "u1", "unclaimed")
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    return runtime, engine, instance


def _start_combat(engine: Dnd2024CombatEngine, instance: GameInstance) -> None:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
    }, SequenceRng([20, 1, 2, 3]))
    assert resolved["ok"] is True
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    assert ruleset_runtime.state(instance)["combat"]["status"] == "active"


def _set_turn(engine: Dnd2024CombatEngine, instance: GameInstance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    # 与既有战斗测试同一手法：手工摆到某个 actor 的回合并给它一份新经济。
    combat["economy"] = engine._fresh_economy(  # noqa: SLF001
        engine._actor_view(instance, combat, actor_id),  # noqa: SLF001
    )


def _current_actor(instance: GameInstance) -> str:
    combat = ruleset_runtime.state(instance)["combat"]
    return str(combat["initiative"][combat["turn_index"]])


@pytest.mark.asyncio
async def test_current_actor_switched_to_ai_is_played_immediately() -> None:
    runtime, engine, instance = _combat_setup()
    _start_combat(engine, instance)
    assert ruleset_runtime.state(instance)["combat"]["initiative"] == [
        "player:ai1", "player:gm", "player:u1", "enemy:goblin-1",
    ]
    _set_turn(engine, instance, "player:gm")
    version_before = int(ruleset_runtime.state(instance)["version"])
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder(), runtime=runtime)

    result = await fixture.controls.set_player_control(GAME_KEY, "gm", "ai")

    assert result["ok"] is True
    assert control_mode(instance, "gm") == "ai"
    assert result["resume"]["handled"] is True
    assert result["resume"]["resumed"] is True
    # 服务器真的提交了合法的 structured intent，而不是等真人再点一次。
    assert version_before < int(ruleset_runtime.state(instance)["version"])
    assert _current_actor(instance) != "player:gm"
    # 战斗没有引入 LLM 规则权威：一次模型调用都不该发生。
    assert fixture.llm.calls == []
    # B18：控制权与战斗推进一起落盘，重新读盘后仍然是推进后的状态。
    assert fixture.save.last["players"]["gm"]["control"]["mode"] == "ai"
    persisted = GameInstance.from_dict(fixture.save.last)
    combat = ruleset_runtime.state(persisted)["combat"]
    assert combat["initiative"][combat["turn_index"]] != "player:gm"


@pytest.mark.asyncio
async def test_a_seat_that_is_not_the_current_actor_never_seizes_the_turn() -> None:
    runtime, engine, instance = _combat_setup()
    _start_combat(engine, instance)
    _set_turn(engine, instance, "player:gm")  # 当前 actor 是另一个真人 PC
    version_before = int(ruleset_runtime.state(instance)["version"])
    combat_before = deepcopy(ruleset_runtime.state(instance)["combat"])
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder(), runtime=runtime)

    result = await fixture.controls.set_player_control(GAME_KEY, "u1", "ai")

    assert result["ok"] is True
    assert control_mode(instance, "u1") == "ai"
    assert result["resume"]["handled"] is True
    assert result["resume"]["resumed"] is False
    assert result["resume"]["reason"] == "not_this_seat"
    assert int(ruleset_runtime.state(instance)["version"]) == version_before
    assert ruleset_runtime.state(instance)["combat"] == combat_before
    assert _current_actor(instance) == "player:gm"
    assert fixture.llm.calls == []


@pytest.mark.asyncio
async def test_outside_combat_the_free_text_progression_takes_over() -> None:
    """没有进行中的权威战斗时，同一入口落到探索推进边界（B6）。"""

    runtime, _engine, instance = _combat_setup()
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder(), runtime=runtime)

    result = await fixture.controls.set_player_control(GAME_KEY, "u1", "ai")

    # 权威战斗分支报告"不归我管"，探索分支接着做闸门判断（gm 还没行动）。
    assert result["ok"] is True
    assert result["resume"]["resumed"] is False
    assert result["resume"]["reason"] == "human_gate_open"
    assert fixture.llm.calls == []


@pytest.mark.asyncio
async def test_a_failed_combat_takeover_rolls_back_instead_of_half_committing(
    monkeypatch,
) -> None:
    runtime, engine, instance = _combat_setup()
    _start_combat(engine, instance)
    _set_turn(engine, instance, "player:gm")
    state_before = deepcopy(ruleset_runtime.state(instance))
    ledger_before = deepcopy(ruleset_runtime.event_ledger(instance))
    fixture = TakeoverFixture(instance, FakePlayerLLM(), SaveRecorder(), runtime=runtime)
    saves_before = len(fixture.save.snapshots)
    monkeypatch.setattr(
        Dnd2024Runtime, "resolve_intent",
        lambda *_args, **_kwargs: {"ok": False, "error": "rules runtime exploded"},
    )

    result = await fixture.controls.set_player_control(GAME_KEY, "gm", "ai")

    assert result["ok"] is True  # 控制权本身已经写入并保存
    assert result["resume"]["handled"] is True
    assert result["resume"]["resumed"] is False
    assert result["resume"]["error_code"] == "AUTOMATIC_TURN_FAILED"
    # 状态回到失败之前，没有半提交，也没有多写一次存档。
    assert ruleset_runtime.state(instance) == state_before
    assert ruleset_runtime.event_ledger(instance) == ledger_before
    assert len(fixture.save.snapshots) == saves_before + 1  # 只有控制权那一次
    assert fixture.save.last["players"]["gm"]["control"]["mode"] == "ai"
