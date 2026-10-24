"""GAL 轨道 P8：角色立绘 / 表情差分 / 音色绑定。

契约：
1) NPC 绑定走 GM 权威路径，字段落 ``inst.npcs``，投影（GM 视角）带出；
2) 校验 fail closed：非法 ref / 表情 label / 音色形状一律拒绝；
3) 音色绑定失效（profile 已删）时投影省略（保持未配置）。
"""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from webapi_harness import web_api
from src.webui.services.characters import _project_npc_bindings


def _portrait_b64(size: tuple[int, int] = (200, 500)) -> str:
    image = Image.new("RGBA", size, (10, 20, 30, 0))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


async def _game_with_npc(api, registry):
    created = await api.create_game(
        "template_world",
        "立绘绑定测试",
        players=[{"character_name": "主持人", "attributes": {"str": 10}}],
        gm_uid="web_session_gm",
    )
    inst = registry.get(api._parse_key(created["game_key"]))
    inst.npcs["heroine"] = {"name": "女主", "character_name": "女主"}
    return created["game_key"], inst


@pytest.mark.asyncio
async def test_npc_standing_portrait_and_expressions_roundtrip(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    game_key, inst = await _game_with_npc(api, registry)
    uploaded = api.save_standing_portrait_upload(_portrait_b64(), "heroine.png")
    assert uploaded["ok"] is True
    ref = uploaded["standing_portrait"]

    result = await api.update_npc_standing_portrait(
        game_key, "heroine",
        {"kind": "upload", "asset_id": ref["asset_id"]},
        {"smile": {"kind": "upload", "asset_id": ref["asset_id"]}},
    )
    assert result["ok"] is True
    npc = inst.npcs["heroine"]
    assert npc["standing_portrait"] == ref
    assert npc["standing_portrait_expressions"]["smile"] == ref

    projected = api.list_characters(game_key, viewer_is_gm=True)["npcs"][0]
    assert projected["standing_portrait"] == ref
    assert projected["standing_portrait_expressions"]["smile"] == ref

    cleared = await api.update_npc_standing_portrait(game_key, "heroine", None, None)
    assert cleared["ok"] is True
    assert "standing_portrait" not in inst.npcs["heroine"]
    assert "standing_portrait_expressions" not in inst.npcs["heroine"]


@pytest.mark.asyncio
async def test_standing_portrait_validation_fails_closed(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    game_key, inst = await _game_with_npc(api, registry)
    uploaded = api.save_standing_portrait_upload(_portrait_b64())
    asset_id = uploaded["standing_portrait"]["asset_id"]

    # 不存在的资产 → 拒绝
    missing = await api.update_npc_standing_portrait(
        game_key, "heroine", {"kind": "upload", "asset_id": "0" * 64},
    )
    assert missing["ok"] is False and "不存在" in missing["error"]
    assert "standing_portrait" not in inst.npcs["heroine"]

    # 非法表情 label → 拒绝（整个更新 fail closed，主图也不落）
    bad_label = await api.update_npc_standing_portrait(
        game_key, "heroine",
        {"kind": "upload", "asset_id": asset_id},
        {"Happy Face": {"kind": "upload", "asset_id": asset_id}},
    )
    assert bad_label["ok"] is False and "表情标签无效" in bad_label["error"]
    assert "standing_portrait" not in inst.npcs["heroine"]

    # 差分超过 16 条 → 拒绝
    overflow = await api.update_npc_standing_portrait(
        game_key, "heroine",
        {"kind": "upload", "asset_id": asset_id},
        {f"exp{i}": {"kind": "upload", "asset_id": asset_id} for i in range(17)},
    )
    assert overflow["ok"] is False and "16" in overflow["error"]


@pytest.mark.asyncio
async def test_npc_voice_binding_shape_and_roundtrip(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    game_key, inst = await _game_with_npc(api, registry)

    invalid = await api.update_npc_voice(
        game_key, "heroine", "personal:not-a-real-id",
    )
    assert invalid["ok"] is False and "个人音色" in invalid["error"]

    valid_id = "personal:" + "ab" * 16
    bound = await api.update_npc_voice(game_key, "heroine", valid_id)
    assert bound == {"ok": True, "voice_profile": valid_id}
    projected = api.list_characters(game_key, viewer_is_gm=True)["npcs"][0]
    # web_api fixture 无 speech 服务时存在性未知 → 保留；合成端仍 fail closed。
    assert projected.get("voice_profile") == valid_id

    cleared = await api.update_npc_voice(game_key, "heroine", None)
    assert cleared == {"ok": True, "voice_profile": None}
    assert "voice_profile" not in inst.npcs["heroine"]


@pytest.mark.asyncio
async def test_player_character_edit_accepts_bindings(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    game_key, inst = await _game_with_npc(api, registry)
    uploaded = api.save_standing_portrait_upload(_portrait_b64())
    ref = uploaded["standing_portrait"]
    gm_uid = "web_session_gm"

    result = await api.update_character(
        game_key, gm_uid,
        {"standing_portrait": ref, "voice_profile": "zh-CN-XiaoxiaoNeural"},
    )
    assert result.get("ok") is True
    cs = inst.get_character_sheet(gm_uid)
    assert cs["standing_portrait"] == ref
    assert cs["voice_profile"] == "zh-CN-XiaoxiaoNeural"

    # 非法引用在玩家编辑路径同样 fail closed
    rejected = await api.update_character(
        game_key, gm_uid, {"standing_portrait": {"kind": "upload", "asset_id": "0" * 64}},
    )
    assert rejected.get("ok") is False


def test_projection_drops_stale_personal_voice_binding():
    voice = "personal:" + "cd" * 16
    row = {"voice_profile": voice}

    # 检查器说已删除 → 省略（失效引用保持未配置）
    deps = SimpleNamespace(voice_profile_exists=lambda _v: False)
    _project_npc_bindings(row, deps)
    assert "voice_profile" not in row

    # 仍存在 → 保留
    row2 = {"voice_profile": voice}
    deps_ok = SimpleNamespace(voice_profile_exists=lambda _v: True)
    _project_npc_bindings(row2, deps_ok)
    assert row2["voice_profile"] == voice

    # 非个人音色（内置/插件）不做存在性省略
    row3 = {"voice_profile": "zh-CN-XiaoxiaoNeural"}
    _project_npc_bindings(row3, deps)
    assert row3["voice_profile"] == "zh-CN-XiaoxiaoNeural"
