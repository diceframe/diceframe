"""Reset/restart keep the game's identity and rebuild its run state.

Regression coverage for:

- restart/reset of an Adventure v2 game silently falling back to the v1 path
  (play_mode, content_binding refs and v2 progress were not carried/rebuilt);
- a failing v2 initialization on restart must leave the previous run current;
- the opening ``plot_update`` of a new game being dropped (no tracker yet);
- ``switch_world`` renaming the game, diverging World identities and
  bypassing the authoritative write gate.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.modules import content_binding, table_settings
from src.webui.services import adventure_runtime
from src.engine.world_state import world_facts
from src.llm.client import LLMResponse
from test_golden_e2e_54pr import (  # noqa: F401  (fixture re-export)
    ADVENTURE_ID,
    _complete_node,
    _dnd_characters,
    _created_golden,
    golden,
)
from test_webui_create_flow import _write_world
from webapi_harness import web_api  # noqa: F401


# ---- 1: restart/reset keep identity and re-initialize Adventure v2 ---------


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["restart_game", "reset_game"])
async def test_new_run_keeps_identity_and_reinitializes_v2_progress(golden, transition) -> None:
    created = await _created_golden(golden)
    game_key = created["game_key"]
    before = golden.instance(game_key)
    assert before.play_mode == "adventure"
    world_ref = content_binding.world_ref(before)
    book_refs = content_binding.book_refs(before)
    assert world_ref
    await _complete_node(golden, created, "gate")
    assert before.adventure_progress["completed_nodes"] == ["gate"]

    await getattr(golden.api, transition)(game_key)
    after = golden.instance(game_key)

    assert after is not before
    assert after.run_id != before.run_id
    assert after.play_mode == "adventure"
    assert after.adventure_binding["adventure_id"] == ADVENTURE_ID
    assert content_binding.world_ref(after) == world_ref
    assert content_binding.book_refs(after) == book_refs
    # Fresh v2 progress for the new run, plus the atomically materialized seed.
    assert after.adventure_progress["active_nodes"] == ["gate"]
    assert after.adventure_progress.get("completed_nodes", []) == []
    assert world_facts(after.world_state)["location:cellar.door"]["value"] == "locked"
    # Survives a save/load round trip.
    payload = after.to_dict()
    # Shape-aware: both values live in modules["adventure_runtime"] since R9-a.
    runtime = (payload.get("modules") or {}).get("adventure_runtime") or {}
    assert runtime.get("play_mode", payload.get("play_mode")) == "adventure"
    assert runtime.get("progress", payload.get("adventure_progress"))["active_nodes"] == ["gate"]


@pytest.mark.asyncio
async def test_failed_v2_initialization_keeps_previous_run_current(golden, monkeypatch) -> None:
    created = await _created_golden(golden)
    game_key = created["game_key"]
    before = golden.instance(game_key)
    await _complete_node(golden, created, "gate")
    progress = dict(before.adventure_progress)
    run_id = before.run_id

    def failing(_instance):
        raise RuntimeError("seed failure")

    monkeypatch.setattr(golden.api._handler._lifecycle, "_initialize_adventure_run", failing)
    result = await golden.api.restart_game(game_key)

    assert result["ok"] is False
    assert result["error_code"] == "ADVENTURE_RUNTIME_INIT_FAILED"
    current = golden.instance(game_key)
    assert current is before
    assert current.run_id == run_id
    assert current.adventure_progress == progress


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["restart_game", "reset_game"])
async def test_unresolvable_v2_adventure_rejects_new_run_with_error_code(
    golden, monkeypatch, transition,
) -> None:
    created = await _created_golden(golden)
    game_key = created["game_key"]
    before = golden.instance(game_key)
    run_id = before.run_id
    progress = dict(before.adventure_progress)

    def missing_source(*_args, **_kwargs):
        raise ValueError("adventure source is not installed")

    monkeypatch.setattr(golden.api._adventure_resolver, "resolve_binding", missing_source)
    result = await getattr(golden.api, transition)(game_key)

    assert result["ok"] is False
    assert result["error_code"] == "ADVENTURE_RUNTIME_INIT_FAILED"
    current = golden.instance(game_key)
    assert current is before
    assert current.run_id == run_id
    assert current.adventure_progress == progress


@pytest.mark.asyncio
async def test_v1_adventure_restart_still_works(web_api) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    (api._rules_dir / "dnd2024_srd.json").write_text(json.dumps({
        "rule_id": "dnd2024_srd", "rule_name": "5E 2024 SRD", "dice_system": "d20",
        "runtime": {"id": "core:dnd2024", "minimum_version": 1},
        "attributes": [
            {"key": key, "name": key.upper(), "min": 3, "max": 20}
            for key in ("str", "dex", "con", "int", "wis", "cha")
        ],
    }, ensure_ascii=False), encoding="utf-8")
    _write_world(worlds_dir, "v1_adventure_world", default_rule="dnd2024_srd")
    preset = api.ruleset_builder_choices(
        "dnd2024_srd", {"locale": "zh-CN"}, "zh-CN",
    )["choices"]["quick_presets"][0]
    character = api.ruleset_builder_finalize(
        "dnd2024_srd", {**preset["draft"], "locale": "zh-CN", "name": "灰沼重开者"}, "zh-CN",
    )["character"]
    created = await api.create_game(
        "v1_adventure_world", "灰沼重开", rule_id="dnd2024_srd",
        adventure_id="core:lanterns_of_greymoor", players=[character],
    )
    assert created["ok"] is True, created
    before = registry.get(api._parse_key(created["game_key"]))
    assert before.adventure_binding["format"] != "diceframe:adventure-graph-v2"

    result = await api.restart_game(created["game_key"])

    assert result["ok"] is True, result
    after = registry.get(api._parse_key(created["game_key"]))
    assert after is not before
    assert after.play_mode == "adventure"
    assert after.adventure_binding == before.adventure_binding
    assert after.adventure_progress == {}


def test_new_run_initializer_skips_v1_and_unbound_bindings() -> None:
    def never(*_args):
        raise AssertionError("must not resolve")

    deps = adventure_runtime.AdventureRuntimeDependencies(
        resolve_binding=never, materialize_world_seed=never,
    )
    v1 = SimpleNamespace(adventure_binding={
        "adventure_id": "core:x", "format": "diceframe:adventure-graph-v1",
    })
    unbound = SimpleNamespace(adventure_binding={})
    assert adventure_runtime.initialize_adventure_new_run(deps, v1)["reason"] == "v1"
    assert adventure_runtime.initialize_adventure_new_run(deps, unbound)["reason"] == "unbound"


# ---- seed creation runs the same Adventure v2 initialization ---------------


@pytest.mark.asyncio
async def test_seed_created_v2_adventure_initializes_progress_and_play_mode(golden) -> None:
    created = await _created_golden(golden)
    source = golden.instance(created["game_key"])
    await _complete_node(golden, created, "gate")

    seeded = await golden.api.create_from_seed(
        table_settings.seed_code(source), players=_dnd_characters(1), gm_uid="seed_gm",
        language="zh-CN",
    )

    assert seeded["ok"] is True, seeded
    instance = golden.instance(seeded["game_key"])
    assert instance is not source
    assert instance.play_mode == "adventure"
    assert instance.adventure_progress["active_nodes"] == ["gate"]
    assert instance.adventure_progress.get("completed_nodes", []) == []
    assert world_facts(instance.world_state)["location:cellar.door"]["value"] == "locked"


# ---- 3: opening plot_update is applied for a brand-new game ----------------


@pytest.mark.asyncio
async def test_opening_plot_update_is_applied_to_new_game(web_api, monkeypatch) -> None:
    api, _lorebook, registry, llm, _worlds = web_api

    async def opening(*, system_prompt, user_message, **kwargs):
        return LLMResponse(
            content="你们在遗迹前醒来。\n---\nQUEST:调查遗迹:active\nSCENE:遗迹入口",
            narration="你们在遗迹前醒来。",
            state_update=None, memory_delta=None, info_asymmetry=None,
            plot_update=None, total_tokens=5, is_narration_only=False,
            provider_used="fake",
        )

    monkeypatch.setattr(llm, "call", opening)
    created = await api.create_game(
        "template_world", "Opening",
        players=[{"character_name": "Hero", "attributes": {"str": 10}}],
    )
    assert created["ok"] is True, created
    instance = registry.get(api._parse_key(created["game_key"]))

    assert instance.plot_tracker is not None
    assert [quest.title for quest in instance.plot_tracker.quests.values()] == ["调查遗迹"]
    recovered = await registry.load(instance.game_key)
    assert [quest.title for quest in recovered.plot_tracker.quests.values()] == ["调查遗迹"]


# ---- 5: switch_world keeps the title and one World identity ----------------


@pytest.mark.asyncio
async def test_switch_world_keeps_title_and_moves_world_ref(web_api) -> None:
    api, lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world", "我的跑团",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    lorebook.create_world("custom_book_only", "只在世界书库里的世界", description="")

    result = await api.switch_world(created["game_key"], "custom_book_only")
    instance = registry.get(api._parse_key(created["game_key"]))

    assert result["ok"] is True
    assert result["world_display_name"] == "只在世界书库里的世界"
    assert instance.world_name == "我的跑团"
    assert result["world_name"] == "我的跑团"
    assert instance.world_id == "custom_book_only"
    assert content_binding.world_ref(instance)["id"] == "custom_book_only"
    assert content_binding.world_ref(instance)["source_id"] == "custom_book_only"
    assert instance.to_dict()["modules"]["content_binding"]["world_ref"]["id"] == "custom_book_only"


@pytest.mark.asyncio
async def test_switch_world_title_follows_world_when_created_with_blank_name(web_api) -> None:
    api, lorebook, registry, _llm, worlds_dir = web_api
    _write_world(worlds_dir, "grey_land")
    template_path = worlds_dir / "grey_land.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    template["world_name"] = "灰沼大陆"
    template_path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    # The web create form sends the world's display name when the name is blank.
    created = await api.create_game(
        "grey_land", "灰沼大陆",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    lorebook.create_world("book_a", "世界书A", description="")
    lorebook.create_world("book_b", "世界书B", description="")

    first = await api.switch_world(created["game_key"], "book_a")
    second = await api.switch_world(created["game_key"], "book_b")
    instance = registry.get(api._parse_key(created["game_key"]))

    assert first["ok"] is True and first["world_name"] == "世界书A"
    assert second["ok"] is True and second["world_name"] == "世界书B"
    assert instance.world_name == "世界书B"


@pytest.mark.asyncio
async def test_switch_world_is_rejected_while_a_round_is_processing(web_api) -> None:
    api, lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world", "我的跑团",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    lorebook.create_world("custom_book_only", "只在世界书库里的世界", description="")
    instance = registry.get(api._parse_key(created["game_key"]))
    before = instance.to_dict()

    async with instance._process_lock:
        result = await api.switch_world(created["game_key"], "custom_book_only")

    assert result["ok"] is False
    assert result["error_code"] == "ROUND_PROCESSING"
    assert instance.to_dict() == before


@pytest.mark.asyncio
async def test_switch_world_is_rejected_during_historical_rewrite(web_api) -> None:
    api, lorebook, registry, _llm, _worlds = web_api
    created = await api.create_game(
        "template_world", "我的跑团",
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )
    lorebook.create_world("custom_book_only", "只在世界书库里的世界", description="")
    instance = registry.get(api._parse_key(created["game_key"]))
    before = instance.to_dict()

    async with instance.historical_rewrite() as acquired:
        assert acquired is True
        # A different request task, as in production (the gate is task-reentrant).
        result = await asyncio.create_task(
            api.switch_world(created["game_key"], "custom_book_only"),
        )

    assert result["ok"] is False
    assert result["error_code"] == "REWRITE_IN_PROGRESS"
    assert instance.to_dict() == before


# ---- switch_world titles use the game's language ---------------------------

_BUILTIN_WORLDS = Path(__file__).resolve().parents[1] / "templates" / "worlds"
_LOCALIZED_WORLDS = ("default_fantasy", "jp_isekai", "coc_horror")


def _install_builtin_worlds(worlds_dir: Path) -> None:
    """Copy built-in v2 templates and their English overlays into the test root."""

    (worlds_dir / "locales" / "en").mkdir(parents=True, exist_ok=True)
    for world_id in _LOCALIZED_WORLDS:
        shutil.copy(_BUILTIN_WORLDS / f"{world_id}.json", worlds_dir / f"{world_id}.json")
        shutil.copy(
            _BUILTIN_WORLDS / "locales" / "en" / f"{world_id}.json",
            worlds_dir / "locales" / "en" / f"{world_id}.json",
        )


def _template_name(api, world_id: str, language: str) -> str:
    # Same source as the create form: the localized world-templates listing.
    listing = api.list_world_templates(language)
    rows = listing.get("templates", listing) if isinstance(listing, dict) else listing
    row = next(item for item in rows if (item.get("world_id") or item.get("id")) == world_id)
    return str(row.get("world_name") or row.get("name"))


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "zh-CN"])
async def test_switch_world_title_follows_in_the_game_language(web_api, language) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    _install_builtin_worlds(worlds_dir)
    names = {world_id: _template_name(api, world_id, language) for world_id in _LOCALIZED_WORLDS}
    if language == "en":
        # The English overlay really differs from the canonical (Chinese) name.
        assert names["default_fantasy"] != _template_name(api, "default_fantasy", "zh-CN")
    # Blank name in the create form: the localized display name becomes the title.
    created = await api.create_game(
        "default_fantasy", names["default_fantasy"], language=language,
        players=[{"character_name": "Hero", "attributes": {"str": 10}}],
    )
    assert created["ok"] is True, created

    first = await api.switch_world(created["game_key"], "jp_isekai")
    second = await api.switch_world(created["game_key"], "coc_horror")
    instance = registry.get(api._parse_key(created["game_key"]))

    assert first["ok"] is True, first
    assert first["world_name"] == first["world_display_name"] == names["jp_isekai"]
    assert second["ok"] is True, second
    assert second["world_name"] == second["world_display_name"] == names["coc_horror"]
    assert instance.world_name == names["coc_horror"]


@pytest.mark.asyncio
async def test_switch_world_keeps_a_user_title_in_an_english_game(web_api) -> None:
    api, _lorebook, registry, _llm, worlds_dir = web_api
    _install_builtin_worlds(worlds_dir)
    created = await api.create_game(
        "default_fantasy", "Friday Table", language="en",
        players=[{"character_name": "Hero", "attributes": {"str": 10}}],
    )

    result = await api.switch_world(created["game_key"], "jp_isekai")

    assert result["ok"] is True, result
    assert result["world_display_name"] == _template_name(api, "jp_isekai", "en")
    assert registry.get(api._parse_key(created["game_key"])).world_name == "Friday Table"
