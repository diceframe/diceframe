from __future__ import annotations

import json

import pytest

from src.webui.services.module_import_adapters import (
    ExternalModuleImportError,
    preview_external_module_import,
    require_human_review,
)


def test_foundry_preview_ignores_executable_fields_and_requires_review() -> None:
    result = preview_external_module_import(
        json.dumps({
            "id": "foundry.castle",
            "title": "Castle",
            "version": "1.2.0",
            "esmodules": ["scripts/boot.js"],
            "packs": [{"label": "Scenes", "name": "scenes"}],
        }).encode(),
        source_name="module.json",
    )

    assert result["ok"] is True
    assert result["format"] == "foundry"
    assert result["review_required"] is True
    assert result["auto_installable"] is False
    assert "foundry_executable_fields_ignored" in result["warnings"]
    assert result["manifest"]["external_packs"] == [{"label": "Scenes"}]

    with pytest.raises(ExternalModuleImportError, match="human_review_required"):
        require_human_review(result)


def test_fantasy_grounds_preview_fails_closed_without_native_id() -> None:
    result = preview_external_module_import(
        b'<root name="Old Keep" version="4.0"><windowclass /></root>',
        source_name="campaign.mod",
    )

    assert result["ok"] is False
    assert result["format"] == "fantasy_grounds"
    assert result["blockers"] == ["module_id_missing"]
    assert result["review_required"] is True

    with pytest.raises(ExternalModuleImportError, match="external_module_draft_blocked"):
        require_human_review(result, approved=True)


def test_unknown_or_non_json_input_is_not_treated_as_a_module() -> None:
    with pytest.raises(ExternalModuleImportError, match="external_module_format_unsupported"):
        preview_external_module_import(b"not a VTT module")

