"""module_hooks registry and the world_reports capability riding on it (#515)."""

from collections.abc import Iterator

import pytest

from src.engine import module_hooks
from src.engine.game_instance import GameInstance
from src.engine.health import health_payload, mark_health_event, record_health_event
from src.engine.modules import (
    check_reveals, combat_extension_state, health, legacy_combat, media, lorebook_runtime, player_control_state, private_channels, round_presentation,
    seat_activity, world_reports,
)
from src.engine.player_control import away_control_policy, set_away_control_policy


@pytest.fixture
def detached_world_reports() -> Iterator[None]:
    removed = module_hooks.unregister_owner(world_reports.MODULE_NAME)
    try:
        yield
    finally:
        for point, handler in removed.items():
            module_hooks.register(point, world_reports.MODULE_NAME, handler)


@pytest.fixture
def scratch_point() -> Iterator[str]:
    point = module_hooks.STATE_READ
    saved = list(module_hooks._HANDLERS[point])
    module_hooks._HANDLERS[point].clear()
    try:
        yield point
    finally:
        module_hooks._HANDLERS[point][:] = saved


def test_unknown_point_and_bad_handlers_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown module hook point"):
        module_hooks.register("round.resett", "x", lambda instance: None)
    with pytest.raises(ValueError, match="owner"):
        module_hooks.register(module_hooks.ROUND_RESET, "", lambda instance: None)
    with pytest.raises(TypeError):
        module_hooks.register(module_hooks.ROUND_RESET, "x", "not callable")


def test_ask_returns_first_answer_or_default_and_reregister_replaces(scratch_point) -> None:
    assert module_hooks.ask(scratch_point, None, "k", default=["d"]) == ["d"]
    module_hooks.register(scratch_point, "a", lambda instance, kind: None)
    module_hooks.register(scratch_point, "b", lambda instance, kind: ["b1"])
    module_hooks.register(scratch_point, "c", lambda instance, kind: ["c"])
    assert module_hooks.ask(scratch_point, None, "k", default=[]) == ["b1"]
    module_hooks.register(scratch_point, "b", lambda instance, kind: ["b2"])
    assert module_hooks.owners(scratch_point) == ("a", "c", "b")
    assert module_hooks.ask(scratch_point, None, "k", default=[]) == ["c"]


def test_world_reports_registers_its_hooks() -> None:
    for point in (module_hooks.ROUND_RESET, module_hooks.STATE_RECORD, module_hooks.STATE_READ):
        assert world_reports.MODULE_NAME in module_hooks.owners(point)


def test_reports_round_trip_through_hooks_and_reset_clears_them() -> None:
    instance = GameInstance(game_key=("web", "hooks-reports", "u"))
    module_hooks.record(instance, module_hooks.OVERREACH, [{"player": "p1"}])
    module_hooks.record(instance, module_hooks.WORLD_EVENTS, [{"event_id": "e"}])
    assert world_reports.last_overreach(instance) == [{"player": "p1"}]
    assert module_hooks.read(instance, module_hooks.WORLD_EVENTS) == [{"event_id": "e"}]
    assert module_hooks.read(instance, "unknown-kind") == []

    instance.reset_round_checks()

    assert module_hooks.read(instance, module_hooks.OVERREACH) == []
    assert world_reports.last_world_events(instance) == []


def test_without_world_reports_the_round_keeps_working_without_reports(detached_world_reports) -> None:
    instance = GameInstance(game_key=("web", "hooks-detached", "u"))
    world_reports.replace_last_overreach(instance, [{"player": "stale"}])

    module_hooks.record(instance, module_hooks.OVERREACH, [{"player": "p1"}])
    instance.reset_round_checks(prepared=True)

    assert module_hooks.read(instance, module_hooks.OVERREACH) == []
    assert world_reports.last_overreach(instance) == [{"player": "stale"}]


@pytest.fixture
def detached_round_presentation() -> Iterator[None]:
    removed = module_hooks.unregister_owner(round_presentation.MODULE_NAME)
    try:
        yield
    finally:
        for point, handler in removed.items():
            module_hooks.register(point, round_presentation.MODULE_NAME, handler)


