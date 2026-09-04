"""GameInstance 状态机测试。"""

import asyncio
from copy import deepcopy

import pytest

from src.engine.character_utils import reset_character_for_restart
from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.engine.health import health_payload, mark_health_event, record_health_event
from src.commands.progression_resolver import ProgressionResolver


def test_versioned_ruleset_state_is_optional_and_round_trips() -> None:
    legacy = GameInstance(game_key=("web", "legacy", "bot"))
    assert "ruleset_runtime" not in legacy.to_dict()

    instance = GameInstance(game_key=("web", "professional", "bot"))
    assert instance.bind_ruleset_runtime({
        "runtime_id": "core:dnd2024",
        "runtime_version": 1,
        "content_version": "srd-5.2.1+r1",
        "state_schema_version": 1,
    })
    instance.event_ledger.append({"batch_id": "test-batch"})

    restored = GameInstance.from_dict(instance.to_dict())

    assert restored.ruleset_runtime == {
        "id": "core:dnd2024",
        "version": 1,
        "content_version": "srd-5.2.1+r1",
        "state_schema_version": 1,
    }
    assert restored.ruleset_state == {"state_schema_version": 1}
    assert restored.event_ledger == [{"batch_id": "test-batch"}]
    assert not restored.bind_ruleset_runtime({
        "runtime_id": "core:dnd2024",
        "runtime_version": 2,
        "content_version": "future",
        "state_schema_version": 2,
    })


def test_persisted_boundary_preserves_opaque_state_and_filters_transient_entries() -> None:
    payload = {
        "game_key": ["web", "typed-boundary", "bot"],
        "state": "active_action",
        "rule_id": "",
        "players": {
            "active": {"character_name": "Active"},
            "away": {"character_name": "Away"},
        },
        "ready_players": ["active"],
        "away_players": ["away"],
        "ruleset_runtime": {"id": "sample:runtime", "extension": {"kept": True}},
        "ruleset_state": {"private_shape": [1, {"kept": True}]},
        "adventure_binding": {"extension": {"kept": True}},
        "event_ledger": [{"payload": {"kept": True}}],
        "pending_payments": [
            {"id": "pending", "status": "pending"},
            {"id": "settled", "status": "accepted"},
        ],
        "table_talk": [
            {"id": "party", "visibility": "party"},
            {"id": "private", "visibility": "private"},
        ],
    }
    original = deepcopy(payload)

    restored = GameInstance.from_dict(payload)

    assert payload == original
    assert restored.rule_id == ""
    assert restored.ready_players == {"active"}
    assert restored.away_players == {"away"}
    assert restored.ruleset_runtime == payload["ruleset_runtime"]
    assert restored.ruleset_state == payload["ruleset_state"]
    assert restored.adventure_binding == payload["adventure_binding"]
    assert restored.event_ledger == payload["event_ledger"]
    assert [item["id"] for item in restored.table_talk] == ["party"]


def test_narrative_perspective_round_trips_and_old_saves_default_to_auto() -> None:
    instance = GameInstance(game_key=("web", "perspective", "bot"))
    instance.set_narrative_perspective("third_person")

    restored = GameInstance.from_dict(instance.to_dict())
    legacy_data = instance.to_dict()
    legacy_data.pop("narrative_perspective")

    assert restored.narrative_perspective == "third_person"
    assert GameInstance.from_dict(legacy_data).narrative_perspective == "auto"
    with pytest.raises(ValueError, match="叙事视角"):
        instance.set_narrative_perspective("角色名")


@pytest.mark.asyncio
async def test_reset_preserves_exact_ruleset_and_adventure_bindings() -> None:
    instance = GameInstance(
        game_key=("web", "professional-restart", "bot"),
        world_id="greymoor",
        rule_id="dnd2024_srd",
    )
    ruleset = {
        "runtime_id": "core:dnd2024",
        "runtime_version": 1,
        "content_version": "srd-5.2.1+r5",
        "state_schema_version": 1,
    }
    adventure = {
        "adventure_id": "core:lanterns_of_greymoor",
        "version": "1.0.0",
        "format": "diceframe:adventure-graph-v1",
        "content_digest": "sha256:test-binding",
        "world_id": "greymoor",
    }
    assert instance.bind_ruleset_runtime(ruleset)
    assert instance.bind_adventure(adventure)
    instance.ruleset_state["version"] = 42
    instance.event_ledger.append({"batch_id": "old-run"})

    await instance.reset()

    assert instance.rule_id == "dnd2024_srd"
    assert instance.ruleset_runtime == {
        "id": "core:dnd2024",
        "version": 1,
        "content_version": "srd-5.2.1+r5",
        "state_schema_version": 1,
    }
    assert instance.adventure_binding == adventure
    assert instance.ruleset_state == {"state_schema_version": 1}
    assert instance.event_ledger == []


