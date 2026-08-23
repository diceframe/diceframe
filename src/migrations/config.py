"""Configuration migrations kept independent from the web bootstrap."""

from __future__ import annotations

DEFAULT_NARRATIVE_MAX_TOKENS = 2048
GENERATION_DEFAULTS_VERSION = 5
TOKEN_FIELD_MIGRATIONS = (
    ("narrative_max_tokens", frozenset({1024, 1536}), DEFAULT_NARRATIVE_MAX_TOKENS),
    ("analysis_max_tokens", frozenset({512}), 1024),
    ("summary_max_tokens", frozenset({400}), 1024),
    ("brief_max_tokens", frozenset({300}), 1024),
    ("text_gen_max_tokens", frozenset({400}), 1024),
)


def migrate_generation_defaults(config: dict) -> bool:
    try:
        version = int(config.get("generation_defaults_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= GENERATION_DEFAULTS_VERSION:
        return False
    for field, old_defaults, new_default in TOKEN_FIELD_MIGRATIONS:
        missing = min(old_defaults)
        try:
            current = int(config.get(field, missing) or missing)
        except (TypeError, ValueError):
            current = missing
        if current in old_defaults:
            config[field] = new_default
    config["generation_defaults_version"] = GENERATION_DEFAULTS_VERSION
    return True
