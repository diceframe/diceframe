from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.bots.qq.adapter import QQTRPGAdapter
from src.bots.qq.card_renderer import BRAND_FOOTER
from src.bots.qq.main import _watch_parent_process
from src.bots.qq.store import QQSessionStore


class FakeAPI:
    def __init__(self, *, consume_bind_token: bool = False):
        self.actions = []
        self.binds = []
        self.advances = []
        self.away_updates = []
        self.character_generations = []
        self.payment_resolutions = []
        self.consume_bind_token = consume_bind_token
        self._bind_token_valid = True
        self.character_players = None
        self.detail_payload = None
        self.map_payload = None
        self.character_updates = []

    async def bind_game(self, game_key, bind_token):
        assert bind_token == "bind-ok"
        if self.consume_bind_token and not self._bind_token_valid:
            raise RuntimeError("绑定凭证无效")
        self.binds.append((game_key, bind_token))
        if self.consume_bind_token:
            self._bind_token_valid = False
        return {"ok": True, "game_key": game_key, "gm_uid": "gm-1", "world_name": "测试世界", "players": [
            {"user_id": "gm-1", "character_name": "主持人"},
            {"user_id": "player-1", "character_name": "艾琳"},
        ]}

    async def action(self, game_key, actor, text, *, confirm=False, source=""):
        self.actions.append((game_key, actor, text, confirm))
        if not confirm and "攻击" in text:
            return {"phase": "dice", "message": "需要掷骰"}
        return {
            "phase": "done",
            "narration": "行动已公开，等待队友行动",
            "roll": {"dice_system": "d20", "value": 16} if confirm else None,
        }

    async def characters(self, game_key, actor):
        players = self.character_players if self.character_players is not None else [{
            "user_id": actor,
            "character_name": "测试角色",
            "character_sheet": {"hp": 8, "max_hp": 10, "gold": 3, "inventory": ["短剑"], "key_items": [{"name": "玉简"}]},
        }]
        return {"players": players,
            "rule_attrs": [
                {"key": "str", "display_name": "力量 (STR)"},
                {"key": "dex", "display_name": "敏捷 (DEX)"},
                {"key": "int", "display_name": "智力 (INT)"},
            ],
            "rule_attrs_total": 72,
            "rule_meta": {
                "attribute_points": 72,
                "max_skills": 6,
                "skill_point_total": 120,
                "max_skill_value": 60,
                "skill_hint": "技能可写武学招式、轻功、医毒或江湖手段。",
                "skill_pools": {"剑客": ["御剑术", "剑气纵横"]},
            },
            "rule_classes": ["剑客", "术士"],
        }

    async def update_character(self, game_key, actor, updates):
        self.character_updates.append((game_key, actor, updates))
        return {"ok": True}

    async def generate_character(self, prompt, *, game_key="", rule_id=""):
        self.character_generations.append((prompt, game_key, rule_id))
        return {"ok": True, "character": {
            "character_name": "吴川",
            "race": "凡人散修",
            "class": "落魄剑修",
            "attributes": {"str": 10, "dex": 14, "con": 11, "int": 12, "wis": 13, "cha": 12},
            "skills": [{"name": "御剑术", "value": 45}, {"name": "医毒", "value": 35}],
            "background": "出身边城，因旧案离山，想查清师门覆灭真相。",
            "equipment": [{"name": "旧铁剑"}, {"name": "伤药"}],
        }}

    async def game_detail(self, game_key, actor):
        payload = {
            "pending_payments": [
            {"id": "pay-1", "uid": "player-1", "amount": 5, "reason": "购买药水", "round": 3, "status": "pending"},
            {"id": "pay-2", "uid": "gm-1", "amount": 99, "reason": "别人的账单", "round": 3, "status": "pending"},
            ],
        }
        if self.detail_payload:
            payload.update(self.detail_payload)
        return payload

    async def private_log(self, game_key, actor):
        return {"ok": True, "messages": [
            {"user_id": actor, "character_name": "艾琳", "round": 2, "text": "你注意到门后有呼吸声。"},
            {"user_id": actor, "character_name": "艾琳", "round": 3, "text": "玉简微微发热。"},
        ]}

    async def resolve_payment(self, game_key, actor, payment_id, accepted):
        self.payment_resolutions.append((game_key, actor, payment_id, accepted))
        return {"ok": True, "accepted": accepted, "payment": {"id": payment_id}}

    async def advance(self, game_key, actor, *, force=True):
        self.advances.append((game_key, actor, force))
        return {"ok": True, "phase": "done", "narration": "迷雾散开，下一幕开始。"}

    async def set_player_away(self, game_key, actor, user_id, *, away):
        self.away_updates.append((game_key, actor, user_id, away))
        name = {"gm-1": "主持人", "player-1": "艾琳"}.get(user_id, user_id)
        return {"ok": True, "user_id": user_id, "character_name": name, "away": away}

    async def map(self, game_key, actor=""):
        return self.map_payload or {
            "current_scene": "竹林古道",
            "locations": [
                {"id": "loc-1", "name": "太虚城", "connected_to": ["loc-2"], "content": "凡人和修士混居的边陲城池。"},
                {"id": "loc-2", "name": "竹林古道", "connected_to": ["loc-1"], "content": "妖雾常在夜里漫过石阶。"},
            ],
        }

    async def build_join_link(self, game_key, user=""):
        suffix = f"&user={user}" if user else ""
        return f"https://table.example/#/join?game={game_key}&share=1{suffix}"


class EnglishFakeAPI(FakeAPI):
    async def bind_game(self, game_key, bind_token):
        result = await super().bind_game(game_key, bind_token)
        result.update({
            "world_name": "The Long Night",
            "language": "en",
            "players": [
                {"user_id": "gm-1", "character_name": "Game Master"},
                {"user_id": "player-1", "character_name": "Erin"},
            ],
        })
        return result


class ExtensionFakeAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        self.extension_calls = []

    async def apply_bridge_extensions(self, stage, payload):
        self.extension_calls.append((stage, dict(payload)))
        if stage == "before_message" and payload.get("text") == "插件命令":
            return {
                "ok": True,
                "handled": True,
                "payload": payload,
                "outputs": [{"type": "text", "text": "插件已处理"}],
            }
        if stage == "after_result" and payload.get("text") == "原始文字":
            changed = dict(payload)
            changed["text"] = "插件修改后的文字"
            return {"ok": True, "handled": False, "payload": changed, "outputs": []}
        if stage == "render" and payload.get("kind") == "card":
            return {
                "ok": True,
                "handled": True,
                "payload": payload,
                "outputs": [{
                    "type": "card",
                    "title": "插件卡片",
                    "subtitle": "",
                    "lines": ["自定义展示"],
                    "fallback_text": "插件卡片：自定义展示",
                }],
            }
        return {"ok": True, "handled": False, "payload": payload, "outputs": []}


