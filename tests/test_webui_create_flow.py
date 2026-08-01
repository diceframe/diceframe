"""WebUI 开局链路测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.commands.game_handler import GameHandler
from src.engine.game_instance import GameRegistry
from src.engine.health import record_health_event
from src.llm.client import LLMResponse
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.store import LorebookStore
from src.webui.api import WebAPI, can_modify_character
from src.webui.session import SessionManager


class FakeLLMClient:
    default = "fake"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call(self, system_prompt: str, user_message: str, **kwargs) -> LLMResponse:
        self.calls.append({
            "system_prompt": system_prompt,
            "user_message": user_message,
            "kwargs": kwargs,
        })
        if "TRPG规则设计师" in system_prompt:
            return LLMResponse(
                content=json.dumps({
                    "rule_name": "凡人修仙轻量规则",
                    "rule_name_en": "Mortal Cultivation Lite",
                    "description": "低资质散修成长的轻量规则。",
                    "dice_system": "d20",
                    "combat_model": "hp_based",
                    "mechanics": "xianxia_lite",
                    "ruleset_level": "assisted",
                    "attributes": [
                        {"key": "body", "name": "体魄", "min": 3, "max": 18},
                        {"key": "sense", "name": "神识", "min": 3, "max": 18},
                        {"key": "will", "name": "心性", "min": 3, "max": 18},
                    ],
                    "special_stats": [{"key": "qi", "name": "灵力", "max": 100}],
                    "attribute_points": 36,
                    "attr_hint": "凡人修仙属性偏低开局，资源比天赋更重要。",
                    "hp_formula": "5 + body * 3",
                    "max_skills": 4,
                    "skill_point_total": 180,
                    "max_skill_value": 80,
                    "skill_mode": "narrative",
                    "skill_hint": "技能填写功法、法术、炼丹、制符等。",
                    "currency": "灵石",
                    "classes": [{"name": "散修", "description": "无宗门依靠的低阶修士", "starter_equipment": ["粗劣飞剑"]}],
                    "skill_pools": {"散修": ["基础吐纳", "御器", "符箓", "遁术"]},
                    "item_categories": {"equipment": ["飞剑"], "consumable": ["丹药"], "misc": ["玉简"]},
                    "gm_prompt_appendix": "保持凡人修仙味：谨慎、资源稀缺、机缘有代价。",
                    "difficulty_instructions": {"轻松": "机缘稍多", "标准": "资源紧张", "硬核": "强敌环伺"},
                }, ensure_ascii=False),
                narration="",
                state_update=None,
                memory_delta=None,
                info_asymmetry=None,
                plot_update=None,
                total_tokens=20,
                is_narration_only=True,
                provider_used="fake",
            )
        return LLMResponse(
            content="艾琳站在试炼大厅中央，新的冒险开始了。",
            narration="艾琳站在试炼大厅中央，新的冒险开始了。",
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=12,
            is_narration_only=True,
            provider_used="fake",
        )


def test_session_rebind_persists_restored_player_identity(tmp_path):
    manager = SessionManager(tmp_path)
    token, original_uid = manager.get_or_create(None)
    assert original_uid.startswith("web_")

    manager.rebind(token, "player_restored")

    reloaded = SessionManager(tmp_path)
    assert reloaded.get_or_create(token) == (token, "player_restored")


def _write_world(
    worlds_dir,
    world_id: str,
    *,
    starter_lorebook: list[dict] | None = None,
    default_rule: str = "freeform_fantasy",
) -> None:
    worlds_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "world_id": world_id,
        "world_name": world_id,
        "description": f"{world_id} description",
        "world_setting": f"{world_id} setting",
        "starter_scene": "试炼大厅",
        "default_rule": default_rule,
        "starter_lorebook": starter_lorebook or [],
    }
    (worlds_dir / f"{world_id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@pytest.fixture()
def web_api(tmp_path):
    data_dir = tmp_path / "data"
    worlds_dir = tmp_path / "worlds"
    prompts_dir = tmp_path / "prompts"
    rules_dir = tmp_path / "rules"
    prompts_dir.mkdir()
    rules_dir.mkdir()
    (prompts_dir / "gm_system_zh.md").write_text("你是测试 GM。", encoding="utf-8")
    (rules_dir / "freeform_fantasy.json").write_text(
        json.dumps({
            "rule_id": "freeform_fantasy",
            "rule_name": "自由幻想",
            "dice_system": "d20",
            "combat_model": "hp_based",
            "attributes": [{"key": "str", "name": "力量", "min": 3, "max": 18}],
            "attribute_points": 60,
            "attr_hint": "属性测试提示",
            "hp_formula": "20 + str",
            "max_skills": 3,
            "skill_mode": "narrative",
            "skill_hint": "技能测试提示",
            "skill_pools": {"游侠": ["侦查", "射击"]},
            "skills": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    _write_world(
        worlds_dir,
        "template_world",
        starter_lorebook=[{
            "id": "template_npc",
            "world_id": "template_world",
            "name": "模板导师",
            "type": "npc",
            "keywords": ["导师"],
            "content": "模板自带角色",
            "tier": "core",
        }],
    )

    registry = GameRegistry(data_dir / "saves")
    lorebook = LorebookStore(data_dir / "lorebook.db")
    lorebook.open()
    fake_llm = FakeLLMClient()
    handler = GameHandler(
        registry=registry,
        llm_client=fake_llm,
        lorebook_matcher=KeywordMatcher(),
        lorebook_store=lorebook,
        memory_store=None,
        prompts_dir=prompts_dir,
        rules_dir=rules_dir,
        worlds_dir=worlds_dir,
    )
    api = WebAPI(
        registry=registry,
        lorebook=lorebook,
        memory=None,
        rules_dir=rules_dir,
        handler=handler,
        llm_client=fake_llm,
        worlds_dir=worlds_dir,
    )
    try:
        yield api, lorebook, registry, fake_llm, worlds_dir
    finally:
        lorebook.close()


@pytest.mark.asyncio
async def test_generate_lorebook_entries_from_natural_language(web_api):
    api, lorebook, _registry, fake_llm, _worlds_dir = web_api
    lorebook.create_world("custom_world", "测试世界", description="用于批量生成测试")

    async def fake_call(system_prompt: str, user_message: str, **kwargs) -> LLMResponse:
        fake_llm.calls.append({
            "system_prompt": system_prompt,
            "user_message": user_message,
            "kwargs": kwargs,
        })
        return LLMResponse(
            content=json.dumps({
                "entries": [
                    {
                        "name": "黑港城",
                        "type": "location",
                        "keywords": ["黑港", "港城"],
                        "content": "雾气笼罩的走私港口，银钥会在码头仓库中安排秘密交易。",
                        "tier": "core",
                        "unreliable": False,
                    },
                    {
                        "name": "银钥会",
                        "type": "faction",
                        "keywords": [],
                        "content": "由学者、走私者和失势贵族组成的隐秘结社，正在寻找月蚀仪式的线索。",
                        "tier": "background",
                    },
                ]
            }, ensure_ascii=False),
            narration="",
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=20,
            is_narration_only=False,
            provider_used="fake",
        )

    fake_llm.call = fake_call

    result = await api.generate_lorebook_entries("custom_world", "黑港城里有银钥会和月蚀仪式。")

    assert result["ok"] is True
    assert result["count"] == 2
    entries = lorebook.list_entries("custom_world")
    assert {e["name"] for e in entries} == {"黑港城", "银钥会"}
    assert next(e for e in entries if e["name"] == "银钥会")["keywords"][0] == "银钥会"
    assert fake_llm.calls[-1]["kwargs"]["json_mode"] is True


@pytest.mark.asyncio
async def test_lorebook_generation_repairs_invalid_json(web_api):
    api, lorebook, _registry, fake_llm, _worlds_dir = web_api
    lorebook.create_world("repair_world", "修复世界", description="测试 JSON 修复")

    async def fake_call(system_prompt: str, user_message: str, **kwargs) -> LLMResponse:
        fake_llm.calls.append({
            "system_prompt": system_prompt,
            "user_message": user_message,
            "kwargs": kwargs,
        })
        if "JSON 修复器" in system_prompt:
            content = json.dumps({
                "entries": [{
                    "name": "青石坊市",
                    "type": "location",
                    "keywords": ["青石坊市"],
                    "content": "低阶散修交换丹药、符箓和传闻的坊市。",
                    "tier": "core",
                    "unreliable": False,
                }]
            }, ensure_ascii=False)
        else:
            content = '{"entries": [{"name": "青石坊市", "type": "location", '
        return LLMResponse(
            content=content,
            narration="",
            state_update=None,
            memory_delta=None,
            info_asymmetry=None,
            plot_update=None,
            total_tokens=10,
            is_narration_only=True,
            provider_used="fake",
        )

    fake_llm.call = fake_call

    result = await api.generate_lorebook_entries("repair_world", "青石坊市是散修交易地点。")

    assert result["ok"] is True
    assert result["count"] == 1
    assert any("JSON 修复器" in c["system_prompt"] for c in fake_llm.calls)


@pytest.mark.asyncio
async def test_coc_hp_has_rule_suggestion_but_can_be_manually_edited(web_api):
    api, _lorebook, registry, _fake_llm, worlds_dir = web_api
    (api._rules_dir / "freeform_coc.json").write_text(
        json.dumps({
            "rule_id": "freeform_coc",
            "rule_name": "克苏鲁调查自由规则",
            "mechanics": "coc7e_core",
            "attributes": [
                {"key": "str", "name": "力量", "min": 3, "max": 18},
                {"key": "con", "name": "体质", "min": 3, "max": 18},
                {"key": "dex", "name": "敏捷", "min": 3, "max": 18},
                {"key": "int", "name": "智力", "min": 3, "max": 18},
                {"key": "edu", "name": "教育", "min": 3, "max": 18},
                {"key": "app", "name": "外貌", "min": 3, "max": 18},
                {"key": "pow", "name": "意志", "min": 3, "max": 18},
                {"key": "siz", "name": "体型", "min": 8, "max": 18},
            ],
            "attribute_points": 80,
            "hp_formula": "max((con + siz) // 2, 1)",
            "classes": [{"name": "调查员"}],
            "skill_pools": {},
            "special_stats": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_world(worlds_dir, "coc_world", default_rule="freeform_coc")

    created = await api.create_game(
        "coc_world",
        "CoC 测试",
        players=[{
            "character_name": "调查员",
            "class": "调查员",
            "attributes": {"str": 7, "con": 9, "dex": 11, "int": 14, "edu": 13, "app": 8, "pow": 9, "siz": 9},
        }],
    )
    inst = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(inst.players))
    cs = inst.get_character_sheet(uid)
    assert cs["hp"] == 9
    assert cs["max_hp"] == 9

    result = await api.update_character(created["game_key"], uid, {"hp": 99, "max_hp": 99})

    assert result["ok"] is True
    cs = inst.get_character_sheet(uid)
    assert cs["hp"] == 99
    assert cs["max_hp"] == 99


@pytest.mark.asyncio
async def test_create_game_uses_created_character_before_opening(web_api):
    api, _lorebook, registry, fake_llm, _worlds_dir = web_api

    result = await api.create_game(
        "template_world",
        "模板世界",
        players=[{
            "character_name": "艾琳",
            "race": "精灵",
            "class": "游侠",
            "attributes": {"str": 12},
            "background": "来自银叶林地",
        }],
    )

    assert result["ok"] is True
    inst = registry.get(api._parse_key(result["game_key"]))
    assert inst is not None
    assert [p["character_name"] for p in inst.players.values()] == ["艾琳"]
    assert "艾琳" in fake_llm.calls[-1]["user_message"]
    assert "精灵 游侠" in fake_llm.calls[-1]["user_message"]
    assert result["players"][0]["character_name"] == "艾琳"


@pytest.mark.asyncio
async def test_create_game_rejects_empty_player_list(web_api):
    api, _lorebook, registry, fake_llm, _worlds_dir = web_api

    result = await api.create_game("template_world", "模板世界", players=[])

    assert result["ok"] is False
    assert "至少创建或选择" in result["error"]
    assert registry.list_all() == []
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_unconfigured_model_is_rejected_before_creating_save_data(web_api):
    api, _lorebook, registry, fake_llm, _worlds_dir = web_api
    fake_llm.providers = {
        "fake": SimpleNamespace(
            provider_name="fake",
            base_url="",
            api_key="",
            model_name="",
        ),
    }

    result = await api.create_game(
        "template_world",
        "模板世界",
        players=[{"character_name": "艾琳", "attributes": {"str": 12}}],
    )

    assert result["ok"] is False
    assert result["error_code"] == "llm_not_configured"
    assert result["missing"] == ["base_url", "model", "api_key"]
    assert registry.list_all() == []
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_ai_generation_reports_unconfigured_model_without_calling_it(web_api):
    api, _lorebook, _registry, fake_llm, _worlds_dir = web_api
    fake_llm.providers = {
        "fake": SimpleNamespace(
            provider_name="fake",
            base_url="https://example.invalid/v1",
            api_key="",
            model_name="test-model",
        ),
    }

    result = await api.generate_rule("测试规则", "freeform_fantasy")

    assert result["ok"] is False
    assert result["error_code"] == "llm_not_configured"
    assert result["missing"] == ["api_key"]
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_pay_tag_deducts_gold_immediately(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world",
        "模板世界",
        players=[{
            "character_name": "艾琳",
            "race": "精灵",
            "class": "游侠",
            "attributes": {"str": 12},
            "gold": 30,
        }],
    )
    inst = registry.get(api._parse_key(result["game_key"]))
    assert inst is not None
    uid = next(iter(inst.players))
    cs = inst.players[uid]["character_sheet"]
    cs["gold"] = 30

    # PAY/负 gold_change 直接扣金币，不再挂起待确认
    api._handler._apply_state_update(inst, {
        "players": {uid: {"gold_change": -12}},
    })

    assert inst.players[uid]["character_sheet"]["gold"] == 18
    assert inst.pending_payments == []


@pytest.mark.asyncio
async def test_negative_gold_change_clamps_at_zero(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world",
        "模板世界",
        players=[{
            "character_name": "洛恩",
            "attributes": {"str": 10},
            "gold": 20,
        }],
    )
    inst = registry.get(api._parse_key(result["game_key"]))
    assert inst is not None
    uid = next(iter(inst.players))
    inst.players[uid]["character_sheet"]["gold"] = 20

    # 金币不足时扣到 0，不为负
    api._handler._apply_state_update(inst, {
        "players": {uid: {"gold_change": -50}},
    })

    assert inst.players[uid]["character_sheet"]["gold"] == 0
    assert inst.pending_payments == []


async def _make_game_with_pending(
    web_api,
    *,
    gold=30,
    amount=12,
    payment_id="pay_test1",
    rewards=None,
):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world",
        "模板世界",
        players=[{
            "character_name": "艾琳",
            "race": "精灵",
            "class": "游侠",
            "attributes": {"str": 12},
            "gold": gold,
        }],
    )
    gk = result["game_key"]
    inst = registry.get(api._parse_key(gk))
    uid = next(iter(inst.players))
    inst.players[uid]["character_sheet"]["gold"] = gold
    inst.gm_uid = uid
    inst.pending_payments.append({
        "id": payment_id, "uid": uid, "amount": amount,
        "recipient_uid": uid,
        "rewards": list(rewards or []),
        "reason": "GM 建议支付", "status": "pending", "round": 1,
    })
    return api, gk, inst, uid


@pytest.mark.asyncio
async def test_resolve_payment_accepted_deducts_gold(web_api):
    api, gk, inst, uid = await _make_game_with_pending(web_api, gold=30, amount=12)
    res = await api.resolve_payment(gk, "pay_test1", True, uid)
    assert res["ok"] is True
    assert res["accepted"] is True
    assert inst.players[uid]["character_sheet"]["gold"] == 18
    assert res["payment"]["status"] == "accepted"
    assert inst.pending_payments == []


@pytest.mark.asyncio
async def test_resolve_payment_rejected_adds_health_event(web_api):
    api, gk, inst, uid = await _make_game_with_pending(web_api, gold=30, amount=12)
    res = await api.resolve_payment(gk, "pay_test1", False, uid)
    assert res["ok"] is True
    assert res["accepted"] is False
    # 拒绝不扣金币
    assert inst.players[uid]["character_sheet"]["gold"] == 30
    # 通知 GM：健康事件
    assert any(e.get("code") == "payment_rejected" for e in inst.health_events)
    assert res["payment"]["status"] == "rejected"
    assert inst.pending_payments == []


@pytest.mark.asyncio
async def test_resolve_payment_permission_non_owner_blocked(web_api):
    api, gk, inst, uid = await _make_game_with_pending(web_api, gold=30, amount=12)
    # 非当事玩家、非 GM 不能处理
    res = await api.resolve_payment(gk, "pay_test1", True, "other_user")
    assert res["ok"] is False
    assert "仅 GM 或当事玩家" in res["error"]
    # 状态未变
    assert next(p for p in inst.pending_payments if p["id"] == "pay_test1")["status"] == "pending"
    assert inst.players[uid]["character_sheet"]["gold"] == 30


@pytest.mark.asyncio
async def test_resolve_payment_insufficient_gold(web_api):
    api, gk, inst, uid = await _make_game_with_pending(
        web_api,
        gold=5,
        amount=12,
        rewards=[{"name": "解毒草", "category": ""}],
    )
    res = await api.resolve_payment(gk, "pay_test1", True, uid)
    assert res["ok"] is False
    assert "金币不足" in res["error"]
    assert inst.players[uid]["character_sheet"]["gold"] == 5
    assert not any(
        item.get("name") == "解毒草"
        for item in inst.players[uid]["character_sheet"].get("inventory", [])
    )
    assert next(p for p in inst.pending_payments if p["id"] == "pay_test1")["status"] == "pending"


@pytest.mark.asyncio
async def test_multiplayer_payment_grants_items_to_recipient(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world",
        "多人交易",
        players=[
            {"character_name": "付款者", "attributes": {"str": 12}, "gold": 30},
            {"character_name": "接收者", "attributes": {"str": 12}, "gold": 5},
        ],
    )
    gk = result["game_key"]
    inst = registry.get(api._parse_key(gk))
    payer_uid, recipient_uid = list(inst.players)
    inst.gm_uid = payer_uid
    inst.players[payer_uid]["character_sheet"]["gold"] = 30
    inst.pending_payments.append({
        "id": "pay_multi",
        "uid": payer_uid,
        "amount": 15,
        "recipient_uid": recipient_uid,
        "rewards": [
            {"name": "解毒草", "category": ""},
            {"name": "止血苔", "category": ""},
        ],
        "reason": "替队友购买药草",
        "status": "pending",
        "round": 1,
    })

    resolved = await api.resolve_payment(
        gk, "pay_multi", True, payer_uid
    )
    assert resolved["ok"] is True
    assert inst.players[payer_uid]["character_sheet"]["gold"] == 15
    recipient_inventory = inst.players[recipient_uid]["character_sheet"]["inventory"]
    assert {item["name"] for item in recipient_inventory} >= {"解毒草", "止血苔"}


@pytest.mark.asyncio
async def test_apply_state_update_creates_pending_payment(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world", "模板世界",
        players=[{"character_name": "艾琳", "attributes": {"str": 12}, "gold": 30}],
    )
    inst = registry.get(api._parse_key(result["game_key"]))
    uid = next(iter(inst.players))
    assert inst.pending_payments == []

    api._handler._apply_state_update(inst, {
        "pending_payments": [{
            "uid": uid,
            "amount": 7,
            "recipient_uid": uid,
            "items": ["药水"],
            "reason": "购买药水",
        }],
    })
    assert len(inst.pending_payments) == 1
    pay = inst.pending_payments[0]
    assert pay["uid"] == uid
    assert pay["amount"] == 7
    assert pay["recipient_uid"] == uid
    assert pay["rewards"][0]["name"] == "药水"
    assert pay["status"] == "pending"
    assert pay["id"].startswith("pay_")
    # PAY 不直接扣金币
    assert inst.players[uid]["character_sheet"]["gold"] == 30
    assert not any(
        item.get("name") == "药水"
        for item in inst.players[uid]["character_sheet"].get("inventory", [])
    )


def test_character_card_library_does_not_include_active_game_players(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    inst = registry.get_or_create(("web", "active_game", "bot"))
    inst.world_name = "另一局游戏"
    inst.players["foreign_user"] = {
        "character_name": "不该出现的局内角色",
        "character_sheet": {
            "character_name": "不该出现的局内角色",
            "race": "人类",
            "class": "战士",
            "attributes": {"str": 12},
        },
    }

    api.save_character_card({
        "character_name": "仓库角色",
        "race": "人类",
        "class": "游侠",
        "attributes": {"str": 10},
    })
    result = api.list_character_cards()
    names = [card["character_name"] for card in result["cards"]]

    assert names == ["仓库角色"]


def test_character_schema_is_available_without_active_game(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api

    result = api.character_schema("freeform_fantasy", "zh-CN")

    assert result["ok"] is True
    assert result["rule_meta"]["rule_id"] == "freeform_fantasy"
    assert len(result["rule_attrs"]) == 1
    assert result["rule_attrs"][0] == {
        "key": "str",
        "name": "力量",
        "name_en": "STR",
        "display_name": "力量 (STR)",
        "min": 3,
        "max": 18,
    }
    assert result["rule_attrs_total"] == 60
    assert result["skill_pool"] == ["侦查", "射击"]


def test_character_card_preserves_rule_blueprint_without_runtime_state(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api

    saved = api.save_character_card({
        "character_name": "规则蓝图角色",
        "rule_id": "freeform_fantasy",
        "rule_name": "自由幻想",
        "rule_version": "1.2.3",
        "mechanics": "d20_core",
        "language": "zh-CN",
        "identity": {"pronouns": "她"},
        "attributes": {"str": 12},
        "skills": [{"name": "侦查", "value": 40}],
        "inventory": [{"name": "火把", "quantity": 2}],
        "key_items": [{"name": "旧钥匙"}],
        "currency": {"name": "金币", "amount": 18},
        "portrait": {"kind": "builtin", "id": "freeform_fantasy:2"},
        "hp": 1,
        "max_hp": 30,
        "xp": 999,
        "deceased": True,
        "status": ["中毒"],
    })

    assert saved["ok"] is True
    card = saved["card"]
    assert card["schema_version"] == 2
    assert card["rule_id"] == "freeform_fantasy"
    assert card["rule_version"] == "1.2.3"
    assert card["identity"] == {"pronouns": "她"}
    assert card["inventory"] == [{"name": "火把", "quantity": 2}]
    assert card["key_items"] == [{"name": "旧钥匙"}]
    assert card["currency"] == {"name": "金币", "amount": 18}
    assert card["portrait"] == {"kind": "builtin", "id": "freeform_fantasy:2"}
    assert not ({"hp", "max_hp", "xp", "deceased", "status"} & card.keys())


def test_character_cards_with_same_identity_can_bind_to_different_rules(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    shared = {
        "character_name": "跨规则角色",
        "race": "人类",
        "class": "调查员",
        "background": "同一个角色概念",
    }

    api.save_character_card({**shared, "rule_id": "freeform_fantasy"})
    api.save_character_card({**shared, "rule_id": "freeform_coc"})

    result = api.list_character_cards()
    assert result["total"] == 2
    assert {card["rule_id"] for card in result["cards"]} == {
        "freeform_fantasy", "freeform_coc",
    }


def test_legacy_character_card_remains_readable_as_unbound(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    legacy_card = {
        "id": "legacy_card",
        "character_name": "旧版角色",
        "race": "人类",
        "class": "冒险者",
        "attributes": {"str": 10},
    }
    api._character_cards_path.parent.mkdir(parents=True, exist_ok=True)
    api._character_cards_path.write_text(
        json.dumps([legacy_card], ensure_ascii=False),
        encoding="utf-8",
    )

    result = api.list_character_cards()

    assert result["total"] == 1
    assert result["cards"][0] == legacy_card
    assert "rule_id" not in result["cards"][0]


def test_save_custom_rule_copies_existing_rule_template(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api

    result = api.save_custom_rule({
        "source_rule_id": "freeform_fantasy",
        "rule_id": "custom_test_rule",
        "rule_name": "测试自定义规则",
        "description": "从自由幻想复制的测试规则",
    })

    assert result["ok"] is True
    rules = api.list_rules()["rules"]
    created = next(rule for rule in rules if rule["rule_id"] == "custom_test_rule")
    assert created["rule_name"] == "测试自定义规则"
    assert created["description"] == "从自由幻想复制的测试规则"
    assert created["custom"] is True


def test_save_custom_rule_rejects_unsafe_rule_id(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api

    result = api.save_custom_rule({
        "source_rule_id": "freeform_fantasy",
        "rule_id": "../bad",
        "rule_name": "坏规则",
    })

    assert result["ok"] is False
    assert "规则 ID" in result["error"]

    cn_result = api.save_custom_rule({
        "source_rule_id": "freeform_fantasy",
        "rule_id": "中文规则",
        "rule_name": "中文规则",
    })
    assert cn_result["ok"] is False
    assert "规则 ID" in cn_result["error"]


def test_update_custom_rule_json(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api

    created = api.save_custom_rule({
        "source_rule_id": "freeform_fantasy",
        "rule_id": "custom_edit_rule",
        "rule_name": "编辑前规则",
        "description": "编辑前说明",
    })
    assert created["ok"] is True

    detail = api.get_rule_template("custom_edit_rule")
    assert detail["ok"] is True
    template = detail["rule"]
    template["rule_name"] = "编辑后规则"
    template["description"] = "编辑后说明"
    template["attribute_points"] = 66

    updated = api.update_custom_rule("custom_edit_rule", template)

    assert updated["ok"] is True
    assert updated["rule"]["rule_name"] == "编辑后规则"
    reloaded = api.get_rule_template("custom_edit_rule")["rule"]
    assert reloaded["description"] == "编辑后说明"
    assert reloaded["attribute_points"] == 66
    assert reloaded["custom"] is True


@pytest.mark.asyncio
async def test_generate_rule_from_base_saves_valid_custom_rule(web_api):
    api, _lorebook, _registry, fake_llm, _worlds_dir = web_api

    result = await api.generate_rule("凡人修仙传式低资质散修成长", "freeform_fantasy")

    assert result["ok"] is True
    assert result["rule_id"].startswith("ai_rule_")
    path = api._rules_dir / f"{result['rule_id']}.json"
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["custom"] is True
    assert saved["source_rule_id"] == "freeform_fantasy"
    assert "凡人修仙" in saved["rule_name"]
    assert any("TRPG规则设计师" in c["system_prompt"] for c in fake_llm.calls)


def test_update_builtin_rule_is_rejected(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    detail = api.get_rule_template("freeform_fantasy")

    result = api.update_custom_rule("freeform_fantasy", detail["rule"])

    assert result["ok"] is False
    assert "内置规则" in result["error"]


def test_rule_template_detail_includes_computed_ui_schema(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api

    detail = api.get_rule_template("freeform_fantasy")

    assert detail["ok"] is True
    rule = detail["rule"]
    assert rule["currency_system"]["units"]
    assert rule["resource_schema"][0]["key"] == "hp"
    assert rule["identity_schema"][0]["legacy_field"] == "race"
    assert rule["progression_schema"]["type"]
    assert rule["ui_schema"]["primary_resources"] == ["hp"]


def test_delete_custom_rule_removes_only_custom_rule(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    created = api.save_custom_rule({
        "source_rule_id": "freeform_fantasy",
        "rule_id": "custom_delete_rule",
        "rule_name": "待删除规则",
    })
    assert created["ok"] is True

    deleted = api.delete_custom_rule("custom_delete_rule")

    assert deleted["ok"] is True
    assert api.get_rule_template("custom_delete_rule")["ok"] is False
    assert all(rule["rule_id"] != "custom_delete_rule" for rule in api.list_rules()["rules"])

    builtin = api.delete_custom_rule("freeform_fantasy")
    assert builtin["ok"] is False
    assert "内置规则" in builtin["error"]


@pytest.mark.asyncio
async def test_character_api_exposes_rule_creation_hints(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api

    created = await api.create_game(
        "template_world",
        "模板世界",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    result = api.list_characters(created["game_key"])

    assert result["rule_attrs_total"] == 60
    assert result["rule_meta"]["attr_hint"] == "属性测试提示"
    assert result["rule_meta"]["skill_mode"] == "narrative"
    assert result["rule_meta"]["skill_hint"] == "技能测试提示"
    assert result["rule_meta"]["skill_pools"]["游侠"] == ["侦查", "射击"]


@pytest.mark.asyncio
async def test_character_list_normalizes_legacy_and_resource_hp(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api

    created = await api.create_game(
        "template_world",
        "HP 同步局",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    inst = registry.get(api._parse_key(created["game_key"]))
    stored = next(iter(inst.players.values()))["character_sheet"]
    stored["hp"] = 46
    stored["max_hp"] = 46
    stored["resources"]["hp"]["current"] = 41
    stored["resources"]["hp"]["max"] = 41

    result = api.list_characters(created["game_key"])

    cs = result["players"][0]["character_sheet"]
    assert cs["resources"]["hp"]["current"] == 46
    assert cs["resources"]["hp"]["max"] == 46
    assert stored["resources"]["hp"]["current"] == 46
    assert stored["resources"]["hp"]["max"] == 46


@pytest.mark.asyncio
async def test_player_join_with_same_name_creates_new_seat(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api

    created = await api.create_game(
        "template_world",
        "模板世界",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    inst = registry.get(api._parse_key(created["game_key"]))
    assert len(inst.players) == 1

    joined = await api.create_player(created["game_key"], {"name": "艾琳"})

    assert joined["ok"] is True
    assert not joined.get("reused")
    assert joined["user_id"] != created["players"][0]["user_id"]
    assert len(inst.players) == 2


@pytest.mark.asyncio
async def test_player_join_reuses_only_explicit_user_link(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api

    created = await api.create_game(
        "template_world",
        "模板世界",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    inst = registry.get(api._parse_key(created["game_key"]))
    existing_uid = created["players"][0]["user_id"]

    joined = await api.create_player(created["game_key"], {"user_id": existing_uid, "name": "随便填"})

    assert joined["ok"] is True
    assert joined["reused"] is True
    assert joined["user_id"] == existing_uid
    assert len(inst.players) == 1


@pytest.mark.asyncio
async def test_create_game_binds_gm_to_first_created_player(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    created = await api.create_game(
        "template_world",
        "GM 身份测试",
        players=[
            {"character_name": "艾琳", "attributes": {"str": 10}},
            {"character_name": "洛恩", "attributes": {"str": 11}},
        ],
        gm_uid="web_session_gm",
    )

    inst = registry.get(api._parse_key(created["game_key"]))
    assert created["players"][0]["user_id"] == "web_session_gm"
    assert created["players"][1]["user_id"].startswith("player_")
    assert inst.gm_uid == created["players"][0]["user_id"]


@pytest.mark.asyncio
async def test_character_wizard_update_changes_display_name_and_sheet(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    created = await api.create_game(
        "template_world",
        "车卡测试",
        players=[{"character_name": "冒险者", "attributes": {"str": 10}}],
        gm_uid="web_session_gm",
    )
    uid = created["players"][0]["user_id"]

    updated = await api.update_character(created["game_key"], uid, {
        "character_name": "新名字",
        "race": "精灵",
        "class": "游侠",
        "attributes": {"str": 12},
        "portrait": {"kind": "builtin", "id": "freeform_fantasy:3"},
    })

    inst = registry.get(api._parse_key(created["game_key"]))
    assert updated["ok"] is True
    assert inst.players[uid]["character_name"] == "新名字"
    assert inst.players[uid]["character_sheet"]["race"] == "精灵"
    assert inst.players[uid]["character_sheet"]["class"] == "游侠"
    assert inst.players[uid]["character_sheet"]["portrait"] == {
        "kind": "builtin", "id": "freeform_fantasy:3",
    }


@pytest.mark.asyncio
async def test_npc_portrait_is_explicit_and_persisted(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    created = await api.create_game(
        "template_world",
        "NPC 头像测试",
        players=[{"character_name": "主持人", "attributes": {"str": 10}}],
        gm_uid="web_session_gm",
    )
    inst = registry.get(api._parse_key(created["game_key"]))
    inst.npcs["npc-guide"] = {"name": "向导", "character_name": "向导"}

    before = api.list_characters(created["game_key"])["npcs"][0]
    assert "portrait" not in before

    updated = await api.update_npc_portrait(
        created["game_key"],
        "npc-guide",
        {"kind": "builtin", "id": "freeform_fantasy:5"},
    )
    assert updated == {
        "ok": True,
        "portrait": {"kind": "builtin", "id": "freeform_fantasy:5"},
    }
    assert inst.npcs["npc-guide"]["portrait"] == updated["portrait"]

    reset = await api.update_npc_portrait(created["game_key"], "npc-guide", None)
    assert reset == {"ok": True, "portrait": None}
    assert "portrait" not in inst.npcs["npc-guide"]


@pytest.mark.asyncio
async def test_create_player_allows_overpointed_sheet(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    created = await api.create_game(
        "template_world", "校验测试",
        players=[{"character_name": "合法者", "attributes": {"str": 10}}],
        gm_uid="web_session_gm",
    )
    result = await api.create_player(created["game_key"], {
        "name": "超限者",
        "attributes": {"str": 999},
    }, force_uid="player_over")
    assert result["ok"] is True
    inst = registry.get(api._parse_key(created["game_key"]))
    assert "player_over" in inst.players
    assert inst.players["player_over"]["character_sheet"]["attributes"]["str"] == 999


@pytest.mark.asyncio
async def test_update_character_allows_values_outside_template_suggestion(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    created = await api.create_game(
        "template_world", "回滚测试",
        players=[{"character_name": "冒险者", "attributes": {"str": 10}}],
        gm_uid="web_session_gm",
    )
    uid = created["players"][0]["user_id"]
    inst = registry.get(api._parse_key(created["game_key"]))
    result = await api.update_character(created["game_key"], uid, {
        "attributes": {"str": 999},
        "hp": 77,
        "max_hp": 88,
    })
    assert result["ok"] is True
    inst2 = registry.get(api._parse_key(created["game_key"]))
    sheet = inst2.players[uid]["character_sheet"]
    assert sheet["attributes"].get("str") == 999
    assert sheet["hp"] == 77
    assert sheet["max_hp"] == 88


def test_validate_character_rejects_invalid_class():
    """职业校验：自定义职业放行（仅 warning），合法/空职业无错误。"""
    from pathlib import Path
    from src.rules.rule_system import RuleSystem
    rule = RuleSystem.load(Path("templates/rules/freeform_fantasy.json"))
    attrs = {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}
    # 自定义职业放行（不再拒绝）
    errors = rule.validate_character({"class": "超级赛亚人", "attributes": attrs})
    assert not any("职业" in e for e in errors)
    # 合法职业通过（无职业相关错误）
    ok_errors = rule.validate_character({"class": "战士", "attributes": attrs})
    assert not any("职业" in e for e in ok_errors)
    # 空职业跳过校验
    empty_errors = rule.validate_character({"class": "", "attributes": attrs})
    assert not any("职业" in e for e in empty_errors)


@pytest.mark.asyncio
async def test_update_character_rejects_overlong_bio(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    created = await api.create_game(
        "template_world", "bio测试",
        players=[{"character_name": "冒险者", "attributes": {"str": 10}}],
        gm_uid="web_session_gm",
    )
    uid = created["players"][0]["user_id"]
    accepted = await api.update_character(created["game_key"], uid, {
        "background": "字" * 4000,
    })
    assert accepted["ok"] is True
    result = await api.update_character(created["game_key"], uid, {
        "background": "字" * 8001,
    })
    assert result["ok"] is False
    assert "背景过长" in result["error"]
    inst = registry.get(api._parse_key(created["game_key"]))
    assert inst.players[uid]["character_sheet"].get("background", "") == "字" * 4000


@pytest.mark.asyncio
async def test_game_detail_exposes_multiplayer_status(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api

    created = await api.create_game(
        "template_world",
        "模板世界",
        solo=False,
        players=[
            {"character_name": "艾琳", "attributes": {"str": 10}},
            {"character_name": "洛恩", "attributes": {"str": 10}},
        ],
    )
    inst = registry.get(api._parse_key(created["game_key"]))
    first_uid = created["players"][0]["user_id"]
    await inst.add_action(first_uid, "我观察门口")
    inst.last_token_budget_bump = {"kind": "narrative", "from": 2048, "to": 4096}

    detail = api.game_detail(created["game_key"])
    status = api.multiplayer_status(created["game_key"])

    assert detail["solo_mode"] is False
    assert detail["token_budget_bump"] == {"kind": "narrative", "from": 2048, "to": 4096}
    assert detail["multiplayer"]["ready_count"] == 1
    assert status["ok"] is True
    assert status["waiting_players"][0]["character_name"] == "洛恩"


@pytest.mark.asyncio
async def test_game_server_roll_uses_world_rule(web_api):
    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    created = await api.create_game(
        "template_world",
        "骰子测试",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )

    result = api.roll_for_game(created["game_key"])

    assert result["ok"] is True
    assert result["dice_system"] == "d20"
    assert 1 <= result["value"] <= 20


@pytest.mark.asyncio
async def test_blank_lorebook_from_template_keeps_starter_lorebook_empty(web_api):
    api, lorebook, _registry, _fake_llm, worlds_dir = web_api

    result = await api.create_game(
        "template_world_blank_case",
        "空白副本",
        create_lorebook=True,
        blank_lorebook=True,
        source_world_id="template_world",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )

    assert result["ok"] is True
    template_data = json.loads((worlds_dir / "template_world_blank_case.json").read_text(encoding="utf-8"))
    assert template_data["starter_lorebook"] == []
    assert template_data["_diceframe_managed"] == "game"
    assert template_data["_diceframe_owner_game"] == result["game_key"]
    assert lorebook.list_entries("template_world_blank_case") == []

    deleted = api.delete_game(result["game_key"])

    assert deleted == {"ok": True, "world_template_removed": True}
    assert not (worlds_dir / "template_world_blank_case.json").exists()


@pytest.mark.asyncio
async def test_copy_lorebook_copies_selected_source_entries(web_api):
    api, lorebook, _registry, _fake_llm, _worlds_dir = web_api
    lorebook.create_world("source_book", "来源世界书")
    lorebook.add_entry({
        "id": "source_book_npc",
        "world_id": "source_book",
        "name": "抄录者",
        "type": "npc",
        "keywords": ["抄录者"],
        "content": "被复制的条目",
        "tier": "core",
    })

    result = await api.create_game(
        "template_world_copy_case",
        "复制副本",
        create_lorebook=True,
        blank_lorebook=True,
        source_world_id="template_world",
        lorebook_world_id="source_book",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )

    assert result["ok"] is True
    entries = lorebook.list_entries("template_world_copy_case")
    assert [entry["name"] for entry in entries] == ["抄录者"]
    assert entries[0]["world_id"] == "template_world_copy_case"


def test_cleanup_orphan_legacy_copy_template_preserves_referenced_copy(web_api):
    api, _lorebook, registry, _fake_llm, worlds_dir = web_api
    world_id = "template_world_copy_1785176322339"
    path = worlds_dir / f"{world_id}.json"
    path.write_text(json.dumps({
        "world_id": world_id,
        "world_name": "贝克兰德（复制世界书）",
        "custom": True,
    }, ensure_ascii=False), encoding="utf-8")
    instance = SimpleNamespace(
        game_key=("web", "copy-user", "web_bot"),
        world_id=world_id,
    )
    registry.register(instance)

    assert api.cleanup_orphan_game_templates() == 0
    assert path.exists()

    registry.remove(instance.game_key)
    assert api.cleanup_orphan_game_templates() == 1
    assert not path.exists()


def test_cleanup_does_not_remove_user_template_that_only_looks_like_copy(web_api):
    api, _lorebook, _registry, _fake_llm, worlds_dir = web_api
    world_id = "my_world_copy_1785176322339"
    path = worlds_dir / f"{world_id}.json"
    path.write_text(json.dumps({
        "world_id": world_id,
        "world_name": "我主动保存的世界",
        "custom": True,
    }, ensure_ascii=False), encoding="utf-8")

    assert api.cleanup_orphan_game_templates() == 0
    assert path.exists()


@pytest.mark.asyncio
async def test_create_from_seed_requires_original_save_and_reuses_world(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api

    original = await api.create_game(
        "template_world",
        "原始世界",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    seed_code = original["seed_code"]

    restarted = await api.create_from_seed(
        seed_code,
        solo=True,
        players=[{"character_name": "洛恩", "attributes": {"str": 11}}],
        gm_uid="web_restart_gm",
    )

    assert restarted["ok"] is True
    assert restarted["world_id"] == "template_world"
    assert restarted["seed_code"] == seed_code
    inst = registry.get(api._parse_key(restarted["game_key"]))
    assert [p["character_name"] for p in inst.players.values()] == ["洛恩"]
    assert inst.gm_uid == "web_restart_gm"

    empty_players = await api.create_from_seed(seed_code, solo=True, players=[])
    assert empty_players["ok"] is False
    assert "至少创建或选择" in empty_players["error"]

    missing = await api.create_from_seed("missing-seed-code", players=[])
    assert missing["ok"] is False
    assert "未找到重开引用码" in missing["error"]


@pytest.mark.asyncio
async def test_restart_game_without_players_is_rejected(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    inst = registry.get_or_create(("web", "empty_game", "web_bot"))
    inst.world_id = "template_world"
    inst.world_name = "模板世界"

    result = await api.restart_game("web|empty_game|web_bot")

    assert result["ok"] is False
    assert "没有角色" in result["error"]
    assert inst.players == {}


@pytest.mark.asyncio
async def test_switch_world_accepts_lorebook_only_world(web_api):
    api, lorebook, registry, _fake_llm, _worlds_dir = web_api

    created = await api.create_game(
        "template_world",
        "模板世界",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    lorebook.create_world("custom_book_only", "只在世界书库里的世界", description="没有模板 JSON")

    result = await api.switch_world(created["game_key"], "custom_book_only")

    assert result["ok"] is True
    assert result["world_id"] == "custom_book_only"
    assert result["world_name"] == "只在世界书库里的世界"
    inst = registry.get(api._parse_key(created["game_key"]))
    assert inst.world_id == "custom_book_only"
    assert inst.world_name == "只在世界书库里的世界"


def test_can_modify_character_allows_owner():
    assert can_modify_character("p1", "p1", "gm") is True


def test_can_modify_character_allows_gm():
    assert can_modify_character("gm", "p1", "gm") is True


def test_can_modify_character_rejects_other_player():
    assert can_modify_character("p2", "p1", "gm") is False


def test_can_modify_character_rejects_empty_session():
    assert can_modify_character("", "p1", "gm") is False


def test_default_quick_actions_by_class():
    assert "攻击" in GameHandler._default_quick_actions_by_class("战士")
    assert "施法" in GameHandler._default_quick_actions_by_class("法师")
    assert "潜行" in GameHandler._default_quick_actions_by_class("盗贼")
    assert "治疗" in GameHandler._default_quick_actions_by_class("牧师")
    assert "射击" in GameHandler._default_quick_actions_by_class("游侠")
    assert "观察" in GameHandler._default_quick_actions_by_class("未知职业")


@pytest.mark.asyncio
async def test_created_character_has_generic_rule_fields(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api

    created = await api.create_game(
        "template_world",
        "schema case",
        players=[{"character_name": "Aerin", "race": "Elf", "class": "Rogue", "attributes": {"str": 10}, "gold": 12}],
    )

    inst = registry.get(api._parse_key(created["game_key"]))
    uid = created["players"][0]["user_id"]
    sheet = inst.players[uid]["character_sheet"]

    assert sheet["identity"]["origin"] == "Elf"
    assert sheet["identity"]["archetype"] == "Rogue"
    assert sheet["resources"]["hp"]["current"] == sheet["hp"]
    assert sheet["currency"]["amount"] == 12
    assert sheet["progression"]["level"] == 1


def test_character_api_exposes_generic_rule_meta(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    inst = registry.get_or_create(("web", "meta", "bot"))
    inst.world_id = "template_world"

    result = api.list_characters("web|meta|bot")

    assert result["rule_meta"]["conflict_model"]["type"] == "hp_based"
    assert result["rule_meta"]["currency_system"]["units"]
    assert result["rule_meta"]["resource_schema"][0]["key"] == "hp"
    assert result["rule_meta"]["identity_schema"][0]["legacy_field"] == "race"


@pytest.mark.asyncio
async def test_game_health_api_marks_event(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    inst = registry.get_or_create(("web", "health_api", "bot"))
    event = record_health_event(inst, "memory", "MEMORY_WRITE_FAILED", "warning", "Memory write failed")

    payload = api.game_health("web|health_api|bot")
    marked = await api.mark_game_health_event("web|health_api|bot", event["id"], resolved=True)

    assert payload["ok"] is True
    assert payload["events"][0]["code"] == "MEMORY_WRITE_FAILED"
    assert marked["ok"] is True
    assert api.game_health("web|health_api|bot")["events"] == []


@pytest.mark.asyncio
async def test_rollback_round_pops_last_log_entry_and_reports_empty(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    result = await api.create_game(
        "template_world",
        "模板世界",
        players=[{
            "character_name": "艾琳",
            "race": "精灵",
            "class": "游侠",
            "attributes": {"str": 12},
        }],
    )
    gk = result["game_key"]
    inst = registry.get(api._parse_key(gk))
    uid = next(iter(inst.players))
    sheet = inst.get_character_sheet(uid)
    sheet["luck"] = 28
    sheet["resources"] = {"luck": {"current": 28, "max": 99}}
    inst.round_number = 3
    inst.log.append({
        "round": 3,
        "round_start_snapshot": {uid: {"luck": 30, "resources": {"luck": {"current": 30, "max": 99}}}},
        "pre_state_snapshot": {uid: {"luck": 28, "resources": {"luck": {"current": 28, "max": 99}}}},
    })

    rolled = await api.rollback_round(gk)

    assert rolled["ok"] is True
    assert len(inst.log) == 1  # 我加的 round3 已 pop，create_game 的开场 log 仍在
    assert inst.round_number == 3
    assert inst.get_character_sheet(uid)["luck"] == 30
    assert inst.get_character_sheet(uid)["resources"]["luck"]["current"] == 30

    second = await api.rollback_round(gk)  # 撤回开场
    assert second["ok"] is True
    assert inst.log == []

    empty = await api.rollback_round(gk)
    assert empty["ok"] is False
    assert "没有可撤回" in empty["error"]
