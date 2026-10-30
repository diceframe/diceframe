"""§8 — Character Card / Tavern 统一到 canonical Lorebook 路径。

施工单要求：普通 Character Card 产品流与旧 Tavern API 都必须走

    parse_character_card_document
    → card body import
    → optional embedded character_book
    → lorebook_v3 adapter
    → canonical commit
    → binding

并真正支持 include / exclude（checked → card + Lorebook；unchecked → card only），
binding 规则为「有 canonical uid → character；没有 → current world 或 unbound」。
旧 ``/api/import-tavern-card`` 只做 façade。
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import zlib
from pathlib import Path

import pytest

from src.lorebook.store import LorebookStore
from src.webui.services.character_cards import (
    CHARACTER_LORE_LABEL,
    CHARACTER_LORE_ROLE,
    CharacterCardDependencies,
    import_character_card,
)
from src.webui.services.tavern import TavernImportDependencies, import_tavern_card

EMBEDDED_BOOK = {
    "name": "Harbor Lore",
    "scan_depth": 5,
    "token_budget": 777,
    "recursive_scanning": True,
    "extensions": {"plugin": {"keep": 1}},
    "entries": [
        {
            "id": "harbor-1", "name": "Harbor", "keys": ["harbor", "dock"],
            "content": "The harbor is quiet at dawn.",
            "priority": 42, "probability": 100,
            "extensions": {"custom_field": "preserved"},
        },
        {"id": "harbor-2", "name": "Tide", "keys": ["tide"], "content": "The tide turns."},
    ],
}


def _ccv3_payload() -> dict:
    return {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            "name": "Aster", "description": "A harbor pilot", "personality": "calm",
            "scenario": "Dockside", "first_mes": "Need a pilot?",
            "character_book": json.loads(json.dumps(EMBEDDED_BOOK)),
        },
    }


def _ccv3_json() -> bytes:
    return json.dumps(_ccv3_payload(), ensure_ascii=False).encode("utf-8")


def _png_with_text_chunks(*chunks: tuple[bytes, bytes]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    body = b"".join(chunk(kind, data) for kind, data in chunks)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + body + chunk(b"IEND", b"")


def _ccv3_png() -> bytes:
    """A real SillyTavern-style PNG: base64(JSON) in a ``chara`` tEXt chunk."""

    payload = base64.b64encode(json.dumps(_ccv3_payload(), ensure_ascii=False).encode("utf-8"))
    return _png_with_text_chunks((b"tEXt", b"chara\x00" + payload))


@pytest.fixture()
def store(tmp_path: Path):
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world("w1", "Harbor World", description="", language="zh-CN")
    try:
        yield store
    finally:
        store.close()


def _deps(tmp_path: Path, store: LorebookStore) -> CharacterCardDependencies:
    return CharacterCardDependencies(
        cards_path=tmp_path / "cards.json",
        lorebook=store,
        rebuild_lorebook_index=lambda _world_id: None,
    )


def _import(tmp_path: Path, store: LorebookStore, raw: bytes, name: str, **kwargs):
    return asyncio.run(import_character_card(
        _deps(tmp_path, store),
        file_data=base64.b64encode(raw).decode(),
        file_name=name,
        **kwargs,
    ))


# ---- 正常 Character Card 产品流 --------------------------------------------


def test_ccv3_json_target_character_card_commits_card_and_embedded_book(tmp_path, store):
    """checked → card + Lorebook：内嵌 character_book 必须真的落进 canonical 库。"""

    result = _import(tmp_path, store, _ccv3_json(), "aster.json", world_id="w1")

    assert result["ok"] is True
    assert result["imported_as"] == "character_card"
    assert result["format"] == "tavern"
    assert result["card"]["character_name"] == "Aster"

    lore = result["lorebook"]
    assert lore["entries"] == 2
    assert lore["role"] == CHARACTER_LORE_ROLE
    assert lore["label"] == CHARACTER_LORE_LABEL
    assert lore["binding"]["scope_kind"] == "world"
    assert lore["binding"]["scope_id"] == "w1"

    stored = store.list_book_entries(lore["book_id"])
    assert len(stored) == 2
    assert {e["content"] for e in stored} == {
        "The harbor is quiet at dawn.", "The tide turns.",
    }
    # binding 真的写进库，且标为 character_card role（world 自带 primary binding）。
    bindings = store.list_bindings(scope_kind="world", scope_id="w1")
    lore_bindings = [b for b in bindings if b["role"] == CHARACTER_LORE_ROLE]
    assert len(lore_bindings) == 1
    assert lore_bindings[0]["book_id"] == lore["book_id"]


def test_ccv3_png_import_commits_card_and_embedded_book(tmp_path, store):
    """CCv3 PNG（chara tEXt chunk）走同一条 canonical 路径。"""

    result = _import(tmp_path, store, _ccv3_png(), "aster.png", world_id="w1")

    assert result["ok"] is True
    assert result["card"]["character_name"] == "Aster"
    assert result["lorebook"]["entries"] == 2
    assert len(store.list_book_entries(result["lorebook"]["book_id"])) == 2


def test_raw_json_png_chunk_still_imports(tmp_path, store):
    """旧版直接写原始 JSON（未 base64）的 PNG 仍然兼容。"""

    raw_json = json.dumps(_ccv3_payload(), ensure_ascii=False).encode("utf-8")
    png = _png_with_text_chunks((b"tEXt", b"chara\x00" + raw_json))

    result = _import(tmp_path, store, png, "aster.png", world_id="w1")

    assert result["ok"] is True
    assert result["card"]["character_name"] == "Aster"


def test_ccv3_keyword_wins_over_chara_and_ztxt_is_supported(tmp_path, store):
    """V3 的 ``ccv3`` 关键字优先于 ``chara``，压缩的 zTXt chunk 也能读。"""

    v2 = {"spec": "chara_card_v2", "data": {"name": "OldName"}}
    v3_b64 = base64.b64encode(json.dumps(_ccv3_payload()).encode("utf-8"))
    png = _png_with_text_chunks(
        (b"tEXt", b"chara\x00" + base64.b64encode(json.dumps(v2).encode("utf-8"))),
        (b"zTXt", b"ccv3\x00\x00" + zlib.compress(v3_b64)),
    )

    result = _import(tmp_path, store, png, "aster.png", world_id="w1")

    assert result["ok"] is True
    assert result["card"]["character_name"] == "Aster"


def test_png_without_card_chunk_reports_missing(tmp_path, store):
    result = _import(tmp_path, store, _png_with_text_chunks(), "empty.png", world_id="w1")

    assert result["ok"] is False
    assert "未找到角色卡数据" in result["error"]


def test_include_character_book_false_imports_card_only(tmp_path, store):
    """unchecked → card only：不提交任何 Lorebook，也不留下 binding。"""

    books_before = {b["id"] for b in store.list_lorebooks()}
    result = _import(
        tmp_path, store, _ccv3_json(), "aster.json",
        world_id="w1", include_character_book=False,
    )

    assert result["ok"] is True
    assert result["card"]["character_name"] == "Aster"
    assert "lorebook" not in result
    assert "lorebook_book_id" not in result
    assert {b["id"] for b in store.list_lorebooks()} == books_before, "unchecked 不该新建 Book"
    assert not [
        b for b in store.list_bindings() if b["role"] == CHARACTER_LORE_ROLE
    ], "unchecked 不该留下 character lore binding"


def test_character_card_without_embedded_book_has_no_lorebook_key(tmp_path, store):
    payload = _ccv3_payload()
    payload["data"].pop("character_book")
    result = _import(
        tmp_path, store, json.dumps(payload).encode("utf-8"), "plain.json", world_id="w1",
    )
    assert result["ok"] is True
    assert "lorebook" not in result


# ---- binding 规则 ----------------------------------------------------------


def test_canonical_character_uid_binds_character_scope(tmp_path, store):
    """有 canonical uid → scope_kind=character，优先于 world。"""

    result = _import(
        tmp_path, store, _ccv3_json(), "aster.json",
        world_id="w1", character_uid="char-7",
    )

    binding = result["lorebook"]["binding"]
    assert binding["scope_kind"] == "character"
    assert binding["scope_id"] == "char-7"
    assert store.list_bindings(scope_kind="character", scope_id="char-7")[0]["book_id"] == (
        result["lorebook"]["book_id"]
    )


def test_no_uid_and_no_world_leaves_book_unbound(tmp_path, store):
    """没有 uid 也没有 current world → unbound（书仍然导入，只是不绑定）。"""

    result = _import(tmp_path, store, _ccv3_json(), "aster.json")

    assert result["lorebook"]["binding"] is None
    assert len(store.list_book_entries(result["lorebook"]["book_id"])) == 2
    assert not [b for b in store.list_bindings() if b["role"] == CHARACTER_LORE_ROLE]


def test_bogus_world_does_not_create_a_dangling_binding(tmp_path, store):
    result = _import(tmp_path, store, _ccv3_json(), "aster.json", world_id="no-such-world")
    assert result["ok"] is True
    assert result["lorebook"]["binding"] is None


# ---- target=npc 仍然工作 ---------------------------------------------------


def test_target_npc_still_commits_book_with_world_binding(tmp_path, store):
    result = _import(
        tmp_path, store, _ccv3_json(), "aster.json", target="npc", world_id="w1",
    )

    assert result["ok"] is True
    assert result["imported_as"] == "npc"
    assert store.get_entry("w1_tavern_Aster") is not None
    assert result["lorebook"]["binding"]["scope_kind"] == "world"
    assert result["lorebook"]["binding"]["scope_id"] == "w1"
    assert result["lorebook_entries"] == 2


# ---- 旧 Tavern API façade --------------------------------------------------


def test_legacy_import_tavern_card_route_commits_the_embedded_book(tmp_path, store):
    """旧 /api/import-tavern-card 不能再只是数一数 character_book。"""

    class _Instance:
        world_id = "w1"

    dependencies = TavernImportDependencies(
        lorebook=store,
        get_instance=lambda _key: _Instance(),
        parse_game_key=lambda key: (key,),
        rebuild_lorebook_index=lambda _world_id: None,
    )
    result = asyncio.run(import_tavern_card(
        dependencies,
        file_data=base64.b64encode(_ccv3_png()).decode(),
        file_name="aster.png",
        game_key="g1",
    ))

    assert result["ok"] is True
    npc = result["npc"]
    assert npc["lorebook_entries"] == 2
    assert len(store.list_book_entries(npc["lorebook_book_id"])) == 2
    # façade 与产品流共用同一 role/label 与同一 canonical commit。
    assert npc["lorebook"]["role"] == CHARACTER_LORE_ROLE
    assert store.list_bindings(scope_kind="world", scope_id="w1")[0]["role"] == CHARACTER_LORE_ROLE


# ---- 保真 ------------------------------------------------------------------


def test_embedded_book_settings_and_unknown_extensions_are_preserved(tmp_path, store):
    """Book settings、entry priority 与未知 extensions 必须原样保留。"""

    result = _import(tmp_path, store, _ccv3_json(), "aster.json", world_id="w1")
    book_id = result["lorebook"]["book_id"]

    book = store.get_lorebook(book_id)
    assert book["scan_depth"] == 5
    assert book["token_budget"] == 777
    assert book["recursive_scanning"] is True

    entries = {e["id"]: e for e in store.list_book_entries(book_id)}
    harbor = next(e for e in entries.values() if e["content"].startswith("The harbor"))
    assert harbor["priority"] == 42
    assert harbor["extensions"]["custom_field"] == "preserved"
    # 外部 id 保留在 provenance，不被合成 id 顶掉。
    assert harbor["provenance"]["external_id"] == "harbor-1"


# ---- 路由层：请求体 → 服务参数的接线 ---------------------------------------


def _route_app(tmp_path: Path, store: LorebookStore):
    from aiohttp import web

    from src.webui.api import WebAPI
    from src.webui.routes.character_cards import register_character_cards

    api = WebAPI.__new__(WebAPI)
    api._character_card_dependencies = _deps(tmp_path, store)
    app = web.Application()
    app["api"] = api
    register_character_cards(app)
    return app


@pytest.mark.asyncio
async def test_import_route_honours_include_character_book_from_the_body(tmp_path, store):
    """路由必须把 include_character_book 真的透传下去，而不是丢掉这段接线。"""

    from aiohttp.test_utils import TestClient, TestServer

    payload = base64.b64encode(_ccv3_json()).decode()
    async with TestClient(TestServer(_route_app(tmp_path, store))) as client:
        # 显式 false → 只有卡，没有 Lorebook。
        response = await client.post("/api/character-cards/import", json={
            "file_data": payload, "file_name": "aster.json",
            "target": "character_card", "world_id": "w1",
            "include_character_book": False,
        })
        assert response.status == 200
        body = await response.json()
        assert body["ok"] is True
        assert "lorebook" not in body
        assert not [b for b in store.list_bindings() if b["role"] == CHARACTER_LORE_ROLE]

        # 默认（不传）→ 内嵌世界书一起落库。
        response = await client.post("/api/character-cards/import", json={
            "file_data": payload, "file_name": "aster2.json",
            "target": "character_card", "world_id": "w1",
        })
        assert response.status == 200
        body = await response.json()
        assert body["lorebook"]["entries"] == 2
        assert body["lorebook"]["label"] == CHARACTER_LORE_LABEL


@pytest.mark.asyncio
async def test_import_route_accepts_a_string_false_for_the_checkbox(tmp_path, store):
    """表单编码的 "false" 不能被 bool() 当成 True：那是真的不导入。"""

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_route_app(tmp_path, store))) as client:
        response = await client.post("/api/character-cards/import", json={
            "file_data": base64.b64encode(_ccv3_json()).decode(),
            "file_name": "aster.json", "target": "character_card",
            "world_id": "w1", "include_character_book": "false",
        })
        assert response.status == 200
        body = await response.json()
        assert body["ok"] is True
        assert "lorebook" not in body, "字符串 'false' 必须仍然是 false"
