"""Safe loader for versioned, locale-separated ruleset content bundles."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from src.engine.language import content_locale_candidates


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_BUNDLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")
_AUTOMATION_LEVELS = frozenset({"deterministic", "guided", "reference"})
_FORBIDDEN_EXECUTION_KEYS = frozenset({
    "python", "javascript", "script", "code", "eval", "module", "callable",
})
_LOCALE_FIELDS = frozenset({
    "name", "description", "summary", "hint", "recommendation_reason",
    "tutorial", "labels", "text",
})
ALLOWED_EFFECT_OPS = frozenset({
    "grant_proficiency", "grant_language", "grant_item", "modify_ability",
    "modify_derived_stat", "add_resource", "consume_resource",
    "restore_resource", "apply_condition", "remove_condition", "deal_damage",
    "heal", "force_save", "make_attack", "set_concentration",
    "grant_action", "grant_reaction",
})


class RulesetBundleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RulesetBundleManifest:
    schema_version: int
    bundle_id: str
    runtime_id: str
    ruleset_version: str
    content_version: str
    default_locale: str
    supported_locales: tuple[str, ...]
    license_id: str
    attribution_path: str


@dataclass(frozen=True, slots=True)
class LoadedRulesetBundle:
    root: Path
    manifest: RulesetBundleManifest
    locale: str
    entities: dict[str, dict[str, dict[str, Any]]]

    def get(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        entity = self.entities.get(kind, {}).get(entity_id)
        return deepcopy(entity) if entity is not None else None

    def list(self, kind: str) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self.entities.get(kind, {}).values()]


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RulesetBundleError(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RulesetBundleError(f"JSON root must be an object: {path}")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise RulesetBundleError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RulesetBundleError(f"{field} must be a positive integer") from exc
    if parsed < 1:
        raise RulesetBundleError(f"{field} must be a positive integer")
    return parsed


def _required_text(value: Any, field: str, pattern: re.Pattern[str] | None = None) -> str:
    parsed = str(value or "").strip()
    if not parsed:
        raise RulesetBundleError(f"{field} must not be empty")
    if pattern is not None and not pattern.fullmatch(parsed):
        raise RulesetBundleError(f"{field} has an invalid format: {parsed}")
    return parsed


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _validate_no_executable_content(entity: dict[str, Any], path: Path) -> None:
    for node in _walk(entity):
        forbidden = _FORBIDDEN_EXECUTION_KEYS.intersection(str(key).lower() for key in node)
        if forbidden:
            raise RulesetBundleError(
                f"executable content key is forbidden in {path}: {sorted(forbidden)[0]}"
            )


def _validate_effects(entity: dict[str, Any], path: Path) -> None:
    for node in _walk(entity):
        if "effects" not in node:
            continue
        effects = node["effects"]
        if not isinstance(effects, list):
            raise RulesetBundleError(f"effects must be an array: {path}")
        for effect in effects:
            if not isinstance(effect, dict):
                raise RulesetBundleError(f"effect must be an object: {path}")
            op = str(effect.get("op") or "")
            if op not in ALLOWED_EFFECT_OPS:
                raise RulesetBundleError(f"unknown effect operation {op!r}: {path}")


class RulesetBundleLoader:
    def __init__(self, bundles_dir: str | Path):
        self.bundles_dir = Path(bundles_dir)

    def load(self, directory_id: str, locale: str = "") -> LoadedRulesetBundle:
        directory_id = _required_text(directory_id, "directory_id", _ID_RE)
        base = self.bundles_dir.resolve()
        root = (base / directory_id).resolve()
        if not root.is_relative_to(base) or not root.is_dir():
            raise RulesetBundleError(f"ruleset bundle does not exist: {directory_id}")

        manifest = self._load_manifest(root)
        entities = self._load_entities(root)
        self._validate_references(entities)
        active_locale = self._select_locale(manifest, locale)
        materialized = deepcopy(entities)
        for overlay_locale in dict.fromkeys((manifest.default_locale, active_locale)):
            self._apply_locale(root, overlay_locale, materialized)
        return LoadedRulesetBundle(root, manifest, active_locale, materialized)

    def _load_manifest(self, root: Path) -> RulesetBundleManifest:
        raw = _read_object(root / "manifest.json")
        schema_version = _positive_int(raw.get("schema_version"), "schema_version")
        if schema_version != 1:
            raise RulesetBundleError(f"unsupported ruleset bundle schema: {schema_version}")
        bundle_id = _required_text(raw.get("bundle_id"), "bundle_id", _BUNDLE_ID_RE)
        runtime_id = _required_text(raw.get("runtime_id"), "runtime_id", _BUNDLE_ID_RE)
        ruleset_version = _required_text(raw.get("ruleset_version"), "ruleset_version")
        content_version = _required_text(raw.get("content_version"), "content_version")
        default_locale = _required_text(raw.get("default_locale"), "default_locale")
        locales = raw.get("supported_locales")
        if not isinstance(locales, list) or not locales:
            raise RulesetBundleError("supported_locales must be a non-empty array")
        supported_locales = tuple(_required_text(item, "supported_locale") for item in locales)
        if len(set(supported_locales)) != len(supported_locales):
            raise RulesetBundleError("supported_locales must not contain duplicates")
        if default_locale not in supported_locales:
            raise RulesetBundleError("default_locale must be listed in supported_locales")
        license_block = raw.get("license")
        if not isinstance(license_block, dict):
            raise RulesetBundleError("license must be an object")
        license_id = _required_text(license_block.get("id"), "license.id")
        attribution_path = _required_text(
            license_block.get("attribution"), "license.attribution"
        )
        attribution = (root / attribution_path).resolve()
        if not attribution.is_relative_to(root) or not attribution.is_file():
            raise RulesetBundleError("license.attribution must reference a file inside the bundle")
        return RulesetBundleManifest(
            schema_version=schema_version,
            bundle_id=bundle_id,
            runtime_id=runtime_id,
            ruleset_version=ruleset_version,
            content_version=content_version,
            default_locale=default_locale,
            supported_locales=supported_locales,
            license_id=license_id,
            attribution_path=attribution_path,
        )

    def _load_entities(self, root: Path) -> dict[str, dict[str, dict[str, Any]]]:
        content_root = root / "content"
        if not content_root.is_dir():
            raise RulesetBundleError("ruleset bundle content directory is missing")
        entities: dict[str, dict[str, dict[str, Any]]] = {}
        entity_roots = [content_root]
        presets_root = root / "presets"
        if presets_root.is_dir():
            entity_roots.append(presets_root)
        for entity_root in entity_roots:
            resolved_root = entity_root.resolve()
            for path in sorted(entity_root.rglob("*.json")):
                if not path.resolve().is_relative_to(resolved_root):
                    raise RulesetBundleError(f"entity path escapes bundle: {path}")
                entity = _read_object(path)
                if _positive_int(entity.get("schema_version"), "entity.schema_version") != 1:
                    raise RulesetBundleError(f"unsupported entity schema: {path}")
                kind = _required_text(entity.get("kind"), "entity.kind", _ID_RE)
                entity_id = _required_text(entity.get("id"), "entity.id", _ID_RE)
                _required_text(entity.get("source_ref"), "entity.source_ref")
                automation = _required_text(
                    entity.get("automation_level"), "entity.automation_level"
                )
                if automation not in _AUTOMATION_LEVELS:
                    raise RulesetBundleError(
                        f"invalid automation_level {automation!r}: {path}"
                    )
                _validate_no_executable_content(entity, path)
                _validate_effects(entity, path)
                by_kind = entities.setdefault(kind, {})
                if entity_id in by_kind:
                    raise RulesetBundleError(f"duplicate entity {kind}:{entity_id}")
                by_kind[entity_id] = entity
        if not entities:
            raise RulesetBundleError("ruleset bundle must contain at least one entity")
        return entities

    @staticmethod
    def _validate_references(entities: dict[str, dict[str, dict[str, Any]]]) -> None:
        for source_kind, by_id in entities.items():
            for source_id, entity in by_id.items():
                for node in _walk(entity):
                    for key, raw in node.items():
                        if key == "source_ref":
                            continue
                        refs: list[Any] = []
                        if key.endswith("_ref"):
                            refs = [raw]
                        elif key.endswith("_refs"):
                            if not isinstance(raw, list):
                                raise RulesetBundleError(
                                    f"{source_kind}:{source_id} field {key} must be an array"
                                )
                            refs = raw
                        for ref in refs:
                            value = str(ref or "")
                            if value.count(":") != 1:
                                raise RulesetBundleError(
                                    f"invalid internal reference {value!r} in {source_kind}:{source_id}"
                                )
                            target_kind, target_id = value.split(":", 1)
                            if target_id not in entities.get(target_kind, {}):
                                raise RulesetBundleError(
                                    f"unresolved reference {value!r} in {source_kind}:{source_id}"
                                )

    @staticmethod
    def _select_locale(manifest: RulesetBundleManifest, requested: str) -> str:
        if not str(requested or "").strip():
            return manifest.default_locale
        for candidate in content_locale_candidates(requested):
            if candidate in manifest.supported_locales:
                return candidate
            language = candidate.split("-", 1)[0].lower()
            for supported in manifest.supported_locales:
                if supported.split("-", 1)[0].lower() == language:
                    return supported
        return manifest.default_locale

    @staticmethod
    def _apply_locale(
        root: Path,
        locale: str,
        entities: dict[str, dict[str, dict[str, Any]]],
    ) -> None:
        locale_root = root / "locales" / locale
        if not locale_root.exists():
            return
        for path in sorted(locale_root.rglob("*.json")):
            overlay = _read_object(path)
            if _positive_int(
                overlay.get("locale_schema_version"), "locale_schema_version"
            ) != 1:
                raise RulesetBundleError(f"unsupported locale schema: {path}")
            if str(overlay.get("locale") or "") != locale:
                raise RulesetBundleError(f"locale overlay identity mismatch: {path}")
            target = overlay.get("target")
            if not isinstance(target, dict):
                raise RulesetBundleError(f"locale target must be an object: {path}")
            kind = _required_text(target.get("kind"), "locale.target.kind", _ID_RE)
            entity_id = _required_text(target.get("id"), "locale.target.id", _ID_RE)
            entity = entities.get(kind, {}).get(entity_id)
            if entity is None:
                raise RulesetBundleError(
                    f"locale overlay target does not exist: {kind}:{entity_id}"
                )
            fields = overlay.get("fields")
            if not isinstance(fields, dict):
                raise RulesetBundleError(f"locale fields must be an object: {path}")
            forbidden = set(fields).difference(_LOCALE_FIELDS)
            if forbidden:
                raise RulesetBundleError(
                    f"locale cannot override mechanics field {sorted(forbidden)[0]!r}: {path}"
                )
            entity.update(deepcopy(fields))
