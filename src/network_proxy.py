"""Shared outbound proxy helpers for API and update HTTP clients."""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit


def env_proxy_url() -> str:
    """Return the first configured outbound proxy from env vars."""
    for name in ("TRPG_PROXY_URL", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def is_supported_proxy_url(url: str) -> bool:
    """aiohttp built-in proxy support covers HTTP(S) proxies."""
    if not url:
        return True
    parsed = urlsplit(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def mask_proxy_url(url: str) -> str:
    """Hide proxy credentials for UI/log output."""
    if not url:
        return ""
    parsed = urlsplit(url)
    netloc = parsed.netloc
    if "@" in netloc:
        auth, host = netloc.rsplit("@", 1)
        user = auth.split(":", 1)[0]
        netloc = (user + ":***@" if user else "***@") + host
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def effective_proxy_url(enabled: bool, configured_url: str) -> str:
    """Return the proxy URL that should be passed to outbound clients."""
    if not enabled:
        return ""
    return (configured_url or env_proxy_url()).strip()
