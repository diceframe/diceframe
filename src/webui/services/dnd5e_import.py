"""dnd5e Actor -> dnd2024 character creation preview/commit (m2-02).

Frozen contract: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §3.2, §5.2, §6.16.
m2-01 supplies the conversion; this service overlays the user's collected
choices, validates them through the dnd2024 runtime's *public* builder
boundary and creates the character through the existing character service. It
never re-derives rules itself and never guesses a missing choice.

Preview is stateless (no write); commit uses the ordinary ``create_player``
path for permissions, seat tokens, persistence and the dnd2024 re-derivation
that makes the runtime the only calculator after import (rule 4).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.compat.dnd5e import (
    Dnd5eConversionError,
    Dnd5eCreationError,
    compose_draft,
    convert_actor,
    has_progression_history,
    is_dnd2024_binding,
    reconcile_runtime,
)
from src.engine.modules import ruleset_runtime
from src.engine.participant_view import Viewer


@dataclass(frozen=True)
class Dnd5eImportDependencies:
    """Explicit seams onto the public builder and character services.

    Only public WebAPI methods are injected; this service never reaches for
    ``api._*`` private members or a full facade object.
    """

    get_instance: Callable[[str], Any | None]
    create_player: Callable[..., Awaitable[dict[str, Any]]]
    builder_choices: Callable[[str, dict[str, Any], str], dict[str, Any]]
    builder_validate: Callable[[str, dict[str, Any], str], dict[str, Any]]
    builder_finalize: Callable[[str, dict[str, Any], str], dict[str, Any]]


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "error": message, **details}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _selection_locale(instance: Any, locale: Any) -> str:
    return str(locale or getattr(instance, "language", "") or "en")


def _context(
    dependencies: Dnd5eImportDependencies,
    game_key: str,
    viewer: Viewer,
) -> tuple[tuple[Any, str] | None, dict[str, Any] | None]:
    """Resolve the game and its dnd2024 binding, GM-only."""

    if not viewer.is_gm:
        return None, _error("dnd5e_import_forbidden", "only the GM may import a dnd5e actor")
    instance = dependencies.get_instance(game_key)
    if instance is None:
        return None, _error("game_not_found", "game does not exist")
    binding = _mapping(ruleset_runtime.binding(instance))
    if not is_dnd2024_binding(binding):
        return None, _error(
            "dnd5e_ruleset_required",
            "dnd5e characters can only be imported into a D&D 2024 game",
        )
    rule_id = str(getattr(instance, "rule_id", "") or "")
    if not rule_id:
        return None, _error("dnd5e_ruleset_required", "game has no ruleset")
    return (instance, rule_id), None


def _builder_choices(
    dependencies: Dnd5eImportDependencies,
    rule_id: str,
    draft: dict[str, Any],
    locale: str,
) -> dict[str, Any]:
    try:
        result = dependencies.builder_choices(rule_id, draft, locale)
    except (AttributeError, TypeError, ValueError):
        return {}
    if not result.get("ok"):
        return {}
    return dict(result.get("choices") or {})


def _validate(
    dependencies: Dnd5eImportDependencies,
    rule_id: str,
    draft: dict[str, Any],
    locale: str,
) -> list[str]:
    try:
        result = dependencies.builder_validate(rule_id, draft, locale)
    except (AttributeError, TypeError, ValueError) as exc:
        return [str(exc)]
    if not result.get("ok"):
        return [str(result.get("error") or result.get("code") or "draft validation failed")]
    return [str(error) for error in result.get("errors") or []]


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _missing_selections(
    draft: Mapping[str, Any],
    choices: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Choices the request still owes, judged from the public builder choices.

    Counts and pools come from ``builder_choices`` (the same authority the
    player builder uses); nothing here hardcodes a class, species or level.
    """

    missing: list[dict[str, Any]] = []

    def add(field: str, reason: str) -> None:
        entry = {"field": field, "reason": reason}
        if entry not in missing:
            missing.append(entry)

    for key, label in (
        ("class_ref", "class"),
        ("species_ref", "species"),
        ("background_ref", "background"),
    ):
        if not draft.get(key):
            add(key, f"the dnd5e actor maps to no dnd2024 {label}")

    bonuses = draft.get("background_ability_bonuses")
    bonus_total = (
        sum(int(value) for value in bonuses.values() if isinstance(value, int))
        if isinstance(bonuses, Mapping) else 0
    )
    if not isinstance(bonuses, Mapping) or bonus_total != 3:
        add(
            "background_ability_bonuses",
            "choose the background split: +2/+1 or +1/+1/+1",
        )

    if choices.get("equipment_packages") and not draft.get("equipment_package_ref"):
        add("equipment_package_ref", "choose the class starting equipment package")
    if (
        choices.get("background_equipment_packages")
        and not draft.get("background_equipment_package_ref")
    ):
        add(
            "background_equipment_package_ref",
            "choose the background starting equipment package",
        )

    if choices.get("languages"):
        languages = _as_list(draft.get("language_refs"))
        if len(languages) != 3 or len(set(map(str, languages))) != 3:
            add(
                "language_refs",
                "choose exactly three different Standard languages, including Common",
            )

    if len(_as_list(choices.get("species_sizes"))) > 1 and not draft.get("species_size"):
        add("species_size", "choose one of the species size options")

    answers = draft.get("species_choice_answers")
    answers = answers if isinstance(answers, Mapping) else {}
    for spec in _as_list(choices.get("species_choices")):
        spec = _mapping(spec)
        choice_id = str(spec.get("id") or "")
        if not choice_id:
            continue
        allowed = _as_list(spec.get("option_ids") or spec.get("option_refs"))
        if answers.get(choice_id) not in allowed:
            add(f"species_choice_answers.{choice_id}", "choose one species option")

    class_skill_count = int(choices.get("class_skill_count") or 0)
    if class_skill_count and len(_as_list(draft.get("class_skill_refs"))) != class_skill_count:
        add("class_skill_refs", f"choose exactly {class_skill_count} class skills")

    species_skill_count = int(choices.get("species_skill_count") or 0)
    if (
        species_skill_count
        and len(_as_list(draft.get("species_skill_refs"))) != species_skill_count
    ):
        add("species_skill_refs", f"choose exactly {species_skill_count} species skills")

    species_feat_count = int(choices.get("species_feat_count") or 0)
    if species_feat_count and len(_as_list(draft.get("species_feat_refs"))) != species_feat_count:
        add("species_feat_refs", f"choose exactly {species_feat_count} species feats")

    feat_answers = draft.get("feat_choice_answers")
    feat_answers = feat_answers if isinstance(feat_answers, Mapping) else {}
    for entry in _as_list(choices.get("feat_choices")):
        entry = _mapping(entry)
        feat_ref = str(entry.get("feat_ref") or "")
        given = feat_answers.get(feat_ref)
        given = given if isinstance(given, Mapping) else {}
        for spec in _as_list(entry.get("specs")):
            spec = _mapping(spec)
            choice_id = str(spec.get("id") or "")
            count = int(spec.get("count") or 0)
            selected = given.get(choice_id)
            if not isinstance(selected, list) or len(selected) != count:
                add(
                    f"feat_choice_answers.{feat_ref}.{choice_id}",
                    f"choose exactly {count} option(s)",
                )

    records = _as_list(draft.get("feature_choices"))
    for slot in _as_list(choices.get("feature_option_choices")):
        slot = _mapping(slot)
        key = (
            str(slot.get("owner") or ""),
            str(slot.get("feature_id") or ""),
            str(slot.get("choice_id") or ""),
        )
        count = int(slot.get("count") or 1)
        selected_count = sum(
            len(_as_list(record.get("selected_refs")))
            for record in records
            if isinstance(record, Mapping)
            and (
                str(record.get("owner") or ""),
                str(record.get("feature_id") or ""),
                str(record.get("choice_id") or ""),
            ) == key
        )
        if selected_count != count:
            add(
                "feature_choices." + ".".join(key),
                f"choose exactly {count} option(s)",
            )
    return missing


