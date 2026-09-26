"""Media migration, live aliases, rollback and portable asset coverage."""

from copy import deepcopy
import base64
import io
import json
import zipfile

import pytest

from src.engine import persistence
from src.engine.game_instance import GameInstance, GameRegistry
from src.engine.module_state import ModuleStateError
from src.engine.modules import media as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v22_to_v23,
    migrate_game_state_payload,
)
from src.webui.services.game_packages import GamePackageDependencies, GamePackageService


def test_migration_moves_references_without_mutating_input():
    payload = {"instance_schema_version": 22, "scene_image": {"kind": "upload", "asset_id": "scene"},
               "map_background": {"kind": "upload", "asset_id": "map"},
               "modules": {"extension": {"schema_version": 9, "opaque": True}}}
    before = deepcopy(payload)
    migrated = migrate_game_state_payload(payload)
    assert payload == before
    assert "scene_image" not in migrated and "map_background" not in migrated
    assert migrated["modules"]["media"] == {"schema_version": 1, **{
        key: payload[key] for key in ("scene_image", "map_background")}}
    assert migrated["modules"]["extension"] == payload["modules"]["extension"]
    assert migrate_game_state_payload(migrated) == migrated


@pytest.mark.parametrize("slot", [{}, {"schema_version": 99, "opaque": [1]}, module.fresh()])
def test_existing_slot_takes_precedence_and_single_step_is_idempotent(slot):
    payload = {"scene_image": {"legacy": True}, "map_background": {}, "modules": {"media": slot}}
    migrated = _migrate_v22_to_v23(payload)
    assert migrated["modules"]["media"] is slot
    before = deepcopy(migrated)
    assert _migrate_v22_to_v23(migrated) == before


@pytest.mark.parametrize("value", [None, False, 1, "invalid", []])
def test_invalid_values_default(value):
    assert _migrate_v22_to_v23({"scene_image": value, "map_background": value})["modules"]["media"] == module.fresh()
    assert module.ensure({"schema_version": 1, "scene_image": value, "map_background": value}) == module.fresh()
    assert module.ensure(value) == module.fresh()


def test_live_properties_replacement_and_round_trip():
    instance = GameInstance(game_key=("web", "media", "bot"))
    for field in ("scene_image", "map_background"):
        reference = {"kind": "upload", "asset_id": field}
        setattr(instance, field, reference)
        assert getattr(instance, field) is reference is instance.modules["media"][field]
        getattr(instance, field)["extra"] = "retained"
    saved = instance.to_dict()
    assert "scene_image" not in saved and "map_background" not in saved
    restored = GameInstance.from_dict(saved)
    assert restored.modules["media"] == instance.modules["media"]
    old = instance.scene_image
    replacement = {"kind": "none"}
    module.replace_scene_image(instance, replacement)
    assert instance.scene_image is replacement
    assert old["asset_id"] == "scene_image"


def test_future_versions_preserved_and_runtime_access_rejected():
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})
    slot = {"schema_version": 99, "scene_image": ["opaque"]}
    before = deepcopy(slot)
    assert module.ensure(slot) is slot
    instance = GameInstance(game_key=("web", "future", "bot"), modules={"media": slot})
    for field in ("scene_image", "map_background"):
        with pytest.raises(ModuleStateError):
            getattr(instance, field)
        with pytest.raises(ModuleStateError):
            setattr(instance, field, {})
    assert instance.to_dict()["modules"]["media"] == before


@pytest.mark.parametrize("modules", [None, [], {}, {"extension": {"schema_version": 99}}])
def test_payload_container_falls_back_without_mutation(modules):
    payload = {"modules": modules, "scene_image": {"kind": "upload"}}
    before = deepcopy(payload)
    assert module.payload_container(payload) is payload
    assert payload == before


def test_payload_container_accepts_legacy_without_modules():
    payload = {"scene_image": {"legacy": True}}
    assert module.payload_container(payload) is payload


def test_payload_container_prefers_current_module_slot_without_repair():
    slot = {"schema_version": module.SCHEMA_VERSION}
    payload = {"modules": {"media": slot}, "scene_image": {"legacy": True}}
    before = deepcopy(payload)
    assert module.payload_container(payload) is slot
    assert payload == before


