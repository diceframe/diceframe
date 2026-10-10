"""Room password hardening: hashed storage, expiring hashed room tokens,
new-password length rule, export/import scrubbing and the player join flow."""

from __future__ import annotations

import hashlib
import json
import zipfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src import password_hashing
from src.engine.modules import narrative_notes
from src.engine.game_instance import GameInstance
from src.engine.module_state import ModuleStateError
from src.engine.modules import room_access
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v37_to_v38,
    migrate_game_state_payload,
    rebind_imported_game_state_payload,
)
from src.webui.routes.games import register_games
from src.webui.services import game_packages
from src.webui.services import room_password as room_password_svc
from test_game_query_routes_http import (
    GM_UID,
    _make_app,
    _make_game,
    _owner,
    _player_url,
    _seat,
    play_env,  # noqa: F401
)

pytest_plugins = ["tests.webapi_harness"]

PASSWORD = "correct horse"
V2_SLOT = {
    "schema_version": 2,
    "max_players": 5,
    "player_access_open": True,
    "bot_bind_token": "bind-secret",
    "room_password": PASSWORD,
    "room_token": "legacy-room-token",
    "seat_credentials": {"p1": {"hash": "seat-digest", "issued_at": "t", "epoch": 1}},
}
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _instance(password: str = "") -> GameInstance:
    instance = GameInstance(game_key=("web", "room-pw", "bot"), gm_uid="gm")
    instance.players = {"p1": {"character_name": "p1", "character_sheet": {}}}
    if password:
        instance.set_room_password(password)
    return instance


def _v37_payload(slot=None) -> dict:
    return {
        "instance_schema_version": 37,
        "game_key": ["web", "room-pw", "bot"],
        "state": "created",
        "modules": {"room_access": deepcopy(V2_SLOT if slot is None else slot)},
    }


# ---- migration 37 -> 38 ------------------------------------------------------


def test_v37_plaintext_password_becomes_a_hash_without_losing_access() -> None:
    original = _v37_payload()
    before = deepcopy(original)
    migrated = migrate_game_state_payload(original)
    assert original == before  # input is never mutated
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION >= 38
    slot = migrated["modules"]["room_access"]
    assert slot["schema_version"] == room_access.SCHEMA_VERSION == 3
    assert "room_password" not in slot and "room_token" not in slot
    assert PASSWORD not in json.dumps(slot) and "legacy-room-token" not in json.dumps(slot)
    # Everything else is carried verbatim.
    for key in ("max_players", "player_access_open", "bot_bind_token", "seat_credentials"):
        assert slot[key] == V2_SLOT[key]
    instance = GameInstance.from_dict(migrated)
    assert room_access.verify_room_password(instance, PASSWORD)
    assert not room_access.verify_room_password(instance, PASSWORD + "x")
    # Players already inside keep their (now hashed, now expiring) room token.
    assert room_access.verify_room_token(instance, "legacy-room-token")
    [record] = slot["room_tokens"]
    assert record["hash"] == hashlib.sha256(b"legacy-room-token").hexdigest()
    expires = datetime.fromisoformat(record["expires_at"])
    assert timedelta(days=29) < expires - datetime.now(timezone.utc) <= timedelta(days=30)
    assert not room_access.verify_room_token(instance, "legacy-room-token", now=expires)


def test_v37_migration_is_idempotent() -> None:
    migrated = migrate_game_state_payload(_v37_payload())
    assert migrate_game_state_payload(migrated) == migrated
    step = _migrate_v37_to_v38(_v37_payload())
    assert _migrate_v37_to_v38(deepcopy(step)) == step


@pytest.mark.parametrize("slot", [
    {"schema_version": 9, "room_password": "future-plaintext", "opaque": True},
    {"schema_version": 3, "room_password_hash": "", "room_tokens": []},
])
def test_v37_migration_never_touches_a_current_or_future_slot(slot) -> None:
    migrated = _migrate_v37_to_v38(_v37_payload(slot))
    assert migrated["modules"]["room_access"] == slot
    assert migrated["instance_schema_version"] == 38


