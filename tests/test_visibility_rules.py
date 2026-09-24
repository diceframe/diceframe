"""Visibility contracts for private economy proposals and manual rolls."""

from __future__ import annotations

import json

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.engine.player_control import set_away_control_policy
from src.engine.visibility_rules import manual_roll_visible_to, proposal_visible_to
from src.llm.context_builder import _manual_roll_visible_to_viewer
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.routes.sse import _play_public_signature
from src.webui.services.game_controls import GameControlDependencies, GameControlService
from src.webui.services.game_queries import GameQueryDependencies, game_detail
from src.webui.services.manual_rolls import ManualRollDependencies, ManualRollService
from src.webui.services.turns import (
    TurnDependencies, _round_payload, economy_decision_pending_payload,
    resume_after_control_change,
)

KEY = "web|visibility|bot"


def _instance() -> GameInstance:
    inst = GameInstance(game_key=("web", "visibility", "bot"), gm_uid="gm")
    inst.state = GameState.ACTIVE_ACTION
    for uid in ("a", "b"):
        inst.put_player(uid, {"user_id": uid, "character_name": uid, "character_sheet": {"hp": 10}})
    return inst


def _proposal(inst: GameInstance, proposal_id: str, **overrides: object) -> dict:
    return {"id": proposal_id, "status": "pending", "run_id": inst.run_id, "kind": "payment",
            "visibility": "private", "payer_uid": "b", **overrides}


def _detail(inst: GameInstance, uid: str) -> dict:
    deps = GameQueryDependencies(
        list_instances=lambda: [inst], get_instance=lambda _: inst,
        parse_game_key=lambda _: inst.game_key, load_world_template=None,
        load_rule_for_game=lambda _: None, ruleset_registry=RulesetRuntimeRegistry(),
    )
    result = game_detail(deps, KEY, viewer_uid=uid, viewer_is_gm=uid == "gm")
    assert result is not None
    return result