def _level_block(draft: Mapping[str, Any], selections: Mapping[str, Any]) -> dict[str, Any] | None:
    level = draft.get("level")
    if isinstance(level, bool) or not isinstance(level, int) or level <= 1:
        return None
    if has_progression_history(selections):
        reason = (
            f"level {level} cannot be created from an external actor: dnd2024 creation "
            "only builds level 1, and the supplied progression.history is not replayed"
        )
    else:
        reason = (
            f"level {level} requires progression.history; this import does not guess "
            "level-ups or create above level 1"
        )
    return {"field": "progression.history", "reason": reason}


def _preflight(
    dependencies: Dnd5eImportDependencies,
    game_key: str,
    viewer: Viewer,
    actor: Any,
    selections: Any,
    locale: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    context, error = _context(dependencies, game_key, viewer)
    if error is not None:
        return None, error
    instance, rule_id = context  # type: ignore[misc]
    if not isinstance(actor, Mapping):
        return None, _error("dnd5e_actor_invalid", "actor must be a JSON object")
    if selections is not None and not isinstance(selections, Mapping):
        return None, _error("dnd5e_selection_invalid", "selections must be a JSON object")
    locale = _selection_locale(instance, locale)
    try:
        conversion = convert_actor(actor, locale=locale)
    except Dnd5eConversionError as exc:
        return None, _error(
            "dnd5e_actor_invalid", str(exc), detail_code=getattr(exc, "error_code", ""),
        )
    try:
        draft = compose_draft(conversion, selections)
    except Dnd5eCreationError as exc:
        return None, _error(
            getattr(exc, "error_code", "dnd5e_creation_invalid"), str(exc),
        )
    draft["locale"] = str(draft.get("locale") or locale)
    draft["level"] = draft.get("level") or 1

    choices = _builder_choices(dependencies, rule_id, draft, locale)
    missing = _missing_selections(draft, choices)
    block = _level_block(draft, selections or {})
    if block is not None:
        missing.insert(0, block)
    errors = _validate(dependencies, rule_id, draft, locale)
    report = deepcopy(dict(_mapping(conversion.get("report"))))
    return {
        "instance": instance,
        "rule_id": rule_id,
        "locale": locale,
        "conversion": conversion,
        "draft": draft,
        "missing": missing,
        "errors": errors,
        "report": report,
    }, None


def _finalize(
    dependencies: Dnd5eImportDependencies,
    state: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        result = dependencies.builder_finalize(
            state["rule_id"], state["draft"], state["locale"],
        )
    except (AttributeError, TypeError, ValueError) as exc:
        return None, _error("dnd5e_creation_failed", str(exc), errors=[str(exc)])
    if not result.get("ok"):
        return None, _error(
            "dnd5e_creation_failed",
            str(result.get("error") or result.get("code") or "character creation failed"),
            errors=[str(error) for error in result.get("errors") or []],
        )
    character = result.get("character")
    if not isinstance(character, Mapping):
        return None, _error("dnd5e_creation_failed", "builder returned no character")
    reconciliation = reconcile_runtime(state["conversion"], character)
    report = deepcopy(state["report"])
    report["runtime_reconciliation"] = reconciliation
    return {
        "character": dict(character),
        "reconciliation": reconciliation,
        "report": report,
    }, None


def preview(
    dependencies: Dnd5eImportDependencies,
    game_key: str,
    viewer: Viewer,
    actor: Any,
    *,
    selections: Any = None,
    locale: Any = "",
) -> dict[str, Any]:
    """Complete and validate a dnd5e actor without creating anything."""

    state, error = _preflight(dependencies, game_key, viewer, actor, selections, locale)
    if error is not None:
        return error
    assert state is not None
    base = {
        "ok": True,
        "valid": False,
        "draft": state["draft"],
        "missing": state["missing"],
        "errors": state["errors"],
        "display": state["conversion"].get("display"),
        "report": state["report"],
        "reconciliation": [],
        "source_unresolved": state["conversion"].get("unresolved") or [],
    }
    if state["missing"] or state["errors"]:
        return base
    finalized, error = _finalize(dependencies, state)
    if error is not None:
        base["errors"] = error.get("errors") or [error.get("error", "")]
        return base
    assert finalized is not None
    return {
        **base,
        "valid": True,
        "character": finalized["character"],
        "report": finalized["report"],
        "reconciliation": finalized["reconciliation"],
    }


async def commit(
    dependencies: Dnd5eImportDependencies,
    game_key: str,
    viewer: Viewer,
    actor: Any,
    *,
    selections: Any = None,
    locale: Any = "",
) -> dict[str, Any]:
    """Create the completed character through the ordinary seat-creation path."""

    state, error = _preflight(dependencies, game_key, viewer, actor, selections, locale)
    if error is not None:
        return error
    assert state is not None
    if state["missing"] or state["errors"]:
        code = (
            "dnd5e_level_requires_progression"
            if any(entry.get("field") == "progression.history" for entry in state["missing"])
            else "dnd5e_creation_incomplete"
        )
        return _error(
            code,
            "the dnd5e actor draft is not complete yet",
            draft=state["draft"],
            missing=state["missing"],
            errors=state["errors"],
            report=state["report"],
        )
    finalized, error = _finalize(dependencies, state)
    if error is not None:
        return error
    assert finalized is not None
    created = await dependencies.create_player(
        game_key, finalized["character"], assign_new_id=True,
    )
    if not created.get("ok"):
        return {
            **created,
            "report": finalized["report"],
            "reconciliation": finalized["reconciliation"],
        }
    receipt = {
        key: created[key]
        for key in ("user_id", "character_name", "seat_token", "reused")
        if key in created
    }
    return {
        "ok": True,
        "created": receipt,
        "character": finalized["character"],
        "report": finalized["report"],
        "reconciliation": finalized["reconciliation"],
    }


__all__ = ["Dnd5eImportDependencies", "commit", "preview"]