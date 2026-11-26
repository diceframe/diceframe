from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import game_config
from src.localization.messages import reset_request_language, set_request_language
from src.webui.services import ruleset_rest
from tests.rulesets.test_dnd2024_character_lifecycle import professional_context  # noqa: F401

KEY = "web|character-lifecycle|web_bot"
HP_REQUIRED_ZH = "至少需要 1 HP 才能发起或加入休息"


def _rest_body(operation_id: str, *, rest: str = "short", expected_revision: int = 0) -> dict:
    body: dict = {
        "rest": rest,
        "confirm_elapsed_time": True,
        "expected_revision": expected_revision,
        "operation_id": operation_id,
    }
    if rest == "short":
        body["hit_dice"] = {"d10": 1}
    return body


def _diverge(character: dict, *, canonical: object, top_level: int) -> None:
    character["hp"] = top_level
    character["ruleset_character"]["resources"]["hp"] = canonical


@pytest.mark.asyncio
async def test_solo_rest_reads_canonical_hp_when_sheet_top_level_diverges(
    professional_context,
) -> None:
    api, instance, _character = professional_context
    game_config.replace_solo_mode(instance, True)
    sheet = instance.get_character_sheet("gm")
    _diverge(sheet, canonical=0, top_level=12)
    before = deepcopy(sheet)

    token = set_request_language("zh-CN")
    try:
        result = await ruleset_rest.submit_live(
            api._live_ruleset_rest_dependencies, KEY, "gm", _rest_body("solo-diverged"),
        )
    finally:
        reset_request_language(token)

    assert result["ok"] is False
    assert result["code"] == "REST_NOT_AVAILABLE"
    assert result["error"] == HP_REQUIRED_ZH
    assert instance.get_character_sheet("gm") == before


@pytest.mark.asyncio
async def test_party_admission_reads_canonical_hp_for_submitter_and_required_set(
    professional_context,
) -> None:
    api, instance, character = professional_context
    game_config.replace_solo_mode(instance, False)
    ally = deepcopy(character)
    ally["character_name"] = "Second Hero"
    ally["ruleset_character"]["identity"]["name"] = "Second Hero"
    ally["hp"] = 5
    ally["ruleset_character"]["resources"]["hp"] = 5
    instance.players["ally"] = {"character_name": "Second Hero", "character_sheet": ally}
    gm_sheet = instance.get_character_sheet("gm")
    _diverge(gm_sheet, canonical=0, top_level=12)

    token = set_request_language("zh-CN")
    try:
        rejected = await ruleset_rest.submit_live(
            api._live_ruleset_rest_dependencies, KEY, "gm", _rest_body("party-diverged"),
        )
    finally:
        reset_request_language(token)

    assert rejected["ok"] is False
    assert rejected["code"] == "REST_NOT_AVAILABLE"
    assert rejected["error"] == HP_REQUIRED_ZH

    started = await ruleset_rest.submit_live(
        api._live_ruleset_rest_dependencies, KEY, "ally", _rest_body("party-healthy"),
    )
    assert started["ok"] is True
    assert started["pending"] is True
    required = {row["user_id"] for row in started["rest_session"]["participants"]}
    assert required == {"ally"}


@pytest.mark.asyncio
async def test_canonical_hp_above_zero_admits_rest_despite_stale_sheet_hp(
    professional_context,
) -> None:
    api, instance, _character = professional_context
    game_config.replace_solo_mode(instance, True)
    sheet = instance.get_character_sheet("gm")
    _diverge(sheet, canonical=5, top_level=0)

    result = await ruleset_rest.submit_live(
        api._live_ruleset_rest_dependencies, KEY, "gm", _rest_body("solo-canonical-only"),
    )

    assert result["ok"] is True
    assert result["revision"] == 1
    assert result["character"]["ruleset_character"]["resources"]["hp"] > 5


@pytest.mark.asyncio
async def test_party_settlement_fails_closed_with_localized_message_when_hp_drops(
    professional_context,
) -> None:
    api, instance, character = professional_context
    game_config.replace_solo_mode(instance, False)
    ally = deepcopy(character)
    ally["character_name"] = "Second Hero"
    ally["ruleset_character"]["identity"]["name"] = "Second Hero"
    ally["hp"] = 5
    ally["ruleset_character"]["resources"]["hp"] = 5
    instance.players["ally"] = {"character_name": "Second Hero", "character_sheet": ally}
    for uid in ("gm", "ally"):
        instance.get_character_sheet(uid)["ruleset_character"]["resources"]["hp"] = 5
        instance.get_character_sheet(uid)["hp"] = 5

    first = await ruleset_rest.submit_live(
        api._live_ruleset_rest_dependencies, KEY, "gm", _rest_body("settle-gm"),
    )
    second = await ruleset_rest.submit_live(
        api._live_ruleset_rest_dependencies, KEY, "ally", _rest_body("settle-ally"),
    )
    assert first["pending"] is True
    assert second["rest_session"]["status"] == "awaiting_approval"

    _diverge(instance.get_character_sheet("gm"), canonical=0, top_level=5)

    token = set_request_language("zh-CN")
    try:
        decided = await ruleset_rest.decide_live(
            api._live_ruleset_rest_dependencies, KEY, "gm", True,
            {"approve": True, "operation_id": "settle-approve"},
        )
    finally:
        reset_request_language(token)

    assert decided["ok"] is False
    assert decided["code"] == "PARTY_REST_FAILED"
    assert decided["error"] == HP_REQUIRED_ZH
    assert decided["rest_session"]["status"] == "error"
    assert instance.get_character_sheet("gm").get("ruleset_revision", 0) == 0
    assert instance.get_character_sheet("ally").get("ruleset_revision", 0) == 0


@pytest.mark.asyncio
async def test_canonical_bool_hp_is_not_treated_as_a_number(professional_context) -> None:
    api, instance, _character = professional_context
    game_config.replace_solo_mode(instance, True)
    _diverge(instance.get_character_sheet("gm"), canonical=True, top_level=0)

    result = await ruleset_rest.submit_live(
        api._live_ruleset_rest_dependencies, KEY, "gm", _rest_body("solo-bool"),
    )

    assert result["ok"] is False
    assert result["code"] == "REST_NOT_AVAILABLE"


def test_party_eligibility_keeps_legacy_sheet_hp_when_canonical_is_absent() -> None:
    instance = GameInstance(("web", "legacy-rest"))
    instance.players["alice"] = {"character_name": "Alice", "character_sheet": {"hp": 3}}
    instance.players["bob"] = {"character_name": "Bob", "character_sheet": {"hp": 0}}
    instance.players["carol"] = {"character_name": "Carol", "character_sheet": {}}

    assert ruleset_rest._rest_eligible_uids(instance) == ["alice"]