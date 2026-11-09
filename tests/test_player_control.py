"""Player control contract (AI teammate plan, PR1).

The contract under test is deliberately small: every seat names *who plays it*
(``human`` / ``ai`` / ``unclaimed``) while the character itself stays exactly
where it was.  What these tests lock down:

* a new game and every loaded save expose an explicit control record;
* schema 13 -> 14 migration never guesses an AI controller: old seats become
  ``human``, which is the pre-contract behaviour;
* ``set_control`` is the only write entry point and fails closed on unknown
  seats / modes / temporary hosting without a resume target, while reads of a
  corrupted record degrade to the conservative default;
* persistence round-trips every mode, and a removed seat leaves no orphan
  control behind;
* round rollback and swipe revert *story* state but never re-assign a seat;
* restart / reset semantics: a temporary AI takeover is never permanent.
"""

from __future__ import annotations

from legacy_save_fixtures import legacy_table_payload

import asyncio

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.engine.player_control import (
    CONTROL_KEY,
    CONTROL_MODES,
    DEFAULT_CONTROL_MODE,
    PlayerControlError,
    ai_controlled_players,
    control_mode,
    default_control,
    ensure_controls,
    get_control,
    human_controlled_players,
    is_ai_controlled,
    is_human_controlled,
    is_unclaimed,
    normalize_control,
    release_temporary_controls,
    set_control,
    unclaimed_players,
)
from src.engine.world_state import apply_world_ops, fact_value, fresh_world_state
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    migrate_game_state_payload,
    normalize_game_state_payload,
)


def make_instance(*, uids: tuple[str, ...] = ("p1", "p2")) -> GameInstance:
    instance = GameInstance(game_key=("web", "player-control", "bot"), rule_id="test")
    instance.state = GameState.ACTIVE_ACTION
    for uid in uids:
        # 走真实席位写入路径：控制记录由聚合保证存在，不靠测试手工补。
        instance.put_player(uid, {
            "user_id": uid,
            "character_name": f"角色{uid}",
            "character_sheet": {"hp": 10, "max_hp": 10},
        })
    return instance


def legacy_payload(*, version: int = 13, **player_extra) -> dict:
    """A persisted payload written before the control contract existed."""

    player = {
        "user_id": "p1",
        "character_name": "阿岚",
        "character_sheet": {"hp": 10, "max_hp": 10},
    }
    player.update(player_extra)
    return {
        "instance_schema_version": version,
        "game_key": ["web", "player-control", "bot"],
        "rule_id": "test",
        "state": "active_action",
        "players": {"p1": player},
    }


# ---- 默认值 -----------------------------------------------------------------


