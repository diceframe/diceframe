"""AI hosting controls in game setup, roster and away handling (plan, PR5).

PR1 made *who plays a seat* persistent, PR2 made it authoritative, and PR3/PR4
made an AI-hosted seat actually play (exploration + combat).  This file locks
down the last piece: the room and the people in it can now *say* who controls a
seat, and a seat handed to the AI because its player stepped away is temporary
in a way that cannot silently become permanent.

Behaviour under test:

* creation carries a per-card control choice, a room-level default for the cards
  the creator did not decide, and a client that sends nothing at all still gets
  today's behaviour (every seat ``human``);
* an unknown control mode at creation fails closed instead of quietly building a
  default seat;
* ``away_control_policy`` is a persisted room setting: ``pause`` by default,
  ``pause`` for every save written before the setting existed, ``pause`` for a
  corrupt stored value, and it survives save/load;
* with ``ai_takeover``, stepping away hands the seat to the server AI in the
  *temporary* shape (``temporary=true`` with ``resume_mode=human``) and coming
  back returns it, while ``pause`` leaves control exactly where it was;
* a GM can hand a human seat to the AI and take it back, and that switch changes
  **only** the control record -- character sheet, HP, ready state and the combat
  actor stay put;
* every control transition only happens at a safe boundary; otherwise the caller
  gets the retryable ``CONTROL_CHANGE_BUSY``;
* temporary hosting is distinguishable from permanent hosting, and it never
  becomes permanent across save/load.

The tests drive the real Web API surface (``create_game``, ``set_player_away``,
``set_player_control``, ``set_away_control_policy``) on a real ``GameInstance``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.engine.modules import legacy_combat
from src.commands.ai_player import AI_ACTION_SOURCE
from src.engine.game_instance import GameInstance, GameState
from src.engine.player_control import (
    DEFAULT_AWAY_CONTROL_POLICY,
    PlayerControlError,
    away_control_policy,
    begin_away_hosting,
    control_mode,
    end_away_hosting,
    get_control,
    is_temporarily_ai_controlled,
    normalize_away_control_policy,
    set_away_control_policy,
    set_control,
)
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    migrate_game_state_payload,
)
from src.engine.modules import player_control_state

pytest_plugins = ["tests.webapi_harness"]


CHARACTERS = [
    {"character_name": "阿岚", "attributes": {"str": 12}},
    {"character_name": "米拉", "attributes": {"str": 11}},
    {"character_name": "白露", "attributes": {"str": 10}},
]


async def _create(api, **kwargs) -> tuple[str, Any]:
    created = await api.create_game(
        "template_world", "模板世界", players=[dict(c) for c in CHARACTERS], **kwargs,
    )
    assert created.get("ok") is not False, created
    instance = api.get_game_instance(created["game_key"])
    return created["game_key"], instance


def _uids(instance: GameInstance) -> list[str]:
    return sorted(instance.players)


# ---- 1. 开房：逐卡控制 + 未认领默认 -----------------------------------------


@pytest.mark.asyncio
async def test_creation_applies_a_per_card_control_choice(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    characters = [dict(c) for c in CHARACTERS]
    characters[0]["control"] = "human"
    characters[1]["control"] = "ai"
    characters[2]["control"] = "unclaimed"

    created = await api.create_game(
        "template_world", "模板世界", players=characters,
    )
    instance = api.get_game_instance(created["game_key"])
    uids = sorted(instance.players)

    assert [control_mode(instance, uid) for uid in uids] == ["human", "ai", "unclaimed"]
    # 每张卡的返回值也带上它自己的控制记录，前端不必再猜。
    assert [player.get("control", {}).get("mode") for player in created["players"]] == [
        "human", "ai", "unclaimed",
    ]
    # 创建时选的 AI 托管是「房间明确设置」的永久托管，不是暂离临时托管：
    # 它不带恢复目标，也不会因为某个玩家点「回来」而被交回。
    ai_record = get_control(instance, uids[1])
    assert ai_record["temporary"] is False
    assert ai_record["resume_mode"] is None
    assert is_temporarily_ai_controlled(instance, uids[1]) is False


@pytest.mark.asyncio
async def test_per_card_choice_beats_the_room_default(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    characters = [dict(c) for c in CHARACTERS]
    characters[0]["control"] = "human"  # 显式覆盖全局默认

    created = await api.create_game(
        "template_world", "模板世界", players=characters,
        unclaimed_control_default="ai",
    )
    instance = api.get_game_instance(created["game_key"])
    uids = sorted(instance.players)

    assert [control_mode(instance, uid) for uid in uids] == ["human", "ai", "ai"]


@pytest.mark.asyncio
async def test_creation_without_any_control_information_keeps_todays_behaviour(
    web_api,
) -> None:
    """旧客户端不发控制信息时，每个席位仍然是 human。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    _game_key, instance = await _create(api)

    assert {control_mode(instance, uid) for uid in instance.players} == {"human"}


