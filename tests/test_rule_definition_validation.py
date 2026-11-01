from __future__ import annotations

from copy import deepcopy

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from webapi_harness import web_api  # noqa: F401
from src.webui.routes.rules import register_rules


@pytest.mark.parametrize(("stats", "path"), [
    ({"mp": 20}, "special_stats"),
    ([{"name": "魔力", "max": 20}], "special_stats[0].key"),
    ([{"key": "mp", "max": "bad"}], "special_stats[0].max"),
    ([{"key": "mp", "max": 20, "initial": 25}], "special_stats[0].initial"),
    ([{"key": "mp", "max": 20}, {"key": "mp", "max": 30}], "special_stats[1].key"),
    ([{"key": "hp", "max": 20}], "special_stats[0].key"),
])
def test_invalid_rule_resources_identify_the_field_and_do_not_replace_saved_rule(web_api, stats, path):
    api, _, _, _, _ = web_api
    assert api.save_custom_rule({"source_rule_id": "freeform_fantasy", "rule_id": "resource_test", "rule_name": "Test"})["ok"]
    original = api.get_rule_template("resource_test")["rule"]
    candidate = {**deepcopy(original), "special_stats": stats}
    result = api.update_custom_rule("resource_test", candidate)
    assert result["ok"] is False
    assert path in result["error"]
    assert api.get_rule_template("resource_test")["rule"] == original


@pytest.mark.asyncio
async def test_rule_route_returns_failure_status_instead_of_refresh_success(web_api):
    api, _, _, _, _ = web_api
    app = web.Application()
    app["api"] = api
    register_rules(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.put('/api/rules/freeform_fantasy', json={"rule_name": "Cannot edit built-in"}, headers={"X-TRPG-Confirm": "true"})
        assert response.status == 422
        assert (await response.json())["ok"] is False


@pytest.mark.asyncio
async def test_valid_mana_and_stamina_declarations_are_loaded_and_initialize_new_characters(web_api):
    from src.engine.character_utils import initial_special_stat_value
    api, _, registry, _, _ = web_api
    assert api.save_custom_rule({"source_rule_id": "freeform_fantasy", "rule_id": "resource_test", "rule_name": "Test"})["ok"]
    original = api.get_rule_template("resource_test")["rule"]
    stats = [{"key": "mp", "name": "魔力", "max": 20, "initial": 15}, {"key": "stamina", "name": "体力", "max": 30}]
    assert api.update_custom_rule("resource_test", {**original, "special_stats": stats})["ok"]
    saved = api.get_rule_template("resource_test")["rule"]["special_stats"]
    assert [initial_special_stat_value(stat, {}) for stat in saved] == [15, 30]
    result = await api.create_game('template_world', 'Resources', rule_id='resource_test', players=[{'character_name': 'Tester', 'attributes': {'str': 12}}])
    assert result['ok'], result
    instance = registry.get(tuple(result['game_key'].split('|')))
    sheet = next(iter(instance.players.values()))['character_sheet']
    assert (sheet['mp'], sheet['max_mp']) == (15, 20)
    assert (sheet['stamina'], sheet['max_stamina']) == (30, 30)


@pytest.mark.parametrize(("block", "value"), [
    ("affection", {"max_delta_per_round": 30}),
    ("affection", {"max_delta_per_round": 5, "unknown": 1}),
    ("adventure_progression", {"auto_confirm_completion": "yes"}),
])
def test_invalid_gameplay_blocks_are_rejected_at_save_and_rules_stay_listable(web_api, block, value):
    api, _, _, _, _ = web_api
    assert api.save_custom_rule({"source_rule_id": "freeform_fantasy", "rule_id": "my_gal", "rule_name": "Mine"})["ok"]
    original = api.get_rule_template("my_gal")["rule"]

    result = api.update_custom_rule("my_gal", {**deepcopy(original), block: value})

    assert result["ok"] is False
    assert block in result["error"]
    assert api.get_rule_template("my_gal")["rule"] == original
    assert any(rule.get("rule_id") == "my_gal" for rule in api.list_rules()["rules"])
