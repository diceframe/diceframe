import json

import pytest

import web_server
from src.webui.access_password import (
    hash_access_password,
    is_valid_access_password,
    mask_access_password,
    verify_access_password,
)


class _ConfigRequest:
    def __init__(self, body):
        self._body = body
        self.headers = {"X-TRPG-Confirm": "true"}
        self.app = {}

    async def json(self):
        return self._body


def test_blank_or_malformed_access_password_is_not_configured():
    assert not is_valid_access_password("   ")
    assert not is_valid_access_password("pbkdf2_sha256$bad")
    assert mask_access_password("   ") == {"configured": False, "masked": ""}
    assert mask_access_password("pbkdf2_sha256$bad") == {"configured": False, "masked": ""}


def test_valid_hashed_access_password_still_verifies():
    stored = hash_access_password("owner-password")

    assert is_valid_access_password(stored)
    assert verify_access_password("owner-password", stored)
    assert not verify_access_password("wrong-password", stored)


@pytest.mark.asyncio
async def test_empty_password_field_does_not_delete_initial_password_file(monkeypatch):
    deleted = []
    monkeypatch.setattr(web_server, "_delete_access_token_file", lambda: deleted.append(True))
    monkeypatch.setattr(web_server, "save_config", lambda: None)
    monkeypatch.setitem(web_server.STATE, "proxy_enabled", False)

    response = await web_server.api_config_post(_ConfigRequest({"access_token": ""}))
    body = json.loads(response.text)

    assert response.status == 200
    assert body["access_password_changed"] is False
    assert deleted == []


@pytest.mark.asyncio
async def test_new_password_deletes_obsolete_initial_password_file(monkeypatch):
    deleted = []
    monkeypatch.setattr(web_server, "_delete_access_token_file", lambda: deleted.append(True))
    monkeypatch.setattr(web_server, "save_config", lambda: None)
    monkeypatch.setitem(web_server.STATE, "proxy_enabled", False)
    monkeypatch.setitem(web_server.STATE, "access_token", hash_access_password("old-password"))

    response = await web_server.api_config_post(_ConfigRequest({"access_token": "new-password"}))
    body = json.loads(response.text)

    assert response.status == 200
    assert body["access_password_changed"] is True
    assert deleted == [True]
    assert verify_access_password("new-password", web_server.STATE["access_token"])
