"""Ruleset-authoritative character lifecycle operations.

Professional character mechanics live in ``ruleset_character``.  These
operations deliberately accept only non-mechanical profile data and rebuild
the legacy projection through the selected runtime before persisting once.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.engine.modules import ruleset_runtime
from src.webui.character_card_projection import dedupe_cards
from src.engine import module_hooks
from src.webui.character_contracts import MAX_BIO_CHARS
from src.webui.character_sheet_authority import adopt_requires_gm_failure
from src.localization.messages import t

if TYPE_CHECKING:
    from src.rulesets.registry import RulesetRuntimeRegistry


@dataclass(frozen=True)
class RulesetCharacterDependencies:
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule_by_id: Callable[[str, str], Any | None]
    load_rule_for_game: Callable[[Any], Any | None]
    ruleset_registry: "RulesetRuntimeRegistry"
    read_cards: Callable[[], list[dict[str, Any]]]
    write_cards: Callable[[list[dict[str, Any]]], None]
    validate_portrait: Callable[[Any], dict[str, str] | None]


PROFILE_FIELDS = frozenset({
    "pronouns",
    "appearance",
    "personality",
    "backstory",
    "ideals",
    "bonds",
    "flaws",
    "notes",
})
PROFILE_PATCH_FIELDS = frozenset({"character_name", "portrait", "profile"})
MAX_CHARACTER_NAME_CHARS = 120


class RulesetCharacterOperationError(ValueError):
    """A user-facing ruleset character lifecycle validation failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _failure(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "error": message}


def _is_rules_aware(runtime: Any) -> bool:
    return getattr(runtime.capabilities, "character_lifecycle", "legacy") == "rules_aware"


def runtime_for_card(
    dependencies: RulesetCharacterDependencies,
    card: dict[str, Any],
) -> Any | None:
    """Resolve a card's runtime without matching translated names or rule IDs."""

    canonical = card.get("ruleset_character")
    binding = canonical.get("rule_binding") if isinstance(canonical, dict) else None
    if not isinstance(binding, dict):
        binding = card.get("rule_binding")
    if isinstance(binding, dict):
        runtime_id = str(binding.get("runtime_id") or "").strip()
        raw_version = binding.get("runtime_version", 1)
        if runtime_id:
            try:
                minimum_version = int(raw_version)
                return dependencies.ruleset_registry.get(
                    runtime_id, minimum_version=max(1, minimum_version),
                )
            except (AttributeError, TypeError, ValueError):
                return None

    rule_id = str(card.get("rule_id") or "").strip()
    if not rule_id:
        return None
    rule = dependencies.load_rule_by_id(
        rule_id, str(card.get("language") or ""),
    )
    if rule is None:
        return None
    try:
        return dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError):
        return None


def card_has_rules_aware_lifecycle(
    dependencies: RulesetCharacterDependencies,
    card: dict[str, Any],
) -> bool:
    runtime = runtime_for_card(dependencies, card)
    return runtime is not None and _is_rules_aware(runtime)


def runtime_metadata_for_card(
    dependencies: RulesetCharacterDependencies,
    card: dict[str, Any],
) -> dict[str, Any] | None:
    runtime = runtime_for_card(dependencies, card)
    if runtime is None:
        return None
    canonical = card.get("ruleset_character")
    binding = canonical.get("rule_binding") if isinstance(canonical, dict) else None
    requested = 1
    if isinstance(binding, dict):
        try:
            requested = max(1, int(binding.get("runtime_version", 1)))
        except (TypeError, ValueError):
            requested = 1
    return {
        "id": runtime.runtime_id,
        "version": runtime.runtime_version,
        "requested_minimum_version": requested,
        "capabilities": runtime.capabilities.to_dict(),
    }


