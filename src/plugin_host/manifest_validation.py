"""Plugin package declarations and config schema validation.

These helpers validate and normalize inputs without loading or publishing a
runtime. PluginHost retains the validation order and owns the install transaction.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.localization.messages import t

from .policy import PERMISSION_DETAILS, effective_plugin_permissions
from .support import STATIC_PLUGIN_TYPES, plugin_type_descriptor

_RULEBOOK_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ALLOWED_CONTROLS = {"switch", "text", "secret", "number", "select", "string-list"}


def validate_ruleset_catalogs(
    manifest: dict[str, Any], plugin_dir: Path,
) -> tuple[Path, tuple[str, ...]] | None:
    """Validate manifest ``ruleset_catalogs``（DNDMOD-04，母方案 §7/§107）。

    与 adventure_packages 同规则：相对/不越界/真实目录/共享父目录；目录内
    是 catalog JSON（装载时经 DNDMOD-01 契约校验）。仅 content-pack。
    """

    declared = manifest.get("ruleset_catalogs")
    if declared is None:
        return None
    if str(manifest.get("plugin_type") or "") != "content-pack":
        raise ValueError(t("plugin_host.host.ruleset_catalogs_content_pack_only"))
    if not isinstance(declared, list) or not declared:
        raise ValueError(t("plugin_host.host.ruleset_catalogs_not_string_array"))
    base = plugin_dir.resolve()
    catalogs: list[Path] = []
    for pattern in declared:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(t("plugin_host.host.ruleset_catalogs_path_empty"))
        normalized = pattern.strip().replace("\\", "/")
        candidate = Path(normalized)
        if candidate.is_absolute() or any(part in ("..", "") for part in candidate.parts):
            raise ValueError(t("plugin_host.host.ruleset_catalogs_path_unsafe"))
        resolved = (base / candidate).resolve()
        if resolved != base and base not in resolved.parents:
            raise ValueError(t("plugin_host.host.ruleset_catalogs_path_outside"))
        if resolved.is_symlink() or not resolved.is_dir():
            raise ValueError(t("plugin_host.host.ruleset_catalogs_dir_missing", normalized=normalized))
        catalogs.append(resolved)
    parents = {catalog.parent for catalog in catalogs}
    if len(parents) != 1:
        raise ValueError(t("plugin_host.host.ruleset_catalogs_same_parent"))
    parent = catalogs[0].parent
    directories = tuple(sorted(catalog.name for catalog in catalogs))
    return parent, directories


def validate_prebuilt_ui(
    manifest: dict[str, Any], plugin_dir: Path,
) -> dict[str, Any] | None:
    """Validate an optional package-owned U02 prebuilt UI surface."""

    declared = manifest.get("prebuilt_ui")
    if declared is None:
        return None
    if not isinstance(declared, dict) or set(declared) != {"entrypoint", "assets"}:
        raise ValueError("prebuilt_ui must contain only entrypoint and assets")
    entrypoint = str(declared.get("entrypoint") or "").replace("\\", "/").strip("/")
    assets = declared.get("assets")
    if (
        not entrypoint.endswith((".html", ".htm"))
        or not isinstance(assets, list)
        or len(assets) > 256
    ):
        raise ValueError("prebuilt_ui entrypoint/assets are invalid")
    paths = [entrypoint, *[str(item or "").replace("\\", "/").strip("/") for item in assets]]
    if len(set(paths)) != len(paths):
        raise ValueError("prebuilt_ui contains duplicate paths")
    normalized: list[str] = []
    for relative in paths:
        candidate = Path(relative)
        if (
            not relative
            or len(relative) > 240
            or candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            raise ValueError("prebuilt_ui path is invalid")
        target = (plugin_dir / relative).resolve()
        ensure_inside(plugin_dir, target)
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"prebuilt_ui file is missing: {relative}")
        normalized.append(relative)
    return {"entrypoint": normalized[0], "assets": normalized[1:]}


def validate_rulebooks(
    manifest: dict[str, Any], plugin_dir: Path,
) -> tuple[dict[str, Any], ...]:
    """Validate the D01 ``rulebooks`` declaration without exposing files."""

    declared = manifest.get("rulebooks")
    if declared is None:
        return ()
    if not isinstance(declared, list) or len(declared) > 32:
        raise ValueError("rulebooks 必须是最多 32 项的数组")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in declared:
        if not isinstance(entry, dict):
            raise ValueError("rulebooks entry 必须是对象")
        fields = set(entry)
        if fields not in (
            {"id", "title", "path", "audience"},
            {"id", "title", "url", "audience"},
        ):
            raise ValueError("rulebooks entry fields are invalid")
        book_id = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        relative = str(entry.get("path") or "").replace("\\", "/").strip("/")
        external_url = str(entry.get("url") or "").strip()
        audience = str(entry.get("audience") or "").strip().lower()
        if not _RULEBOOK_ID_RE.fullmatch(book_id) or book_id in seen:
            raise ValueError("rulebooks id is invalid or duplicated")
        if not title or len(title) > 200:
            raise ValueError("rulebooks title is invalid")
        if audience not in {"player", "gm"}:
            raise ValueError("rulebooks audience must be player or gm")
        if "url" in fields:
            parsed = urlsplit(external_url)
            if (
                len(external_url) > 2048
                or parsed.scheme.lower() != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("rulebooks external URL must be a public HTTPS URL")
            normalized.append({
                "id": book_id,
                "title": title,
                "url": external_url,
                "audience": audience,
                "kind": "external",
            })
            seen.add(book_id)
            continue
        candidate = Path(relative)
        if (
            not relative
            or candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)
            or len(relative) > 240
        ):
            raise ValueError("rulebooks path is invalid")
        target = (plugin_dir / relative).resolve()
        ensure_inside(plugin_dir, target)
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"rulebook file is missing: {relative}")
        suffix = target.suffix.lower()
        if suffix not in {".md", ".markdown", ".pdf"}:
            raise ValueError("rulebooks only support Markdown or PDF files")
        seen.add(book_id)
        normalized.append({
            "id": book_id,
            "title": title,
            "path": relative,
            "audience": audience,
            "kind": "pdf" if suffix == ".pdf" else "markdown",
        })
    return tuple(normalized)


def validate_adventure_packages(
    manifest: dict[str, Any], plugin_dir: Path,
) -> tuple[Path, tuple[str, ...]] | None:
    """Validate manifest ``adventure_packages``（MOD-03，母方案 §107）。

    声明式冒险包：路径相对、不越界、真实目录（非 symlink）、含
    manifest.json；全部包必须共享同一父目录（v1 约束：一个 plugin =
    一个 declared-only loader）。仅 content-pack 允许声明。返回
    (公共父目录, 包目录名集合)，未声明返回 None。
    """

    declared = manifest.get("adventure_packages")
    if declared is None:
        return None
    if str(manifest.get("plugin_type") or "") != "content-pack":
        raise ValueError(t("plugin_host.host.adventure_packages_content_pack_only"))
    if not isinstance(declared, list) or not declared:
        raise ValueError(t("plugin_host.host.adventure_packages_not_string_array"))
    base = plugin_dir.resolve()
    packages: list[Path] = []
    for pattern in declared:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(t("plugin_host.host.adventure_packages_path_empty"))
        normalized = pattern.strip().replace("\\", "/")
        candidate = Path(normalized)
        if candidate.is_absolute() or any(part in ("..", "") for part in candidate.parts):
            raise ValueError(t("plugin_host.host.adventure_packages_path_unsafe"))
        resolved = (base / candidate).resolve()
        if resolved != base and base not in resolved.parents:
            raise ValueError(t("plugin_host.host.adventure_packages_path_outside"))
        if resolved.is_symlink() or not resolved.is_dir():
            raise ValueError(t("plugin_host.host.adventure_packages_dir_missing", normalized=normalized))
        if not (resolved / "manifest.json").is_file():
            raise ValueError(t("plugin_host.host.adventure_packages_manifest_missing", normalized=normalized))
        packages.append(resolved)
    parents = {package.parent for package in packages}
    if len(parents) != 1:
        raise ValueError(t("plugin_host.host.adventure_packages_same_parent"))
    parent = packages[0].parent
    directories = tuple(sorted(package.name for package in packages))
    return parent, directories


def validate_schema(schema: dict[str, Any]) -> None:
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        raise ValueError(t("plugin_host.host.config_schema_not_object"))
    for key, field_schema in schema["properties"].items():
        control = (field_schema.get("ui") or {}).get("control")
        if control and control not in _ALLOWED_CONTROLS:
            raise ValueError(t("plugin_host.host.field_control_unsupported", field=key, control=control))


def plugin_type(manifest: Mapping[str, object]) -> str:
    return str(manifest.get("plugin_type") or "").strip()


def has_entrypoint(manifest: Mapping[str, object]) -> bool:
    command = manifest.get("entrypoint")
    return isinstance(command, list) and bool(command)


def validate_entrypoint(manifest: dict[str, Any], plugin_type: str) -> None:
    command = manifest.get("entrypoint")
    if command is None and plugin_type in STATIC_PLUGIN_TYPES:
        return
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise ValueError(t("plugin_host.host.entrypoint_required", plugin_type=plugin_type))


def validate_manifest_permissions(manifest: dict[str, Any]) -> None:
    permissions = manifest.get("permissions", [])
    if permissions is None:
        permissions = []
    if not isinstance(permissions, list) or not all(isinstance(item, str) and item.strip() for item in permissions):
        raise ValueError(t("plugin_host.host.permissions_not_string_array"))
    unknown = sorted({item.strip() for item in permissions} - set(PERMISSION_DETAILS))
    if unknown:
        raise ValueError(
            t("plugin_host.host.permissions_unknown", unknown=', '.join(unknown))
        )


def validate_runtime_permissions(manifest: dict[str, Any], schema: dict[str, Any]) -> None:
    plugin_type = str(manifest.get("plugin_type") or "").strip()
    permissions = set(effective_plugin_permissions(manifest, schema))
    required = plugin_type_descriptor(plugin_type).get("required_permission")
    if required and required not in permissions:
        raise ValueError(t("plugin_host.host.permission_required", plugin_type=plugin_type, required=required))


def sensitive(field_schema: dict[str, Any]) -> bool:
    ui = field_schema.get("ui") or {}
    return bool(ui.get("sensitive") or ui.get("control") == "secret")


def normalize_value(field_schema: dict[str, Any], value: Any) -> Any:
    field_type = field_schema.get("type")
    if field_type == "boolean": return bool(value)
    if field_type == "number":
        number = float(value)
        if "exclusiveMinimum" in field_schema and number <= float(field_schema["exclusiveMinimum"]): raise ValueError(t("plugin_host.host.value_below_minimum"))
        return number
    if field_type == "integer": return int(value)
    if field_type == "array": return list(dict.fromkeys(str(item).strip() for item in (value if isinstance(value, list) else []) if str(item).strip()))
    text = str(value or "").strip()
    if field_schema.get("enum") and text not in field_schema["enum"]: raise ValueError(t("plugin_host.host.option_invalid"))
    return text


def validate_required(schema: dict[str, Any], config: dict[str, Any], secrets_data: dict[str, str]) -> None:
    for key in schema.get("required", []):
        if not config.get(key) and not secrets_data.get(key):
            raise ValueError(t("plugin_host.host.config_required_missing", field=key))


def ensure_inside(root: Path, target: Path) -> None:
    root = root.resolve()
    target = target.resolve()
    if target != root and root not in target.parents:
        raise ValueError(t("plugin_host.host.path_outside"))
