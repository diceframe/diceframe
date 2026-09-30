from __future__ import annotations

import pytest

from src.content_modules.refs import (
    ContentDraft,
    ContentRefError,
    build_commit_plan,
    collect_content_refs,
)
from src.lorebook.importer import lorebook_content_draft, preview_lorebook_import


def test_content_draft_is_source_aware_and_portable() -> None:
    draft = ContentDraft(
        kind="lorebook",
        source_kind="device",
        source_id="phone-1",
        external_id="book-1",
        payload={"name": "Book"},
    )

    assert draft.ref.to_portable_dict() == {
        "source_kind": "device",
        "source_id": "phone-1",
        "kind": "lorebook",
        "id": "book-1",
        "digest": "",
    }


def test_commit_plan_exposes_the_three_duplicate_choices() -> None:
    draft = ContentDraft("lorebook", "device", "phone-1", "book-1", {})
    existing = [draft.ref]

    assert build_commit_plan([draft], existing=existing, duplicate_policy="update").operations[0].action == "update"
    assert build_commit_plan([draft], existing=existing, duplicate_policy="skip").operations[0].action == "skip"
    assert build_commit_plan([draft], existing=existing, duplicate_policy="duplicate").operations[0].action == "create"


def test_reference_closure_only_reads_explicit_reference_fields() -> None:
    refs = collect_content_refs(
        {"description": "npc:looks_like_prose", "refs": [{"kind": "npc", "id": "guard"}]},
        default_source="device:phone-1",
    )

    assert [ref.to_portable_dict() for ref in refs] == [{
        "source_kind": "device", "source_id": "phone-1", "kind": "npc", "id": "guard", "digest": "",
    }]


def test_reference_closure_rejects_unknown_kind() -> None:
    with pytest.raises(ContentRefError):
        collect_content_refs(
            {"references": ["unknown:thing"]},
            default_source="device:phone-1",
        )


def test_lorebook_preview_contains_shared_draft_and_plan() -> None:
    result = preview_lorebook_import({
        "spec": "lorebook_v3",
        "data": {"lorebook": {"name": "Imported", "entries": []}},
    })

    assert result["content_draft"].kind == "lorebook"
    assert result["commit_plan"].operations[0].action == "create"
