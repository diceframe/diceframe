"""Built-in image-generation HTTP handlers and game authorization."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.imagegen import ImageGenerationError, ImageGenerationResult
from src.webui.routes import generated_images
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY
from src.webui.services.generated_images import (
    GeneratedImageDependencies,
    GeneratedImageService,
)


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
    def __init__(self, file_path: Path, *, error: str = "", llm_client=None):
        self._imagegen = _FakeImageGenerationService(file_path, error=error)
        self._reg = _Registry(_FakeInstance())
        self.background_updates = []
        self.generated_images = GeneratedImageService(GeneratedImageDependencies(
            imagegen=self._imagegen,
            get_instance=self.get_game_instance,
            update_map_background=self.update_map_background,
            llm_client=llm_client,
        ))

    def _parse_key(self, game_key):
        return tuple(game_key.split("|"))

    def get_game_instance(self, game_key):
        return self._reg.get(self._parse_key(game_key))

    def generated_image_file(self, asset_id):
        return self.generated_images.image_file(asset_id)

    def image_generation_status(self):
        return self.generated_images.public_config()

    async def optimize_image_prompt(self, **request):
        return await self.generated_images.optimize_prompt(**request)

    async def generate_generated_image(self, **request):
        return await self.generated_images.generate_image(**request)

    def list_game_generated_images(self, game_key, user_id, *, purpose=""):
        return self.generated_images.list_game_images(
            game_key, user_id, purpose=purpose,
        )

    async def use_generated_image_as_map_background(
        self, game_key, user_id, asset_id,
    ):
        return await self.generated_images.use_as_map_background(
            game_key, user_id, asset_id,
        )

    async def update_map_background(self, game_key, selection):
        self.background_updates.append((game_key, selection))
        return {"ok": True, "map_background": selection}


class _StoryboardLlm:
    """Small deterministic public-storyboard model used by service tests."""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    async def call(self, *_args, **_kwargs):
        self.calls += 1
        return SimpleNamespace(narration=self.content, content=self.content)


def _storyboard_instance() -> SimpleNamespace:
    return SimpleNamespace(
        gm_uid="gm",
        game_key=("web", "room", "bot"),
        run_id="run-1",
        scene="雾港",
        players={
            "alice": {"character_name": "Alice"},
            "bob": {"character_name": "Bob"},
        },
        log=[{
            "round": 3,
            "gm_response": "Alice在码头守望。与此同时，Bob在塔底检查残骸。",
            "actions": [],
            "current_swipe": 0,
            "scene_panels": [],
        }],
        scene_image=None,
    )


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
        player_preview=False,
        access_password_configured=False,
        asset_id=ASSET_ID,
        confirmed=True,
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
            "player_preview": player_preview,
            ACCESS_PASSWORD_CONFIGURED_KEY: access_password_configured,
        }
        self.can_read_body = body is not None
        self.headers = {"X-TRPG-Confirm": "true"} if confirmed else {}

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
async def test_prompt_optimizer_is_admin_only_and_returns_preview(tmp_path):
    class _Llm:
        async def call(self, **kwargs):
            assert kwargs["temperature"] == 0.3
            assert "{scene}" in kwargs["user_message"]
            return SimpleNamespace(
                narration="Cinematic scene: {scene}",
                content="Cinematic scene: {scene}",
            )

    api = _FakeApi(tmp_path, llm_client=_Llm())
    denied = await generated_images.api_optimize_image_prompt(_Request(
        api,
        body={"field": "manual_prompt", "text": "Draw {scene}"},
        query={"share": "1"},
        game_key="",
    ))
    assert denied.status == 403

    response = await generated_images.api_optimize_image_prompt(_Request(
        api,
        body={
            "field": "manual_prompt",
            "text": "Draw {scene}",
            "language": "en",
        },
        game_key="",
        access_password_configured=True,
        owner_authenticated=True,
    ))
    assert response.status == 200
    assert json.loads(response.text) == {
        "ok": True,
        "text": "Cinematic scene: {scene}",
        "input_chars": 12,
        "output_chars": 24,
    }


@pytest.mark.asyncio
async def test_prompt_optimizer_rejects_dropped_or_unknown_variables(tmp_path):
    class _Llm:
        async def call(self, **_kwargs):
            return SimpleNamespace(narration="Cinematic harbor", content="")

    service = GeneratedImageService(GeneratedImageDependencies(
        imagegen=None,
        get_instance=lambda _key: None,
        update_map_background=lambda *_args: None,
        llm_client=_Llm(),
    ))
    with pytest.raises(ImageGenerationError, match="遗漏"):
        await service.optimize_prompt(
            field="auto_prompt", text="Draw {scene}", language="zh-CN",
        )
    with pytest.raises(ImageGenerationError, match="不支持的模板变量"):
        await service.optimize_prompt(
            field="auto_prompt", text="Draw {secret}", language="zh-CN",
        )


def test_avatar_references_only_include_explicit_panel_participants(tmp_path):
    alice_avatar = tmp_path / "alice.webp"
    bob_avatar = tmp_path / "bob.webp"
    alice_avatar.write_bytes(b"alice")
    bob_avatar.write_bytes(b"bob")
    instance = SimpleNamespace(players={
        "alice": {"character_sheet": {"portrait": {"kind": "upload", "asset_id": "a"}}},
        "bob": {"character_sheet": {"portrait": {"kind": "upload", "asset_id": "b"}}},
    })
    service = GeneratedImageService(GeneratedImageDependencies(
        imagegen=None,
        get_instance=lambda _key: instance,
        update_map_background=lambda *_args: None,
        avatar_file=lambda asset_id: {"a": alice_avatar, "b": bob_avatar}.get(asset_id),
    ))

    references = service._avatar_references(instance, [
        {"participants": ["alice"]},
        {"participants": []},
    ])

    assert [reference.character_id for reference in references] == ["alice"]


@pytest.mark.asyncio
async def test_multiplayer_avatar_generation_uses_only_effective_reference_labels(tmp_path):
    """Missing portraits must not create an N-files-to-N+1-names mapping."""

    alice_avatar = tmp_path / "alice.webp"
    bob_avatar = tmp_path / "bob.webp"
    alice_avatar.write_bytes(b"alice")
    bob_avatar.write_bytes(b"bob")

    class _Instance(SimpleNamespace):
        def set_scene_image(self, reference):
            self.scene_image = reference

    instance = _Instance(
        gm_uid="gm",
        game_key=("web", "room", "bot"),
        run_id="run-1",
        round_number=3,
        scene="雾港",
        players={
            "alice": {
                "character_name": "Alice",
                "character_sheet": {
                    "portrait": {"kind": "upload", "asset_id": "a"},
                },
            },
            "bob": {
                "character_name": "Bob",
                "character_sheet": {
                    "portrait": {"kind": "upload", "asset_id": "b"},
                },
            },
            # Charlie is visibly present but has no uploaded portrait.
            "charlie": {"character_name": "Charlie", "character_sheet": {}},
        },
        log=[{
            "round": 3,
            "gm_response": "Alice、Bob和Charlie一起站在雾港码头。",
            "actions": [],
            "current_swipe": 0,
            "scene_panels": [],
        }],
        scene_image=None,
    )

    class _UnexpectedLlm:
        calls = 0

        async def call(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("manual single-image generation must not infer a storyboard")

    async def save(_instance):
        return None

    imagegen = _FakeImageGenerationService(tmp_path / "unused.webp")
    imagegen.auto_storyboard = True
    llm = _UnexpectedLlm()
    service = GeneratedImageService(GeneratedImageDependencies(
        imagegen=imagegen,
        get_instance=lambda _key: instance,
        update_map_background=lambda *_args: None,
        avatar_file=lambda asset_id: {
            "a": alice_avatar,
            "b": bob_avatar,
        }.get(asset_id),
        save_instance=save,
        llm_client=llm,
    ))

    result = await service.generate_current_round(
        "web|room|bot",
        "gm",
        "Alice、Bob和Charlie在码头并肩调查。",
        3,
        panels=[],
        use_avatar_references=True,
    )

    assert result["ok"] is True
    assert llm.calls == 0
    request = imagegen.requests[-1]
    assert [reference.character_id for reference in request.reference_images] == [
        "alice",
        "bob",
    ]
    assert request.context["avatar_reference_names"] == {
        "alice": "Alice",
        "bob": "Bob",
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
async def test_owner_authenticated_session_uses_persisted_gm_identity(tmp_path):
    """The local owner session id (web_*) is not the game's gm_uid."""
    api = _FakeApi(tmp_path)
    owner = _Request(
        api,
        user_id="web_local_session",
        owner_authenticated=True,
    )

    generated = await generated_images.api_generate_image(
        _Request(
            api,
            user_id="web_local_session",
            owner_authenticated=True,
            body={"prompt": "harbor", "purpose": "scene"},
        )
    )
    assert generated.status == 200

    history = await generated_images.api_game_generated_images(owner)
    assert history.status == 200

    background = await generated_images.api_generated_image_as_map_background(owner)
    assert background.status == 200

    preview = await generated_images.api_generate_image(
        _Request(
            api,
            user_id="player",
            owner_authenticated=True,
            player_preview=True,
            body={"prompt": "harbor", "purpose": "scene"},
        )
    )
    assert preview.status == 403


