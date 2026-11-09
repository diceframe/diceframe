"""Ghost-player cleanup must never delete a seat it cannot disprove.

Regression for a real table: the codec keeps only ``log[-100:]``, so the old
rule ("not mentioned in the retained window") dropped seats that were simply
quiet, and every seat-scoped entry point then answered 403 for them — a Push
decision nobody could take stalled the round until the stuck-run recovery.

Issue #714 / release v3.1.2 follow-up.
"""

from src.engine.modules import room_access, seat_activity
from src.migrations.instance import normalize_game_state_payload


def payload(players, *, log=None, modules=None):
    data = {
        "instance_schema_version": 21,
        "game_key": ["web", "ghost", "bot"],
        "rule_id": "freeform_coc",
        "state": "active_action",
        "players": players,
        "log": log if log is not None else [{"actions": [{"user_id": "p1", "text": "行动"}]}],
    }
    if modules is not None:
        data["modules"] = modules
    return data


def seat(**extra):
    return {"user_id": "quiet", **extra}


def test_unmentioned_seat_with_a_character_keeps_its_place():
    """The reported case: a joined seat silenced by the log window stays."""

    migrated = normalize_game_state_payload(payload({
        "p1": {"user_id": "p1", "character_name": "阿岚", "character_sheet": {"hp": 10}},
        "quiet": seat(character_name="小周", character_sheet={"hp": 8}),
    }))

    assert sorted(migrated["players"]) == ["p1", "quiet"]


def test_seat_credential_keeps_an_unmentioned_seat():
    """A link somebody can still use is evidence the seat exists."""

    migrated = normalize_game_state_payload(payload(
        {
            "p1": {"user_id": "p1", "character_name": "阿岚"},
            "quiet": seat(),
        },
        modules={
            room_access.MODULE_NAME: {
                "schema_version": room_access.SCHEMA_VERSION,
                "seat_credentials": {"quiet": {"hash": "abc"}},
            },
        },
    ))

    assert sorted(migrated["players"]) == ["p1", "quiet"]


def test_recorded_seat_activity_keeps_an_unmentioned_seat():
    """Acting once is remembered past the log window."""

    migrated = normalize_game_state_payload(payload(
        {
            "p1": {"user_id": "p1", "character_name": "阿岚"},
            "quiet": seat(),
        },
        modules={
            seat_activity.MODULE_NAME: {
                "schema_version": seat_activity.SCHEMA_VERSION,
                "acted_seats": ["quiet"],
            },
        },
    ))

    assert sorted(migrated["players"]) == ["p1", "quiet"]


def test_ready_and_away_marks_keep_the_seat():
    """#716 fail-closed: a ready/away flag is persisted evidence of a real seat."""

    for key in ("ready_players", "away_players"):
        data = payload({
            "p1": {"user_id": "p1", "character_name": "阿岚"},
            "quiet": {**seat(), "control": {"mode": "ai", "revision": 1}},
        })
        data[key] = ["quiet"]
        migrated = normalize_game_state_payload(data)
        assert sorted(migrated["players"]) == ["p1", "quiet"], key
        assert migrated[key] == ["quiet"], key


def test_empty_shell_is_still_removed():
    """No log reference, no credential, no action, no character: still a ghost."""

    migrated = normalize_game_state_payload(payload({
        "p1": {"user_id": "p1", "character_name": "阿岚", "character_sheet": {"hp": 10}},
        "quiet": {**seat(), "control": {"mode": "ai", "revision": 1}},
    }))

    assert sorted(migrated["players"]) == ["p1"]


def test_human_seat_without_a_character_stays_kept():
    """The v41 backfill treats a human-controlled seat as having played."""

    migrated = normalize_game_state_payload(payload({
        "p1": {"user_id": "p1", "character_name": "阿岚", "character_sheet": {"hp": 10}},
        "quiet": {**seat(), "control": {"mode": "human", "revision": 1}},
    }))

    assert sorted(migrated["players"]) == ["p1", "quiet"]


def test_unreadable_module_state_keeps_everyone():
    """Fail closed: a slot this cleanup cannot read is not evidence of absence."""

    for modules in ("broken", {"room_access": "broken"}, {room_access.MODULE_NAME: {"seat_credentials": "broken"}}):
        migrated = normalize_game_state_payload(payload({
            "p1": {"user_id": "p1", "character_name": "阿岚", "character_sheet": {"hp": 10}},
            "quiet": seat(),
        }, modules=modules))
        assert sorted(migrated["players"]) == ["p1", "quiet"], modules
