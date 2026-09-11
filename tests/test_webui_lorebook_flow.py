"""WebUI lorebook 生成与复制测试（自 test_webui_create_flow 拆分）。"""

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
                        "visibility": "public",
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
    by_name = {e["name"]: e for e in entries}
    # AI 标了 public 的常识条目写入 canonical 标记；未标的 fail-closed 成 GM 秘密
    assert by_name["黑港城"]["visible_to"] == ["*"]
    assert by_name["银钥会"].get("visible_to", []) == []
    assert all("visibility" not in e for e in entries)
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


@pytest.mark.asyncio
async def test_update_entry_accepts_frontend_payload_with_reserved_word_fields(web_api):
    """回归：前端保存已有条目会整份提交（含 order/group），路由必须能落盘。

    修复前 SET 子句列名未加引号，UPDATE 拼出 `SET "order" = ?` 的反例
    （裸 `order = ?`）→ sqlite3.OperationalError: near "order" → 路由 500，
    用户看到的就是「世界书保存按钮失效」。
    """
    api, lorebook, _registry, _llm, _worlds = web_api
    lorebook.create_world("edit_world", "编辑世界")
    lorebook.add_entry({
        "id": "npc_herbalist", "world_id": "edit_world", "name": "草药师",
        "type": "npc", "keywords": ["草药"], "content": "旧内容",
    })

    api.update_entry("npc_herbalist", {
        "id": "npc_herbalist", "world_id": "edit_world", "name": "草药师", "type": "npc",
        "keywords": ["草药", "药草"], "content": "新内容",
        "visible_to": [], "connected_to": [], "triggers_recursive": [],
        "order": 42, "group": "NPC", "group_weight": 3, "probability": 80,
    })

    entry = lorebook.get_entry("npc_herbalist")
    assert entry["content"] == "新内容"
    assert entry["keywords"] == ["草药", "药草"]
    assert entry["order"] == 42
    assert entry["group"] == "NPC"
    assert entry["group_weight"] == 3