@pytest.mark.asyncio
async def test_owner_identity_is_forwarded_to_current_round_route(tmp_path):
    api = _FakeApi(tmp_path)
    seen = {}

    async def fake_generate_current_round_image(
        game_key, user_id, prompt, round_number, panels, use_avatar_references,
        panel_count, combine_avatar_references,
    ):
        seen["game_key"] = game_key
        seen["user_id"] = user_id
        assert combine_avatar_references is True
        return {"ok": True}

    api.generate_current_round_image = fake_generate_current_round_image
    response = await generated_images.api_generate_current_round_image(
        _Request(
            api,
            user_id="web_local_session",
            owner_authenticated=True,
            body={"prompt": "harbor"},
        )
    )

    assert response.status == 200
    assert seen == {"game_key": "web|room|bot", "user_id": "gm"}


@pytest.mark.asyncio
async def test_storyboard_candidate_is_persisted_and_read_after_service_recreation(tmp_path):
    """Closing/unmounting the modal must not lose an unapplied candidate."""
    instance = _storyboard_instance()
    saves = []

    async def save(current):
        saves.append(current)

    imagegen = _FakeImageGenerationService(tmp_path / "unused.webp")
    imagegen.auto_storyboard = True
    llm = _StoryboardLlm(
        '{"panels":['
        '{"participants":["alice"],"location":"码头",'
        '"description":"Alice在雾中守望","evidence_ids":["n1"]},'
        '{"participants":["bob"],"location":"塔底",'
        '"description":"Bob检查潮湿残骸","evidence_ids":["n2"]}]}',
    )
    deps = GeneratedImageDependencies(
        imagegen=imagegen,
        get_instance=lambda _key: instance,
        update_map_background=lambda *_args: None,
        save_instance=save,
        llm_client=llm,
    )
    first = GeneratedImageService(deps)

    result = await first.analyze_storyboard("web|room|bot", "gm", 3)
    assert result["ok"] is True
    assert len(result["panels"]) == 2
    assert instance.log[-1]["scene_panels"] == []
    assert instance.log[-1]["scene_panel_candidate"]["requested_panel_count"] is None
    assert len(saves) == 1

    # A new service instance represents a dialog/component being recreated;
    # the persisted candidate, not the in-memory cache, is the source of truth.
    recreated = GeneratedImageService(deps)
    draft = recreated.storyboard_draft("web|room|bot", "gm", 3)
    assert draft["panels"] == []
    assert draft["candidate"]["panels"] == result["panels"]
    assert draft["candidate"]["requested_panel_count"] is None


