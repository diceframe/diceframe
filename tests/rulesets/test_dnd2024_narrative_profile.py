"""The authored narrative profile must reach the narrator, bounded.

``ruleset_character.profile`` is what a player writes in the advanced character
center.  Mechanisms never read it, but an AI GM that cannot see who the character
is portrays a blank stranger — the exact "my card went back to its initial state"
symptom, even when the stored data is intact.  These tests freeze both halves:
the per-round LLM state and the opening-narration hook.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.commands.game_lifecycle import GameLifecycle
from src.engine.game_instance import GameInstance
from src.rulesets.contracts import NarrativeProfileRuntime
from src.rulesets.dnd2024.narrative_profile import (
    FIELD_LIMITS,
    TOTAL_LIMIT,
    narrative_profile_excerpt,
    profile_excerpt,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

PROFILE = {
    "pronouns": "she/her",
    "appearance": "Red hair, dented pauldron, a chipped holy symbol.",
    "personality": "Clipped and practical; hides warmth behind procedure.",
    "backstory": "Left the frontier watch after the bridge fell.",
    "ideals": "A promise kept is worth more than a promise made.",
    "bonds": "The watch sergeant who covered her retreat.",
    "flaws": "Cannot walk away from a fight she did not start.",
    "notes": "Prefers non-lethal solutions.",
}


def _professional_character(runtime: Dnd2024Runtime, profile: dict | None = None) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian")
    character = runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Vesper"},
    )
    if profile is not None:
        character["ruleset_character"]["profile"] = deepcopy(profile)
    return character


def _instance(character: dict) -> GameInstance:
    instance = GameInstance(
        game_key=("web", "narrative-profile", "web_bot"),
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
    return instance


# ---- excerpt contract -------------------------------------------------------


def test_excerpt_keeps_the_narrator_fields_and_drops_personal_notes() -> None:
    excerpt = profile_excerpt(PROFILE)

    assert excerpt["personality"].startswith("Clipped and practical")
    assert excerpt["backstory"].startswith("Left the frontier watch")
    # "仅供自己记录" is declared private by the UI: it never leaves the sheet.
    assert "notes" not in excerpt
    assert set(excerpt) == {key for key, _limit in FIELD_LIMITS}


def test_excerpt_is_bounded_per_field_and_in_total() -> None:
    limits = dict(FIELD_LIMITS)
    long_profile = {key: "x" * 5000 for key in limits}

    excerpt = profile_excerpt(long_profile)

    assert sum(len(value) for value in excerpt.values()) <= TOTAL_LIMIT
    for key, value in excerpt.items():
        assert len(value) <= limits[key]
    # Budget is spent in priority order: the fields the narrator needs most
    # arrive first, the tail never pushes the total over the bound.
    assert excerpt["personality"] and excerpt["backstory"]


def test_a_short_profile_is_not_truncated() -> None:
    excerpt = profile_excerpt({
        "pronouns": "she/her", "personality": "Clipped.", "bonds": "The watch.",
    })

    assert excerpt == {"pronouns": "she/her", "personality": "Clipped.", "bonds": "The watch."}


def test_excerpt_tolerates_missing_or_malformed_data() -> None:
    assert profile_excerpt(None) == {}
    assert profile_excerpt(["not", "a", "bag"]) == {}
    assert profile_excerpt({"personality": 12, "backstory": "   "}) == {}
    assert narrative_profile_excerpt({}) == {}
    assert narrative_profile_excerpt({"ruleset_character": "nope"}) == {}
    assert narrative_profile_excerpt({"ruleset_character": {"profile": {"bonds": "The watch."}}}) == {
        "bonds": "The watch.",
    }


# ---- per-round LLM state ----------------------------------------------------

def test_build_llm_view_carries_the_authored_profile() -> None:
    runtime = Dnd2024Runtime()
    instance = _instance(_professional_character(runtime, PROFILE))

    sheet = runtime.build_llm_view(instance)["players"]["gm"]["character_sheet"]

    assert sheet["narrative_profile"]["personality"].startswith("Clipped and practical")
    assert "notes" not in sheet["narrative_profile"]
    # Mechanics still come from canonical state, never from the profile bag.
    assert isinstance(sheet["hp"], int) and sheet["hp"] > 0
    assert "hp" not in sheet["narrative_profile"]


def test_build_llm_view_omits_an_empty_profile() -> None:
    runtime = Dnd2024Runtime()
    instance = _instance(_professional_character(runtime))

    sheet = runtime.build_llm_view(instance)["players"]["gm"]["character_sheet"]

    assert "narrative_profile" not in sheet


def test_the_runtime_declares_the_narrator_profile_hook() -> None:
    runtime = Dnd2024Runtime()
    assert isinstance(runtime, NarrativeProfileRuntime)

    excerpt = runtime.character_narrative_profile(_professional_character(runtime, PROFILE))

    assert excerpt["flaws"].startswith("Cannot walk away")
    assert sum(len(value) for value in excerpt.values()) <= TOTAL_LIMIT


# ---- opening narration ------------------------------------------------------

def _lifecycle(prompt) -> GameLifecycle:
    from unittest.mock import AsyncMock, Mock

    return GameLifecycle(
        registry=Mock(), llm_client=Mock(), prompt=prompt, state_applier=Mock(),
        ensure_matcher_for_world=Mock(), create_game=AsyncMock(),
        load_world_template=Mock(), narrative_max_tokens=100, brief_max_tokens=100,
    )


def test_opening_prompt_line_uses_the_bound_ruleset_excerpt() -> None:
    class _Prompt:
        @staticmethod
        def runtime(_instance):
            return Dnd2024Runtime()

    lifecycle = _lifecycle(_Prompt())
    instance = _instance(_professional_character(Dnd2024Runtime(), PROFILE))
    character = instance.get_character_sheet("gm")

    line = lifecycle._narrative_profile_line(
        lifecycle._narrative_profile_runtime(instance), character,
    )

    assert "personality=Clipped and practical" in line
    assert "notes" not in line
    assert len(line) <= 240


def test_the_opening_party_block_carries_the_profile() -> None:
    class _Prompt:
        @staticmethod
        def runtime(_instance):
            return Dnd2024Runtime()

    lifecycle = _lifecycle(_Prompt())
    instance = _instance(_professional_character(Dnd2024Runtime(), PROFILE))

    block = lifecycle._opening_player_lines(instance)

    assert block.startswith("- Vesper：Human Fighter")
    assert "人物资料：" in block
    assert "personality=Clipped and practical" in block
    assert "notes" not in block
    # 一行一个席位，不带出 JSON 结构
    assert block.count("\n") == 0


def test_the_opening_party_block_stays_plain_without_a_ruleset_hook() -> None:
    from unittest.mock import Mock

    lifecycle = _lifecycle(Mock(runtime=None))
    instance = _instance(_professional_character(Dnd2024Runtime(), PROFILE))

    block = lifecycle._opening_player_lines(instance)

    assert "人物资料" not in block
    assert block.startswith("- Vesper：")


def test_opening_prompt_line_is_empty_without_a_ruleset_hook() -> None:
    from unittest.mock import Mock

    lifecycle = _lifecycle(Mock(runtime=None))
    instance = _instance(_professional_character(Dnd2024Runtime(), PROFILE))

    assert lifecycle._narrative_profile_runtime(instance) is None
    assert lifecycle._narrative_profile_line(None, instance.get_character_sheet("gm")) == ""


def test_a_broken_hook_never_blocks_the_opening() -> None:
    class _Broken:
        def character_narrative_profile(self, _character):
            raise RuntimeError("boom")

    line = GameLifecycle._narrative_profile_line(
        _Broken(), {"ruleset_character": {"profile": PROFILE}},
    )

    assert line == ""
    assert isinstance(_Broken(), NarrativeProfileRuntime)


@pytest.mark.parametrize("sheet", [None, {}, {"character_sheet": "nope"}])
def test_opening_prompt_line_accepts_degenerate_sheets(sheet) -> None:
    assert GameLifecycle._narrative_profile_line(Dnd2024Runtime(), sheet) == ""