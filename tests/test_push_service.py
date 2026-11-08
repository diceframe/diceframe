from src.engine.modules import checks
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from src.engine.participant_view import Viewer
from tests.test_push_decisions import prepared


def setup(save=None):
    from src.webui.services.check_decisions import CheckDecisionDependencies, CheckDecisionService

    inst, _ = prepared()
    save = save or AsyncMock()
    service = CheckDecisionService(CheckDecisionDependencies(lambda _key: inst, save))
    return inst, service, save


def body(inst, **extra):
    return {"run_id": inst.run_id, "round_number": inst.round_number,
            "expected_revision": checks.last_checks(inst)[0]["push"]["revision"], **extra}


@pytest.mark.asyncio
async def test_permissions_stale_and_client_dice_rejected_before_save():
    inst, service, save = setup()
    before = deepcopy(inst.to_dict())
    for viewer, operation, data in [
        (Viewer("seat", "b"), "request", body(inst, reason="other seat")),
        (Viewer("seat", "a"), "offer", body(inst, consequence="self approved")),
        (Viewer("gm"), "request", body(inst, reason="no player consent")),
        (Viewer("seat", "a"), "request", body(inst, reason="test", roll=1)),
        (Viewer("seat", "a"), "request", {**body(inst, reason="test"), "run_id": "old"}),
    ]:
        assert not (await service.update("g", "c1", viewer, operation, data))["ok"]
    assert inst.to_dict() == before
    save.assert_not_called()


@pytest.mark.asyncio
async def test_owner_authenticated_seat_still_decides_its_own_push():
    """The host's own browser plays a seat: reads resolve as GM, Push must not.

    Regression for a table whose owner credential is in the same browser that
    plays the check's seat: deriving the acting identity from the viewer turned
    ``request``/``offer``/``confirm`` into a Keeper adjudication, so every click
    answered 403 and the round sat in judgment until the stuck-run recovery.
    """
    inst, service, _ = setup()
    host = Viewer("gm", "a")
    result = await service.update("g", "c1", host, "request", body(inst, reason="use the lamp"))
    assert result["ok"] and not result["ready_to_resolve"]
    result = await service.update("g", "c1", host, "offer", body(inst, consequence="the guard hears you"))
    assert result["ok"] and not result["ready_to_resolve"]
    result = await service.update("g", "c1", host, "confirm", body(inst))
    assert result["ok"] and result["ready_to_resolve"]


@pytest.mark.asyncio
async def test_keeper_authority_never_decides_for_a_seat():
    inst, service, save = setup()
    before = deepcopy(inst.to_dict())
    # The Keeper owns neither consent nor rejection of a player's Push …
    for viewer in (Viewer("gm"), Viewer("gm", "b")):
        denied = await service.update("g", "c1", viewer, "decline", body(inst))
        assert not denied["ok"] and denied["code"] == "PUSH_FORBIDDEN"
    assert inst.to_dict() == before
    save.assert_not_called()


def test_refusals_carry_a_message_and_a_localizable_code():
    from src.webui.errors import ERROR_CODE_MAP
    from src.webui.services.check_decisions import refusal

    for code in ("PUSH_FORBIDDEN", "PUSH_INVALID_REQUEST", "PUSH_INVALID_OR_STALE", "PREVIEW_READ_ONLY"):
        payload = refusal(code)
        assert payload["code"] == code and payload["error"]
        assert ERROR_CODE_MAP[payload["error"]]


@pytest.mark.asyncio
async def test_save_failure_restores_whole_state_and_does_not_cancel_timer():
    inst, service, save = setup(AsyncMock(side_effect=OSError("disk full")))
    from unittest.mock import Mock

    timer = Mock()
    timer.done.return_value = False
    inst._luck_timers["c1"] = timer
    before = deepcopy(inst.to_dict())
    with pytest.raises(OSError):
        await service.update("g", "c1", Viewer("seat", "a"), "request", body(inst, reason="new approach"))
    assert inst.to_dict() == before
    timer.cancel.assert_not_called()
    assert inst._luck_timers["c1"] is timer


@pytest.mark.asyncio
async def test_keeper_offer_and_player_confirm_resume_only_after_final_decision(monkeypatch):
    inst, service, _ = setup()
    result = await service.update("g", "c1", Viewer("seat", "a"), "request", body(inst, reason="new approach"))
    assert result["ok"] and not result["ready_to_resolve"]
    result = await service.update("g", "c1", Viewer("gm"), "offer", body(inst, consequence="Sound the alarm"))
    assert not result["ready_to_resolve"]
    from src.engine.dice_rng import DiceResult

    monkeypatch.setattr("src.engine.check_decisions.check_d100_bonus", lambda *_args: (
        DiceResult("d100", [23], 0, 23, 23), "普通成功"))
    result = await service.update("g", "c1", Viewer("seat", "a"), "confirm", body(inst))
    assert result["ok"] and result["ready_to_resolve"]
    assert result["check_result"]["push"]["original"]["roll"] == 60


@pytest.mark.asyncio
async def test_push_only_recovery_keeps_waiting_for_player_consent(tmp_path):
    from src.engine.game_instance import GameRegistry, GameState
    from src.engine import check_decisions

    inst, _ = prepared()
    check_decisions.change(inst, "c1", "a", "request", run_id=inst.run_id, round_number=inst.round_number,
                           expected_revision=0, reason="new approach")
    registry = GameRegistry(tmp_path / "saves")
    await registry.save(inst)
    recovered = await GameRegistry(tmp_path / "saves").recover_all()
    assert len(recovered) == 1
    assert recovered[0].state == GameState.ACTIVE_JUDGMENT
    assert check_decisions.pending(recovered[0])


@pytest.mark.asyncio
async def test_luck_save_failure_cannot_charge_a_player_twice():
    from src.webui.services.game_controls import GameControlDependencies, GameControlService

    inst, _ = prepared()
    save = AsyncMock(side_effect=OSError("disk full"))
    service = GameControlService(GameControlDependencies(lambda _key: inst.game_key, lambda _key: inst, save, lambda _inst: None))
    before = deepcopy(inst.to_dict())
    with pytest.raises(OSError):
        await service.resolve_luck_decision("g", "c1", "a", True)
    assert inst.to_dict() == before
    save.side_effect = None
    assert (await service.resolve_luck_decision("g", "c1", "a", True))["ok"]
    assert inst.get_character_sheet("a")["luck"] == 30
    assert not inst.pending_luck_checks()
    assert (await service.resolve_luck_decision("g", "c1", "a", True))["already_resolved"]
    assert inst.get_character_sheet("a")["luck"] == 30


@pytest.mark.asyncio
async def test_future_sibling_decision_rejects_before_any_publication():
    inst, service, save = setup()
    other = deepcopy(checks.last_checks(inst)[0])
    other["check_id"] = "future"
    other["push"]["schema_version"] = 99
    checks.last_checks(inst).append(other)
    before = deepcopy(inst.to_dict())
    result = await service.update("g", "c1", Viewer("seat", "a"), "request", body(inst, reason="new approach"))
    assert not result["ok"]
    assert inst.to_dict() == before
    save.assert_not_called()
