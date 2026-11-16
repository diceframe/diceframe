"""Stable, secret-free identities for derived embedding vectors."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit


PROFILE_ALGORITHM_VERSION = "memory-embedding-profile-v1"


def _endpoint_identity(value: object) -> str:
    """Normalize an endpoint without retaining credentials or query secrets."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.rstrip("/")
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def profile_for_client(client: Any) -> str:
    """Return a deterministic profile containing no API key or secret."""

    explicit = getattr(client, "fingerprint", None)
    if callable(explicit):
        value = str(explicit() or "").strip()
        if value:
            return value
    source = str(getattr(client, "source", "external") or "external").strip().lower()
    payload = {
        "algorithm": PROFILE_ALGORITHM_VERSION,
        "source": source,
        "model": str(getattr(client, "model", "") or "").strip(),
        "endpoint": _endpoint_identity(getattr(client, "base_url", "")),
        "max_input_chars": int(getattr(client, "max_input_chars", 0) or 0),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{PROFILE_ALGORITHM_VERSION}:{sha256(encoded.encode('utf-8')).hexdigest()[:32]}"


def content_hash(entity: object, relation: object, value: object) -> str:
    """Hash exactly the canonical text sent to the embedding backend."""

    text = f"{entity}: {relation} → {value}"
    return sha256(text.encode("utf-8")).hexdigest()
