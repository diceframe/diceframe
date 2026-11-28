"""Conversion levels and reports for the FVTT scene adapter (B3).

Frozen source of truth: ``docs/internal/FVTT_COMPAT_WORKPLAN.md`` §6.6
(降级语义) and §8.2 (不支持内容的处理). A report never changes the converted
document; it only classifies what happened so a preview surface can show it.

Levels are ordered from mildest to most severe. ``contains_script`` is the
highest severity because executable content was found; it is still preserved,
never executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConversionLevel(str, Enum):
    """Stable conversion-level vocabulary (workplan §6.6)."""

    CONVERTIBLE = "convertible"
    SAVED_NOT_RUNNABLE = "saved_not_runnable"
    DEGRADED_DISPLAY = "degraded_display"
    MISSING_EXTERNAL_REF = "missing_external_ref"
    MISSING_MODULE_DEPENDENCY = "missing_module_dependency"
    UNSUPPORTED_RULESET = "unsupported_ruleset"
    UNKNOWN_FUTURE_STRUCTURE = "unknown_future_structure"
    CONTAINS_SCRIPT = "contains_script"


_LEVEL_RANK: dict[ConversionLevel, int] = {
    ConversionLevel.CONVERTIBLE: 0,
    ConversionLevel.SAVED_NOT_RUNNABLE: 1,
    ConversionLevel.DEGRADED_DISPLAY: 2,
    ConversionLevel.MISSING_EXTERNAL_REF: 3,
    ConversionLevel.MISSING_MODULE_DEPENDENCY: 4,
    ConversionLevel.UNSUPPORTED_RULESET: 5,
    ConversionLevel.UNKNOWN_FUTURE_STRUCTURE: 6,
    ConversionLevel.CONTAINS_SCRIPT: 7,
}


@dataclass(frozen=True, slots=True)
class ConversionIssue:
    """One classified conversion observation tied to a payload path."""

    level: ConversionLevel
    path: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level.value, "path": self.path, "detail": self.detail}


@dataclass(slots=True)
class ConversionReport:
    """Structured preview report for one scene import.

    ``missing_references`` entries are ``{"kind", "path", "external_id"}``;
    nothing is auto-created or resolved to satisfy them.
    """

    source_version: str
    issues: list[ConversionIssue] = field(default_factory=list)
    preserved_fields: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    missing_references: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    contains_script: bool = False

    @property
    def level(self) -> ConversionLevel:
        current = ConversionLevel.CONVERTIBLE
        for issue in self.issues:
            if _LEVEL_RANK[issue.level] > _LEVEL_RANK[current]:
                current = issue.level
        return current

    def add(self, level: ConversionLevel, path: str, detail: str) -> None:
        issue = ConversionIssue(level=level, path=path, detail=detail)
        if issue not in self.issues:
            self.issues.append(issue)
        if level is ConversionLevel.CONTAINS_SCRIPT:
            self.contains_script = True

    def preserve(self, field_name: str) -> None:
        if field_name not in self.preserved_fields:
            self.preserved_fields.append(field_name)

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
            "preserved_fields": list(self.preserved_fields),
            "unknown_fields": list(self.unknown_fields),
            "missing_references": [dict(item) for item in self.missing_references],
            "warnings": list(self.warnings),
            "contains_script": self.contains_script,
        }


__all__ = [
    "ConversionIssue",
    "ConversionLevel",
    "ConversionReport",
]