class BrokenExtensionFakeAPI(FakeAPI):
    async def apply_bridge_extensions(self, _stage, _payload):
        raise RuntimeError("broken extension service")


class FakeSender:
    def __init__(self):
        self.messages = []

    async def send_group_text(self, group_id, text):
        self.messages.append((group_id, text))
        return {"status": "ok"}

    async def send_private_text(self, user_id, text):
        self.messages.append((f"private:{user_id}", text))
        return {"status": "ok"}


class FakeImageSender(FakeSender):
    async def send_group_image(self, group_id, image_path, caption=""):
        self.messages.append((group_id, f"IMAGE:{image_path}:{caption}"))
        return {"status": "ok"}

    async def send_private_image(self, user_id, image_path, caption=""):
        self.messages.append((f"private:{user_id}", f"IMAGE:{image_path}:{caption}"))
        return {"status": "ok"}


class PrivateBlockedSender(FakeSender):
    async def send_private_text(self, user_id, text):
        raise RuntimeError("private message blocked")


def qq_config(**overrides):
    data = {
        "blocked_users": (),
        "block_official_bots": False,
        "chat_filter_enabled": False,
        "group_list": (),
        "group_list_mode": "whitelist",
        "private_list": (),
        "private_list_mode": "whitelist",
        "show_dropped_logs": False,
        "reply_delay_min_sec": 0,
        "reply_delay_max_sec": 0,
        "command_dedup_window_sec": 6,
        "link_reminder_enabled": True,
        "ai_character_creation_enabled": True,
        "advance_allowed_users": (),
        "card_cache_max_age_hours": 24,
        "card_cache_max_files": 200,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def group_message(message_id, user_id, text, *, self_id="999", group_id="100"):
    return {
        "post_type": "message",
        "message_type": "group",
        "message_id": message_id,
        "self_id": self_id,
        "group_id": group_id,
        "user_id": user_id,
        "message": [
            {"type": "at", "data": {"qq": self_id}},
            {"type": "text", "data": {"text": text}},
        ],
    }


def group_message_with_mentions(message_id, user_id, text, mentions, *, self_id="999", group_id="100"):
    segments = [
        {"type": "at", "data": {"qq": self_id}},
        {"type": "text", "data": {"text": text}},
    ]
    for qq in mentions:
        segments.append({"type": "at", "data": {"qq": qq}})
    return {
        "post_type": "message",
        "message_type": "group",
        "message_id": message_id,
        "self_id": self_id,
        "group_id": group_id,
        "user_id": user_id,
        "message": segments,
    }


def private_message(message_id, user_id, text, *, self_id="999"):
    return {
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "self_id": self_id,
        "user_id": user_id,
        "message": [{"type": "text", "data": {"text": text}}],
    }


def private_message_from_group(message_id, user_id, text, *, group_id="100", self_id="999"):
    """群临时会话私聊：message_type=private 但 payload 带 group_id（来源群）。"""
    payload = private_message(message_id, user_id, text, self_id=self_id)
    payload["group_id"] = group_id
    return payload


@pytest.mark.asyncio
async def test_bind_action_and_server_roll_flow(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    api = FakeAPI()
    sender = FakeSender()
    adapter = QQTRPGAdapter(api, store, sender)

    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "42", "我攻击守卫"))
    await adapter.handle_payload(group_message("m3", "42", "掷骰"))

    assert store.group("100")["game_key"] == "web|game|bot"
    assert api.actions == [
        ("web|game|bot", "gm-1", "我攻击守卫", False),
        ("web|game|bot", "gm-1", "我攻击守卫", True),
    ]
    assert "D20 = 16" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_bot_extension_can_handle_qq_command_before_builtin_logic(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    api = ExtensionFakeAPI()
    sender = FakeSender()
    adapter = QQTRPGAdapter(api, store, sender)

    await adapter.handle_payload(group_message("plugin-1", "42", "插件命令"))

    assert sender.messages[-1] == ("100", "插件已处理")
    assert api.actions == []
    assert api.extension_calls[0][0] == "before_message"


@pytest.mark.asyncio
async def test_bot_extension_can_modify_text_and_replace_card_renderer(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    api = ExtensionFakeAPI()
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(api, store, sender)
    adapter._render_card_png = lambda *_args, **_kwargs: tmp_path / "plugin-card.png"

    await adapter._send_group_text("100", "原始文字")
    await adapter._send_group_card(
        "100",
        title="内置卡片",
        lines=["内置展示"],
        fallback="内置卡片：内置展示",
    )

    assert sender.messages[0] == ("100", "插件修改后的文字")
    assert "IMAGE:" in sender.messages[1][1]
    assert any(stage == "render" and payload.get("kind") == "card" for stage, payload in api.extension_calls)


@pytest.mark.asyncio
async def test_bot_extension_failure_falls_back_to_builtin_qq_behavior(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(BrokenExtensionFakeAPI(), store, sender)

    await adapter._send_group_text("100", "内置回复")
    await adapter.handle_payload(group_message("broken-1", "42", "帮助"))

    assert sender.messages[0] == ("100", "内置回复")
    assert "尚未绑定" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_bind_success_message_explains_next_steps(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)

    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    text = sender.messages[-1][1]
    assert "接下来这样玩" in text
    assert "@我 加入 角色名" in text
    assert "可认领" in text
    assert "@我 帮助" in text


@pytest.mark.asyncio
async def test_english_game_binding_drives_qq_commands_and_presenters(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = EnglishFakeAPI()
    api.character_players = [
        {"user_id": "gm-1", "character_name": "Game Master"},
        {
            "user_id": "player-1",
            "character_name": "Erin",
            "character_sheet": {
                "hp": 8,
                "max_hp": 10,
                "gold": 3,
                "attributes": {"dex": 14},
                "level_up_points": 1,
            },
        },
    ]
    adapter = QQTRPGAdapter(api, store, sender)

    await adapter.handle_payload(group_message("en-1", "42", "bind web|game|bot bind-ok"))
    assert store.group("100")["language"] == "en"
    assert "How to get started" in sender.messages[-1][1]

    await adapter.handle_payload(group_message("en-2", "43", "help"))
    assert "DiceFrame chat quick start" in sender.messages[-1][1]

    await adapter.handle_payload(group_message("en-3", "43", "join Erin"))
    assert store.player("100", "43")["user_id"] == "player-1"
    assert "Character claimed: Erin" in sender.messages[-1][1]

    await adapter.handle_payload(group_message("en-4", "43", "attributes"))
    assert "Points available: 1" in sender.messages[-1][1]

    await adapter.handle_payload(group_message("en-5", "43", "add dex 1"))
    assert api.character_updates[-1][2] == {"attributes": {"dex": 15}}
    assert "0 remaining" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_english_web_action_sync_uses_english_fallback_name_and_punctuation(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await store.bind_group("100", "web|game|bot", "42", "gm-1", language="en")

    await adapter._send_web_action_to_group("100", {"text": "I inspect the gate", "source": "web"})

    assert sender.messages[-1][1] == "Adventurer: I inspect the gate"


@pytest.mark.asyncio
async def test_message_id_dedup_survives_reload(tmp_path):
    path = tmp_path / "sessions.json"
    first = QQSessionStore(path)
    assert await first.remember_message("same") is True
    assert await first.remember_message("same") is False

    second = QQSessionStore(path)
    await second.load()
    assert await second.remember_message("same") is False
    assert json.loads(path.read_text(encoding="utf-8"))["recent_message_ids"] == ["same"]


@pytest.mark.asyncio
async def test_semantic_command_dedup_blocks_replayed_bind_with_new_message_id(tmp_path):
    config = SimpleNamespace(
        blocked_users=(), block_official_bots=False,
        chat_filter_enabled=False, command_dedup_window_sec=6,
        reply_delay_min_sec=0, reply_delay_max_sec=0,
    )
    store = QQSessionStore(tmp_path / "sessions.json")
    api = FakeAPI()
    sender = FakeSender()
    adapter = QQTRPGAdapter(api, store, sender, config)

    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "42", "  绑定   web|game|bot   bind-ok  "))

    assert api.binds == [("web|game|bot", "bind-ok")]
    assert len(sender.messages) == 1


@pytest.mark.asyncio
async def test_replayed_bind_token_cannot_steal_group_gm_platform_identity(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    api = FakeAPI(consume_bind_token=True)
    sender = FakeSender()
    adapter = QQTRPGAdapter(api, store, sender)

    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "绑定 web|game|bot bind-ok"))

    assert store.group("100")["gm_platform_id"] == "42"
    assert store.player("100", "42")["user_id"] == "gm-1"
    assert store.player("100", "43") is None


@pytest.mark.asyncio
async def test_unmentioned_group_message_is_ignored(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    payload = group_message("m1", "42", "普通闲聊")
    payload["message"] = [{"type": "text", "data": {"text": "普通闲聊"}}]

    await adapter.handle_payload(payload)

    assert sender.messages == []


@pytest.mark.asyncio
async def test_player_can_claim_existing_character_once(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))
    await adapter.handle_payload(group_message("m3", "44", "加入 艾琳"))

    assert store.player("100", "43")["user_id"] == "player-1"
    assert store.player("100", "44") is None
    assert "已被其他群成员认领" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_join_refreshes_roster_for_character_created_after_bind(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    api.character_players = [
        {"user_id": "gm-1", "character_name": "主持人"},
        {"user_id": "player-1", "character_name": "艾琳"},
        {"user_id": "player-2", "character_name": "吴川"},
    ]

    await adapter.handle_payload(group_message("m2", "43", "加入 吴川"))

    assert store.player("100", "43")["user_id"] == "player-2"
    assert "已认领角色：吴川" in sender.messages[-1][1]
    assert "吴川" in [item["character_name"] for item in store.group("100")["roster"]]


@pytest.mark.asyncio
async def test_join_allows_complete_character_name_inside_natural_sentence(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    api.character_players = [
        {"user_id": "gm-1", "character_name": "主持人"},
        {"user_id": "player-2", "character_name": "吴川"},
    ]
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "加入我新建的角色 吴川"))

    assert store.player("100", "43")["user_id"] == "player-2"
    assert "已认领角色：吴川" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_unclaimed_player_gets_actionable_onboarding(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "我观察四周"))

    text = sender.messages[-1][1]
    assert "你还没认领角色" in text
    assert "第一步" in text
    assert "@我 加入 角色名" in text
    assert "艾琳" in text


@pytest.mark.asyncio
async def test_unclaimed_player_can_read_group_help(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "帮助"))

    text = sender.messages[-1][1]
    assert "DiceFrame 群聊新手指南" in text
    assert "先认领角色" in text
    assert "艾琳" in text


