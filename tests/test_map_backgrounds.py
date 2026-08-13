import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from src.webui.services import map_backgrounds
from src.webui.services import maps as map_service


class MapBackgroundApi:
    def __init__(self, tmp_path):
        self._map_backgrounds_dir = tmp_path / "map-backgrounds"


def png_payload(size=(1200, 800), color=(27, 48, 68)) -> str:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_map_background_upload_preserves_aspect_ratio_and_deduplicates(tmp_path):
    api = MapBackgroundApi(tmp_path)

    first = map_backgrounds.save_map_background_upload(api, png_payload(), "map.png")
    second = map_backgrounds.save_map_background_upload(api, png_payload(), "copy.png")

    assert first["ok"] is True
    assert first["map_background"] == second["map_background"]
    path = map_backgrounds.resolve_map_background_file(api, first["map_background"])
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
    assert map_backgrounds.validate_map_background_selection(
        api,
        {"kind": "builtin", "id": asset_id},
    ) == {"kind": "builtin", "id": asset_id}


def test_map_background_selection_rejects_external_urls(tmp_path):
    api = MapBackgroundApi(tmp_path)
    with pytest.raises(ValueError):
        map_backgrounds.validate_map_background_selection(
            api,
            {"kind": "url", "url": "https://example.com/map.png"},
        )


class GameMapApi(MapBackgroundApi):
    def __init__(self, tmp_path, selection):
        super().__init__(tmp_path)
        self.instance = SimpleNamespace(
            world_id="default_fantasy",
            rule_id="freeform_dnd",
            scene="",
            map_background=selection,
        )
        self._plugins = None
        self._reg = SimpleNamespace(get=lambda _key: self.instance)
        self._lore = SimpleNamespace(list_entries=lambda _world, _kind: [])

    @staticmethod
    def _parse_key(game_key):
        return ("web", game_key, "web_bot")

    def validate_map_background_selection(self, selection):
        return map_backgrounds.validate_map_background_selection(self, selection)

    def map_background_file(self, asset_id):
        return map_backgrounds.map_background_file(self, asset_id)


def test_existing_game_can_disable_or_replace_automatic_background(tmp_path):
    disabled = map_service.get_map_locations(GameMapApi(tmp_path, {"kind": "none"}), "save-1")
    occult = map_service.get_map_locations(
        GameMapApi(tmp_path, {"kind": "builtin", "id": "occult-town-v1"}),
        "save-1",
    )

    assert disabled["active_map"]["background"] is None
    assert occult["active_map"]["background"]["url"].endswith("occult-town-v1.webp")
    assert occult["background_selection"] == {"kind": "builtin", "id": "occult-town-v1"}


def test_uploaded_background_uses_game_scoped_asset_url(tmp_path):
    api = GameMapApi(tmp_path, {"kind": "auto"})
    uploaded = map_backgrounds.save_map_background_upload(api, png_payload(), "map.png")
    api.instance.map_background = uploaded["map_background"]

    result = map_service.get_map_locations(api, "save-1")
    asset_id = uploaded["map_background"]["asset_id"]

    assert result["active_map"]["background"]["url"] == (
        f"/api/games/save-1/map-background-asset/{asset_id}"
    )