def _turns(inst: GameInstance) -> TurnDependencies:
    async def save(_inst: GameInstance) -> None:
        return None

    async def process(_inst: GameInstance, **_kwargs: object) -> tuple[str, None]:
        return "narration", None

    async def unused(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("unused dependency")

    return TurnDependencies(
        get_instance=lambda _: inst, parse_game_key=lambda _: inst.game_key,
        ruleset_registry=RulesetRuntimeRegistry(), load_rule_for_game=lambda _: None,
        prepare_round_checks_ai=None, prepare_round_checks=None,
        resolve_pending_dice=unused, roll_for_game=lambda _: {}, save_instance=save,
        process_round=process, resolve_luck_decision=unused, decline_pending_luck=unused,
    )


@pytest.mark.asyncio
async def test_l8_away_takeover_resume_hides_other_seat_private_proposal() -> None:
    inst = _instance()
    set_away_control_policy(inst, "ai_takeover")
    inst.economy["proposals"].append(_proposal(
        inst, "b-only", kind="purchase", approval_policy="payer", recipient_uid="b",
        rewards=[{"kind": "item", "item_id": "test-item"}],
    ))
    await inst.add_action("b", "I wait.")
    turns = _turns(inst)

    async def save(_inst: GameInstance) -> None:
        return None

    controls = GameControlService(GameControlDependencies(
        parse_game_key=lambda _: inst.game_key, get_instance=lambda _: inst,
        save_instance=save, load_rule=lambda _: None,
        resume_after_control_change=lambda key, uid: resume_after_control_change(turns, key, seat_uid=uid),
    ))
    response = await controls.set_player_away(KEY, "a", True)
    assert response["ok"] is True
    assert response["resume"]["economy_proposals"] == []


@pytest.mark.parametrize("viewer,gm,overrides,expected", [
    ("outsider", True, {}, True),
    ("outsider", False, {"visibility": "party"}, True),
    ("b", False, {}, True),
    ("legacy", False, {"uid": "legacy"}, True),
    ("a", False, {"recipient_uid": "a"}, True),
    ("contributor", False, {"contributors": [{"uid": "contributor"}]}, True),
    ("outsider", False, {}, False),
    ("", False, {}, False),
])
def test_proposal_visibility_truth_table(viewer, gm, overrides, expected) -> None:
    assert proposal_visible_to(
        {"visibility": "private", "payer_uid": "b", **overrides},
        viewer_uid=viewer, viewer_is_gm=gm,
    ) is expected


@pytest.mark.parametrize("viewer,gm,overrides,expected", [
    ("gm", True, {}, True),
    ("outsider", False, {"visibility": "party"}, True),
    ("outsider", False, {"visibility": None}, True),
    ("creator", False, {}, True),
    ("target", False, {}, True),
    ("outsider", False, {}, False),
    ("", False, {}, False),
])
def test_manual_roll_visibility_truth_table(viewer, gm, overrides, expected) -> None:
    assert manual_roll_visible_to(
        {"visibility": "private", "created_by": "creator", "target_uids": ["target"], **overrides},
        viewer_uid=viewer, viewer_is_gm=gm,
    ) is expected


@pytest.mark.parametrize("viewer", ["gm", "a", "b", "legacy", "contributor", "outsider", ""])
def test_e1_e2_e3_e4_proposal_ids_agree(viewer: str) -> None:
    inst = _instance()
    inst.economy["proposals"] = [
        _proposal(inst, "payer"),
        _proposal(inst, "recipient", recipient_uid="a"),
        _proposal(inst, "legacy", uid="legacy"),
        _proposal(inst, "contributor", contributors=[{"uid": "contributor"}]),
        _proposal(inst, "party", visibility="party"),
    ]
    expected = {p["id"] for p in inst.economy["proposals"] if proposal_visible_to(
        p, viewer_uid=viewer, viewer_is_gm=viewer == "gm",
    )}
    ids = lambda proposals: {p["id"] for p in proposals}
    assert ids(_detail(inst, viewer)["economy_proposals"]) == expected
    assert ids(_round_payload(inst, "", viewer_uid=viewer)["economy_proposals"]) == expected
    assert ids(economy_decision_pending_payload(inst, viewer)["economy_proposals"]) == expected
    assert ids(json.loads(_play_public_signature(inst, viewer))["economy_proposals"]) == expected


@pytest.mark.parametrize("viewer", ["gm", "a", "b", "outsider", ""])
def test_m1_m2_m3_manual_roll_ids_agree(viewer: str) -> None:
    inst = _instance()
    inst.manual_roll_requests = [
        {"id": "party", "visibility": "party"},
        {"id": "default"},
        {"id": "creator", "visibility": "private", "created_by": "a"},
        {"id": "target", "visibility": "private", "created_by": "b", "target_uids": ["a"]},
        {"id": "other", "visibility": "private", "created_by": "b"},
    ]
    for req in inst.manual_roll_requests:
        req.update({"run_id": inst.run_id, "status": "resolved", "results": {"a": {"total": 4}}})
        req.setdefault("target_uids", ["a"])
    service = ManualRollService(ManualRollDependencies(
        parse_game_key=lambda _: inst.game_key, get_instance=lambda _: inst,
        save_instance=_turns(inst).save_instance,
    ))
    expected = {r["id"] for r in inst.manual_roll_requests if manual_roll_visible_to(
        r, viewer_uid=viewer, viewer_is_gm=viewer == "gm",
    )}
    assert {r["id"] for r in service.list(KEY, viewer)} == expected
    assert {r["id"] for r in _detail(inst, viewer)["manual_rolls"]} == expected
    assert {r["id"] for r in inst.manual_roll_requests if _manual_roll_visible_to_viewer(
        r, viewer == "gm", viewer,
    )} == expected


def test_empty_viewer_does_not_match_unset_gm_uid() -> None:
    inst = _instance()
    inst.gm_uid = ""
    inst.economy["proposals"] = [_proposal(inst, "private"), _proposal(inst, "party", visibility="party")]
    assert {p["id"] for p in _detail(inst, "")["economy_proposals"]} == {"party"}
    assert {p["id"] for p in _round_payload(inst, "")["economy_proposals"]} == {"party"}
    assert {p["id"] for p in economy_decision_pending_payload(inst)["economy_proposals"]} == {"party"}
    assert {p["id"] for p in json.loads(_play_public_signature(inst, ""))["economy_proposals"]} == {"party"}
    inst.manual_roll_requests = [
        {"id": "private", "visibility": "private", "created_by": "b", "target_uids": ["a"]},
        {"id": "party", "visibility": "party"},
    ]
    service = ManualRollService(ManualRollDependencies(
        parse_game_key=lambda _: inst.game_key, get_instance=lambda _: inst,
        save_instance=_turns(inst).save_instance,
    ))
    assert {req["id"] for req in service.list(KEY, "")} == {"party"}


def test_recipient_sees_private_payment_in_round_and_barrier() -> None:
    inst = _instance()
    inst.economy["proposals"].append(_proposal(inst, "to-a", recipient_uid="a"))
    assert [p["id"] for p in _round_payload(inst, "", viewer_uid="a")["economy_proposals"]] == ["to-a"]
    assert [p["id"] for p in economy_decision_pending_payload(inst, "a")["economy_proposals"]] == ["to-a"]