@pytest.mark.asyncio
async def test_unclaimed_player_can_request_invite_link(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "邀请"))

    invite_text = sender.messages[-2][1]
    tutorial_text = sender.messages[-1][1]
    assert "邀请链接" in invite_text
    assert "\n　　https://table.example/#/join?game=web|game|bot&share=1" in invite_text
    assert "\n　　玩家打开后" in invite_text
    assert "@我 加入 角色名" in invite_text
    assert "群聊跑团新玩家一图流" in tutorial_text
    assert "@我 前情" in tutorial_text
    assert "@我 新建角色" in tutorial_text
    assert "车卡" in tutorial_text
    assert "@我 掷骰" in tutorial_text


@pytest.mark.asyncio
async def test_invite_sends_player_tutorial_as_image_without_embedding_link(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        captured.update(kwargs)
        path = tmp_path / "player_tutorial.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "邀请"))

    assert sender.messages[-2][1].startswith("邀请链接")
    assert sender.messages[-1][1].startswith("IMAGE:")
    assert captured["title"] == "群聊跑团新玩家一图流"
    assert captured["footer"] == BRAND_FOOTER
    assert any("@我 前情" in line for line in captured["lines"])
    assert not any("https://" in line for line in captured["lines"])


@pytest.mark.asyncio
async def test_invite_mentioned_player_by_private_message(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    sender.messages.clear()

    await adapter.handle_payload(group_message_with_mentions("m2", "42", "邀请 ", ["55"]))

    assert any(target == "100" and "邀请链接" in text for target, text in sender.messages)
    assert any(
        target == "private:55"
        and "你被邀请加入一局 DiceFrame 跑团" in text
        and "网页入口：https://table.example/#/join?game=web|game|bot&share=1" in text
        for target, text in sender.messages
    )
    assert any(target == "100" and "已私聊邀请 QQ 55" in text for target, text in sender.messages)


@pytest.mark.asyncio
async def test_invite_me_sends_private_message_to_requester(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    sender.messages.clear()

    await adapter.handle_payload(group_message("m2", "43", "邀请我"))

    assert any(
        target == "private:43" and "网页入口：https://table.example/#/join?game=web|game|bot&share=1" in text
        for target, text in sender.messages
    )


@pytest.mark.asyncio
async def test_private_invite_failure_falls_back_to_group_notice(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = PrivateBlockedSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    sender.messages.clear()

    await adapter.handle_payload(group_message_with_mentions("m2", "42", "邀请 ", ["55"]))

    assert any(
        target == "100" and "暂时无法私聊邀请 QQ 55" in text and "添加 Bot 好友" in text
        for target, text in sender.messages
    )


@pytest.mark.asyncio
async def test_unclaimed_player_can_read_public_recap_from_group(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    api.detail_payload = {
        "scene": "竹林古道",
        "round_number": 7,
        "multiplayer": {"waiting_players": [{"user_id": "player-2", "character_name": "吴川"}]},
        "recap": {
            "current_scene": "竹林古道",
            "round_number": 7,
            "narrative": "众人追查失踪商队，已发现妖雾与破损车辙有关。",
            "recent_rounds": [
                {
                    "round": 6,
                    "actions": [{"character_name": "无名", "text": "检查车辙"}],
                    "gm_response": "你们在泥地里发现半人半兽的脚印。",
                },
            ],
        },
    }
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "前情"))

    text = sender.messages[-1][1]
    assert "前情提要" in text
    assert "第 7 轮" in text
    assert "竹林古道" in text
    assert "失踪商队" in text
    assert "R6" in text
    assert "吴川" in text


@pytest.mark.asyncio
async def test_unclaimed_player_can_request_public_map_from_group(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    api.map_payload = {
        "current_scene": "竹林古道",
        "locations": [
            {"id": "city", "name": "太虚城", "connected_to": ["road"], "content": "边陲城池，有客栈、药铺和告示栏。"},
            {"id": "road", "name": "竹林古道", "connected_to": ["city"], "content": "雾气沿石阶上涌，夜里能听见铃声。"},
        ],
    }
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "地图"))

    text = sender.messages[-1][1]
    assert "场景地图" in text
    assert "竹林古道" in text
    assert "太虚城" in text
    assert "太虚城 ↔ 竹林古道" in text


@pytest.mark.asyncio
async def test_gm_can_advance_round_from_group(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "42", "推进"))

    assert api.advances == [("web|game|bot", "gm-1", True)]
    assert sender.messages[-2] == ("100", "收到推进指令，GM 正在思考中，生成下一段剧情…")
    assert "迷雾散开" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_non_gm_cannot_advance_by_default(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "下一轮"))

    assert api.advances == []
    assert "只有本局 GM" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_configured_user_can_advance_round_from_group(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender, qq_config(advance_allowed_users=("43",)))
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "下一轮"))

    assert api.advances == [("web|game|bot", "gm-1", True)]
    assert "迷雾散开" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_claimed_player_can_mark_self_away_and_back(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))

    await adapter.handle_payload(group_message("m3", "43", "暂离"))
    await adapter.handle_payload(group_message("m4", "43", "回来"))

    assert api.away_updates == [
        ("web|game|bot", "player-1", "player-1", True),
        ("web|game|bot", "player-1", "player-1", False),
    ]
    assert "默认跟随队伍" in sender.messages[-2][1]
    assert "已回来" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_gm_can_mark_named_player_away(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "42", "暂离 艾琳"))

    assert api.away_updates == [("web|game|bot", "gm-1", "player-1", True)]
    assert "艾琳 已暂离" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_non_gm_cannot_mark_other_player_away(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "暂离 主持人"))

    assert api.away_updates == []
    assert "只能切换自己的暂离状态" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_claimed_player_can_request_private_perception_from_group(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))

    await adapter.handle_payload(group_message("m3", "43", "感知"))

    assert sender.messages[-2][0] == "private:43"
    assert "角色感知" in sender.messages[-2][1]
    assert "玉简微微发热" in sender.messages[-2][1]
    assert "已私聊你最近的角色感知" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_group_perception_reports_private_message_failure_without_leaking_content(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = PrivateBlockedSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))

    await adapter.handle_payload(group_message("m3", "43", "感知"))

    assert "暂时无法私聊你" in sender.messages[-1][1]
    assert not any("玉简微微发热" in text for _, text in sender.messages)


