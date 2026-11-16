from __future__ import annotations

import base64
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from src.engine.game_instance import GameInstance, GameRegistry
from src.engine.game_state import GameState
from src.engine.modules import seat_activity
from src.rules.rule_system import RuleSystem
from src.rulesets.builtin import build_default_ruleset_registry
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024 import advancement_access
from src.webui.character_sheet_authority import ADOPT_REQUIRES_GM
from src.webui.routes.character_cards import register_character_cards
from src.webui.routes.games import register_games
from src.webui.services import (
    character_cards,
    characters,
    ruleset_advancement,
    ruleset_characters,
    ruleset_rest,
)
from src.webui.services._common import _parse_game_key
from src.engine.modules import ruleset_runtime


class _Api:
    def __init__(self, tmp_path):
        self._reg = GameRegistry(tmp_path / "saves")
        self._character_cards_path = tmp_path / "character_cards.json"
        self._ruleset_registry = build_default_ruleset_registry()
        self._runtime = self._ruleset_registry.get("core:dnd2024")
        self._rule = RuleSystem({
            "rule_id": "dnd2024_srd",
            "rule_name": "5E 2024 SRD",
            "runtime": {"id": "core:dnd2024", "minimum_version": 1},
        })
        self._live_ruleset_rest_dependencies = (
            ruleset_rest.LiveRulesetRestDependencies(
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
                save_instance=self._reg.save,
                load_rule_for_game=self._load_rule_for_game,
                ruleset_registry=self._ruleset_registry,
            )
        )
        self._character_dependencies = characters.CharacterDependencies(
            games=characters.CharacterGameDependencies(
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
                save_instance=self._reg.save,
            ),
            rules=characters.CharacterRuleDependencies(
                rules_dir=None,
                load_rule_by_id=self._load_rule_by_id,
                load_rule_for_game=self._load_rule_for_game,
                ruleset_registry=self._ruleset_registry,
            ),
            assets=characters.CharacterAssetDependencies(
                lorebook=None,
                load_world_template=lambda _world_id, _language: None,
                avatar_file=self.avatar_file,
                generated_image_file=self.generated_image_file,
            ),
            save_character_card=lambda character: character_cards.save_character_card(
                self._character_card_dependencies, character,
            ),
        )
        self._ruleset_character_dependencies = (
            ruleset_characters.RulesetCharacterDependencies(
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
                save_instance=self._reg.save,
                load_rule_by_id=self._load_rule_by_id,
                load_rule_for_game=self._load_rule_for_game,
                ruleset_registry=self._ruleset_registry,
                read_cards=lambda: character_cards._read_cards(
                    self._character_card_dependencies,
                ),
                write_cards=lambda cards: character_cards._write_cards(
                    self._character_card_dependencies, cards,
                ),
                validate_portrait=lambda portrait: characters._validated_portrait(
                    self._character_dependencies.assets, portrait,
                ),
            )
        )
        self._character_card_dependencies = (
            character_cards.CharacterCardDependencies(
                cards_path=self._character_cards_path,
                ruleset_card_metadata=lambda card: ruleset_characters.runtime_metadata_for_card(
                    self._ruleset_character_dependencies, card,
                ),
                normalize_ruleset_card=lambda card: ruleset_characters.normalize_character_card_blueprint(
                    self._ruleset_character_dependencies, card,
                ),
                is_ruleset_card=lambda card: ruleset_characters.card_has_rules_aware_lifecycle(
                    self._ruleset_character_dependencies, card,
                ),
            )
        )
        self._card_advancement_dependencies = (
            ruleset_advancement.CardAdvancementDependencies(
                read_cards=lambda: character_cards._read_cards(
                    self._character_card_dependencies,
                ),
                write_cards=lambda cards: character_cards._write_cards(
                    self._character_card_dependencies, cards,
                ),
                load_rule_by_id=self._load_rule_by_id,
                runtime_for_card=lambda card: ruleset_characters.runtime_for_card(
                    self._ruleset_character_dependencies, card,
                ),
            )
        )
        self._live_advancement_dependencies = (
            ruleset_advancement.LiveAdvancementDependencies(
                get_instance=self._reg.get,
                parse_game_key=_parse_game_key,
                save_instance=self._reg.save,
                load_rule_for_game=self._load_rule_for_game,
                ruleset_registry=self._ruleset_registry,
            )
        )

    @staticmethod
    def _parse_key(game_key: str):
        return _parse_game_key(game_key)

    def get_game_instance(self, game_key: str):
        return self._reg.get(self._parse_key(game_key))

    def _load_rule_for_game(self, instance):
        del instance
        return self._rule

    def _load_rule_by_id(self, rule_id: str, language: str = ""):
        del language
        return self._rule if rule_id == "dnd2024_srd" else None

    def avatar_file(self, asset_id: str):
        del asset_id
        return None

    def generated_image_file(self, asset_id: str):
        del asset_id
        return None

    def save_character_card(self, character: dict):
        return character_cards.save_character_card(
            self._character_card_dependencies, character,
        )

    async def ruleset_rest_resolve_live(self, game_key: str, user_id: str, body):
        return await ruleset_rest.resolve_live(
            self._live_ruleset_rest_dependencies, game_key, user_id, body,
        )

    async def ruleset_rest_submit_live(self, game_key: str, user_id: str, body):
        return await ruleset_rest.submit_live(
            self._live_ruleset_rest_dependencies, game_key, user_id, body,
        )

    async def ruleset_rest_resolve_live_party(
        self, game_key: str, user_id: str, body,
    ):
        return await ruleset_rest.resolve_live_party(
            self._live_ruleset_rest_dependencies, game_key, user_id, body,
        )

    def update_ruleset_character_card_profile(self, card_id: str, patch):
        return ruleset_characters.update_character_card_profile(
            self._ruleset_character_dependencies, card_id, patch,
        )

    async def update_ruleset_character_profile(
        self, game_key: str, user_id: str, patch,
    ):
        return await ruleset_characters.update_live_character_profile(
            self._ruleset_character_dependencies, game_key, user_id, patch,
        )

    async def adopt_ruleset_character_card(
        self, game_key: str, user_id: str, card_id: str, *, gm_authority: bool,
    ):
        return await ruleset_characters.adopt_character_card(
            self._ruleset_character_dependencies, game_key, user_id, card_id,
            gm_authority=gm_authority,
        )

    def preview_character_card_advancement(self, card_id: str, body):
        return ruleset_advancement.preview_card(
            self._card_advancement_dependencies, card_id, body,
        )

    def apply_character_card_advancement(self, card_id: str, body):
        return ruleset_advancement.apply_card(
            self._card_advancement_dependencies, card_id, body,
        )

    def pending_character_card_choices(self, card_id: str):
        return ruleset_advancement.pending_card_choices(
            self._card_advancement_dependencies, card_id,
        )

    def complete_character_card_choices(self, card_id: str, body):
        return ruleset_advancement.complete_card_choices(
            self._card_advancement_dependencies, card_id, body,
        )

    def preview_live_character_advancement(
        self, game_key: str, user_id: str, body,
    ):
        return ruleset_advancement.preview_live(
            self._live_advancement_dependencies, game_key, user_id, body,
        )

    async def apply_live_character_advancement(
        self, game_key: str, user_id: str, body,
    ):
        return await ruleset_advancement.apply_live(
            self._live_advancement_dependencies, game_key, user_id, body,
        )

    async def control_live_advancement(self, game_key: str, body):
        return await ruleset_advancement.control_live(
            self._live_advancement_dependencies, game_key, body,
        )


