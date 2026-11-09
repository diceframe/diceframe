from copy import deepcopy
import json

import pytest

from src.engine.game_instance import GameInstance
from src.llm.context_builder import _shrink_to_window, build_context, build_player_safe_context
from src.llm.private_perceptions import format_gm_private_perceptions


@pytest.fixture
def game():
    instance = GameInstance(game_key=("test", "perceptions", "bot"), language="en")
    instance.put_player("alice", {"character_name": "Alice", "character_sheet": {}})
    instance.put_player("bob", {"character_name": "Bob", "character_sheet": {}})
    instance.log = [
        {"round": number, "actions": [], "gm_response": "The party searches the room.", "swipes": []}
        for number in (1, 2, 3)
    ]
    instance.append_private_message("alice", {"round": 1, "text": "A luggage strap lies under the seat; it remains there."})
    instance.append_private_message("bob", {"round": 2, "text": "A hidden blue mark is visible to Bob."})
    return instance


@pytest.mark.asyncio
async def test_gm_remembers_private_discovery_without_adventure_binding(game):
    before_players = deepcopy(game.players)
    context = await build_context(game, "GM rules", [], "Alice picks up the strap.")

    assert "A luggage strap lies under the seat; it remains there." in context
    assert "A hidden blue mark is visible to Bob." in context
    assert '"actor_uid": "alice"' in context and '"character": "Bob"' in context
    assert not game.adventure_binding
    assert game.players == before_players


@pytest.mark.asyncio
async def test_player_and_party_contexts_do_not_receive_other_characters_perceptions(game):
    own = await build_player_safe_context(game, "GM rules", [], "What do I know?", "alice")
    party = await build_player_safe_context(game, "GM rules", [], "What does the party know?", "alice", visibility="party")

    assert "A luggage strap lies under the seat" in own
    assert "hidden blue mark" not in own
    assert "A luggage strap lies under the seat" not in party
    assert "hidden blue mark" not in party


@pytest.mark.asyncio
async def test_history_override_and_swipes_exclude_unavailable_perceptions(game):
    game.log[1]["swipes"] = ["Original version", "Alternate version"]
    game.append_private_message("alice", {"round": 3, "text": "A future discovery."})
    before = deepcopy(game.modules["private_channels"])
    context = await build_context(game, "GM rules", [], "Continue.", history_override=game.log[:2])

    assert "A luggage strap lies under the seat" in context
    assert "hidden blue mark" not in context
    assert "A future discovery" not in context
    assert game.modules["private_channels"] == before


@pytest.mark.asyncio
async def test_recent_public_history_limit_does_not_erase_private_knowledge(game):
    context = await build_context(game, "GM rules", [], "Continue.", context_recent_rounds=1)
    assert "A luggage strap lies under the seat" in context


@pytest.mark.parametrize("language,heading", [
    ("en", "Private Perceptions · GM Only"),
    ("zh-CN", "各角色已收到的私密感知"),
    ("ja", "Private Perceptions · GM Only"),
])
def test_localized_policy_and_complete_json_records(game, language, heading):
    text = format_gm_private_perceptions(game, game.log, language, max_chars=3000)
    assert heading in text
    assert [row["actor_uid"] for row in json.loads(text.rsplit("\n", 1)[1])] == ["alice", "bob"]


def test_budget_keeps_newest_complete_observations_and_ignores_malformed_data(game):
    logs = game.modules["private_channels"]["private_log"]
    logs["alice"] = [
        {"round": 1, "text": "OLD_OVERSIZED " + "x" * 9000},
        None, {"round": True, "text": "INVALID_BOOLEAN_ROUND"},
        {"round": 2, "text": {"not": "text"}},
        {"round": [2], "text": "INVALID_LIST_ROUND"},
        {"round": 3, "text": "LATEST_DISCOVERY"},
    ]
    logs["outsider"] = [{"round": 3, "text": "UNRELATED_ACTOR"}]
    text = format_gm_private_perceptions(game, game.log, "en", max_chars=900)
    assert len(text) <= 900
    assert "LATEST_DISCOVERY" in text
    assert all(marker not in text for marker in ("OLD_OVERSIZED", "INVALID_", "UNRELATED_ACTOR"))
    json.loads(text.rsplit("\n", 1)[1])
    assert format_gm_private_perceptions(game, game.log, "en", max_chars=10) == ""


def test_removed_history_and_extreme_context_shrinking_drop_private_records(game):
    assert format_gm_private_perceptions(game, [], "en", max_chars=3000) == ""
    text = format_gm_private_perceptions(game, game.log, "en", max_chars=3000)
    parts = [text]
    _shrink_to_window(parts, {"private_perceptions": 0}, len(text) - 1)
    assert parts == [""]