def test_round_presentation_round_trips_through_game_instance() -> None:
    instance = GameInstance(game_key=("web", "hooks-presentation", "u"))
    instance.set_quick_actions(["look", " ", "run"])
    instance.add_gm_directive({"id": "d1"})
    instance.add_gm_directive({"id": "d2"})
    instance.record_combat_result({"hit": True})
    instance.set_state_update_recap({"hp": 1})
    instance.set_token_budget_bump(100, 200)

    assert round_presentation.quick_actions(instance) == ["look", "run"]
    assert module_hooks.read_value(instance, module_hooks.STATE_UPDATE) == {"hp": 1}
    instance.consume_gm_directives({"d1"})
    assert round_presentation.gm_directives(instance) == [{"id": "d2"}]

    instance.begin_round_processing()
    assert round_presentation.pending_combat_results(instance) == []
    assert round_presentation.last_token_budget_bump(instance) is None
    assert round_presentation.quick_actions(instance) == ["look", "run"]


def test_without_round_presentation_the_extras_are_dropped(detached_round_presentation) -> None:
    instance = GameInstance(game_key=("web", "hooks-presentation-off", "u"))
    instance.set_quick_actions(["look"])
    instance.add_gm_directive({"id": "d1"})
    instance.record_combat_result({"hit": True})
    instance.set_state_update_recap({"hp": 1})
    instance.consume_gm_directives({"d1"})
    instance.begin_round_processing()

    assert module_hooks.read(instance, module_hooks.QUICK_ACTIONS) == []
    assert module_hooks.read(instance, module_hooks.GM_DIRECTIVES) == []
    assert module_hooks.read_value(instance, module_hooks.STATE_UPDATE) is None
    assert round_presentation.quick_actions(instance) == []


def test_lorebook_timers_tick_at_round_begin_and_clear_on_reset() -> None:
    instance = GameInstance(game_key=("web", "hooks-lorebook", "u"))
    # Pre-v2 timer shape, which the tick still supports.
    lorebook_runtime.timers(instance).update({"e1": {"remaining": 2}, "e2": {"remaining": 1}})

    instance.begin_round_processing()

    assert lorebook_runtime.timers(instance) == {"e1": {"remaining": 1}}
    assert module_hooks.read_value(instance, module_hooks.LOREBOOK_TIMERS) is lorebook_runtime.timers(instance)
    module_hooks.run(module_hooks.INSTANCE_RESET, instance)
    assert lorebook_runtime.timers(instance) == {}


def test_without_lorebook_runtime_entries_have_no_timers() -> None:
    removed = module_hooks.unregister_owner(lorebook_runtime.MODULE_NAME)
    try:
        instance = GameInstance(game_key=("web", "hooks-lorebook-off", "u"))
        lorebook_runtime.timers(instance).update({"e1": {"remaining": 2}})
        instance.begin_round_processing()
        module_hooks.run(module_hooks.INSTANCE_RESET_PREFLIGHT, instance)

        assert module_hooks.read_value(instance, module_hooks.LOREBOOK_TIMERS) is None
        assert lorebook_runtime.timers(instance) == {"e1": {"remaining": 2}}
    finally:
        for point, handler in removed.items():
            module_hooks.register(point, lorebook_runtime.MODULE_NAME, handler)


def test_health_events_round_trip_and_reset() -> None:
    instance = GameInstance(game_key=("web", "hooks-health", "u"))
    event = record_health_event(instance, "llm", "TIMEOUT", "error", "LLM timed out")
    assert health.health_events(instance) == [event]
    assert health.health_status(instance)["llm"] == "error"
    assert mark_health_event(instance, event["id"], resolved=True)
    assert health_payload(instance)["events"] == []

    module_hooks.run(module_hooks.INSTANCE_RESET, instance)
    assert health.health_events(instance) == [] and health.health_status(instance) == {}


def test_without_health_events_are_not_kept() -> None:
    removed = module_hooks.unregister_owner(health.MODULE_NAME)
    try:
        instance = GameInstance(game_key=("web", "hooks-health-off", "u"))
        event = record_health_event(instance, "llm", "TIMEOUT", "error", "LLM timed out")
        assert event["code"] == "TIMEOUT"
        assert not mark_health_event(instance, event["id"], resolved=True)
        payload = health_payload(instance)
        # The payload also carries unrelated sections (narrative quality
        # metrics); the contract here is that no health event is kept.
        assert {key: payload[key] for key in ("ok", "status", "events", "total")} == {
            "ok": True, "status": {}, "events": [], "total": 0,
        }
        assert health.health_events(instance) == []
    finally:
        for point, handler in removed.items():
            module_hooks.register(point, health.MODULE_NAME, handler)