@pytest.mark.asyncio
async def test_claimed_player_can_list_and_confirm_own_payment(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))

    await adapter.handle_payload(group_message("m3", "43", "支付"))
    await adapter.handle_payload(group_message("m4", "43", "确认支付"))

    assert sender.messages[-3][0] == "private:43"
    assert "购买药水" in sender.messages[-3][1]
    assert "别人的账单" not in sender.messages[-3][1]
    assert api.payment_resolutions == [("web|game|bot", "player-1", "pay-1", True)]
    assert "已确认支付 5 金币" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_claimed_player_can_reject_payment_by_index_in_private(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))
    payload = {
        "post_type": "message", "message_type": "private", "message_id": "m3",
        "self_id": "999", "user_id": "43", "message": [{"type": "text", "data": {"text": "拒绝支付 1"}}],
    }

    await adapter.handle_payload(payload)

    assert api.payment_resolutions == [("web|game|bot", "player-1", "pay-1", False)]
    assert "已拒绝支付 5 金币" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_new_character_command_renders_creation_checklist(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "新建角色"))

    text = next(text for target, text in sender.messages if target == "100" and "新建角色 / 车卡" in text)
    assert "新建角色 / 车卡" in text
    assert "角色名" in text
    assert "力量 (STR)" in text
    assert "职业/定位：可自拟" in text
    assert "参考：剑客" in text
    assert "剑客" in text
    assert "建议选 6 个左右" in text
    assert "参考技能点 120" in text
    assert "单项参考 60" in text
    assert "最多" not in text
    assert "上限" not in text
    assert "御剑术" in text
    assert "武学招式" in text
    assert "网页建卡入口" in text


@pytest.mark.asyncio
async def test_cheka_alias_renders_creation_checklist(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "车卡"))

    text = next(text for target, text in sender.messages if target == "100" and "新建角色 / 车卡" in text)
    assert "新建角色 / 车卡" in text
    assert "角色名" in text
    assert "网页建卡入口" in text


@pytest.mark.asyncio
async def test_new_character_card_sends_link_as_text(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        captured.update(kwargs)
        path = tmp_path / "new_character.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "车卡"))

    assert captured["title"] == "新建角色 / 车卡"
    assert captured["footer"] == BRAND_FOOTER
    assert not any("网页建卡入口" in line for line in captured["lines"])
    assert sender.messages[-2][1].startswith("IMAGE:")
    assert "网页建卡入口：https://table.example/#/join" in sender.messages[-1][1]
    assert not any(target == "private:43" for target, _ in sender.messages)


@pytest.mark.asyncio
async def test_regular_character_creation_does_not_start_private_ai_wizard(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "车卡"))

    assert api.character_generations == []
    assert not any(target == "private:43" and "AI 辅助车卡" in text for target, text in sender.messages)
    assert "43" not in adapter.character_wizards


@pytest.mark.asyncio
async def test_ai_character_creation_starts_private_wizard_only_when_requested(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "AI车卡"))

    assert api.character_generations == []
    assert any(target == "private:43" and "AI 辅助车卡" in text for target, text in sender.messages)
    assert "43" in adapter.character_wizards


