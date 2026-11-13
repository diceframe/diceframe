import pytest

from src.webui.ui_extension_probe import (
    DeclarativeUiError,
    normalize_declarative_descriptor,
    project_binding,
    project_declarative_descriptor,
)


def test_declarative_ui_probe_normalizes_whitelisted_tree_and_projection():
    descriptor = normalize_declarative_descriptor({
        "ui_sdk": 1,
        "surfaces": [{
            "slot": "character-sheet",
            "type": "declarative",
            "root": {
                "component": "group",
                "children": [
                    {"component": "value", "label": "HP", "binding": "view.resources.hp"},
                    {"component": "button", "label": "Rest", "action": "rest.short", "arguments": {"kind": "short"}},
                ],
            },
        }],
    })
    assert descriptor["surfaces"][0]["slot"] == "character-sheet"
    assert descriptor["surfaces"][0]["root"]["children"][1]["arguments"] == {"kind": "short"}
    assert project_binding({"resources": {"hp": 7}}, "view.resources.hp") == 7


def test_declarative_ui_probe_rejects_unknown_components_and_unprojected_bindings():
    with pytest.raises(DeclarativeUiError):
        normalize_declarative_descriptor({
            "ui_sdk": 1,
            "surfaces": [{
                "slot": "character-sheet", "type": "declarative",
                "root": {"component": "html", "value": "<script>alert(1)</script>"},
            }],
        })
    with pytest.raises(DeclarativeUiError):
        project_binding({"public": {}}, "view.hidden.secret")


def test_declarative_ui_projection_resolves_only_the_supplied_viewmodel():
    descriptor = {
        "ui_sdk": 1,
        "surfaces": [{
            "slot": "action-panel", "type": "declarative",
            "root": {
                "component": "group",
                "children": [
                    {"component": "value", "binding": "view.resources.hp"},
                    {"component": "list", "binding": "view.effects"},
                ],
            },
        }],
    }
    projected = project_declarative_descriptor(
        descriptor,
        {"resources": {"hp": 7}, "effects": ["shield"]},
    )
    children = projected["surfaces"][0]["root"]["children"]
    assert children[0]["projected_value"] == 7
    assert children[1]["projected_value"] == ["shield"]
    assert "hidden" not in projected


def test_declarative_ui_projection_fails_closed_for_missing_or_oversized_viewmodel():
    descriptor = {
        "ui_sdk": 1,
        "surfaces": [{
            "slot": "character-sheet", "type": "declarative",
            "root": {"component": "value", "binding": "view.private.hp"},
        }],
    }
    with pytest.raises(DeclarativeUiError, match="not available"):
        project_declarative_descriptor(descriptor, {"public": {}})
    huge = {"public": "x" * 140_000}
    with pytest.raises(DeclarativeUiError, match="too large"):
        project_declarative_descriptor(
            {"ui_sdk": 1, "surfaces": [{
                "slot": "character-sheet", "type": "declarative",
                "root": {"component": "value", "binding": "view.public"},
            }]},
            huge,
        )