def normalize_character_card_blueprint(
    dependencies: RulesetCharacterDependencies,
    character: dict[str, Any],
) -> dict[str, Any]:
    """Validate and rebuild a professional card before it enters local storage.

    A display ``rule_id`` or runtime ID is never enough. The rule template must
    resolve to the same runtime, and that runtime rebuilds every mechanical
    value from canonical choices. Only validated profile/portrait data is
    reapplied afterward. Legacy and unbound cards keep their existing path.
    """

    if not isinstance(character, dict):
        raise RulesetCharacterOperationError(
            "INVALID_RULESET_CHARACTER", t("ruleset_characters.card_not_object"),
        )
    runtime = runtime_for_card(dependencies, character)
    if runtime is None or not _is_rules_aware(runtime):
        return deepcopy(character)

    canonical = character.get("ruleset_character")
    binding = canonical.get("rule_binding") if isinstance(canonical, dict) else None
    if not isinstance(canonical, dict) or not isinstance(binding, dict):
        raise RulesetCharacterOperationError(
            "PROFESSIONAL_CHARACTER_BLUEPRINT_REQUIRED",
            t("ruleset_characters.card_missing_authoritative_data"),
        )
    rule_id = str(binding.get("rule_id") or character.get("rule_id") or "").strip()
    if not rule_id:
        raise RulesetCharacterOperationError(
            "RULESET_NOT_FOUND", t("ruleset_characters.card_without_rule_id"),
        )
    locale = str(canonical.get("locale") or character.get("language") or "")
    rule = dependencies.load_rule_by_id(rule_id, locale)
    if rule is None:
        raise RulesetCharacterOperationError(
            "RULESET_NOT_FOUND", t("ruleset_characters.card_rule_missing", rule_id=rule_id),
        )
    try:
        selected_runtime = dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RulesetCharacterOperationError(
            "RULESET_RUNTIME_UNAVAILABLE", str(exc),
        ) from exc
    if selected_runtime.runtime_id != runtime.runtime_id:
        raise RulesetCharacterOperationError(
            "INCOMPATIBLE_RULESET_CHARACTER", t("ruleset_characters.card_runtime_binding_mismatch"),
        )
    try:
        normalized = runtime.normalize_character_submission(
            rule, deepcopy(character), locale,
        )
    except (TypeError, ValueError) as exc:
        code = str(getattr(exc, "error_code", "") or "")
        raise RulesetCharacterOperationError(
            code or "INVALID_RULESET_CHARACTER", t("ruleset_characters.card_failed_validation", exc=exc),
        ) from exc

    profile_patch: dict[str, Any] = {}
    source_profile = canonical.get("profile")
    if isinstance(source_profile, dict):
        profile_patch["profile"] = deepcopy(source_profile)
    # An empty portrait is the card schema's "use default" sentinel, not a
    # user-supplied portrait object that needs validation.
    if character.get("portrait") not in (None, {}):
        profile_patch["portrait"] = deepcopy(character.get("portrait"))
    if profile_patch:
        normalized, _name = _apply_profile(
            dependencies, runtime, normalized, profile_patch,
        )

    # Retain human-facing library metadata, never imported operation/revision
    # journals. A newly stored blueprint starts with a fresh entity revision.
    for key in (
        "id", "card_id", "source", "source_plugin", "plugin_content_id",
        "rule_id", "rule_name", "rule_version", "mechanics", "language",
    ):
        if key in character:
            normalized[key] = deepcopy(character[key])
    return normalized


