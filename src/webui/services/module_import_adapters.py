"""Safe, preview-first adapters for external VTT module metadata.

External VTT packages are treated as data sources only.  This boundary may
inspect JSON/XML metadata and produce a native-module draft, but it never
loads a VTT runtime, imports executable code, or commits a package.  A human
must explicitly review the draft before a future native-module writer can use
it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree


class ExternalModuleImportError(ValueError):
    """The input cannot be safely represented as a reviewable draft."""


@dataclass(frozen=True)
class ExternalModuleDraft:
    adapter: str
    source_name: str
    manifest: dict[str, Any]
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def review_required(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "source_name": self.source_name,
            "manifest": dict(self.manifest),
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "review_required": self.review_required,
            "auto_installable": False,
        }


def _safe_name(value: Any, fallback: str) -> str:
    name = str(value or "").strip()
    return name[:200] or fallback


def _foundry_draft(payload: dict[str, Any], source_name: str) -> ExternalModuleDraft:
    metadata = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else payload
    module_id = _safe_name(metadata.get("id"), "")
    name = _safe_name(metadata.get("title") or metadata.get("name"), module_id or "Foundry module")
    warnings: list[str] = []
    executable_fields = ("scripts", "esmodules", "scripts", "relationships")
    if any(metadata.get(field) for field in executable_fields):
        warnings.append("foundry_executable_fields_ignored")
    packs = metadata.get("packs") if isinstance(metadata.get("packs"), list) else []
    if packs:
        warnings.append("foundry_compendium_data_requires_explicit_mapping")
    blockers = ["module_id_missing"] if not module_id else []
    return ExternalModuleDraft(
        adapter="foundry",
        source_name=source_name,
        manifest={
            "id": module_id,
            "name": name,
            "version": _safe_name(metadata.get("version"), ""),
            "source_format": "foundry",
            "content_profile": "content-pack",
            "external_packs": [
                {"label": _safe_name(pack.get("label") or pack.get("name"), "pack")}
                for pack in packs
                if isinstance(pack, dict)
            ],
        },
        warnings=tuple(dict.fromkeys(warnings)),
        blockers=tuple(blockers),
    )


def _fantasy_grounds_draft(payload: bytes, source_name: str) -> ExternalModuleDraft:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ExternalModuleImportError("fantasy_grounds_xml_invalid") from exc
    name = _safe_name(root.attrib.get("name"), PurePosixPath(source_name).stem or "Fantasy Grounds module")
    return ExternalModuleDraft(
        adapter="fantasy_grounds",
        source_name=source_name,
        manifest={
            "id": "",
            "name": name,
            "version": _safe_name(root.attrib.get("version"), ""),
            "source_format": "fantasy_grounds",
            "content_profile": "content-pack",
        },
        warnings=("fantasy_grounds_records_require_explicit_mapping",),
        blockers=("module_id_missing",),
    )


def preview_external_module_import(payload: bytes, *, source_name: str = "") -> dict[str, Any]:
    """Convert recognized VTT metadata into a non-installable review draft."""

    name = str(source_name or "external-module").strip() or "external-module"
    stripped = bytes(payload or b"").lstrip()
    if not stripped:
        raise ExternalModuleImportError("external_module_empty")
    if stripped.startswith(b"<"):
        draft = _fantasy_grounds_draft(stripped, name)
    else:
        try:
            document = json.loads(stripped.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalModuleImportError("external_module_format_unsupported") from exc
        if not isinstance(document, dict):
            raise ExternalModuleImportError("external_module_document_invalid")
        if not isinstance(document.get("manifest"), dict) and not any(
            key in document for key in ("id", "title", "name", "packs", "esmodules", "scripts")
        ):
            raise ExternalModuleImportError("external_module_format_unsupported")
        draft = _foundry_draft(document, name)
    result = draft.to_dict()
    result["ok"] = not draft.blockers
    result["format"] = draft.adapter
    return result


def require_human_review(draft: dict[str, Any], *, approved: bool = False) -> None:
    """Guard the future commit boundary; preview output is never installable."""

    if not approved:
        raise ExternalModuleImportError("human_review_required")
    if draft.get("blockers"):
        raise ExternalModuleImportError("external_module_draft_blocked")
