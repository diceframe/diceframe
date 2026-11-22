"""Validate the portable runtime lock before release packaging."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REQUIREMENTS = ROOT / "requirements.txt"
EMBEDDING_REQUIREMENTS = ROOT / "requirements-embedding.txt"
PORTABLE_LOCK = ROOT / "requirements-portable.lock"
_NAME_SEPARATOR = re.compile(r"[-_.]+")
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")
_LOCKED_REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s]+)\s+--hash=sha256:([0-9a-fA-F]{64})\s*$"
)


def normalize_name(value: str) -> str:
    return _NAME_SEPARATOR.sub("-", value).lower()


def direct_requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped or stripped.startswith("-"):
            continue
        match = _REQUIREMENT_NAME.match(stripped)
        if not match:
            raise ValueError(f"无法解析运行依赖：{line}")
        names.add(normalize_name(match.group(1)))
    return names


def locked_requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LOCKED_REQUIREMENT.fullmatch(stripped)
        if not match:
            raise ValueError(f"便携版依赖必须精确固定版本与 SHA-256：{line}")
        name = normalize_name(match.group(1))
        if name in names:
            raise ValueError(f"便携版依赖重复：{name}")
        names.add(name)
    return names


def validate_portable_lock(requirements: Path, lock: Path) -> None:
    direct = direct_requirement_names(requirements)
    locked = locked_requirement_names(lock)
    missing = sorted(direct - locked)
    if missing:
        raise ValueError("便携版依赖锁缺少直接运行依赖：" + ", ".join(missing))


def main() -> int:
    try:
        validate_portable_lock(RUNTIME_REQUIREMENTS, PORTABLE_LOCK)
        validate_portable_lock(EMBEDDING_REQUIREMENTS, PORTABLE_LOCK)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"portable lock valid: {len(locked_requirement_names(PORTABLE_LOCK))} hashed packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