def _validate_profile_patch(
    dependencies: RulesetCharacterDependencies,
    patch: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise RulesetCharacterOperationError("INVALID_CHARACTER_PROFILE", t("ruleset_characters.profile_not_object"))
    unknown = sorted(set(patch) - PROFILE_PATCH_FIELDS)
    if unknown:
        raise RulesetCharacterOperationError(
            "MECHANICAL_CHARACTER_FIELD_FORBIDDEN",
            t("ruleset_characters.profile_cannot_change_rule_fields", unknown=', '.join(unknown)),
        )

    result: dict[str, Any] = {}
    if "character_name" in patch:
        name = str(patch.get("character_name") or "").strip()
        if not name:
            raise RulesetCharacterOperationError("INVALID_CHARACTER_PROFILE", t("ruleset_characters.name_empty"))
        if len(name) > MAX_CHARACTER_NAME_CHARS:
            raise RulesetCharacterOperationError(
                "INVALID_CHARACTER_PROFILE",
                t("ruleset_characters.name_too_long", max_character_name_chars=MAX_CHARACTER_NAME_CHARS),
            )
        result["character_name"] = name

    if "portrait" in patch:
        try:
            result["portrait"] = dependencies.validate_portrait(
                patch.get("portrait"),
            )
        except ValueError as exc:
            raise RulesetCharacterOperationError("INVALID_CHARACTER_PROFILE", str(exc)) from exc

    if "profile" in patch:
        raw_profile = patch.get("profile")
        if not isinstance(raw_profile, dict):
            raise RulesetCharacterOperationError("INVALID_CHARACTER_PROFILE", t("ruleset_characters.profile_fields_not_object"))
        unknown_profile = sorted(set(raw_profile) - PROFILE_FIELDS)
        if unknown_profile:
            raise RulesetCharacterOperationError(
                "INVALID_CHARACTER_PROFILE",
                t("ruleset_characters.unsupported_profile_fields", unknown_profile=', '.join(unknown_profile)),
            )
        profile: dict[str, str] = {}
        total = 0
        for key, value in raw_profile.items():
            if not isinstance(value, str):
                raise RulesetCharacterOperationError(
                    "INVALID_CHARACTER_PROFILE", t("ruleset_characters.profile_field_not_text", field=key),
                )
            text = value.strip()
            total += len(text)
            profile[key] = text
        if total > MAX_BIO_CHARS:
            raise RulesetCharacterOperationError(
                "INVALID_CHARACTER_PROFILE",
                t("ruleset_characters.profile_too_long", max_bio_chars=MAX_BIO_CHARS),
            )
        result["profile"] = profile
    return result


def _apply_profile(
    dependencies: RulesetCharacterDependencies,
    runtime: Any,
    current: dict[str, Any],
    patch: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    validated = _validate_profile_patch(dependencies, patch)
    canonical = current.get("ruleset_character")
    if not isinstance(canonical, dict):
        raise RulesetCharacterOperationError(
            "PROFESSIONAL_CHARACTER_BLUEPRINT_REQUIRED",
            t("ruleset_characters.character_missing_authoritative_data"),
        )
    canonical = deepcopy(canonical)
    binding = canonical.get("rule_binding")
    if not isinstance(binding, dict) or str(binding.get("runtime_id") or "") != runtime.runtime_id:
        raise RulesetCharacterOperationError(
            "INCOMPATIBLE_RULESET_CHARACTER", t("ruleset_characters.rule_version_incompatible"),
        )

    current_identity = canonical.get("identity")
    identity = deepcopy(current_identity) if isinstance(current_identity, dict) else {}
    name = str(identity.get("name") or current.get("character_name") or "").strip()
    if "character_name" in validated:
        name = validated["character_name"]
        identity["name"] = name
        canonical["identity"] = identity

    if "profile" in validated:
        current_profile = canonical.get("profile")
        profile = deepcopy(current_profile) if isinstance(current_profile, dict) else {}
        for key, value in validated["profile"].items():
            if value:
                profile[key] = value
            else:
                profile.pop(key, None)
        if profile:
            canonical["profile"] = profile
        else:
            canonical.pop("profile", None)

    try:
        projected = runtime.project_legacy_character(canonical)
    except (TypeError, ValueError) as exc:
        raise RulesetCharacterOperationError(
            "INVALID_RULESET_CHARACTER", t("ruleset_characters.projection_failed", exc=exc),
        ) from exc
    if not isinstance(projected, dict):
        raise RulesetCharacterOperationError(
            "INVALID_RULESET_CHARACTER", t("ruleset_characters.projection_invalid"),
        )

    updated = deepcopy(current)
    updated.update(projected)
    updated["rule_binding"] = deepcopy(binding)
    updated["ruleset_character"] = canonical
    updated["character_name"] = name
    if "portrait" in validated:
        portrait = validated["portrait"]
        if portrait is None:
            updated.pop("portrait", None)
        else:
            updated["portrait"] = portrait
    return updated, name


async def update_live_character_profile(
    dependencies: RulesetCharacterDependencies,
    game_key: str,
    user_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None or user_id not in instance.players:
        return _failure("CHARACTER_NOT_FOUND", "角色不存在")
    async with instance.authoritative_write() as write_entered:
        if not write_entered:
            return _failure("REWRITE_IN_PROGRESS", t("ruleset_characters.rewriting_wait_and_retry"))
        return await _update_live_character_profile_authority(
            dependencies, instance, game_key, user_id, patch,
        )


async def _update_live_character_profile_authority(
    dependencies: RulesetCharacterDependencies,
    instance: Any,
    game_key: str,
    user_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return _failure("RULESET_NOT_FOUND", t("ruleset_characters.game_rule_missing"))
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        return _failure("RULESET_RUNTIME_UNAVAILABLE", str(exc))
    if not _is_rules_aware(runtime):
        return _failure("RULESET_CHARACTER_NOT_SUPPORTED", t("ruleset_characters.rule_not_professional_profile"))

    before_player = deepcopy(instance.players[user_id])
    try:
        updated, name = _apply_profile(
            dependencies, runtime, instance.get_character_sheet(user_id), patch,
        )
    except RulesetCharacterOperationError as exc:
        return _failure(exc.code, str(exc))

    instance.set_player_name(user_id, name)
    instance.set_character_sheet(user_id, updated)
    try:
        await dependencies.save_instance(instance)
    except Exception:
        instance.put_player(user_id, before_player)
        raise
    return {"ok": True, "character": deepcopy(updated)}


async def adopt_character_card(
    dependencies: RulesetCharacterDependencies,
    game_key: str,
    user_id: str,
    card_id: str,
    *,
    gm_authority: bool,
) -> dict[str, Any]:
    """Replace one live professional character from a server-owned blueprint.

    Once the seat has acted only the GM may replace its sheet (adoption
    resets HP, resources and equipment: a free refill / re-roll).  Checked
    inside the authoritative write so it cannot race the seat's first action.
    """
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None or user_id not in instance.players:
        return _failure("CHARACTER_NOT_FOUND", "角色不存在")
    async with instance.authoritative_write() as write_entered:
        if not write_entered:
            return _failure("REWRITE_IN_PROGRESS", t("ruleset_characters.rewriting_wait_and_retry"))
        if not gm_authority and module_hooks.seat_has_acted(instance, user_id):
            return adopt_requires_gm_failure()
        return await _adopt_character_card_authority(
            dependencies, instance, game_key, user_id, card_id,
        )


async def _adopt_character_card_authority(
    dependencies: RulesetCharacterDependencies,
    instance: Any,
    game_key: str,
    user_id: str,
    card_id: str,
) -> dict[str, Any]:
    """Replace one live professional character from a server-owned blueprint."""
    card = next(
        (
            item for item in dedupe_cards(dependencies.read_cards())
            if str(item.get("id") or "") == str(card_id or "")
        ),
        None,
    )
    if card is None:
        return _failure("CHARACTER_NOT_FOUND", t("ruleset_characters.card_missing", card_id=card_id))
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return _failure("RULESET_NOT_FOUND", t("ruleset_characters.game_rule_missing"))
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        return _failure("RULESET_RUNTIME_UNAVAILABLE", str(exc))
    card_runtime = runtime_for_card(dependencies, card)
    if (
        not _is_rules_aware(runtime)
        or card_runtime is None
        or card_runtime.runtime_id != runtime.runtime_id
    ):
        return _failure("INCOMPATIBLE_RULESET_CHARACTER", t("ruleset_characters.card_incompatible_with_ruleset"))
    try:
        normalized = runtime.normalize_character_submission(
            rule, deepcopy(card), str(getattr(instance, "language", "") or ""),
        )
    except ValueError as exc:
        return _failure(
            str(getattr(exc, "error_code", "") or "INVALID_RULESET_CHARACTER"),
            str(exc),
        )
    canonical = normalized.get("ruleset_character")
    binding = canonical.get("rule_binding") if isinstance(canonical, dict) else None
    # 绑定由游戏在创建时决定；采用角色卡只能匹配，不能反向建立绑定。
    if not ruleset_runtime.binding(instance):
        return _failure(
            "RULESET_BINDING_MISSING",
            t("ruleset_characters.save_without_ruleset_version"),
        )
    if not isinstance(binding, dict) or not instance.ruleset_binding_matches(binding):
        return _failure("INCOMPATIBLE_RULESET_CHARACTER", t("ruleset_characters.card_incompatible_with_save_version"))
    if isinstance(card.get("portrait"), dict):
        normalized["portrait"] = deepcopy(card["portrait"])
    name = str(normalized.get("character_name") or card.get("character_name") or "").strip()
    if not name:
        return _failure("INVALID_CHARACTER_PROFILE", t("ruleset_characters.name_empty"))
    before_player = deepcopy(instance.players[user_id])
    instance.set_player_name(user_id, name)
    instance.set_character_sheet(user_id, normalized)
    try:
        await dependencies.save_instance(instance)
    except Exception:
        instance.put_player(user_id, before_player)
        raise
    return {"ok": True, "character": deepcopy(normalized)}


def update_character_card_profile(
    dependencies: RulesetCharacterDependencies,
    card_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    # Local import keeps the storage module independent from ruleset runtime code.
    cards = dedupe_cards(dependencies.read_cards())
    for index, card in enumerate(cards):
        if str(card.get("id") or "") != card_id:
            continue
        runtime = runtime_for_card(dependencies, card)
        if runtime is None or not _is_rules_aware(runtime):
            return _failure(
                "RULESET_CHARACTER_NOT_SUPPORTED", t("ruleset_characters.card_not_professional_profile"),
            )
        try:
            updated, _name = _apply_profile(
                dependencies, runtime, card, patch,
            )
        except RulesetCharacterOperationError as exc:
            return _failure(exc.code, str(exc))
        updated["id"] = card_id
        updated["schema_version"] = max(2, int(card.get("schema_version", 2) or 2))
        cards[index] = updated
        dependencies.write_cards(cards)
        return {"ok": True, "card": deepcopy(updated)}
    return _failure("CHARACTER_NOT_FOUND", t("ruleset_characters.card_missing", card_id=card_id))
