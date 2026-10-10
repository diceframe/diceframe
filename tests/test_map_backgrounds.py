import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from src.engine.modules import narrative_notes
from src.engine.game_instance import GameInstance, GameRegistry
from src.webui.services import map_backgrounds
from src.webui.services import maps as map_service
from src.engine.participant_view import Viewer
from src.engine.modules import media

GM_VIEWER = Viewer("gm", "gm")


class MapBackgroundApi:
    def __init__(self, tmp_path):
        self.map_backgrounds = map_backgrounds.MapBackgroundService(
            tmp_path / "map-backgrounds", lambda _asset_id: None,
        )

    def validate_map_background_selection(self, selection):
        return self.map_backgrounds.validate(selection)

    def map_background_file(self, asset_id):
        return self.map_backgrounds.file(asset_id)


def png_payload(size=(1200, 800), color=(27, 48, 68)) -> str:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_map_background_upload_preserves_aspect_ratio_and_deduplicates(tmp_path):
    api = MapBackgroundApi(tmp_path)

    first = api.map_backgrounds.save_upload(png_payload(), "map.png")
    second = api.map_backgrounds.save_upload(png_payload(), "copy.png")

    assert first["ok"] is True
    assert first["map_background"] == second["map_background"]
    path = api.map_backgrounds.resolve_file(first["map_background"])
    assert path is not None
    with Image.open(path) as image:
        assert image.size == (1200, 800)
        assert image.format == "WEBP"


@pytest.mark.parametrize("asset_id", [
    "fantasy-region-v1",
    "occult-town-v1",
    "cyber-city-v1",
])
def test_builtin_map_background_selections_are_valid(tmp_path, asset_id):
    api = MapBackgroundApi(tmp_path)
    assert api.map_backgrounds.validate(
        {"kind": "builtin", "id": asset_id},
    ) == {"kind": "builtin", "id": asset_id}


def test_map_background_selection_rejects_external_urls(tmp_path):
    api = MapBackgroundApi(tmp_path)
    with pytest.raises(ValueError):
        api.map_backgrounds.validate(
            {"kind": "url", "url": "https://example.com/map.png"},
        )


class GameMapApi(MapBackgroundApi):
    def __init__(self, tmp_path, selection):
        super().__init__(tmp_path)
        self.instance = SimpleNamespace(
            world_id="default_fantasy",
            rule_id="freeform_dnd",
            modules={
                "narrative_notes": narrative_notes.fresh(),
                "media": {**media.fresh(), "map_background": selection},
            },
        )
        self._plugins = None
        self._reg = SimpleNamespace(get=lambda _key: self.instance)
        self._lore = SimpleNamespace(list_entries=lambda _world, _kind: [])

    @staticmethod
    def _parse_key(game_key):
        return ("web", game_key, "web_bot")


async def _unused_save(_instance):
    return None


def _map_dependencies(api) -> map_service.MapDependencies:
    return map_service.MapDependencies(
        get_instance=api._reg.get,
        parse_game_key=api._parse_key,
        list_lore_entries=api._lore.list_entries,
        list_map_assets=lambda _world_id: {
            "maps": [], "locations": [], "icons": [], "scenes": [],
        },
        validate_background_selection=api.validate_map_background_selection,
        save_instance=_unused_save,
        load_world_template=lambda _world_id: None,
        map_background_file=api.map_background_file,
        generated_image_file=lambda _asset_id: None,
    )

def test_existing_game_can_disable_or_replace_automatic_background(tmp_path):
    disabled_api = GameMapApi(tmp_path, {"kind": "none"})
    disabled = map_service.get_map_locations(
        _map_dependencies(disabled_api), "save-1", viewer=GM_VIEWER,
    )
    occult_api = GameMapApi(
        tmp_path, {"kind": "builtin", "id": "occult-town-v1"},
    )
    occult = map_service.get_map_locations(
        _map_dependencies(occult_api),
        "save-1",
        viewer=GM_VIEWER,
    )

    assert disabled["active_map"]["background"] is None
    assert occult["active_map"]["background"]["url"].endswith("occult-town-v1.webp")
    assert occult["background_selection"] == {"kind": "builtin", "id": "occult-town-v1"}


def test_uploaded_background_uses_game_scoped_asset_url(tmp_path):
    api = GameMapApi(tmp_path, {"kind": "auto"})
    uploaded = api.map_backgrounds.save_upload(png_payload(), "map.png")
    media.replace_map_background(api.instance, uploaded["map_background"])

    result = map_service.get_map_locations(_map_dependencies(api), "save-1", viewer=GM_VIEWER)
    asset_id = uploaded["map_background"]["asset_id"]

    assert result["active_map"]["background"]["url"] == (
        f"/api/games/save-1/map-background-asset/{asset_id}"
    )


def _persistent_map_dependencies(
    registry: GameRegistry,
    backgrounds: map_backgrounds.MapBackgroundService,
) -> map_service.MapDependencies:
    return map_service.MapDependencies(
        get_instance=registry.get,
        parse_game_key=lambda game_key: tuple(game_key.split("|")),
        list_lore_entries=lambda _world_id, _entry_type: [],
        list_map_assets=lambda _world_id: {
            "maps": [], "locations": [], "icons": [], "scenes": [],
        },
        validate_background_selection=backgrounds.validate,
        save_instance=registry.save,
        load_world_template=lambda _world_id: None,
        map_background_file=backgrounds.file,
        generated_image_file=lambda _asset_id: None,
    )


@pytest.mark.asyncio
async def test_map_background_update_is_persisted(tmp_path):
    registry = GameRegistry(tmp_path / "saves")
    backgrounds = map_backgrounds.MapBackgroundService(
        tmp_path / "map-backgrounds", lambda _asset_id: None,
    )
    instance = GameInstance(
        game_key=("web", "map-save", "web_bot"),
        world_id="default_fantasy",
    )
    registry.register(instance)

    result = await map_service.update_map_background(
        _persistent_map_dependencies(registry, backgrounds),
        "web|map-save|web_bot",
        {"kind": "none"},
    )

    assert result["ok"] is True
    persisted = GameInstance.from_dict(
        json.loads(
            registry._save_path(instance.game_key).read_text(encoding="utf-8")
        )
    )
    assert media.map_background(persisted) == {"kind": "none"}


def test_map_background_asset_is_scoped_to_the_game_selection(tmp_path):
    registry = GameRegistry(tmp_path / "saves")
    backgrounds = map_backgrounds.MapBackgroundService(
        tmp_path / "map-backgrounds", lambda _asset_id: None,
    )
    uploaded = backgrounds.save_upload(png_payload(), "map.png")
    selection = uploaded["map_background"]
    instance = GameInstance(
        game_key=("web", "map-asset", "web_bot"),
        world_id="default_fantasy",
    )
    instance.set_map_background(selection)
    registry.register(instance)
    dependencies = _persistent_map_dependencies(registry, backgrounds)

    assert map_service.map_background_asset(
        dependencies,
        "web|map-asset|web_bot",
        selection["asset_id"],
    ) == backgrounds.file(selection["asset_id"])
    assert map_service.map_background_asset(
        dependencies,
        "web|map-asset|web_bot",
        "another-upload",
    ) is None
