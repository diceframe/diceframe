from types import SimpleNamespace

from src.webui.viewer import viewer_for


def _instance():
    return SimpleNamespace(gm_uid="gm", players={"p1": {}})


def test_owner_preview_projects_target_seat_for_read_routes():
    inst = _instance()
    request = {
        "user_id": "p1",
        "owner_authenticated": True,
        "player_preview": True,
    }
    viewer = viewer_for(request, inst)
    assert viewer.kind == "seat"
    assert not viewer.is_gm
    assert viewer.uid == "p1"


def test_owner_without_preview_keeps_gm_read_projection():
    viewer = viewer_for(
        {"user_id": "", "owner_authenticated": True, "player_preview": False},
        _instance(),
    )
    assert viewer.kind == "gm"


def test_unknown_preview_is_not_gm():
    viewer = viewer_for(
        {"user_id": "missing", "owner_authenticated": True, "player_preview": True},
        _instance(),
    )
    assert viewer.kind == "outsider"
