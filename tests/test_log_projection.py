"""Public log responses must not expose snapshots or future internal fields."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from src.webui.services import logs


PUBLIC_FIELDS = frozenset({
    "round", "actions", "player_actions", "gm_response", "state_changes",
    "check_results", "swipes", "current_swipe", "timestamp",
    "story_recaps", "scene_image",
})


PUBLIC_ACTION_FIELDS = frozenset({"user_id", "text"})


@pytest.fixture
def internal_action():
    return {
        "user_id": "p1",
        "text": "open the door",
        "metadata": {"private": True},
        "check_request": {"secret_dc": 18},
        "combat_outcome": {"hidden": "value"},
        "future_internal_field": {"secret": True},
        "revision_count": 2,
        "dice_pending": False,
        "dice_system": "d20",
        "dice_roll_source": "server",
    }


@pytest.fixture
def log_context():
    entry = {
        "round": 1,
        "actions": [{"user_id": "p1", "text": "Inspect the door"}],
        "player_actions": {"p1": "Inspect the door"},
        "gm_response": "The door is locked.",
        "state_changes": [],
        "check_results": [],
        "swipes": ["The door is locked."],
        "current_swipe": 0,
        "timestamp": "2026-09-23T12:00:00Z",
        "story_recaps": ["Arrival"],
        "scene_image": {"url": "/scene.png"},
        "round_start_snapshot": {"p1": {"hp": 10}},
        "combat_extension_round_start": {"hidden": "combat-start"},
        "pre_state_snapshot": {"p1": {"secret": "private-player-state"}},
        "pre_combat_extension_snapshot": {"hidden": "combat-before"},
        "pre_world_state": {
            "facts": {"traitor": {"visibility": "gm", "value": "secret-traitor-name"}},
        },
        "pre_adventure_progress": {"next": "hidden-ending"},
        "tags_summary": "gm-only-summary",
        "future_internal": {"secret": "future-secret"},
    }
    # A log without actions must still go through the public projection.
    second = {key: deepcopy(value) for key, value in entry.items() if key != "actions"}
    second["round"] = 2
    instance = SimpleNamespace(log=[entry, second])
    dependencies = logs.LogDependencies(
        registry={("web", "room", "bot"): instance},
        parse_game_key=lambda key: tuple(key.split("|")),
    )
    return dependencies, instance


def test_l6_public_log_withholds_snapshots_and_gm_world_facts(log_context):
    dependencies, instance = log_context
    before = deepcopy(instance.log)

    result = logs.get_log(dependencies, "web|room|bot")

    for entry in result["log"]:
        assert set(entry) <= PUBLIC_FIELDS
        assert "pre_world_state" not in entry
    assert "secret-traitor-name" not in json.dumps(result)
    assert result["log"] == [
        {key: value for key, value in entry.items() if key in PUBLIC_FIELDS}
        for entry in before
    ]
    assert logs.PUBLIC_LOG_FIELDS == PUBLIC_FIELDS
    assert instance.log == before


def test_public_log_withholds_unknown_future_fields(log_context):
    dependencies, _ = log_context

    result = logs.get_log(dependencies, "web|room|bot", include_internal=False)

    assert all("future_internal" not in entry for entry in result["log"])
    assert "future-secret" not in json.dumps(result)


def test_gm_log_retains_complete_entries_without_mutating_source(log_context):
    dependencies, instance = log_context
    before = deepcopy(instance.log)

    result = logs.get_log(dependencies, "web|room|bot", include_internal=True)

    assert result == {"log": before, "total": 2, "page": 1, "total_pages": 1}
    result["log"][0]["pre_world_state"]["facts"].clear()
    assert instance.log == before


@pytest.mark.parametrize("page, expected_round", [(1, 2), (2, 1)])
def test_public_log_projection_preserves_pagination(log_context, page, expected_round):
    dependencies, _ = log_context

    result = logs.get_log(dependencies, "web|room|bot", page=page, per_page=1)

    assert result["total"] == 2
    assert result["page"] == page
    assert result["total_pages"] == 2
    assert len(result["log"]) == 1
    assert result["log"][0]["round"] == expected_round
    assert set(result["log"][0]) <= PUBLIC_FIELDS


@pytest.mark.parametrize("field", ["actions", "player_actions"])
def test_public_log_projects_nested_actions_without_mutating_source(
    log_context, internal_action, field,
):
    dependencies, instance = log_context
    instance.log[0][field] = [internal_action, "legacy action", None]
    before = deepcopy(instance.log)

    result = logs.get_log(dependencies, "web|room|bot", include_internal=False)

    assert logs.PUBLIC_ACTION_FIELDS == PUBLIC_ACTION_FIELDS
    assert result["log"][0][field] == [
        {"user_id": "p1", "text": "open the door"}, "legacy action", None,
    ]
    assert instance.log == before
    result["log"][0][field][0]["text"] = "changed response"
    assert instance.log == before


@pytest.mark.parametrize("field", ["actions", "player_actions"])
def test_internal_log_retains_complete_nested_actions(log_context, internal_action, field):
    dependencies, instance = log_context
    instance.log[0][field] = [internal_action]
    before = deepcopy(instance.log)

    result = logs.get_log(dependencies, "web|room|bot", include_internal=True)

    assert result["log"] == before
    result["log"][0][field][0]["metadata"]["private"] = False
    assert instance.log == before


@pytest.mark.parametrize("field", ["actions", "player_actions"])
@pytest.mark.parametrize("prefix", ["【GM指令】", "[GM Directive]"])
def test_nested_action_projection_keeps_gm_directive_filter(log_context, field, prefix):
    dependencies, instance = log_context
    # In particular, player_actions must be projected even without actions.
    instance.log[0].pop("actions")
    instance.log[0][field] = [
        {"user_id": "system", "text": f"  {prefix} hidden plan", "metadata": {"private": True}},
        {"user_id": "system", "text": "Public announcement"},
        {"user_id": "p1", "text": f"{prefix} quoted by player"},
    ]
    before = deepcopy(instance.log)

    public = logs.get_log(dependencies, "web|room|bot")
    internal = logs.get_log(dependencies, "web|room|bot", include_internal=True)

    assert public["log"][0][field] == before[0][field][1:]
    assert internal["log"] == before
    assert instance.log == before


def test_missing_game_log_is_unchanged(log_context):
    dependencies, _ = log_context

    assert logs.get_log(dependencies, "web|missing|bot", page=3) == {
        "log": [], "total": 0, "page": 3,
    }
