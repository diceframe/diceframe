"""Stateless Web/API boundary for professional ruleset advancement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.engine.modules import ruleset_runtime
from src.webui.character_card_projection import dedupe_cards
from src.webui.ruleset_draft_validation import validate_draft_shape
from src.rulesets.contracts import LiveAdvancementTransactionRuntime
from src.localization.messages import t

if TYPE_CHECKING:
    from src.rulesets.registry import RulesetRuntimeRegistry


@dataclass(frozen=True)
class RulesetAdvancementDependencies:
    load_rule_by_id: Callable[[str, str], Any | None]
    ruleset_registry: "RulesetRuntimeRegistry"


@dataclass(frozen=True)
class CardAdvancementDependencies:
    read_cards: Callable[[], list[dict[str, Any]]]
    write_cards: Callable[[list[dict[str, Any]]], None]
    load_rule_by_id: Callable[[str, str], Any | None]
    runtime_for_card: Callable[[dict[str, Any]], Any | None]


@dataclass(frozen=True)
class LiveAdvancementDependencies:
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule_for_game: Callable[[Any], Any | None]
    ruleset_registry: "RulesetRuntimeRegistry"


def _context(
    dependencies: RulesetAdvancementDependencies,
    rule_id: str,
    language: str,
):
    rule = dependencies.load_rule_by_id(rule_id, language)
    if rule is None:
        return None, None, {
            "ok": False,
            "code": "RULE_NOT_FOUND",
            "error": t("ruleset_advancement.rule_missing", rule_id=rule_id),
        }
    runtime = dependencies.ruleset_registry.resolve(rule.template)
    if not all(
        callable(getattr(runtime, name, None))
        for name in ("progression_table", "preview_advancement", "apply_advancement")
    ):
        return rule, runtime, {
            "ok": False,
            "code": "RULESET_ADVANCEMENT_UNAVAILABLE",
            "error": t("ruleset_advancement.no_professional_advancement"),
        }
    return rule, runtime, None


def progression(
    dependencies: RulesetAdvancementDependencies,
    rule_id: str,
    class_ref: str,
    start_level: int = 1,
    end_level: int = 20,
    language: str = "",
) -> dict[str, Any]:
    rule, runtime, error = _context(dependencies, rule_id, language)
    if error:
        return error
    rows = runtime.progression_table(
        rule, class_ref, start_level, end_level, language,
    )
    return {"ok": True, "rule_id": rule.rule_id, "progression": rows}


def _payload(body: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = validate_draft_shape(body)
    character = parsed.get("character")
    choices = parsed.get("choices", {})
    if not isinstance(character, dict):
        raise ValueError("advancement character must be a JSON object")
    if not isinstance(choices, dict):
        raise ValueError("advancement choices must be a JSON object")
    return character, choices


def preview(
    dependencies: RulesetAdvancementDependencies,
    rule_id: str,
    body: Any,
    language: str = "",
) -> dict[str, Any]:
    rule, runtime, error = _context(dependencies, rule_id, language)
    if error:
        return error
    character, choices = _payload(body)
    return {
        "ok": True,
        "rule_id": rule.rule_id,
        "advancement": runtime.preview_advancement(rule, character, choices),
    }


def apply(
    dependencies: RulesetAdvancementDependencies,
    rule_id: str,
    body: Any,
    language: str = "",
) -> dict[str, Any]:
    rule, runtime, error = _context(dependencies, rule_id, language)
    if error:
        return error
    character, choices = _payload(body)
    return {
        "ok": True,
        "rule_id": rule.rule_id,
        "character": runtime.apply_advancement(rule, character, choices),
    }


def _card_context(dependencies: CardAdvancementDependencies, card_id: str):
    cards = dedupe_cards(dependencies.read_cards())
    for index, card in enumerate(cards):
        if str(card.get("id") or "") != card_id:
            continue
        rule_id = str(card.get("rule_id") or "").strip()
        language = str(card.get("language") or "")
        rule = dependencies.load_rule_by_id(rule_id, language)
        runtime = dependencies.runtime_for_card(card)
        if rule is None or runtime is None:
            return cards, index, card, None, None, {
                "ok": False,
                "code": "RULESET_RUNTIME_UNAVAILABLE",
                "error": t("ruleset_advancement.card_ruleset_unavailable"),
            }
        if not all(
            callable(getattr(runtime, name, None))
            for name in ("preview_advancement", "apply_advancement")
        ):
            return cards, index, card, rule, runtime, {
                "ok": False,
                "code": "RULESET_ADVANCEMENT_UNAVAILABLE",
                "error": t("ruleset_advancement.no_professional_advancement"),
            }
        return cards, index, card, rule, runtime, None
    return cards, -1, None, None, None, {
        "ok": False,
        "code": "CHARACTER_NOT_FOUND",
        "error": t("ruleset_advancement.card_missing", card_id=card_id),
    }


def _entity_choices(body: Any) -> dict[str, Any]:
    parsed = validate_draft_shape(body)
    choices = parsed.get("choices", {})
    if not isinstance(choices, dict):
        raise ValueError("advancement choices must be a JSON object")
    return choices


def preview_card(
    dependencies: CardAdvancementDependencies,
    card_id: str,
    body: Any,
) -> dict[str, Any]:
    _cards, _index, card, rule, runtime, error = _card_context(
        dependencies, card_id,
    )
    if error:
        return error
    choices = _entity_choices(body)
    return {
        "ok": True,
        "rule_id": rule.rule_id,
        "card_id": card_id,
        "revision": int(card.get("ruleset_revision", 0) or 0),
        "advancement": runtime.preview_advancement(rule, card, choices),
    }


def apply_card(
    dependencies: CardAdvancementDependencies,
    card_id: str,
    body: Any,
) -> dict[str, Any]:
    cards, index, card, rule, runtime, error = _card_context(
        dependencies, card_id,
    )
    if error:
        return error
    parsed = validate_draft_shape(body)
    choices = _entity_choices(parsed)
    operation_id = str(parsed.get("operation_id") or "").strip()
    if not operation_id or len(operation_id) > 160:
        return {
            "ok": False,
            "code": "INVALID_OPERATION_ID",
            "error": t("ruleset_advancement.operation_id_required"),
        }
    revision = int(card.get("ruleset_revision", 0) or 0)
    operation_log = card.get("ruleset_operation_log")
    operation_log = deepcopy(operation_log) if isinstance(operation_log, list) else []
    previous = next(
        (
            entry for entry in operation_log
            if isinstance(entry, dict) and entry.get("operation_id") == operation_id
        ),
        None,
    )
    if previous is not None:
        return {
            "ok": True,
            "rule_id": rule.rule_id,
            "card_id": card_id,
            "revision": revision,
            "duplicate": True,
            "character": deepcopy(card),
            "card": deepcopy(card),
        }
    raw_expected = parsed.get("expected_revision")
    if isinstance(raw_expected, bool):
        raw_expected = None
    try:
        expected_revision = int(raw_expected)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "code": "INVALID_CHARACTER_REVISION",
            "error": t("ruleset_advancement.expected_revision_required"),
        }
    if expected_revision != revision:
        return {
            "ok": False,
            "code": "STALE_CHARACTER_REVISION",
            "error": t("ruleset_advancement.card_updated_elsewhere"),
            "revision": revision,
        }

    # Re-run the authoritative next-level preview at commit time. Besides
    # validating choices, this makes level 20 and stale UI submissions a
    # normal client error instead of an unhandled exception.
    try:
        readiness = runtime.preview_advancement(rule, card, choices)
    except ValueError as exc:
        return {
            "ok": False, "code": "ADVANCEMENT_NOT_READY", "error": str(exc),
            "revision": revision,
        }
    if not readiness.get("ok"):
        return {
            "ok": False,
            "code": "ADVANCEMENT_NOT_READY",
            "error": "; ".join(str(item) for item in readiness.get("errors") or [])
            or "character cannot advance at this time",
            "revision": revision,
        }
    advanced = runtime.apply_advancement(rule, card, choices)
    updated = deepcopy(card)
    updated.update(advanced)
    updated["id"] = card_id
    updated["schema_version"] = max(2, int(card.get("schema_version", 2) or 2))
    updated["ruleset_revision"] = revision + 1
    operation_log.append({
        "operation_id": operation_id,
        "kind": "advancement",
        "revision": revision + 1,
    })
    updated["ruleset_operation_log"] = operation_log[-32:]
    cards[index] = updated
    dependencies.write_cards(cards)
    return {
        "ok": True,
        "rule_id": rule.rule_id,
        "card_id": card_id,
        "revision": revision + 1,
        "duplicate": False,
        "character": deepcopy(updated),
        "card": deepcopy(updated),
    }


def _live_context(
    dependencies: LiveAdvancementDependencies,
    game_key: str,
    user_id: str,
):
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None:
        return None, None, None, None, {
            "ok": False, "code": "GAME_NOT_FOUND", "error": "游戏不存在",
        }
    if user_id not in instance.players:
        return instance, None, None, None, {
            "ok": False, "code": "CHARACTER_NOT_FOUND", "error": "角色不存在",
        }
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return instance, None, None, None, {
            "ok": False, "code": "RULE_NOT_FOUND", "error": t("ruleset_advancement.game_rule_missing"),
        }
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        return instance, rule, None, None, {
            "ok": False, "code": "RULESET_RUNTIME_UNAVAILABLE", "error": str(exc),
        }
    if not all(
        callable(getattr(runtime, name, None))
        for name in ("preview_advancement", "apply_advancement")
    ):
        return instance, rule, runtime, None, {
            "ok": False,
            "code": "RULESET_ADVANCEMENT_UNAVAILABLE",
            "error": t("ruleset_advancement.no_professional_advancement"),
        }
    return instance, rule, runtime, instance.get_character_sheet(user_id), None


def _live_context_for_instance(
    dependencies: LiveAdvancementDependencies,
    instance: Any,
    user_id: str,
):
    if user_id not in instance.players:
        return instance, None, None, None, {
            "ok": False, "code": "CHARACTER_NOT_FOUND", "error": "角色不存在",
        }
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return instance, None, None, None, {
            "ok": False, "code": "RULE_NOT_FOUND", "error": t("ruleset_advancement.game_rule_missing"),
        }
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        return instance, rule, None, None, {
            "ok": False, "code": "RULESET_RUNTIME_UNAVAILABLE", "error": str(exc),
        }
    if not all(callable(getattr(runtime, name, None)) for name in ("preview_advancement", "apply_advancement")):
        return instance, rule, runtime, None, {
            "ok": False, "code": "RULESET_ADVANCEMENT_UNAVAILABLE", "error": t("ruleset_advancement.no_professional_advancement"),
        }
    return instance, rule, runtime, instance.get_character_sheet(user_id), None


def preview_live(
    dependencies: LiveAdvancementDependencies,
    game_key: str,
    user_id: str,
    body: Any,
) -> dict[str, Any]:
    _instance, rule, runtime, character, error = _live_context(
        dependencies, game_key, user_id,
    )
    if error:
        return error
    choices = _entity_choices(body)
    advancement = runtime.preview_advancement(rule, character, choices)
    if isinstance(runtime, LiveAdvancementTransactionRuntime):
        try:
            runtime.validate_live_advancement(
                _instance, user_id, int(advancement.get("to_level", 0) or 0),
            )
        except ValueError as exc:
            return {
                "ok": False, "code": "ADVANCEMENT_NOT_GRANTED", "error": str(exc),
                "revision": int(character.get("ruleset_revision", 0) or 0),
                "advancement_status": runtime.live_advancement_status(_instance),
            }
    return {
        "ok": True,
        "rule_id": rule.rule_id,
        "game_key": game_key,
        "user_id": user_id,
        "revision": int(character.get("ruleset_revision", 0) or 0),
        "advancement": advancement,
    }


async def apply_live(
    dependencies: LiveAdvancementDependencies,
    game_key: str,
    user_id: str,
    body: Any,
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return {"ok": False, "code": "GAME_NOT_FOUND", "error": "游戏不存在"}
    async with instance.authoritative_write() as write_entered:
        if not write_entered:
            return {
                "ok": False, "code": "REWRITE_IN_PROGRESS",
                "error": t("ruleset_advancement.rewriting_wait_and_retry"),
            }
        if dependencies.get_instance(instance.game_key) is not instance:
            return {"ok": False, "code": "STALE_RUN", "error": t("ruleset_advancement.game_restarted_refresh_and_retry")}
        return await _apply_live_authority(dependencies, game_key, user_id, body, instance)


async def _apply_live_authority(
    dependencies: LiveAdvancementDependencies,
    game_key: str,
    user_id: str,
    body: Any,
    locked_instance: Any | None = None,
) -> dict[str, Any]:
    instance, rule, runtime, character, error = (
        _live_context(dependencies, game_key, user_id)
        if locked_instance is None
        else _live_context_for_instance(dependencies, locked_instance, user_id)
    )
    if error:
        return error
    parsed = validate_draft_shape(body)
    choices = _entity_choices(parsed)
    operation_id = str(parsed.get("operation_id") or "").strip()
    if not operation_id or len(operation_id) > 160:
        return {
            "ok": False, "code": "INVALID_OPERATION_ID",
            "error": t("ruleset_advancement.operation_id_required"),
        }
    revision = int(character.get("ruleset_revision", 0) or 0)
    raw_log = character.get("ruleset_operation_log")
    operation_log = deepcopy(raw_log) if isinstance(raw_log, list) else []
    previous = next((
        entry for entry in operation_log
        if isinstance(entry, dict) and entry.get("operation_id") == operation_id
    ), None)
    if previous is not None:
        if previous.get("kind") != "advancement":
            return {
                "ok": False, "code": "OPERATION_ID_REUSED",
                "error": t("ruleset_advancement.operation_id_reused"),
            }
        return {
            "ok": True, "rule_id": rule.rule_id, "game_key": game_key,
            "user_id": user_id, "revision": revision, "duplicate": True,
            "character": deepcopy(character),
        }
    raw_expected = parsed.get("expected_revision")
    if isinstance(raw_expected, bool):
        raw_expected = None
    try:
        expected_revision = int(raw_expected)
    except (TypeError, ValueError):
        return {
            "ok": False, "code": "INVALID_CHARACTER_REVISION",
            "error": t("ruleset_advancement.expected_revision_required"),
        }
    if expected_revision != revision:
        return {
            "ok": False, "code": "STALE_CHARACTER_REVISION",
            "error": t("ruleset_advancement.character_updated_elsewhere"), "revision": revision,
        }

    try:
        readiness = runtime.preview_advancement(rule, character, choices)
    except ValueError as exc:
        return {
            "ok": False, "code": "ADVANCEMENT_NOT_READY", "error": str(exc),
            "revision": revision,
        }
    if not readiness.get("ok"):
        return {
            "ok": False,
            "code": "ADVANCEMENT_NOT_READY",
            "error": "; ".join(str(item) for item in readiness.get("errors") or [])
            or "character cannot advance at this time",
            "revision": revision,
        }
    # Live entitlement validation may materialize defaults in ruleset state.
    try:
        ruleset_runtime.require_binding(instance, runtime.runtime_id)
    except ruleset_runtime.RulesetBindingError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    live_policy = (
        runtime if isinstance(runtime, LiveAdvancementTransactionRuntime) else None
    )
    if live_policy is not None:
        try:
            live_policy.validate_live_advancement(
                instance, user_id, int(readiness.get("to_level", 0) or 0),
            )
        except ValueError as exc:
            return {
                "ok": False, "code": "ADVANCEMENT_NOT_GRANTED", "error": str(exc),
                "revision": revision,
            }
    advanced = runtime.apply_advancement(rule, character, choices)
    updated = deepcopy(character)
    updated.update(advanced)
    updated["ruleset_revision"] = revision + 1
    operation_log.append({
        "operation_id": operation_id, "kind": "advancement", "revision": revision + 1,
    })
    updated["ruleset_operation_log"] = operation_log[-32:]
    before_player = deepcopy(instance.players[user_id])
    before_advancement = (
        live_policy.snapshot_live_advancement(instance)
        if live_policy is not None
        else None
    )
    try:
        if live_policy is not None:
            live_policy.consume_live_advancement(
                instance, user_id, int(readiness.get("to_level", 0) or 0),
            )
        instance.set_character_sheet(user_id, updated)
        if live_policy is not None:
            live_policy.reconcile_live_advancement(instance, user_id)
        await dependencies.save_instance(instance)
    except Exception:
        instance.put_player(user_id, before_player)
        if live_policy is not None:
            live_policy.restore_live_advancement(instance, before_advancement)
        raise
    return {
        "ok": True, "rule_id": rule.rule_id, "game_key": game_key,
        "user_id": user_id, "revision": revision + 1, "duplicate": False,
        "character": deepcopy(updated),
        "advancement_status": (
            live_policy.live_advancement_status(instance)
            if live_policy is not None
            else None
        ),
    }


def live_status(
    dependencies: LiveAdvancementDependencies,
    game_key: str,
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return {"ok": False, "code": "GAME_NOT_FOUND", "error": "游戏不存在"}
    rule = dependencies.load_rule_for_game(instance)
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template) if rule else None
    except (AttributeError, TypeError, ValueError):
        runtime = None
    if not isinstance(runtime, LiveAdvancementTransactionRuntime):
        return {"ok": False, "code": "RULESET_ADVANCEMENT_UNAVAILABLE", "error": t("ruleset_advancement.advancement_control_unsupported")}
    return {"ok": True, "advancement": runtime.live_advancement_status(instance)}


async def control_live(
    dependencies: LiveAdvancementDependencies,
    game_key: str,
    body: Any,
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return {"ok": False, "code": "GAME_NOT_FOUND", "error": "游戏不存在"}
    async with instance.authoritative_write() as write_entered:
        if not write_entered:
            return {
                "ok": False, "code": "REWRITE_IN_PROGRESS",
                "error": t("ruleset_advancement.rewriting_wait_and_retry"),
            }
        return await _control_live_authority(dependencies, game_key, body)


async def _control_live_authority(
    dependencies: LiveAdvancementDependencies,
    game_key: str,
    body: Any,
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if not instance:
        return {"ok": False, "code": "GAME_NOT_FOUND", "error": "游戏不存在"}
    rule = dependencies.load_rule_for_game(instance)
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template) if rule else None
    except (AttributeError, TypeError, ValueError):
        runtime = None
    if not isinstance(runtime, LiveAdvancementTransactionRuntime):
        return {"ok": False, "code": "RULESET_ADVANCEMENT_UNAVAILABLE", "error": t("ruleset_advancement.advancement_control_unsupported")}
    parsed = validate_draft_shape(body)
    try:
        ruleset_runtime.require_binding(instance, runtime.runtime_id)
    except ruleset_runtime.RulesetBindingError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    result = runtime.apply_live_advancement_control(instance, parsed)
    if not result.get("ok"):
        return result
    await dependencies.save_instance(instance)
    return result
