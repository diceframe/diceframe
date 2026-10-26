from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.engine.game_instance import GameRegistry, GameState
from src.engine.modules import room_access, run_finale, skill_growth
from src.engine.world_state import apply_world_ops
from tests.test_intermission_growth import fixture
from tests.test_run_story_service import GM, setup


@pytest.mark.asyncio
async def test_finale_commit_keeps_access_settings_out_of_the_gameplay_fingerprint(tmp_path):
    service, _, _, _ = setup()
    instance, rule, check = fixture()
    instance.game_key = ("web", "finale-fingerprint", "gm")
    instance.state = GameState.ACTIVE_ACTION
    instance.gm_uid = "gm"
    room_access.replace_max_players(instance, 4)
    room_access.replace_player_access_open(instance, False)
    instance.set_room_password("swordfish")
    room_access.issue_room_token(instance, token="existing-room-token")
    room_access.issue_seat_token(instance, "alice")
    skill_growth.mark_checks(instance, rule, [check])

    registry = GameRegistry(tmp_path / "saves")
    registry.register(instance)
    service.deps = replace(
        service.deps,
        get_instance=lambda _: registry.get(instance.game_key),
        save_instance=registry.save,
        load_rule=lambda _: rule,
    )

    prepared = await service.mutate("game", GM, "prepare", {"run_id": instance.run_id})
    assert prepared["ok"]
    job_id = prepared["finale"]["id"]
    assert service.get("game", GM)["finale"]["status"] == "preparing"

    # Exercise the actual save/load codec before the GM saves reviewed material.
    restored_registry = GameRegistry(tmp_path / "saves")
    restored = await restored_registry.load(instance.game_key)
    assert restored is not None
    service.deps = replace(
        service.deps,
        get_instance=lambda _: restored_registry.get(instance.game_key),
        save_instance=restored_registry.save,
    )

    material = {
        "run_id": restored.run_id,
        "job_id": job_id,
        "job_revision": 0,
        "epilogue": "The end",
        "recap": "The whole run",
    }
    assert (await service.mutate("game", GM, "materials", material))["ok"]
    with patch("src.engine.dice_rng.roll_die", side_effect=[98, 6]):
        committed = await service.mutate(
            "game", GM, "commit", {**material, "job_revision": 1, "confirmed": True},
        )
    assert committed["ok"]
    assert restored.state == GameState.ENDED
    assert committed["finale"]["receipt"]["skill_growth"]["changes"]
    grown = deepcopy(restored.get_character_sheet("alice"))
    again = await service.mutate(
        "game", GM, "commit", {**material, "job_revision": 1, "confirmed": True},
    )
    assert again["ok"] and restored.get_character_sheet("alice") == grown
    assert room_access.verify_room_token(restored, "existing-room-token")
    assert room_access.has_seat_token(restored, "alice")


@pytest.mark.asyncio
async def test_direct_preparation_with_room_access_allows_service_materials():
    service, instance, _, _ = setup()
    instance.set_room_password("swordfish")
    room_access.issue_room_token(instance, token="existing-room-token")
    job = run_finale.prepare(instance)
    result = await service.mutate("key", GM, "materials", {
        "run_id": instance.run_id, "job_id": job["id"], "job_revision": 0,
        "epilogue": "The end", "recap": "The whole run",
    })
    assert result["ok"] and run_finale.view(instance)["job"]["revision"] == 1


@pytest.mark.asyncio
async def test_access_rotation_during_preparation_is_preserved_and_does_not_invalidate():
    service, instance, _, _ = setup()
    instance.set_room_password("old-password")
    room_access.issue_room_token(instance, token="old-token")
    old_seat_token = room_access.issue_seat_token(instance, "alice")
    prepared = await service.mutate("key", GM, "prepare", {"run_id": instance.run_id})
    room_access.replace_max_players(instance, 3)
    instance.set_room_password("new-password")
    room_access.issue_room_token(instance, token="rotated-token")
    room_access.revoke_seat_token(instance, "alice")
    new_seat_token = room_access.issue_seat_token(instance, "alice")
    run_finale.require_job(instance, prepared["finale"]["id"])
    result = await service.mutate("key", GM, "materials", {
        "run_id": instance.run_id, "job_id": prepared["finale"]["id"], "job_revision": 0,
        "epilogue": "The end", "recap": "The whole run",
    })
    assert result["ok"]
    assert room_access.max_players(instance) == 3
    assert room_access.verify_room_password(instance, "new-password")
    assert not room_access.verify_room_token(instance, "old-token")
    assert room_access.verify_room_token(instance, "rotated-token")
    assert room_access.verify_seat_token(instance, old_seat_token) is None
    assert room_access.verify_seat_token(instance, new_seat_token) == "alice"


@pytest.mark.parametrize("mutate", [
    lambda instance: instance.get_character_sheet("alice").update(hp=1),
    lambda instance: apply_world_ops(instance, [{"op": "set_fact", "key": "actor:alice.location", "value": "changed"}]),
    lambda instance: instance.log[0].update(gm_response="Changed oldest round"),
    lambda instance: instance.modules["run_story"].update(revision=1),
], ids=["character", "world", "oldest-log", "story-module"])
def test_gameplay_changes_still_invalidate_preparation(mutate):
    _, instance, _, _ = setup()
    instance.log = [{"round": number, "gm_response": f"Round {number}"} for number in range(101)]
    job = run_finale.prepare(instance)
    before = deepcopy(run_finale.view(instance))
    mutate(instance)
    with pytest.raises(ValueError, match="frozen run changed"):
        run_finale.materials(instance, {"job_id": job["id"], "job_revision": 0,
                                        "epilogue": "The end", "recap": "The whole run"})
    assert run_finale.view(instance) == before


@pytest.mark.asyncio
async def test_stale_digest_requires_cancel_and_fresh_preparation():
    service, instance, _, _ = setup()
    prepared = await service.mutate("key", GM, "prepare", {"run_id": instance.run_id})
    job_id = prepared["finale"]["id"]
    instance.modules["run_finale"]["job"]["source_digest"] = "stale-source-digest"
    before = deepcopy(run_finale.view(instance))
    material = {
        "run_id": instance.run_id, "job_id": job_id, "job_revision": 0,
        "epilogue": "The end", "recap": "The whole run",
    }
    result = await service.mutate("key", GM, "materials", material)
    assert not result["ok"] and "frozen run changed" in result["error"]
    assert run_finale.view(instance) == before
    assert (await service.mutate("key", GM, "cancel", material))["ok"]
    fresh = await service.mutate("key", GM, "prepare", {"run_id": instance.run_id})
    assert fresh["ok"] and fresh["finale"]["id"] != job_id
    assert (await service.mutate("key", GM, "materials", {
        **material, "job_id": fresh["finale"]["id"],
    }))["ok"]


@pytest.mark.asyncio
async def test_finale_ai_draft_with_room_access_is_review_only():
    service, instance, _, model = setup()
    instance.set_room_password("swordfish")
    room_access.issue_room_token(instance, token="existing-room-token")
    prepared = await service.mutate("key", GM, "prepare", {"run_id": instance.run_id})
    model.call.return_value = SimpleNamespace(
        content='{"epilogue":"Draft end","recap":"Draft recap"}', total_tokens=1,
    )
    result = await service.generate("key", GM, "finale", {
        "run_id": instance.run_id, "job_id": prepared["finale"]["id"], "token_consent": True,
    }, AsyncMock())
    assert result["ok"]
    assert run_finale.view(instance)["archive"] is None
    assert run_finale.view(instance)["job"]["epilogue"] == ""
