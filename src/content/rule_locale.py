"""Pure V2 rule locale materialization helper."""

from __future__ import annotations

import copy
from typing import Any

_DISPLAY_FIELDS = frozenset({
    "rule_name", "name", "description", "attr_hint", "skill_hint",
    "gm_prompt_appendix", "check_planner_appendix", "difficulty_instructions", "currency",
})
_DISPLAY_STRING_FIELDS = _DISPLAY_FIELDS - {"difficulty_instructions"}
_NESTED_DISPLAY_FIELDS = frozenset({"name", "description", "label", "hint", "flavor"})


def _validate_display_fields(values: dict[str, Any], location: str) -> None:
    for key, value in values.items():
        if key in _DISPLAY_STRING_FIELDS and not isinstance(value, str):
            raise ValueError(f"locale overlay {location}.{key} must be a string")
        if key == "difficulty_instructions":
            if not isinstance(value, dict) or any(
                not isinstance(name, str) or not isinstance(text, str)
                for name, text in value.items()
            ):
                raise ValueError(
                    f"locale overlay {location}.difficulty_instructions must be a string map"
                )


def materialize_rule(core: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    allowed_top = {"locale_schema_version", "locale", "target", "fields", "rule", "attributes", "classes", "items", "skills", "special_stats"}
    unknown_top = set(overlay) - allowed_top
    if unknown_top:
        raise ValueError(f"locale overlay contains unknown top-level fields: {sorted(unknown_top)}")
    if overlay.get("locale_schema_version") != 1 or not str(overlay.get("locale") or "").strip():
        raise ValueError("rule locale schema is invalid")
    target = overlay.get("target")
    if not isinstance(target, dict) or target.get("kind") != "rule" or str(target.get("id") or "") != str(core.get("rule_id") or ""):
        raise ValueError("rule locale target is invalid")
    result = copy.deepcopy(core)
    fields = overlay.get("fields", {})
    rule_fields = overlay.get("rule", {})
    if not isinstance(fields, dict) or not isinstance(rule_fields, dict):
        raise ValueError("rule locale fields and rule must be objects")
    forbidden = set(fields) - _DISPLAY_FIELDS
    if forbidden:
        raise ValueError(f"locale overlay contains mechanics fields: {sorted(forbidden)}")
    forbidden = set(rule_fields) - _DISPLAY_FIELDS
    if forbidden:
        raise ValueError(f"locale overlay contains mechanics fields: {sorted(forbidden)}")
    _validate_display_fields(fields, "fields")
    _validate_display_fields(rule_fields, "rule")
    result.update({key: value for key, value in rule_fields.items() if key in _DISPLAY_FIELDS})
    result.update({key: value for key, value in fields.items() if key in _DISPLAY_FIELDS})
    for key, values in (("attributes", overlay.get("attributes")), ("classes", overlay.get("classes")), ("items", overlay.get("items")), ("skills", overlay.get("skills")), ("special_stats", overlay.get("special_stats"))):
        if values is None:
            continue
        if not isinstance(values, dict):
            raise ValueError(f"rule locale {key} must be an object")
        if key == "attributes":
            allowed = {str(item.get("key")) for item in result.get("attributes", []) if isinstance(item, dict) and item.get("key")}
            nested_allowed = {"name", "label", "hint"}
        elif key == "classes":
            allowed = {str(item.get("id")) for item in result.get("classes", []) if isinstance(item, dict) and item.get("id")}
            nested_allowed = {"name", "description"}
        elif key == "items":
            raw_items = result.get("items", {})
            allowed = {str(item) for item in raw_items} if isinstance(raw_items, dict) else set()
            nested_allowed = {"name", "description"}
        elif key == "special_stats":
            allowed = {str(item.get("key")) for item in result.get("special_stats", []) if isinstance(item, dict) and item.get("key")}
            nested_allowed = _NESTED_DISPLAY_FIELDS
        else:
            raw_skills = result.get("skills", result.get("skill_names", {}))
            allowed = {str(item) for item in raw_skills} if isinstance(raw_skills, dict) else set()
            # Aliases are localized input labels resolving to an existing
            # canonical skill id; they do not define a skill or its outcome.
            nested_allowed = {"aliases", "name", "description", "label", "hint"}
        unknown_ids = set(values) - allowed
        if unknown_ids:
            raise ValueError(f"locale overlay contains unknown {key} identities: {sorted(unknown_ids)}")
        for identity, data in values.items():
            if not isinstance(data, dict):
                raise ValueError(f"locale overlay {key}.{identity} must be an object")
            forbidden = set(data) - nested_allowed
            if forbidden:
                raise ValueError(f"locale overlay contains mechanics fields: {sorted(forbidden)}")
            if "aliases" in data and not isinstance(data["aliases"], list):
                raise ValueError("locale overlay skill aliases must be a list")
            if "aliases" in data and any(not isinstance(alias, str) for alias in data["aliases"]):
                raise ValueError("locale overlay skill aliases must contain only strings")
            if any(not isinstance(value, str) for name, value in data.items() if name != "aliases"):
                raise ValueError(f"locale overlay {key}.{identity} display fields must be strings")
        if key == "attributes":
            by_key = {str(item.get("key")): item for item in result.get("attributes", []) if isinstance(item, dict)}
            for identity, data in values.items():
                if identity in by_key and isinstance(data, dict):
                    by_key[identity].update({k: v for k, v in data.items() if k in {"name", "label", "hint"}})
        elif key == "classes":
            by_id = {str(item.get("id")): item for item in result.get("classes", []) if isinstance(item, dict) and item.get("id")}
            for identity, data in values.items():
                if identity in by_id and isinstance(data, dict):
                    by_id[identity].update({k: v for k, v in data.items() if k in {"name", "description"}})
                    if "starter_equipment_ids" in by_id[identity]:
                        by_id[identity]["starter_equipment"] = list(by_id[identity]["starter_equipment_ids"])
        elif key == "items" and isinstance(result.get("items"), dict):
            for identity, data in values.items():
                if identity in result["items"] and isinstance(data, dict):
                    result["items"][identity].update({k: v for k, v in data.items() if k in {"name", "description"}})
        elif key == "skills":
            raw_skills = result.get("skills")
            if isinstance(raw_skills, dict):
                result["canonical_skill_names"] = {
                    str(identity): str(data.get("name") or identity)
                    for identity, data in raw_skills.items()
                    if isinstance(data, dict)
                }
                for identity, data in values.items():
                    if identity in raw_skills and isinstance(raw_skills[identity], dict):
                        raw_skills[identity].update({
                            k: v for k, v in data.items()
                            if k in {"aliases", "name", "description", "label", "hint"}
                        })
            result.setdefault("skill_names", {}).update({
                identity: str(data.get("name") or identity)
                for identity, data in values.items()
            })
        elif key == "special_stats":
            by_key = {str(item.get("key")): item for item in result.get("special_stats", []) if isinstance(item, dict)}
            for identity, data in values.items():
                if identity in by_key and isinstance(data, dict):
                    by_key[identity].update({k: v for k, v in data.items() if k in _NESTED_DISPLAY_FIELDS})
    result["active_locale"] = overlay.get("locale", result.get("default_locale", ""))
    return result
