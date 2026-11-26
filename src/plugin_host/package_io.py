"""File operations for plugin packages, separate from installation transactions."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from src.localization.messages import t

from .manifest_validation import ensure_inside, valid_plugin_id
from .package_limits import (
    MAX_PLUGIN_ARCHIVE_FILES,
    MAX_PLUGIN_FILE_BYTES,
    MAX_PLUGIN_PACKAGE_BYTES,
    MAX_PLUGIN_PATH_CHARS,
    MAX_PLUGIN_UNPACKED_BYTES,
)


def _extract_zip(payload: bytes, target_dir: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValueError(t("plugin_host.host.package_not_zip")) from exc
    with archive:
        items = archive.infolist()
        if len(items) > MAX_PLUGIN_ARCHIVE_FILES:
            raise ValueError(t("plugin_host.host.package_too_many_files", max_plugin_archive_files=MAX_PLUGIN_ARCHIVE_FILES))
        total_unpacked = sum(info.file_size for info in items if not info.is_dir())
        if total_unpacked > MAX_PLUGIN_UNPACKED_BYTES:
            raise ValueError(t("plugin_host.host.package_extracted_too_large"))
        seen_paths: set[str] = set()
        for info in items:
            name = info.filename.replace("\\", "/")
            parts = Path(name).parts
            if not name or Path(name).is_absolute() or any(part == ".." for part in parts):
                raise ValueError(t("plugin_host.host.package_path_invalid"))
            if len(name) > MAX_PLUGIN_PATH_CHARS:
                raise ValueError(t("plugin_host.host.package_path_too_long"))
            normalized = "/".join(parts).casefold()
            if normalized in seen_paths:
                raise ValueError(t("plugin_host.host.package_path_duplicate"))
            seen_paths.add(normalized)
            if info.flag_bits & 0x1:
                raise ValueError(t("plugin_host.host.package_encrypted"))
            if info.file_size > MAX_PLUGIN_FILE_BYTES:
                raise ValueError(t("plugin_host.host.package_file_too_large"))
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise ValueError(t("plugin_host.host.package_symlink"))
            resolved = (target_dir / name).resolve()
            ensure_inside(target_dir, resolved)
        archive.extractall(target_dir)


def package_files(plugin_id: str, files: dict[str, str | bytes], *, flat: bool = False) -> bytes:
    """把内存中的插件文件打包成 zip 字节，镜像 _extract_zip 的约束。

    files 的 key 是相对路径。``flat=False``（默认，.dfplugin 用）会自动加
    ``<plugin_id>/`` 前缀；``flat=True``（仓库源码用）不加前缀，plugin.json
    落到根目录，解压即可推到 GitHub。
    """
    if not valid_plugin_id(plugin_id):
        raise ValueError(t("plugin_host.host.plugin_id_invalid"))
    buffer = io.BytesIO()
    seen: set[str] = set()
    total_unpacked = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel, content in files.items():
            name = str(rel or "").replace("\\", "/").strip("/")
            parts = Path(name).parts
            if not name or Path(name).is_absolute() or any(part == ".." for part in parts):
                raise ValueError(t("plugin_host.host.export_path_invalid", rel=rel))
            if not flat and parts[0] != plugin_id:
                name = f"{plugin_id}/{name}"
                parts = Path(name).parts
            if len(name) > MAX_PLUGIN_PATH_CHARS:
                raise ValueError(t("plugin_host.host.export_path_too_long", rel=rel))
            normalized = "/".join(parts).casefold()
            if normalized in seen:
                raise ValueError(t("plugin_host.host.export_path_duplicate", rel=rel))
            seen.add(normalized)
            data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            if len(data) > MAX_PLUGIN_FILE_BYTES:
                raise ValueError(t("plugin_host.host.export_file_too_large", rel=rel))
            total_unpacked += len(data)
            if total_unpacked > MAX_PLUGIN_UNPACKED_BYTES:
                raise ValueError(t("plugin_host.host.export_total_too_large"))
            if len(seen) > MAX_PLUGIN_ARCHIVE_FILES:
                raise ValueError(t("plugin_host.host.export_too_many_files"))
            archive.writestr(name, data)
    payload = buffer.getvalue()
    if len(payload) > MAX_PLUGIN_PACKAGE_BYTES:
        raise ValueError(t("plugin_host.host.export_package_too_large"))
    return payload


def _find_install_root(temp_dir: Path) -> Path:
    if (temp_dir / "plugin.json").exists():
        return temp_dir
    candidates = [path.parent for path in temp_dir.glob("**/plugin.json")]
    if not candidates:
        raise ValueError(t("plugin_host.host.package_manifest_missing"))
    if len(candidates) > 1:
        raise ValueError(t("plugin_host.host.package_multiple_manifests"))
    return candidates[0]


def _atomic_json(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
