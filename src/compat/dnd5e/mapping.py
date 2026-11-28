"""Explicit dnd5e -> dnd2024 mapping tables, catalog access and conversion reports.

Frozen source of truth: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §3.2 (six
rules), §5.2, §6.16 (M2 contract) and §20.3/20.4. This module owns *all* dnd5e
field knowledge inside ``src/compat/dnd5e``; ``src/compat/foundry`` never
learns about dnd5e (§3.2 rule 6).

Mapping policy (no guessing):

- A display name or item identifier is only mapped when an *explicit* table
  entry exists and the target entity is present in the shipped dnd2024
  catalog. Nothing is resolved by fuzzy or localized-name similarity.
- dnd5e item identifiers are canonical content ids. The only normalization
  applied is ``-`` -> ``_`` (the dnd2024 catalog id convention), and an exact
  catalog hit is still required. A handful of ids whose stems genuinely differ
  are listed in :data:`ITEM_IDENTIFIER_ALIASES`.
- Anything without an explicit mapping is preserved verbatim and reported as
  ``saved_not_runnable`` (§3.2 rule 3).

dnd5e field shapes are verified against foundryvtt/dnd5e ``6.1.x``
(``module/data/actor/templates/creature.mjs``, ``module/data/actor/character.mjs``,
``module/data/actor/templates/details.mjs``, ``module/data/shared/traits-field``
data) — see ``tests/fixtures/dnd5e/*.meta.json`` for the pinned sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from src.compat.foundry.report import ConversionIssue, ConversionLevel
from src.rulesets.bundle import LoadedRulesetBundle, RulesetBundleLoader

BUNDLE_ID = "dnd2024_srd"
_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates" / "rulesets"

# dnd5e actor/item document types this converter understands at all.
CHARACTER_ACTOR_TYPES = frozenset({"character"})

# Inventory item types: these can become an item/feat/spell catalog reference.
INVENTORY_ITEM_TYPES = frozenset({
    "weapon", "equipment", "consumable", "tool", "loot", "container",
    "backpack", "ammunition",
})
ACTOR_SCOPED_ITEM_TYPES = frozenset({"class", "subclass", "race", "background", "feat"})
SUPPORTED_ITEM_TYPES = INVENTORY_ITEM_TYPES | {"feat", "spell"}
_SPELL_ITEM_TYPE = "spell"

#: dnd5e race/species identifier (or authoritative English name, only used for
#: pre-identifier exports) -> dnd2024 ``species`` id. ``half-elf`` / ``half-orc``
#: are intentionally absent: SRD 2024 has no equivalent, so they stay preserved.
SPECIES_IDENTIFIERS: dict[str, str] = {
    "human": "human",
    "elf": "elf",
    "dwarf": "dwarf",
    "halfling": "halfling",
    "dragonborn": "dragonborn",
    "gnome": "gnome",
    "tiefling": "tiefling",
    "goliath": "goliath",
    "orc": "orc",
}

#: dnd5e class identifier/name -> dnd2024 ``class`` id.
CLASS_IDENTIFIERS: dict[str, str] = {
    "barbarian": "barbarian",
    "bard": "bard",
    "cleric": "cleric",
    "druid": "druid",
    "fighter": "fighter",
    "monk": "monk",
    "paladin": "paladin",
    "ranger": "ranger",
    "rogue": "rogue",
    "sorcerer": "sorcerer",
    "warlock": "warlock",
    "wizard": "wizard",
}

#: dnd5e background identifier/name -> dnd2024 ``background`` id. Only the four
#: backgrounds present in the shipped SRD 2024 catalog are mapped.
BACKGROUND_IDENTIFIERS: dict[str, str] = {
    "acolyte": "acolyte",
    "criminal": "criminal",
    "sage": "sage",
    "soldier": "soldier",
}

#: dnd5e skill keys are already the dnd2024 skill ids; the explicit table keeps
#: a typo or a future renamed key from silently passing through.
SKILL_IDENTIFIERS: dict[str, str] = {
    key: key
    for key in (
        "acrobatics", "animal_handling", "arcana", "athletics", "deception",
        "history", "insight", "intimidation", "investigation", "medicine",
        "nature", "perception", "performance", "persuasion", "religion",
        "sleight_of_hand", "stealth", "survival",
    )
}

#: dnd5e trait language keys -> dnd2024 ``language`` id. ``ALL`` and custom
#: language strings have no ref and stay preserved.
LANGUAGE_IDENTIFIERS: dict[str, str] = {
    key: key
    for key in (
        "common", "common_sign_language", "draconic", "dwarvish", "elvish",
        "giant", "gnomish", "goblin", "halfling", "orc", "abyssal", "celestial",
        "deep_speech", "druidic", "infernal", "primordial", "sylvan",
        "thieves_cant", "undercommon",
    )
}

#: dnd5e feat identifier -> dnd2024 ``feat`` id (the catalog only ships these).
FEAT_IDENTIFIERS: dict[str, str] = {
    "alert": "alert",
    "lucky": "lucky",
    "magic_initiate_cleric": "magic_initiate_cleric",
    "magic_initiate_druid": "magic_initiate_druid",
    "magic_initiate_wizard": "magic_initiate_wizard",
    "savage_attacker": "savage_attacker",
    "skilled": "skilled",
}

#: dnd5e CONFIG.DND5E.alignments keys (and their full English labels) ->
#: dnd2024 ``ALIGNMENTS`` vocabulary.
ALIGNMENT_IDENTIFIERS: dict[str, str] = {
    "lg": "lawful_good", "lawful_good": "lawful_good",
    "ng": "neutral_good", "neutral_good": "neutral_good",
    "cg": "chaotic_good", "chaotic_good": "chaotic_good",
    "ln": "lawful_neutral", "lawful_neutral": "lawful_neutral",
    "n": "neutral", "neutral": "neutral",
    "cn": "chaotic_neutral", "chaotic_neutral": "chaotic_neutral",
    "le": "lawful_evil", "lawful_evil": "lawful_evil",
    "ne": "neutral_evil", "neutral_evil": "neutral_evil",
    "ce": "chaotic_evil", "chaotic_evil": "chaotic_evil",
}

#: dnd5e item identifiers whose stem differs from the dnd2024 catalog id.
ITEM_IDENTIFIER_ALIASES: dict[str, str] = {
    "leather": "leather_armor",
    "studded_leather": "studded_leather_armor",
}


class Dnd5eConversionError(ValueError):
    """Raised when a dnd5e document is too malformed to convert safely."""

    def __init__(self, message: str, *, error_code: str = "dnd5e_malformed") -> None:
        super().__init__(message)
        self.error_code = error_code


def normalize_key(value: Any) -> str:
    """Canonical table key: lowercase, trimmed, spaces/hyphens as underscores."""

    text = str(value or "").strip().lower()
    if not text:
        return ""
    collapsed = text.replace("-", "_").replace(" ", "_")
    return "_".join(part for part in collapsed.split("_") if part)


@dataclass(frozen=True, slots=True)
class Dnd2024Catalog:
    """Read-only view over the shipped dnd2024 bundle used for ref validation."""

    bundle: LoadedRulesetBundle
    spell_ids: frozenset[str]

    def has(self, kind: str, entity_id: str) -> bool:
        if kind == "spell":
            return entity_id in self.spell_ids
        return bool(entity_id) and entity_id in self.bundle.entities.get(kind, {})

    def name(self, kind: str, entity_id: str) -> str:
        if kind == "spell":
            for row in self._spell_rows():
                if str(row.get("id") or "") == entity_id:
                    return str(row.get("name") or entity_id)
            return entity_id
        entity = self.bundle.entities.get(kind, {}).get(entity_id) or {}
        return str(entity.get("name") or entity_id)

    def ids(self, kind: str) -> frozenset[str]:
        if kind == "spell":
            return self.spell_ids
        return frozenset(self.bundle.entities.get(kind, {}))

    def _spell_rows(self) -> list[dict[str, Any]]:
        catalog = self.bundle.entities.get("spell_catalog", {}).get("srd_spells") or {}
        rows = catalog.get("spells") or []
        return [row for row in rows if isinstance(row, dict)]


def _build_catalog(bundle: LoadedRulesetBundle) -> Dnd2024Catalog:
    catalog = bundle.entities.get("spell_catalog", {}).get("srd_spells") or {}
    spell_ids = frozenset(
        str(row.get("id") or "")
        for row in (catalog.get("spells") or [])
        if isinstance(row, dict) and row.get("id")
    )
    return Dnd2024Catalog(bundle=bundle, spell_ids=spell_ids)


_CATALOG_CACHE: dict[str, Dnd2024Catalog] = {}


def default_catalog(locale: str = "") -> Dnd2024Catalog:
    """The shipped dnd2024 catalog, materialized once per requested locale."""

    cache_key = str(locale or "").replace("_", "-")
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None:
        return cached
    bundle = RulesetBundleLoader(_TEMPLATES_DIR).load(BUNDLE_ID, locale)
    built = _build_catalog(bundle)
    _CATALOG_CACHE[cache_key] = built
    return built


def map_species(raw: Any, catalog: Dnd2024Catalog) -> str | None:
    return _map_entity(raw, SPECIES_IDENTIFIERS, "species", catalog)


def map_class(raw: Any, catalog: Dnd2024Catalog) -> str | None:
    return _map_entity(raw, CLASS_IDENTIFIERS, "class", catalog)


def map_background(raw: Any, catalog: Dnd2024Catalog) -> str | None:
    return _map_entity(raw, BACKGROUND_IDENTIFIERS, "background", catalog)


def map_skill(raw: Any, catalog: Dnd2024Catalog) -> str | None:
    return _map_entity(raw, SKILL_IDENTIFIERS, "skill", catalog)


def map_language(raw: Any, catalog: Dnd2024Catalog) -> str | None:
    return _map_entity(raw, LANGUAGE_IDENTIFIERS, "language", catalog)


def map_alignment(raw: Any) -> str | None:
    return ALIGNMENT_IDENTIFIERS.get(normalize_key(raw))


def map_item_identifier(raw: Any, catalog: Dnd2024Catalog) -> str | None:
    key = normalize_key(raw)
    if not key:
        return None
    alias = ITEM_IDENTIFIER_ALIASES.get(key)
    if alias and catalog.has("item", alias):
        return alias
    if catalog.has("item", key):
        return key
    return None


def map_spell_identifier(raw: Any, catalog: Dnd2024Catalog) -> str | None:
    key = normalize_key(raw)
    return key if key and catalog.has("spell", key) else None


def map_feat_identifier(raw: Any, catalog: Dnd2024Catalog) -> str | None:
    key = normalize_key(raw)
    if not key:
        return None
    mapped = FEAT_IDENTIFIERS.get(key)
    if mapped and catalog.has("feat", mapped):
        return mapped
    if catalog.has("feat", key):
        return key
    return None


def _map_entity(
    raw: Any,
    table: Mapping[str, str],
    kind: str,
    catalog: Dnd2024Catalog,
) -> str | None:
    # Older exports stored plain names in details.race/class/background; the
    # same explicit table answers those ("Fighter" -> "fighter"), and nothing
    # else. Fuzzy or partial names never resolve.
    key = normalize_key(raw)
    mapped = table.get(key)
    if mapped and catalog.has(kind, mapped):
        return mapped
    return None


@dataclass(slots=True)
class Dnd5eConversionReport:
    """Structured conversion report (workplan §6.16, §3.2/§12 vocabulary).

    ``runtime_reconciliation`` is the rule-4 interface: after m2-02 creates the
    character, the dnd2024 runtime recomputes everything and records declared
    vs recomputed mismatches here. This slice only leaves the field empty.
    """

    source_version: str = "dnd5e"
    issues: list[ConversionIssue] = field(default_factory=list)
    unsupported: list[dict[str, str]] = field(default_factory=list)
    preserved_fields: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    missing_references: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    runtime_reconciliation: list[dict[str, Any]] = field(default_factory=list)
    contains_script: bool = False

    _LEVEL_RANK = {
        ConversionLevel.CONVERTIBLE: 0,
        ConversionLevel.SAVED_NOT_RUNNABLE: 1,
        ConversionLevel.DEGRADED_DISPLAY: 2,
        ConversionLevel.MISSING_EXTERNAL_REF: 3,
        ConversionLevel.MISSING_MODULE_DEPENDENCY: 4,
        ConversionLevel.UNSUPPORTED_RULESET: 5,
        ConversionLevel.UNKNOWN_FUTURE_STRUCTURE: 6,
        ConversionLevel.CONTAINS_SCRIPT: 7,
    }

    @property
    def level(self) -> ConversionLevel:
        current = ConversionLevel.CONVERTIBLE
        for issue in self.issues:
            if self._LEVEL_RANK[issue.level] > self._LEVEL_RANK[current]:
                current = issue.level
        return current

    def add(self, level: ConversionLevel, path: str, detail: str) -> None:
        issue = ConversionIssue(level=level, path=path, detail=detail)
        if issue not in self.issues:
            self.issues.append(issue)
        if level is ConversionLevel.CONTAINS_SCRIPT:
            self.contains_script = True

    def keep(self, path: str, detail: str, *, kind: str = "unsupported") -> None:
        """Preserve an unmapped structure as inert data (saved, not runnable)."""

        self.add(ConversionLevel.SAVED_NOT_RUNNABLE, path, detail)
        entry = {"path": path, "kind": kind, "detail": detail}
        if entry not in self.unsupported:
            self.unsupported.append(entry)
        self.preserve(path)

    def preserve(self, path: str) -> None:
        if path not in self.preserved_fields:
            self.preserved_fields.append(path)

    def unknown(self, path: str) -> None:
        if path not in self.unknown_fields:
            self.unknown_fields.append(path)

    def missing(self, kind: str, path: str, external_id: str = "") -> None:
        entry = {"kind": kind, "path": path, "external_id": external_id}
        if entry not in self.missing_references:
            self.missing_references.append(entry)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_version": self.source_version,
            "level": self.level.value,
            "issues": [issue.to_dict() for issue in self.issues],
            "unsupported": [dict(item) for item in self.unsupported],
            "preserved_fields": list(self.preserved_fields),
            "unknown_fields": list(self.unknown_fields),
            "missing_references": [dict(item) for item in self.missing_references],
            "warnings": list(self.warnings),
            "runtime_reconciliation": [dict(item) for item in self.runtime_reconciliation],
            "contains_script": self.contains_script,
        }


__all__ = [
    "ACTOR_SCOPED_ITEM_TYPES",
    "ALIGNMENT_IDENTIFIERS",
    "BACKGROUND_IDENTIFIERS",
    "BUNDLE_ID",
    "CHARACTER_ACTOR_TYPES",
    "CLASS_IDENTIFIERS",
    "Dnd2024Catalog",
    "Dnd5eConversionError",
    "Dnd5eConversionReport",
    "FEAT_IDENTIFIERS",
    "INVENTORY_ITEM_TYPES",
    "ITEM_IDENTIFIER_ALIASES",
    "LANGUAGE_IDENTIFIERS",
    "SKILL_IDENTIFIERS",
    "SPECIES_IDENTIFIERS",
    "SUPPORTED_ITEM_TYPES",
    "default_catalog",
    "map_alignment",
    "map_background",
    "map_class",
    "map_feat_identifier",
    "map_item_identifier",
    "map_language",
    "map_skill",
    "map_species",
    "map_spell_identifier",
    "normalize_key",
]