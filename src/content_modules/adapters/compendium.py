"""FVTT Compendium -> DiceFrame content drafts (workplan §6.17 m3-01).

A compendium pack source (JSON array, JSONL, YAML) is pushed as one sync
item with format ``foundry-compendium``. It becomes:

- one Book (``lorebook_v3``) per JournalEntry whose text pages are its
  entries;
- one Book holding every Item and Actor (NPC) as a typed entry;
- Scene records map to ``scene`` drafts whose payload is the portable
  Tactical Scene definition (``tactical-scene-v1`` + TacticalSceneDocument);
  the per-game creation is an explicit GM action, never a pack commit;
- RollTable, Playlist, Macro and unknown document shapes are reported
  ``unsupported`` with their raw document kept in the import report.

Mechanics are never exchanged: ``system``, embedded items, effects and token
prototypes stay behind and are reported through provenance. Nothing is
executed; scripts are flagged and dropped.

Commit reuses the Lorebook kind importer this module wraps, so preview,
``plan_digest`` revalidation, ``update``/``duplicate``/``skip`` decisions and
the atomic writes are the existing pipeline, not a second one.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from src.compat.foundry.compendium import (
    CODE_COMPENDIUM_FORMAT_UNSUPPORTED,
    CODE_COMPENDIUM_MALFORMED,
    CompendiumSource,
    FoundryCompendiumError,
    FoundryRecord,
    parse_compendium_source,
)
from src.content_modules.adapters.foundry_scene import (
    PORTABLE_SCENE_SCHEMA,
    portable_scene,
)
from src.content_modules.plan import content_digest
from src.content_modules.refs import ContentDraft, ContentRef
from src.content_modules.sync import KindImporter, KindPlan, SyncItem, SyncItemError, SyncSource
from src.engine.world.contracts import canonical_id

COMPENDIUM_FORMAT = "foundry-compendium"
LOREBOOK_V3_SPEC = "lorebook_v3"

CODE_FORMAT_UNSUPPORTED = "FOUNDRY_COMPENDIUM_FORMAT_UNSUPPORTED"
CODE_MALFORMED = "FOUNDRY_COMPENDIUM_MALFORMED"
CODE_NOTHING_IMPORTABLE = "FOUNDRY_COMPENDIUM_NOTHING_IMPORTABLE"

CODE_UNSUPPORTED_RECORD = "FOUNDRY_COMPENDIUM_UNSUPPORTED"
CODE_SCRIPT_IGNORED = "FOUNDRY_COMPENDIUM_SCRIPT_IGNORED"
CODE_EMBEDDED_DATA_REJECTED = "FOUNDRY_COMPENDIUM_EMBEDDED_DATA_REJECTED"
CODE_PAGE_UNSUPPORTED = "FOUNDRY_COMPENDIUM_PAGE_UNSUPPORTED"

#: Document shape -> portable content kind. The kind's owner decides storage:
#: a Journal is a Book, Item/NPC are Book entries (content_package authority).
CONTENT_KIND_BY_DOCUMENT: dict[str, str] = {
    "JournalEntry": "lorebook",
    "Item": "item",
    "Actor": "npc",
    "Scene": "scene",
}

UNSUPPORTED_DOCUMENTS: dict[str, str] = {
    "RollTable": "a random table has no portable DiceFrame content kind",
    "Playlist": "a playlist has no portable DiceFrame content kind",
    "Macro": "macros are executable and are never imported",
}

_REF_KIND_BY_DOCUMENT = {"Actor": "npc", "Item": "item", "JournalEntry": "lorebook"}
_UUID_REF = re.compile(r"@UUID\[(Actor|Item|JournalEntry)\.([A-Za-z0-9_-]+)")

#: Ever copied into a draft: they are runtime mechanics, not narrative content.
_MECHANICAL_FIELDS = frozenset({
    "system", "items", "effects", "prototypeToken", "ownership", "permission", "delta",
})
#: Already read into the draft payload; not repeated inside the extensions block.
_READ_FIELDS = frozenset({"_id", "name", "type", "pages"})


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def prose(value: Any) -> str:
    """Fvtt journal/item text is HTML; DiceFrame stores plain text."""

    parser = _HtmlText()
    parser.feed(str(value or ""))
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


@dataclass(frozen=True, slots=True)
class UnsupportedRecord:
    """One document kept out of the import, raw text included in the report."""

    document_type: str
    document_id: str
    name: str
    reason: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_type": self.document_type,
            "document_id": self.document_id,
            "name": self.name,
            "reason": self.reason,
            "raw": copy.deepcopy(self.raw),
        }


@dataclass(frozen=True, slots=True)
class CompendiumMapping:
    """Per-record mapping result: neutral drafts plus the §12 report."""

    pack_name: str
    source_format: str
    drafts: tuple[ContentDraft, ...] = ()
    unsupported: tuple[UnsupportedRecord, ...] = ()
    warnings: tuple[dict[str, str], ...] = ()

    def drafts_for(self, kind: str) -> tuple[ContentDraft, ...]:
        return tuple(draft for draft in self.drafts if draft.kind == kind)

    def report(self) -> dict[str, Any]:
        return {
            "pack": self.pack_name,
            "format": self.source_format,
            "drafts": [draft.to_portable_dict() for draft in self.drafts],
            "unsupported": [row.to_dict() for row in self.unsupported],
            "warnings": [dict(row) for row in self.warnings],
        }


# ---- mapping ------------------------------------------------------------------


def _clean_value(value: Any, state: dict[str, bool]) -> Any:
    """JSON-safe copy that never carries an embedded ``data:`` asset."""

    if isinstance(value, str):
        if value.startswith("data:"):
            state["data"] = True
            return ""
        return value
    if isinstance(value, Mapping):
        return {str(key): _clean_value(child, state) for key, child in value.items()}
    if isinstance(value, list):
        return [_clean_value(child, state) for child in value]
    return value


def _foundry_extensions(record: FoundryRecord, *, pack: str, source_format: str) -> dict[str, Any]:
    state = {"data": False}
    unknown = {
        key: _clean_value(value, state)
        for key, value in record.raw.items()
        if key not in _READ_FIELDS and key not in _MECHANICAL_FIELDS
    }
    foundry = {
        "document_type": record.document_type,
        "document_id": record.id,
        "pack": pack,
        "source_format": source_format,
        "unknown_fields": unknown,
        "mechanics": "not_exchanged",
    }
    if record.contains_script:
        foundry["contains_script"] = True
    return {"foundry": foundry, "_rejected_embedded_data": state["data"] or None}


def _references(text: str, *, source_kind: str, source_id: str) -> tuple[ContentRef, ...]:
    refs = []
    for document_type, document_id in _UUID_REF.findall(text):
        kind = _REF_KIND_BY_DOCUMENT.get(document_type)
        if kind is None:
            continue
        refs.append(ContentRef(
            source_kind=source_kind, source_id=source_id, kind=kind, id=document_id, explicit=True,
        ))
    return tuple(dict.fromkeys(refs))


def _system_field(raw: Mapping[str, Any], *keys: str) -> Any:
    system = raw.get("system")
    node: Any = system
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def _item_text(raw: Mapping[str, Any]) -> str:
    description = _system_field(raw, "description")
    if isinstance(description, Mapping):
        description = description.get("value")
    return prose(description)


def _actor_text(raw: Mapping[str, Any]) -> str:
    biography = _system_field(raw, "details", "biography")
    if isinstance(biography, Mapping):
        biography = biography.get("value")
    if biography is None:
        biography = _system_field(raw, "biography")
    return prose(biography)


def _scene_text(raw: Mapping[str, Any]) -> str:
    notes = raw.get("notes")
    parts = []
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, Mapping):
                parts.append(prose(note.get("text")))
    return "\n".join(part for part in parts if part)


def _journal_book(record: FoundryRecord, extensions: dict[str, Any], warnings: list[dict[str, str]]) -> dict[str, Any] | None:
    entries: list[dict[str, Any]] = []
    pages = record.raw.get("pages")
    for index, page in enumerate(pages if isinstance(pages, list) else []):
        if not isinstance(page, Mapping):
            continue
        page_type = str(page.get("type") or "")
        if page_type != "text":
            warnings.append({
                "code": CODE_PAGE_UNSUPPORTED,
                "message": f"journal '{record.name}' page {index} ({page_type or 'unknown'}) has no text entry",
            })
            continue
        text = page.get("text")
        content = prose(text.get("content") if isinstance(text, Mapping) else text)
        if not content:
            continue
        entries.append({
            "id": f"{record.id}:p{index}",
            "name": str(page.get("name") or f"{record.name} {index + 1}").strip(),
            "content": content,
            "keys": [],
            "extensions": {"diceframe": {"type": "journal"}, **copy.deepcopy(extensions)},
        })
    if not entries:
        return None
    return {
        "spec": LOREBOOK_V3_SPEC,
        "data": {"lorebook": {"name": record.name, "entries": entries}},
    }


def _entry_payload(record: FoundryRecord, kind: str, extensions: dict[str, Any]) -> dict[str, Any]:
    if kind == "item":
        return {
            "name": record.name,
            "content": _item_text(record.raw),
            "item_type": str(record.raw.get("type") or ""),
            "extensions": extensions,
        }
    if kind == "npc":
        return {
            "name": record.name,
            "content": _actor_text(record.raw),
            "actor_type": str(record.raw.get("type") or ""),
            "extensions": extensions,
        }
    return {"name": record.name, "content": _scene_text(record.raw), "extensions": extensions}


def _scene_payload(record: FoundryRecord) -> tuple[dict[str, Any], dict[str, Any]]:
    """Portable scene definition plus its conversion provenance."""

    document, report = portable_scene(record.raw)
    payload = {
        "name": record.name,
        "content": _scene_text(record.raw),
        "spec": PORTABLE_SCENE_SCHEMA,
        "tactical_scene": document,
    }
    provenance = {
        "source_version": report.source_version,
        "conversion_level": report.level.value,
        "contains_script": report.contains_script,
    }
    return payload, provenance


def _build_draft(
    record: FoundryRecord,
    *,
    kind: str,
    source_kind: str,
    source_id: str,
    pack: str,
    source_format: str,
    warnings: list[dict[str, str]],
) -> ContentDraft | None:
    if kind == "scene":
        payload, conversion = _scene_payload(record)
        reference_text = str(payload["content"])
    else:
        conversion = {}
        extensions = _foundry_extensions(record, pack=pack, source_format=source_format)
        if extensions.pop("_rejected_embedded_data", None):
            warnings.append({
                "code": CODE_EMBEDDED_DATA_REJECTED,
                "message": f"embedded data: asset in '{record.name}' was dropped",
            })
        if kind == "lorebook":
            payload = _journal_book(record, extensions, warnings)
            if payload is None:
                return None
            reference_text = "\n".join(
                str(entry.get("content") or "") for entry in payload["data"]["lorebook"]["entries"]
            )
        else:
            payload = _entry_payload(record, kind, extensions)
            reference_text = str(payload.get("content") or "")
    return ContentDraft(
        kind=kind,
        source_kind=source_kind,
        source_id=source_id,
        external_id=canonical_id(record.id, field="foundry document id"),
        payload=payload,
        references=_references(reference_text, source_kind=source_kind, source_id=source_id),
        provenance={
            "adapter": COMPENDIUM_FORMAT,
            "document_type": record.document_type,
            "document_id": record.id,
            "pack": pack,
            "source_format": source_format,
            **conversion,
        },
        digest=content_digest(payload),
    )


def map_compendium(
    source: CompendiumSource, *, source_kind: str, source_id: str, pack_name: str = "",
) -> CompendiumMapping:
    """Map parsed records to drafts; unmappable ones keep their raw document."""

    pack = pack_name or source.pack_name
    drafts: list[ContentDraft] = []
    unsupported: list[UnsupportedRecord] = []
    warnings: list[dict[str, str]] = []
    for record in source.records:
        if record.contains_script:
            warnings.append({
                "code": CODE_SCRIPT_IGNORED,
                "message": f"{record.document_type or 'document'} '{record.name}' contains executable content; it was not run",
            })
        kind = CONTENT_KIND_BY_DOCUMENT.get(record.document_type)
        if kind is None:
            unsupported.append(UnsupportedRecord(
                document_type=record.document_type or "Unknown",
                document_id=record.id,
                name=record.name,
                reason=UNSUPPORTED_DOCUMENTS.get(
                    record.document_type, "the document shape has no portable DiceFrame content kind",
                ),
                raw=record.raw,
            ))
            continue
        try:
            draft = _build_draft(
                record, kind=kind, source_kind=source_kind, source_id=source_id,
                pack=pack, source_format=source.source_format, warnings=warnings,
            )
        except ValueError as exc:
            unsupported.append(UnsupportedRecord(
                document_type=record.document_type, document_id=record.id,
                name=record.name, reason=str(exc), raw=record.raw,
            ))
            continue
        if draft is None:
            unsupported.append(UnsupportedRecord(
                document_type=record.document_type, document_id=record.id,
                name=record.name, reason="no text content to import", raw=record.raw,
            ))
            continue
        drafts.append(draft)
    return CompendiumMapping(
        pack_name=pack,
        source_format=source.source_format,
        drafts=tuple(drafts),
        unsupported=tuple(unsupported),
        warnings=tuple(warnings),
    )


# ---- Book documents for the existing Lorebook import --------------------------


def _group_name(pack: str, kinds: set[str]) -> str:
    scope = "items and npcs" if len(kinds) > 1 else ("items" if "item" in kinds else "npcs")
    return f"{pack}: {scope}" if pack else f"Compendium {scope}"


def _group_document(pack: str, drafts: tuple[ContentDraft, ...]) -> dict[str, Any]:
    kinds = {draft.kind for draft in drafts}
    entries = []
    for draft in drafts:
        payload = draft.payload
        entries.append({
            "id": draft.external_id,
            "name": payload["name"],
            "content": payload["content"],
            "keys": [],
            "extensions": {"diceframe": {"type": draft.kind}, **copy.deepcopy(payload["extensions"])},
        })
    return {
        "spec": LOREBOOK_V3_SPEC,
        "data": {"lorebook": {"name": _group_name(pack, kinds), "entries": entries}},
    }


def book_documents(mapping: CompendiumMapping) -> tuple[tuple[str, dict[str, Any]], ...]:
    """``(ref suffix, lorebook_v3 document)`` per Book this pack produces."""

    pack = mapping.pack_name
    books: list[tuple[str, dict[str, Any]]] = []
    for draft in mapping.drafts_for("lorebook"):
        books.append((f"journal.{draft.external_id}", copy.deepcopy(draft.payload)))
    stored = mapping.drafts_for("item") + mapping.drafts_for("npc")
    if stored:
        books.append(("items", _group_document(pack, stored)))
    return tuple(books)


def _book_ref(base: str, suffix: str) -> str:
    ref = f"{base}.{suffix}"
    if len(ref) <= 120:
        return ref
    digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:24]
    return f"{base[:80]}.{suffix[:12]}.{digest}"


# ---- importer -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PreparedBook:
    ref: str
    item: SyncItem
    prepared: Any


@dataclass(frozen=True, slots=True)
class PreparedCompendium:
    mapping: CompendiumMapping
    books: tuple[_PreparedBook, ...]


class CompendiumSyncImporter:
    """Kind importer for ``foundry-compendium`` items on the lorebook slot.

    The wrapped Lorebook importer keeps owning every non-compendium document,
    so registering this class changes nothing for ordinary lorebook pushes.
    """

    def __init__(self, lorebook: KindImporter) -> None:
        self.lorebook = lorebook

    def lock(self):
        return self.lorebook.lock()

    def prepare(self, source: SyncSource, item: SyncItem):
        if item.format != COMPENDIUM_FORMAT:
            return self.lorebook.prepare(source, item)
        try:
            parsed = parse_compendium_source(item.document)
        except FoundryCompendiumError as exc:
            code = CODE_MALFORMED if exc.code == CODE_COMPENDIUM_MALFORMED else CODE_FORMAT_UNSUPPORTED
            raise SyncItemError(code, str(exc), client_ref=item.client_ref) from exc
        mapping = map_compendium(parsed, source_kind=source.kind, source_id=source.id)
        books = self._prepare_books(source, item, mapping)
        if not books:
            raise SyncItemError(
                CODE_NOTHING_IMPORTABLE,
                "this compendium has no book content that can be stored here "
                "(scene definitions are imported into one game by the GM; "
                "unmapped documents are reported)",
                client_ref=item.client_ref,
            )
        return PreparedCompendium(mapping=mapping, books=books)

    def _prepare_books(
        self, source: SyncSource, item: SyncItem, mapping: CompendiumMapping,
    ) -> tuple[_PreparedBook, ...]:
        documents = book_documents(mapping)
        hint = item.canonical_hint if len(documents) == 1 else ""
        books = []
        for suffix, document in documents:
            ref = _book_ref(str(item.client_ref), suffix)
            synthetic = SyncItem(
                client_ref=ref, kind="lorebook", format=LOREBOOK_V3_SPEC,
                document=document, canonical_hint=hint,
            )
            books.append(_PreparedBook(ref=ref, item=synthetic, prepared=self.lorebook.prepare(source, synthetic)))
        return tuple(books)

    def plan(self, source: SyncSource, item: SyncItem, prepared: Any) -> KindPlan:
        if not isinstance(prepared, PreparedCompendium):
            return self.lorebook.plan(source, item, prepared)
        entries: list[tuple[str, Any]] = []
        plans: list[KindPlan] = []
        for book in prepared.books:
            kind_plan = self.lorebook.plan(source, book.item, book.prepared)
            entries.extend(kind_plan.entries)
            plans.append(kind_plan)

        def execute(resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for kind_plan in plans:
                rows.extend(kind_plan.execute(resolved))
            return rows

        warnings = [dict(row) for row in prepared.mapping.warnings]
        warnings.extend({"code": CODE_UNSUPPORTED_RECORD, "message": (
            f"{row.document_type} '{row.name}' was kept in the import report only: {row.reason}"
        )} for row in prepared.mapping.unsupported)
        for kind_plan in plans:
            warnings.extend(kind_plan.warnings)
        return KindPlan(entries=entries, execute=execute, warnings=warnings)


__all__ = [
    "CODE_EMBEDDED_DATA_REJECTED",
    "CODE_FORMAT_UNSUPPORTED",
    "CODE_MALFORMED",
    "CODE_NOTHING_IMPORTABLE",
    "CODE_PAGE_UNSUPPORTED",
    "CODE_SCRIPT_IGNORED",
    "CODE_UNSUPPORTED_RECORD",
    "COMPENDIUM_FORMAT",
    "CONTENT_KIND_BY_DOCUMENT",
    "CompendiumMapping",
    "CompendiumSyncImporter",
    "PreparedCompendium",
    "UNSUPPORTED_DOCUMENTS",
    "UnsupportedRecord",
    "book_documents",
    "map_compendium",
    "prose",
]