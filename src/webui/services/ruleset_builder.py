"""Stateless Web/API boundary for versioned professional character builders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.rules.rule_system import RuleSystem
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.ruleset_draft_validation import (
    MAX_BUILDER_DRAFT_BYTES,
    MAX_BUILDER_DRAFT_DEPTH,
    MAX_BUILDER_DRAFT_NODES,
    validate_draft_shape,
)
from src.localization.messages import t

__all__ = [
    "MAX_BUILDER_DRAFT_BYTES",
    "MAX_BUILDER_DRAFT_DEPTH",
    "MAX_BUILDER_DRAFT_NODES",
    "RulesetBuilderDependencies",
    "validate_draft_shape",
]


@dataclass(frozen=True)
class RulesetBuilderDependencies:
    load_rule: Callable[[str, str], RuleSystem | None]
    ruleset_registry: RulesetRuntimeRegistry


def _context(
    dependencies: RulesetBuilderDependencies,
    rule_id: str,
    language: str,
    draft: Any,
):
    draft = validate_draft_shape(draft)
    if language and not draft.get("locale"):
        draft = {**draft, "locale": language}
    rule = dependencies.load_rule(rule_id, language)
    if rule is None:
        return None, None, draft, {
            "ok": False,
            "code": "RULE_NOT_FOUND",
            "error": t("webui.services.ruleset_builder.rule_missing", rule_id=rule_id),
        }
    runtime = dependencies.ruleset_registry.resolve(rule.template)
    if runtime.capabilities.character_builder != "professional":
        return rule, runtime, draft, {
            "ok": False,
            "code": "RULESET_BUILDER_UNAVAILABLE",
            "error": t("webui.services.ruleset_builder.uses_generic_creation"),
        }
    return rule, runtime, draft, None


def experience(
    dependencies: RulesetBuilderDependencies,
    rule_id: str,
    language: str = "",
) -> dict[str, Any]:
    rule = dependencies.load_rule(rule_id, language)
    if rule is None:
        return {"ok": False, "code": "RULE_NOT_FOUND", "error": t("webui.services.ruleset_builder.rule_missing", rule_id=rule_id)}
    runtime = dependencies.ruleset_registry.resolve(rule.template)
    return {
        "ok": True,
        "rule_id": rule.rule_id,
        "ruleset_runtime": dependencies.ruleset_registry.describe(rule.template).to_dict(),
        "experience": runtime.describe_experience(rule, language),
    }


def choices(
    dependencies: RulesetBuilderDependencies,
    rule_id: str,
    draft: Any,
    language: str = "",
) -> dict[str, Any]:
    rule, runtime, draft, error = _context(dependencies, rule_id, language, draft)
    if error:
        return error
    return {"ok": True, "rule_id": rule.rule_id, "choices": runtime.builder_choices(rule, draft)}


def validate(
    dependencies: RulesetBuilderDependencies,
    rule_id: str,
    draft: Any,
    language: str = "",
) -> dict[str, Any]:
    rule, runtime, draft, error = _context(dependencies, rule_id, language, draft)
    if error:
        return error
    errors = runtime.validate_character(rule, draft)
    return {"ok": True, "rule_id": rule.rule_id, "valid": not errors, "errors": errors}


def derive(
    dependencies: RulesetBuilderDependencies,
    rule_id: str,
    draft: Any,
    language: str = "",
) -> dict[str, Any]:
    rule, runtime, draft, error = _context(dependencies, rule_id, language, draft)
    if error:
        return error
    errors = runtime.validate_character(rule, draft)
    if errors:
        return {"ok": False, "code": "INVALID_CHARACTER_DRAFT", "errors": errors}
    return {"ok": True, "rule_id": rule.rule_id, "character": runtime.derive_character(rule, draft)}


def finalize(
    dependencies: RulesetBuilderDependencies,
    rule_id: str,
    draft: Any,
    language: str = "",
) -> dict[str, Any]:
    rule, runtime, draft, error = _context(dependencies, rule_id, language, draft)
    if error:
        return error
    errors = runtime.validate_character(rule, draft)
    if errors:
        return {"ok": False, "code": "INVALID_CHARACTER_DRAFT", "errors": errors}
    return {"ok": True, "rule_id": rule.rule_id, "character": runtime.finalize_character(rule, draft)}
