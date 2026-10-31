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

    blocked = api.delete_standing_portrait(ref["asset_id"])
    assert blocked["ok"] is False and "角色引用" in blocked["error"]

    cleared = await api.update_npc_standing_portrait(game_key, "heroine", None, None)
    assert cleared["ok"] is True
    assert "standing_portrait" not in inst.npcs["heroine"]
    assert "standing_portrait_expressions" not in inst.npcs["heroine"]
    assert api.delete_standing_portrait(ref["asset_id"])["ok"] is True


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


@pytest.mark.asyncio
async def test_player_standing_portrait_partial_updates_preserve_other_field(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    game_key, inst = await _game_with_npc(api, registry)
    uploaded = api.save_standing_portrait_upload(_portrait_b64())
    ref = uploaded["standing_portrait"]
    gm_uid = "web_session_gm"

    initial = await api.update_character(
        game_key,
        gm_uid,
        {
            "standing_portrait": ref,
            "standing_portrait_expressions": {"smile": ref},
        },
    )
    assert initial["ok"] is True

    # Updating either field alone must not clear its sibling.
    assert (await api.update_character(
        game_key, gm_uid, {"standing_portrait": ref},
    ))["ok"] is True
    sheet = inst.get_character_sheet(gm_uid)
    assert sheet["standing_portrait"] == ref
    assert sheet["standing_portrait_expressions"] == {"smile": ref}

    assert (await api.update_character(
        game_key, gm_uid, {"standing_portrait_expressions": {"sad": ref}},
    ))["ok"] is True
    sheet = inst.get_character_sheet(gm_uid)
    assert sheet["standing_portrait"] == ref
    assert sheet["standing_portrait_expressions"] == {"sad": ref}

    # Explicit None / {} retains the sibling while clearing only the named field.
    assert (await api.update_character(
        game_key, gm_uid, {"standing_portrait": None},
    ))["ok"] is True
    sheet = inst.get_character_sheet(gm_uid)
    assert "standing_portrait" not in sheet
    assert sheet["standing_portrait_expressions"] == {"sad": ref}

    assert (await api.update_character(
        game_key, gm_uid, {"standing_portrait_expressions": {}},
    ))["ok"] is True
    sheet = inst.get_character_sheet(gm_uid)
    assert "standing_portrait" not in sheet
    assert "standing_portrait_expressions" not in sheet


@pytest.mark.asyncio
async def test_player_standing_portrait_partial_update_is_atomic_on_invalid_field(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    game_key, inst = await _game_with_npc(api, registry)
    uploaded = api.save_standing_portrait_upload(_portrait_b64())
    ref = uploaded["standing_portrait"]
    gm_uid = "web_session_gm"
    await api.update_character(
        game_key,
        gm_uid,
        {
            "standing_portrait": ref,
            "standing_portrait_expressions": {"smile": ref},
        },
    )
    before = dict(inst.get_character_sheet(gm_uid))

    rejected = await api.update_character(
        game_key,
        gm_uid,
        {
            "standing_portrait": {"kind": "upload", "asset_id": "0" * 64},
            "standing_portrait_expressions": {"sad": ref},
        },
    )

    assert rejected["ok"] is False
    after = inst.get_character_sheet(gm_uid)
    assert after["standing_portrait"] == before["standing_portrait"]
    assert after["standing_portrait_expressions"] == before["standing_portrait_expressions"]


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


@pytest.mark.asyncio
async def test_npc_standing_portrait_partial_updates_preserve_other_field(web_api):
    """Same contract as the player sheet: an omitted field is left unchanged."""

    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    game_key, inst = await _game_with_npc(api, registry)
    ref = api.save_standing_portrait_upload(_portrait_b64())["standing_portrait"]

    assert (await api.update_npc_standing_portrait(
        game_key, "heroine", ref, {"smile": ref},
    ))["ok"] is True
    assert (await api.update_npc_standing_portrait(game_key, "heroine", ref))["ok"] is True
    assert inst.npcs["heroine"]["standing_portrait_expressions"] == {"smile": ref}
    assert (await api.update_npc_standing_portrait(
        game_key, "heroine", expressions={"sad": ref},
    ))["ok"] is True
    assert inst.npcs["heroine"]["standing_portrait"] == ref
    assert inst.npcs["heroine"]["standing_portrait_expressions"] == {"sad": ref}

    # Explicit None clears only the named field; an empty request changes nothing.
    assert (await api.update_npc_standing_portrait(game_key, "heroine", None))["ok"] is True
    assert "standing_portrait" not in inst.npcs["heroine"]
    assert inst.npcs["heroine"]["standing_portrait_expressions"] == {"sad": ref}
    assert (await api.update_npc_standing_portrait(game_key, "heroine"))["ok"] is False
    assert inst.npcs["heroine"]["standing_portrait_expressions"] == {"sad": ref}


@pytest.mark.asyncio
async def test_standing_portrait_kept_while_a_round_snapshot_can_restore_it(web_api):
    from src.engine.modules import round_safety

    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    game_key, inst = await _game_with_npc(api, registry)
    ref = api.save_standing_portrait_upload(_portrait_b64())["standing_portrait"]
    assert (await api.update_npc_standing_portrait(game_key, "heroine", ref))["ok"] is True
    inst.capture_round_entity_snapshot()
    assert (await api.update_npc_standing_portrait(game_key, "heroine", None))["ok"] is True

    # A failed round restores the snapshot, and with it the NPC's portrait.
    blocked = api.delete_standing_portrait(ref["asset_id"])
    assert blocked["ok"] is False and "角色引用" in blocked["error"]

    round_safety.clear_snapshots(inst)
    assert api.delete_standing_portrait(ref["asset_id"])["ok"] is True


@pytest.mark.asyncio
async def test_standing_portrait_kept_while_an_unloaded_save_mentions_it(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    await _game_with_npc(api, registry)
    ref = api.save_standing_portrait_upload(_portrait_b64())["standing_portrait"]
    # A save startup recovery could not load (e.g. written by a newer runtime).
    orphan = registry.save_dir / "web#unloadable#bot"
    orphan.mkdir(parents=True)
    (orphan / "state.json").write_text(
        '{"schema_version": 999, "npcs": {"x": {"standing_portrait": '
        f'{{"kind": "upload", "asset_id": "{ref["asset_id"]}"}}}}}}}}',
        encoding="utf-8",
    )

    blocked = api.delete_standing_portrait(ref["asset_id"])
    assert blocked["ok"] is False and "角色引用" in blocked["error"]

    (orphan / "state.json").write_text('{"schema_version": 999, "npcs": {}}', encoding="utf-8")
    assert api.delete_standing_portrait(ref["asset_id"])["ok"] is True


@pytest.mark.asyncio
async def test_standing_portrait_kept_while_an_old_round_beyond_the_save_window_mentions_it(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    _game_key, inst = await _game_with_npc(api, registry)
    ref = api.save_standing_portrait_upload(_portrait_b64())["standing_portrait"]
    # Only the first of 150 rounds still carries the reference; the save
    # encodes the recent rounds only, but a swipe can restore that one.
    inst.log[:] = [
        {"round": index + 1, "gm_response": "…", "actions": []} for index in range(150)
    ]
    inst.log[0]["pre_state_snapshot"] = {"npcs": {"heroine": {"standing_portrait": ref}}}

    blocked = api.delete_standing_portrait(ref["asset_id"])
    assert blocked["ok"] is False and "角色引用" in blocked["error"]

    inst.log[0].pop("pre_state_snapshot")
    assert api.delete_standing_portrait(ref["asset_id"])["ok"] is True


@pytest.mark.asyncio
async def test_standing_portrait_kept_while_an_unloaded_chat_log_mentions_it(web_api):
    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    await _game_with_npc(api, registry)
    ref = api.save_standing_portrait_upload(_portrait_b64())["standing_portrait"]
    orphan = registry.save_dir / "web#unloadable#bot"
    orphan.mkdir(parents=True)
    (orphan / "state.json").write_text('{"schema_version": 999, "npcs": {}}', encoding="utf-8")
    (orphan / "chatlog.jsonl").write_text(
        '{"round": 1, "pre_state_snapshot": {"npcs": {"x": {"standing_portrait": '
        f'{{"kind": "upload", "asset_id": "{ref["asset_id"]}"}}}}}}}}}}\n',
        encoding="utf-8",
    )

    blocked = api.delete_standing_portrait(ref["asset_id"])
    assert blocked["ok"] is False and "角色引用" in blocked["error"]
