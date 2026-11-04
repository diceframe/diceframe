from __future__ import annotations

import pytest

from src.webui.services import legal


def _service(api) -> legal.LegalService:
    return legal.LegalService(legal.LegalDependencies(
        fetch_public_json=api.fetch_public_content_json,
        fetch_public_text=api.fetch_public_content_text,
    ))


@pytest.mark.parametrize("document_name", ["terms", "privacy"])
@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_bundled_legal_documents_are_complete(document_name, language):
    assert len(legal._bundled_text(document_name, language)) > 1000
    assert "DiceFrame" in legal._bundled_text(document_name, language)


def test_bundled_privacy_hashes_match_the_published_content_manifest():
    documents = legal.bundled_documents()

    assert documents["privacy"]["languages"]["zh"]["sha256"] == (
        "2abf7c72212c50e335380d78fedd7f1f82ef325a0dacd9378951aa9df7d31323"
    )
    assert documents["privacy"]["languages"]["en"]["sha256"] == (
        "5163f983721737c6401abf033699b47ec96d6d07d1b26adf9c1efb017beb24ac"
    )


def test_same_legal_revision_with_different_hash_is_rejected():
    state = {}
    bundled = legal.bundled_documents()
    acceptance = legal.acceptance_payload(bundled, "zh-CN")
    documents = legal.bundled_documents()
    documents["privacy"]["languages"]["zh"]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="版本已更新"):
        legal.record_acceptance(
            state,
            acceptance=acceptance,
            documents=documents,
            accepted_at="now",
        )


def test_legal_acceptance_is_date_versioned_and_hashed():
    state = {}
    documents = legal.bundled_documents()
    acceptance = legal.acceptance_payload(documents, "zh-CN")

    assert legal.accepted(state, documents) is False
    legal.record_acceptance(
        state,
        acceptance=acceptance,
        documents=documents,
        accepted_at="2026-08-20T00:00:00Z",
    )

    assert legal.accepted(state, documents) is True
    assert state["legal_accepted_at"] == "2026-08-20T00:00:00Z"
    assert state["legal_terms_accepted_updated_at"] == documents["terms"]["updated_at"]
    assert state["legal_terms_accepted_hash"] == acceptance["terms"]["sha256"]


def test_persisted_acceptance_state_restores_every_recorded_field():
    saved = {
        "legal_terms_accepted_updated_at": "2026-08-11",
        "legal_terms_accepted_version": "1.0",
        "legal_terms_accepted_hash": "a" * 64,
        "legal_terms_accepted_language": "zh",
        "legal_privacy_accepted_updated_at": "2026-08-11",
        "legal_privacy_accepted_version": "1.0",
        "legal_privacy_accepted_hash": "b" * 64,
        "legal_privacy_accepted_language": "zh",
        "legal_accepted_at": "2026-08-11T00:00:00+00:00",
        "unrelated": "must not leak",
    }

    restored = legal.persisted_acceptance_state(saved)

    assert restored == {key: value for key, value in saved.items() if key != "unrelated"}
    assert legal.accepted(restored, legal.bundled_documents()) is False


def test_same_document_date_does_not_require_confirmation_for_formatting_hash_change():
    state = {}
    documents = legal.bundled_documents()
    legal.record_acceptance(
        state,
        acceptance=legal.acceptance_payload(documents, "zh-CN"),
        documents=documents,
        accepted_at="now",
    )
    documents["terms"]["languages"]["zh"]["sha256"] = "0" * 64
    documents["terms"]["languages"]["en"]["sha256"] = "1" * 64

    assert legal.accepted(state, documents) is True


def test_changed_document_date_requires_confirmation():
    state = {}
    documents = legal.bundled_documents()
    legal.record_acceptance(
        state,
        acceptance=legal.acceptance_payload(documents, "zh-CN"),
        documents=documents,
        accepted_at="now",
    )
    documents["privacy"]["updated_at"] = "2026-08-12"

    assert legal.accepted(state, documents) is False


def test_wrong_legal_hash_is_rejected():
    documents = legal.bundled_documents()
    acceptance = legal.acceptance_payload(documents, "zh-CN")
    acceptance["privacy"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="版本已更新"):
        legal.record_acceptance({}, acceptance=acceptance, documents=documents, accepted_at="now")


@pytest.mark.asyncio
async def test_online_document_is_used_only_when_it_matches_manifest_hash():
    documents = legal.bundled_documents()
    manifest = {"documents": documents}

    class Api:
        async def fetch_public_content_json(self, path, **kwargs):
            assert path == "manifest.json"
            assert kwargs == {"force_refresh": True, "allow_cached": False}
            return manifest

        async def fetch_public_content_text(self, path, **kwargs):
            assert kwargs == {"force_refresh": True, "allow_cached": False}
            for document_name, entry in documents.items():
                for language, localized in entry["languages"].items():
                    if localized["path"] == path:
                        return legal._bundled_text(document_name, language)
            return ""

    result = await _service(Api()).document("terms", "zh-CN")
    assert result["source"] == "online"
    assert result["sha256"] == documents["terms"]["languages"]["zh"]["sha256"]


@pytest.mark.asyncio
async def test_bad_online_document_is_not_shown_as_a_bundled_snapshot():
    documents = legal.bundled_documents()

    class Api:
        async def fetch_public_content_json(self, _path, **_kwargs):
            return {"documents": documents}

        async def fetch_public_content_text(self, _path, **_kwargs):
            return "tampered"

    with pytest.raises(legal.LegalContentUnavailable, match="校验失败"):
        await _service(Api()).document("privacy", "en")


@pytest.mark.asyncio
async def test_older_online_manifest_cannot_downgrade_bundled_legal_documents():
    older = legal.bundled_documents()
    for entry in older.values():
        entry["version"] = "1.0"
        entry["updated_at"] = "2026-08-11"
        for language, localized in entry["languages"].items():
            localized["path"] = localized["path"].replace("/1.1/", "/1.0/")

    class Api:
        async def fetch_public_content_json(self, _path, **_kwargs):
            return {"documents": older}

        async def fetch_public_content_text(self, _path, **_kwargs):
            raise AssertionError("older online content must not replace the bundled policy")

    documents = await _service(Api()).current_documents()
    result = await _service(Api()).document("privacy", "zh-CN")
    assert documents["privacy"]["version"] == "1.2"
    assert result["source"] == "bundled"
    assert result["version"] == "1.2"