@pytest.mark.asyncio
async def test_private_ai_character_wizard_generates_and_publishes_public_draft(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "AI车卡"))
    sender.messages.clear()

    await adapter.handle_payload(private_message("m3", "43", "我想玩一个落魄剑修，嘴硬心软，擅长御剑和医毒"))

    assert api.character_generations == [("我想玩一个落魄剑修，嘴硬心软，擅长御剑和医毒", "web|game|bot", "")]
    assert any(target == "private:43" and "AI 角色草稿" in text and "吴川" in text for target, text in sender.messages)
    assert not any(target == "100" and "吴川" in text for target, text in sender.messages)
    sender.messages.clear()

    await adapter.handle_payload(private_message("m4", "43", "确认"))

    public_text = "\n".join(text for target, text in sender.messages if target == "100")
    assert "新角色草稿" in public_text
    assert "玩家：43" in public_text
    assert "吴川" in public_text
    assert "御剑术" in public_text
    assert "审核" not in public_text
    assert "待审核" not in public_text
    assert "43" not in adapter.character_wizards


@pytest.mark.asyncio
async def test_ai_character_wizard_can_be_disabled(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender, qq_config(ai_character_creation_enabled=False))
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "AI车卡"))

    assert api.character_generations == []
    assert "43" not in adapter.character_wizards
    assert not any(target == "private:43" for target, _ in sender.messages)
    assert any(target == "100" and "已关闭 AI 辅助车卡" in text for target, text in sender.messages)


@pytest.mark.asyncio
async def test_ai_character_wizard_private_failure_keeps_web_link_fallback(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = PrivateBlockedSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "43", "AI车卡"))

    assert "43" not in adapter.character_wizards
    assert "暂时无法私聊你" in sender.messages[-1][1]
    assert api.character_generations == []


@pytest.mark.asyncio
async def test_group_whitelist_and_global_block_are_enforced(tmp_path):
    config = SimpleNamespace(
        blocked_users=("66",), block_official_bots=False,
        chat_filter_enabled=True, group_list=("100",),
        group_list_mode="whitelist", show_dropped_logs=False,
    )
    sender = FakeSender()
    adapter = QQTRPGAdapter(FakeAPI(), QQSessionStore(tmp_path / "sessions.json"), sender, config)

    await adapter.handle_payload(group_message("m1", "66", "帮助"))
    denied_group = group_message("m2", "42", "帮助", group_id="200")
    await adapter.handle_payload(denied_group)
    await adapter.handle_payload(group_message("m3", "42", "帮助"))

    assert len(sender.messages) == 1
    assert "尚未绑定游戏" in sender.messages[0][1]


@pytest.mark.asyncio
async def test_private_natural_language_never_enters_game_action(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))
    payload = {
        "post_type": "message", "message_type": "private", "message_id": "m3",
        "self_id": "999", "user_id": "43", "message": [{"type": "text", "data": {"text": "我观察四周"}}],
    }

    await adapter.handle_payload(payload)

    assert api.actions == []
    assert sender.messages[-1][0] == "private:43"
    assert "私聊 Bot 只做功能操作" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_private_smalltalk_does_not_enter_game_action(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))

    await adapter.handle_payload(private_message("m3", "43", "在吗"))

    assert api.actions == []
    assert sender.messages[-1][0] == "private:43"
    assert "私聊 Bot 只做功能操作" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_private_action_prefix_still_does_not_enter_game_action(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))

    await adapter.handle_payload(private_message("m3", "43", "行动 观察门后的动静"))

    assert api.actions == []
    assert "正式行动请在群聊" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_temporary_session_private_with_group_id_does_not_enter_game_action(tmp_path):
    """群临时会话私聊带 group_id，自然语言描述必须走私聊处理，不能漏进对局 action。"""
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))

    await adapter.handle_payload(private_message_from_group("m3", "43", "我观察四周"))

    assert api.actions == []
    assert sender.messages[-1][0] == "private:43"
    assert "私聊 Bot 只做功能操作" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_ai_wizard_temporary_session_private_generates_character_not_action(tmp_path):
    """AI 车卡进行中，用户从群临时会话私聊回复描述（带 group_id），应走 generate_character 而非对局 action。"""
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "AI车卡"))
    sender.messages.clear()

    await adapter.handle_payload(
        private_message_from_group("m3", "43", "落魄剑修，嘴硬心软，擅长御剑和医毒")
    )

    assert api.actions == []
    assert api.character_generations == [("落魄剑修，嘴硬心软，擅长御剑和医毒", "web|game|bot", "")]
    assert any(target == "private:43" and "AI 角色草稿" in text for target, text in sender.messages)


@pytest.mark.asyncio
async def test_stale_character_wizard_expires_and_falls_back(tmp_path):
    """wizard 超过 TTL 未活动自动失效，过期后私聊描述不再生成角色，走 fallback 提示。"""
    import time as _time

    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "AI车卡"))
    assert "43" in adapter.character_wizards

    # 把 wizard 的 updated_at 设为 11 分钟前，触发 TTL 过期
    adapter.character_wizards["43"]["updated_at"] = _time.time() - 660
    sender.messages.clear()

    await adapter.handle_payload(
        private_message_from_group("m3", "43", "落魄剑修，嘴硬心软")
    )

    assert api.character_generations == []
    assert "43" not in adapter.character_wizards  # 过期已清除
    assert sender.messages[-1][0] == "private:43"  # 走 fallback 私聊回复，未进 wizard 生成


def test_background_lines_splits_paragraphs_with_empty_marker():
    """角色背景按空行拆段落，段间用空串标记，供卡片渲染留空行。"""
    bg = "第一段背景。\n\n第二段背景，较长。\n\n第三段。"
    lines = QQTRPGAdapter._background_lines(bg)
    assert lines[0] == "背景："
    assert lines.count("") == 2  # 三段两个分隔
    assert "第一段背景。" in lines
    assert "第三段。" in lines


def test_background_lines_empty_or_blank_returns_placeholder():
    assert QQTRPGAdapter._background_lines("") == ["背景：暂无背景"]
    assert QQTRPGAdapter._background_lines("   \n  ") == ["背景：暂无背景"]


def test_background_lines_single_paragraph_no_separator():
    assert QQTRPGAdapter._background_lines("一段背景。") == ["背景：", "一段背景。"]


