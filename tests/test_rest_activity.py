from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import resource_recovery, ruleset_runtime
from src.webui.services.game_queries import lobby_detail
from src.webui.services.rest_activity import rest_activity


def _player(name: str) -> dict:
    return {"character_name": name, "character_sheet": {"sanity": 40}}


def _source(source_id: str, *, run_id: str, kind: str, actor: str,
            healer: str = "", status: str = "pending") -> dict:
    return {
        "id": source_id,
        "source_id": source_id,
        "run_id": run_id,
        "kind": kind,
        "actor_uid": actor,
        "healer_uid": healer,
        "skill_id": "psychoanalysis" if kind == "therapy" else "",
        "resource_key": "sanity",
        "resource_delta": "",
        "reason": "private reason must not be projected",
        "authorizer": "gm" if status in {"approved", "settled", "rejected"} else "",
        "origin_id": "",
        "started_tick": 0,
        "status": status,
        "seed": "a" * 64,
        "policy": {"version": 1, "policy": "coc_light", "luck_recovery": "check"},
        "created_round": 1,
    }


def _receipt(source: dict) -> dict:
    return {
        "id": source["id"],
        "source_id": source["id"],
        "run_id": source["run_id"],
        "actor_uid": source["actor_uid"],
        "healer_uid": source["healer_uid"],
        "kind": source["kind"],
        "purpose": source["kind"],
        "skill_id": source["skill_id"],
        "check_id": "",
        "resource_key": "sanity",
        "resource_delta": 2,
        "before": 40,
        "after": 42,
        "delta": 2,
        "rolls": {},
        "success": True,
        "fumble": False,
        "policy": source["policy"],
        "round": 1,
        "origin_id": "",
        "authorizer": source["authorizer"],
        "reason": source["reason"],
        "operation_id": "operation-private",
        "scope": "scope-private",
    }


def test_recovery_activity_is_public_stable_and_receipt_backed() -> None:
    instance = GameInstance(("web", "activity"))
    instance.players.update({"alice": _player("Alice"), "bob": _player("Bob")})
    pending = _source("source-pending", run_id=instance.run_id, kind="overnight", actor="alice")
    settled = _source(
        "source-settled", run_id=instance.run_id, kind="therapy", actor="alice",
        healer="bob", status="settled",
    )
    instance.modules[resource_recovery.MODULE_NAME] = {
        **resource_recovery.fresh(),
        "sources": {pending["id"]: pending, settled["id"]: settled},
        "receipts": {settled["id"]: _receipt(settled)},
        "operations": {"operation-private": settled["id"]},
        "consumed": {"scope-private": settled["id"]},
    }

    before = deepcopy(instance.to_dict())
    first = rest_activity(instance)
    second = rest_activity(instance)

    assert first == second
    assert instance.to_dict() == before
    assert first == [
        {
            "id": "source-pending:requested",
            "kind": "overnight",
            "status": "requested",
            "actors": [{"uid": "alice", "name": "Alice"}],
        },
        {
            "id": "source-settled:requested",
            "kind": "therapy",
            "status": "requested",
            "actors": [
                {"uid": "alice", "name": "Alice"},
                {"uid": "bob", "name": "Bob"},
            ],
        },
        {
            "id": "source-settled:settled",
            "kind": "therapy",
            "status": "settled",
            "actors": [
                {"uid": "alice", "name": "Alice"},
                {"uid": "bob", "name": "Bob"},
            ],
        },
    ]
    assert "private" not in repr(first)


def test_recovery_activity_filters_old_runs_departed_players_and_luck() -> None:
    instance = GameInstance(("web", "activity-filter"))
    instance.players["alice"] = _player("Alice")
    current = _source("current", run_id=instance.run_id, kind="overnight", actor="alice")
    old = _source("old", run_id="old-run", kind="overnight", actor="alice")
    departed = _source("departed", run_id=instance.run_id, kind="overnight", actor="bob")
    luck = _source("luck", run_id=instance.run_id, kind="luck", actor="alice")
    luck["origin_id"] = "intermission"
    luck["resource_key"] = "luck"
    instance.modules[resource_recovery.MODULE_NAME] = {
        **resource_recovery.fresh(),
        "sources": {s["id"]: s for s in (current, old, departed, luck)},
    }

    assert rest_activity(instance) == [
        {
            "id": "current:requested",
            "kind": "overnight",
            "status": "requested",
            "actors": [{"uid": "alice", "name": "Alice"}],
        },
    ]


