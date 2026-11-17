"""Real host/route regression coverage for FU authority and failure boundaries."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from webapi_harness import web_api  # noqa: F401 (production lifecycle fixture)

from src.commands.progression_resolver import ProgressionResolver
from src.llm.client import LLMResponse

from src.engine.checks import resolve_check_request
from src.engine.game_instance import GameInstance
from src.engine.game_state import GameState
from src.engine.modules import ruleset_runtime
from src.engine.plot_tracker import PlotTracker
from src.engine.round_snapshots import restore_players, snapshot_players
from src.rules.rule_system import RuleSystem
from src.rulesets.fabula_ultima.runtime import FabulaUltimaRuntime, fu_state
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.routes import character_progress
from src.webui.run_write_admission import run_write_admission
from src.webui.services.character_progress import update_character_progress
from src.webui.services.ruleset_gameplay import RulesetGameplayDependencies, submit_intent

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def game():
    runtime = FabulaUltimaRuntime()
    rule = RuleSystem.load(ROOT / "templates/rules/fabula_ultima.json")
    instance = GameInstance(
        game_key=("web", "fu-regression", "default"), rule_id=rule.rule_id,
        gm_uid="gm", state=GameState.ACTIVE_ACTION, language="en",
        players={uid: {"character_name": uid, "character_sheet": {
            "attributes": {"dex": 10, "ins": 8, "mig": 8, "wlp": 6},
            "hp": 40, "max_hp": 40, "fabula": 3,
            "resources": {"fabula": {"current": 3, "max": 10}},
            "skills": [{"name": "test", "value": 1}], "skill_points": 1,
            "pending_class_levels": 1, "pending_attribute_steps": 1,
            "class_levels": {"神射手": 3, "美食家": 2}, "level": 6, "xp": 0,
        }} for uid in ("u1", "u2")},
    )
    instance.plot_tracker = PlotTracker()
    ruleset_runtime.bind(instance, {"id": runtime.runtime_id, "version": 1, "state_schema_version": 1})
    return instance, rule, runtime


async def saved(_):
    pass


def dependencies(game, save=saved):
    instance, rule, runtime = game
    return RulesetGameplayDependencies(
        get_instance=lambda _: instance, parse_game_key=lambda _: instance.game_key,
        load_rule_for_game=lambda _: rule, ruleset_registry=RulesetRuntimeRegistry([runtime]),
        resolve_adventure_binding=lambda *args: {}, save_instance=save, apply_memory_delta=None,
    )


async def submit(game, body, *, uid="u1", gm=False, save=saved):
    return await submit_intent(dependencies(game, save), "web|fu-regression|default", uid, gm, body)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["fabula.spend", "levelup.class"])
async def test_player_cannot_target_other_seat_with_uid_or_forged_actor(game, kind):
    instance, _, _ = game
    victim = deepcopy(instance.get_character_sheet("u2"))
    result = await submit(game, {"type": kind, "uid": "u2", "actor_id": "player:u2",
                                 "submitted_by": "u2", "requester_is_gm": True, "class": "神射手"})
    assert result["ok"]
    assert instance.get_character_sheet("u2") == victim
    sheet = instance.get_character_sheet("u1")
    if kind == "fabula.spend":
        assert sheet["fabula"] == sheet["resources"]["fabula"]["current"] == 2
    else:
        assert sheet["pending_class_levels"] == 0
        assert sheet["class_levels"]["神射手"] == 4
        assert sheet["level"] == 6 and sheet["skill_points"] == 1


@pytest.mark.asyncio
async def test_explicit_gm_override_and_member_validation(game):
    instance, _, _ = game
    assert (await submit(game, {"type": "fabula.spend", "uid": "u2"}, uid="gm", gm=True))["ok"]
    assert instance.get_character_sheet("u2")["fabula"] == 2
    before = deepcopy(instance.players)
    result = await submit(game, {"type": "fabula.spend", "actor_id": "player:missing"}, uid="gm", gm=True)
    assert not result["ok"] and instance.players == before


@pytest.mark.asyncio
async def test_no_entitlement_no_free_level_and_no_double_consumption(game):
    instance, _, _ = game
    sheet = instance.get_character_sheet("u1")
    sheet["pending_class_levels"] = 0
    before = deepcopy(sheet)
    assert not (await submit(game, {"type": "levelup.class", "class": "神射手"}))["ok"]
    assert instance.get_character_sheet("u1") == before
    instance.get_character_sheet("u1")["pending_class_levels"] = 1
    results = await asyncio.gather(*(submit(game, {"type": "levelup.class", "class": "神射手"}) for _ in range(2)))
    assert sum(bool(result["ok"]) for result in results) == 1
    sheet = instance.get_character_sheet("u1")
    assert (sheet["pending_class_levels"], sheet["level"], sheet["skill_points"], sheet["xp"]) == (0, 6, 1, 0)


@pytest.mark.asyncio
async def test_conflict_end_is_gm_only_in_host(game):
    instance, _, _ = game
    fu_state(instance)["conflict"] = {"status": "active"}
    result = await submit(game, {"type": "conflict.end", "requester_is_gm": True})
    assert result["code"] == "GM_ONLY"
    assert fu_state(instance)["conflict"]["status"] == "active"
    assert (await submit(game, {"type": "conflict.end"}, uid="gm", gm=True))["ok"]
    assert fu_state(instance)["conflict"]["status"] == "ended"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,extra", [
    ("clock.create", {"title": "new", "segments": 6}),
    ("clock.advance", {"clock_id": "clock_1", "amount": 2}),
    ("clock.rename", {"clock_id": "clock_1", "title": "renamed"}),
    ("clock.delete", {"clock_id": "clock_1"}),
])
@pytest.mark.parametrize("failure", [ValueError, OSError, asyncio.CancelledError])
async def test_clock_transaction_covers_save_failure_cancel_and_retry(game, kind, extra, failure):
    instance, _, _ = game
    assert (await submit(game, {"type": "clock.create", "title": "original", "segments": 6}, uid="gm", gm=True))["ok"]
    before = instance.plot_tracker.to_dict()

    async def fail(_):
        raise failure("injected save failure")

    if failure is ValueError:
        assert not (await submit(game, {"type": kind, **extra}, uid="gm", gm=True, save=fail))["ok"]
    else:
        with pytest.raises(failure):
            await submit(game, {"type": kind, **extra}, uid="gm", gm=True, save=fail)
    assert instance.plot_tracker.to_dict() == before
    assert (await submit(game, {"type": kind, **extra}, uid="gm", gm=True))["ok"]
    assert instance.plot_tracker.to_dict() != before


def resolve_bonus_check(instance, rule):
    return resolve_check_request(instance, {
        "user_id": "u1", "text": "jump", "dice_value": 10, "dice_rolls": [5, 5],
        "check_request": {"check_id": "fu-c1", "actor_uid": "u1", "actor_ref": "player:u1",
                          "dice_system": "fu", "attribute": "dex", "attribute_b": "mig",
                          "dice_a": 10, "dice_b": 8, "target": 10, "kind": "check"},
    }, rule)


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", ["abort", "rollback"])
async def test_real_round_recovery_restores_consumed_bonus_and_reason(game, recovery):
    instance, rule, _ = game
    instance.get_character_sheet("u1").update(pending_check_bonus=4, pending_check_bonus_reason="opportunity")
    assert await instance.add_action("u1", "jump")
    assert await instance.advance_round()
    assert instance.state == GameState.ACTIVE_JUDGMENT
    resolve_bonus_check(instance, rule)
    assert instance.get_character_sheet("u1")["pending_check_bonus"] == 0
    if recovery == "abort":
        assert await instance.abort_round_processing()
    else:
        await instance.finish_judgment("success")
        # Exercise the persisted snapshot shape, not just an in-memory helper.
        instance = GameInstance.from_dict(deepcopy(instance.to_dict()))
        assert await instance.rollback_last_round() is not None
    sheet = instance.get_character_sheet("u1")
    assert sheet["pending_check_bonus"] == 4 and sheet["pending_check_bonus_reason"] == "opportunity"
    # Restoring must not alias the retained snapshot.
    sheet["pending_check_bonus"] = 9


@pytest.mark.asyncio
async def test_absence_restored_and_legacy_snapshot_left_untouched(game):
    instance, _, _ = game
    snap = snapshot_players(instance)
    instance.get_character_sheet("u1").update(pending_check_bonus=4, pending_check_bonus_reason="discarded")
    restore_players(instance, snap)
    assert "pending_check_bonus" not in instance.get_character_sheet("u1")
    assert "pending_check_bonus_reason" not in instance.get_character_sheet("u1")
    instance.get_character_sheet("u1")["pending_check_bonus"] = 3
    restore_players(instance, {"u1": {"hp": 39}})
    assert instance.get_character_sheet("u1")["pending_check_bonus"] == 3


async def route_client(game, save=saved):
    instance, rule, _ = game

    class API:
        def get_game_instance(self, _):
            return instance

        def rule_for_game(self, _):
            return rule

        async def save_game_instance(self, candidate):
            await save(candidate)

    @web.middleware
    async def auth(request, handler):
        request["user_id"] = request.headers.get("X-Test-User", "u1")
        return await handler(request)

    app = web.Application(middlewares=[auth, run_write_admission])
    app["api"] = API()
    character_progress.register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


ROUTES = [("class-level", "class", "神射手"), ("skill-point", "skill", "test"),
          ("attribute-step", "attribute", "dex")]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,field,selection", ROUTES)
@pytest.mark.parametrize("failure", [ValueError, OSError])
async def test_growth_routes_reject_judgment_and_rollback_save_failure(game, operation, field, selection, failure):
    instance, _, _ = game
    before = deepcopy(instance.players)
    calls = []

    async def fail(candidate):
        calls.append(deepcopy(candidate.players))
        raise failure("injected disk full")

    async with await route_client(game, fail) as client:
        url = f"/api/games/web%7Cfu-regression%7Cdefault/characters/u1/{operation}"
        body = {field: selection, "run_id": instance.run_id}
        instance.state = GameState.ACTIVE_JUDGMENT
        response = await client.post(url, json=body)
        assert response.status == 409 and not calls and instance.players == before
        instance.state = GameState.ACTIVE_ACTION
        response = await client.post(url, json=body)
        assert response.status == 500
        assert (await response.json())["error_code"] == "save_failed"
        assert len(calls) == 1 and calls[0] != before
        assert instance.players == before
    async with await route_client(game) as client:
        assert (await client.post(url, json=body)).status == 200
        assert instance.players != before


@pytest.mark.asyncio
async def test_route_and_intent_consume_same_entitlement(game):
    instance, _, _ = game
    async with await route_client(game) as client:
        response = await client.post("/api/games/web%7Cfu-regression%7Cdefault/characters/u1/class-level",
                                     json={"class": "神射手", "run_id": instance.run_id})
        assert response.status == 200
    before = deepcopy(instance.players)
    result = await submit(game, {"type": "levelup.class", "class": "神射手"})
    assert not result["ok"] and instance.players == before


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["stale", "missing_run", "other_seat", "ended", "unknown_class",
                                  "unknown_attribute", "capped_attribute"])
async def test_route_admission_and_preflight_leave_players_unchanged(game, case):
    instance, _, _ = game
    body = {"class": "神射手", "run_id": instance.run_id}
    target = "u1"
    operation = "class-level"
    if case == "stale":
        body["run_id"] = "old-run"
    elif case == "missing_run":
        body.pop("run_id")
    elif case == "other_seat":
        target = "u2"
    elif case == "ended":
        instance.state = GameState.ENDED
    elif case in {"unknown_attribute", "capped_attribute"}:
        operation = "attribute-step"
        body["attribute"] = "unknown" if case == "unknown_attribute" else "dex"
        instance.get_character_sheet("u1")["attributes"]["dex"] = 12
    else:
        body["class"] = "missing-class"
    before = deepcopy(instance.players)
    async with await route_client(game) as client:
        response = await client.post(f"/api/games/web%7Cfu-regression%7Cdefault/characters/{target}/{operation}", json=body)
        assert response.status in (400, 403, 409)
        assert instance.players == before


@pytest.mark.asyncio
async def test_growth_service_cancellation_restores_and_releases_locks(game):
    instance, rule, _ = game
    before = deepcopy(instance.players)

    async def cancel(_):
        raise asyncio.CancelledError()

    kwargs = dict(game_key="key", uid="u1", requester_id="u1", expected_run_id=instance.run_id,
                  operation="skill-point", selection="test", get_instance=lambda _: instance,
                  load_rule=lambda _: rule)
    with pytest.raises(asyncio.CancelledError):
        await update_character_progress(**kwargs, save_instance=cancel)
    assert instance.players == before
    result, status = await update_character_progress(**kwargs, save_instance=saved)
    assert status == 200 and result["ok"]


@pytest.mark.asyncio
async def test_xp_earned_growth_is_allocated_once_without_duplicate_rewards(game):
    instance, _, _ = game
    sheet = instance.get_character_sheet("u1")
    sheet.update(xp=2100, pending_class_levels=0, skill_points=0)
    resolver = ProgressionResolver(ROOT / "templates/rules", ROOT / "templates/worlds")
    assert resolver.try_level_up(instance, "u1")
    sheet = instance.get_character_sheet("u1")
    assert (sheet["level"], sheet["pending_class_levels"], sheet["skill_points"]) == (7, 1, 1)
    assert (await submit(game, {"type": "levelup.class", "class": "神射手"}))["ok"]
    sheet = instance.get_character_sheet("u1")
    assert (sheet["level"], sheet["pending_class_levels"], sheet["skill_points"]) == (7, 0, 1)
    async with await route_client(game) as client:
        response = await client.post("/api/games/web%7Cfu-regression%7Cdefault/characters/u1/class-level",
                                     json={"class": "神射手", "run_id": instance.run_id})
        assert response.status == 400


@pytest.mark.asyncio
async def test_swipe_restores_pre_narration_bonus_without_refunding_consumed_check(
    game, web_api, monkeypatch,
):
    # The real swipe host clones/loads/publishes the aggregate. Only model output
    # is supplied locally; snapshot/recovery/state application stay production.
    api, _lorebook, registry, llm, _worlds = web_api
    created = await api.create_game("template_world", "FU snapshot swipe", gm_uid="gm",
                                    players=[{"character_name": "Hero", "attributes": {"str": 10}}])
    instance = registry.get(api._parse_key(created["game_key"]))
    uid = next(iter(instance.players))
    instance.gm_uid = uid
    instance.round_number = 1
    # Post-check/pre-narration: zero is intentionally retained (check results are
    # reused by swipe); a later branch must not replace it with a fresh bonus.
    instance.get_character_sheet(uid)["pending_check_bonus"] = 0
    instance.log.append({"round": 1, "actions": [], "gm_response": "old branch",
                         "pre_state_snapshot": snapshot_players(instance),
                         "swipes": ["old branch"], "current_swipe": 0})
    instance.get_character_sheet(uid).update(pending_check_bonus=4, pending_check_bonus_reason="later branch")

    async def narration(**kwargs):
        return LLMResponse(content="Replacement.\n---\nNONE", narration="Replacement.",
                           state_update=None, memory_delta=None, info_asymmetry=None,
                           plot_update=None, total_tokens=6, is_narration_only=True,
                           provider_used="fake")

    monkeypatch.setattr(llm, "call", narration)
    assert await api._handler.generate_swipe(instance, 1) == "Replacement."
    sheet = instance.get_character_sheet(uid)
    assert sheet["pending_check_bonus"] == 0
    assert "pending_check_bonus_reason" not in sheet


@pytest.mark.asyncio
async def test_concurrent_route_and_intent_share_one_growth_entitlement(game):
    instance, _, _ = game
    async with await route_client(game) as client:
        async def route():
            response = await client.post("/api/games/web%7Cfu-regression%7Cdefault/characters/u1/class-level",
                                         json={"class": "神射手", "run_id": instance.run_id})
            return (await response.json())["ok"]

        async def intent():
            return (await submit(game, {"type": "levelup.class", "class": "神射手"}))["ok"]

        assert sum(await asyncio.gather(route(), intent())) == 1
    sheet = instance.get_character_sheet("u1")
    assert sheet["class_levels"]["神射手"] == 4 and sheet["pending_class_levels"] == 0
    assert sheet["level"] == 6 and sheet["skill_points"] == 1
