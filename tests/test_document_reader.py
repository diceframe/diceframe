from pathlib import Path

import pytest

from src.plugin_host.document_reader import (
    DocumentReadError,
    project_document,
    read_document,
)


def test_document_reader_is_bounded_and_projects_gm_visibility(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Harbor", encoding="utf-8")
    (tmp_path / "guide.pdf").write_bytes(b"%PDF-1.7 fake")
    assert read_document(tmp_path, "README.md", kind="markdown") == "# Harbor"
    assert read_document(tmp_path, "guide.pdf", kind="pdf").startswith(b"%PDF-")
    hidden = project_document("secret.md", kind="markdown", visibility="gm")
    assert hidden.available is False
    assert "confidential" in hidden.warning


def test_document_reader_rejects_escape_symlink_and_bad_pdf(tmp_path: Path):
    with pytest.raises(DocumentReadError):
        read_document(tmp_path, "../secret.md", kind="markdown")
    (tmp_path / "bad.pdf").write_bytes(b"not pdf")
    with pytest.raises(DocumentReadError, match="PDF"):
        read_document(tmp_path, "bad.pdf", kind="pdf")
    outside = tmp_path.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(DocumentReadError, match="symlink"):
        read_document(tmp_path, "link.md", kind="markdown")
