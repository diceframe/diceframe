"""dnd5e Actor -> display DTO + dnd2024 draft candidate (workplan §6.16).

Pure conversion only (§3.2): no dnd5e derived-data, Activity or ActiveEffect
interpreter is implemented here. The draft candidate is shaped for
``Dnd2024CharacterBuilder.finalize`` (m2-02 creates the character; this module
never calls it).

Verified dnd5e 6.1.x shapes (see ``tests/fixtures/dnd5e/*.meta.json``):

- ``system.abilities.<str|dex|con|int|wis|cha>.value`` is the persisted score.
- ``system.details.race`` / ``system.details.background`` are embedded Item
  references (``LocalDocumentField`` persisted as the item ``_id``); the
  authoritative identifier lives on the ``race`` / ``background`` item.
- Classes are embedded ``class`` Items; ``system.details.level`` is derived and
  not persisted, so the level is the sum of ``class.system.levels``.
- ``system.skills.<key>.value`` is the proficiency level (0, 0.5, 1, 2).
- ``system.traits.languages`` is a ``SimpleTraitField`` (``value`` set plus a
  free-text ``custom`` string).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .item_converter import convert_item_into
from .mapping import (
    CHARACTER_ACTOR_TYPES,
    Dnd2024Catalog,
    Dnd5eConversionError,
    Dnd5eConversionReport,
    default_catalog,
    map_alignment,
    map_background,
    map_class,
    map_language,
    map_skill,
    map_species,
)

ABILITY_IDS = ("str", "dex", "con", "int", "wis", "cha")

_ACTOR_HANDLED_KEYS = frozenset({
    "_id", "_stats", "effects", "flags", "folder", "img", "items", "name",
    "ownership", "prototypeToken", "sort", "system", "token", "type",
})

_ACTOR_SYSTEM_HANDLED = frozenset({
    "abilities", "attributes", "bonuses", "bastion", "conditions", "currency",
    "details", "favorites", "resources", "rolls", "skills", "spells", "tools",
    "traits",
})

_ACTOR_ITEM_TYPES = frozenset({"class", "subclass", "race", "background"})


def _malformed(message: str) -> Dnd5eConversionError:
    return Dnd5eConversionError(message, error_code="dnd5e_actor_malformed")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _items(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [item for item in (raw.get("items") or []) if isinstance(item, Mapping)]


def _item_identifier(item: Mapping[str, Any] | None) -> str:
    if item is None:
        return ""
    return _text(_mapping(item.get("system")).get("identifier"))


def _item_name(item: Mapping[str, Any] | None) -> str:
    return _text(item.get("name")) if item is not None else ""


def _resolve_embedded(
    value: Any,
    items: list[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Resolve a LocalDocumentField value (item ``_id``) to the embedded item."""

    if isinstance(value, Mapping):
        return value
    key = _text(value)
    if not key:
        return None
    for item in items:
        if _text(item.get("_id")) == key:
            return item
    return None


def _find_items(items: list[Mapping[str, Any]], *types: str) -> list[Mapping[str, Any]]:
    wanted = set(types)
    return [item for item in items if _text(item.get("type")) in wanted]


def _actor_item_label(item: Mapping[str, Any] | None, fallback: Any) -> dict[str, str]:
    return {
        "raw_name": _item_name(item) or _text(fallback),
        "raw_identifier": _item_identifier(item),
    }


def _map_actor_entity(
    item: Mapping[str, Any] | None,
    fallback: Any,
    mapper,
    catalog: Dnd2024Catalog,
) -> str | None:
    if item is not None:
        identifier = _item_identifier(item)
        if identifier:
            return mapper(identifier, catalog)
        return mapper(_item_name(item), catalog)
    return mapper(fallback, catalog) if fallback else None


