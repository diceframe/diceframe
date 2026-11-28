"""foundry-02: FVTT Scene import preview/commit (GM-only, digest-guarded)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.application_actions import ActionRegistry
from src.compat.foundry import import_scene
from src.compat.foundry.import_plan import claimed_plan, compute_plan_digest
from src.engine.game_instance import GameInstance
from src.engine.modules import tactical_scene
from src.engine.participant_view import resolve_viewer
from src.webui.routes.foundry import register_foundry
from src.webui.services import foundry_import as foundry_import_service
from src.webui.services import tactical_scene_ops as tactical_scene_ops_service
from src.webui.services import tactical_scene_read as tactical_scene_read_service
from src.webui.services.assistant_foundry_tools import (
    FoundryToolDependencies,
    foundry_tools,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "foundry"
GAME_KEY = "g"


def _raw_scene(folder: str = "v14/scene", name: str = "scene_core.json") -> dict:
    return json.loads((FIXTURES / folder / name).read_text(encoding="utf-8"))


def _instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "g", "u"), players={"p1": {}})
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


def _deps(instance: GameInstance, saver: _Saver | None = None):
    return foundry_import_service.FoundryImportDependencies(
        get_instance=lambda key: instance if key == GAME_KEY else None,
        save_instance=saver or _Saver(),
    )


def _preview(instance: GameInstance, raw: dict | None = None, *, version: str = "v14", uid: str = "gm1"):
    return foundry_import_service.preview(
        _deps(instance), GAME_KEY, _viewer(instance, uid), raw if raw is not None else _raw_scene(),
        source_version=version,
    )


async def _commit(
    instance: GameInstance,
    preview_result: dict,
    policies: dict,
    *,
    operation_id: str = "op-1",
    plan_digest: str | None = None,
    digest_override: str | None = None,
    uid: str = "gm1",
    saver: _Saver | None = None,
    broadcast=None,
) -> dict:
    return await foundry_import_service.commit(
        _deps(instance, saver), GAME_KEY, _viewer(instance, uid), preview_result["plan"],
        plan_digest=plan_digest or preview_result["plan_digest"],
        policies=policies,
        operation_id=operation_id,
        broadcast=broadcast,
    )


def _create_policy(item: dict) -> dict:
    return {item["canonical_id"]: {"action": "create"}}


def _imported_canonical_id(raw: dict | None = None) -> str:
    document, _report = import_scene(raw or _raw_scene(), source_version="v14")
    return document["canonical_id"]


def _min_scene(canonical_id: str) -> dict:
    return {
        "schema_version": 1,
        "kind": "scene",
        "canonical_id": canonical_id,
        "display_name": canonical_id,
        "placeables": {},
    }


async def _write_other_scene(instance: GameInstance) -> None:
    deps = tactical_scene_ops_service.TacticalSceneOpDependencies(
        get_instance=lambda key: instance if key == GAME_KEY else None,
        save_instance=_Saver(),
    )
    await tactical_scene_ops_service.apply_tactical_scene_op(
        deps, GAME_KEY, _viewer(instance, "gm1"),
        {"kind": "tactical_scene_create", "scene": _min_scene("other_scene")},
        expected_revision=tactical_scene.revision(instance),
    )


# ---- preview --------------------------------------------------------------


def test_preview_builds_plan_without_writing_authoritative_state():
    instance = _instance()
    before = deepcopy(tactical_scene.slot(instance))

    result = _preview(instance)

    assert result["ok"] is True
    assert result["plan_digest"].startswith("sha256:")
    plan = result["plan"]
    item = plan["items"][0]
    assert item["canonical_id"] == _imported_canonical_id()
    assert item["conflict"] == "new"
    assert item["existing_revision"] is None
    assert item["level"] == "missing_external_ref"
    assert plan["base_revision"] == 0
    assert plan["source_version"] == "v14"
    assert tactical_scene.slot(instance) == before
    assert tactical_scene.revision(instance) == 0
    assert tactical_scene.scenes(instance) == {}


def test_preview_rejects_unsupported_version_fail_closed():
    instance = _instance()
    result = _preview(instance, version="v12")
    assert result["ok"] is False
    assert result["error_code"] == "foundry_version_unsupported"


def test_preview_marks_scripts_inert_instead_of_executing():
    raw = _raw_scene()
    raw["script"] = "game.macros.run()"
    result = _preview(instance := _instance(), raw)
    assert result["ok"] is True
    assert result["contains_script"] is True
    item = result["plan"]["items"][0]
    assert item["contains_script"] is True
    assert item["level"] == "contains_script"
    assert item["document"]["extensions"]["script"] == "game.macros.run()"

    committed = _commit_sync(instance, result, _create_policy(item))
    assert committed["ok"] is True
    stored = tactical_scene.scenes(instance)[item["canonical_id"]]
    assert stored["extensions"]["script"] == "game.macros.run()"


def _commit_sync(instance: GameInstance, preview_result: dict, policies: dict) -> dict:
    import asyncio

    return asyncio.run(_commit(instance, preview_result, policies))


def test_preview_scrubs_credentials_from_preserved_extensions():
    raw = _raw_scene()
    raw["password"] = "hunter2"
    raw["secret"] = {"key": "value"}
    result = _preview(_instance(), raw)
    assert result["ok"] is True
    item = result["plan"]["items"][0]
    assert "password" not in item["document"]["extensions"]
    assert "secret" not in item["document"]["extensions"]
    assert "scene.extensions.password" in result["plan"]["scrubbed_fields"]
    assert "scene.extensions.secret" in result["plan"]["scrubbed_fields"]


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "C:/Windows/system32/config",
        "file:///etc/passwd",
        "data:image/png;base64,AAAA",
        "worlds\\..\\escape.webp",
    ],
)
def test_preview_rejects_unsafe_asset_paths(unsafe_path: str):
    raw = _raw_scene()
    raw["tiles"][0]["texture"]["src"] = unsafe_path
    result = _preview(_instance(), raw)
    assert result["ok"] is False
    assert result["error_code"] == "tactical_scene_invalid_payload"


# ---- commit ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_create_persists_provenance_and_is_visible_to_reads():
    instance = _instance()
    saver = _Saver()
    result = _preview(instance)
    item = result["plan"]["items"][0]
    round_tripped = json.loads(json.dumps(result["plan"]))

    committed = await foundry_import_service.commit(
        _deps(instance, saver), GAME_KEY, _viewer(instance, "gm1"), round_tripped,
        plan_digest=result["plan_digest"], policies=_create_policy(item),
        operation_id="op-create",
    )

    assert committed["ok"] is True
    assert committed["idempotent"] is False
    assert committed["revision"] == 1
    assert saver.calls == 1
    assert committed["receipt"]["items"][0]["status"] == "created"

    stored = tactical_scene.scenes(instance)[item["canonical_id"]]
    marker = stored["provenance"]["foundry_import"]
    assert marker["operation_id"] == "op-create"
    assert marker["plan_digest"] == result["plan_digest"]
    assert marker["source_version"] == "v14"

    read = tactical_scene_read_service.tactical_scene_read(
        tactical_scene_read_service.TacticalSceneReadDependencies(get_instance=lambda key: instance),
        GAME_KEY, _viewer(instance, "gm1"), item["canonical_id"],
    )
    assert read["ok"] is True
    assert read["scene"]["display_name"] == "Ruined Keep"


def _browser_roundtrip(value):
    """JSON parse + stringify as a browser does: one number type, so 1.0 -> 1."""

    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _browser_roundtrip(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_browser_roundtrip(child) for child in value]
    return value


@pytest.mark.asyncio
async def test_live_preview_commit_is_stable_across_the_browser_plan_roundtrip():
    """frontend-04 事故：live 里 preview→commit 即使状态不变也曾返回 stale。

    浏览器把 plan 响应解析后原样回传，其单一数字类型会把整数浮点写成整数
    （`zoom: 1.0` → `1`）。digest 必须绑定规范化内容而非传输编码。
    """

    instance = _instance()
    saver = _Saver()
    raw = _raw_scene(name="scene_placeables.json")
    preview = _preview(instance, raw)
    assert preview["ok"] is True
    assert '"zoom": 1.0' in json.dumps(preview["plan"])

    plan = _browser_roundtrip(json.loads(json.dumps(preview["plan"])))
    assert '"zoom": 1.0' not in json.dumps(plan)
    assert compute_plan_digest(claimed_plan(plan)) == preview["plan_digest"]

    item = preview["plan"]["items"][0]
    committed = await foundry_import_service.commit(
        _deps(instance, saver), GAME_KEY, _viewer(instance, "gm1"), plan,
        plan_digest=preview["plan_digest"],
        policies={item["canonical_id"]: {"action": "create"}},
        operation_id="op-live",
    )
    assert committed["ok"] is True, committed
    assert committed["receipt"]["items"][0]["status"] == "created"
    assert tactical_scene.revision(instance) == 1

    second = _preview(instance, raw)
    second_plan = _browser_roundtrip(json.loads(json.dumps(second["plan"])))
    second_item = second["plan"]["items"][0]
    await _write_other_scene(instance)

    stale = await foundry_import_service.commit(
        _deps(instance), GAME_KEY, _viewer(instance, "gm1"), second_plan,
        plan_digest=second["plan_digest"],
        policies={
            second_item["canonical_id"]: {
                "action": "update", "expected_revision": second_item["existing_revision"],
            },
        },
        operation_id="op-live-stale",
    )
    assert stale["ok"] is False
    assert stale["error_code"] == "foundry_import_stale"
    assert set(tactical_scene.scenes(instance)) == {second_item["canonical_id"], "other_scene"}


@pytest.mark.asyncio
async def test_stale_digest_is_rejected_before_any_write():
    instance = _instance()
    preview = _preview(instance)
    item = preview["plan"]["items"][0]
    await _write_other_scene(instance)

    result = await _commit(instance, preview, _create_policy(item))

    assert result["ok"] is False
    assert result["error_code"] == "foundry_import_stale"
    assert set(tactical_scene.scenes(instance)) == {"other_scene"}
    assert tactical_scene.revision(instance) == 1


@pytest.mark.asyncio
async def test_commit_update_requires_matching_expected_revision():
    instance = _instance()
    created = await _commit(instance, _preview(instance), {_imported_canonical_id(): {"action": "create"}})
    assert created["ok"] is True

    raw = _raw_scene()
    raw["name"] = "Ruined Keep v2"
    preview = _preview(instance, raw)
    item = preview["plan"]["items"][0]
    assert item["conflict"] == "existing"
    assert item["existing_revision"] == 1

    wrong = await _commit(
        instance, preview,
        {item["canonical_id"]: {"action": "update", "expected_revision": 0}},
        operation_id="op-update-wrong",
    )
    assert wrong["ok"] is False
    assert wrong["error_code"] == "foundry_import_stale"

    preview = _preview(instance, raw)
    item = preview["plan"]["items"][0]
    updated = await _commit(
        instance, preview,
        {item["canonical_id"]: {"action": "update", "expected_revision": item["existing_revision"]}},
        operation_id="op-update",
    )
    assert updated["ok"] is True
    stored = tactical_scene.scenes(instance)[item["canonical_id"]]
    assert stored["display_name"] == "Ruined Keep v2"
    assert stored["revision"] == 2


@pytest.mark.asyncio
async def test_commit_duplicate_creates_a_new_identity():
    instance = _instance()
    await _commit(instance, _preview(instance), {_imported_canonical_id(): {"action": "create"}})

    preview = _preview(instance)
    item = preview["plan"]["items"][0]
    assert item["conflict"] == "existing"
    duplicated = await _commit(
        instance, preview,
        {item["canonical_id"]: {"action": "duplicate"}},
        operation_id="op-dup",
    )
    assert duplicated["ok"] is True
    assert duplicated["receipt"]["items"][0]["status"] == "duplicated"

    new_id = duplicated["receipt"]["items"][0]["canonical_id"]
    assert new_id.startswith("import.dup.")
    scenes = tactical_scene.scenes(instance)
    assert set(scenes) == {item["canonical_id"], new_id}
    assert scenes[new_id]["external_ids"] == {}
    assert scenes[new_id]["provenance"]["foundry_import"]["duplicated_from"] == item["canonical_id"]


@pytest.mark.asyncio
async def test_commit_skip_writes_nothing():
    instance = _instance()
    saver = _Saver()
    preview = _preview(instance)
    item = preview["plan"]["items"][0]

    result = await _commit(instance, preview, {item["canonical_id"]: {"action": "skip"}}, saver=saver)

    assert result["ok"] is True
    assert result["receipt"]["items"][0]["status"] == "skipped"
    assert tactical_scene.revision(instance) == 0
    assert tactical_scene.scenes(instance) == {}
    assert saver.calls == 0


@pytest.mark.asyncio
async def test_repeated_commit_with_same_digest_and_operation_is_idempotent():
    instance = _instance()
    saver = _Saver()
    preview = _preview(instance)
    item = preview["plan"]["items"][0]
    first = await _commit(instance, preview, _create_policy(item), operation_id="op-idem", saver=saver)
    assert first["ok"] is True and first["idempotent"] is False

    second = await _commit(instance, preview, _create_policy(item), operation_id="op-idem", saver=saver)

    assert second["ok"] is True
    assert second["idempotent"] is True
    assert second["receipt"]["items"][0]["status"] == "created"
    assert saver.calls == 1
    assert tactical_scene.revision(instance) == 1


@pytest.mark.asyncio
async def test_invalid_policies_are_rejected():
    instance = _instance()
    preview = _preview(instance)
    item = preview["plan"]["items"][0]

    missing = await _commit(instance, preview, {})
    assert missing["error_code"] == "foundry_import_plan_invalid"

    unknown_target = await _commit(
        instance, preview, {item["canonical_id"]: {"action": "create"}, "nope": {"action": "skip"}},
    )
    assert unknown_target["error_code"] == "foundry_import_plan_invalid"

    bad_action = await _commit(instance, preview, {item["canonical_id"]: {"action": "merge"}})
    assert bad_action["error_code"] == "foundry_import_plan_invalid"

    update_new = await _commit(
        instance, preview,
        {item["canonical_id"]: {"action": "update", "expected_revision": 0}},
    )
    assert update_new["error_code"] == "foundry_import_plan_invalid"

    created = await _commit(instance, preview, _create_policy(item))
    assert created["ok"] is True
    preview = _preview(instance)
    item = preview["plan"]["items"][0]
    create_existing = await _commit(instance, preview, {item["canonical_id"]: {"action": "create"}})
    assert create_existing["error_code"] == "foundry_import_plan_invalid"


@pytest.mark.asyncio
async def test_save_failure_rolls_back_the_whole_commit():
    instance = _instance()
    preview = _preview(instance)
    item = preview["plan"]["items"][0]

    result = await _commit(instance, preview, _create_policy(item), saver=_Saver(fail=True))

    assert result["ok"] is False
    assert result["error_code"] == "save_failed"
    assert tactical_scene.revision(instance) == 0
    assert tactical_scene.scenes(instance) == {}


@pytest.mark.asyncio
async def test_seat_and_outsider_are_forbidden():
    instance = _instance()
    preview = _preview(instance)
    item = preview["plan"]["items"][0]

    for uid in ("p1", "stranger"):
        denied_preview = _preview(instance, uid=uid)
        assert denied_preview["ok"] is False
        assert denied_preview["error_code"] == "tactical_scene_forbidden"

        denied_commit = await _commit(instance, preview, _create_policy(item), uid=uid)
        assert denied_commit["ok"] is False
        assert denied_commit["error_code"] == "tactical_scene_forbidden"

    assert tactical_scene.scenes(instance) == {}


@pytest.mark.asyncio
async def test_commit_broadcasts_tactical_scene_updated_after_saving():
    instance = _instance()
    events: list[tuple[str, dict, dict]] = []

    async def broadcast(game_key: str, data: dict, **kwargs) -> None:
        events.append((game_key, deepcopy(data), kwargs))

    preview = _preview(instance)
    item = preview["plan"]["items"][0]
    result = await _commit(instance, preview, _create_policy(item), broadcast=broadcast)
    assert result["ok"] is True

    seat = next(event for event in events if event[1]["visibility"] == "seat")
    gm_event = next(event for event in events if event[1]["visibility"] == "gm")
    assert seat[2] == {"exclude_uid": "gm1"}
    assert gm_event[2] == {"private_uid": "gm1"}
    for _, data, _ in events:
        assert data["event_type"] == "tactical_scene_updated"
        assert data["scene_id"] == item["canonical_id"]
        assert data["revision"] == 1
        assert data["source_operation"] == "tactical_scene_create"
        assert data["run_id"] == instance.run_id
        assert "save" not in data and "modules" not in data
    assert "ownership" not in seat[1]["projection"]
    assert "ownership" in gm_event[1]["projection"]


# ---- thin route / actions -------------------------------------------------


class _FoundryApi:
    def __init__(self, instance: GameInstance, saver: _Saver | None = None) -> None:
        self.instance = instance
        self.deps = _deps(instance, saver)

    def get_game_instance(self, game_key: str):
        return self.instance if game_key == GAME_KEY else None

    def foundry_import_preview(self, game_key, viewer, scene, *, source_version):
        return foundry_import_service.preview(
            self.deps, game_key, viewer, scene, source_version=source_version,
        )

    async def foundry_import_commit(
        self, game_key, viewer, plan, *, plan_digest, policies, operation_id, broadcast=None,
    ):
        return await foundry_import_service.commit(
            self.deps, game_key, viewer, plan,
            plan_digest=plan_digest, policies=policies,
            operation_id=operation_id, broadcast=broadcast,
        )


@web.middleware
async def _identity(request: web.Request, handler):
    request["user_id"] = request.headers.get("X-Test-User", "")
    request["owner_authenticated"] = False
    request["player_preview"] = False
    return await handler(request)


async def _client(instance: GameInstance):
    app = web.Application(middlewares=[_identity])
    app["api"] = _FoundryApi(instance)
    register_foundry(app)
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_routes_are_thin_and_map_status_codes():
    instance = _instance()
    raw = _raw_scene()
    async with await _client(instance) as client:
        preview = await client.post(
            "/api/games/g/foundry/scenes/preview",
            json={"scene": raw, "source_version": "v14"},
            headers={"X-Test-User": "gm1"},
        )
        assert preview.status == 200
        body = await preview.json()
        assert body["ok"] is True
        item = body["plan"]["items"][0]

        forbidden = await client.post(
            "/api/games/g/foundry/scenes/preview",
            json={"scene": raw, "source_version": "v14"},
            headers={"X-Test-User": "p1"},
        )
        assert forbidden.status == 403
        assert (await forbidden.json())["error_code"] == "tactical_scene_forbidden"

        unsupported = await client.post(
            "/api/games/g/foundry/scenes/preview",
            json={"scene": raw, "source_version": "v12"},
            headers={"X-Test-User": "gm1"},
        )
        assert unsupported.status == 422

        committed = await client.post(
            "/api/games/g/foundry/scenes/commit",
            json={
                "plan": body["plan"],
                "plan_digest": body["plan_digest"],
                "policies": {item["canonical_id"]: {"action": "create"}},
                "operation_id": "op-route",
            },
            headers={"X-Test-User": "gm1"},
        )
        assert committed.status == 200
        assert (await committed.json())["ok"] is True

        stale = await client.post(
            "/api/games/g/foundry/scenes/commit",
            json={
                "plan": body["plan"],
                "plan_digest": body["plan_digest"],
                "policies": {item["canonical_id"]: {"action": "create"}},
                "operation_id": "op-route-2",
            },
            headers={"X-Test-User": "gm1"},
        )
        assert stale.status == 409
        assert (await stale.json())["error_code"] == "foundry_import_stale"


def _tool_dependencies(instance: GameInstance) -> FoundryToolDependencies:
    deps = _deps(instance)

    async def commit(game_key, viewer, plan, **options):
        return await foundry_import_service.commit(deps, game_key, viewer, plan, **options)

    return FoundryToolDependencies(
        get_game=lambda key: instance if key == GAME_KEY else None,
        viewer_for=lambda game, owner: _viewer(game, owner),
        preview=lambda game_key, viewer, scene, source_version: foundry_import_service.preview(
            deps, game_key, viewer, scene, source_version=source_version,
        ),
        commit=commit,
    )


@pytest.mark.asyncio
async def test_agent_actions_are_discoverable_gm_only_and_delegate():
    instance = _instance()
    registry = ActionRegistry(foundry_tools(_tool_dependencies(instance)))

    preview_action = registry.get("foundry_import_preview")
    commit_action = registry.get("foundry_import_commit")
    assert preview_action is not None and commit_action is not None
    assert preview_action.mutation is False and preview_action.effective_effect == "read"
    assert commit_action.mutation is True and commit_action.effective_effect == "write"
    args = {"game_key": GAME_KEY}
    assert preview_action.authorize(args, "p1") is False
    assert preview_action.authorize(args, "gm1") is True
    assert commit_action.authorize(args, "p1") is False
    assert commit_action.authorize(args, "gm1") is True

    previewed = preview_action.handler(
        {"game_key": GAME_KEY, "source_version": "v14", "scene": _raw_scene()}, "gm1",
    )
    assert previewed["ok"] is True
    item = previewed["plan"]["items"][0]

    committed = await commit_action.handler(
        {
            "game_key": GAME_KEY, "operation_id": "op-agent", "plan_digest": previewed["plan_digest"],
            "plan": previewed["plan"], "policies": {item["canonical_id"]: {"action": "create"}},
        },
        "gm1",
    )
    assert committed["ok"] is True
    assert tactical_scene.revision(instance) == 1


# ---- credentials and cross-version identity --------------------------------


def test_import_scrubs_provider_and_room_credentials_from_extensions():
    instance = _instance()
    raw = _raw_scene()
    raw["room_token"] = "room-token-value"
    raw["seat_credentials"] = {"s1": "secret"}
    raw["room_password_hash"] = "hash"
    raw["ai_provider_key_main"] = "sk-provider-secret"
    raw["tokens"] = [{
        "_id": "tokScrub000000001", "name": "Carrier", "x": 0, "y": 0,
        "ai_provider_key_fallback": "sk-fallback", "flags": {},
    }]

    result = _preview(instance, raw)

    assert result["ok"] is True
    scrubbed = " ".join(result["plan"]["scrubbed_fields"])
    for key in ("room_token", "seat_credentials", "room_password_hash", "ai_provider_key_main"):
        assert key in scrubbed
    item = result["plan"]["items"][0]
    assert item["document"]["extensions"].keys().isdisjoint({
        "room_token", "seat_credentials", "room_password_hash", "ai_provider_key_main",
    })
    token = item["document"]["placeables"]["tokens"][
        next(iter(item["document"]["placeables"]["tokens"]))
    ]
    assert token["extensions"].keys().isdisjoint({"ai_provider_key_fallback"})


@pytest.mark.asyncio
async def test_a_v13_payload_updates_a_scene_first_imported_from_v14():
    """跨版本 external-id 命中走 update，不产生重复场景（§0.5 遗留 8）。"""

    instance = _instance()
    first = _preview(instance)
    committed = await _commit(instance, first, {
        _imported_canonical_id(): {"action": "create"},
    }, operation_id="op-v14")
    assert committed["ok"] is True

    v13_payload = _raw_scene("v13/scene", "scene_v13_legacy.json")
    v13_payload["_id"] = _raw_scene()["_id"]

    second = _preview(instance, v13_payload, version="v13")
    assert second["ok"] is True
    item = second["plan"]["items"][0]
    assert item["conflict"] == "existing"
    assert item["canonical_id"] == _imported_canonical_id()
    assert item["existing_revision"] is not None

    updated = await _commit(instance, second, {
        item["canonical_id"]: {"action": "update", "expected_revision": item["existing_revision"]},
    }, operation_id="op-v13")
    assert updated["ok"] is True
    assert set(tactical_scene.scenes(instance)) == {_imported_canonical_id()}

    replay = await _commit(instance, second, {
        item["canonical_id"]: {"action": "update", "expected_revision": item["existing_revision"]},
    }, operation_id="op-v13")
    assert replay["ok"] is True
    assert set(tactical_scene.scenes(instance)) == {_imported_canonical_id()}