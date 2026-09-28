from types import SimpleNamespace

from src.engine.participant_view import resolve_viewer


def _instance():
    return SimpleNamespace(gm_uid="gm", players={"p1": {}, "p2": {}})


def test_resolve_viewer_identity_rules():
    inst = _instance()
    assert resolve_viewer(inst, user_id="gm", owner_authenticated=False, player_preview=False).kind == "gm"
    assert resolve_viewer(inst, user_id="p1", owner_authenticated=False, player_preview=False).kind == "seat"
    assert resolve_viewer(inst, user_id="other", owner_authenticated=False, player_preview=False).kind == "outsider"
    assert resolve_viewer(inst, user_id="", owner_authenticated=True, player_preview=False).kind == "gm"
    assert resolve_viewer(inst, user_id="p1", owner_authenticated=True, player_preview=False).kind == "gm"


def test_preview_is_always_target_seat_or_outsider():
    inst = _instance()
    seat = resolve_viewer(inst, user_id="p1", owner_authenticated=True, player_preview=True)
    outsider = resolve_viewer(inst, user_id="other", owner_authenticated=True, player_preview=True)
    assert (seat.kind, seat.uid, seat.is_member) == ("seat", "p1", True)
    assert (outsider.kind, outsider.uid, outsider.is_member) == ("outsider", "other", False)


def test_empty_unverified_identity_is_outsider():
    viewer = resolve_viewer(_instance(), user_id="", owner_authenticated=False, player_preview=False)
    assert viewer.kind == "outsider"
