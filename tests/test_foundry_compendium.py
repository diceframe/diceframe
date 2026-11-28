"""FVTT Compendium pack -> content packages (workplan §6.17 m3-01/m3-03).

Protected contracts:
- JSON array, JSONL and YAML source files parse to typed documents; LevelDB
  never reaches the parser and malformed sources fail closed;
- Journal/Item/Actor/Scene map to ContentDrafts; RollTable/Playlist/Macro and
  unknown shapes are reported ``unsupported`` with their raw document;
- mechanics are not exchanged (``system``/items/effects stay behind), scripts
  are flagged and never run;
- a pack rides the existing content sync preview/commit pipeline: one Book per
  Journal, one Book for Items/NPCs, explicit update/duplicate/skip, stale
  ``plan_digest`` refused;
- a Scene becomes the portable ``tactical-scene-v1`` definition and the
  explicit GM import creates one game scene without overwriting local ones.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.compat.foundry.compendium import (
    CODE_COMPENDIUM_MALFORMED,
    FoundryCompendiumError,
    parse_compendium_source,
)
from src.content_modules.adapters.compendium import (
    CODE_EMBEDDED_DATA_REJECTED,
    CODE_NOTHING_IMPORTABLE,
    CODE_PAGE_UNSUPPORTED,
    CODE_SCRIPT_IGNORED,
    COMPENDIUM_FORMAT,
    CONTENT_KIND_BY_DOCUMENT,
    book_documents,
    map_compendium,
)
from src.content_modules.refs import CONTENT_KIND_REGISTRY, PORTABLE_SCENE_SCHEMA
from src.engine.game_instance import GameInstance
from src.engine.modules import tactical_scene
from src.engine.participant_view import resolve_viewer
from src.webui.services import tactical_scene_ops as tactical_scene_ops_service
from src.webui.services import portable_scene_import

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "foundry" / "compendium"
DEVICE = {"kind": "device", "id": "install-a"}

pytest_plugins = ["tests.webapi_harness"]


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _source(name: str):
    return parse_compendium_source(_text(name), filename=name)


def _mapping(name: str):
    return map_compendium(_source(name), source_kind="device", source_id="install-a")


def _pack_item(name: str = "journal_pack.json", *, ref: str = "pack-1") -> dict:
    return {"client_ref": ref, "kind": "lorebook", "format": COMPENDIUM_FORMAT, "document": _text(name)}


def _preview(api, items: list[dict]) -> dict:
    return api.preview_content_import({"source": DEVICE, "items": items})


def _commit(api, items: list[dict], *, digest: str, decisions: dict | None = None) -> dict:
    return api.commit_content_import({
        "source": DEVICE, "items": items, "plan_digest": digest, "decisions": decisions or {},
    })


def _rows(result: dict) -> dict[str, dict]:
    return {row["client_ref"]: row for row in result["items"]}


# ---- parsing ------------------------------------------------------------------


def test_fixture_expectations() -> None:
    expectations = json.loads(_text("expectations.json"))
    for name, expected in expectations.items():
        source = _source(name)
        assert source.source_format == expected["format"], name
        assert len(source.records) == expected["records"], name
        assert [record.document_type for record in source.records] == expected["types"], name
        assert source.pack_name == expected.get("pack_name", ""), name


def test_document_name_and_envelope_pack_type_win_over_shape() -> None:
    declared = json.loads(_text("journal_pack.json"))[0]
    declared["documentName"] = "JournalEntry"
    source = parse_compendium_source(json.dumps([declared]))
    assert source.records[0].document_type == "JournalEntry"

    envelope = parse_compendium_source(_text("envelope_pack.json"))
    assert envelope.pack_name == "Lantern Pack"
    assert envelope.records[0].document_type == "Item"


def test_preparsed_documents_are_accepted() -> None:
    rows = json.loads(_text("journal_pack.json"))
    source = parse_compendium_source(rows)
    assert [record.id for record in source.records] == [row["_id"] for row in rows]


@pytest.mark.parametrize(
    "name",
    ["malformed/bad_json.json", "malformed/missing_id.json", "malformed/duplicate_id.json", "malformed/broken.jsonl"],
)
def test_malformed_sources_fail_closed(name: str) -> None:
    with pytest.raises(FoundryCompendiumError) as caught:
        parse_compendium_source((FIXTURES / name).read_bytes(), filename=Path(name).name)
    assert caught.value.code == CODE_COMPENDIUM_MALFORMED


def test_binary_and_empty_sources_are_malformed() -> None:
    with pytest.raises(FoundryCompendiumError):
        parse_compendium_source(b"\x00\x01\x02 leveldb block")
    with pytest.raises(FoundryCompendiumError):
        parse_compendium_source("")
    with pytest.raises(FoundryCompendiumError):
        parse_compendium_source(12345)


def test_oversized_source_is_malformed(monkeypatch) -> None:
    from src.compat.foundry import compendium

    monkeypatch.setattr(compendium, "MAX_COMPENDIUM_BYTES", 64)
    with pytest.raises(FoundryCompendiumError):
        parse_compendium_source("[" + " " * 128 + "]")


# ---- mapping ------------------------------------------------------------------


def test_journal_maps_to_lorebook_book_and_keeps_references() -> None:
    mapping = _mapping("journal_pack.json")
    [draft] = mapping.drafts_for("lorebook")
    assert draft.kind == "lorebook"
    assert (draft.source_kind, draft.source_id, draft.external_id) == ("device", "install-a", "JrnlGreyMoor001")
    assert draft.digest.startswith("sha256:")
    book = draft.payload["data"]["lorebook"]
    assert book["name"] == "Grey Moor Journal"
    # The image page has no text entry; it is reported, not guessed.
    assert [entry["name"] for entry in book["entries"]] == ["Opening", "Rumours"]
    assert "The moor is silent." in book["entries"][0]["content"]
    assert [(ref.kind, ref.id) for ref in draft.references] == [("npc", "MiraRanger001")]
    assert any(row["code"] == CODE_PAGE_UNSUPPORTED for row in mapping.warnings)


def test_items_map_to_item_drafts_without_mechanics_or_embedded_assets() -> None:
    mapping = _mapping("items_pack.jsonl")
    drafts = mapping.drafts_for("item")
    assert [draft.external_id for draft in drafts] == ["ItemRope00000001", "ItemLantern000001"]
    rope = drafts[0].payload
    assert rope["content"] == "Fifty feet of stout hemp rope."
    # ``system`` is mechanics and never copied into the portable payload.
    assert "system" not in rope and "system" not in rope["extensions"]["foundry"]["unknown_fields"]
    assert rope["extensions"]["foundry"]["mechanics"] == "not_exchanged"
    # The lantern's ``data:`` image is dropped, not embedded.
    assert drafts[1].payload["extensions"]["foundry"]["unknown_fields"]["img"] == ""
    assert any(row["code"] == CODE_EMBEDDED_DATA_REJECTED for row in mapping.warnings)


def test_scene_maps_to_the_portable_tactical_scene_definition() -> None:
    mapping = _mapping("actors_scenes_pack.yaml")
    assert CONTENT_KIND_BY_DOCUMENT == {
        "JournalEntry": "lorebook", "Item": "item", "Actor": "npc", "Scene": "scene",
    }
    [npc] = mapping.drafts_for("npc")
    assert npc.payload["content"] == "Mira tracks the moor's eastern edge."

    [scene] = mapping.drafts_for("scene")
    payload = scene.payload
    assert payload["spec"] == PORTABLE_SCENE_SCHEMA
    assert payload["content"] == "Cold ashes; someone left in a hurry."
    document = payload["tactical_scene"]
    assert document["kind"] == "scene"
    assert document["display_name"] == "Moor Camp"
    assert document["canonical_id"].startswith("fvtt.")
    assert document["grid"]["type"] == "square" and document["grid"]["size"] == 100
    # Tokens are whatever the adapter converted; empty collections stay empty.
    assert len(document["placeables"]["tokens"]) == 1
    assert document["placeables"]["walls"] == {}
    assert "system" not in payload
    assert scene.provenance["source_version"] == "v14"
    # Scenes are not part of any persisted Book: their import is per game.
    assert all("SceneMoorCamp001" not in json.dumps(document) for _suffix, document in book_documents(mapping))


def test_scene_without_a_declared_version_is_reported_not_guessed() -> None:
    rows = json.loads(json.dumps([{
        "_id": "SceneNoStats001",
        "name": "Unknown Map",
        "width": 100,
        "height": 100,
        "grid": {"type": 1, "size": 100},
    }]))
    mapping = map_compendium(
        parse_compendium_source(rows), source_kind="device", source_id="install-a",
    )
    assert mapping.drafts_for("scene") == ()
    [row] = mapping.unsupported
    assert row.document_type == "Scene" and "coreVersion" in row.reason


def test_scene_kind_spec_routes_to_per_game_scene_creation() -> None:
    spec = CONTENT_KIND_REGISTRY.spec("scene")
    assert spec.portable_schema == PORTABLE_SCENE_SCHEMA
    assert spec.import_target == "tactical_scene_create"
    assert spec.runtime_projection == "tactical_scene"
    assert spec.authority == "content_package"


def test_unmappable_documents_are_reported_with_their_raw_document() -> None:
    mapping = _mapping("journal_pack.json")
    unsupported = {row.document_type: row for row in mapping.unsupported}
    assert set(unsupported) == {"RollTable", "Macro"}
    macro = unsupported["Macro"]
    assert macro.raw["command"].startswith("fetch(")
    assert "executable" in macro.reason
    assert any(row["code"] == CODE_SCRIPT_IGNORED for row in mapping.warnings)
    report = mapping.report()
    assert report["unsupported"][1]["raw"]["command"].startswith("fetch(")


def test_content_drafts_use_the_neutral_contract() -> None:
    mapping = _mapping("actors_scenes_pack.yaml")
    for draft in mapping.drafts:
        portable = draft.to_portable_dict()
        assert portable["ref"]["source_kind"] == "device"
        assert portable["ref"]["source_id"] == "install-a"
        assert portable["payload"]
        assert draft.provenance["adapter"] == COMPENDIUM_FORMAT


# ---- sync pipeline ------------------------------------------------------------


def test_preview_writes_nothing_and_shows_the_compendium_report(web_api) -> None:
    api, lorebook, *_ = web_api
    items = [_pack_item()]
    result = _preview(api, items)

    assert result["ok"] is True, result
    assert result["plan_digest"].startswith("sha256:")
    [row] = result["items"]
    assert row["kind"] == "lorebook" and row["action"] == "create"
    codes = {warning["code"] for warning in row["warnings"]}
    assert {"FOUNDRY_COMPENDIUM_UNSUPPORTED", CODE_SCRIPT_IGNORED, CODE_PAGE_UNSUPPORTED} <= codes
    assert lorebook.list_lorebooks() == []


def test_commit_creates_books_with_typed_entries(web_api) -> None:
    api, lorebook, *_ = web_api
    items = [_pack_item()]
    preview = _preview(api, items)

    result = _commit(api, items, digest=preview["plan_digest"])

    assert result["ok"] is True, result
    [row] = result["items"]
    assert row["kind"] == "lorebook" and row["status"] == "created"
    book = lorebook.get_lorebook(row["canonical_id"])
    assert book["source_kind"] == "device" and book["source_id"] == "install-a"
    entries = lorebook.list_book_entries(row["canonical_id"])
    assert [entry["name"] for entry in entries] == ["Opening", "Rumours"]
    assert {entry["type"] for entry in entries} == {"journal"}
    assert all("fetch(" not in json.dumps(entry) for entry in entries)


def test_items_pack_persists_typed_entries_without_mechanics(web_api) -> None:
    api, lorebook, *_ = web_api
    items = [_pack_item("items_pack.jsonl")]
    result = _commit(api, items, digest=_preview(api, items)["plan_digest"])

    assert result["ok"] is True, result
    [row] = result["items"]
    entries = lorebook.list_book_entries(row["canonical_id"])
    assert {entry["name"] for entry in entries} == {"Hemp Rope", "Hooded Lantern"}
    assert {entry["type"] for entry in entries} == {"item"}
    hanged = next(entry for entry in entries if entry["name"] == "Hooded Lantern")
    assert "data:image" not in json.dumps(hanged)


def test_repush_is_explicitly_unchanged_and_stale_digest_is_refused(web_api) -> None:
    api, lorebook, *_ = web_api
    items = [_pack_item()]
    created = _commit(api, items, digest=_preview(api, items)["plan_digest"])
    book_id = created["items"][0]["canonical_id"]
    revision = lorebook.get_lorebook(book_id)["revision"]

    repreview = _preview(api, items)
    [row] = repreview["items"]
    assert row["action"] == "unchanged"
    unchanged = _commit(api, items, digest=repreview["plan_digest"], decisions={row["client_ref"]: "update"})
    assert unchanged["items"][0]["status"] == "unchanged"
    assert lorebook.get_lorebook(book_id)["revision"] == revision

    stale = _commit(api, items, digest=_preview(api, items)["plan_digest"][:-4] + "0000")
    assert stale["ok"] is False and stale["error_code"] == "PLAN_STALE"


def test_update_duplicate_and_skip_need_an_explicit_choice(web_api) -> None:
    api, lorebook, *_ = web_api
    first = _pack_item("envelope_pack.json")
    created = _commit(api, [first], digest=_preview(api, [first])["plan_digest"])
    old_id = created["items"][0]["canonical_id"]

    edited_text = _text("envelope_pack.json").replace("cracked pane", "cracked pane and a dent")
    edited = [{**first, "document": edited_text}]
    preview = _preview(api, edited)
    [row] = preview["items"]
    assert row["action"] == "update"

    refused = _commit(api, edited, digest=preview["plan_digest"])
    assert refused["ok"] is False and refused["error_code"] == "DECISION_REQUIRED"

    skipped = _commit(api, edited, digest=preview["plan_digest"], decisions={row["client_ref"]: "skip"})
    assert skipped["items"][0]["status"] == "skipped"
    assert "cracked pane" in lorebook.list_book_entries(old_id)[0]["content"]

    duplicated = _commit(api, edited, digest=preview["plan_digest"], decisions={row["client_ref"]: "duplicate"})
    new_id = duplicated["items"][0]["canonical_id"]
    assert new_id != old_id
    assert "dent" in lorebook.list_book_entries(new_id)[0]["content"]

    revised = [{**first, "document": edited_text.replace("and a dent", "and a scratch")}]
    revised_preview = _preview(api, revised)
    [revised_row] = revised_preview["items"]
    assert revised_row["action"] == "update"
    updated = _commit(
        api, revised,
        digest=revised_preview["plan_digest"],
        decisions={revised_row["client_ref"]: "update"},
    )
    assert updated["items"][0]["status"] == "updated"
    assert "scratch" in lorebook.list_book_entries(new_id)[0]["content"]


def test_lorebook_documents_still_go_through_their_own_importer(web_api) -> None:
    api, lorebook, *_ = web_api
    document = {"spec": "lorebook_v3", "data": {"lorebook": {"name": "Atlas", "entries": [
        {"id": "town", "name": "Town", "content": "Town lore"},
    ]}}}
    items = [{"client_ref": "book-1", "kind": "lorebook", "format": "lorebook_v3", "document": document}]
    preview = _preview(api, items)
    assert preview["ok"] is True, preview
    [row] = preview["items"]
    assert row["kind"] == "lorebook" and row["action"] == "create"

    result = _commit(api, items, digest=preview["plan_digest"])
    assert result["ok"] is True and result["items"][0]["status"] == "created"
    assert lorebook.list_book_entries(result["items"][0]["canonical_id"])[0]["name"] == "Town"


def test_malformed_compendium_is_refused_by_the_pipeline(web_api) -> None:
    api, lorebook, *_ = web_api
    items = [_pack_item("malformed/bad_json.json")]
    result = _preview(api, items)
    assert result["ok"] is False
    assert result["error_code"] == "FOUNDRY_COMPENDIUM_MALFORMED"
    assert lorebook.list_lorebooks() == []


def test_scene_only_compendium_reports_nothing_importable(web_api) -> None:
    api, lorebook, *_ = web_api
    scene_only = [{
        "_id": "SceneOnly0000001",
        "name": "Lonely Map",
        "width": 100,
        "height": 100,
        "_stats": {"coreVersion": "14.370"},
        "grid": {"type": 1, "size": 100},
        "tokens": [],
    }]
    items = [{"client_ref": "pack-scene", "kind": "lorebook", "format": COMPENDIUM_FORMAT,
              "document": json.dumps(scene_only)}]
    result = _preview(api, items)
    # A scene is a portable definition, not a pack-storable Book: the per-game
    # GM import owns creation (m3-03), so the sync pipeline stays fail-closed.
    assert result["ok"] is False and result["error_code"] == CODE_NOTHING_IMPORTABLE
    assert lorebook.list_lorebooks() == []


# ---- portable scene import, one game (workplan §6.17 m3-03) -----------------


GAME_KEY = "g"


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", GAME_KEY, "u"), players={"p1": {}})
    instance.gm_uid = "gm1"
    return instance


def _viewer(instance: GameInstance, uid: str):
    return resolve_viewer(
        instance, user_id=uid, owner_authenticated=False, player_preview=False,
    )


class _Saver:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def __call__(self, instance: GameInstance) -> None:
        if self.fail:
            raise OSError("disk full")
        self.calls += 1


def _deps(instance: GameInstance, saver: "_Saver | None" = None):
    return tactical_scene_ops_service.TacticalSceneOpDependencies(
        get_instance=lambda key: instance if key == GAME_KEY else None,
        save_instance=saver or _Saver(),
    )


def _definition() -> dict:
    [scene] = _mapping("actors_scenes_pack.yaml").drafts_for("scene")
    return scene.to_portable_dict()["payload"]


_DEFAULT_INTENT = object()


async def _import(instance, definition, *, intent=_DEFAULT_INTENT, uid="gm1", saver=None, broadcast=None):
    return await portable_scene_import.import_scene_definition(
        _deps(instance, saver), GAME_KEY, _viewer(instance, uid),
        definition=definition,
        intent={"confirm": True} if intent is _DEFAULT_INTENT else intent,
        broadcast=broadcast,
    )


@pytest.mark.asyncio
async def test_portable_definition_creates_one_tactical_scene() -> None:
    instance = _instance()
    definition = _definition()
    saver = _Saver()

    result = await _import(instance, definition, saver=saver)

    assert result["ok"] is True and result["created"] is True
    scene_id = definition["tactical_scene"]["canonical_id"]
    assert result["scene_id"] == scene_id
    stored = tactical_scene.scenes(instance)[scene_id]
    assert stored["display_name"] == "Moor Camp"
    # Converted tokens only; no wall or route is invented.
    assert len(stored["placeables"]["tokens"]) == 1
    assert stored["placeables"]["walls"] == {}
    assert saver.calls == 1
    assert tactical_scene.revision(instance) == 1
    assert tactical_scene.active_scene_ref(instance) is None


@pytest.mark.asyncio
async def test_reimporting_the_same_definition_is_idempotent() -> None:
    instance = _instance()
    definition = _definition()
    saver = _Saver()

    first = await _import(instance, definition, saver=saver)
    second = await _import(instance, definition, saver=saver)

    assert first["created"] is True
    assert second["ok"] is True and second["created"] is False
    assert second["scene_id"] == first["scene_id"]
    assert second["revision"] == first["revision"] == 1
    assert saver.calls == 1
    assert list(tactical_scene.scenes(instance)) == [first["scene_id"]]


@pytest.mark.asyncio
async def test_same_identity_with_different_content_is_refused_without_overwrite() -> None:
    instance = _instance()
    definition = _definition()
    await _import(instance, definition)
    before = deepcopy(tactical_scene.slot(instance))
    changed = deepcopy(definition)
    changed["tactical_scene"]["display_name"] = "Moor Camp (edited elsewhere)"

    result = await _import(instance, changed)

    assert result["ok"] is False
    assert result["error_code"] == portable_scene_import.CODE_CONFLICT
    assert tactical_scene.slot(instance) == before


@pytest.mark.asyncio
async def test_same_name_needs_an_explicit_choice_and_never_overwrites() -> None:
    instance = _instance()
    first = await _import(instance, _definition())
    assert first["created"] is True

    other = deepcopy(_definition())
    other["tactical_scene"]["canonical_id"] = "fvtt.foundry_v14.scene.othercamp"
    other["tactical_scene"]["external_ids"] = {"foundry_v14": "OtherCamp000001"}
    refused = await _import(instance, other)
    assert refused["ok"] is False
    assert refused["error_code"] == portable_scene_import.CODE_NAME_CONFLICT

    allowed = await _import(instance, other, intent={"confirm": True, "confirm_name_collision": True})
    assert allowed["ok"] is True and allowed["created"] is True
    scenes = tactical_scene.scenes(instance)
    assert set(scenes) == {first["scene_id"], allowed["scene_id"]}
    assert scenes[first["scene_id"]]["display_name"] == "Moor Camp"


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", [
    None,
    {},
    {"spec": "tactical-scene-v1", "tactical_scene": {"kind": "scene"}},
    {"spec": "tactical-scene-v1", "tactical_scene": []},
])
async def test_malformed_definitions_are_refused(definition) -> None:
    instance = _instance()
    before = deepcopy(tactical_scene.slot(instance))

    result = await _import(instance, definition)

    assert result["ok"] is False
    assert result["error_code"] == portable_scene_import.CODE_INVALID
    assert tactical_scene.slot(instance) == before


@pytest.mark.asyncio
async def test_import_is_gm_only() -> None:
    instance = _instance()
    before = deepcopy(tactical_scene.slot(instance))

    result = await _import(instance, _definition(), uid="p1")

    assert result["ok"] is False
    assert result["error_code"] == portable_scene_import.CODE_FORBIDDEN
    assert tactical_scene.slot(instance) == before


@pytest.mark.asyncio
async def test_import_requires_explicit_confirmation() -> None:
    instance = _instance()
    for intent in ({}, {"confirm": False}, {"confirm": "yes"}, None):
        result = await _import(instance, _definition(), intent=intent)
        assert result["ok"] is False
        assert result["error_code"] == portable_scene_import.CODE_UNCONFIRMED
    assert tactical_scene.scenes(instance) == {}


@pytest.mark.asyncio
async def test_successful_import_broadcasts_the_scene_projection() -> None:
    instance = _instance()
    events: list[tuple[dict, dict]] = []

    async def broadcast(game_key, payload, **kwargs):
        events.append((payload, kwargs))

    result = await _import(instance, _definition(), broadcast=broadcast)

    assert result["ok"] is True
    assert {payload["event_type"] for payload, _ in events} == {"tactical_scene_updated"}
    assert events[0][1].get("exclude_uid") == "gm1"
    assert events[1][1].get("private_uid") == "gm1"


@pytest.mark.asyncio
async def test_save_failure_leaves_the_slot_untouched() -> None:
    instance = _instance()
    before = deepcopy(tactical_scene.slot(instance))

    result = await _import(instance, _definition(), saver=_Saver(fail=True))

    assert result["ok"] is False
    assert result["error_code"] == "save_failed"
    assert tactical_scene.slot(instance) == before