def _detached(module) -> dict:
    return module_hooks.unregister_owner(module.MODULE_NAME)


def _reattach(module, removed: dict) -> None:
    for point, handler in removed.items():
        module_hooks.register(point, module.MODULE_NAME, handler)


def test_check_reveals_are_read_through_hooks() -> None:
    instance = GameInstance(game_key=("web", "hooks-reveals", "u"))
    check_reveals.mark_revealed(instance, "c1", "p1")
    assert set(module_hooks.read_value(instance, module_hooks.CHECK_REVEALS) or {}) == {"c1"}
    removed = _detached(check_reveals)
    try:
        assert module_hooks.read_value(instance, module_hooks.CHECK_REVEALS) is None
    finally:
        _reattach(check_reveals, removed)


def test_away_control_policy_round_trips_and_defaults_without_module() -> None:
    instance = GameInstance(game_key=("web", "hooks-away", "u"))
    other = next(p for p in ("ai_takeover", "pause") if p != away_control_policy(instance))
    assert set_away_control_policy(instance, other) == other
    assert player_control_state.away_control_policy(instance) == other
    removed = _detached(player_control_state)
    try:
        assert away_control_policy(instance) == "pause"
        set_away_control_policy(instance, "pause")
        assert player_control_state.away_control_policy(instance) == other
    finally:
        _reattach(player_control_state, removed)


def test_private_channels_through_hooks_and_absent_module() -> None:
    instance = GameInstance(game_key=("web", "hooks-private", "u"))
    instance.append_private_message("p1", {"text": "hi"})
    instance.append_table_talk({"id": "t1", "visibility": "party"})
    assert private_channels.private_log(instance) == {"p1": [{"text": "hi"}]}
    instance.clear_private_messages("p1")
    assert private_channels.private_log(instance) == {}
    module_hooks.run(module_hooks.INSTANCE_RESET, instance)
    assert private_channels.table_talk(instance) == []

    removed = _detached(private_channels)
    try:
        instance.append_private_message("p1", {"text": "lost"})
        instance.append_table_talk({"id": "t2", "visibility": "party"})
        instance.clear_private_messages("p1")
        assert private_channels.private_log(instance) == {}
        assert private_channels.table_talk(instance) == []
        assert module_hooks.read(instance, module_hooks.TABLE_TALK) == []
    finally:
        _reattach(private_channels, removed)


def _seated(key: str) -> GameInstance:
    instance = GameInstance(game_key=("web", key, "u"))
    instance.players = {"p1": {"user_id": "p1", "character_name": "Avery", "character_sheet": {}}}
    return instance


def test_seat_hooks_mark_query_and_forget() -> None:
    instance = _seated("hooks-seat")
    assert not module_hooks.seat_has_acted(instance, "p1")
    module_hooks.run(module_hooks.SEAT_ACTED, instance, "p1", module_hooks.SEAT_SOURCE_LUCK)
    module_hooks.run(module_hooks.SEAT_ACTED, instance, "stranger", module_hooks.SEAT_SOURCE_LUCK)
    assert module_hooks.seat_has_acted(instance, "p1")
    assert seat_activity.has_acted(instance, "p1") and not seat_activity.has_acted(instance, "stranger")
    module_hooks.run(module_hooks.SEAT_FORGOTTEN, instance, "p1")
    assert not module_hooks.seat_has_acted(instance, "p1")


def test_unreadable_seat_slot_still_fails_closed() -> None:
    instance = _seated("hooks-seat-closed")
    instance.modules[seat_activity.MODULE_NAME] = {"schema_version": 999}
    assert module_hooks.seat_has_acted(instance, "p1")


def test_without_seat_activity_no_seat_is_locked() -> None:
    removed = _detached(seat_activity)
    try:
        instance = _seated("hooks-seat-off")
        module_hooks.run(module_hooks.SEAT_PREFLIGHT, instance)
        module_hooks.run(module_hooks.SEAT_ACTED, instance, "p1", module_hooks.SEAT_SOURCE_INTENT)
        assert not module_hooks.seat_has_acted(instance, "p1")
        assert seat_activity.has_acted(instance, "p1") is False
    finally:
        _reattach(seat_activity, removed)


