"""WebUI 角色与加入链路测试（自 test_webui_create_flow 拆分）。"""

from __future__ import annotations

import json
from copy import deepcopy
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

from webapi_harness import FakeLLMClient, web_api, write_world

def test_session_rebind_persists_restored_player_identity(tmp_path):
    manager = SessionManager(tmp_path)
    token, original_uid = manager.get_or_create(None)
    assert original_uid.startswith("web_")

    manager.rebind(token, "player_restored")

    reloaded = SessionManager(tmp_path)
    assert reloaded.get_or_create(token) == (token, "player_restored")


@pytest.mark.asyncio
async def test_professional_character_is_rederived_bound_and_saved_without_field_collision(
    web_api,
):
    api, _lorebook, registry, _fake_llm, worlds_dir = web_api
    (api._rules_dir / "dnd2024_srd.json").write_text(
        json.dumps({
            "rule_id": "dnd2024_srd",
            "rule_name": "5E 2024 SRD 专业规则",
            "dice_system": "d20",
            "combat_model": "hp_based",
            "runtime": {"id": "core:dnd2024", "minimum_version": 1},
            "attributes": [
                {"key": key, "name": key.upper(), "min": 3, "max": 20}
                for key in ("str", "dex", "con", "int", "wis", "cha")
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    write_world(worlds_dir, "dnd2024_world", default_rule="dnd2024_srd")
    choices = api.ruleset_builder_choices("dnd2024_srd", {"locale": "zh-CN"}, "zh-CN")
    preset = choices["choices"]["quick_presets"][0]
    finalized = api.ruleset_builder_finalize(
        "dnd2024_srd",
        {**preset["draft"], "locale": "zh-CN", "name": "边界测试者"},
        "zh-CN",
    )["character"]
    finalized["hp"] = 999
    finalized["armor_class"] = 999
    finalized["attributes"]["str"] = 99

    created = await api.create_game(
        "dnd2024_world",
        "专业规则测试",
        rule_id="dnd2024_srd",
        players=[finalized],
    )

    assert created["ok"] is True
    instance = registry.get(api._parse_key(created["game_key"]))
    assert instance is not None
    sheet = instance.get_character_sheet(next(iter(instance.players)))
    canonical = sheet["ruleset_character"]
    assert sheet["hp"] == canonical["resources"]["hp"] != 999
    assert sheet["armor_class"] == canonical["derived"]["armor_class"] != 999
    assert sheet["attributes"]["str"] == canonical["abilities"]["str"] != 99
    assert isinstance(sheet["equipment"], list)
    assert isinstance(canonical["equipment"], dict)
    assert instance.ruleset_runtime["id"] == "core:dnd2024"
    cards = api.list_character_cards()["cards"]
    assert cards[-1]["ruleset_character"]["rule_binding"]["content_version"] == (
        "srd-5.2.1+r5"
    )


@pytest.mark.asyncio
async def test_professional_seed_restart_keeps_rule_and_prevalidates_before_mutation(
    web_api,
):
    api, _lorebook, registry, _fake_llm, worlds_dir = web_api
    (api._rules_dir / "dnd2024_srd.json").write_text(
        json.dumps({
            "rule_id": "dnd2024_srd",
            "rule_name": "5E 2024 SRD 专业规则",
            "dice_system": "d20",
            "runtime": {"id": "core:dnd2024", "minimum_version": 1},
            "attributes": [
                {"key": key, "name": key.upper(), "min": 3, "max": 20}
                for key in ("str", "dex", "con", "int", "wis", "cha")
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    write_world(worlds_dir, "dnd2024_seed_world", default_rule="dnd2024_srd")
    preset = api.ruleset_builder_choices(
        "dnd2024_srd", {"locale": "zh-CN"}, "zh-CN",
    )["choices"]["quick_presets"][0]
    finalized = api.ruleset_builder_finalize(
        "dnd2024_srd",
        {**preset["draft"], "locale": "zh-CN", "name": "种子测试者"},
        "zh-CN",
    )["character"]
    original = await api.create_game(
        "dnd2024_seed_world",
        "专业规则种子",
        rule_id="dnd2024_srd",
        players=[finalized],
    )
    before_keys = {inst.game_key for inst in registry.list_all()}

    invalid = deepcopy(finalized)
    invalid["ruleset_character"]["rule_binding"]["runtime_version"] = 999
    rejected = await api.create_from_seed(original["seed_code"], players=[invalid])

    assert rejected["ok"] is False
    assert rejected["error_code"] == "INVALID_PROFESSIONAL_CHARACTER"
    assert {inst.game_key for inst in registry.list_all()} == before_keys

    finalized["hp"] = 999
    restarted = await api.create_from_seed(
        original["seed_code"], players=[finalized], gm_uid="seed_gm",
    )

    assert restarted["ok"] is True
    instance = registry.get(api._parse_key(restarted["game_key"]))
    assert instance.rule_id == "dnd2024_srd"
    assert instance.ruleset_runtime["id"] == "core:dnd2024"
    assert instance.get_character_sheet("seed_gm")["hp"] != 999


@pytest.mark.asyncio
async def test_professional_seed_restart_migrates_known_unreleased_adventure_binding(
    web_api,
):
    api, _lorebook, registry, _fake_llm, worlds_dir = web_api
    (api._rules_dir / "dnd2024_srd.json").write_text(
        json.dumps({
            "rule_id": "dnd2024_srd",
            "rule_name": "5E 2024 SRD 专业规则",
            "dice_system": "d20",
            "runtime": {"id": "core:dnd2024", "minimum_version": 1},
            "attributes": [
                {"key": key, "name": key.upper(), "min": 3, "max": 20}
                for key in ("str", "dex", "con", "int", "wis", "cha")
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    write_world(worlds_dir, "dnd2024_adventure_world", default_rule="dnd2024_srd")
    preset = api.ruleset_builder_choices(
        "dnd2024_srd", {"locale": "zh-CN"}, "zh-CN",
    )["choices"]["quick_presets"][0]
    character = api.ruleset_builder_finalize(
        "dnd2024_srd",
        {**preset["draft"], "locale": "zh-CN", "name": "灰沼重开者"},
        "zh-CN",
    )["character"]
    original = await api.create_game(
        "dnd2024_adventure_world",
        "灰沼兼容重开",
        rule_id="dnd2024_srd",
        adventure_id="core:lanterns_of_greymoor",
        players=[character],
    )
    assert original["ok"] is True
    original_instance = registry.get(api._parse_key(original["game_key"]))
    current_binding = deepcopy(original_instance.adventure_binding)
    old_digest = (
        "sha256:363c6786c0e9460ec911d85460c49b610addf8e86cc86d136538daee24d6740c"
    )
    original_instance.adventure_binding["content_digest"] = old_digest
    await registry.save(original_instance)

    restarted = await api.create_from_seed(
        original["seed_code"], players=[character], gm_uid="adventure_seed_gm",
    )

    assert restarted["ok"] is True
    assert original_instance.adventure_binding == current_binding
    recreated = registry.get(api._parse_key(restarted["game_key"]))
    assert recreated.adventure_binding == current_binding


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
    write_world(worlds_dir, "coc_world", default_rule="freeform_coc")

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
    assert result["ruleset_runtime"]["id"] == "core:legacy"


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
async def test_player_join_rejects_new_seat_when_game_is_full(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    created = await api.create_game(
        "template_world",
        "满员测试",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    inst = registry.get(api._parse_key(created["game_key"]))
    inst.max_players = 1

    rejected = await api.create_player(created["game_key"], {"name": "洛恩"})
    restored = await api.create_player(created["game_key"], {
        "user_id": created["players"][0]["user_id"],
    })

    assert rejected == {
        "ok": False,
        "error": "房间已满（最多 1 人）",
        "error_code": "game_room_full",
    }
    assert restored["ok"] is True
    assert restored["reused"] is True
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

    cleared = await api.update_character(created["game_key"], uid, {"portrait": None})
    assert cleared["ok"] is True
    assert "portrait" not in inst.players[uid]["character_sheet"]


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


def test_can_modify_character_allows_owner():
    assert can_modify_character("p1", "p1", "gm") is True


def test_can_modify_character_allows_gm():
    assert can_modify_character("gm", "p1", "gm") is True


def test_can_modify_character_rejects_other_player():
    assert can_modify_character("p2", "p1", "gm") is False


def test_can_modify_character_rejects_empty_session():
    assert can_modify_character("", "p1", "gm") is False


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


def test_character_api_localizes_persisted_lorebook_npcs_for_game_language(web_api):
    api, lorebook, registry, _fake_llm, worlds_dir = web_api
    world_id = "localized_character_world"
    write_world(worlds_dir, world_id, starter_lorebook=[{
        "id": "npc_guide", "name": "向导", "type": "npc",
        "keywords": ["向导"], "content": "中文介绍", "tier": "core",
    }])
    core_path = worlds_dir / f"{world_id}.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    core.update({"world_schema_version": 2, "default_locale": "zh-CN"})
    core_path.write_text(json.dumps(core, ensure_ascii=False), encoding="utf-8")
    locale_path = worlds_dir / "locales" / "en" / f"{world_id}.json"
    locale_path.parent.mkdir(parents=True, exist_ok=True)
    locale_path.write_text(json.dumps({
        "locale_schema_version": 1,
        "locale": "en",
        "target": {"kind": "world", "id": world_id},
        "fields": {"world_name": "Localized Character World"},
        "starter_lorebook": {
            "npc_guide": {
                "name": "Old Guide",
                "keywords": ["guide"],
                "content": "English introduction",
            },
        },
    }), encoding="utf-8")
    lorebook.create_world(world_id, "本地化角色世界")
    lorebook.add_entry({
        "id": "npc_guide", "world_id": world_id, "name": "向导",
        "type": "npc", "keywords": ["向导"], "content": "中文介绍",
        "tier": "core",
    })
    instance = registry.get_or_create(("web", "localized-npcs", "bot"))
    instance.world_id = world_id
    instance.language = "en"

    result = api.list_characters("web|localized-npcs|bot")

    assert result["npcs"][0]["npc_id"] == "npc_guide"
    assert result["npcs"][0]["name"] == "Old Guide"
    assert result["npcs"][0]["content"] == "English introduction"
    assert result["npcs"][0]["status"] == "Lorebook"


@pytest.mark.asyncio
async def test_character_skill_effect_round_trip(web_api):
    """技能效果说明（effect）在保存 → 重新读取 → 再次编辑后都必须保留。"""

    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    created = await api.create_game(
        "template_world",
        "技能效果测试",
        players=[{"character_name": "冒险者", "attributes": {"str": 10}}],
        gm_uid="web_session_gm",
    )
    uid = created["players"][0]["user_id"]
    effect = "向目标发射火球，造成火焰伤害。"

    saved = await api.update_character(created["game_key"], uid, {
        "skills": [
            {"name": "火焰球", "value": 80, "effect": effect},
            {"name": "侦查", "value": 45},
        ],
    })
    assert saved["ok"] is True
    inst = registry.get(api._parse_key(created["game_key"]))
    assert inst.players[uid]["character_sheet"]["skills"] == [
        {"name": "火焰球", "value": 80, "effect": effect},
        {"name": "侦查", "value": 45},
    ]

    # 之后编辑其它字段（不提技能）也不能让 effect 消失或被清空。
    again = await api.update_character(created["game_key"], uid, {"level": 2})
    assert again["ok"] is True
    skills = inst.players[uid]["character_sheet"]["skills"]
    assert skills[0]["effect"] == effect
    assert "effect" not in skills[1]