@pytest.mark.asyncio
class TestGameInstance:
    async def test_action_is_rejected_while_historical_rewrite_holds_process_barrier(self):
        instance = GameInstance(game_key=("web", "rewrite", "bot"), gm_uid="gm")
        instance.players = {
            "gm": {
                "character_name": "GM",
                "character_sheet": {"deceased": False},
            },
        }
        async with instance._process_lock:
            added = await instance.add_action("gm", "在重写期间偷偷行动")
        assert added is False
        assert instance.action_queue == []
        assert instance.pending_actions == []

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

    async def test_solo_action_replaces_previous(self):
        # 切换行动应替换旧行动，而不是追加堆积（避免触发 3 条上限、旧检定残留）
        inst = GameInstance(game_key=("qq", "123", "bot1"), solo_mode=True)
        inst.state = GameState.ACTIVE_ACTION
        await inst.add_action("user1", "第一步")
        await inst.add_action("user1", "第二步")

        assert [action["text"] for action in inst.action_queue] == ["第二步"]

    async def test_solo_action_replaces_old_pending_dice(self):
        # 回归：solo 反复切换待掷骰行动，应只保留最新一条，且旧检定作废
        inst = GameInstance(game_key=("qq", "123", "bot1"), solo_mode=True)
        inst.state = GameState.ACTIVE_ACTION
        inst.players["user1"] = {"character_name": "冒险者"}
        for i in range(5):
            await inst.add_action(
                "user1",
                f"检查杂物间第{i + 1}次",
                dice_pending=True,
                dice_system="d100",
                check_request={"label": f"侦查检定{i}", "dice_system": "d100"},
            )
        assert len(inst.action_queue) == 1
        assert inst.action_queue[0]["text"] == "检查杂物间第5次"
        assert inst.action_queue[0]["dice_pending"] is True
        assert not inst.has_pending_dice("user2")  # 无其他玩家待掷骰

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

        original = deepcopy(data)
        restored = GameInstance.from_dict(data)

        assert data == original
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

    async def test_to_llm_view_includes_inventory_and_key_items(self):
        inst = GameInstance(game_key=("qq", "123", "bot1"))
        inst.players["u1"] = {
            "character_name": "艾琳",
            "character_sheet": {
                "inventory": [{"name": "火把", "qty": 1}],
                "key_items": [{"name": "旧钥匙", "category": "key_item"}],
                "equipment": [{"name": "铁剑"}],
            },
        }

        view = inst.to_llm_view()

        sheet = view["players"]["u1"]["character_sheet"]
        assert sheet["inventory"] == [{"name": "火把", "qty": 1}]
        assert sheet["key_items"] == [{"name": "旧钥匙", "category": "key_item"}]
        assert sheet["equipment"] == [{"name": "铁剑"}]


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

    async def test_save_writes_chatlog_and_load_merges(self, tmp_path):
        """save 把完整 log 增量写进 chatlog.jsonl，load 拼回完整历史（不丢旧 log）。"""
        import json as _json
        reg = GameRegistry(tmp_path / "saves")
        inst = GameInstance(game_key=("web", "room1", "bot"))
        # 模拟 150 条 log（超过核心态 100 条上限）
        for i in range(150):
            inst.log.append({"round": i + 1, "content": f"log-{i + 1}"})
        await reg.save(inst)
        chatlog = tmp_path / "saves" / "web#room1#bot" / "chatlog.jsonl"
        assert chatlog.exists()
        lines = [l for l in chatlog.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 150  # 完整历史
        # state.json 核心态只留最近 100 条
        state = _json.loads((tmp_path / "saves" / "web#room1#bot" / "state.json").read_text(encoding="utf-8"))
        assert len(state["log"]) == 100

        # 重新 load：内存 log 应恢复完整 150 条
        reg2 = GameRegistry(tmp_path / "saves")
        restored = await reg2.load(("web", "room1", "bot"))
        assert restored is not None
        assert len(restored.log) == 150
        assert restored.log[0]["round"] == 1  # 最早的在
        assert restored.log[-1]["round"] == 150  # 最新的在

    async def test_save_appends_incrementally(self, tmp_path):
        """连续 save 不重复写 chatlog（增量追加）。"""
        reg = GameRegistry(tmp_path / "saves")
        inst = GameInstance(game_key=("web", "room2", "bot"))
        inst.log.append({"round": 1, "content": "a"})
        await reg.save(inst)
        inst.log.append({"round": 2, "content": "b"})
        await reg.save(inst)
        chatlog = tmp_path / "saves" / "web#room2#bot" / "chatlog.jsonl"
        lines = [l for l in chatlog.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 2  # 不重复
        assert "round" in lines[0] and "round" in lines[1]

    async def test_old_save_migrates_to_chatlog(self, tmp_path):
        """老存档（无 chatlog 但有 log）load 时自动迁移到 chatlog。"""
        import json as _json
        import os
        save_dir = tmp_path / "saves"
        key_dir = save_dir / "web#room3#bot"
        key_dir.mkdir(parents=True)
        state = {
            "game_key": ["web", "room3", "bot"],
            "world_id": "w1", "world_name": "W", "state": "paused",
            "players": {}, "npcs": {}, "round_number": 2, "log": [
                {"round": 1, "content": "old1"}, {"round": 2, "content": "old2"},
            ],
            "summary": {}, "key_facts": [],
        }
        (key_dir / "state.json").write_text(_json.dumps(state), encoding="utf-8")
        reg = GameRegistry(save_dir)
        inst = await reg.load(("web", "room3", "bot"))
        assert inst is not None
        assert len(inst.log) == 2
        # 迁移后 chatlog.jsonl 已生成
        chatlog = key_dir / "chatlog.jsonl"
        assert chatlog.exists()
        lines = [l for l in chatlog.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 2

    async def test_rollback_then_save_load_keeps_history_consistent(self, tmp_path):
        """rollback 弹出末尾轮次后 save→load，被回滚的轮次不复活、历史与状态一致。"""
        reg = GameRegistry(tmp_path / "saves")
        inst = GameInstance(game_key=("web", "rb", "bot"))
        for i in range(3):
            inst.log.append({"round": i + 1, "content": f"r{i+1}"})
        await reg.save(inst)
        await inst.rollback_last_round()  # 弹出 r3
        assert len(inst.log) == 2
        await reg.save(inst)
        reg2 = GameRegistry(tmp_path / "saves")
        restored = await reg2.load(("web", "rb", "bot"))
        assert len(restored.log) == 2  # 死条目 r3 不复活
        assert restored.log[-1]["round"] == 2

    async def test_rollback_then_advance_save_load(self, tmp_path):
        """rollback 后推进新轮，load 后死条目丢弃、新轮保留。"""
        reg = GameRegistry(tmp_path / "saves")
        inst = GameInstance(game_key=("web", "rba", "bot"))
        for i in range(3):
            inst.log.append({"round": i + 1, "content": f"r{i+1}"})
        await reg.save(inst)
        await inst.rollback_last_round()  # 弹出 r3
        inst.log.append({"round": 4, "content": "r4"})  # 推进新轮
        await reg.save(inst)
        reg2 = GameRegistry(tmp_path / "saves")
        restored = await reg2.load(("web", "rba", "bot"))
        assert [e["round"] for e in restored.log] == [1, 2, 4]  # r3 死条目丢弃，r4 保留

    async def test_swipe_then_save_load_preserves_swipe(self, tmp_path):
        """swipe 改写末尾轮的 gm_response 后 save→load，swipe 版本保留、旧版本不复活。"""
        reg = GameRegistry(tmp_path / "saves")
        inst = GameInstance(game_key=("web", "sw", "bot"))
        inst.log.append({"round": 1, "gm_response": "原版", "actions": [], "swipes": []})
        await reg.save(inst)
        await inst.finish_judgment_with_swipe("swipe版", 1)
        assert inst.log[-1]["gm_response"] == "swipe版"
        await reg.save(inst)
        reg2 = GameRegistry(tmp_path / "saves")
        restored = await reg2.load(("web", "sw", "bot"))
        assert restored.log[-1]["gm_response"] == "swipe版"  # swipe 保留，旧版不复活

    async def test_swipe_middle_round_then_save_load(self, tmp_path):
        """swipe 改写中间轮（非末尾），load 后 swipe 保留、其他轮不受影响。"""
        reg = GameRegistry(tmp_path / "saves")
        inst = GameInstance(game_key=("web", "swm", "bot"))
        for i in range(3):
            inst.log.append({"round": i + 1, "gm_response": f"原{i+1}", "actions": [], "swipes": []})
        await reg.save(inst)
        await inst.finish_judgment_with_swipe("swipe2", 2)  # swipe 第 2 轮
        await reg.save(inst)
        reg2 = GameRegistry(tmp_path / "saves")
        restored = await reg2.load(("web", "swm", "bot"))
        assert [e["gm_response"] for e in restored.log] == ["原1", "swipe2", "原3"]

    async def test_swipe_window_edge_with_non_advancing_save_keeps_early_history(self, tmp_path):
        """swipe 窗口边界轮 + 非推进 save，load 后窗口前的更早历史不丢。

        场景：150 轮（超出 100 窗口），swipe 第 51 轮（core_log[0]），某非推进操作
        （改房间密码等）触发 save。state.json 里 r51 是 swipe 版，chatlog 里 r51 仍是
        原版，锚点对齐失败——兜底须保留 r1..r50，不能整个用 core_log 替换。
        """
        reg = GameRegistry(tmp_path / "saves")
        inst = GameInstance(game_key=("web", "swe", "bot"))
        for i in range(150):
            inst.log.append({"round": i + 1, "content": f"r{i+1}"})
        await reg.save(inst)
        # 模拟 swipe 第 51 轮（core_log[0]）：改 content，不推进轮次
        inst.log[50]["content"] = "r51-swiped"
        await reg.save(inst)  # 非推进 save
        reg2 = GameRegistry(tmp_path / "saves")
        restored = await reg2.load(("web", "swe", "bot"))
        assert len(restored.log) == 150  # 更早历史不丢
        assert restored.log[0]["round"] == 1  # r1 仍在
        assert restored.log[50]["content"] == "r51-swiped"  # swipe 版保留
        assert restored.log[-1]["round"] == 150

    async def test_import_save_zip_creates_new_game(self, tmp_path):
        """导入存档 zip 生成新 game_key，不覆盖现有对局，且立即可见于内存。"""
        import io
        import json as _json
        import zipfile
        from src.engine.game_instance import GameRegistry
        reg = GameRegistry(tmp_path / "saves")
        # 导出一个 zip（含 state.json + chatlog.jsonl）
        state = {
            "game_key": ["web", "orig", "bot"],
            "instance_schema_version": 2,
            "run_id": "run_exported",
            "memory_namespace": "source-memory",
            "world_id": "w1", "world_name": "Orig", "state": "paused",
            "players": {}, "npcs": {}, "round_number": 5, "log": [],
            "summary": {}, "key_facts": [],
            "economy": {
                "schema_version": 1,
                "run_id": "run_exported",
                "next_sequence": 2,
                "proposals": [{
                    "id": "eco_pending", "run_id": "run_exported",
                    "status": "pending",
                }],
                "transactions": [{
                    "id": "tx_committed", "run_id": "run_exported",
                    "status": "committed",
                }],
                "effect_groups": [{
                    "id": "effect_pending", "run_id": "run_exported",
                    "status": "pending", "effects": {},
                }],
                "external_effects_outbox": [
                    {
                        "id": "memory:pending",
                        "run_id": "run_exported",
                        "kind": "memory_delta",
                        "status": "pending",
                        "payload": {"add": ["待投递记忆"]},
                    },
                    {
                        "id": "memory:delivered",
                        "run_id": "run_exported",
                        "kind": "memory_delta",
                        "status": "delivered",
                    },
                ],
                "outcomes": [{
                    "id": "outcome_declined", "run_id": "run_exported",
                    "status": "declined",
                }],
                "purchase_quotes": [{
                    "id": "quote_open", "run_id": "run_exported",
                    "status": "open", "round": 5,
                    "payer_uid": "p1", "amount": 5, "items": ["通行证"],
                }],
                "idempotency_records": {},
            },
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("state.json", _json.dumps(state))
            zf.writestr("chatlog.jsonl", _json.dumps({"round": 1, "content": "a"}) + "\n")
        result = await reg.import_save_zip(buffer.getvalue())
        assert result["ok"] is True
        new_key = tuple(result["game_key"])  # 返回 list（平台|target|account 三段）
        assert new_key[1] != "orig"  # 自动生成新 game_key
        # 内存立即可见（不必等重启 recover_all）
        assert reg.get(new_key) is not None
        assert reg.get(new_key).round_number == 5
        imported = reg.get(new_key)
        assert imported.run_id != "run_exported"
        assert imported.memory_namespace != "source-memory"
        assert imported.memory_namespace.endswith(imported.run_id)
        assert imported.economy["run_id"] == imported.run_id
        assert all(
            item["run_id"] == imported.run_id
            for key in (
                "proposals", "transactions", "effect_groups",
                "external_effects_outbox", "outcomes",
            )
            for item in imported.economy[key]
        )
        assert "purchase_quotes" not in imported.economy
        assert [
            item["id"] for item in imported.economy["external_effects_outbox"]
        ] == ["memory:pending"]
        # state.json 内 game_key 已改写为新值，避免 register 串到原对局
        saved = _json.loads(reg._save_path(new_key).read_text(encoding="utf-8"))
        assert saved["game_key"] == list(new_key)
        # 不覆盖原存档目录
        assert not (tmp_path / "saves" / "web#orig#bot").exists()

    async def test_import_save_zip_game_key_parseable(self, tmp_path):
        """导入返回的 game_key 经 | join 后能被 _parse_key 正确解析（公开 key 可访问）。"""
        import io
        import json as _json
        import zipfile
        from src.engine.game_instance import GameRegistry
        from src.webui.api import WebAPI
        reg = GameRegistry(tmp_path / "saves")
        state = {
            "game_key": ["web", "orig", "bot"], "world_id": "w1", "world_name": "Orig",
            "state": "paused", "players": {}, "npcs": {}, "round_number": 1,
            "log": [], "summary": {}, "key_facts": [],
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("state.json", _json.dumps(state))
        result = await reg.import_save_zip(buffer.getvalue())
        public_key = "|".join(str(x) for x in result["game_key"])
        assert WebAPI._parse_key(public_key) == tuple(result["game_key"])

    async def test_import_save_zip_materializes_portable_scene_image(self, tmp_path):
        import io
        import json as _json
        import zipfile
        from src.engine.game_instance import GameRegistry

        state = {
            "game_key": ["web", "orig", "bot"],
            "world_id": "w1", "world_name": "Orig", "state": "paused",
            "players": {}, "npcs": {}, "round_number": 1, "log": [],
            "summary": {}, "key_facts": [],
            "scene_image": {"kind": "save_asset", "path": "scene-image.asset"},
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("state.json", _json.dumps(state))
            zf.writestr("scene-image.asset", b"portable-scene")
        imported_payloads = []

        result = await GameRegistry(tmp_path / "saves").import_save_zip(
            buffer.getvalue(),
            scene_image_importer=lambda raw: (
                imported_payloads.append(raw)
                or {"ok": True, "scene_image": {"kind": "upload", "asset_id": "local-scene"}}
            ),
        )

        assert result["ok"] is True
        assert imported_payloads == [b"portable-scene"]
        registry = GameRegistry(tmp_path / "saves")
        restored = await registry.load(tuple(result["game_key"]))
        assert restored.scene_image == {"kind": "upload", "asset_id": "local-scene"}

    @pytest.mark.asyncio
    async def test_import_save_zip_materializes_portable_map_background(self, tmp_path):
        import io
        import json
        import zipfile

        state = {
            "game_key": ["web", "portable-map", "bot"],
            "state": "waiting",
            "world_id": "default_fantasy",
            "map_background": {"kind": "save_asset", "path": "map-background.asset"},
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("state.json", json.dumps(state))
            zf.writestr("map-background.asset", b"portable-map")

        result = await GameRegistry(tmp_path / "saves").import_save_zip(
            buffer.getvalue(),
            map_background_importer=lambda raw: (
                {"ok": True, "map_background": {"kind": "upload", "asset_id": "local-map"}}
                if raw == b"portable-map"
                else {"ok": False, "error": "bad payload"}
            ),
        )

        assert result["ok"] is True
        restored = GameRegistry(tmp_path / "saves")
        instance = await restored.load(tuple(result["game_key"]))
        assert instance is not None
        assert instance.map_background == {"kind": "upload", "asset_id": "local-map"}

    async def test_import_save_zip_rejects_missing_state(self, tmp_path):
        """存档包缺 state.json 报错。"""
        import io
        import zipfile
        from src.engine.game_instance import GameRegistry
        reg = GameRegistry(tmp_path / "saves")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("readme.txt", "hi")
        result = await reg.import_save_zip(buffer.getvalue())
        assert result["ok"] is False

    async def test_import_save_zip_rejects_oversized_unpacked_state(self, tmp_path, monkeypatch):
        import io
        import json as _json
        import zipfile
        import src.engine.game_instance as game_instance
        from src.engine.game_instance import GameRegistry
        monkeypatch.setattr(game_instance, "MAX_SAVE_STATE_BYTES", 32)
        reg = GameRegistry(tmp_path / "saves")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("state.json", _json.dumps({"game_key": ["web", "orig", "bot"], "padding": "x" * 1000}))

        result = await reg.import_save_zip(buffer.getvalue())

        assert result == {"ok": False, "error": "state.json 解压后过大"}

    async def test_import_save_zip_enforces_package_limit_without_http_route(self, tmp_path, monkeypatch):
        import src.engine.game_instance as game_instance
        from src.engine.game_instance import GameRegistry

        monkeypatch.setattr(game_instance, "MAX_SAVE_PACKAGE_BYTES", 4)
        result = await GameRegistry(tmp_path / "saves").import_save_zip(b"12345")

        assert result == {"ok": False, "error": "存档包不能超过 50 MB"}

    async def test_import_save_zip_rejects_duplicate_members(self, tmp_path):
        import io
        import zipfile
        from src.engine.game_instance import GameRegistry
        reg = GameRegistry(tmp_path / "saves")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("state.json", "{}")
            with pytest.warns(UserWarning, match="Duplicate name"):
                zf.writestr("state.json", "{}")

        result = await reg.import_save_zip(buffer.getvalue())

        assert result == {"ok": False, "error": "存档包包含重复文件"}


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


def test_reset_character_for_restart_clears_canonical_dnd_death_state():
    cs = {
        "hp": 0,
        "max_hp": 12,
        "deceased": True,
        "ruleset_character": {
            "resources": {"hp": 0, "max_hp": 12},
            "conditions": {
                "unconscious": {"source": "zero_hp"},
                "dead": {"source": "death_saves"},
                "death_saves": {"successes": 0, "failures": 3},
            },
        },
    }

    reset_character_for_restart(cs)

    assert cs["hp"] == 12
    assert cs["deceased"] is False
    assert cs["ruleset_character"]["resources"]["hp"] == 12
    assert cs["ruleset_character"]["conditions"] == {}


def test_reset_character_for_restart_clears_nested_generic_runtime_state():
    cs = {
        "hp": 0,
        "max_hp": 9,
        "ruleset_character": {
            "resources": {"hp": {"current": 0, "max": 9}},
            "conditions": {"dead": {"source": "zero_hp"}},
            "deceased": True,
        },
    }

    reset_character_for_restart(cs)

    assert cs["ruleset_character"]["resources"]["hp"] == {"current": 9, "max": 9}
    assert cs["ruleset_character"]["conditions"] == {}
    assert cs["ruleset_character"]["deceased"] is False


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
    # P2-F：恢复后标记待决定，供前端提示（定时器不跨重启）
    assert restored[0].pending_luck_after_recovery is True


@pytest.mark.asyncio
async def test_recovery_pauses_game_without_pending_luck(tmp_path):
    """无待幸运决定的局恢复后进入 PAUSED，且不标记待决定。"""
    registry = GameRegistry(tmp_path / "saves")
    inst = GameInstance(("web", "luck_paused", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.round_checks_prepared = True
    inst.last_checks = [{
        "check_id": "check-1",
        "actor_uid": "p1",
        "luck_decision": "spent",
    }]
    registry.register(inst)
    await registry.save(inst)

    restored_registry = GameRegistry(tmp_path / "saves")
    restored = await restored_registry.recover_all()

    assert restored[0].state == GameState.PAUSED
    assert restored[0].pending_luck_after_recovery is False


@pytest.mark.asyncio
async def test_system_decline_luck_times_out_single_check():
    """超时只 decline 触发的那条检定，并正确报告是否清空。"""
    inst = GameInstance(("web", "luck_timeout", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.round_checks_prepared = True
    inst.last_checks = [
        {"check_id": "a", "actor_uid": "p1", "luck_decision": "pending"},
        {"check_id": "b", "actor_uid": "p2", "luck_decision": "pending"},
    ]

    # 超时第一条：还有 b 待决，declined_all 应为 False
    res = await inst.system_decline_luck("a")
    assert res["ok"] is True
    assert res["declined_all"] is False
    assert inst.last_checks[0]["luck_decision"] == "declined"
    assert inst.last_checks[0]["luck_timeout"] is True
    assert inst.last_checks[1]["luck_decision"] == "pending"

    # 超时最后一条：declined_all 应为 True
    res2 = await inst.system_decline_luck("b")
    assert res2["ok"] is True
    assert res2["declined_all"] is True
    assert inst.pending_luck_checks() == []


@pytest.mark.asyncio
async def test_system_decline_luck_skips_already_resolved():
    """玩家已手动决定时，超时回调不重复改判。"""
    inst = GameInstance(("web", "luck_resolved", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.round_checks_prepared = True
    inst.last_checks = [
        {"check_id": "a", "actor_uid": "p1", "luck_decision": "spent"},
    ]
    res = await inst.system_decline_luck("a")
    assert res["ok"] is False
    assert res["code"] == "LUCK_ALREADY_RESOLVED"
    assert inst.last_checks[0]["luck_decision"] == "spent"


@pytest.mark.asyncio
async def test_system_decline_luck_missing_check():
    inst = GameInstance(("web", "luck_missing", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.round_checks_prepared = True
    inst.last_checks = []
    res = await inst.system_decline_luck("ghost")
    assert res["ok"] is False
    assert res["code"] == "CHECK_NOT_FOUND"


@pytest.mark.asyncio
async def test_resolve_luck_decision_cancels_timer():
    """手动决议先于超时到达时，对应定时器被取消。"""
    inst = GameInstance(("web", "luck_cancel", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.gm_uid = "gm"
    inst.round_checks_prepared = True
    inst.players["p1"] = {"character_name": "调查员", "character_sheet": {"luck": 50}}
    inst.last_checks = [{
        "check_id": "a", "actor_uid": "p1", "dice": "d100",
        "verdict": "失败", "roll": 45, "threshold": 40,
        "luck_decision": "pending", "luck_spend_available": True,
    }]
    # 模拟已挂起的超时定时器
    fake_task = asyncio.ensure_future(asyncio.sleep(100))
    inst._luck_timers["a"] = fake_task

    await inst.resolve_luck_decision("a", "p1", spend=True, allow_gm=False)
    await asyncio.sleep(0)  # 让事件循环处理取消

    assert fake_task.cancelled() is True
    assert "a" not in inst._luck_timers
    assert inst.last_checks[0]["luck_decision"] == "spent"


@pytest.mark.asyncio
async def test_luck_timeout_schedules_and_resumes_round():
    """调度器为 pending 检定挂任务；_luck_timeout 触发后 decline 并重新推进回合。"""
    from src.commands.round_processor import RoundProcessor

    inst = GameInstance(("web", "luck_timer", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.round_checks_prepared = True
    inst.luck_timeout_seconds = 1
    inst.last_checks = [{"check_id": "a", "actor_uid": "p1", "luck_decision": "pending"}]

    class FakeRegistry:
        def get(self, key):
            return inst
        async def save(self, instance):
            pass

    processor = RoundProcessor(
        FakeRegistry(), None, None, None, None, None, None, None, None,
        None, None, None, None, 1, 1, 1,
    )
    resumed = []

    async def fake_process_round(instance, **kw):
        resumed.append(instance.game_key)
        return "narration", None
    processor.process_round = fake_process_round  # type: ignore

    # 1) 调度：为 pending 检定挂上未完成的定时器任务
    processor._schedule_luck_timeouts(inst)
    task = inst._luck_timers.get("a")
    assert task is not None and not task.done()
    inst._cancel_luck_timer("a")  # 同步 pop + cancel，模拟手动决议先到达
    assert "a" not in inst._luck_timers
    try:
        await task  # 等取消落地
    except asyncio.CancelledError:
        pass

    # 2) 触发：timeout=0 使 sleep(0) 立即返回，验证 decline + 重新推进
    await processor._luck_timeout(inst.game_key, "a", 0)
    assert inst.last_checks[0]["luck_decision"] == "declined"
    assert inst.last_checks[0]["luck_timeout"] is True
    assert resumed == [inst.game_key]


@pytest.mark.asyncio
async def test_luck_timeout_disabled_when_zero():
    """luck_timeout_seconds=0 时不挂定时器（异步局无限等待）。"""
    from src.commands.round_processor import RoundProcessor

    inst = GameInstance(("web", "luck_disabled", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.round_checks_prepared = True
    inst.luck_timeout_seconds = 0
    inst.last_checks = [{"check_id": "a", "actor_uid": "p1", "luck_decision": "pending"}]

    class FakeRegistry:
        def get(self, key):
            return inst
        async def save(self, instance):
            pass

    processor = RoundProcessor(
        FakeRegistry(), None, None, None, None, None, None, None, None,
        None, None, None, None, 1, 1, 1,
    )
    processor._schedule_luck_timeouts(inst)
    assert inst._luck_timers == {}


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


@pytest.mark.asyncio
async def test_configure_session_luck_timeout_validation():
    """P1-D：configure_session 校验幸运超时范围并落字段。"""
    inst = GameInstance(("web", "luck_cfg", "bot"))
    inst.configure_session(luck_timeout_seconds=120)
    assert inst.luck_timeout_seconds == 120
    inst.configure_session(luck_timeout_seconds=0)
    assert inst.luck_timeout_seconds == 0
    with pytest.raises(ValueError):
        inst.configure_session(luck_timeout_seconds=9999)
    with pytest.raises(ValueError):
        inst.configure_session(luck_timeout_seconds=-1)
    # 不传则不改变已有值
    inst.configure_session()
    assert inst.luck_timeout_seconds == 0


def test_hardcore_blocks_revive():
    """P2-O：硬核难度禁止复活，角色保持死亡。"""
    from src.commands.round_effects import apply_revive_commands
    inst = GameInstance(("web", "revive_hard", "bot"))
    inst.difficulty = "硬核"
    inst.players["p1"] = {"character_name": "勇者", "character_sheet": {"hp": 0, "deceased": True, "max_hp": 50}}
    apply_revive_commands(inst, {"revive_commands": [{"uid": "p1", "method": "法术"}]})
    assert inst.players["p1"]["character_sheet"]["deceased"] is True


def test_normal_allows_revive():
    """P2-O：标准难度可正常复活。"""
    from src.commands.round_effects import apply_revive_commands
    inst = GameInstance(("web", "revive_ok", "bot"))
    inst.difficulty = "标准"
    inst.players["p1"] = {"character_name": "勇者", "character_sheet": {"hp": 0, "deceased": True, "max_hp": 50}}
    apply_revive_commands(inst, {"revive_commands": [{"uid": "p1", "method": "法术"}]})
    cs = inst.players["p1"]["character_sheet"]
    assert cs["deceased"] is False
    assert cs["hp"] > 0


def test_ja_language_roundtrip():
    """P3-A Phase4：language=ja 存档 round-trip 不丢（to_dict→from_dict）。"""
    inst = GameInstance(("web", "ja_rt", "bot"))
    inst.language = "ja"
    data = inst.to_dict()
    restored = GameInstance.from_dict(data)
    assert restored.language == "ja"