@pytest.mark.asyncio
async def test_web_sync_forwards_new_round_actions_and_narration(tmp_path):
    """bot 轮询发现 web 端轮次推进，补发未见行动（纯文本）+ 叙事卡。"""
    store = QQSessionStore(tmp_path / "sessions.json")
    await store.bind_group("100", "web|game|bot", "42", "gm-1")
    sender = FakeSender()
    api = FakeAPI()
    api.detail_payload = {
        "round_number": 2,
        "recap": {"recent_rounds": [{"round": 1, "actions": [{"character_name": "艾琳", "text": "我观察四周", "signature": "u1:t1"}], "gm_response": "守卫警惕地盯着你。"}]},
    }
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    # 第一次轮询：基线，不补发
    await adapter._poll_web_notifications()
    assert sender.messages == []
    # round 未变，不转发
    await adapter._poll_web_notifications()
    assert sender.messages == []
    # round 推进到 3，补发未见行动 + 叙事卡
    api.detail_payload["round_number"] = 3
    api.detail_payload["recap"]["recent_rounds"] = [
        {"round": 2, "actions": [{"character_name": "艾琳", "text": "我攻击守卫", "signature": "u1:t2"}], "gm_response": "剑光一闪。"}
    ]
    await adapter._poll_web_notifications()
    # 补发行动（纯文本「角色名：行动」）+ 叙事卡 fallback 文本
    assert len(sender.messages) == 2
    assert sender.messages[0] == ("100", "艾琳：我攻击守卫")
    assert "剑光一闪" in sender.messages[1][1]


@pytest.mark.asyncio
async def test_web_sync_realtime_pending_action_then_no_duplicate_on_advance(tmp_path):
    """网页提交行动实时转发；推进时已转过的行动不重复补发，只发叙事。"""
    store = QQSessionStore(tmp_path / "sessions.json")
    await store.bind_group("100", "web|game|bot", "42", "gm-1")
    sender = FakeSender()
    api = FakeAPI()
    api.detail_payload = {
        "round_number": 2,
        "recap": {"pending_actions": [{"character_name": "艾琳", "text": "我观察四周", "signature": "u1:t1"}]},
    }
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    # 第一次轮询：基线 + 实时转发 pending 行动
    await adapter._poll_web_notifications()
    assert sender.messages == [("100", "艾琳：我观察四周")]
    # 同一 pending 不重复转发
    await adapter._poll_web_notifications()
    assert sender.messages == [("100", "艾琳：我观察四周")]
    # 推进：行动已在 seen，不补发；只发叙事
    api.detail_payload["round_number"] = 3
    api.detail_payload["recap"]["recent_rounds"] = [
        {"round": 2, "actions": [{"character_name": "艾琳", "text": "我观察四周", "signature": "u1:t1"}], "gm_response": "守卫没发现你。"}
    ]
    api.detail_payload["recap"].pop("pending_actions", None)
    await adapter._poll_web_notifications()
    # 推进不补发行动（已实时转），只多一条叙事
    assert len(sender.messages) == 2
    assert "守卫没发现你" in sender.messages[1][1]


@pytest.mark.asyncio
async def test_web_sync_forwards_state_changes_on_advance(tmp_path):
    """轮次推进时叙事 + 状态变动合并成一张 GM 叙事卡。"""
    store = QQSessionStore(tmp_path / "sessions.json")
    await store.bind_group("100", "web|game|bot", "42", "gm-1")
    sender = FakeSender()
    api = FakeAPI()
    api.detail_payload = {
        "round_number": 2,
        "recap": {"recent_rounds": [{"round": 1, "actions": [], "gm_response": "", "state_changes": []}]},
    }
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter._poll_web_notifications()  # 基线
    assert sender.messages == []
    api.detail_payload["round_number"] = 3
    api.detail_payload["recap"]["recent_rounds"] = [
        {"round": 2, "actions": [], "gm_response": "守卫走远了。", "state_changes": ["【状态变动】艾琳：HP 10 -> 7（-3）"]}
    ]
    await adapter._poll_web_notifications()
    # 合并卡：叙事正文 + 状态变动在一张
    assert len(sender.messages) == 1
    msg = sender.messages[0][1]
    assert "守卫走远了" in msg
    assert "状态变动" in msg
    assert "HP" in msg
    assert "艾琳" in msg


@pytest.mark.asyncio
async def test_group_action_shows_thinking_when_actor_will_trigger_round(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    api.detail_payload = {
        "solo_mode": False,
        "multiplayer": {"solo_mode": False, "waiting_players": [{"user_id": "player-1", "character_name": "艾琳"}]},
    }
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))

    await adapter.handle_payload(group_message("m3", "43", "我观察四周"))

    assert sender.messages[-2] == ("100", "GM 正在思考中，生成下一段剧情…")
    assert "行动已公开" in sender.messages[-1][1]


@pytest.mark.asyncio
async def test_group_action_does_not_show_thinking_when_waiting_for_others(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    api.detail_payload = {
        "solo_mode": False,
        "multiplayer": {
            "solo_mode": False,
            "waiting_players": [
                {"user_id": "player-1", "character_name": "艾琳"},
                {"user_id": "gm-1", "character_name": "主持人"},
            ],
        },
    }
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))

    await adapter.handle_payload(group_message("m3", "43", "我观察四周"))

    assert not any("GM 正在思考中" in text for _, text in sender.messages)


