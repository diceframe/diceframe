"""Built-in image-generation HTTP handlers and game authorization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.imagegen import ImageGenerationError, ImageGenerationResult
from src.webui.routes import generated_images
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY


ASSET_ID = "a" * 64


class _FakeAssets:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.queries = []

    def file(self, asset_id):
        return self.file_path if asset_id == ASSET_ID else None

    def list_records(self, **filters):
        self.queries.append(filters)
        return [{
            "generation_id": "1" * 32,
            "asset_id": ASSET_ID,
            "purpose": "scene",
            "prompt": "harbor",
            "context": {"round": 2},
            "created_at": "2026-08-22T00:00:00+00:00",
        }]


class _FakeImageGenerationService:
    enabled = True
    available = True
    provider_id = "openai-compatible"
    model = "image-model"
    auto_scene = True

    def __init__(self, file_path: Path, *, error: str = ""):
        self.assets = _FakeAssets(file_path)
        self.error = error
        self.requests = []

    def public_config(self):
        return {
            "enabled": self.enabled,
            "available": self.available,
            "provider": self.provider_id,
            "model": self.model,
            "auto_scene": self.auto_scene,
        }

    async def generate(self, request):
        self.requests.append(request)
        if self.error:
            raise ImageGenerationError(self.error)
        return ImageGenerationResult(
            generation_id="1" * 32,
            asset_id=ASSET_ID,
            purpose=request.purpose,
            prompt=request.prompt,
            revised_prompt="",
            provider=self.provider_id,
            model=self.model,
            created_at="2026-08-22T00:00:00+00:00",
        )


class _FakeInstance:
    def __init__(self, *, gm_uid="gm", players=("player",)):
        self.game_key = ("web", "room", "bot")
        self.gm_uid = gm_uid
        self.players = dict.fromkeys(players)


class _Registry:
    def __init__(self, instance):
        self.instance = instance

    def get(self, key):
        return self.instance if key == self.instance.game_key else None


class _FakeApi:
    def __init__(self, file_path: Path, *, error: str = ""):
        self._imagegen = _FakeImageGenerationService(file_path, error=error)
        self._reg = _Registry(_FakeInstance())
        self.background_updates = []

    def _parse_key(self, game_key):
        return tuple(game_key.split("|"))

    def generated_image_file(self, asset_id):
        return self._imagegen.assets.file(asset_id)

    async def update_map_background(self, game_key, selection):
        self.background_updates.append((game_key, selection))
        return {"ok": True, "map_background": selection}


class _Request:
    def __init__(
        self,
        api,
        *,
        user_id="player",
        body=None,
        query=None,
        game_key="web|room|bot",
        owner_authenticated=False,
        access_password_configured=False,
        asset_id=ASSET_ID,
    ):
        self.app = {"api": api}
        self.match_info = {"asset_id": asset_id}
        if game_key:
            self.match_info["game_key"] = game_key
        self._body = body
        self.query = query or {}
        self._values = {
            "user_id": user_id,
            "owner_authenticated": owner_authenticated,
            ACCESS_PASSWORD_CONFIGURED_KEY: access_password_configured,
        }
        self.can_read_body = body is not None

    def get(self, key, default=None):
        return self._values.get(key, default)

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_status_exposes_public_image_generation_config(tmp_path):
    response = await generated_images.api_image_generation_status(_Request(_FakeApi(tmp_path)))
    assert response.status == 200
    assert json.loads(response.text) == {
        "enabled": True,
        "available": True,
        "provider": "openai-compatible",
        "model": "image-model",
        "auto_scene": True,
    }


@pytest.mark.asyncio
async def test_global_generation_requires_admin_and_returns_generated_reference(tmp_path):
    api = _FakeApi(tmp_path)
    denied = await generated_images.api_generate_image(_Request(
        api,
        user_id="",
        game_key="",
        body={"prompt": "harbor", "purpose": "freeform"},
        access_password_configured=True,
    ))
    assert denied.status == 403

    response = await generated_images.api_generate_image(_Request(
        api,
        user_id="",
        game_key="",
        body={"prompt": "harbor", "purpose": "freeform", "style": "ink"},
        access_password_configured=True,
        owner_authenticated=True,
    ))
    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["reference"] == {"kind": "generated", "asset_id": ASSET_ID}
    request = api._imagegen.requests[-1]
    assert request.owner_type == "library"
    assert request.owner_id == "local"
    assert request.style == "ink"


@pytest.mark.asyncio
async def test_game_generation_enforces_purpose_permissions(tmp_path):
    api = _FakeApi(tmp_path)
    avatar = await generated_images.api_generate_image(_Request(
        api,
        user_id="player",
        body={"prompt": "young investigator", "purpose": "avatar"},
    ))
    assert avatar.status == 200
    assert api._imagegen.requests[-1].owner_id == "web:room:bot"

    scene_denied = await generated_images.api_generate_image(_Request(
        api,
        user_id="player",
        body={"prompt": "harbor", "purpose": "scene"},
    ))
    assert scene_denied.status == 403

    scene = await generated_images.api_generate_image(_Request(
        api,
        user_id="gm",
        body={"prompt": "harbor", "purpose": "scene", "context": {"round": 2}},
    ))
    assert scene.status == 200
    assert api._imagegen.requests[-1].context == {"round": 2}

    outsider = await generated_images.api_generate_image(_Request(
        api,
        user_id="stranger",
        body={"prompt": "portrait", "purpose": "avatar"},
    ))
    assert outsider.status == 403


@pytest.mark.asyncio
async def test_generation_validates_input_and_maps_service_errors(tmp_path):
    api = _FakeApi(tmp_path)
    empty = await generated_images.api_generate_image(_Request(api, body={"prompt": "  "}))
    assert empty.status == 400
    invalid = await generated_images.api_generate_image(_Request(
        api,
        user_id="gm",
        body={"prompt": "x", "purpose": "cover"},
    ))
    assert invalid.status == 400

    failing = _FakeApi(tmp_path, error="upstream down")
    response = await generated_images.api_generate_image(_Request(
        failing,
        user_id="gm",
        body={"prompt": "harbor", "purpose": "scene"},
    ))
    assert response.status == 400
    assert json.loads(response.text)["error"] == "upstream down"


@pytest.mark.asyncio
async def test_game_history_filters_by_owner_and_purpose(tmp_path):
    api = _FakeApi(tmp_path)
    response = await generated_images.api_game_generated_images(_Request(
        api,
        query={"purpose": "scene"},
    ))
    assert response.status == 200
    images = json.loads(response.text)["images"]
    assert images[0]["round"] == 2
    assert api._imagegen.assets.queries == [{
        "owner_type": "game",
        "owner_id": "web:room:bot",
        "purpose": "scene",
    }]

    outsider = await generated_images.api_game_generated_images(_Request(
        api,
        user_id="stranger",
    ))
    assert outsider.status == 403


@pytest.mark.asyncio
async def test_generated_asset_file_and_map_background(tmp_path):
    image_path = tmp_path / "generated.webp"
    image_path.write_bytes(b"image")
    api = _FakeApi(image_path)

    file_response = await generated_images.api_generated_image_file(_Request(api, game_key=""))
    assert file_response.status == 200
    assert Path(file_response._path) == image_path
    missing = await generated_images.api_generated_image_file(_Request(api, game_key="", asset_id="c" * 64))
    assert missing.status == 404

    scoped = await generated_images.api_generated_image_file(_Request(api, user_id="player"))
    assert scoped.status == 200
    scoped_denied = await generated_images.api_generated_image_file(_Request(api, user_id="stranger"))
    assert scoped_denied.status == 403

    denied = await generated_images.api_generated_image_as_map_background(_Request(
        api,
        user_id="player",
    ))
    assert denied.status == 400
    applied = await generated_images.api_generated_image_as_map_background(_Request(
        api,
        user_id="gm",
    ))
    assert applied.status == 200
    assert api.background_updates == [
        ("web|room|bot", {"kind": "generated", "asset_id": ASSET_ID}),
    ]
