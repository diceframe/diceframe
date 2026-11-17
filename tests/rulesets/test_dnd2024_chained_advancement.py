"""Chained advancement over the stateless rules endpoint.

A starting level is a sequence of ordinary single-level advancements, not a new
engine path: the client replays ``preview``/``apply`` once per level and the
character it submits carries one ``history`` entry per level it gained.  This
test drives that sequence at the HTTP layer, because the panel's loop is only
correct if the endpoint really is stateless between calls -- the second request
must derive everything from the character the first one returned.
"""

from __future__ import annotations

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from src.rules.rule_system import RuleSystem
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.legacy_adapter import LegacyRulesetAdapter
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.routes.rules import register_rules
from src.webui.services import ruleset_advancement, ruleset_rest

from dnd2024_http_common import quick_character

RULE_ID = "test_dnd2024"


class _ChainedApi:
    """The smallest api surface ``register_rules`` needs for this path."""

    def __init__(self, runtime: Dnd2024Runtime):
        self._ruleset_registry = RulesetRuntimeRegistry([LegacyRulesetAdapter(), runtime])
        self._professional_rule = RuleSystem({
            "rule_id": RULE_ID,
            "runtime": {"id": "core:dnd2024", "minimum_version": 1},
        })
        self._ruleset_rest_dependencies = ruleset_rest.RulesetRestDependencies(
            load_rule_by_id=self._load_rule_by_id,
            ruleset_registry=self._ruleset_registry,
        )
        self._ruleset_advancement_dependencies = (
            ruleset_advancement.RulesetAdvancementDependencies(
                load_rule_by_id=self._load_rule_by_id,
                ruleset_registry=self._ruleset_registry,
            )
        )

    def _load_rule_by_id(self, rule_id: str, language: str = ""):
        del language
        return self._professional_rule if rule_id == RULE_ID else None

    def ruleset_progression(
        self, rule_id: str, class_ref: str, start_level: int = 1,
        end_level: int = 20, language: str = "",
    ):
        return ruleset_advancement.progression(
            self._ruleset_advancement_dependencies,
            rule_id, class_ref, start_level, end_level, language,
        )

    def ruleset_advancement_preview(self, rule_id: str, body, language: str = ""):
        return ruleset_advancement.preview(
            self._ruleset_advancement_dependencies, rule_id, body, language,
        )

    def ruleset_advancement_apply(self, rule_id: str, body, language: str = ""):
        return ruleset_advancement.apply(
            self._ruleset_advancement_dependencies, rule_id, body, language,
        )

    def ruleset_rest_resolve(self, rule_id: str, body, language: str = ""):
        return ruleset_rest.resolve(
            self._ruleset_rest_dependencies, rule_id, body, language,
        )


def _history(character: dict) -> list[tuple[int, int]]:
    canonical = character.get("ruleset_character") or {}
    entries = (canonical.get("progression") or {}).get("history") or []
    return [(int(entry["from_level"]), int(entry["to_level"])) for entry in entries]


@pytest.mark.asyncio
async def test_the_rules_endpoint_carries_a_character_from_level_one_to_three() -> None:
    runtime = Dnd2024Runtime()
    character = quick_character(runtime, "stalwart_guardian", "Chained HTTP")
    assert character["level"] == 1

    app = web.Application()
    app["api"] = _ChainedApi(runtime)
    register_rules(app)

    async with TestClient(TestServer(app)) as client:
        first_preview = await (
            await client.post(
                f"/api/rules/{RULE_ID}/advancement/preview",
                json={"character": character, "choices": {"hp_method": "fixed"}},
            )
        ).json()
        assert first_preview["ok"] is True
        assert first_preview["advancement"]["from_level"] == 1
        assert first_preview["advancement"]["to_level"] == 2
        assert first_preview["advancement"]["ok"] is True

        first_apply = await (
            await client.post(
                f"/api/rules/{RULE_ID}/advancement/apply",
                json={"character": character, "choices": {"hp_method": "fixed"}},
            )
        ).json()
        assert first_apply["ok"] is True
        level_two = first_apply["character"]
        assert level_two["level"] == 2
        assert _history(level_two) == [(1, 2)]
        # The stateless endpoint has no revision or idempotency key to echo:
        # the panel must not start sending one in this mode.
        assert "revision" not in first_apply
        assert "operation_id" not in first_apply

        second_preview = await (
            await client.post(
                f"/api/rules/{RULE_ID}/advancement/preview",
                json={"character": level_two, "choices": {"hp_method": "fixed"}},
            )
        ).json()
        assert second_preview["advancement"]["from_level"] == 2
        assert second_preview["advancement"]["to_level"] == 3
        # Level 3 owes a subclass; the chain must stop here instead of picking
        # one for the player, which is why the panel simply renders the errors.
        assert second_preview["advancement"]["ok"] is False
        assert "subclass_ref is required at this level" in second_preview["advancement"]["errors"]
        subclass = second_preview["advancement"]["requirements"][0]["options"][0]["value"]

        blocked = await client.post(
            f"/api/rules/{RULE_ID}/advancement/apply",
            json={"character": level_two, "choices": {"hp_method": "fixed"}},
        )
        assert blocked.status == 422

        second_apply = await (
            await client.post(
                f"/api/rules/{RULE_ID}/advancement/apply",
                json={
                    "character": level_two,
                    "choices": {"hp_method": "fixed", "subclass_ref": subclass},
                },
            )
        ).json()
        assert second_apply["ok"] is True
        level_three = second_apply["character"]
        assert level_three["level"] == 3
        assert _history(level_three) == [(1, 2), (2, 3)]
