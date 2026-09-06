from __future__ import annotations

from copy import deepcopy

import pytest

from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.legacy_adapter import LegacyRulesetAdapter
from src.rulesets.builtin import build_default_ruleset_registry
from src.rulesets.registry import (
    DuplicateRulesetRuntimeError,
    IncompatibleRulesetRuntimeError,
    InvalidRulesetBindingError,
    RulesetRuntimeRegistry,
    UnknownRulesetRuntimeError,
    parse_ruleset_binding,
)


class _Runtime:
    runtime_id = "test:professional"
    runtime_version = 3
    capabilities = RulesetCapabilities(
        experience_profile="test",
        character_builder="professional",
        authoritative_intents=True,
        deterministic_combat=True,
        versioned_state=True,
    )

    def describe_experience(self, rule, locale):
        return {}

    def builder_choices(self, rule, draft):
        return {}

    def validate_character(self, rule, draft):
        return []

    def derive_character(self, rule, draft):
        return dict(draft)

    def finalize_character(self, rule, draft):
        return dict(draft)

    def available_intents(self, instance, actor_id):
        return []

    def validate_intent(self, instance, intent):
        return {"ok": True}

    def resolve_intent(self, instance, intent, rng):
        return {}

    def build_llm_view(self, instance):
        return {}

    def project_legacy_character(self, character):
        return dict(character)

    def migrate_state(self, payload, from_version):
        return dict(payload)


def test_missing_runtime_binding_resolves_to_legacy() -> None:
    registry = build_default_ruleset_registry()

    runtime = registry.resolve({"rule_id": "freeform"})
    metadata = registry.describe({"rule_id": "freeform"}).to_dict()

    assert isinstance(runtime, LegacyRulesetAdapter)
    assert metadata == {
        "id": "core:legacy",
        "version": 1,
        "requested_minimum_version": 1,
        "capabilities": {
            "experience_profile": "generic",
            "character_builder": "legacy",
            "character_lifecycle": "legacy",
            "authoritative_intents": False,
            "deterministic_combat": False,
            "versioned_state": False,
            "session_zero": False,
            "tutorial_coach": False,
            "combat_action_effects": False,
            "combat_resource_pools": False,
            "combat_scheduler": "",
            "narrative_turns": False,
            "adventure_formats": (),
        },
    }


def test_explicit_binding_uses_only_runtime_id_and_version() -> None:
    registry = RulesetRuntimeRegistry([LegacyRulesetAdapter(), _Runtime()])
    template = {
        "rule_id": "not_named_after_runtime",
        "rule_name": "任意翻译名",
        "runtime": {"id": "test:professional", "minimum_version": 2},
    }

    assert registry.resolve(template).runtime_id == "test:professional"
    assert registry.describe(template).requested_minimum_version == 2


def test_string_binding_is_supported_as_version_one_shorthand() -> None:
    registry = RulesetRuntimeRegistry([_Runtime()])

    assert registry.resolve({"runtime": "test:professional"}).runtime_version == 3


def test_unknown_and_incompatible_runtime_bindings_are_rejected() -> None:
    registry = RulesetRuntimeRegistry([_Runtime()])

    with pytest.raises(UnknownRulesetRuntimeError):
        registry.resolve({"runtime": {"id": "missing:runtime"}})
    with pytest.raises(IncompatibleRulesetRuntimeError):
        registry.resolve({
            "runtime": {"id": "test:professional", "minimum_version": 4},
        })


@pytest.mark.parametrize(
    "raw",
    [None, {}, {"id": ""}, {"id": "x", "minimum_version": 0}, {"id": "x", "minimum_version": True}],
)
def test_invalid_explicit_bindings_are_rejected(raw) -> None:
    template = {"runtime": raw}
    if raw is None:
        # Missing/JSON null intentionally means legacy for old templates.
        assert parse_ruleset_binding(template).runtime_id == "core:legacy"
        return
    with pytest.raises(InvalidRulesetBindingError):
        parse_ruleset_binding(template)


def test_duplicate_registration_is_rejected() -> None:
    registry = RulesetRuntimeRegistry([_Runtime()])

    with pytest.raises(DuplicateRulesetRuntimeError):
        registry.register(_Runtime())


def test_legacy_adapter_delegates_validation_without_mutating_draft() -> None:
    adapter = LegacyRulesetAdapter()
    rule = RuleSystem({
        "rule_id": "legacy_test",
        "attribute_points": 10,
        "attributes": [{"key": "str", "name": "Strength", "min": 1, "max": 10}],
        "classes": [],
    })
    draft = {"attributes": {"str": 11}, "skills": []}
    before = deepcopy(draft)

    errors = adapter.validate_character(rule, draft)

    assert errors
    assert draft == before
    assert adapter.derive_character(rule, draft) == draft
    assert adapter.derive_character(rule, draft) is not draft
