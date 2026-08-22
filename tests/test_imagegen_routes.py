"""图像生成路由：插件测试、画廊与场景图转地图背景（fake 直调 handler）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.imagegen import ImageGenError
from src.webui.routes import imagegen


class _FakeGenerator:
    def __init__(self, *, available=True, error=""):
        self.available_flag = available
        self.error = error
        self.prompts = []

    def available(self):
        return self.available_flag

    async def generate(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise ImageGenError(self.error)
        return {
            "reference": {"kind": "upload", "asset_id": "a" * 64},
            "asset_id": "a" * 64,
            "prompt": prompt,
            "revised_prompt": "",
        }


class _FakeInstance:
    def __init__(self, *, gm_uid="gm", players=("player",)):
        self.gm_uid = gm_uid
        self.players = dict.fromkeys(players)
        self.log = [
            {"round": 1, "gm_response": "a", "scene_image": {
                "status": "ready", "prompt": "harbor",
                "reference": {"kind": "upload", "asset_id": "a" * 64},
            }},
            {"round": 2, "gm_response": "b"},
        ]


class _FakeApi:
    def __init__(self, *, generator=None, instance=None):
        self._imagegen = generator
        self._fake_instance = instance or _FakeInstance()
        self.background_updates = []

    class _reg:
        @staticmethod
        def get(key):
            return None

    registry = _reg()

    def _parse_key(self, game_key):
        return tuple(game_key.split("|"))

    def scene_image_file(self, asset_id):
        if asset_id == "a" * 64:
            return Path(__file__)
        return None

    def save_map_background_upload(self, file_data, file_name=""):
        return {"ok": True, "map_background": {"kind": "upload", "asset_id": "b" * 64}}

    async def update_map_background(self, game_key, selection):
        self.background_updates.append((game_key, selection))
        return {"ok": True, "map_background": selection}


class _GameRegistry(_FakeApi._reg):
    def __init__(self, instance):
        self.instance = instance

    def get(self, key):
        return self.instance


def _api_with_game(generator=None):
    api = _FakeApi(generator=generator)
    api._reg = _GameRegistry(api._fake_instance)
    return api


class _Request:
    def __init__(self, api, *, user_id="player", body=None, query=None):
        self.app = {"api": api}
        self.match_info = {"game_key": "web|room|bot"}
        self._body = body or {}
        self.query = query or {}
        self._user_id = user_id
        self.can_read_body = body is not None

    def get(self, key, default=None):
        return self._user_id if key == "user_id" else default

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_imagegen_test_route_returns_asset_for_admin():
    generator = _FakeGenerator()
    response = await imagegen.api_test_imagegen(
        _Request(_api_with_game(generator), user_id="", body={"prompt": "harbor"}),
    )

    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["ok"] is True
    assert payload["asset_id"] == "a" * 64
    assert generator.prompts == ["harbor"]


@pytest.mark.asyncio
async def test_imagegen_test_route_rejects_empty_prompt_and_share_page():
    api = _api_with_game(_FakeGenerator())
    empty = await imagegen.api_test_imagegen(_Request(api, user_id="", body={"prompt": "  "}))
    assert empty.status == 400

    share = await imagegen.api_test_imagegen(
        _Request(api, user_id="", body={"prompt": "x"}, query={"user": "p"}),
    )
    assert share.status == 403


@pytest.mark.asyncio
async def test_imagegen_test_route_maps_service_error():
    api = _api_with_game(_FakeGenerator(error="upstream down"))
    response = await imagegen.api_test_imagegen(
        _Request(api, user_id="", body={"prompt": "harbor"}),
    )
    assert response.status == 400
    assert json.loads(response.text)["error"] == "upstream down"


@pytest.mark.asyncio
async def test_game_images_route_lists_ready_images_desc():
    response = await imagegen.api_game_images(_Request(_api_with_game()))

    assert response.status == 200
    images = json.loads(response.text)["images"]
    assert [item["round"] for item in images] == [1]
    assert images[0]["asset_id"] == "a" * 64
    assert images[0]["prompt"] == "harbor"


@pytest.mark.asyncio
async def test_game_images_route_rejects_non_members():
    outsider = await imagegen.api_game_images(
        _Request(_api_with_game(), user_id="stranger"),
    )
    assert outsider.status == 403


@pytest.mark.asyncio
async def test_map_background_from_scene_requires_gm_and_valid_asset():
    api = _api_with_game()

    player = await imagegen.api_map_background_from_scene(
        _Request(api, user_id="player", body={"asset_id": "a" * 64}),
    )
    assert player.status == 400  # 非 GM：返回业务错误
    assert api.background_updates == []

    gm_missing = await imagegen.api_map_background_from_scene(
        _Request(api, user_id="gm", body={"asset_id": "c" * 64}),
    )
    assert gm_missing.status == 400

    gm_ok = await imagegen.api_map_background_from_scene(
        _Request(api, user_id="gm", body={"asset_id": "a" * 64}),
    )
    assert gm_ok.status == 200
    assert json.loads(gm_ok.text)["ok"] is True
    assert api.background_updates == [("web|room|bot", {"kind": "upload", "asset_id": "b" * 64})]
