from src.engine.game_instance import GameInstance, GameRegistry
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services.game_queries import GameQueryDependencies, list_games


def _query_dependencies(registry: GameRegistry) -> GameQueryDependencies:
    return GameQueryDependencies(
        list_instances=registry.list_all,
        get_instance=registry.get,
        parse_game_key=lambda game_key: tuple(game_key.split("|")),
        load_world_template=None,
        load_rule_for_game=lambda _instance: None,
        ruleset_registry=RulesetRuntimeRegistry(),
    )


def test_list_games_returns_most_recent_activity_first(tmp_path):
    registry = GameRegistry(tmp_path)
    old = GameInstance(
        game_key=("web", "old", "bot"),
        rule_id="freeform_fantasy",
        world_name="Old",
    )
    old.started_at = "2026-08-01T10:00:00+00:00"
    old.last_activity = "2026-08-02T10:00:00+00:00"
    registry.register(old)
    new = GameInstance(
        game_key=("web", "new", "bot"),
        rule_id="freeform_fantasy",
        world_name="New",
    )
    new.started_at = "2026-08-19T10:00:00+00:00"
    new.last_activity = "2026-08-20T10:00:00+00:00"
    registry.register(new)
    registry.register(GameInstance(
        game_key=("web", "undated", "bot"),
        rule_id="freeform_fantasy",
        world_name="Undated",
    ))

    result = list_games(_query_dependencies(registry))

    assert [game["game_key"] for game in result["games"]] == [
        "web|new|bot",
        "web|old|bot",
        "web|undated|bot",
    ]
    assert result["games"][0]["started_at"] == "2026-08-19T10:00:00+00:00"


def test_list_games_reports_each_saves_real_player_limit(tmp_path):
    registry = GameRegistry(tmp_path)
    instance = GameInstance(
        game_key=("web", "three-seat", "bot"),
        rule_id="freeform_fantasy",
        world_name="Three seats",
    )
    instance.max_players = 3
    registry.register(instance)

    result = list_games(_query_dependencies(registry))

    assert result["games"][0]["max_players"] == 3