def test_new_game_declares_the_current_schema() -> None:
    instance = make_instance()

    assert CURRENT_INSTANCE_SCHEMA_VERSION >= 14
    assert instance.to_dict()["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert instance.instance_schema_version == CURRENT_INSTANCE_SCHEMA_VERSION


def test_every_seat_has_an_explicit_control_record_after_construction() -> None:
    instance = make_instance()

    for uid in ("p1", "p2"):
        assert instance.players[uid][CONTROL_KEY] == default_control()
        assert control_mode(instance, uid) == DEFAULT_CONTROL_MODE == "human"
        assert is_human_controlled(instance, uid) is True
        assert is_ai_controlled(instance, uid) is False
        assert is_unclaimed(instance, uid) is False


def test_unknown_seat_reads_as_the_conservative_default() -> None:
    instance = make_instance()

    # 读取不猜测 AI：未知席位只是"没有控制证据"，不是非法状态。
    assert get_control(instance, "ghost") == default_control()
    assert control_mode(instance, "ghost") == "human"


# ---- 控制模式词汇表 ---------------------------------------------------------


@pytest.mark.parametrize("mode", ["gm", "remote_bot", "script", "hybrid", "spectator", "", None, 7])
def test_unsupported_modes_are_rejected_on_write(mode: object) -> None:
    instance = make_instance()

    with pytest.raises(PlayerControlError):
        set_control(instance, "p1", mode)  # type: ignore[arg-type]

    # 拒绝后不写入任何东西。
    assert get_control(instance, "p1") == default_control()


@pytest.mark.parametrize("mode", ["gm", "remote_bot", None, 42, {"mode": "ai"}])
def test_corrupt_persisted_modes_degrade_to_human(mode: object) -> None:
    # 读路径宁可保守：绝不把损坏数据猜成一个 AI 控制者。
    record = normalize_control({"mode": mode, "revision": 3})

    assert record["mode"] == "human"
    assert record["revision"] == 3
    assert record == {**default_control(), "revision": 3}


def test_control_vocabulary_is_closed() -> None:
    assert CONTROL_MODES == ("human", "ai", "unclaimed")


def test_unknown_seat_cannot_be_assigned() -> None:
    instance = make_instance()

    with pytest.raises(PlayerControlError):
        set_control(instance, "ghost", "ai")

    assert "ghost" not in instance.players


# ---- revision ---------------------------------------------------------------


def test_revision_tracks_real_changes_only() -> None:
    instance = make_instance()

    assert get_control(instance, "p1")["revision"] == 0

    first = set_control(instance, "p1", "ai")

    assert first["revision"] == 1
    assert first == {"mode": "ai", "revision": 1, "temporary": False, "resume_mode": None}

    # 重复同一个请求不是一次控制权交接，revision 不动。
    assert set_control(instance, "p1", "ai")["revision"] == 1

    assert set_control(instance, "p1", "human")["revision"] == 2
    assert set_control(instance, "p1", "unclaimed")["revision"] == 3


@pytest.mark.parametrize("revision", [-1, 1.5, True, "3", None])
def test_corrupt_revisions_degrade_to_zero(revision: object) -> None:
    assert normalize_control({"mode": "ai", "revision": revision})["revision"] == 0


# ---- 临时托管 ---------------------------------------------------------------


def test_temporary_takeover_requires_a_resume_target() -> None:
    instance = make_instance()

    with pytest.raises(PlayerControlError):
        set_control(instance, "p1", "ai", temporary=True)
    with pytest.raises(PlayerControlError):
        set_control(instance, "p1", "ai", temporary=True, resume_mode="ai")

    assert get_control(instance, "p1") == default_control()


@pytest.mark.parametrize("resume_mode", [None, "ai", "script", "", "human_gm", 7])
def test_a_temporary_ai_record_without_a_valid_resume_target_degrades_to_human(
    resume_mode: object,
) -> None:
    """``temporary=true`` 却没有可用的恢复目标是自相矛盾的记录。

    这种记录既不能猜一个恢复目标继续托管，也不能留在 ``ai`` 上假装它可恢复：
    读出来必须安全降级为真人，并保留合法的 revision（降级不改变"这条记录被改过
    几次"这个事实）。
    """

    record = normalize_control({
        "mode": "ai",
        "revision": 4,
        "temporary": True,
        "resume_mode": resume_mode,
    })

    assert record == {
        "mode": "human", "revision": 4, "temporary": False, "resume_mode": None,
    }


def test_no_normalization_ever_yields_a_temporary_ai_without_a_resume_target() -> None:
    """穷举 ``temporary`` × ``resume_mode`` 的组合，非法状态不得漏出。"""

    for resume_mode in [None, "ai", "script", "", "human", "unclaimed", 7, True]:
        for temporary in [True, False]:
            record = normalize_control({
                "mode": "ai",
                "revision": 4,
                "temporary": temporary,
                "resume_mode": resume_mode,
            })
            illegal = (
                record["mode"] == "ai"
                and record["temporary"] is True
                and record["resume_mode"] is None
            )
            assert not illegal, (temporary, resume_mode, record)


def test_a_legal_temporary_record_and_a_permanent_ai_record_are_untouched() -> None:
    """安全降级不得误伤合法记录：临时托管仍可恢复，永久 AI 仍是永久 AI。"""

    temporary = normalize_control({
        "mode": "ai", "revision": 4, "temporary": True, "resume_mode": "human",
    })
    assert temporary == {
        "mode": "ai", "revision": 4, "temporary": True, "resume_mode": "human",
    }

    permanent = normalize_control({"mode": "ai", "revision": 4})
    assert permanent == {
        "mode": "ai", "revision": 4, "temporary": False, "resume_mode": None,
    }
    # 幂等：归一化过的记录再归一化不变。
    assert normalize_control(temporary) == temporary
    assert normalize_control(permanent) == permanent


def test_a_degraded_temporary_record_is_still_releasable() -> None:
    """降级之后的席位必须能被正常当作真人席位使用（不是"读得出来但用不了"）。"""

    instance = make_instance()
    instance.players["p1"]["control"] = {
        "mode": "ai", "revision": 4, "temporary": True, "resume_mode": None,
    }

    assert control_mode(instance, "p1") == "human"
    assert is_human_controlled(instance, "p1") is True
    assert ai_controlled_players(instance) == []
    assert human_controlled_players(instance) == ["p1", "p2"]


def test_temporary_takeover_releases_back_to_its_resume_mode() -> None:
    instance = make_instance()
    set_control(instance, "p1", "ai", temporary=True, resume_mode="human")
    set_control(instance, "p2", "ai")

    assert ai_controlled_players(instance) == ["p1", "p2"]

    released = release_temporary_controls(instance)

    # 只有「暂离托管」的席位被放回真人；永久托管的席位不受影响。
    assert released == ["p1"]
    assert control_mode(instance, "p1") == "human"
    assert get_control(instance, "p1")["temporary"] is False
    assert get_control(instance, "p1")["resume_mode"] is None
    assert control_mode(instance, "p2") == "ai"
    assert ai_controlled_players(instance) == ["p2"]


def test_temporary_fields_are_dropped_when_the_seat_is_not_ai_hosted() -> None:
    # temporary / resume_mode 只在 AI 托管时有意义，否则读出来是自相矛盾的状态。
    assert normalize_control({
        "mode": "human", "temporary": True, "resume_mode": "human",
    }) == {**default_control(), "mode": "human"}

    instance = make_instance()
    set_control(instance, "p1", "ai", temporary=True, resume_mode="unclaimed")
    assert control_mode(instance, "p1") == "ai"

    # 直接改回真人：临时字段必须一起清掉。
    set_control(instance, "p1", "human")
    assert get_control(instance, "p1") == {
        "mode": "human", "revision": 2, "temporary": False, "resume_mode": None,
    }


# ---- 席位筛选 ---------------------------------------------------------------


def test_mode_filters_list_the_matching_seats() -> None:
    instance = make_instance(uids=("p1", "p2", "p3"))
    set_control(instance, "p1", "ai")
    set_control(instance, "p2", "unclaimed")

    assert ai_controlled_players(instance) == ["p1"]
    assert unclaimed_players(instance) == ["p2"]
    assert human_controlled_players(instance) == ["p3"]
    assert is_unclaimed(instance, "p2") is True


def test_ensure_controls_repairs_hand_edited_records_idempotently() -> None:
    instance = make_instance()
    instance.players["p1"].pop(CONTROL_KEY)
    instance.players["p2"][CONTROL_KEY] = {"mode": "script", "revision": -4}

    assert ensure_controls(instance) == ["p1", "p2"]
    assert get_control(instance, "p1") == default_control()
    assert get_control(instance, "p2") == default_control()

    # 幂等：已经规范的记录不会被再次改写。
    assert ensure_controls(instance) == []

    # 合法的 AI 记录不会被"修复"成 human。
    set_control(instance, "p1", "ai")
    assert ensure_controls(instance) == []
    assert control_mode(instance, "p1") == "ai"


# ---- 迁移 13 -> 14 ----------------------------------------------------------


def test_v13_save_gains_human_control_on_every_seat() -> None:
    migrated = migrate_game_state_payload(legacy_payload(version=13))

    # v13 存档会一路链到当前 schema；这里只断言它落在当前版本上，
    # 而不是把某一次 bump 的编号写死。
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["players"]["p1"][CONTROL_KEY] == default_control()
    assert migrated["players"]["p1"][CONTROL_KEY]["mode"] == "human"


def test_legacy_v1_save_also_lands_on_human_control() -> None:
    payload = legacy_payload(version=1, gold=5)
    payload["started_at"] = "2024-01-01T00:00:00+00:00"

    migrated = migrate_game_state_payload(payload)

    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert migrated["players"]["p1"][CONTROL_KEY]["mode"] == "human"


def test_migration_is_idempotent_and_never_overwrites_a_valid_record() -> None:
    instance = make_instance()
    set_control(instance, "p1", "ai")
    set_control(instance, "p2", "unclaimed")
    payload = instance.to_dict()
    payload = legacy_table_payload(payload)
    payload["instance_schema_version"] = 13

    migrated = migrate_game_state_payload(payload)
    again = migrate_game_state_payload(migrated)

    assert again["players"]["p1"][CONTROL_KEY]["mode"] == "ai"
    assert again["players"]["p2"][CONTROL_KEY]["mode"] == "unclaimed"
    assert again == migrated


def test_migration_does_not_guess_ai_for_seats_with_corrupt_control() -> None:
    payload = legacy_payload(version=13)
    payload["players"]["p1"][CONTROL_KEY] = {"mode": "gm", "temporary": True}

    migrated = migrate_game_state_payload(payload)

    assert migrated["players"]["p1"][CONTROL_KEY] == default_control()


# ---- save / load ------------------------------------------------------------


@pytest.mark.parametrize("mode", ["human", "ai", "unclaimed"])
def test_save_load_roundtrip_preserves_control(mode: str) -> None:
    instance = make_instance()
    set_control(instance, "p1", mode)
    set_control(instance, "p2", "ai", temporary=True, resume_mode="human")

    recovered = GameInstance.from_dict(instance.to_dict())

    assert control_mode(recovered, "p1") == mode
    assert get_control(recovered, "p1") == get_control(instance, "p1")
    assert get_control(recovered, "p2") == {
        "mode": "ai", "revision": 1, "temporary": True, "resume_mode": "human",
    }
    assert recovered.to_dict()["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION


def test_loading_a_save_without_control_never_invents_an_ai_seat() -> None:
    instance = GameInstance.from_dict({
        "game_key": ["web", "player-control", "bot"],
        "state": "active_action",
        "instance_schema_version": 13,
        "players": {
            "p1": {"user_id": "p1", "character_name": "阿岚", "character_sheet": {"hp": 10}},
        },
    })

    assert control_mode(instance, "p1") == "human"
    assert ai_controlled_players(instance) == []


def test_removing_a_seat_leaves_no_orphan_control() -> None:
    instance = make_instance()
    set_control(instance, "p2", "ai")

    del instance.players["p2"]

    # 控制记录与席位同生共死：没有任何地方还记着一个不存在的 AI 角色。
    assert "p2" not in instance.players
    assert ai_controlled_players(instance) == []
    assert get_control(instance, "p2") == default_control()
    assert all(uid in instance.players for uid in (
        *human_controlled_players(instance),
        *ai_controlled_players(instance),
        *unclaimed_players(instance),
    ))


def test_ghost_player_cleanup_removes_its_control_too() -> None:
    """加载时清理幽灵玩家，不应留下指向已删除席位的控制记录。"""

    payload = {
        "instance_schema_version": 14,
        "game_key": ["web", "player-control", "bot"],
        "rule_id": "test",
        "state": "active_action",
        "players": {
            "p1": {
                "user_id": "p1", "character_name": "阿岚",
                "character_sheet": {"hp": 10},
                "control": {"mode": "ai", "revision": 4},
            },
            # 空席位：没有角色、没有任何行动痕迹（有角色的席位不会被当成幽灵）。
            "ghost": {
                "user_id": "ghost",
                "control": {"mode": "ai", "revision": 1},
            },
        },
        "log": [{"actions": [{"user_id": "p1", "text": "行动"}]}],
    }

    migrated = normalize_game_state_payload(payload)

    assert sorted(migrated["players"]) == ["p1"]
    assert migrated["players"]["p1"][CONTROL_KEY]["mode"] == "ai"
    assert "ghost" not in migrated["players"]


# ---- 回合回滚 / 重开 --------------------------------------------------------


@pytest.mark.asyncio
async def test_round_rollback_reverts_story_state_but_not_control() -> None:
    instance = make_instance()
    instance.round_number = 1
    assert instance._do_advance_locked() is True

    # 本轮剧情写入了世界事实，同时真人在本轮中途把席位交给 AI 临时托管。
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "door:cellar.locked", "value": False},
    ])
    set_control(instance, "p1", "ai", temporary=True, resume_mode="human")
    handed_over = get_control(instance, "p1")
    await instance.finish_judgment("本轮叙事", state_changes=[])

    assert await instance.rollback_last_round() == 1

    # 剧情回滚：本轮写入的世界事实被撤销。
    assert fact_value(instance.world_state, "door:cellar.locked") is None
    # 控制权不回滚：不能因为剧情回滚又让米拉变回真人。
    assert get_control(instance, "p1") == handed_over
    assert control_mode(instance, "p1") == "ai"
    assert get_control(instance, "p1")["temporary"] is True


@pytest.mark.asyncio
async def test_abort_round_processing_keeps_control() -> None:
    instance = make_instance()
    instance.round_number = 1
    assert instance._do_advance_locked() is True
    set_control(instance, "p1", "ai")
    instance.state = GameState.ACTIVE_JUDGMENT

    assert await instance.abort_round_processing() is True

    assert control_mode(instance, "p1") == "ai"


def test_reset_drops_the_roster_and_its_control_together() -> None:
    instance = make_instance()
    set_control(instance, "p1", "ai", temporary=True, resume_mode="human")

    asyncio.run(instance.reset())

    # reset 重建名册：既没有残留席位，也没有残留的 AI 托管记录。
    assert instance.players == {}
    assert ai_controlled_players(instance) == []
    assert human_controlled_players(instance) == []


def test_reset_starts_from_an_empty_world_too() -> None:
    instance = make_instance()
    set_control(instance, "p1", "ai")

    asyncio.run(instance.reset())

    assert instance.world_state == fresh_world_state()