@pytest.mark.asyncio
async def test_stale_storyboard_candidate_is_ignored_after_applied_draft_changes(tmp_path):
    instance = _storyboard_instance()
    imagegen = _FakeImageGenerationService(tmp_path / "unused.webp")
    imagegen.auto_storyboard = True
    llm = _StoryboardLlm(
        '{"panels":[{"participants":["alice"],"location":"码头",'
        '"description":"Alice在雾中守望","evidence_ids":["n1"]},'
        '{"participants":["bob"],"location":"塔底",'
        '"description":"Bob检查潮湿残骸","evidence_ids":["n2"]}]}',
    )
    service = GeneratedImageService(GeneratedImageDependencies(
        imagegen=imagegen,
        get_instance=lambda _key: instance,
        update_map_background=lambda *_args: None,
        llm_client=llm,
    ))
    result = await service.analyze_storyboard("web|room|bot", "gm", 3)
    assert result["ok"] is True
    candidate = instance.log[-1]["scene_panel_candidate"]

    # Applying/editing a draft changes the source revision.  The old
    # candidate must not be shown as if it were still applicable.
    instance.log[-1]["scene_panels"] = [{
        "participants": ["alice"],
        "location": "码头",
        "description": "已应用的单图草稿",
    }]
    assert instance.log[-1]["scene_panel_candidate"] == candidate
    draft = service.storyboard_draft("web|room|bot", "gm", 3)
    assert draft["panels"][0]["description"] == "已应用的单图草稿"
    assert draft["candidate"] is None


