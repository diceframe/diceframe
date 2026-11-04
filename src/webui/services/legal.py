"""Versioned public legal documents and the local record of user acceptance."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


LEGAL_VERSION = "1.1"
LEGAL_UPDATED_AT = "2026-08-20"
_DOCUMENT_METADATA = {
    "terms": {"version": LEGAL_VERSION, "updated_at": LEGAL_UPDATED_AT},
    "privacy": {"version": "1.2", "updated_at": "2026-08-21"},
}
_LEGAL_DIR = Path(__file__).resolve().parents[3] / "legal"
_DOCUMENTS = {
    ("terms", "zh"): "TERMS_OF_SERVICE_CN.md",
    ("terms", "en"): "TERMS_OF_SERVICE_EN.md",
    ("privacy", "zh"): "PRIVACY_POLICY_CN.md",
    ("privacy", "en"): "PRIVACY_POLICY_EN.md",
}
_DOCUMENT_NAMES = ("terms", "privacy")
_PERSISTED_ACCEPTANCE_FIELDS = tuple(
    f"legal_{name}_accepted_{suffix}"
    for name in _DOCUMENT_NAMES
    for suffix in ("updated_at", "version", "hash", "language")
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LegalContentUnavailable(RuntimeError):
    """The current online legal original could not be retrieved and verified."""


@dataclass(frozen=True)
class LegalDependencies:
    fetch_public_json: Callable[..., Awaitable[dict[str, Any] | None]]
    fetch_public_text: Callable[..., Awaitable[str]]


def persisted_acceptance_state(saved: dict[str, Any]) -> dict[str, str]:
    """Restore every legal-acceptance field that record_acceptance writes."""
    return {
        field: str(saved.get(field) or "")
        for field in (*_PERSISTED_ACCEPTANCE_FIELDS, "legal_accepted_at")
    }


def _language(value: str) -> str:
    return "zh" if (value or "").lower().startswith("zh") else "en"


def _bundled_text(document_name: str, language: str) -> str:
    key = (document_name, _language(language))
    filename = _DOCUMENTS.get(key)
    if filename is None:
        raise KeyError(document_name)
    return (_LEGAL_DIR / filename).read_text(encoding="utf-8")


def bundled_documents() -> dict[str, Any]:
    """Return the immutable legal snapshot shipped in this DiceFrame release."""
    documents: dict[str, Any] = {}
    for document_name in _DOCUMENT_NAMES:
        metadata = _DOCUMENT_METADATA[document_name]
        languages: dict[str, Any] = {}
        for language in ("zh", "en"):
            text = _bundled_text(document_name, language)
            languages[language] = {
                "path": f"legal/{document_name}/{metadata['version']}/{language}.md",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        documents[document_name] = {
            "version": metadata["version"],
            "updated_at": metadata["updated_at"],
            "languages": languages,
        }
    return documents


def _valid_remote_documents(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    raw_documents = manifest.get("documents") if isinstance(manifest, dict) else None
    if not isinstance(raw_documents, dict):
        return None
    parsed: dict[str, Any] = {}
    for document_name in _DOCUMENT_NAMES:
        entry = raw_documents.get(document_name)
        if not isinstance(entry, dict):
            return None
        version = entry.get("version")
        updated_at = entry.get("updated_at")
        raw_languages = entry.get("languages")
        if (
            not isinstance(version, str)
            or not version
            or not isinstance(updated_at, str)
            or not _DATE.fullmatch(updated_at)
            or not isinstance(raw_languages, dict)
        ):
            return None
        languages: dict[str, Any] = {}
        for language in ("zh", "en"):
            localized = raw_languages.get(language)
            if not isinstance(localized, dict):
                return None
            path = localized.get("path")
            digest = localized.get("sha256")
            expected_path = f"legal/{document_name}/{version}/{language}.md"
            if path != expected_path or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                return None
            languages[language] = {"path": path, "sha256": digest}
        parsed[document_name] = {"version": version, "updated_at": updated_at, "languages": languages}
    return parsed


async def _remote_documents(
    dependencies: LegalDependencies, *, allow_cached: bool,
) -> dict[str, Any] | None:
    return _valid_remote_documents(
        await dependencies.fetch_public_json(
            "manifest.json",
            force_refresh=True,
            allow_cached=allow_cached,
        )
    )


def _select_documents(remote: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    bundled = bundled_documents()
    if remote is not None:
        # Hub 清单缺任一文档时整体回退内置快照，避免 `remote[name]` KeyError 500；
        # 且仅当所有远端文档都不比内置旧时才用远端（防降级）。
        remote_fresh = True
        for name in _DOCUMENT_NAMES:
            entry = remote.get(name)
            bundled_entry = bundled.get(name)
            if not isinstance(entry, dict) or not isinstance(bundled_entry, dict):
                remote_fresh = False
                break
            if entry.get("updated_at") < bundled_entry.get("updated_at"):
                remote_fresh = False
                break
        if remote_fresh:
            return remote, "online"
    return bundled, "bundled"


async def current_documents(dependencies: LegalDependencies) -> dict[str, Any]:
    """Use the newest reachable manifest; retain the release snapshot for offline startup."""
    remote = await _remote_documents(dependencies, allow_cached=True)
    return _select_documents(remote)[0]


async def document(
    dependencies: LegalDependencies, document_name: str, language: str,
) -> dict[str, Any]:
    if document_name not in _DOCUMENT_NAMES:
        raise KeyError(document_name)
    selected_language = _language(language)
    documents, source = _select_documents(
        await _remote_documents(dependencies, allow_cached=False)
    )
    metadata = documents[document_name]
    localized = metadata["languages"][selected_language]
    if source == "bundled":
        text = _bundled_text(document_name, selected_language)
    else:
        text = await dependencies.fetch_public_text(
            localized["path"],
            force_refresh=True,
            allow_cached=False,
        )
    if not text or hashlib.sha256(text.encode("utf-8")).hexdigest() != localized["sha256"]:
        raise LegalContentUnavailable("当前法律原文校验失败")
    return {
        "ok": True,
        "document": document_name,
        "language": selected_language,
        "version": metadata["version"],
        "updated_at": metadata["updated_at"],
        "sha256": localized["sha256"],
        "source": source,
        "content": text,
    }


def acceptance_payload(documents: dict[str, Any], language: str) -> dict[str, dict[str, str]]:
    selected_language = _language(language)
    return {
        name: {
            "version": str(documents[name]["version"]),
            "updated_at": str(documents[name]["updated_at"]),
            "language": selected_language,
            "sha256": str(documents[name]["languages"][selected_language]["sha256"]),
        }
        for name in _DOCUMENT_NAMES
    }


def accepted(state: dict[str, Any], documents: dict[str, Any] | None = None) -> bool:
    expected = documents or bundled_documents()
    for name in _DOCUMENT_NAMES:
        entry = expected[name]
        accepted_date = state.get(f"legal_{name}_accepted_updated_at")
        if accepted_date is not None:
            if accepted_date != entry["updated_at"]:
                return False
            continue

        # Compatibility for installations that accepted the original version/hash
        # record before document dates became the confirmation boundary.
        if state.get(f"legal_{name}_accepted_version") == entry["version"]:
            saved_hash = state.get(f"legal_{name}_accepted_hash")
            allowed_hashes = {language["sha256"] for language in entry["languages"].values()}
            if saved_hash in allowed_hashes:
                continue
        return False
    return True


def record_acceptance(
    state: dict[str, Any],
    *,
    acceptance: dict[str, Any],
    documents: dict[str, Any],
    accepted_at: str,
) -> None:
    for name in _DOCUMENT_NAMES:
        supplied = acceptance.get(name) if isinstance(acceptance, dict) else None
        if not isinstance(supplied, dict):
            raise ValueError("法律文件版本已更新，请重新阅读后确认")
        language = _language(str(supplied.get("language") or ""))
        expected = documents[name]
        localized = expected["languages"][language]
        if (
            supplied.get("updated_at") != expected["updated_at"]
            or supplied.get("version") != expected["version"]
            or supplied.get("sha256") != localized["sha256"]
        ):
            raise ValueError("法律文件版本已更新，请重新阅读后确认")
        state[f"legal_{name}_accepted_updated_at"] = expected["updated_at"]
        state[f"legal_{name}_accepted_version"] = expected["version"]
        state[f"legal_{name}_accepted_hash"] = localized["sha256"]
        state[f"legal_{name}_accepted_language"] = language
    state["legal_accepted_at"] = accepted_at


def bundle_version(documents: dict[str, Any]) -> str:
    return "|".join(f"{name}:{documents[name]['updated_at']}" for name in _DOCUMENT_NAMES)


class LegalService:
    """Verified legal originals over an explicit public-content boundary."""

    def __init__(self, dependencies: LegalDependencies) -> None:
        self._dependencies = dependencies

    async def current_documents(self) -> dict[str, Any]:
        return await current_documents(self._dependencies)

    async def document(
        self, document_name: str, language: str,
    ) -> dict[str, Any]:
        return await document(self._dependencies, document_name, language)
