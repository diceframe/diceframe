# -*- coding: utf-8 -*-
"""Tests for offline Ed25519 honors verification, local persistence, and HTTP routes."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.webui.routes import honors as honors_routes
from src.webui.routes.honors import HONORS_SERVICE_KEY, register_honors
from src.webui.services.honors import (
    HonorsService,
    HonorsTokenError,
    load_ed25519_public_key,
    sign_honors_token,
    verify_honors_token,
)


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    pub_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    pub_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode("ascii")
    return private_key, pub_b64


def test_sign_and_verify_honors_token_roundtrip():
    private_key, pub_b64 = _generate_keypair()
    public_key = load_ed25519_public_key(pub_b64)
    token = sign_honors_token(
        {
            "id": "DF-0042",
            "name": "星界旅人",
            "badges": ["pioneer", "world_weaver", "pioneer"],
            "iat": 1764300000,
        },
        private_key,
    )
    payload = verify_honors_token(token, public_key)
    assert payload == {
        "id": "DF-0042",
        "name": "星界旅人",
        "badges": ["pioneer", "world_weaver"],
        "iat": 1764300000,
    }


def test_verify_honors_token_rejects_tampering_and_bad_format():
    private_key, pub_b64 = _generate_keypair()
    other_key, _ = _generate_keypair()
    public_key = load_ed25519_public_key(pub_b64)

    valid = sign_honors_token(
        {"id": "DF-0001", "name": "Tester", "badges": ["pioneer"], "iat": 1764300000},
        private_key,
    )
    prefix, payload_seg, sig_seg = valid.split(".")

    # Tampered payload
    tampered_payload = base64.urlsafe_b64encode(
        json.dumps(
            {"id": "DF-0001", "name": "Hacked", "badges": ["pioneer"], "iat": 1764300000}
        ).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    with pytest.raises(HonorsTokenError) as exc_info:
        verify_honors_token(f"{prefix}.{tampered_payload}.{sig_seg}", public_key)
    assert exc_info.value.code == "invalid_signature"

    # Wrong signing key
    wrong_key_token = sign_honors_token(
        {"id": "DF-0001", "name": "Tester", "badges": ["pioneer"], "iat": 1764300000},
        other_key,
    )
    with pytest.raises(HonorsTokenError) as exc_info:
        verify_honors_token(wrong_key_token, public_key)
    assert exc_info.value.code == "invalid_signature"

    # Bad prefix
    with pytest.raises(HonorsTokenError) as exc_info:
        verify_honors_token(f"BAD1.{payload_seg}.{sig_seg}", public_key)
    assert exc_info.value.code == "invalid_format"


@pytest.mark.asyncio
async def test_honors_service_persistence_featured_and_tamper_fail_closed(tmp_path: Path):
    private_key, pub_b64 = _generate_keypair()
    service = HonorsService(tmp_path, public_key_b64=pub_b64, api_base="")

    assert service.get_state()["active"] is None

    token = sign_honors_token(
        {
            "id": "DF-0042",
            "name": "星界旅人",
            "badges": ["pioneer", "world_weaver", "voyage_mark"],
            "iat": 1764300000,
        },
        private_key,
    )
    activated = service.activate_token(token)
    assert activated["active"]["id"] == "DF-0042"
    assert activated["active"]["featured_badge_id"] == "pioneer"

    updated = service.set_featured_badge("world_weaver")
    assert updated["active"]["featured_badge_id"] == "world_weaver"

    with pytest.raises(HonorsTokenError) as exc_info:
        service.set_featured_badge("arcane_forger")
    assert exc_info.value.code == "badge_not_owned"

    # Offline wall separates listed items and counter badges
    wall = await service.fetch_wall()
    assert wall["source"] == "offline"
    assert len(wall["items"]) == 1
    assert wall["items"][0]["badges"] == ["pioneer", "world_weaver"]
    assert wall["counters"] == {"voyage_mark": 1}

    # Re-reading from a fresh service preserves verified state
    reloaded = HonorsService(tmp_path, public_key_b64=pub_b64, api_base="")
    assert reloaded.get_state()["active"]["featured_badge_id"] == "world_weaver"

    # Tampering with stored file fails closed
    store_file = tmp_path / "honors.json"
    raw = json.loads(store_file.read_text(encoding="utf-8"))
    raw["token"] = token[:-4] + "AAAA"
    store_file.write_text(json.dumps(raw), encoding="utf-8")
    tampered_state = reloaded.get_state()
    assert tampered_state["active"] is None
    assert tampered_state["warning"] == "invalid_signature"

    cleared = reloaded.clear_credential()
    assert cleared["active"] is None
    assert not store_file.exists()


@pytest.mark.asyncio
async def test_honors_http_routes_end_to_end(tmp_path: Path):
    private_key, pub_b64 = _generate_keypair()
    app = web.Application()
    app[HONORS_SERVICE_KEY] = HonorsService(tmp_path, public_key_b64=pub_b64, api_base="")
    register_honors(app)

    registered_paths = {
        resource.canonical for resource in app.router.resources()
    }
    assert "/api/honors" in registered_paths
    assert "/api/honors/activate" in registered_paths
    assert "/api/honors/featured" in registered_paths
    assert "/api/honors/wall" in registered_paths

    token = sign_honors_token(
        {
            "id": "DF-0099",
            "name": "Keeper",
            "badges": ["world_weaver", "chronicler"],
            "iat": 1764300100,
        },
        private_key,
    )

    def _req(body=None, query=None):
        async def _json():
            return body if body is not None else {}

        return SimpleNamespace(app=app, json=_json, query=query or {})

    resp = await honors_routes.api_honors_get(_req())
    assert resp.status == 200
    body = json.loads(resp.text)
    assert body["ok"] is True
    assert body["active"] is None
    assert len(body["catalog"]) >= 5

    bad = await honors_routes.api_honors_activate(_req({"token": "invalid"}))
    assert bad.status == 400
    bad_body = json.loads(bad.text)
    assert bad_body["ok"] is False

    ok = await honors_routes.api_honors_activate(_req({"token": token}))
    assert ok.status == 200
    ok_body = json.loads(ok.text)
    assert ok_body["active"]["name"] == "Keeper"

    feat = await honors_routes.api_honors_featured(_req({"badge_id": "chronicler"}))
    assert feat.status == 200
    feat_body = json.loads(feat.text)
    assert feat_body["active"]["featured_badge_id"] == "chronicler"

    wall = await honors_routes.api_honors_wall(_req(query={"badge": "chronicler", "q": "Keep"}))
    assert wall.status == 200
    wall_body = json.loads(wall.text)
    assert wall_body["total"] == 1
    assert wall_body["items"][0]["id"] == "DF-0099"

    deleted = await honors_routes.api_honors_clear(_req())
    assert deleted.status == 200
    del_body = json.loads(deleted.text)
    assert del_body["active"] is None
