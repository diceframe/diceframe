from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from src.engine.game_instance import GameInstance, GameRegistry
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services.game_queries import (
    GameQueryDependencies,
    game_detail,
    list_games,
    player_context,
)


def _query_dependencies(
    registry: GameRegistry,
    *,
    load_world_template=None,
    load_rule_for_game=lambda _instance: None,
    ruleset_registry: RulesetRuntimeRegistry | None = None,
) -> GameQueryDependencies:
    return GameQueryDependencies(
        list_instances=registry.list_all,
        get_instance=registry.get,
        parse_game_key=lambda game_key: tuple(game_key.split("|")),
        load_world_template=load_world_template,
        load_rule_for_game=load_rule_for_game,
        ruleset_registry=ruleset_registry or RulesetRuntimeRegistry(),
    )


def test_list_projects_legacy_rule_without_mutating_instance(tmp_path: Path) -> None:
    registry = GameRegistry(tmp_path)
    instance = GameInstance(
        game_key=("web", "legacy", "bot"),
        world_id="legacy-world",
        rule_id="",
    )
    registry.register(instance)
    dependencies = _query_dependencies(
        registry,
        load_world_template=lambda _world_id: {
            "default_rule": "freeform_fantasy",
        },
    )

    result = list_games(dependencies)

    assert result["games"][0]["rule_id"] == "freeform_fantasy"
    assert instance.rule_id == ""


def test_dnd_detail_projection_is_runtime_owned_and_read_only(
    tmp_path: Path,
) -> None:
    registry = GameRegistry(tmp_path)
    instance = GameInstance(
        game_key=("web", "dnd", "bot"),
        world_id="dnd-world",
        rule_id="dnd2024_srd",
    )
    instance.players["hero"] = {
        "character_name": "Hero",
        "character_sheet": {
            "ruleset_character": {
                "build": {"level": 1, "class_levels": [{"level": 1}]},
            },
        },
    }
    instance.ruleset_runtime = {
        "id": "core:dnd2024",
        "version": 1,
        "requested_minimum_version": 1,
    }
    instance.ruleset_state = {"legacy": {"kept": True}}
    registry.register(instance)
    rule = SimpleNamespace(template={
        "runtime": {"id": "core:dnd2024", "minimum_version": 1},
    })
    dependencies = _query_dependencies(
        registry,
        load_rule_for_game=lambda _instance: rule,
        ruleset_registry=RulesetRuntimeRegistry([Dnd2024Runtime()]),
    )
    before = deepcopy(instance.ruleset_state)

    detail = game_detail(dependencies, "web|dnd|bot")

    assert detail is not None
    assert detail["ruleset_runtime"]["id"] == "core:dnd2024"
    assert detail["advancement"] == {
        "mode": "milestone",
        "authority": "ai_gm",
        "players": [{
            "user_id": "hero",
            "character_name": "Hero",
            "level": 1,
            "xp": 0,
            "next_level_xp": 300,
            "entitled": False,
            "target_level": 0,
            "source": "",
        }],
    }
    assert instance.ruleset_state == before


def test_generic_game_queries_do_not_import_dnd() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "webui"
        / "services"
        / "game_queries.py"
    ).read_text(encoding="utf-8")

    assert "src.rulesets.dnd2024" not in source


def test_game_detail_projects_manual_roll_results_with_private_visibility(tmp_path: Path) -> None:
    registry = GameRegistry(tmp_path)
    instance = GameInstance(
        game_key=("web", "rolls", "bot"), gm_uid="gm",
    )
    instance.round_number = 3
    instance.manual_roll_requests = [
        {
            "id": "party-roll", "round_number": 2, "label": "察觉鉴定",
            "formula": "d20", "visibility": "party", "target_uids": ["ally"],
            "target_names": {"ally": "Mira"}, "status": "resolved", "created_by": "gm",
            "created_at": "2026-09-13T01:00:00+00:00",
            "results": {"ally": {"total": 15, "rolls": [15], "modifier": 0, "natural": 15}},
        },
        {
            "id": "private-roll", "round_number": 3, "label": "秘密鉴定",
            "formula": "d20+2", "visibility": "private", "target_uids": ["ally"],
            "target_names": {"ally": "Mira"}, "status": "resolved", "created_by": "gm",
            "created_at": "2026-09-13T01:01:00+00:00",
            "results": {"ally": {"total": 17, "rolls": [15], "modifier": 2, "natural": 15}},
        },
    ]
    registry.register(instance)
    dependencies = _query_dependencies(registry)

    party_view = game_detail(dependencies, "web|rolls|bot", viewer_uid="ally")
    assert party_view is not None
    assert [item["id"] for item in party_view["manual_rolls"]] == ["party-roll", "private-roll"]
    outsider_view = game_detail(dependencies, "web|rolls|bot", viewer_uid="outsider")
    assert outsider_view is not None
    assert [item["id"] for item in outsider_view["manual_rolls"]] == ["party-roll"]


def test_player_context_projects_server_owned_identity_flags() -> None:
    assert player_context(
        preview=True, delegate=False, user_id="player-1",
    ) == {
        "ok": True,
        "preview": True,
        "delegate": False,
        "user_id": "player-1",
    }
