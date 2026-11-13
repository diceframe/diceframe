"""Bounded package-document reader for the D01 experiment.

It reads only an already-installed package path.  It never fetches URLs,
executes PDF actions/scripts, follows symlinks, or treats a GM label as a
confidentiality guarantee for a public package.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
DocumentKind = Literal["markdown", "pdf"]


class DocumentReadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DocumentProjection:
    path: str
    kind: DocumentKind
    available: bool
    visibility: str
    warning: str = ""


def project_document(
    relative_path: str,
    *,
    kind: str,
    visibility: str = "public",
    viewer_is_gm: bool = False,
) -> DocumentProjection:
    path = str(relative_path or "").replace("\\", "/").strip("/")
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"markdown", "pdf"}:
        raise DocumentReadError("document kind is unsupported")
    if not path or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
        raise DocumentReadError("document path is invalid")
    normalized_visibility = str(visibility or "public").strip().lower()
    if normalized_visibility not in {"public", "gm"}:
        raise DocumentReadError("document visibility is unsupported")
    if normalized_visibility == "gm" and not viewer_is_gm:
        return DocumentProjection(
            path, normalized_kind, False, normalized_visibility,
            "GM-only labels do not make a public package confidential",
        )
    return DocumentProjection(path, normalized_kind, True, normalized_visibility)


def _ensure_inside(root: Path, target: Path) -> None:
    root = root.resolve()
    target = target.resolve()
    if target != root and root not in target.parents:
        raise DocumentReadError("document path escapes package")
    current = target
    while current != root:
        if current.is_symlink():
            raise DocumentReadError("document path contains a symlink")
        current = current.parent


def read_document(
    package_root: Path,
    relative_path: str,
    *,
    kind: str,
) -> bytes | str:
    root = Path(package_root).resolve()
    projection = project_document(relative_path, kind=kind)
    target = (root / projection.path).resolve()
    _ensure_inside(root, target)
    if not target.is_file() or target.is_symlink():
        raise DocumentReadError("document is missing or not a regular file")
    size = target.stat().st_size
    if size > MAX_DOCUMENT_BYTES:
        raise DocumentReadError("document exceeds the size limit")
    data = target.read_bytes()
    if projection.kind == "pdf":
        if not data.startswith(b"%PDF-"):
            raise DocumentReadError("document is not a PDF")
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentReadError("markdown document is not UTF-8") from exc