@pytest.mark.asyncio
async def test_private_message_never_shows_thinking_or_submits_action_in_solo_mode(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeSender()
    api = FakeAPI()
    api.detail_payload = {"solo_mode": True, "multiplayer": {"solo_mode": True, "waiting_players": []}}
    adapter = QQTRPGAdapter(api, store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    await adapter.handle_payload(group_message("m2", "43", "加入 艾琳"))
    payload = {
        "post_type": "message", "message_type": "private", "message_id": "m3",
        "self_id": "999", "user_id": "43", "message": [{"type": "text", "data": {"text": "我观察四周"}}],
    }

    await adapter.handle_payload(payload)

    assert api.actions == []
    assert not any("GM 正在思考中" in text for _, text in sender.messages)
    assert sender.messages[-1][0] == "private:43"


@pytest.mark.asyncio
async def test_status_uses_image_card_when_sender_supports_it(tmp_path, monkeypatch):
    def fake_render(out_dir, **kwargs):
        path = tmp_path / "status.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    await adapter.handle_payload(group_message("m2", "42", "状态"))

    assert any(message[1].startswith("IMAGE:") for message in sender.messages)


@pytest.mark.asyncio
async def test_group_status_does_not_send_web_entry_link(tmp_path, monkeypatch):
    def fake_render(out_dir, **kwargs):
        path = tmp_path / "status.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    sender.messages.clear()

    await adapter.handle_payload(group_message("m2", "42", "状态"))

    assert not any("网页角色入口" in text or "网页入口" in text for _, text in sender.messages)
    assert not any(target.startswith("private:") for target, _ in sender.messages)


@pytest.mark.asyncio
async def test_group_status_shows_attributes_skills_quests(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        captured.update(kwargs)
        path = tmp_path / "status_full.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    api = FakeAPI()
    api.character_players = [{
        "user_id": "42",
        "character_name": "艾琳",
        "character_sheet": {
            "hp": 8, "max_hp": 10, "gold": 3,
            "inventory": ["短剑"], "key_items": [{"name": "玉简"}],
            "attributes": {"str": 10, "dex": 14, "int": 12},
            "skills": [{"name": "御剑术", "value": 45}, {"name": "医毒", "value": 35}],
        },
    }]
    api.detail_payload = {"plot_tracker": {"quests": {
        "q1": {"title": "寻找玉简", "status": "active", "progress": "已到城外"},
        "q2": {"title": "旧案了结", "status": "completed", "round_updated": 2},
    }}}
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(api, store, sender, qq_config())

    await adapter._send_status("100", "42", "web|game|bot", "42")

    lines = captured.get("lines", [])
    assert any("力量 (STR) 10" in ln for ln in lines), lines
    assert any("御剑术 45" in ln for ln in lines), lines
    assert any("[进行] 寻找玉简" in ln for ln in lines), lines
    assert any("[完成] 旧案了结" in ln for ln in lines), lines


@pytest.mark.asyncio
async def test_round_summary_shows_payment_amount_and_quick_actions(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        captured.update(kwargs)
        path = tmp_path / "summary.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender, qq_config())

    await adapter._send_round_summary_card(
        "100", 3, "迷雾散开。",
        ["【状态变动】艾琳：HP 8->5（-3）"],
        pending_payments=[{"id": "pay-1", "uid": "gm-1", "amount": 20, "reason": "购买药水", "status": "pending", "round": 3}],
        quick_actions=["调查四周", "继续前进"],
    )

    lines = captured.get("lines", [])
    hint = captured.get("hint") or []
    assert any("迷雾散开" in ln for ln in lines), lines
    assert any("购买药水：20 金币" in ln for ln in lines), lines
    assert ("调查四周", "继续前进") in hint, hint
    assert any("【状态变动】" in ln for ln in lines), lines


@pytest.mark.asyncio
async def test_round_summary_deduplicates_state_changes_already_in_narration(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        captured.update(kwargs)
        path = tmp_path / "summary_dedup.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender, qq_config())
    state_line = "【状态变动】尤洛：获得 摄魂银针（含施术者魂魄残片） x1；获得 朱砂符文绢帛（祭品名录） x1"

    await adapter._send_round_summary_card(
        "100",
        3,
        f"黑雾沉入银针。\n\n{state_line}",
        [state_line],
    )

    lines = captured.get("lines", [])
    joined = "\n".join(lines)
    assert joined.count("【状态变动】尤洛") == 1


@pytest.mark.asyncio
async def test_group_action_advancing_sends_single_summary(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        captured.update(kwargs)
        path = tmp_path / "adv.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    api = FakeAPI()
    # game_detail 返回真实 recap（recent_rounds 用 round 字段）；result.recap 是 last_state_update，无轮次字段
    api.detail_payload = {
        "round_number": 5,
        "recap": {"recent_rounds": [{"round": 5, "gm_response": "守卫被你说服，转身离去。", "state_changes": ["【状态变动】守卫：态度 敌对 -> 友善"]}]},
        "quick_actions": ["调查四周", "继续前进", "回头", "原地休息"],
    }
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(api, store, sender, qq_config())

    result = {
        "phase": "done",
        "advanced": True,
        "narration": "守卫被你说服，转身离去。",
        "quick_actions": ["调查四周", "继续前进", "回头", "原地休息"],
        "pending_payments": [{"id": "p1", "uid": "u1", "amount": 20, "reason": "贿赂守卫", "status": "pending", "round": 5}],
        "recap": {"some_state_update": True},
    }
    await adapter._send_action_result("100", "web|game|bot", "player-1", result)

    # 只发一张 GM 叙事合并卡（不再有行动结果图）
    assert captured.get("title") == "GM 叙事", captured
    lines = captured.get("lines", [])
    assert any("守卫被你说服" in ln for ln in lines), lines
    assert any("贿赂守卫：20 金币" in ln for ln in lines), lines
    assert any("【状态变动】守卫" in ln for ln in lines), lines
    hint = captured.get("hint") or []
    assert ("调查四周", "继续前进") in hint, hint
    assert ("回头", "原地休息") in hint, hint
    # 标记该轮已群发，避免 _poll 再发第二张
    assert adapter._web_sync_last_round.get("web|game|bot") == 5


@pytest.mark.asyncio
async def test_action_advance_then_poll_no_duplicate(tmp_path, monkeypatch):
    """群聊行动触发推进发一张卡后，_poll 不应再发第二张叙事图。"""
    def fake_render(out_dir, **kwargs):
        path = tmp_path / "card.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    await store.bind_group("100", "web|game|bot", "42", "gm-1")
    sender = FakeImageSender()
    api = FakeAPI()
    api.detail_payload = {
        "round_number": 5,
        "recap": {"recent_rounds": [{"round": 5, "actions": [], "gm_response": "守卫被你说服，转身离去。", "state_changes": []}]},
    }
    adapter = QQTRPGAdapter(api, store, sender, qq_config())

    result = {
        "phase": "done",
        "advanced": True,
        "narration": "守卫被你说服，转身离去。",
        "pending_payments": [],
        "recap": {"some_state_update": True},
    }
    await adapter._send_action_result("100", "web|game|bot", "player-1", result)
    images = [m for m in sender.messages if m[1].startswith("IMAGE:")]
    assert len(images) == 1, sender.messages
    assert adapter._web_sync_last_round.get("web|game|bot") == 5

    # 之后轮询：round 仍是 5（<= last 5），不应再发第二张叙事图
    await adapter._poll_web_notifications()
    images = [m for m in sender.messages if m[1].startswith("IMAGE:")]
    assert len(images) == 1, sender.messages


@pytest.mark.asyncio
async def test_poll_skips_new_round_while_group_action_inflight(tmp_path, monkeypatch):
    """群聊行动进行中（in-flight）_poll 不抢发新轮卡；行动结束 _send_action_result 已推 last，poll 不重复发。"""
    def fake_render(out_dir, **kwargs):
        path = tmp_path / "card.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    await store.bind_group("100", "web|game|bot", "42", "gm-1")
    sender = FakeImageSender()
    api = FakeAPI()
    api.detail_payload = {
        "round_number": 5,
        "recap": {"recent_rounds": [{"round": 5, "actions": [], "gm_response": "守卫被你说服，转身离去。", "state_changes": []}]},
    }
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    adapter._web_sync_last_round["web|game|bot"] = 4
    adapter._group_action_inflight["web|game|bot"] = True
    await adapter._poll_web_notifications()
    images = [m for m in sender.messages if m[1].startswith("IMAGE:")]
    assert images == [], sender.messages
    assert adapter._web_sync_last_round.get("web|game|bot") == 4  # inflight 跳过时不推 last
    adapter._group_action_inflight["web|game|bot"] = False
    adapter._web_sync_last_round["web|game|bot"] = 5  # 行动结束 _send_action_result 已发卡并推 last
    await adapter._poll_web_notifications()
    images = [m for m in sender.messages if m[1].startswith("IMAGE:")]
    assert images == [], sender.messages


@pytest.mark.asyncio
async def test_group_sourced_action_not_echoed_back(tmp_path):
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender, qq_config())

    await adapter._send_web_action_to_group("100", {"character_name": "艾琳", "text": "我攻击", "signature": "s1", "source": "group"})
    assert not sender.messages  # 群里经 bot 发的行动不再复读回群

    await adapter._send_web_action_to_group("100", {"character_name": "艾琳", "text": "我调查", "signature": "s2", "source": ""})
    assert any("艾琳：我调查" in text for _, text in sender.messages)


@pytest.mark.asyncio
async def test_link_reminder_can_be_disabled_for_group_help_and_status(tmp_path, monkeypatch):
    def fake_render(out_dir, **kwargs):
        path = tmp_path / "card.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender, qq_config(link_reminder_enabled=False))
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    sender.messages.clear()

    await adapter.handle_payload(group_message("m2", "43", "帮助"))
    await adapter.handle_payload(group_message("m3", "42", "状态"))

    assert sender.messages
    assert not any("网页入口" in text or "网页角色入口" in text for _, text in sender.messages)


@pytest.mark.asyncio
async def test_join_link_is_sent_as_text_not_rendered_into_image(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        captured.update(kwargs)
        path = tmp_path / "bind.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    adapter = QQTRPGAdapter(FakeAPI(), store, sender)

    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))

    assert not any("网页入口" in line for line in captured["lines"])
    assert sender.messages[0][1].startswith("IMAGE:")
    assert "网页入口：https://table.example/#/join" in sender.messages[1][1]


@pytest.mark.asyncio
async def test_parent_watch_stops_transport_when_parent_process_is_gone(monkeypatch):
    class FakeTransport:
        def __init__(self):
            self.stopped = False

        async def stop(self):
            self.stopped = True

    transport = FakeTransport()
    monkeypatch.setattr("src.bots.qq.main._pid_is_alive", lambda pid: False)

    await _watch_parent_process(123456, transport, interval_sec=0)

    assert transport.stopped is True


@pytest.mark.asyncio
async def test_allocate_points_shows_attribute_card(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        path = tmp_path / "attrs.png"
        path.write_bytes(b"png")
        captured.update(kwargs)
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    api = FakeAPI()
    api.character_players = [{
        "user_id": "gm-1",
        "character_name": "艾琳",
        "character_sheet": {"attributes": {"str": 10, "dex": 14, "int": 12}, "level_up_points": 2, "hp": 8, "max_hp": 10, "gold": 3},
    }]
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    sender.messages.clear()

    await adapter.handle_payload(group_message("m2", "42", "加点"))

    lines = captured.get("lines", [])
    assert any("力量" in ln and "10" in ln for ln in lines)
    assert any("可分配点数：2" in ln for ln in lines)


@pytest.mark.asyncio
async def test_allocate_attribute_point_consumes_pool(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        path = tmp_path / "add.png"
        path.write_bytes(b"png")
        captured.update(kwargs)
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    api = FakeAPI()
    api.character_players = [{
        "user_id": "gm-1",
        "character_name": "艾琳",
        "character_sheet": {"attributes": {"str": 10, "dex": 14, "int": 12}, "level_up_points": 2, "hp": 8, "max_hp": 10, "gold": 3},
    }]
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    sender.messages.clear()

    await adapter.handle_payload(group_message("m2", "42", "加 力量 1"))

    assert api.character_updates == [("web|game|bot", "gm-1", {"attributes": {"str": 11}})]
    lines = captured.get("lines", [])
    assert any("力量" in ln and "11" in ln for ln in lines)


@pytest.mark.asyncio
async def test_allocate_attribute_point_insufficient_pool(tmp_path, monkeypatch):
    def fake_render(out_dir, **kwargs):
        path = tmp_path / "fail.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    api = FakeAPI()
    api.character_players = [{
        "user_id": "gm-1",
        "character_name": "艾琳",
        "character_sheet": {"attributes": {"str": 10}, "level_up_points": 0, "hp": 8, "max_hp": 10, "gold": 3},
    }]
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    sender.messages.clear()

    await adapter.handle_payload(group_message("m2", "42", "加 力量 1"))

    assert api.character_updates == []
    assert any("点数不足" in text for _, text in sender.messages)


@pytest.mark.asyncio
async def test_allocate_attribute_point_unknown_name(tmp_path, monkeypatch):
    def fake_render(out_dir, **kwargs):
        path = tmp_path / "unk.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    sender = FakeImageSender()
    api = FakeAPI()
    api.character_players = [{
        "user_id": "gm-1",
        "character_name": "艾琳",
        "character_sheet": {"attributes": {"str": 10, "dex": 14, "int": 12}, "level_up_points": 2, "hp": 8, "max_hp": 10, "gold": 3},
    }]
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "绑定 web|game|bot bind-ok"))
    sender.messages.clear()

    await adapter.handle_payload(group_message("m2", "42", "加 体质 1"))

    assert api.character_updates == []
    assert any("未找到属性" in text for _, text in sender.messages)


@pytest.mark.asyncio
async def test_advance_group_sends_merged_card_and_marks_web_sync(tmp_path, monkeypatch):
    captured = {}

    def fake_render(out_dir, **kwargs):
        path = tmp_path / "adv.png"
        path.write_bytes(b"png")
        captured.update(kwargs)
        return path

    monkeypatch.setattr("src.bots.qq.adapter.render_card_png", fake_render)
    store = QQSessionStore(tmp_path / "sessions.json")
    await store.bind_group("100", "web|game|bot", "42", "gm-1")
    sender = FakeImageSender()
    api = FakeAPI()
    api.detail_payload = {
        "round_number": 3,
        "recap": {"recent_rounds": [
            {"round": 2, "actions": [], "gm_response": "迷雾散开。", "state_changes": ["【状态变动】艾琳：HP 10 -> 7"]}
        ]},
    }
    adapter = QQTRPGAdapter(api, store, sender, qq_config())
    await adapter.handle_payload(group_message("m1", "42", "推进"))

    lines = captured.get("lines", [])
    assert any("迷雾散开" in ln for ln in lines)
    assert any("状态变动" in ln for ln in lines)
    assert any("HP" in ln for ln in lines)
    # 标记 last_round，web sync 不会重复发这次推进
    assert adapter._web_sync_last_round.get("web|game|bot") == 3