@pytest.mark.parametrize("modules", [None, [], "corrupt", {}])
def test_v37_migration_materializes_a_fresh_slot(modules) -> None:
    migrated = _migrate_v37_to_v38({"instance_schema_version": 37, "modules": modules})
    assert migrated["modules"]["room_access"] == room_access.fresh()


@pytest.mark.parametrize("password, locked", [
    ("", False), (None, False), (0, False), ([], False),
    (12345678, True), ({"opaque": 1}, True),
])
def test_v37_unusable_password_values_fail_closed(password, locked) -> None:
    migrated = migrate_game_state_payload(_v37_payload({**V2_SLOT, "room_password": password}))
    instance = GameInstance.from_dict(migrated)
    assert room_access.has_room_password(instance) is locked
    assert not room_access.verify_room_password(instance, str(password))
    # Without a password there is nothing a token could unlock. A locked room
    # keeps the token of players already inside, as before the upgrade.
    assert room_access.verify_room_token(instance, "legacy-room-token") is locked


def test_short_legacy_password_keeps_working_after_upgrade() -> None:
    migrated = migrate_game_state_payload(_v37_payload({**V2_SLOT, "room_password": "abcd"}))
    instance = GameInstance.from_dict(migrated)
    assert room_access.verify_room_password(instance, "abcd")


# ---- password verification -----------------------------------------------------


def test_verify_is_exact_and_rejects_plaintext_storage() -> None:
    instance = _instance(PASSWORD)
    assert room_access.verify_room_password(instance, PASSWORD)
    for wrong in ("", " " + PASSWORD, PASSWORD.upper(), "correct", None):
        assert not room_access.verify_room_password(instance, wrong)  # type: ignore[arg-type]
    # A plaintext value in the hash field (tampered save) never verifies.
    instance.modules["room_access"]["room_password_hash"] = PASSWORD
    assert room_access.has_room_password(instance) is True
    assert not room_access.verify_room_password(instance, PASSWORD)


