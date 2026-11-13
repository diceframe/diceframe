from types import SimpleNamespace

from src.webui.services.plugins import PluginHostDependencies, list_plugin_ui, project_plugin_ui


def test_plugin_ui_projection_normalizes_active_descriptors_and_diagnoses_bad_ones():
    host = SimpleNamespace(plugins={
        "good": SimpleNamespace(
            status="active",
            manifest={
                "name": "Good",
                "ui": {
                    "ui_sdk": 1,
                    "surfaces": [{
                        "slot": "character-sheet",
                        "type": "declarative",
                        "root": {"component": "value", "binding": "view.hp"},
                    }],
                },
            },
        ),
        "bad": SimpleNamespace(
            status="active",
            manifest={
                "name": "Bad",
                "ui": {"ui_sdk": 1, "surfaces": [{
                    "slot": "character-sheet",
                    "type": "declarative",
                    "root": {"component": "html", "value": "unsafe"},
                }]},
            },
        ),
        "disabled": SimpleNamespace(
            status="disabled",
            manifest={"ui": {"ui_sdk": 1, "surfaces": []}},
        ),
    })
    result = list_plugin_ui(PluginHostDependencies(plugin_host=host))
    assert result["ok"] is True
    assert [item["plugin_id"] for item in result["views"]] == ["good"]
    assert result["diagnostics"][0]["plugin_id"] == "bad"


def test_plugin_ui_projection_requires_registered_action_contracts_when_available():
    host = SimpleNamespace(plugins={
        "action-pack": SimpleNamespace(
            status="active",
            manifest={"ui": {"ui_sdk": 1, "surfaces": [{
                "slot": "action-panel", "type": "declarative",
                "root": {"component": "button", "action": "missing.action"},
            }]}},
        ),
    })
    result = list_plugin_ui(PluginHostDependencies(
        plugin_host=host,
        describe_action=lambda name: (_ for _ in ()).throw(KeyError(name)),
    ))
    assert result["views"] == []
    assert result["diagnostics"][0]["plugin_id"] == "action-pack"


def test_plugin_ui_projection_applies_owner_supplied_viewmodel_and_keeps_actions_declared():
    host = SimpleNamespace(plugins={
        "good": SimpleNamespace(
            status="active",
            manifest={
                "name": "Good",
                "ui": {"ui_sdk": 1, "surfaces": [{
                    "slot": "character-sheet", "type": "declarative",
                    "root": {"component": "value", "binding": "view.hp"},
                }]},
            },
        ),
    })
    result = project_plugin_ui(PluginHostDependencies(plugin_host=host), "good", {"hp": 9})
    assert result["ok"] is True
    assert result["descriptor"]["surfaces"][0]["root"]["projected_value"] == 9

    denied = project_plugin_ui(PluginHostDependencies(plugin_host=host), "good", {})
    assert denied["ok"] is False
