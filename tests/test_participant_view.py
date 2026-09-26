"""Participant read identity priority and fail-closed preview boundaries."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from src.engine.participant_view import Viewer, resolve_viewer


@pytest.mark.parametrize("uid,owner,preview,kind,expected_uid", [
    ("p1", True, True, "seat", "p1"),
    ("", True, False, "gm", "gm"),
    ("gm", False, False, "gm", "gm"),
    ("owner", True, False, "gm", "owner"),
    ("p1", False, False, "seat", "p1"),
    ("other", False, False, "outsider", "other"),
    ("other", True, True, "outsider", "other"),
    ("", False, False, "outsider", ""),
    ("gm", True, True, "outsider", "gm"),
])
def test_resolve_viewer_priority(uid, owner, preview, kind, expected_uid):
    instance = SimpleNamespace(gm_uid="gm", players={"p1": {}})
    viewer = resolve_viewer(instance, user_id=uid, owner_authenticated=owner, player_preview=preview)
    assert viewer == Viewer(kind, expected_uid)
    assert viewer.is_gm == (kind == "gm")
    assert viewer.is_seat == (kind == "seat")
    assert viewer.is_member == (kind != "outsider")


def test_preview_gm_who_also_has_a_seat_is_only_a_seat():
    instance = SimpleNamespace(gm_uid="gm", players={"gm": {}})
    assert resolve_viewer(instance, user_id="gm", owner_authenticated=True, player_preview=True) == Viewer("seat", "gm")


def test_missing_instance_is_safe():
    assert resolve_viewer(None, user_id="p1", owner_authenticated=False, player_preview=False) == Viewer("outsider", "p1")


def test_viewer_is_immutable():
    viewer = Viewer("seat", "p1")
    with pytest.raises(FrozenInstanceError):
        viewer.kind = "gm"