def test_password_and_token_checks_use_constant_time_compare(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    real = password_hashing.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(password_hashing.hmac, "compare_digest", spy)
    instance = _instance(PASSWORD)
    assert room_access.verify_room_password(instance, PASSWORD)
    assert not room_access.verify_room_password(instance, "wrong-password")
    assert len(calls) == 2

    token_calls: list[str] = []
    monkeypatch.setattr(room_access.hmac, "compare_digest", lambda a, b: token_calls.append(a) or real(a, b))
    first, _ = room_access.issue_room_token(instance, now=NOW)
    room_access.issue_room_token(instance, now=NOW)
    room_access.issue_room_token(instance, now=NOW)
    assert room_access.verify_room_token(instance, first, now=NOW)
    # Every live record is compared, even after the first one matched.
    assert len(token_calls) == 3


def test_set_password_rejects_writes_to_a_future_slot() -> None:
    slot = {"schema_version": 9, "opaque": True}
    instance = GameInstance(game_key=("web", "future", "bot"), modules={"room_access": deepcopy(slot)})
    with pytest.raises(ModuleStateError):
        room_access.set_room_password(instance, PASSWORD)
    assert instance.modules["room_access"] == slot


# ---- room tokens ---------------------------------------------------------------


def test_room_token_is_stored_hashed_and_expires() -> None:
    instance = _instance(PASSWORD)
    token, expires_at = room_access.issue_room_token(instance, ttl_seconds=3600, now=NOW)
    stored = json.dumps(instance.to_dict())
    assert token not in stored
    assert hashlib.sha256(token.encode()).hexdigest() in stored
    assert datetime.fromisoformat(expires_at) == NOW + timedelta(hours=1)
    assert room_access.verify_room_token(instance, token, now=NOW + timedelta(minutes=59))
    assert not room_access.verify_room_token(instance, token, now=NOW + timedelta(hours=1))
    assert not room_access.verify_room_token(instance, "other", now=NOW)


def test_room_tokens_need_a_password_and_die_with_it() -> None:
    instance = _instance()
    with pytest.raises(ValueError):
        room_access.issue_room_token(instance)
    instance.set_room_password(PASSWORD)
    token, _ = room_access.issue_room_token(instance)
    instance.set_room_password("")
    assert not room_access.verify_room_token(instance, token)


def test_issuing_prunes_expired_and_caps_records() -> None:
    instance = _instance(PASSWORD)
    room_access.issue_room_token(instance, ttl_seconds=60, now=NOW)
    later = NOW + timedelta(minutes=5)
    for _ in range(room_access.MAX_ROOM_TOKENS + 3):
        room_access.issue_room_token(instance, now=later)
    records = instance.modules["room_access"]["room_tokens"]
    assert len(records) == room_access.MAX_ROOM_TOKENS
    assert all(datetime.fromisoformat(record["expires_at"]) > later for record in records)


def test_malformed_token_records_never_verify() -> None:
    instance = _instance(PASSWORD)
    digest = hashlib.sha256(b"tok").hexdigest()
    instance.modules["room_access"]["room_tokens"] = [
        {"hash": digest},
        {"hash": digest, "expires_at": "not-a-date"},
        {"hash": digest, "expires_at": "2999-01-01T00:00:00"},  # naive
        "garbage",
    ]
    assert not room_access.verify_room_token(instance, "tok")


@pytest.mark.parametrize("raw, expected", [
    (None, room_access.DEFAULT_ROOM_TOKEN_TTL_SECONDS),
    ("", room_access.DEFAULT_ROOM_TOKEN_TTL_SECONDS),
    ("7", 7 * 86400),
    ("0.5", 43200),
    ("9999", 365 * 86400),
    ("0", room_access.DEFAULT_ROOM_TOKEN_TTL_SECONDS),
    ("-3", room_access.DEFAULT_ROOM_TOKEN_TTL_SECONDS),
    ("nan", room_access.DEFAULT_ROOM_TOKEN_TTL_SECONDS),
    ("abc", room_access.DEFAULT_ROOM_TOKEN_TTL_SECONDS),
])
def test_room_token_ttl_is_configurable(raw, expected) -> None:
    environ = {} if raw is None else {room_password_svc.ROOM_TOKEN_TTL_ENV: raw}
    assert room_password_svc.room_token_ttl_seconds(environ) == expected


# ---- new password length -----------------------------------------------------


@pytest.mark.parametrize("password, ok", [("abcde", False), ("abcdef", True), ("", True)])
def test_new_passwords_need_six_characters(password, ok) -> None:
    instance = _instance("previous")
    if ok:
        instance.set_room_password(password)
        assert room_access.has_room_password(instance) is bool(password)
    else:
        with pytest.raises(ValueError, match="至少 6 位"):
            instance.set_room_password(password)
        # A rejected change keeps the old password.
        assert room_access.verify_room_password(instance, "previous")


# ---- export / import -----------------------------------------------------------


def _assert_scrubbed(state: dict, *, closed: bool) -> None:
    rendered = json.dumps(state, ensure_ascii=False)
    for secret in (PASSWORD, "legacy-room-token", "bind-secret", "seat-digest", "pbkdf2_sha256"):
        assert secret not in rendered, secret
    slot = state["modules"]["room_access"]
    assert slot.get("room_tokens", []) == []
    assert slot.get("room_password_hash", "") == ""
    assert slot["bot_bind_token"] == ""
    assert slot["seat_credentials"] == {}
    assert slot["player_access_open"] is (not closed)


def _export(tmp_path, payload: dict) -> dict:
    save_path = tmp_path / "state.json"
    save_path.write_text(json.dumps(payload), encoding="utf-8")
    service = game_packages.GamePackageService(game_packages.GamePackageDependencies(
        parse_game_key=lambda game_key: tuple(game_key.split("|")),
        get_instance=lambda _key: SimpleNamespace(gm_uid="gm", world_name="w"),
        state_path_for=lambda _key: save_path,
        import_save_zip=AsyncMock(),
        resolve_scene_image_file=lambda _reference: None,
        resolve_map_background_file=lambda _reference: None,
        save_scene_image_upload=lambda _payload: {"ok": True},
        save_map_background_upload=lambda _payload: {"ok": True},
    ))
    result = service.export_game_package("web|room-pw|bot")
    assert result["ok"] is True
    with zipfile.ZipFile(BytesIO(result["payload"])) as archive:
        return json.loads(archive.read("state.json"))


def test_export_never_contains_access_credentials(tmp_path) -> None:
    instance = _instance(PASSWORD)
    instance.set_bot_bind_token("bind-secret")
    room_access.issue_room_token(instance, token="legacy-room-token")
    room_access.issue_seat_token(instance, "p1")
    state = _export(tmp_path, instance.to_dict())
    _assert_scrubbed(state, closed=True)


def test_export_of_an_unmigrated_v37_save_is_scrubbed_too(tmp_path) -> None:
    state = _export(tmp_path, _v37_payload())
    _assert_scrubbed(state, closed=True)
    assert state["modules"]["room_access"]["room_password"] == ""


def test_export_of_an_open_room_stays_open(tmp_path) -> None:
    instance = _instance()
    instance.set_bot_bind_token("bind-secret")
    state = _export(tmp_path, instance.to_dict())
    _assert_scrubbed(state, closed=False)


def test_import_drops_credentials_even_from_old_packages() -> None:
    # A package exported before this change still carries plaintext secrets.
    payload = rebind_imported_game_state_payload(
        _v37_payload(), game_key=("web", "imported", "bot"), run_id="run-2",
    )
    _assert_scrubbed(payload, closed=True)
    imported = GameInstance.from_dict(payload)
    assert room_access.has_room_password(imported) is False
    assert room_access.player_access_open(imported) is False


def test_import_scrubs_pre_slot_legacy_saves() -> None:
    legacy = {
        "instance_schema_version": 28,
        "game_key": ["web", "old", "bot"],
        "state": "created",
        "room_password": PASSWORD,
        "room_token": "legacy-room-token",
        "bot_bind_token": "bind-secret",
        "player_access_open": True,
    }
    payload = rebind_imported_game_state_payload(legacy, game_key=("web", "imported", "bot"), run_id="r")
    _assert_scrubbed(payload, closed=True)


def test_scrub_on_a_raw_legacy_payload_closes_access() -> None:
    legacy = {"room_password": PASSWORD, "room_token": "t", "bot_bind_token": "b", "player_access_open": True}
    assert room_access.scrub_access_credentials(legacy) is True
    assert legacy == {"room_password": "", "room_token": "", "bot_bind_token": "", "player_access_open": False}


# ---- HTTP: GM view and player join flow --------------------------------------


@pytest.mark.asyncio
async def test_gm_detail_never_contains_the_password_or_its_hash(play_env) -> None:
    game_key, instance = _make_game(play_env, "gm-view", bind_adventure=False, room_password=PASSWORD)
    stored_hash = instance.modules["room_access"]["room_password_hash"]
    app = _make_app(play_env)
    async with TestClient(TestServer(app)) as client:
        detail = await client.get(f"/api/games/{game_key}", headers=_owner())
        body = await detail.json()
    assert detail.status == 200
    assert body["has_room_password"] is True
    rendered = json.dumps(body, ensure_ascii=False)
    assert PASSWORD not in rendered and stored_hash not in rendered
    assert "room_password_hash" not in rendered and "room_tokens" not in rendered


@pytest.mark.asyncio
async def test_player_join_flow_with_password_change_and_expiry(play_env, monkeypatch) -> None:
    game_key, instance = _make_game(play_env, "join-flow", bind_adventure=False)
    instance.set_room_password(PASSWORD)
    app = _make_app(play_env)
    seat = _seat(instance)
    verify_url = f"/api/games/{game_key}/verify-room-password"
    detail_url = f"/api/games/{game_key}"

    async with TestClient(TestServer(app)) as client:
        lobby = await (await client.get(_player_url(detail_url), headers=seat)).json()
        assert lobby["has_room_password"] is True

        blocked = await client.get(_player_url(f"{detail_url}/adventure"), headers=seat)
        assert blocked.status == 403
        assert (await blocked.json())["needs_room_password"] is True

        wrong = await client.post(_player_url(verify_url), headers=seat, json={"password": "nope-nope"})
        assert wrong.status == 403
        assert "room_token" not in await wrong.json()

        right = await client.post(_player_url(verify_url), headers=seat, json={"password": PASSWORD})
        body = await right.json()
        assert right.status == 200 and right.headers["Cache-Control"] == "no-store"
        token = body["room_token"]
        assert token and datetime.fromisoformat(body["expires_at"]) > datetime.now(timezone.utc)
        assert token not in json.dumps(instance.to_dict())

        # A second player gets a separate token; the first keeps working.
        second = await (await client.post(
            _player_url(verify_url), headers=seat, json={"password": PASSWORD},
        )).json()
        assert second["room_token"] != token
        for held in (token, second["room_token"]):
            ok = await client.get(_player_url(f"{detail_url}/adventure"), headers={**seat, "X-Room-Token": held})
            assert ok.status == 200, await ok.json()

        # Expired token -> the same "needs room password" answer as no token.
        far_future = datetime.now(timezone.utc) + timedelta(days=31)
        real_now = room_access._now
        monkeypatch.setattr(room_access, "_now", lambda now: now or far_future)
        expired = await client.get(_player_url(f"{detail_url}/adventure"), headers={**seat, "X-Room-Token": token})
        assert expired.status == 403
        assert (await expired.json())["needs_room_password"] is True
        monkeypatch.setattr(room_access, "_now", real_now)

        # The GM changing the password revokes every room token.
        instance.set_room_password("brand-new-pass")
        revoked = await client.get(_player_url(f"{detail_url}/adventure"), headers={**seat, "X-Room-Token": token})
        assert revoked.status == 403
        assert (await (await client.post(
            _player_url(verify_url), headers=seat, json={"password": PASSWORD},
        )).json())["ok"] is False

        # The owner never needs a room token.
        owner = await client.get(f"{detail_url}/adventure", headers=_owner())
        assert owner.status == 200


@pytest.mark.asyncio
async def test_gm_password_route_enforces_new_length_and_never_echoes(play_env) -> None:
    game_key, instance = _make_game(play_env, "gm-set", bind_adventure=False)

    @web.middleware
    async def as_gm(request, handler):
        request["user_id"] = GM_UID
        return await handler(request)

    app = web.Application(middlewares=[as_gm])
    app["api"] = play_env.api
    register_games(app)
    url = f"/api/games/{game_key}/room-password"
    async with TestClient(TestServer(app)) as client:
        short = await client.post(url, headers=_owner(), json={"password": "abcde"})
        ok = await client.post(url, headers=_owner(), json={"password": "abcdef"})
        short_body, ok_body = await short.json(), await ok.json()
    assert short.status == 400 and "至少 6 位" in short_body["error"]
    assert ok.status == 200 and ok_body == {"ok": True, "has_room_password": True}
    assert room_access.verify_room_password(instance, "abcdef")


# ---- review fixes: password change during verification -----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_to", ["brand-new-pass", ""])
async def test_password_change_during_verification_issues_no_token(monkeypatch, changed_to) -> None:
    instance = _instance(PASSWORD)
    real_verify = password_hashing.verify_password_hash

    def gm_changes_password_mid_check(candidate, stored):
        # The GM replaces (or removes) the password while PBKDF2 runs.
        instance.set_room_password(changed_to)
        return real_verify(candidate, stored)

    monkeypatch.setattr(password_hashing, "verify_password_hash", gm_changes_password_mid_check)
    monkeypatch.setattr(room_access, "verify_password_hash", gm_changes_password_mid_check, raising=False)
    save = AsyncMock()
    result, status = await room_password_svc.verify_and_issue_room_token(instance, PASSWORD, save)
    assert status == 409, result
    assert result["ok"] is False and "room_token" not in result
    assert instance.modules["room_access"]["room_tokens"] == []
    save.assert_not_awaited()


# ---- review fixes: the upgrade leaves no plaintext on disk -------------------


def _write_v37_save(directory, *, password: str, token: str) -> dict:
    instance = _instance()
    data = instance.to_dict()
    data["instance_schema_version"] = 37
    data["modules"]["room_access"] = {**V2_SLOT, "room_password": password, "room_token": token, "seat_credentials": {}}
    directory.mkdir(parents=True, exist_ok=True)
    return data


@pytest.mark.asyncio
async def test_loading_a_v37_save_rewrites_state_and_backup_without_plaintext(tmp_path) -> None:
    from src.engine.game_instance import GameRegistry

    registry = GameRegistry(tmp_path / "saves")
    game_key = ("web", "room-pw", "bot")
    state_path = registry._save_path(game_key)
    current = _write_v37_save(state_path.parent, password=PASSWORD, token="legacy-room-token")
    older = _write_v37_save(state_path.parent, password="older-secret", token="older-room-token")
    state_path.write_text(json.dumps(current), encoding="utf-8")
    state_path.with_name("state.backup.json").write_text(json.dumps(older), encoding="utf-8")

    loaded = await registry.load(game_key)
    assert loaded is not None
    on_disk = state_path.read_text(encoding="utf-8")
    backup = state_path.with_name("state.backup.json").read_text(encoding="utf-8")
    for secret in (PASSWORD, "legacy-room-token"):
        assert secret not in on_disk
    for secret in ("older-secret", "older-room-token"):
        assert secret not in backup
    assert json.loads(on_disk)["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    # The backup is upgraded in place (still the older state), not replaced.
    backup_instance = GameInstance.from_dict(json.loads(backup))
    assert room_access.verify_room_password(backup_instance, "older-secret")

    # The legacy token's expiry was fixed once at the upgrade, not per load.
    first_expiry = json.loads(on_disk)["modules"]["room_access"]["room_tokens"][0]["expires_at"]
    reloaded = await GameRegistry(tmp_path / "saves").load(game_key)
    assert reloaded is not None
    assert reloaded.modules["room_access"]["room_tokens"][0]["expires_at"] == first_expiry
    assert room_access.verify_room_password(reloaded, PASSWORD)
    assert room_access.verify_room_token(reloaded, "legacy-room-token")


@pytest.mark.asyncio
async def test_loading_a_current_save_does_not_rewrite_it(tmp_path) -> None:
    from src.engine.game_instance import GameRegistry

    registry = GameRegistry(tmp_path / "saves")
    instance = _instance(PASSWORD)
    state_path = registry._save_path(instance.game_key)
    state_path.parent.mkdir(parents=True)
    text = json.dumps(instance.to_dict())
    state_path.write_text(text, encoding="utf-8")
    assert await registry.load(instance.game_key) is not None
    assert state_path.read_text(encoding="utf-8") == text
    assert not state_path.with_name("state.backup.json").exists()


# ---- review fixes: room tokens stay out of URLs and logs ---------------------


@pytest.mark.asyncio
async def test_room_token_is_accepted_only_as_a_header(play_env) -> None:
    game_key, instance = _make_game(play_env, "header-only", bind_adventure=False)
    instance.set_room_password(PASSWORD)
    token, _ = room_access.issue_room_token(instance)
    app = _make_app(play_env)
    seat = _seat(instance)
    url = f"/api/games/{game_key}/adventure"
    async with TestClient(TestServer(app)) as client:
        in_query = await client.get(_player_url(url, room_token=token), headers=seat)
        in_header = await client.get(_player_url(url), headers={**seat, "X-Room-Token": token})
    assert in_query.status == 403
    assert in_header.status == 200


def test_access_log_redacts_credentials_in_the_request_line() -> None:
    import logging

    from src.runtime_logging import install_access_log_redaction

    logger = logging.getLogger("aiohttp.access")
    install_access_log_redaction(logger)
    install_access_log_redaction(logger)  # idempotent
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Collect()
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        logger.info(
            '1.2.3.4 [t] "GET /api/games/a/sse?share=1&room_token=SECRET1&ticket=SECRET2'
            '&seat=SECRET3&seat_token=SECRET4&user=p1 HTTP/1.1" 200 5 "-" "ua"'
        )
        logger.info('%s "%s" %s', "1.2.3.4", "GET /x?room_token=SECRET5 HTTP/1.1", 200)
        # aiohttp also passes the request line as structured "extra" data.
        logger.info("line", extra={"first_request_line": "GET /x?ticket=SECRET6&share=1 HTTP/1.1"})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    rendered = " ".join(
        record.getMessage() + str(getattr(record, "first_request_line", "")) for record in records
    )
    for secret in ("SECRET1", "SECRET2", "SECRET3", "SECRET4", "SECRET5", "SECRET6"):
        assert secret not in rendered
    assert "user=p1" in rendered and "room_token=[redacted]" in rendered
    assert sum(isinstance(f, type(logger.filters[0])) for f in logger.filters) == 1


def test_cors_allows_the_room_token_header() -> None:
    from src.webui import cors

    response = web.Response()
    cors._apply_allowed_cors_headers(response, "https://table.example")
    allowed = {part.strip() for part in response.headers["Access-Control-Allow-Headers"].split(",")}
    assert "X-Room-Token" in allowed


# ---- review fixes: staged commits never roll back room access ----------------


def test_committing_a_staged_aggregate_keeps_the_live_room_access() -> None:
    live = _instance("old-password")
    live.run_id = "run-1"
    old_token, _ = room_access.issue_room_token(live)
    staged = GameInstance.from_dict(deepcopy(live.to_dict()))  # e.g. before an LLM call
    narrative_notes.replace_scene(staged, "staged scene")
    # Meanwhile the GM changes the password and a player gets a seat link.
    live.set_room_password("new-password")
    seat_token = room_access.issue_seat_token(live, "p1")

    live.replace_persisted_state_from(staged)

    assert narrative_notes.scene(live) == "staged scene"  # the staged domain change is committed
    assert room_access.verify_room_password(live, "new-password")
    assert not room_access.verify_room_password(live, "old-password")
    assert not room_access.verify_room_token(live, old_token)
    assert room_access.verify_seat_token(live, seat_token) == "p1"


# ---- review fixes: one trimming rule (none) ------------------------------------


def test_passwords_are_never_trimmed_and_blank_ones_are_refused() -> None:
    instance = _instance("  spaced pass  ")
    assert room_access.verify_room_password(instance, "  spaced pass  ")
    assert not room_access.verify_room_password(instance, "spaced pass")
    with pytest.raises(ValueError, match="空白"):
        instance.set_room_password("        ")
    assert room_access.verify_room_password(instance, "  spaced pass  ")


@pytest.mark.asyncio
async def test_create_and_verify_use_the_same_untrimmed_password(web_api) -> None:
    api, _lorebook, registry, _llm, _worlds_dir = web_api
    players = [{
        "character_name": "A", "class": "战士",
        "attributes": {"str": 14, "dex": 10, "con": 12, "int": 10, "wis": 10, "cha": 10},
    }]
    created = await api.create_game(
        "template_world", "trim-check", players=list(players), solo=False, room_password=" lead-and-trail ",
    )
    assert created["ok"] is True, created
    instance = registry.get(api._parse_key(created["game_key"]))
    assert room_access.verify_room_password(instance, " lead-and-trail ")
    assert not room_access.verify_room_password(instance, "lead-and-trail")
    blank = await api.create_game(
        "template_world", "blank-check", players=list(players), solo=False, room_password="       ",
    )
    assert blank["ok"] is False and "空白" in blank["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("password, closed", [(PASSWORD, True), ("", False)])
async def test_import_tells_the_gm_when_it_closed_the_player_entrance(tmp_path, password, closed) -> None:
    from src.engine import persistence
    from src.engine.game_instance import GameRegistry

    source = _instance(password)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("state.json", json.dumps(source.to_dict()))
    registry = GameRegistry(tmp_path / "saves")
    result = await persistence.import_save_zip(registry, buffer.getvalue())
    assert result["ok"] is True, result
    assert result["player_access_closed"] is closed
    imported = registry.get(tuple(result["game_key"]))
    assert room_access.has_room_password(imported) is False
    assert room_access.player_access_open(imported) is (not closed)


@pytest.mark.asyncio
async def test_a_plaintext_backup_is_upgraded_even_when_the_state_is_current(tmp_path) -> None:
    from src.engine.game_instance import GameRegistry

    registry = GameRegistry(tmp_path / "saves")
    current = _instance(PASSWORD)
    state_path = registry._save_path(current.game_key)
    older = _write_v37_save(state_path.parent, password="older-secret", token="older-room-token")
    state_text = json.dumps(current.to_dict())
    state_path.write_text(state_text, encoding="utf-8")
    backup_path = state_path.with_name("state.backup.json")
    backup_path.write_text(json.dumps(older), encoding="utf-8")

    assert await registry.load(current.game_key) is not None
    backup = backup_path.read_text(encoding="utf-8")
    assert "older-secret" not in backup and "older-room-token" not in backup
    assert room_access.verify_room_password(GameInstance.from_dict(json.loads(backup)), "older-secret")
    assert state_path.read_text(encoding="utf-8") == state_text  # the current state is left alone


@pytest.mark.asyncio
async def test_wrong_room_passwords_through_the_real_stack_are_rate_limited(play_env) -> None:
    import web_server
    from aiohttp import web as aio_web

    from src.webui.abuse_guard import ABUSE_GUARD_KEY, AbuseGuard, abuse_guard_middleware

    game_key, instance = _make_game(play_env, "limited", bind_adventure=False)
    instance.set_room_password(PASSWORD)
    app = aio_web.Application(middlewares=[abuse_guard_middleware, web_server.auth_middleware])
    app[ABUSE_GUARD_KEY] = AbuseGuard()
    app["api"] = play_env.api
    app["subsystems"] = SimpleNamespace(registry=play_env.registry)
    register_games(app)
    seat = _seat(instance)
    url = _player_url(f"/api/games/{game_key}/verify-room-password")
    async with TestClient(TestServer(app)) as client:
        # Correct entries never count.
        for _ in range(8):
            assert (await client.post(url, headers=seat, json={"password": PASSWORD})).status == 200
        statuses = [
            (await client.post(url, headers=seat, json={"password": "wrong-guess"})).status
            for _ in range(6)
        ]
    assert statuses == [403] * 5 + [429]


@pytest.mark.asyncio
async def test_overwriting_an_unreadable_backup_is_logged_without_credentials(tmp_path, caplog) -> None:
    import logging

    from src.engine.game_instance import GameRegistry

    registry = GameRegistry(tmp_path / "saves")
    current = _instance(PASSWORD)
    state_path = registry._save_path(current.game_key)
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(current.to_dict()), encoding="utf-8")
    backup_path = state_path.with_name("state.backup.json")
    backup_path.write_text('{"room_password": "' + PASSWORD + '", broken', encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="trpg"):
        assert await registry.load(current.game_key) is not None
    assert PASSWORD not in backup_path.read_text(encoding="utf-8")
    warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("web|room-pw|bot" in message and "state.backup.json" in message for message in warnings), warnings
    assert all(PASSWORD not in message for message in warnings)
    stored_hash = current.modules["room_access"]["room_password_hash"]
    assert all(stored_hash not in message for message in warnings)