def _professional_character(runtime: Dnd2024Runtime) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"]
        if item["id"] == "stalwart_guardian"
    )
    return runtime.finalize_character(
        None,
        {**preset["draft"], "locale": "en", "name": "Boundary Hero"},
    )


@pytest.fixture()
def professional_context(tmp_path):
    api = _Api(tmp_path)
    character = _professional_character(api._runtime)
    instance = GameInstance(
        game_key=("web", "character-lifecycle", "web_bot"),
        world_id="test-world",
        rule_id="dnd2024_srd",
        gm_uid="gm",
        language="en",
    )
    instance.players["gm"] = {
        "character_name": character["character_name"],
        "character_sheet": deepcopy(character),
    }
    assert instance.bind_ruleset_runtime(character["rule_binding"])
    api._reg.register(instance)
    return api, instance, character


@pytest.mark.asyncio
async def test_legacy_live_update_rejects_professional_mechanics_atomically(
    professional_context,
) -> None:
    api, instance, _character = professional_context
    before = deepcopy(instance.players["gm"])

    result = await characters.update_character(
        api._character_dependencies,
        "web|character-lifecycle|web_bot",
        "gm",
        {
            "character_name": "Partially Mutated",
            "portrait": {"kind": "builtin", "id": "wizard-1"},
            "hp": 999,
            "attributes": {"str": 99},
        },
    )

    assert result == {
        "ok": False,
        "error_code": "RULESET_CHARACTER_OPERATION_REQUIRED",
        "error": "专业规则角色不能使用旧版通用编辑接口",
    }
    assert instance.players["gm"] == before


