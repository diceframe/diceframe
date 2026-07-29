from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path

HASH_PREFIX = "pbkdf2_sha256"
HASH_ITERATIONS = 210_000
RESET_FILENAME = "reset_access_password.txt"


def normalize_access_password(value: object) -> str:
    """Treat empty or whitespace-only configuration as no password."""
    return str(value or "").strip()


def hash_access_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), HASH_ITERATIONS)
    return f"{HASH_PREFIX}${HASH_ITERATIONS}${salt}${digest.hex()}"


def is_hashed_access_password(value: str) -> bool:
    return str(value or "").startswith(f"{HASH_PREFIX}$")


def is_valid_access_password(value: object) -> bool:
    stored = normalize_access_password(value)
    if not stored:
        return False
    if not is_hashed_access_password(stored):
        return True
    try:
        prefix, iterations_raw, salt, expected = stored.split("$", 3)
        iterations = int(iterations_raw)
        salt.encode("ascii")
        digest = bytes.fromhex(expected)
    except (UnicodeEncodeError, ValueError, TypeError):
        return False
    return (
        prefix == HASH_PREFIX
        and 1 <= iterations <= 10_000_000
        and bool(salt)
        and len(digest) == hashlib.sha256().digest_size
    )


def verify_access_password(candidate: str, stored: str) -> bool:
    candidate = normalize_access_password(candidate)
    stored = normalize_access_password(stored)
    if not candidate or not is_valid_access_password(stored):
        return False
    if not is_hashed_access_password(stored):
        return hmac.compare_digest(candidate, stored)
    try:
        prefix, iterations_raw, salt, expected = stored.split("$", 3)
        iterations = int(iterations_raw)
    except (ValueError, TypeError):
        return False
    if prefix != HASH_PREFIX or iterations < 1 or not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), salt.encode("ascii"), iterations).hex()
    return hmac.compare_digest(digest, expected)


def mask_access_password(value: str) -> dict[str, object]:
    if not is_valid_access_password(value):
        return {"configured": False, "masked": ""}
    return {"configured": True, "masked": "已设置"}


def reset_file_path(data_dir: Path) -> Path:
    return data_dir / RESET_FILENAME


def consume_reset_password(data_dir: Path) -> str:
    path = reset_file_path(data_dir)
    if not path.exists():
        return ""
    password = path.read_text(encoding="utf-8").strip()
    if len(password) < 6:
        raise RuntimeError(f"{path} 中的新访问密码至少需要 6 位。请修改后重新启动。")
    path.unlink()
    return password
