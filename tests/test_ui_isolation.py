import pytest

from src.webui.ui_isolation import (
    UiIsolationError,
    build_frame_descriptor,
    validate_action_message,
    validate_handshake,
    validate_navigation_target,
    validate_resource_request,
    frame_response_headers,
    UiReconnectPolicy,
)


def test_u02_frame_descriptor_is_prebuilt_and_origin_bound():
    descriptor = build_frame_descriptor(
        "demo-pack",
        "ui/index.html",
        origin="https://ui.example",
        assets=["ui/app.js", "ui/style.css", "ui/app.js"],
    )
    assert descriptor.entrypoint == "ui/index.html"
    assert descriptor.assets == ("ui/app.js", "ui/style.css")
    assert "connect-src 'none'" in descriptor.csp
    assert validate_resource_request("ui/app.js", descriptor) == "ui/app.js"
    headers = frame_response_headers(descriptor)
    assert headers["Content-Security-Policy"] == descriptor.csp
    assert headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in headers
    with pytest.raises(UiIsolationError, match="not declared"):
        validate_resource_request("ui/other.js", descriptor)


@pytest.mark.parametrize("entrypoint", ["../index.html", "/index.html", "ui/app.js"])
def test_u02_descriptor_rejects_escape_and_non_html_entrypoints(entrypoint):
    with pytest.raises(UiIsolationError):
        build_frame_descriptor("demo-pack", entrypoint, origin="https://ui.example")


def test_u02_handshake_requires_nonce_identity_and_explicit_origin():
    nonce = "nonce-0123456789"
    result = validate_handshake(
        {"type": "diceframe.ui.hello", "plugin_id": "demo-pack", "nonce": nonce, "sdk": 1},
        plugin_id="demo-pack", nonce=nonce, origin="https://ui.example",
    )
    assert result["type"] == "diceframe.ui.ready"
    with pytest.raises(UiIsolationError):
        validate_handshake(
            {"type": "diceframe.ui.hello", "plugin_id": "demo-pack", "nonce": "wrong-0123456789", "sdk": 1},
            plugin_id="demo-pack", nonce=nonce, origin="https://ui.example",
        )
    with pytest.raises(UiIsolationError, match="origin"):
        validate_handshake(
            {"type": "diceframe.ui.hello", "plugin_id": "demo-pack", "nonce": nonce, "sdk": 1},
            plugin_id="demo-pack", nonce=nonce, origin="https://evil.example",
            expected_origin="https://ui.example",
        )


def test_u02_origin_and_navigation_policy_reject_credentials_and_cross_origin_targets():
    with pytest.raises(UiIsolationError):
        build_frame_descriptor("demo-pack", "ui/index.html", origin="https://ui.example/app")
    assert validate_navigation_target(
        "https://UI.EXAMPLE/ui/index.html", expected_origin="https://ui.example",
    ) == "https://UI.EXAMPLE/ui/index.html"
    with pytest.raises(UiIsolationError, match="origin mismatch"):
        validate_navigation_target("https://evil.example/", expected_origin="https://ui.example")
    with pytest.raises(UiIsolationError, match="credentials"):
        validate_navigation_target("https://user:pass@ui.example/", expected_origin="https://ui.example")
def test_u02_message_bridge_only_accepts_registered_action_and_json_arguments():
    message = validate_action_message(
        {
            "type": "diceframe.ui.action",
            "plugin_id": "demo-pack",
            "request_id": "req-1",
            "action": "character.rest",
            "arguments": {"character_id": "c1"},
        },
        plugin_id="demo-pack", allowed_actions={"character.rest"},
    )
    assert message["arguments"] == {"character_id": "c1"}
    with pytest.raises(UiIsolationError):
        validate_action_message(
            {
                "type": "diceframe.ui.action", "plugin_id": "demo-pack",
                "request_id": "req-1", "action": "admin.dump",
                "arguments": {},
            },
            plugin_id="demo-pack", allowed_actions={"character.rest"},
        )
    with pytest.raises(UiIsolationError):
        validate_action_message(
            {
                "type": "diceframe.ui.action", "plugin_id": "demo-pack",
                "request_id": "req-1", "action": "character.rest",
                "arguments": {}, "token": "secret",
            },
            plugin_id="demo-pack", allowed_actions={"character.rest"},
        )


def test_u02_reconnect_policy_is_bounded_and_never_replays_ambiguous_writes():
    policy = UiReconnectPolicy(max_attempts=2, base_delay_ms=100)
    assert policy.replay_decision("req-1", read_only=False) == "manual_retry_required"
    assert policy.transport_lost() == {"state": "reconnecting", "attempt": 1, "delay_ms": 100}
    assert policy.replay_decision("req-1", read_only=False) == "wait_for_reconnect"
    assert policy.handshake_succeeded()["state"] == "connected"
    assert policy.replay_decision("req-1", read_only=True) == "replay_read_only"
    assert policy.replay_decision("req-1", confirmed=True) == "use_confirmed_receipt"
    assert policy.transport_lost()["attempt"] == 1
    assert policy.transport_lost()["attempt"] == 2
    assert policy.transport_lost()["state"] == "readonly"
    with pytest.raises(UiIsolationError, match="request id"):
        policy.replay_decision("bad request id", read_only=True)