@pytest.mark.asyncio
async def test_live_profile_update_preserves_canonical_mechanics(
    professional_context,
) -> None:
    api, instance, _character = professional_context
    before = deepcopy(instance.get_character_sheet("gm")["ruleset_character"])

    result = await ruleset_characters.update_live_character_profile(
        api._ruleset_character_dependencies,
        "web|character-lifecycle|web_bot",
        "gm",
        {
            "character_name": "Renamed Hero",
            "portrait": {"kind": "builtin", "id": "wizard-1"},
            "profile": {
                "pronouns": "they/them",
                "appearance": "A weathered blue cloak.",
                "personality": "Patient with new adventurers.",
                "backstory": "Left the academy to protect a frontier town.",
                "notes": "Prefers non-lethal solutions.",
            },
        },
    )

    assert result["ok"] is True
    sheet = instance.get_character_sheet("gm")
    canonical = sheet["ruleset_character"]
    assert instance.players["gm"]["character_name"] == "Renamed Hero"
    assert canonical["identity"]["name"] == "Renamed Hero"
    assert canonical["profile"]["backstory"].startswith("Left the academy")
    assert sheet["portrait"] == {"kind": "builtin", "id": "wizard-1"}
    for key in (
        "rule_binding", "build", "abilities", "proficiencies", "resources",
        "equipment", "features", "spellcasting", "derived", "sources", "progression",
    ):
        assert canonical[key] == before[key]
    assert sheet["hp"] == canonical["resources"]["hp"]
    assert sheet["attributes"] == canonical["abilities"]


def test_raw_card_update_rejects_professional_canonical_overwrite(
    professional_context,
) -> None:
    api, _instance, character = professional_context
    saved = character_cards.save_character_card(
        api._character_card_dependencies, character,
    )["card"]
    before = deepcopy(
        character_cards.list_character_cards(api._character_card_dependencies)["cards"]
    )

    result = character_cards.update_character_card(
        api._character_card_dependencies,
        saved["id"],
        {
            "character_name": "Partially Mutated",
            "ruleset_character": {"resources": {"hp": 999}},
        },
    )

    assert result["ok"] is False
    assert result["error_code"] == "RULESET_CHARACTER_OPERATION_REQUIRED"
    assert character_cards.list_character_cards(
        api._character_card_dependencies,
    )["cards"] == before


def test_professional_card_is_rebuilt_before_storage(professional_context) -> None:
    api, _instance, character = professional_context
    forged = deepcopy(character)
    forged["ruleset_character"]["resources"]["hp"] = 9999
    forged["ruleset_character"]["derived"]["armor_class"] = 99
    forged["ruleset_character"]["profile"] = {"notes": "Keep this safe note."}

    result = character_cards.save_character_card(
        api._character_card_dependencies, forged,
    )

    assert result["ok"] is True
    card = result["card"]
    assert card["ruleset_character"]["resources"]["hp"] != 9999
    assert card["ruleset_character"]["derived"]["armor_class"] != 99
    assert card["ruleset_character"]["profile"] == {"notes": "Keep this safe note."}