def test_combat_view_reads_through_hooks_and_is_neutral_without_module() -> None:
    from tests.test_legacy_combat_module import bind_dnd_combat

    instance = bind_dnd_combat(
        GameInstance(game_key=("web", "hooks-combat-view", "u")),
        {"status": "active", "initiative": ["player:p1", "npc:goblin"], "turn_index": 1},
    )
    view = module_hooks.combat_view(instance)
    assert view == legacy_combat.view(instance)
    assert view["combat_state"] == "active" and view["combat_active"] is True
    view["initiative_order"].append("tampered")
    assert "tampered" not in module_hooks.combat_view(instance)["initiative_order"]

    removed = module_hooks.unregister_owner(legacy_combat.HOOK_OWNER)
    try:
        assert module_hooks.combat_view(instance) == module_hooks.neutral_combat_view()
    finally:
        for point, handler in removed.items():
            module_hooks.register(point, legacy_combat.HOOK_OWNER, handler)


def test_run_any_runs_every_handler(scratch_point) -> None:
    calls: list[str] = []
    module_hooks.register(scratch_point, "a", lambda *args: calls.append("a") or False)
    module_hooks.register(scratch_point, "b", lambda *args: calls.append("b") or True)
    module_hooks.register(scratch_point, "c", lambda *args: calls.append("c") or False)
    assert module_hooks.run_any(scratch_point, None, "k") is True
    assert calls == ["a", "b", "c"]


def test_combat_extension_commit_and_abort_through_hooks() -> None:
    from src.engine.combat_narrative import pending_events

    instance = GameInstance(game_key=("web", "hooks-combat-ext", "u"))
    combat_extension_state.replace_current(instance, {"schema_version": 1, "pending_summaries": ["hit"]})
    combat_extension_state.capture_combat_extension_snapshot(instance)
    entry: dict = {"state_changes": ["moved"]}
    module_hooks.run(module_hooks.ROUND_COMMIT, instance, entry, None)
    assert entry["state_changes"] == ["moved", "hit"]
    assert {"combat_extension_round_start", "pre_combat_extension_snapshot"} <= set(entry)
    assert combat_extension_state.round_snapshots(instance) == {}
    assert pending_events(instance) == []

    combat_extension_state.capture_combat_extension_snapshot(instance)
    combat_extension_state.replace_current(instance, {"schema_version": 1, "changed": True})
    assert module_hooks.run_any(module_hooks.ROUND_ABORT, instance) is True
    assert "changed" not in combat_extension_state.current(instance)


def test_without_combat_extension_rounds_carry_no_combat_state() -> None:
    from src.engine.combat_narrative import pending_events

    removed = _detached(combat_extension_state)
    try:
        instance = GameInstance(game_key=("web", "hooks-combat-ext-off", "u"))
        combat_extension_state.replace_current(instance, {"schema_version": 1, "pending_summaries": ["hit"]})
        entry: dict = {"state_changes": []}
        module_hooks.run(module_hooks.ROUND_COMMIT, instance, entry, None)
        assert entry == {"state_changes": []}
        assert module_hooks.run_any(module_hooks.ROUND_ABORT, instance) is False
        assert module_hooks.read_value(instance, module_hooks.COMBAT_EXTENSION_SNAPSHOT) is None
        assert pending_events(instance) == []
    finally:
        _reattach(combat_extension_state, removed)


def test_media_references_through_hooks_and_absent_module() -> None:
    instance = GameInstance(game_key=("web", "hooks-media", "u"))
    instance.set_scene_image({"kind": "generated", "asset_id": "a1"})
    instance.set_map_background({"kind": "upload", "asset_id": "m1"})
    assert media.scene_image(instance) == {"kind": "generated", "asset_id": "a1"}
    assert module_hooks.read_value(instance, module_hooks.MAP_BACKGROUND) == {"kind": "upload", "asset_id": "m1"}

    removed = _detached(media)
    try:
        instance.set_scene_image({"kind": "generated", "asset_id": "a2"})
        assert module_hooks.read_value(instance, module_hooks.SCENE_IMAGE) is None
        assert media.scene_image(instance) == {"kind": "generated", "asset_id": "a1"}
    finally:
        _reattach(media, removed)