@pytest.mark.parametrize("slot", [{}, {"schema_version": 2}, {"schema_version": 99}, None, [], "invalid", 1])
def test_payload_container_rejects_invalid_or_unknown_slot_without_mutation(slot):
    payload = {"modules": {"media": slot}, "scene_image": {"legacy": True}}
    before = deepcopy(payload)
    with pytest.raises(ModuleStateError, match="media module"):
        module.payload_container(payload)
    assert payload == before


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", [
    {"schema_version": 99, "scene_image": {"future": "opaque"}, "map_background": {"future": "opaque"}},
    {"schema_version": 2}, {}, None, [], "invalid",
], ids=["future-opaque", "schema-2", "missing-schema", "null", "list", "string"])
async def test_package_operations_reject_unsupported_media_before_any_asset_or_save_write(tmp_path, slot):
    registry = GameRegistry(tmp_path / "saves")
    instance = registry.get_or_create(("web", "unsupported-media", "bot"))
    state = instance.to_dict()
    state["modules"]["media"] = deepcopy(slot)
    state["scene_image"] = {"legacy": True, "kind": "save_asset", "path": "scene-image.asset"}
    state["map_background"] = {"legacy": True, "kind": "save_asset", "path": "map-background.asset"}
    before = deepcopy(state)
    state_path = registry.save_package_state_path(instance.game_key)
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    def unexpected_asset_access(_value):
        pytest.fail("unsupported media must not resolve or import any asset, including stale legacy references")

    service = GamePackageService(GamePackageDependencies(
        parse_game_key=lambda key: tuple(key.split("|")),
        get_instance=registry.get,
        state_path_for=registry.save_package_state_path,
        import_save_zip=registry.import_save_zip,
        resolve_scene_image_file=unexpected_asset_access,
        resolve_map_background_file=unexpected_asset_access,
        save_scene_image_upload=unexpected_asset_access,
        save_map_background_upload=unexpected_asset_access,
    ))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("state.json", json.dumps(state))
        archive.writestr("scene-image.asset", b"scene")
        archive.writestr("map-background.asset", b"map")
        archive.writestr("chatlog.jsonl", '{"role":"gm"}\n')
    payload = buffer.getvalue()

    def filesystem_snapshot():
        return {path.relative_to(tmp_path): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob("*")}

    original_files = filesystem_snapshot()
    export = service.export_game_package("|".join(instance.game_key))
    package_import = await service.import_game_package(payload)
    direct_import = await persistence.import_save_zip(
        registry, payload,
        scene_image_importer=unexpected_asset_access,
        map_background_importer=unexpected_asset_access,
    )
    for result in (export, package_import, direct_import):
        assert result["ok"] is False
        assert result["error_code"] == "UNSUPPORTED_MEDIA_SCHEMA"
        assert result["status"] == 400
        assert "media module" in result["error"]
        assert "payload" not in result and "game_key" not in result
    assert filesystem_snapshot() == original_files
    assert state == before
    assert json.loads(state_path.read_text(encoding="utf-8")) == before
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert json.loads(archive.read("state.json")) == before
    assert registry.list_active() == [instance]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [True, False], ids=["legacy-top-level", "module-slot"])
async def test_export_then_import_preserves_both_asset_attachments(tmp_path, legacy):
    registry = GameRegistry(tmp_path / "saves")
    instance = registry.get_or_create(("web", "assets", "bot"))
    instance.scene_image = {"kind": "upload", "asset_id": "scene"}
    instance.map_background = {"kind": "upload", "asset_id": "map"}
    await registry.save(instance)
    state_path = registry.save_package_state_path(instance.game_key)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if legacy:
        slot = state["modules"].pop("media")
        state.update({key: slot[key] for key in ("scene_image", "map_background")})
        state["instance_schema_version"] = 22
        state_path.write_text(json.dumps(state), encoding="utf-8")
    original_bytes = state_path.read_bytes()
    files = {}
    for kind in ("scene", "map"):
        path = tmp_path / f"{kind}.asset"
        path.write_bytes(f"{kind}-image-bytes".encode())
        files[kind] = path
    imported_files = {}

    def upload(encoded, kind, field):
        path = tmp_path / f"imported-{kind}.asset"
        path.write_bytes(base64.b64decode(encoded))
        imported_files[kind] = path
        return {"ok": True, field: {"kind": "upload", "asset_id": f"imported-{kind}"}}

    service = GamePackageService(GamePackageDependencies(
        parse_game_key=lambda key: tuple(key.split("|")),
        get_instance=registry.get,
        state_path_for=registry.save_package_state_path,
        import_save_zip=registry.import_save_zip,
        resolve_scene_image_file=lambda reference: files["scene"] if reference == instance.scene_image else None,
        resolve_map_background_file=lambda reference: files["map"] if reference == instance.map_background else None,
        save_scene_image_upload=lambda encoded: upload(encoded, "scene", "scene_image"),
        save_map_background_upload=lambda encoded: upload(encoded, "map", "map_background"),
    ))
    exported = service.export_game_package("|".join(instance.game_key))
    assert exported["ok"] is True
    assert state_path.read_bytes() == original_bytes
    with zipfile.ZipFile(io.BytesIO(exported["payload"])) as archive:
        assert archive.read("scene-image.asset") == files["scene"].read_bytes()
        assert archive.read("map-background.asset") == files["map"].read_bytes()
        packed = json.loads(archive.read("state.json"))
        container = packed if legacy else packed["modules"]["media"]
        assert container["scene_image"] == {"kind": "save_asset", "path": "scene-image.asset"}
        assert container["map_background"] == {"kind": "save_asset", "path": "map-background.asset"}
    result = await service.import_game_package(exported["payload"])
    assert result["ok"] is True
    imported_key = tuple(result["game_key"].split("|"))
    restored = registry.get(imported_key)
    assert restored.scene_image == {"kind": "upload", "asset_id": "imported-scene"}
    assert restored.map_background == {"kind": "upload", "asset_id": "imported-map"}
    assert restored.run_id != instance.run_id
    for kind in ("scene", "map"):
        assert imported_files[kind].read_bytes() == files[kind].read_bytes()
    reloaded = await registry.load(imported_key)
    assert reloaded.scene_image == restored.scene_image
    assert reloaded.map_background == restored.map_background