@pytest.mark.asyncio
async def test_invalid_professional_import_is_rejected_before_storage(
    professional_context,
) -> None:
    api, _instance, character = professional_context
    forged = deepcopy(character)
    forged["schema_version"] = 2
    forged["rule_id"] = "dnd2024_srd"
    forged["ruleset_character"]["build"]["class_levels"][0]["class_ref"] = "class:not-real"
    payload = base64.b64encode(
        json.dumps(forged, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")

    imported = await character_cards.import_character_card(
        api._character_card_dependencies,
        file_data=payload,
        file_name="forged.json",
    )

    assert imported["ok"] is False
    assert imported["error_code"] == "INVALID_RULESET_CHARACTER"
    assert character_cards.list_character_cards(
        api._character_card_dependencies,
    )["cards"] == []


def test_card_profile_update_preserves_professional_blueprint(
    professional_context,
) -> None:
    api, _instance, character = professional_context
    saved = character_cards.save_character_card(
        api._character_card_dependencies, character,
    )["card"]
    before = deepcopy(saved["ruleset_character"])

    result = ruleset_characters.update_character_card_profile(
        api._ruleset_character_dependencies,
        saved["id"],
        {
            "character_name": "Library Hero",
            "profile": {"backstory": "Kept as reusable campaign-neutral notes."},
        },
    )

    assert result["ok"] is True
    card = result["card"]
    assert card["character_name"] == "Library Hero"
    assert card["ruleset_character"]["identity"]["name"] == "Library Hero"
    assert card["ruleset_character"]["profile"]["backstory"].startswith("Kept as")
    for key in (
        "rule_binding", "build", "abilities", "proficiencies", "resources",
        "equipment", "features", "spellcasting", "derived", "sources", "progression",
    ):
        assert card["ruleset_character"][key] == before[key]


def test_dnd_runtime_declares_rules_aware_character_lifecycle() -> None:
    assert Dnd2024Runtime.capabilities.character_lifecycle == "rules_aware"


def test_card_advancement_is_entity_backed_versioned_and_idempotent(
    professional_context,
) -> None:
    api, _instance, character = professional_context
    saved = character_cards.save_character_card(
        api._character_card_dependencies, character,
    )["card"]
    card_id = saved["id"]
    choices = {"hp_method": "fixed"}

    preview = ruleset_advancement.preview_card(
        api._card_advancement_dependencies, card_id, {"choices": choices},
    )
    applied = ruleset_advancement.apply_card(
        api._card_advancement_dependencies,
        card_id,
        {"choices": choices, "expected_revision": 0, "operation_id": "level-2"},
    )
    duplicate = ruleset_advancement.apply_card(
        api._card_advancement_dependencies,
        card_id,
        {"choices": choices, "expected_revision": 0, "operation_id": "level-2"},
    )
    stale = ruleset_advancement.apply_card(
        api._card_advancement_dependencies,
        card_id,
        {"choices": choices, "expected_revision": 0, "operation_id": "level-3"},
    )

    assert preview["revision"] == 0
    assert preview["advancement"]["to_level"] == 2
    assert applied["ok"] is True
    assert applied["revision"] == 1
    assert applied["card"]["ruleset_character"]["build"]["level"] == 2
    assert duplicate["ok"] is True
    assert duplicate["duplicate"] is True
    assert duplicate["revision"] == 1
    assert stale["ok"] is False
    assert stale["code"] == "STALE_CHARACTER_REVISION"


@pytest.mark.asyncio
async def test_live_advancement_is_entity_backed_versioned_and_idempotent(
    professional_context,
) -> None:
    api, instance, _character = professional_context
    advancement_access.grant(instance, "gm", source="gm")

    preview = ruleset_advancement.preview_live(
        api._live_advancement_dependencies,
        "web|character-lifecycle|web_bot", "gm", {"choices": {"hp_method": "fixed"}},
    )
    applied = await ruleset_advancement.apply_live(
        api._live_advancement_dependencies,
        "web|character-lifecycle|web_bot", "gm",
        {
            "choices": {"hp_method": "fixed"},
            "expected_revision": 0,
            "operation_id": "live-level-2",
        },
    )
    duplicate = await ruleset_advancement.apply_live(
        api._live_advancement_dependencies,
        "web|character-lifecycle|web_bot", "gm",
        {
            "choices": {"hp_method": "fixed"},
            "expected_revision": 0,
            "operation_id": "live-level-2",
        },
    )
    stale = await ruleset_advancement.apply_live(
        api._live_advancement_dependencies,
        "web|character-lifecycle|web_bot", "gm",
        {
            "choices": {"hp_method": "fixed"},
            "expected_revision": 0,
            "operation_id": "live-level-3",
        },
    )

    assert preview["revision"] == 0
    assert applied["revision"] == 1
    assert applied["character"]["ruleset_character"]["build"]["level"] == 2
    assert duplicate["duplicate"] is True
    assert stale["code"] == "STALE_CHARACTER_REVISION"
    assert instance.get_character_sheet("gm")["ruleset_revision"] == 1


@pytest.mark.asyncio
async def test_live_advancement_is_rejected_during_swipe(professional_context) -> None:
    api, instance, _character = professional_context
    advancement_access.grant(instance, "gm", source="gm")
    before_sheet = deepcopy(instance.get_character_sheet("gm"))
    before_state = deepcopy(ruleset_runtime.state(instance))
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold_rewrite() -> None:
        async with instance.historical_rewrite() as acquired:
            assert acquired is True
            async with instance._process_lock:
                entered.set()
                await release.wait()

    rewrite = asyncio.create_task(hold_rewrite())
    await entered.wait()
    result = await asyncio.wait_for(
        ruleset_advancement.apply_live(
            api._live_advancement_dependencies,
            "web|character-lifecycle|web_bot", "gm",
            {
                "choices": {"hp_method": "fixed"},
                "expected_revision": 0,
                "operation_id": "blocked-level-2",
            },
        ),
        1,
    )
    assert result["code"] == "REWRITE_IN_PROGRESS"
    assert instance.get_character_sheet("gm") == before_sheet
    assert ruleset_runtime.state(instance) == before_state
    release.set()
    await asyncio.wait_for(rewrite, 1)


@pytest.mark.asyncio
async def test_live_rest_rolls_on_server_and_is_idempotent(professional_context) -> None:
    api, instance, _character = professional_context
    sheet = instance.get_character_sheet("gm")
    sheet["ruleset_character"]["resources"]["hp"] = 1
    sheet["hp"] = 1

    applied = await ruleset_rest.resolve_live(
        api._live_ruleset_rest_dependencies,
        "web|character-lifecycle|web_bot", "gm",
        {
            "rest": "short",
            "hit_dice": {"d10": 1},
            "confirm_elapsed_time": True,
            "expected_revision": 0,
            "operation_id": "short-rest-1",
        },
    )
    duplicate = await ruleset_rest.resolve_live(
        api._live_ruleset_rest_dependencies,
        "web|character-lifecycle|web_bot", "gm",
        {
            "rest": "short",
            "hit_dice": {"d10": 1},
            "confirm_elapsed_time": True,
            "expected_revision": 0,
            "operation_id": "short-rest-1",
        },
    )
    forged = await ruleset_rest.resolve_live(
        api._live_ruleset_rest_dependencies,
        "web|character-lifecycle|web_bot", "gm",
        {
            "rest": "short",
            "hit_die_rolls": {"d10": [10]},
            "confirm_elapsed_time": True,
            "expected_revision": 1,
            "operation_id": "short-rest-forged",
        },
    )

    rested = instance.get_character_sheet("gm")
    assert applied["ok"] is True
    assert applied["revision"] == 1
    assert rested["hp"] > 1
    assert rested["ruleset_character"]["resources"]["hit_dice"]["d10"] == 0
    assert duplicate["duplicate"] is True
    assert duplicate["character"] == rested
    assert forged["code"] == "CLIENT_ROLLS_FORBIDDEN"


@pytest.mark.asyncio
async def test_live_rest_is_rejected_during_historical_rewrite(professional_context) -> None:
    api, instance, _character = professional_context
    before = deepcopy(instance.get_character_sheet("gm"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold_rewrite() -> None:
        async with instance.historical_rewrite() as acquired:
            assert acquired is True
            entered.set()
            await release.wait()

    rewrite = asyncio.create_task(hold_rewrite())
    await entered.wait()
    result = await asyncio.wait_for(
        ruleset_rest.resolve_live(
            api._live_ruleset_rest_dependencies,
            "web|character-lifecycle|web_bot", "gm",
            {
                "rest": "short", "hit_dice": {"d10": 1},
                "confirm_elapsed_time": True, "expected_revision": 0,
                "operation_id": "rewrite-rest",
            },
        ),
        1,
    )
    assert result["code"] == "REWRITE_IN_PROGRESS"
    assert instance.get_character_sheet("gm") == before
    release.set()
    await asyncio.wait_for(rewrite, 1)


@pytest.mark.asyncio
async def test_multiplayer_rest_waits_for_every_present_character_and_resolves_once(
    professional_context,
) -> None:
    api, instance, character = professional_context
    ally = deepcopy(character)
    ally["character_name"] = "Second Hero"
    ally["ruleset_character"]["identity"]["name"] = "Second Hero"
    instance.players["ally"] = {
        "character_name": "Second Hero",
        "character_sheet": ally,
    }
    for uid in ("gm", "ally"):
        sheet = instance.get_character_sheet(uid)
        sheet["hp"] = 1
        sheet["ruleset_character"]["resources"]["hp"] = 1

    waiting = await ruleset_rest.resolve_live_party(
        api._live_ruleset_rest_dependencies,
        "web|character-lifecycle|web_bot", "gm",
        {
            "rest": "short", "hit_dice": {"d10": 1},
            "confirm_elapsed_time": True, "expected_revision": 0,
            "operation_id": "party-rest-gm",
        },
    )

    assert waiting["pending"] is True
    assert waiting["resolved"] is False
    assert waiting["rest_session"]["ready_count"] == 1
    assert waiting["rest_session"]["active_count"] == 2
    assert instance.get_character_sheet("gm")["hp"] == 1
    assert instance.get_character_sheet("ally")["hp"] == 1

    resolved = await ruleset_rest.resolve_live_party(
        api._live_ruleset_rest_dependencies,
        "web|character-lifecycle|web_bot", "ally",
        {
            "rest": "short", "hit_dice": {"d10": 1},
            "confirm_elapsed_time": True, "expected_revision": 0,
            "operation_id": "party-rest-ally",
        },
    )

    assert resolved["resolved"] is True
    assert resolved["rest_session"]["status"] == "completed"
    assert {row["user_id"] for row in resolved["party_results"]} == {"gm", "ally"}
    for uid in ("gm", "ally"):
        sheet = instance.get_character_sheet(uid)
        assert sheet["hp"] > 1
        assert sheet["ruleset_revision"] == 1
        assert sheet["ruleset_character"]["resources"]["hit_dice"]["d10"] == 0


def test_authoritative_event_batch_advances_character_revision_once(
    professional_context,
) -> None:
    api, instance, _character = professional_context
    before_hp = instance.get_character_sheet("gm")["ruleset_character"]["resources"]["hp"]
    batch = {
        "batch_id": "batch_character_damage_1",
        "intent_id": "damage-1",
        "intent_type": "attack",
        "expected_version": 0,
        "result_version": 1,
        "events": [{
            "type": "resource.changed",
            "target_id": "player:gm",
            "resource": "hp",
            "delta": -2,
        }],
        "source_ref": "test:authoritative-damage",
    }

    applied = api._runtime.apply_event_batch(instance, batch)
    replayed = api._runtime.apply_event_batch(instance, batch)

    sheet = instance.get_character_sheet("gm")
    assert applied["applied"] is True
    assert applied["character_revisions"] == {"gm": 1}
    assert sheet["ruleset_character"]["resources"]["hp"] == before_hp - 2
    assert sheet["ruleset_revision"] == 1
    assert replayed["duplicate"] is True
    assert replayed["character_revisions"] == {}
    assert sheet["ruleset_operation_log"][-1]["operation_id"] == batch["batch_id"]


@pytest.mark.asyncio
async def test_live_character_adopts_server_owned_professional_blueprint(
    professional_context,
) -> None:
    api, instance, _character = professional_context
    replacement = _professional_character(api._runtime)
    replacement["ruleset_character"]["identity"]["name"] = "Replacement Hero"
    replacement["character_name"] = "Replacement Hero"
    saved = character_cards.save_character_card(
        api._character_card_dependencies, replacement,
    )["card"]

    result = await ruleset_characters.adopt_character_card(
        api._ruleset_character_dependencies,
        "web|character-lifecycle|web_bot", "gm", saved["id"],
        gm_authority=True,
    )

    assert result["ok"] is True
    sheet = instance.get_character_sheet("gm")
    assert instance.players["gm"]["character_name"] == "Replacement Hero"
    assert sheet["character_name"] == "Replacement Hero"
    assert sheet["hp"] == sheet["ruleset_character"]["resources"]["hp"]


@pytest.mark.asyncio
async def test_player_side_adoption_is_gm_only_once_the_seat_acted(
    professional_context,
) -> None:
    """Adoption resets HP / resources / equipment: after acting it is a re-roll."""
    api, instance, _character = professional_context
    replacement = _professional_character(api._runtime)
    replacement["ruleset_character"]["identity"]["name"] = "Refill Hero"
    replacement["character_name"] = "Refill Hero"
    saved = character_cards.save_character_card(
        api._character_card_dependencies, replacement,
    )["card"]
    game_key = "web|character-lifecycle|web_bot"
    instance.state = GameState.ACTIVE_ACTION  # a running game, as on the web
    original = deepcopy(instance.players["gm"])

    fresh_seat = await ruleset_characters.adopt_character_card(
        api._ruleset_character_dependencies, game_key, "gm", saved["id"],
        gm_authority=False,
    )
    assert fresh_seat["ok"] is True
    assert instance.players["gm"]["character_name"] == "Refill Hero"

    instance.players["gm"] = deepcopy(original)
    seat_activity.mark_acted(instance, "gm")
    before = deepcopy(instance.players["gm"])
    rejected = await api.adopt_ruleset_character_card(
        game_key, "gm", saved["id"], gm_authority=False,
    )
    assert rejected["ok"] is False
    assert rejected["error_code"] == ADOPT_REQUIRES_GM
    assert instance.players["gm"] == before

    by_gm = await ruleset_characters.adopt_character_card(
        api._ruleset_character_dependencies, game_key, "gm", saved["id"],
        gm_authority=True,
    )
    assert by_gm["ok"] is True
    assert instance.players["gm"]["character_name"] == "Refill Hero"


def _http_app(api: _Api) -> web.Application:
    @web.middleware
    async def identity(request: web.Request, handler):
        request["user_id"] = request.headers.get("X-Test-User", "")
        request["owner_authenticated"] = request.headers.get("X-Test-Owner") == "1"
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app["api"] = api
    app["subsystems"] = SimpleNamespace(registry=api._reg)
    register_games(app)
    register_character_cards(app)
    return app


@pytest.mark.asyncio
async def test_profile_http_routes_enforce_live_identity_and_patch_cards(
    professional_context,
) -> None:
    api, instance, character = professional_context
    saved = character_cards.save_character_card(
        api._character_card_dependencies, character,
    )["card"]
    game_path = "/api/games/web%7Ccharacter-lifecycle%7Cweb_bot/character/gm/profile"
    card_path = f"/api/character-cards/{saved['id']}/profile"

    async with TestClient(TestServer(_http_app(api))) as client:
        denied = await client.patch(
            game_path,
            headers={"X-Test-User": "intruder"},
            json={"character_name": "Stolen"},
        )
        updated = await client.patch(
            game_path,
            headers={"X-Test-User": "gm"},
            json={"profile": {"personality": "Welcoming and concise."}},
        )
        updated_card = await client.patch(
            card_path,
            headers={"X-Test-Owner": "1"},
            json={"profile": {"notes": "Reusable blueprint notes."}},
        )
        updated_card_body = await updated_card.json()

    assert denied.status == 403
    assert updated.status == 200
    assert instance.get_character_sheet("gm")["ruleset_character"]["profile"] == {
        "personality": "Welcoming and concise.",
    }
    assert updated_card.status == 200
    assert updated_card_body["card"]["ruleset_character"]["profile"]["notes"].startswith(
        "Reusable"
    )


@pytest.mark.asyncio
async def test_card_choice_routes_answer_with_status_codes_over_http(
    professional_context,
) -> None:
    api, _instance, character = professional_context
    saved = character_cards.save_character_card(
        api._character_card_dependencies, character,
    )["card"]
    revision = int(saved.get("ruleset_revision", 0) or 0)
    pending_path = f"/api/character-cards/{saved['id']}/choices/pending"
    complete_path = f"/api/character-cards/{saved['id']}/choices/complete"
    headers = {"X-Test-Owner": "1"}

    async with TestClient(TestServer(_http_app(api))) as client:
        pending = await client.get(pending_path, headers=headers)
        pending_body = await pending.json()
        missing_card = await client.get(
            "/api/character-cards/no-such-card/choices/pending",
            headers=headers,
        )
        stale = await client.post(
            complete_path,
            headers=headers,
            json={
                "repairs": {"subclass_ref": "subclass:fighter:champion"},
                "operation_id": "http-choice-stale",
                "expected_revision": revision + 7,
            },
        )
        empty = await client.post(
            complete_path,
            headers=headers,
            json={
                "repairs": {},
                "operation_id": "http-choice-empty",
                "expected_revision": revision,
            },
        )
        empty_body = await empty.json()

    assert pending.status == 200
    assert pending_body["ok"] is True
    assert pending_body["missing"] == []
    assert missing_card.status == 404
    assert stale.status == 409
    assert empty.status == 422
    assert empty_body["code"] == "ADVANCEMENT_NOT_READY"


@pytest.mark.asyncio
async def test_live_advancement_and_rest_http_routes_enforce_identity(
    professional_context,
) -> None:
    api, instance, _character = professional_context
    base = "/api/games/web%7Ccharacter-lifecycle%7Cweb_bot/character/gm"

    async with TestClient(TestServer(_http_app(api))) as client:
        denied_control = await client.post(
            "/api/games/web%7Ccharacter-lifecycle%7Cweb_bot/advancement/control",
            headers={"X-Test-User": "intruder"},
            json={"action": "grant", "user_id": "gm"},
        )
        granted = await client.post(
            "/api/games/web%7Ccharacter-lifecycle%7Cweb_bot/advancement/control",
            headers={"X-Test-User": "gm"},
            json={"action": "grant", "user_id": "gm"},
        )
        preview = await client.post(
            f"{base}/advancement/preview",
            headers={"X-Test-User": "gm"},
            json={"choices": {"hp_method": "fixed"}},
        )
        denied = await client.post(
            f"{base}/advancement/apply",
            headers={"X-Test-User": "intruder"},
            json={
                "choices": {"hp_method": "fixed"},
                "expected_revision": 0,
                "operation_id": "http-level-2",
            },
        )
        applied = await client.post(
            f"{base}/advancement/apply",
            headers={"X-Test-User": "gm"},
            json={
                "choices": {"hp_method": "fixed"},
                "expected_revision": 0,
                "operation_id": "http-level-2",
            },
        )
        rested = await client.post(
            f"{base}/rest",
            headers={"X-Test-User": "gm"},
            json={
                "rest": "long",
                "confirm_elapsed_time": True,
                "expected_revision": 1,
                "operation_id": "http-long-rest",
            },
        )

    assert denied_control.status == 403
    assert granted.status == 200
    assert preview.status == 200
    assert denied.status == 403
    assert applied.status == 200
    assert rested.status == 200
    assert instance.get_character_sheet("gm")["ruleset_revision"] == 2