@pytest.mark.asyncio
async def test_creation_fails_closed_on_an_unknown_control_mode(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    characters = [dict(c) for c in CHARACTERS]
    characters[1]["control"] = "remote_bot"

    created = await api.create_game(
        "template_world", "模板世界", players=characters,
    )

    assert created.get("ok") is False
    assert created.get("error_code") == "INVALID_PLAYER_CONTROL"


# ---- 2. 房间设置 away_control_policy ----------------------------------------


def test_away_policy_defaults_to_pause() -> None:
    instance = GameInstance(game_key="g")

    assert player_control_state.away_control_policy(instance) == DEFAULT_AWAY_CONTROL_POLICY == "pause"
    assert away_control_policy(instance) == "pause"


def test_away_policy_only_accepts_the_two_known_values() -> None:
    instance = GameInstance(game_key="g")

    assert set_away_control_policy(instance, "ai_takeover") == "ai_takeover"
    assert away_control_policy(instance) == "ai_takeover"
    with pytest.raises(PlayerControlError):
        set_away_control_policy(instance, "ai_everything")
    # 拒绝之后仍然是上一个合法值，不会被写坏。
    assert away_control_policy(instance) == "ai_takeover"


def test_a_save_written_before_the_setting_existed_becomes_pause() -> None:
    """v14 存档没有这个字段，唯一不猜测的答案是旧版本行为。"""

    migrated = migrate_game_state_payload({
        "instance_schema_version": 14,
        "game_key": ["web", "away-policy", "bot"],
        "players": {},
    })

    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["modules"]["player_control"]["away_control_policy"] == "pause"


def test_a_corrupt_stored_policy_reads_as_pause() -> None:
    assert normalize_away_control_policy("nonsense") == "pause"
    assert normalize_away_control_policy(None) == "pause"

    migrated = migrate_game_state_payload({
        "instance_schema_version": 14,
        "away_control_policy": "nonsense",
        "players": {},
    })
    assert migrated["modules"]["player_control"]["away_control_policy"] == "pause"


def test_away_policy_migration_is_idempotent() -> None:
    once = migrate_game_state_payload({
        "instance_schema_version": 14,
        "away_control_policy": "ai_takeover",
        "players": {},
    })
    twice = migrate_game_state_payload(once)

    assert once["modules"]["player_control"]["away_control_policy"] == "ai_takeover"
    assert twice["modules"]["player_control"]["away_control_policy"] == "ai_takeover"


def test_away_policy_survives_save_and_load() -> None:
    instance = GameInstance(game_key="g")
    set_away_control_policy(instance, "ai_takeover")

    recovered = GameInstance.from_dict(instance.to_dict())

    assert away_control_policy(recovered) == "ai_takeover"


@pytest.mark.asyncio
async def test_room_policy_is_settable_through_the_api(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)

    ok = await api.set_away_control_policy(game_key, "ai_takeover")
    assert ok["ok"] is True
    assert away_control_policy(instance) == "ai_takeover"

    bad = await api.set_away_control_policy(game_key, "whatever")
    assert bad["ok"] is False
    assert bad.get("error_code") == "AWAY_POLICY_UNSUPPORTED"


@pytest.mark.asyncio
async def test_the_policy_reaches_the_frontend_detail_payload(web_api) -> None:
    """房间设置要有 UI 入口，前提是详情 payload 带着当前值。

    否则前端只能硬编码默认值——GM 改成 ai_takeover 之后，弹窗会显示错误状态。
    """

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, _instance = await _create(api)

    default_detail = api.game_detail(game_key)
    assert default_detail["away_control_policy"] == "pause"

    await api.set_away_control_policy(game_key, "ai_takeover")

    updated_detail = api.game_detail(game_key)
    assert updated_detail["away_control_policy"] == "ai_takeover"


@pytest.mark.asyncio
async def test_the_policy_survives_a_reload_into_the_detail_payload(web_api) -> None:
    """刷新页面 / 重开进程后，房间设置仍然是 GM 选过的那个值。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")

    reloaded = GameInstance.from_dict(instance.to_dict())

    assert away_control_policy(reloaded) == "ai_takeover"


# ---- 3. 暂离语义 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_leaves_control_exactly_where_it_was(web_api) -> None:
    """默认 pause 保持旧语义：暂离只改在场状态，角色不交给 AI。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    uid = sorted(instance.players)[0]

    result = await api.set_player_away(game_key, uid, True)

    assert result["ok"] is True
    assert control_mode(instance, uid) == "human"
    assert uid in instance.away_players


@pytest.mark.asyncio
async def test_ai_takeover_hands_over_temporarily_and_gives_it_back(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")
    uid = sorted(instance.players)[0]
    sheet_before = dict(instance.players[uid]["character_sheet"])

    await api.set_player_away(game_key, uid, True)

    record = get_control(instance, uid)
    assert record["mode"] == "ai"
    assert record["temporary"] is True
    assert record["resume_mode"] == "human"
    assert is_temporarily_ai_controlled(instance, uid) is True

    back = await api.set_player_away(game_key, uid, False)

    assert back["ok"] is True
    assert control_mode(instance, uid) == "human"
    assert get_control(instance, uid)["temporary"] is False
    assert get_control(instance, uid)["resume_mode"] is None
    assert is_temporarily_ai_controlled(instance, uid) is False
    # 「无损」：暂离与回来都不动角色卡。
    assert dict(instance.players[uid]["character_sheet"]) == sheet_before


def test_returning_does_not_steal_a_permanently_hosted_seat() -> None:
    """回来只归还「临时托管」；GM 永久交给 AI 的席位不会被抢回。"""

    instance = GameInstance(game_key="g")
    instance.put_player("p1", {"character_name": "阿岚"})
    set_control(instance, "p1", "ai")

    assert end_away_hosting(instance, "p1")["mode"] == "ai"
    assert is_temporarily_ai_controlled(instance, "p1") is False


def test_away_hosting_is_a_no_op_for_a_seat_without_a_human() -> None:
    instance = GameInstance(game_key="g")
    instance.put_player("p1", {"character_name": "阿岚"})
    set_control(instance, "p1", "unclaimed")

    assert begin_away_hosting(instance, "p1")["mode"] == "unclaimed"


@pytest.mark.asyncio
async def test_temporary_hosting_does_not_become_permanent(web_api) -> None:
    """重启 / 重新加载之后，临时托管仍然可以归还，而不是变成永久 AI。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")
    uid = sorted(instance.players)[0]
    await api.set_player_away(game_key, uid, True)

    reloaded = GameInstance.from_dict(instance.to_dict())

    assert is_temporarily_ai_controlled(reloaded, uid) is True
    assert get_control(reloaded, uid)["resume_mode"] == "human"
    assert end_away_hosting(reloaded, uid)["mode"] == "human"
    assert control_mode(reloaded, uid) == "human"


# ---- 4. GM 托管控件 ----------------------------------------------------------


@pytest.mark.asyncio
async def test_handing_the_last_pending_human_to_the_ai_continues_through_the_api(
    web_api,
) -> None:
    """端到端：真实 WebAPI + 真实 GameHandler 下，切换后立刻继续（PR B）。

    这个测试锁的是组合根接线：`api.set_player_control` 必须把控制权写入后的
    即时接管接到既有的回合推进入口上，而不是只在服务单测里成立。
    """

    api, _lorebook, _registry, fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    uids = sorted(instance.players)
    # 三个席位里的两个真人先交行动，最后一个还没交。
    for uid in (uids[0], uids[2]):
        submitted = await api.submit_action(game_key, uid, "我点亮提灯。")
        assert submitted["payload"]["advanced"] is False
    assert instance.human_actions_ready() is False  # 真人一侧还没交齐
    calls_before = len(fake_llm.calls)

    hosted = await api.set_player_control(game_key, uids[1], "ai")

    assert hosted["ok"] is True
    assert control_mode(instance, uids[1]) == "ai"
    # 服务器立刻替该席位补了行动并推进，不需要真人再提交一次。
    new_calls = fake_llm.calls[calls_before:]
    assert new_calls, "切换之后服务器没有任何模型调用"
    # 其中至少一次是「按玩家身份扮演角色」的补行动调用，而不是 GM 叙事调用。
    assert any(
        "你只代表这一个角色" in str(call["system_prompt"]) for call in new_calls
    )
    assert hosted["resume"]["resumed"] is True
    assert hosted["resume"]["advanced"] is True
    # 本轮已经完成并进入日志：AI 席位补上的行动与真人行动在同一条队列里，
    # 且没有任何伪造的空行动。
    recorded = list(instance.log[-1]["actions"])
    by_uid = {str(action.get("user_id") or ""): action for action in recorded}
    assert set(by_uid) == set(uids)
    assert by_uid[uids[1]]["metadata"]["source"] == AI_ACTION_SOURCE
    assert str(by_uid[uids[1]]["text"]).strip()
    assert all(str(action.get("text") or "").strip() for action in recorded)


@pytest.mark.asyncio
async def test_gm_can_hand_a_human_seat_to_the_ai_and_take_it_back(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    uid = sorted(instance.players)[1]
    assert control_mode(instance, uid) == "human"

    hosted = await api.set_player_control(game_key, uid, "ai")
    assert hosted["ok"] is True
    assert control_mode(instance, uid) == "ai"
    # GM 的明确决定不是「暂离临时托管」，所以不带临时标记。
    assert is_temporarily_ai_controlled(instance, uid) is False

    stopped = await api.set_player_control(game_key, uid, "human")
    assert stopped["ok"] is True
    assert control_mode(instance, uid) == "human"


@pytest.mark.asyncio
async def test_gm_switch_changes_only_the_control_record(web_api) -> None:
    """切换托管不得复制角色、重置 ready / HP，也不得重建战斗 actor。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    uids = sorted(instance.players)
    uid = uids[1]
    instance.players[uid]["character_sheet"]["hp"] = 7
    instance.ready_players.add(uid)
    legacy_combat.replace_combat_enemies(instance, [{"actor_id": "enemy:1", "hp": 9}])
    sheet_before = dict(instance.players[uid]["character_sheet"])
    sheet_name_before = instance.players[uid]["character_name"]
    player_keys_before = set(instance.players[uid])

    await api.set_player_control(game_key, uid, "ai")

    assert dict(instance.players[uid]["character_sheet"]) == sheet_before
    assert instance.players[uid]["character_sheet"]["hp"] == 7
    assert instance.ready_players == {uid}
    assert legacy_combat.combat_enemies(instance) == [{"actor_id": "enemy:1", "hp": 9}]
    # 席位本身没有被重建：键集合不变，角色名不变。
    assert set(instance.players[uid]) == player_keys_before
    assert instance.players[uid]["character_name"] == sheet_name_before
    assert len(instance.players) == len(uids)


@pytest.mark.asyncio
async def test_gm_switch_refuses_an_unsupported_mode(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    uid = sorted(instance.players)[1]

    result = await api.set_player_control(game_key, uid, "unclaimed")

    assert result["ok"] is False
    assert result.get("error_code") == "CONTROL_MODE_UNSUPPORTED"
    assert control_mode(instance, uid) == "human"


# ---- 5. 安全边界 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_changes_are_refused_while_a_round_is_in_flight(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")
    uid = sorted(instance.players)[0]
    instance.state = GameState.ACTIVE_JUDGMENT

    hosted = await api.set_player_control(game_key, uid, "ai")
    away = await api.set_player_away(game_key, uid, True)

    for result in (hosted, away):
        assert result["ok"] is False
        assert result.get("error_code") == "CONTROL_CHANGE_BUSY"
    assert control_mode(instance, uid) == "human"


@pytest.mark.asyncio
async def test_away_and_back_work_at_the_safe_boundary(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")
    uid = sorted(instance.players)[0]
    instance.state = GameState.ACTIVE_ACTION

    assert (await api.set_player_away(game_key, uid, True))["ok"] is True
    assert (await api.set_player_away(game_key, uid, False))["ok"] is True


# ---- 5b. authority transaction：复核与写入必须原子 ---------------------------


@pytest.mark.asyncio
async def test_gm_control_change_is_refused_while_processing_holds_the_boundary(
    web_api,
) -> None:
    """Case A：处理边界一旦被占用，控制权转换不能插进去。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")
    uid = sorted(instance.players)[0]
    before = dict(get_control(instance, uid))

    async with instance._process_lock:
        blocked = await api.set_player_control(game_key, uid, "ai")
        away = await api.set_player_away(game_key, uid, True)

    assert blocked["ok"] is False
    assert blocked.get("error_code") == "CONTROL_CHANGE_BUSY"
    # ai_takeover 的暂离会改控制权，因此同样必须等安全边界。
    assert away["ok"] is False
    assert away.get("error_code") == "CONTROL_CHANGE_BUSY"
    assert get_control(instance, uid) == before
    assert uid not in instance.away_players


@pytest.mark.asyncio
async def test_the_safe_boundary_check_and_the_control_write_are_one_section(
    web_api, monkeypatch,
) -> None:
    """Case A 的结构性锁定：安全边界复核必须发生在**引擎事务内部**。

    在 ``control_change_block`` 判定安全的那一刻往事件循环排一个「立刻进入判定
    阶段」的回调，并断言写入先于它——即复核与写入之间没有让出事件循环。

    注意这条测试的证明边界：它真正锁死的是"复核不再位于服务层、而是随写入一起
    在聚合事务内执行"。如果有人把复核搬回服务层（本 blocker 之前的形态），补丁就
    打不中，``events`` 会为空而失败。它**不**单独证明任意实现下都没有 await。
    """

    import asyncio

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    uid = sorted(instance.players)[0]

    events: list[str] = []
    # 补丁必须打在 game_instance 的命名空间上：它是按名字导入的绑定，
    # 改 player_control 的模块属性不会影响已绑定的引用。
    from src.engine import game_instance as gi

    original_check = gi.control_change_block

    def spy_check(target: Any) -> str:
        result = original_check(target)
        if not result:
            asyncio.get_running_loop().call_soon(
                lambda: (events.append("processing"), setattr(
                    instance, "state", GameState.ACTIVE_JUDGMENT,
                )),
            )
        return result

    monkeypatch.setattr(gi, "control_change_block", spy_check)

    result = await api.set_player_control(game_key, uid, "ai")
    await asyncio.sleep(0)

    assert result["ok"] is True
    assert control_mode(instance, uid) == "ai"
    # 写入先发生；"开始处理"只能排在它之后。
    assert events == ["processing"]


@pytest.mark.asyncio
async def test_away_takeover_never_exposes_away_without_a_controller(
    web_api, monkeypatch,
) -> None:
    """Case B：暂离成功时，不可观察到 ``away=true`` 而控制者仍是真人。

    两件事一起断言：
    1. 在"在场状态写入"之后排一个记录回调，最终只可能观察到 ``(True, "ai")``
       ——已经离开却没有任何 AI 接手的席位是没人负责的状态；
    2. 控制权转换是在**持有 ``_lock``** 时发生的（引擎事务内），而不是像修复前
       那样由服务层在两次 await 之间另行调用。
    """

    import asyncio

    from src.engine import game_instance as gi

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")
    uid = sorted(instance.players)[0]

    observed: list[tuple[bool, str]] = []
    lock_held_during_handover: list[bool] = []
    original_locked = GameInstance._set_player_away_locked
    original_begin = gi.begin_away_hosting

    def spy_locked(self: GameInstance, user_id: str, away: bool) -> bool:
        ok = original_locked(self, user_id, away)
        if ok and away:
            asyncio.get_running_loop().call_soon(
                lambda: observed.append((user_id in self.away_players, control_mode(self, user_id))),
            )
        return ok

    def spy_begin(target: GameInstance, user_id: str) -> dict[str, Any]:
        lock_held_during_handover.append(target._lock.locked())
        return original_begin(target, user_id)

    monkeypatch.setattr(GameInstance, "_set_player_away_locked", spy_locked)
    monkeypatch.setattr(gi, "begin_away_hosting", spy_begin)

    result = await api.set_player_away(game_key, uid, True)
    await asyncio.sleep(0)

    assert result["ok"] is True
    assert observed == [(True, "ai")]
    assert lock_held_during_handover == [True]
    assert get_control(instance, uid)["temporary"] is True


@pytest.mark.asyncio
async def test_returning_restores_presence_and_control_together(web_api) -> None:
    """Case C：回来时四项必须一起变，不会出现"回来了但控制权还在 AI"。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")
    uid = sorted(instance.players)[0]
    await api.set_player_away(game_key, uid, True)
    assert get_control(instance, uid)["temporary"] is True

    result = await api.set_player_away(game_key, uid, False)

    assert result["ok"] is True
    assert uid not in instance.away_players
    assert control_mode(instance, uid) == "human"
    assert get_control(instance, uid)["temporary"] is False
    assert get_control(instance, uid)["resume_mode"] is None


@pytest.mark.asyncio
async def test_returning_through_the_service_does_not_steal_a_permanent_ai_seat(
    web_api,
) -> None:
    """Case D：GM 永久托管给 AI 的席位，不会被玩家点"回来"抢走。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")
    uid = sorted(instance.players)[0]
    await api.set_player_control(game_key, uid, "ai")
    assert is_temporarily_ai_controlled(instance, uid) is False

    result = await api.set_player_away(game_key, uid, False)

    assert result["ok"] is True
    assert control_mode(instance, uid) == "ai"
    assert uid not in instance.away_players


@pytest.mark.asyncio
async def test_control_change_is_refused_while_a_rewrite_owns_the_authority_gate(
    web_api,
) -> None:
    """历史重写期间不得改变控制权。

    ``control_change_block`` 只看 ``state`` 与 ``_process_lock``，对"正在分阶段
    重写历史"一无所知；因此仅靠它做判定，控制权变更可以落进一次重写中间。正确
    的边界是 ``authoritative_write``——重写期间写者必须被拒绝。

    这里直接置位 ``_rewrite_in_progress`` 来模拟"重写由别的 task 持有"：同一 task
    内 ``authoritative_write`` 是可重入的，用 ``historical_rewrite()`` 包住调用
    反而会走重入豁免，测不到拒绝路径。
    """

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    await api.set_away_control_policy(game_key, "ai_takeover")
    uid = sorted(instance.players)[0]
    before = dict(get_control(instance, uid))

    instance._rewrite_in_progress = True
    try:
        blocked = await api.set_player_control(game_key, uid, "ai")
        away = await api.set_player_away(game_key, uid, True)
    finally:
        instance._rewrite_in_progress = False

    assert blocked["ok"] is False
    assert blocked.get("error_code") == "CONTROL_CHANGE_BUSY"
    assert away["ok"] is False
    assert away.get("error_code") == "CONTROL_CHANGE_BUSY"
    assert get_control(instance, uid) == before
    assert uid not in instance.away_players

    # 重写结束后同样的操作必须恢复正常，证明拒绝来自边界而不是永久失效。
    recovered = await api.set_player_control(game_key, uid, "ai")
    assert recovered["ok"] is True


@pytest.mark.asyncio
async def test_pause_away_does_not_require_a_processing_boundary(web_api) -> None:
    """pause 只改在场状态，不涉及控制权，因此不引入新的拒绝路径。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    uid = sorted(instance.players)[0]
    assert away_control_policy(instance) == "pause"

    async with instance._process_lock:
        result = await api.set_player_away(game_key, uid, True)

    assert result["ok"] is True
    assert uid in instance.away_players
    assert control_mode(instance, uid) == "human"


# ---- 6. 徽章状态可从服务端派生 ------------------------------------------------


@pytest.mark.asyncio
async def test_roster_reports_human_ai_and_unclaimed_seats(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    uids = sorted(instance.players)
    await api.set_player_control(game_key, uids[1], "ai")
    set_control(instance, uids[2], "unclaimed")

    status = instance.multiplayer_status()

    assert [p["user_id"] for p in status["ai_players"]] == [uids[1]]
    assert [p["user_id"] for p in status["unclaimed_players"]] == [uids[2]]
    assert status["ai_count"] == 1
    assert status["unclaimed_count"] == 1
    # ready / waiting 只统计真人席位，托管与未认领不会挡推进。
    assert status["ready_count"] == 0
    assert [p["user_id"] for p in status["waiting_players"]] == [uids[0]]


@pytest.mark.asyncio
async def test_temporary_hosting_is_distinguishable_from_permanent(web_api) -> None:
    """徽章要能区分「AI 临时托管」与「AI 托管」。"""

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    game_key, instance = await _create(api)
    uids = sorted(instance.players)

    await api.set_player_control(game_key, uids[1], "ai")
    assert is_temporarily_ai_controlled(instance, uids[1]) is False

    await api.set_away_control_policy(game_key, "ai_takeover")
    await api.set_player_away(game_key, uids[0], True)
    assert is_temporarily_ai_controlled(instance, uids[0]) is True

    # 两种 AI 席位都在 ai_players 里，但临时标记只在其中一个上。
    status = instance.multiplayer_status()
    assert {p["user_id"] for p in status["ai_players"]} == {uids[0], uids[1]}


# ---- 7. 认领路径在开房之后仍然成立 -------------------------------------------


@pytest.mark.asyncio
async def test_an_unclaimed_creation_card_can_still_be_claimed(web_api) -> None:
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    characters = [dict(c) for c in CHARACTERS]
    characters[2]["control"] = "unclaimed"
    created = await api.create_game(
        "template_world", "模板世界", players=characters,
    )
    instance = api.get_game_instance(created["game_key"])
    uid = sorted(instance.players)[2]
    assert control_mode(instance, uid) == "unclaimed"

    joined = await api.create_player(
        created["game_key"], {"user_id": uid, "name": "随便填"},
    )

    assert joined["ok"] is True
    assert control_mode(instance, uid) == "human"