@pytest.mark.asyncio
async def test_fixed_count_candidate_is_bound_to_requested_count(tmp_path):
    instance = _storyboard_instance()
    imagegen = _FakeImageGenerationService(tmp_path / "unused.webp")
    imagegen.auto_storyboard = True
    llm = _StoryboardLlm(
        '{"panels":[{"participants":["alice"],"location":"码头",'
        '"description":"Alice守望","evidence_ids":["n1"]},'
        '{"participants":["bob"],"location":"塔底",'
        '"description":"Bob检查","evidence_ids":["n2"]}]}',
    )
    service = GeneratedImageService(GeneratedImageDependencies(
        imagegen=imagegen,
        get_instance=lambda _key: instance,
        update_map_background=lambda *_args: None,
        llm_client=llm,
    ))
    result = await service.analyze_storyboard("web|room|bot", "gm", 3, panel_count=2)
    assert result["ok"] is True
    assert instance.log[-1]["scene_panel_candidate"]["requested_panel_count"] == 2

    # The generic draft projection still exposes it for a reopened dialog,
    # while a mismatched fixed-count lookup rejects it.
    draft = service.storyboard_draft("web|room|bot", "gm", 3)
    assert draft["candidate"]["requested_panel_count"] == 2
    assert service._read_persisted_candidate(
        instance.log[-1], requested_panel_count=3,
    ) is None


@pytest.mark.asyncio
async def test_fixed_count_failed_analysis_is_not_cached_or_saved(tmp_path):
    instance = _storyboard_instance()
    imagegen = _FakeImageGenerationService(tmp_path / "unused.webp")
    imagegen.auto_storyboard = True
    llm = _StoryboardLlm(json.dumps({"panels": [
        {"participants": ["alice"], "location": "码头", "description": f"动作{i}", "evidence_ids": ["n1"]}
        for i in range(3)
    ]}))
    service = GeneratedImageService(GeneratedImageDependencies(
        imagegen=imagegen, get_instance=lambda _key: instance,
        update_map_background=lambda *_args: None, llm_client=llm,
    ))
    automatic = await service.analyze_storyboard("web|room|bot", "gm", 3)
    assert automatic["ok"] and len(automatic["panels"]) == 3
    old_candidate = instance.log[-1]["scene_panel_candidate"].copy()
    for _ in range(2):
        result = await service.analyze_storyboard("web|room|bot", "gm", 3, panel_count=6)
        assert result["ok"] is False and "要求 6 格" in result["error"]
    assert llm.calls == 5  # automatic once, then two attempts per fixed request.
    assert instance.log[-1]["scene_panel_candidate"] == old_candidate
    assert all(key[2] != 6 for key in service._storyboard_cache)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", range(1, 7))
async def test_fixed_generation_keeps_count_in_request_and_saved_layout(tmp_path, count):
    instance = _storyboard_instance()
    instance.set_scene_image = lambda reference: setattr(instance, "scene_image", reference)
    imagegen = _FakeImageGenerationService(tmp_path / "unused.webp")
    imagegen.auto_storyboard = True
    panels = [
        {"participants": ["alice"], "location": "码头", "description": f"动作{i}"}
        for i in range(count)
    ]

    async def save(_instance):
        pass

    service = GeneratedImageService(GeneratedImageDependencies(
        imagegen=imagegen, get_instance=lambda _key: instance,
        update_map_background=lambda *_args: None, save_instance=save,
    ))
    rejected = await service.generate_current_round("web|room|bot", "gm", "公开要求", 3, panels + [panels[0]], False, count)
    assert not rejected["ok"] and not imagegen.requests
    result = await service.generate_current_round("web|room|bot", "gm", "公开要求", 3, panels, False, count)
    assert result["ok"]
    assert len(result["panels"]) == count
    assert imagegen.requests[0].context["requested_panel_count"] == count
    assert imagegen.requests[0].context["storyboard"]["panels"] == panels
    assert instance.log[-1]["scene_image"]["panels"] == panels
    if count == 6:
        assert result["layout"] == instance.log[-1]["scene_image"]["layout"] == "six-panel"


@pytest.mark.asyncio
async def test_current_round_route_forwards_uncombined_reference_choice(tmp_path):
    api = _FakeApi(tmp_path)

    async def generate(*args):
        assert args[-1] is False
        return {"ok": True}

    api.generate_current_round_image = generate
    result = await generated_images.api_generate_current_round_image(_Request(
        api, owner_authenticated=True,
        body={"prompt": "harbor", "combine_avatar_references": False},
    ))
    assert result.status == 200
    result = await generated_images.api_generate_current_round_image(_Request(
        api, body={"prompt": "harbor", "combine_avatar_references": "false"},
    ))
    assert result.status == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 7, 2.5, True, "6"])
async def test_storyboard_routes_reject_invalid_fixed_counts(tmp_path, count):
    api = _FakeApi(tmp_path)
    for route in (generated_images.api_analyze_storyboard, generated_images.api_generate_current_round_image):
        result = await route(_Request(api, body={"prompt": "harbor", "panel_count": count}))
        assert result.status == 400


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
