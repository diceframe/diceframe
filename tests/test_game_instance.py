"""GameInstance 状态机测试。"""

import asyncio

import pytest

from src.engine.character_utils import reset_character_for_restart
from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.health import health_payload, mark_health_event, record_health_event
from src.commands.progression_resolver import ProgressionResolver


@pytest.mark.asyncio
class TestGameInstance:
    async def test_initial_state(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        assert inst.state == GameState.CREATED
        assert inst.round_number == 0
        assert len(inst.players) == 0

    async def test_activate_and_start_round(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        await inst.activate()
        assert inst.state == GameState.ACTIVE_ACTION
        assert inst.started_at != ""

    async def test_start_round(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.state = GameState.ACTIVE_ACTION
        await inst.start_round()
        assert inst.round_number == 1

    async def test_add_action(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.state = GameState.ACTIVE_ACTION
        await inst.start_round()
        ok = await inst.add_action("user1", "我踹开门")
        assert ok
        assert len(inst.action_queue) == 1
        assert "user1" in inst.ready_players

    async def test_add_action_stores_structured_fields(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.state = GameState.ACTIVE_ACTION
        await inst.start_round()
        await inst.add_action("user1", "我攻击哥布林",
                              selected_attribute="str", selected_skill="剑术",
                              target_text="哥布林")
        action = inst.action_queue[0]
        assert action["text"] == "我攻击哥布林"
        assert action["selected_attribute"] == "str"
        assert action["selected_skill"] == "剑术"
        assert action["target_text"] == "哥布林"

    async def test_multiplayer_action_revision_replaces_previous_action(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"), solo_mode=False)
        inst.state = GameState.ACTIVE_ACTION
        await inst.add_action("user1", "先观察门口\n(系统掷骰: d20=17)")
        await inst.add_action("user1", "改为检查窗户\n(系统掷骰: d20=2)")

        assert len(inst.action_queue) == 1
        assert inst.action_queue[0]["revision_count"] == 2
        assert inst.action_queue[0]["text"] == "改为检查窗户\n(系统掷骰: d20=17)"

    async def test_pending_dice_blocks_advance_until_roll_is_applied(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"), solo_mode=False)
        inst.players["user1"] = {"character_name": "艾琳", "character_sheet": {"deceased": False}}
        inst.state = GameState.ACTIVE_ACTION

        await inst.add_action("user1", "我攻击守卫", dice_pending=True, dice_system="d20")

        assert inst.has_pending_dice("user1") is True
        assert inst.should_advance() is False
        assert inst.multiplayer_status()["submitted_actions"][0]["dice_pending"] is True

        ok = await inst.apply_action_roll("user1", "d20", 16, source="player")

        assert ok is True
        assert inst.has_pending_dice("user1") is False
        assert inst.action_queue[0]["revision_count"] == 1
        assert inst.action_queue[0]["dice_roll_source"] == "player"
        assert inst.action_queue[0]["text"] == "我攻击守卫\n(系统掷骰: d20=16)"
        assert inst.should_advance() is True

    async def test_solo_actions_remain_separate(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"), solo_mode=True)
        inst.state = GameState.ACTIVE_ACTION
        await inst.add_action("user1", "第一步")
        await inst.add_action("user1", "第二步")

        assert [action["text"] for action in inst.action_queue] == ["第一步", "第二步"]

    async def test_action_blocked_in_judgment(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.state = GameState.ACTIVE_JUDGMENT
        ok = await inst.add_action("user1", "我踹开门")
        assert not ok
        assert len(inst.pending_actions) == 1

    async def test_advance_round(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.players["user1"] = {"character_sheet": {"deceased": False}}
        inst.state = GameState.ACTIVE_ACTION
        await inst.start_round()
        ok = await inst.advance_round()
        assert ok
        assert inst.state == GameState.ACTIVE_JUDGMENT

    async def test_finish_judgment(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.state = GameState.ACTIVE_JUDGMENT
        inst.round_number = 1
        await inst.finish_judgment("门被踹开了")
        assert inst.state == GameState.ACTIVE_ACTION
        assert inst.round_number == 2
        assert inst.total_llm_calls == 1

    async def test_serialization_roundtrip(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.players["u1"] = {"character_name": "剑士"}
        inst.round_number = 5
        inst.quick_actions = ["调查脚印", "询问守卫"]
        data = inst.to_dict()
        restored = GameInstance.from_dict(data)
        assert restored.game_key == inst.game_key
        assert restored.round_number == 5
        assert restored.players["u1"]["character_name"] == "剑士"
        assert restored.quick_actions == ["调查脚印", "询问守卫"]

    async def test_from_dict_prunes_unreferenced_ghost_players(self):
        data = {
            "game_key": ["web", "jp_isekai", "bot"],
            "state": "active_action",
            "players": {
                "web_user": {"character_name": "艾琳", "character_sheet": {"deceased": False}},
                "ghost_user": {"character_name": "幽灵玩家", "character_sheet": {"deceased": False}},
            },
            "ready_players": ["web_user", "ghost_user"],
            "action_queue": [{"user_id": "ghost_user", "text": "不该保留"}],
            "pending_actions": [{"user_id": "ghost_user", "text": "也不该保留"}],
            "log": [{
                "round": 39,
                "actions": [{"user_id": "web_user", "text": "继续训练"}],
                "pre_state_snapshot": {"web_user": {"hp": 46}},
            }],
        }

        restored = GameInstance.from_dict(data)

        assert set(restored.players) == {"web_user"}
        assert restored.ready_players == {"web_user"}
        assert restored.action_queue == []
        assert restored.pending_actions == []

    async def test_from_dict_keeps_waiting_players_without_log(self):
        data = {
            "game_key": ["web", "new_room", "bot"],
            "state": "waiting",
            "players": {
                "host": {"character_name": "房主", "character_sheet": {"deceased": False}},
                "guest": {"character_name": "客人", "character_sheet": {"deceased": False}},
            },
            "log": [],
        }

        restored = GameInstance.from_dict(data)

        assert set(restored.players) == {"host", "guest"}

    async def test_alive_players(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.players["u1"] = {"character_sheet": {"deceased": False}}
        inst.players["u2"] = {"character_sheet": {"deceased": True}}
        assert inst.alive_players == {"u1"}

    async def test_should_advance_all_ready(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.players["u1"] = {"character_sheet": {"deceased": False}}
        inst.state = GameState.ACTIVE_ACTION
        inst.ready_players = {"u1"}
        assert inst.should_advance()

    async def test_gm_character_waits_for_action_in_multiplayer(self):
        inst = GameInstance(game_key=("web", "room", "bot"), gm_uid="gm")
        inst.players["gm"] = {"character_name": "无名", "character_sheet": {"deceased": False}}
        inst.players["p1"] = {"character_name": "吴川", "character_sheet": {"deceased": False}}
        inst.state = GameState.ACTIVE_ACTION
        inst.ready_players = {"p1"}
        inst.action_queue = [{"user_id": "p1", "text": "我观察四周"}]

        status = inst.multiplayer_status()

        assert inst.should_advance() is False
        assert status["ready_count"] == 1
        assert status["player_count"] == 2
        assert status["waiting_players"] == [{"user_id": "gm", "character_name": "无名"}]

    async def test_multiplayer_status_lists_ready_and_waiting_players(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.players["u1"] = {"character_name": "艾琳", "character_sheet": {"deceased": False}}
        inst.players["u2"] = {"character_name": "洛恩", "character_sheet": {"deceased": False}}
        inst.state = GameState.ACTIVE_ACTION
        inst.ready_players = {"u1"}
        inst.action_queue = [{"user_id": "u1", "text": "观察"}]

        status = inst.multiplayer_status()

        assert status["ready_count"] == 1
        assert status["alive_count"] == 2
        assert status["ready_players"] == [{"user_id": "u1", "character_name": "艾琳"}]
        assert status["waiting_players"] == [{"user_id": "u2", "character_name": "洛恩"}]
        assert status["can_advance"] is True
        assert status["submitted_actions"] == [{
            "user_id": "u1",
            "character_name": "艾琳",
            "text": "观察",
            "revision_count": 1,
            "dice_pending": False,
            "dice_system": "",
            "dice_roll_source": "",
        }]

    async def test_away_player_does_not_block_multiplayer_round(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.players["u1"] = {"character_name": "艾琳", "character_sheet": {"deceased": False}}
        inst.players["u2"] = {"character_name": "洛恩", "character_sheet": {"deceased": False}}
        inst.state = GameState.ACTIVE_ACTION
        inst.ready_players = {"u1"}
        inst.away_players = {"u2"}
        inst.action_queue = [{"user_id": "u1", "text": "继续追踪"}]

        status = inst.multiplayer_status()

        assert inst.should_advance() is True
        assert status["alive_count"] == 2
        assert status["active_count"] == 1
        assert status["away_players"] == [{"user_id": "u2", "character_name": "洛恩"}]
        assert status["waiting_players"] == []

    async def test_away_player_is_visible_to_llm_as_following_not_deciding(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.players["u1"] = {"character_name": "艾琳", "character_sheet": {"deceased": False}}
        inst.players["u2"] = {"character_name": "洛恩", "character_sheet": {"deceased": False}}
        inst.away_players = {"u2"}

        view = inst.to_llm_view()

        assert view["players"]["u2"]["attendance"] == "away"
        assert view["away_players"] == ["洛恩"]
        assert "不主动做重大决定" in view["attendance_note"]


@pytest.mark.asyncio
class TestGameRegistry:
    async def test_make_key_and_get(self):
        from src.engine.game_instance import GameRegistry
        from pathlib import Path
        reg = GameRegistry(Path("/tmp/test_trpg"))
        key = reg.make_game_key("qq", "123", "bot1")
        assert reg.get(key) is None
        inst = reg.get_or_create(key)
        assert reg.get(key) is inst

    async def test_list_active(self):
        from src.engine.game_instance import GameRegistry
        from pathlib import Path
        reg = GameRegistry(Path("/tmp/test_trpg"))
        k1 = reg.make_game_key("qq", "111", "bot1")
        k2 = reg.make_game_key("qq", "222", "bot1")
        reg.get_or_create(k1).state = GameState.ACTIVE_ACTION
        reg.get_or_create(k2).state = GameState.ENDED
        active = reg.list_active()
        assert len(active) == 1

    async def test_save_path_rejects_path_traversal(self, tmp_path):
        from src.engine.game_instance import GameRegistry
        reg = GameRegistry(tmp_path / "saves")

        with pytest.raises(ValueError):
            reg._save_path(("web", "..\\..\\..\\..\\outside", "bot"))
        with pytest.raises(ValueError):
            reg._save_path(("web", "../../../../outside", "bot"))


def test_webapi_parse_key_rejects_path_traversal():
    from src.webui.api import WebAPI

    assert WebAPI._parse_key("web|room|bot") == ("web", "room", "bot")
    assert WebAPI._parse_key("web|..\\..\\outside|bot") == ("__invalid_game_key__", "", "")


def test_health_event_roundtrip_and_marking():
    inst = GameInstance(game_key=("web", "health", "bot"))
    event = record_health_event(
        inst,
        component="parser",
        code="TAG_PARSE_STREAK",
        severity="warning",
        title="Parser fallback",
    )

    restored = GameInstance.from_dict(inst.to_dict())
    payload = health_payload(restored)

    assert payload["ok"] is True
    assert payload["events"][0]["id"] == event["id"]
    assert payload["status"]["parser"] == "warning"
    assert mark_health_event(restored, event["id"], resolved=True) is True
    assert health_payload(restored)["events"] == []
    assert health_payload(restored, include_resolved=True)["events"][0]["resolved"] is True


def test_health_events_trim_to_limit():
    inst = GameInstance(game_key=("web", "health_trim", "bot"))
    for idx in range(105):
        record_health_event(inst, "save", f"E{idx}", "info", f"event {idx}")

    assert len(inst.health_events) == 100
    assert inst.health_events[0]["code"] == "E5"
    assert inst.health_events[-1]["code"] == "E104"


def test_reset_character_for_restart_preserves_zero_gold():
    cs = {
        "hp": 0,
        "max_hp": 42,
        "gold": 0,
        "deceased": True,
        "death_round": 7,
        "status": "昏迷",
    }

    reset_character_for_restart(cs)

    assert cs["hp"] == 42
    assert cs["gold"] == 0
    assert cs["deceased"] is False
    assert "death_round" not in cs
    assert "status" not in cs


def test_level_up_syncs_legacy_hp_and_resource_hp(tmp_path):
    inst = GameInstance(game_key=("web", "hp_sync", "bot"))
    inst.players["u1"] = {
        "character_name": "艾琳",
        "character_sheet": {
            "level": 1,
            "xp": 100,
            "hp": 41,
            "max_hp": 41,
            "resources": {"hp": {"current": 41, "max": 41}},
        },
    }
    resolver = ProgressionResolver(tmp_path / "rules", tmp_path / "worlds")

    messages = resolver.try_level_up(inst, "u1")

    cs = inst.players["u1"]["character_sheet"]
    assert messages
    assert cs["hp"] == 51
    assert cs["max_hp"] == 51
    assert cs["resources"]["hp"]["current"] == 51
    assert cs["resources"]["hp"]["max"] == 51


@pytest.mark.asyncio
async def test_recovery_keeps_pending_luck_decision_actionable(tmp_path):
    registry = GameRegistry(tmp_path / "saves")
    inst = GameInstance(("web", "luck", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.round_checks_prepared = True
    inst.last_checks = [{
        "check_id": "check-1",
        "actor_uid": "p1",
        "luck_decision": "pending",
    }]
    registry.register(inst)
    await registry.save(inst)

    restored_registry = GameRegistry(tmp_path / "saves")
    restored = await restored_registry.recover_all()

    assert restored[0].state == GameState.ACTIVE_JUDGMENT
    assert restored[0].pending_luck_checks()[0]["check_id"] == "check-1"


@pytest.mark.asyncio
async def test_finished_round_keeps_an_independent_start_snapshot_for_rollback():
    inst = GameInstance(("web", "luck_snapshot", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.players["p1"] = {
        "character_name": "调查员",
        "character_sheet": {"luck": 28},
    }
    inst.round_start_snapshot = {"p1": {"luck": 30}}

    await inst.finish_judgment("本轮结束")

    assert inst.round_start_snapshot == {}
    assert inst.log[-1]["round_start_snapshot"]["p1"]["luck"] == 30