def test_dnd_activity_uses_started_tick_identity_and_terminal_state() -> None:
    instance = GameInstance(("web", "dnd-activity"))
    instance.players.update({"alice": _player("Alice"), "bob": _player("Bob")})
    ruleset_runtime.replace_state(instance, {
        "rest_session": {
            "status": "collecting",
            "rest": "short",
            "required_uids": ["alice", "bob"],
            "participants": {"alice": {"operation_id": "private"}},
            "started_tick": 1442,
        },
    })

    requested = rest_activity(instance)
    assert len(requested) == 1
    assert requested[0]["kind"] == "short"
    assert requested[0]["status"] == "requested"
    assert {actor["uid"] for actor in requested[0]["actors"]} == {"alice"}
    requested_id = requested[0]["id"]

    ruleset_runtime.state(instance)["rest_session"]["participants"]["bob"] = {
        "operation_id": "private-bob",
    }
    ruleset_runtime.state(instance)["rest_session"].update({
        "status": "completed",
        "resolved_tick": 1502,
    })
    completed = rest_activity(instance)
    assert [item["status"] for item in completed] == ["requested", "settled"]
    assert completed[0]["id"] == requested_id
    assert completed[1]["id"] != requested_id
    assert {actor["uid"] for actor in completed[0]["actors"]} == {"alice", "bob"}

    ruleset_runtime.state(instance)["rest_session"].pop("started_tick")
    assert rest_activity(instance) == []


def test_dnd_activity_requires_a_submitted_participant() -> None:
    instance = GameInstance(("web", "dnd-activity-empty"))
    instance.players.update({"alice": _player("Alice"), "bob": _player("Bob")})
    ruleset_runtime.replace_state(instance, {
        "rest_session": {
            "status": "collecting",
            "rest": "long",
            "required_uids": ["alice", "bob"],
            "participants": {},
            "started_tick": 1442,
        },
    })

    assert rest_activity(instance) == []


def test_unknown_recovery_schema_fails_closed() -> None:
    instance = GameInstance(("web", "activity-schema"))
    instance.players["alice"] = _player("Alice")
    instance.modules[resource_recovery.MODULE_NAME] = {"schema_version": 99}

    assert rest_activity(instance) == []


def test_activity_limit_keeps_recent_requested_terminal_pairs() -> None:
    instance = GameInstance(("web", "activity-limit"))
    instance.players["alice"] = _player("Alice")
    sources = [
        _source(f"source-{index}", run_id=instance.run_id, kind="overnight", actor="alice", status="settled")
        for index in range(21)
    ]
    receipts = {}
    operations = {}
    consumed = {}
    for index, source in enumerate(sources):
        receipt = _receipt(source)
        receipt["operation_id"] = f"operation-{index}"
        receipt["scope"] = f"scope-{index}"
        receipts[source["id"]] = receipt
        operations[receipt["operation_id"]] = source["id"]
        consumed[receipt["scope"]] = source["id"]
    instance.modules[resource_recovery.MODULE_NAME] = {
        **resource_recovery.fresh(),
        "sources": {source["id"]: source for source in sources},
        "receipts": receipts,
        "operations": operations,
        "consumed": consumed,
    }

    activity = rest_activity(instance)

    assert len(activity) == 40
    assert activity[0]["id"] == "source-1:requested"
    assert activity[1]["id"] == "source-1:settled"
    assert all(
        first["status"] == "requested" and second["status"] == "settled"
        for first, second in zip(activity[::2], activity[1::2])
    )


@pytest.mark.asyncio
async def test_service_mutation_is_visible_to_other_roster_members() -> None:
    from tests.test_resource_recovery_service import ALICE, GM, body, setup

    service, instance, _, _ = setup()
    request = await service.mutate(
        "key", ALICE, "request", body(service, instance, "activity-request", kind="overnight"),
    )
    assert request["ok"]
    source_id = request["source_id"]
    assert rest_activity(instance)[0]["status"] == "requested"

    settled = await service.mutate(
        "key", GM, "approve", body(service, instance, "activity-settle", source_id=source_id),
    )
    assert settled["ok"]
    activity = rest_activity(instance)
    assert [item["status"] for item in activity] == ["requested", "settled"]
    assert all(item["actors"] == [{"uid": "alice", "name": "alice"}] for item in activity)


def test_lobby_projection_excludes_rest_activity() -> None:
    projected = lobby_detail({
        "game_key": "web|activity|bot",
        "world_name": "World",
        "rest_activity": [{"id": "private", "kind": "short", "status": "requested", "actors": []}],
    })

    assert "rest_activity" not in projected