def _display_abilities(system: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = _mapping(system.get("abilities"))
    result: dict[str, dict[str, Any]] = {}
    for ability in ABILITY_IDS:
        entry = _mapping(raw.get(ability))
        score = _int(entry.get("value"))
        if ability in raw:
            result[ability] = {"score": score, "declared": True}
    return result


def _display_skills(
    system: Mapping[str, Any], catalog: Dnd2024Catalog,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, entry in _mapping(system.get("skills")).items():
        data = _mapping(entry)
        proficiency = data.get("value")
        ref_id = map_skill(key, catalog)
        rows.append({
            "id": key,
            "ref": f"skill:{ref_id}" if ref_id else None,
            "proficiency": proficiency if isinstance(proficiency, (int, float)) else 0,
            "declared": True,
        })
    return rows


def _display_languages(
    system: Mapping[str, Any],
) -> tuple[list[dict[str, str]], str]:
    traits = _mapping(system.get("traits"))
    languages = _mapping(traits.get("languages"))
    keys = languages.get("value")
    rows: list[dict[str, str]] = []
    if isinstance(keys, (list, tuple)):
        rows = [{"key": str(key), "custom": ""} for key in keys if str(key)]
    custom = _text(languages.get("custom"))
    return rows, custom


def _display(raw: Mapping[str, Any], system: Mapping[str, Any]) -> dict[str, Any]:
    attributes = _mapping(system.get("attributes"))
    hp = _mapping(attributes.get("hp"))
    ac = _mapping(attributes.get("ac"))
    movement = _mapping(attributes.get("movement"))
    details = _mapping(system.get("details"))
    return {
        "name": _text(raw.get("name")),
        "type": _text(raw.get("type")),
        "image": _text(raw.get("img")),
        "level": None,  # filled by the caller from embedded class items
        "alignment": _text(details.get("alignment")),
        "hp": {
            "value": _int(hp.get("value")),
            "max": _int(hp.get("max")),
            "temp": _int(hp.get("temp")),
            "declared": True,
        },
        "armor_class": _int(ac.get("value")),
        "speed": _int(movement.get("walk")),
        "declared": True,
    }


def _class_levels(items: list[Mapping[str, Any]]) -> int:
    total = 0
    for item in _find_items(items, "class"):
        total += _int(_mapping(item.get("system")).get("levels")) or 0
    return total


def _class_skill_refs(
    skills: list[dict[str, Any]],
    *,
    class_entity: Mapping[str, Any] | None,
    background_entity: Mapping[str, Any] | None,
    report: Dnd5eConversionReport,
) -> list[str]:
    if not class_entity:
        return []
    background_skills = set(
        str(ref) for ref in (background_entity or {}).get("skill_refs") or []
    )
    allowed = set(
        str(ref) for ref in _mapping(class_entity.get("skill_choice")).get("allowed_refs") or []
    )
    required = _int(_mapping(class_entity.get("skill_choice")).get("count")) or 0
    proficient = [
        row["ref"] for row in skills
        if row["ref"] and isinstance(row["proficiency"], (int, float))
        and row["proficiency"] >= 1
    ]
    selected = sorted(
        ref for ref in proficient if ref in allowed and ref not in background_skills
    )
    if len(selected) != required:
        report.warn(
            "class skill choices cannot be fully reconstructed: "
            f"draft needs {required}, source maps {len(selected)} "
            "(proficiencies are not attributed to class vs species/feat in dnd5e data)"
        )
    return selected


def _language_refs(
    rows: list[dict[str, str]],
    custom: str,
    catalog: Dnd2024Catalog,
    report: Dnd5eConversionReport,
) -> list[str]:
    refs: list[str] = []
    for row in rows:
        key = row["key"]
        mapped = map_language(key, catalog)
        if mapped:
            ref = f"language:{mapped}"
            if ref not in refs:
                refs.append(ref)
        else:
            report.keep(
                f"system.traits.languages.value[{key}]",
                "language has no dnd2024 SRD catalog entry",
                kind="language",
            )
    if custom:
        report.keep(
            "system.traits.languages.custom",
            f"custom language text preserved: {custom}",
            kind="language",
        )
    return refs


def _unresolved_choices(
    *,
    species_entity: Mapping[str, Any] | None,
    class_entity: Mapping[str, Any] | None,
    background_entity: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    unresolved: list[dict[str, str]] = []

    def add(field: str, reason: str) -> None:
        unresolved.append({"field": field, "reason": reason})

    if species_entity:
        if _int(_mapping(species_entity.get("skill_choice")).get("count")):
            add("species_skill_refs", "species skill choice is not recorded in dnd5e actor data")
        if _int(_mapping(species_entity.get("feat_choice")).get("count")):
            add("species_feat_refs", "species origin feat is not recorded in dnd5e actor data")
        for spec in species_entity.get("choice_specs") or []:
            if isinstance(spec, Mapping) and spec.get("id"):
                add(
                    f"species_choice_answers.{spec['id']}",
                    "species lineage/option choice is not recorded in dnd5e actor data",
                )
    if class_entity:
        if class_entity.get("equipment_package_refs"):
            add("equipment_package_ref", "dnd5e does not record the chosen starting package")
        if _int(_mapping(class_entity.get("skill_choice")).get("count")):
            add("class_skill_refs", "class skill picks require confirmation")
    if background_entity and background_entity.get("equipment_package_refs"):
        add(
            "background_equipment_package_ref",
            "dnd5e does not record the chosen background package",
        )
    return unresolved


def convert_actor(
    raw_dnd5e: Any,
    *,
    locale: str = "",
    catalog: Dnd2024Catalog | None = None,
) -> dict[str, Any]:
    """Convert one raw dnd5e character Actor into display + draft candidate.

    The returned dict has ``display``, ``draft`` (candidate only, for m2-02),
    ``catalog_refs``, ``unresolved`` (user decisions m2-02 must collect),
    ``extensions.raw`` (verbatim source, never interpreted) and ``report``.
    """

    if not isinstance(raw_dnd5e, Mapping):
        raise _malformed("dnd5e actor must be an object")
    name = _text(raw_dnd5e.get("name"))
    if not name:
        raise _malformed("dnd5e actor has no name")
    actor_type = _text(raw_dnd5e.get("type"))
    if not actor_type:
        raise _malformed("dnd5e actor has no type")
    if actor_type not in CHARACTER_ACTOR_TYPES:
        raise Dnd5eConversionError(
            f"dnd5e actor type {actor_type!r} is not supported (character only)",
            error_code="dnd5e_actor_unsupported",
        )
    system = raw_dnd5e.get("system")
    if system is not None and not isinstance(system, Mapping):
        raise _malformed("dnd5e actor system is not an object")
    system = _mapping(system)
    items_value = raw_dnd5e.get("items", [])
    if not isinstance(items_value, list):
        raise _malformed("dnd5e actor items is not an array")
    if any(not isinstance(item, Mapping) for item in items_value):
        raise _malformed("dnd5e actor contains a non-object item")

    resolved = catalog if catalog is not None else default_catalog(locale)
    stats = _mapping(raw_dnd5e.get("_stats"))
    report = Dnd5eConversionReport(
        source_version=_text(stats.get("systemVersion")) or "dnd5e",
    )

    for key in raw_dnd5e:
        if key not in _ACTOR_HANDLED_KEYS:
            report.unknown(f"actor.{key}")
    for key in system:
        if key not in _ACTOR_SYSTEM_HANDLED:
            report.unknown(f"system.{key}")

    items = _items(raw_dnd5e)
    details = _mapping(system.get("details"))

    species_item = _resolve_embedded(details.get("race"), items) or (
        (_find_items(items, "race", "species") or [None])[0]
    )
    background_item = _resolve_embedded(details.get("background"), items) or (
        (_find_items(items, "background") or [None])[0]
    )
    class_items = _find_items(items, "class")
    subclass_items = _find_items(items, "subclass")

    species_ref_id = _map_actor_entity(
        species_item, details.get("race"), map_species, resolved,
    )
    background_ref_id = _map_actor_entity(
        background_item, details.get("background"), map_background, resolved,
    )
    class_refs: list[dict[str, Any]] = []
    for item in class_items:
        mapped = _map_actor_entity(item, item.get("name"), map_class, resolved)
        class_refs.append({
            "ref": f"class:{mapped}" if mapped else None,
            "levels": _int(_mapping(item.get("system")).get("levels")),
            **_actor_item_label(item, item.get("name")),
        })
    for item in subclass_items:
        report.keep(
            f"items[{_items(raw_dnd5e).index(item)}]",
            "subclass item is preserved; subclass assignment is not part of a level-1 draft",
            kind="subclass",
        )

    single_class_id = None
    if len(class_items) == 1:
        single_class_id = _map_actor_entity(class_items[0], class_items[0].get("name"), map_class, resolved)
        if single_class_id is None:
            report.keep(
                "system (class item)",
                f"class {_item_identifier(class_items[0]) or _item_name(class_items[0])!r} "
                "has no dnd2024 SRD catalog entry",
                kind="class",
            )
            report.missing("class", "system (class item)", _item_identifier(class_items[0]))
    elif len(class_items) > 1:
        report.keep(
            "items (class)",
            "multiple classes are preserved; a single-class level-1 draft cannot be proposed",
            kind="class",
        )

    if species_item is not None and species_ref_id is None:
        report.keep(
            "system.details.race",
            f"species {_item_identifier(species_item) or _item_name(species_item)!r} "
            "has no dnd2024 SRD catalog entry",
            kind="species",
        )
        report.missing("species", "system.details.race", _item_identifier(species_item))
    elif species_item is None and details.get("race"):
        report.keep(
            "system.details.race",
            f"species {_text(details.get('race'))!r} has no dnd2024 SRD catalog entry",
            kind="species",
        )
        report.missing("species", "system.details.race", _text(details.get("race")))

    if background_item is not None and background_ref_id is None:
        report.keep(
            "system.details.background",
            f"background {_item_identifier(background_item) or _item_name(background_item)!r} "
            "has no dnd2024 SRD catalog entry",
            kind="background",
        )
        report.missing(
            "background", "system.details.background", _item_identifier(background_item),
        )
    elif background_item is None and details.get("background"):
        report.keep(
            "system.details.background",
            f"background {_text(details.get('background'))!r} has no dnd2024 SRD catalog entry",
            kind="background",
        )
        report.missing(
            "background", "system.details.background", _text(details.get("background")),
        )

    level = _class_levels(items)
    if class_items and level != 1:
        report.warn(
            f"actor level {level} exceeds dnd2024 level-1 creation; "
            "progression is required before creation (m2-02)"
        )

    alignment_raw = _text(details.get("alignment"))
    alignment = map_alignment(alignment_raw) if alignment_raw else None
    if alignment_raw and alignment is None:
        report.keep(
            "system.details.alignment",
            f"alignment {alignment_raw!r} is not a recognized dnd2024 alignment",
            kind="alignment",
        )

    ability_values: dict[str, int] = {}
    abilities_raw = _mapping(system.get("abilities"))
    for ability in ABILITY_IDS:
        entry = _mapping(abilities_raw.get(ability))
        score = _int(entry.get("value"))
        if score is None:
            report.warn(f"ability {ability} has no declared integer score")
            continue
        if not 3 <= score <= 20:
            report.warn(f"ability {ability} score {score} is outside 3..20")
        ability_values[ability] = score
    if set(ability_values) != set(ABILITY_IDS):
        report.keep(
            "system.abilities",
            "incomplete ability scores are preserved; a full base_abilities set is required",
            kind="abilities",
        )
    else:
        report.warn(
            "dnd5e stores final ability scores; the background +2/+1 split cannot be "
            "reconstructed without guessing, so background_ability_bonuses is left "
            "empty for the user to confirm (m2-02)"
        )

    skill_rows = _display_skills(system, resolved)
    language_rows, custom_languages = _display_languages(system)
    language_refs = _language_refs(language_rows, custom_languages, resolved, report)

    species_entity = resolved.bundle.get("species", species_ref_id) if species_ref_id else None
    background_entity = (
        resolved.bundle.get("background", background_ref_id) if background_ref_id else None
    )
    class_entity = resolved.bundle.get("class", single_class_id) if single_class_id else None

    class_skill_refs = _class_skill_refs(
        skill_rows,
        class_entity=class_entity,
        background_entity=background_entity,
        report=report,
    )

    item_rows: list[dict[str, Any]] = []
    item_refs: list[str] = []
    for index, item in enumerate(items):
        if _text(item.get("type")) in _ACTOR_ITEM_TYPES:
            continue
        try:
            converted = convert_item_into(
                item, catalog=resolved, report=report, path=f"items[{index}]",
            )
        except Dnd5eConversionError as exc:
            report.keep(f"items[{index}]", f"item could not be converted: {exc}")
            continue
        row = converted["display"]
        row["catalog_ref"] = converted["catalog_ref"]
        item_rows.append(row)
        if converted["catalog_ref"]:
            item_refs.append(converted["catalog_ref"])

    display = _display(raw_dnd5e, system)
    display["level"] = level or None
    display["species"] = (
        {
            "name": resolved.name("species", species_ref_id) if species_ref_id else "",
            "ref": f"species:{species_ref_id}" if species_ref_id else None,
            **_actor_item_label(species_item, details.get("race")),
        }
        if (species_item is not None or details.get("race"))
        else None
    )
    display["background"] = (
        {
            "name": resolved.name("background", background_ref_id) if background_ref_id else "",
            "ref": f"background:{background_ref_id}" if background_ref_id else None,
            **_actor_item_label(background_item, details.get("background")),
        }
        if (background_item is not None or details.get("background"))
        else None
    )
    for row in class_refs:
        ref_id = row["ref"].split(":", 1)[1] if row["ref"] else ""
        row["name"] = resolved.name("class", ref_id) if ref_id else row["raw_name"]
    display["class"] = class_refs
    display["abilities"] = _display_abilities(system)
    display["skills"] = skill_rows
    display["languages"] = [
        {"key": row["key"], "ref": (
            f"language:{mapped}" if (mapped := map_language(row["key"], resolved)) else None
        )}
        for row in language_rows
    ] + ([{"key": "custom", "ref": None, "text": custom_languages}] if custom_languages else [])
    display["items"] = item_rows

    if raw_dnd5e.get("effects"):
        report.keep(
            "effects",
            "embedded ActiveEffect data is a modelling reference only; not interpreted",
            kind="active_effect",
        )

    draft: dict[str, Any] | None = None
    unresolved = _unresolved_choices(
        species_entity=species_entity,
        class_entity=class_entity,
        background_entity=background_entity,
    )
    if len(class_items) == 1:
        draft = {
            "name": name,
            "level": level or 1,
            "ability_method": "rolled",
            "base_abilities": dict(ability_values),
            "background_ability_bonuses": {},
            "class_ref": f"class:{single_class_id}" if single_class_id else None,
            "species_ref": f"species:{species_ref_id}" if species_ref_id else None,
            "background_ref": f"background:{background_ref_id}" if background_ref_id else None,
            "class_skill_refs": class_skill_refs,
            "class_tool_refs": [],
            "equipment_package_ref": None,
            "background_equipment_package_ref": None,
            "language_refs": language_refs,
            "species_size": None,
            "species_choice_answers": {},
            "species_skill_refs": [],
            "species_feat_refs": [],
            "feat_choice_answers": {},
        }
        if alignment:
            draft["alignment"] = alignment
    elif not class_items:
        report.warn("no class item; a dnd2024 draft candidate cannot be proposed")
    else:
        report.warn(
            "multiple class items; a single-class dnd2024 draft candidate cannot be proposed"
        )

    catalog_refs: list[str] = []
    for ref in [
        f"species:{species_ref_id}" if species_ref_id else None,
        f"class:{single_class_id}" if single_class_id else None,
        f"background:{background_ref_id}" if background_ref_id else None,
        *class_skill_refs,
        *language_refs,
        *item_refs,
    ]:
        if ref and ref not in catalog_refs:
            catalog_refs.append(ref)

    return {
        "kind": "dnd5e_actor",
        "source_version": report.source_version,
        "display": display,
        "draft": draft,
        "catalog_refs": catalog_refs,
        "unresolved": unresolved,
        "extensions": {"raw": deepcopy(dict(raw_dnd5e))},
        "report": report.to_dict(),
    }