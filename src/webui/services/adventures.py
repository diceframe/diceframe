"""Adventure catalogue and server-owned compatibility resolution."""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from src.adventures.registry import AdventureSource, AdventureSourceRegistry
from src.adventures import (
    AdventureBundleError,
    AdventureBundleLoader,
    LoadedAdventureBundle,
    is_builtin_adventure_directory,
)
from src.rulesets.registry import RulesetRuntimeRegistry

_DIRECTORY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")
MAX_ADVENTURE_PACKAGE_BYTES = 5 * 1024 * 1024
MAX_ADVENTURE_FILE_BYTES = 1024 * 1024
MAX_ADVENTURE_FILES = 256


@dataclass(frozen=True)
class AdventureDependencies:
    adventure_loader: AdventureBundleLoader
    list_instances: Callable[[], list[Any]]
    load_rule_by_id: Callable[[str, str], Any | None]
    ruleset_registry: RulesetRuntimeRegistry
    default_runtime_requirement: Callable[[], dict[str, Any]]
    builtin_adventures_dir: Path | None = None


def _runtime_for_rule(
    dependencies: AdventureDependencies,
    rule_id: str,
    language: str,
) -> Any | None:
    rule = dependencies.load_rule_by_id(
        str(rule_id or "").strip(), language,
    )
    if rule is None:
        return None
    try:
        return dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError):
        return None


def _compatibility(
    bundle: LoadedAdventureBundle, runtime: Any | None, world_id: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    manifest = bundle.manifest
    if runtime is None:
        reasons.append("ruleset_runtime_unavailable")
    else:
        formats = set(getattr(runtime.capabilities, "adventure_formats", ()) or ())
        if manifest.format not in formats:
            reasons.append("adventure_format_unsupported")
        if runtime.runtime_id != manifest.required_runtime_id:
            reasons.append("runtime_id_mismatch")
        if runtime.runtime_version < manifest.required_runtime_version:
            reasons.append("runtime_version_too_old")
    if (
        manifest.world_policy == "fixed"
        and str(world_id or "") != manifest.recommended_world_id
    ):
        reasons.append("world_mismatch")
    return ("compatible" if not reasons else "incompatible", reasons)


def list_adventures(
    dependencies: AdventureDependencies,
    rule_id: str = "",
    world_id: str = "",
    language: str = "",
) -> dict[str, Any]:
    runtime = _runtime_for_rule(dependencies, rule_id, language)
    try:
        bundles = dependencies.adventure_loader.list(language)
    except AdventureBundleError as exc:
        return {"ok": False, "error_code": "ADVENTURE_CATALOG_INVALID", "error": str(exc)}
    items: list[dict[str, Any]] = []
    for bundle in bundles:
        status, reasons = _compatibility(bundle, runtime, world_id)
        adventure = bundle.adventure
        usages = _bound_games(dependencies, bundle.manifest.adventure_id)
        builtin = _is_builtin_bundle(dependencies, bundle)
        items.append({
            "adventure_id": bundle.manifest.adventure_id,
            "version": bundle.manifest.version,
            "format": bundle.manifest.format,
            "world_policy": bundle.manifest.world_policy,
            "recommended_world_id": bundle.manifest.recommended_world_id,
            "required_runtime": {
                "id": bundle.manifest.required_runtime_id,
                "minimum_version": bundle.manifest.required_runtime_version,
            },
            "name": str(adventure.get("tutorial", {}).get("name") or adventure.get("name") or adventure["id"]),
            "summary": str(adventure.get("tutorial", {}).get("summary") or adventure.get("summary") or ""),
            "estimated_minutes": int(adventure.get("estimated_minutes", 0) or 0),
            "compatibility": status,
            "incompatibility_reasons": reasons,
            "directory_id": bundle.root.name,
            "source": "builtin" if builtin else "custom",
            "custom": not builtin,
            "editable": not builtin and not usages,
            "in_use": len(usages),
        })
    return {"ok": True, "adventures": items}


def _bound_games(
    dependencies: AdventureDependencies,
    adventure_id: str,
) -> list[str]:
    return [
        "|".join(instance.game_key)
        for instance in dependencies.list_instances()
        if str(
            (getattr(instance, "adventure_binding", {}) or {}).get("adventure_id") or ""
        ) == adventure_id
    ]


def _is_builtin_bundle(
    dependencies: AdventureDependencies,
    bundle: LoadedAdventureBundle,
) -> bool:
    builtin_root = dependencies.builtin_adventures_dir
    return is_builtin_adventure_directory(bundle.root) or (
        isinstance(builtin_root, Path)
        and bundle.root.parent.resolve() == builtin_root.resolve()
    )


def _resolve_bundle(
    dependencies: AdventureDependencies,
    adventure_id: str,
    language: str = "",
) -> LoadedAdventureBundle:
    try:
        return dependencies.adventure_loader.resolve(
            str(adventure_id or ""), language,
        )
    except AdventureBundleError as exc:
        raise ValueError(str(exc)) from exc


def _json_files(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        result[relative] = json.loads(path.read_text(encoding="utf-8"))
    return result


def adventure_detail(
    dependencies: AdventureDependencies,
    adventure_id: str,
    language: str = "",
) -> dict[str, Any]:
    bundle = _resolve_bundle(dependencies, adventure_id, language)
    usages = _bound_games(dependencies, bundle.manifest.adventure_id)
    builtin = _is_builtin_bundle(dependencies, bundle)
    return {
        "ok": True,
        "adventure": {
            "adventure_id": bundle.manifest.adventure_id,
            "directory_id": bundle.root.name,
            "version": bundle.manifest.version,
            "format": bundle.manifest.format,
            "content_digest": bundle.content_digest,
            "custom": not builtin,
            "editable": not builtin and not usages,
            "bound_games": usages,
            "files": _json_files(bundle.root),
        },
    }


def _validate_directory_id(value: Any) -> str:
    directory_id = str(value or "").strip().lower()
    if not _DIRECTORY_ID_RE.fullmatch(directory_id):
        raise ValueError("adventure directory_id must use lowercase canonical characters")
    return directory_id


def _validate_package_id(value: Any) -> str:
    adventure_id = str(value or "").strip()
    if not _PACKAGE_ID_RE.fullmatch(adventure_id) or adventure_id.startswith("core:"):
        raise ValueError("custom adventure_id must be canonical and must not use the core namespace")
    return adventure_id


def _ensure_identity_available(
    dependencies: AdventureDependencies,
    adventure_id: str,
) -> None:
    for bundle in dependencies.adventure_loader.list(""):
        if bundle.manifest.adventure_id == adventure_id:
            raise ValueError(f"adventure identity already exists: {adventure_id}")


def _write_json_files(root: Path, files: Any) -> None:
    if not isinstance(files, dict) or not files or len(files) > MAX_ADVENTURE_FILES:
        raise ValueError("adventure files must be a non-empty bounded object")
    total = 0
    for raw_name, value in files.items():
        name = str(raw_name or "").replace("\\", "/")
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix != ".json"
            or not relative.parts
        ):
            raise ValueError(f"invalid adventure file path: {name}")
        payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        if len(payload) > MAX_ADVENTURE_FILE_BYTES:
            raise ValueError(f"adventure file is too large: {name}")
        total += len(payload)
        if total > MAX_ADVENTURE_PACKAGE_BYTES:
            raise ValueError("adventure package is too large")
        target = root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _validated_stage(
    dependencies: AdventureDependencies,
    directory_id: str,
    files: dict[str, Any],
) -> tuple[Path, Any]:
    parent = dependencies.adventure_loader.adventures_dir.parent
    temporary = tempfile.TemporaryDirectory(prefix="diceframe-adventure-", dir=parent)
    root = Path(temporary.name) / directory_id
    root.mkdir()
    try:
        _write_json_files(root, files)
        bundle = AdventureBundleLoader(Path(temporary.name)).load(directory_id, "")
    except Exception:
        temporary.cleanup()
        raise
    return root, temporary


def copy_adventure(
    dependencies: AdventureDependencies,
    adventure_id: str,
    body: dict[str, Any],
    language: str = "",
) -> dict[str, Any]:
    source = _resolve_bundle(dependencies, adventure_id, language)
    directory_id = _validate_directory_id(body.get("directory_id"))
    target = dependencies.adventure_loader.adventures_dir / directory_id
    if target.exists():
        raise ValueError(f"adventure directory already exists: {directory_id}")
    new_id = _validate_package_id(body.get("adventure_id") or f"user:{directory_id}")
    _ensure_identity_available(dependencies, new_id)
    files = _json_files(source.root)
    manifest = dict(files.get("manifest.json") or {})
    manifest.update({
        "adventure_id": new_id,
        "version": str(body.get("version") or "1.0.0").strip(),
        "custom": True,
        "source_adventure_id": source.manifest.adventure_id,
    })
    files["manifest.json"] = manifest
    requested_locale = str(body.get("locale") or "").replace("_", "-")
    locale = (
        requested_locale
        if requested_locale in source.manifest.supported_locales
        else source.manifest.default_locale
    )
    locale_path = f"locales/{locale}/adventure.json"
    locale_file = files.get(locale_path)
    if isinstance(locale_file, dict):
        fields = locale_file.setdefault("fields", {})
        tutorial = fields.setdefault("tutorial", {}) if isinstance(fields, dict) else {}
        if isinstance(tutorial, dict):
            if str(body.get("name") or "").strip():
                tutorial["name"] = str(body["name"]).strip()
            if str(body.get("summary") or "").strip():
                tutorial["summary"] = str(body["summary"]).strip()
    staged, temporary = _validated_stage(dependencies, directory_id, files)
    try:
        shutil.move(str(staged), str(target))
    finally:
        temporary.cleanup()
    bundle = dependencies.adventure_loader.resolve(new_id, language)
    return {
        "ok": True,
        "adventure_id": bundle.manifest.adventure_id,
        "directory_id": directory_id,
        "content_digest": bundle.content_digest,
    }


def create_adventure(
    dependencies: AdventureDependencies,
    body: dict[str, Any],
    language: str = "",
) -> dict[str, Any]:
    """Create a small, valid user package that can be expanded in the editor.

    The package is intentionally data-only.  A new package starts with one scene
    and one chapter so it can be selected immediately, while all rich content is
    optional and can be added later through the structured editor.
    """
    directory_id = _validate_directory_id(body.get("directory_id"))
    target = dependencies.adventure_loader.adventures_dir / directory_id
    if target.exists():
        raise ValueError(f"adventure directory already exists: {directory_id}")
    adventure_id = _validate_package_id(body.get("adventure_id") or f"user:{directory_id}")
    _ensure_identity_available(dependencies, adventure_id)
    name = str(body.get("name") or "未命名冒险").strip()
    summary = str(body.get("summary") or "").strip()
    world_policy = str(body.get("world_policy") or "portable").strip()
    if world_policy not in {"fixed", "portable", "agnostic"}:
        raise ValueError("world_policy must be fixed, portable, or agnostic")
    world_id = str(body.get("recommended_world_id") or "").strip()
    if world_policy == "fixed" and not world_id:
        raise ValueError("fixed adventures require recommended_world_id")
    stem = directory_id
    scene_id = f"{stem}_opening"
    chapter_id = "chapter_1"
    step_id = "opening"
    source_ref = f"diceframe-user:{adventure_id}"
    raw_runtime = body.get("required_runtime")
    runtime_requirement = (
        dict(raw_runtime)
        if isinstance(raw_runtime, dict)
        else dict(dependencies.default_runtime_requirement())
    )
    runtime_id = str(runtime_requirement.get("id") or "").strip()
    raw_minimum_version = runtime_requirement.get("minimum_version", 1)
    if not runtime_id:
        raise ValueError("required_runtime.id must not be empty")
    if isinstance(raw_minimum_version, bool):
        raise ValueError("required_runtime.minimum_version must be a positive integer")
    try:
        minimum_runtime_version = int(raw_minimum_version)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "required_runtime.minimum_version must be a positive integer"
        ) from exc
    if minimum_runtime_version < 1:
        raise ValueError("required_runtime.minimum_version must be a positive integer")
    files: dict[str, Any] = {
        "manifest.json": {
            "schema_version": 1,
            "adventure_id": adventure_id,
            "version": str(body.get("version") or "1.0.0").strip(),
            "format": "diceframe:adventure-graph-v1",
            "world_policy": world_policy,
            "recommended_world_id": world_id,
            "required_runtime": {
                "id": runtime_id,
                "minimum_version": minimum_runtime_version,
            },
            "default_locale": "zh-CN",
            "supported_locales": ["zh-CN", "en", "de"],
            "custom": True,
        },
        "adventure.json": {
            "schema_version": 1,
            "kind": "adventure",
            "id": stem,
            "source_ref": source_ref,
            "recommended_world_id": world_id,
            "automation_level": "guided",
            "estimated_minutes": int(body.get("estimated_minutes", 60) or 60),
            "start_step_id": step_id,
            "chapters": [{"id": chapter_id, "step_ids": [step_id]}],
            "steps": [{
                "id": step_id,
                "chapter_id": chapter_id,
                "scene_ref": f"scene:{scene_id}",
                "requires": "none",
                "choice_ids": [],
            }],
            "choices": [],
        },
        f"content/scenes/{scene_id}.json": {
            "schema_version": 1,
            "kind": "scene",
            "id": scene_id,
            "source_ref": source_ref,
            "automation_level": "guided",
            "npc_refs": [],
        },
    }
    _SCAFFOLD_TEXT = {
        "zh-CN": {"chapter_1": "第一章", "opening": "开场", "narration": "冒险从这里开始。", "opening_scene": "开场场景"},
        "en": {"chapter_1": "Chapter 1", "opening": "Opening", "narration": "The adventure begins here.", "opening_scene": "Opening Scene"},
        "de": {"chapter_1": "Kapitel 1", "opening": "Eröffnung", "narration": "Das Abenteuer beginnt hier.", "opening_scene": "Eröffnungsszene"},
    }
    for locale in ("zh-CN", "en", "de"):
        localized_name = name
        localized_summary = summary
        scaffold_text = _SCAFFOLD_TEXT[locale]
        files[f"locales/{locale}/adventure.json"] = {
            "locale_schema_version": 1,
            "locale": locale,
            "target": {"kind": "adventure", "id": stem},
            "fields": {"tutorial": {
                "name": localized_name,
                "summary": localized_summary,
                "chapters": {chapter_id: {"name": scaffold_text["chapter_1"]}},
                "steps": {step_id: {
                    "title": scaffold_text["opening"],
                    "narration": scaffold_text["narration"],
                    "objective": "",
                    "hint": "",
                }},
                "choices": {},
            }},
        }
        files[f"locales/{locale}/scenes/{scene_id}.json"] = {
            "locale_schema_version": 1,
            "locale": locale,
            "target": {"kind": "scene", "id": scene_id},
            "fields": {"name": scaffold_text["opening_scene"], "description": ""},
        }
    staged, temporary = _validated_stage(dependencies, directory_id, files)
    try:
        shutil.move(str(staged), str(target))
    finally:
        temporary.cleanup()
    bundle = dependencies.adventure_loader.resolve(adventure_id, language)
    return {"ok": True, "adventure_id": adventure_id, "directory_id": directory_id, "content_digest": bundle.content_digest}


def update_adventure(
    dependencies: AdventureDependencies,
    adventure_id: str,
    body: dict[str, Any],
    language: str = "",
) -> dict[str, Any]:
    current = _resolve_bundle(dependencies, adventure_id, language)
    if _is_builtin_bundle(dependencies, current):
        raise PermissionError("built-in adventures must be copied before editing")
    usages = _bound_games(dependencies, current.manifest.adventure_id)
    if usages:
        raise PermissionError("adventure is bound to a save and cannot be edited")
    files = body.get("files")
    if not isinstance(files, dict):
        raise ValueError("adventure files are required")
    manifest = files.get("manifest.json")
    if not isinstance(manifest, dict) or str(manifest.get("adventure_id") or "") != adventure_id:
        raise ValueError("editing cannot change canonical adventure identity")
    staged, temporary = _validated_stage(
        dependencies, current.root.name, files,
    )
    backup = current.root.with_name(f".{current.root.name}.backup")
    try:
        if backup.exists():
            shutil.rmtree(backup)
        current.root.replace(backup)
        try:
            shutil.move(str(staged), str(current.root))
        except Exception:
            backup.replace(current.root)
            raise
        shutil.rmtree(backup)
    finally:
        temporary.cleanup()
    updated = dependencies.adventure_loader.resolve(adventure_id, language)
    return {"ok": True, "content_digest": updated.content_digest}


def delete_adventure(
    dependencies: AdventureDependencies,
    adventure_id: str,
) -> dict[str, Any]:
    bundle = _resolve_bundle(dependencies, adventure_id)
    if _is_builtin_bundle(dependencies, bundle):
        raise PermissionError("built-in adventures cannot be deleted")
    usages = _bound_games(dependencies, bundle.manifest.adventure_id)
    if usages:
        raise PermissionError("adventure is bound to a save and cannot be deleted")
    shutil.rmtree(bundle.root)
    return {"ok": True, "deleted": bundle.manifest.adventure_id}


def export_adventure(
    dependencies: AdventureDependencies,
    adventure_id: str,
) -> tuple[str, bytes]:
    bundle = _resolve_bundle(dependencies, adventure_id)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle.root.rglob("*.json")):
            archive.write(path, f"{bundle.root.name}/{path.relative_to(bundle.root).as_posix()}")
    return f"{bundle.root.name}.dfadventure.zip", output.getvalue()


def import_adventure(
    dependencies: AdventureDependencies,
    payload: bytes,
    directory_id: str = "",
) -> dict[str, Any]:
    if not payload or len(payload) > MAX_ADVENTURE_PACKAGE_BYTES:
        raise ValueError("adventure package is empty or too large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValueError("adventure package is not a valid ZIP file") from exc
    files: dict[str, Any] = {}
    roots: set[str] = set()
    total = 0
    with archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if not members or len(members) > MAX_ADVENTURE_FILES:
            raise ValueError("adventure package contains an invalid number of files")
        for member in members:
            path = PurePosixPath(member.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
                raise ValueError(f"invalid adventure archive path: {member.filename}")
            roots.add(path.parts[0] if len(path.parts) > 1 else "")
        if len(roots) != 1:
            raise ValueError("adventure ZIP must contain exactly one package directory")
        root = next(iter(roots))
        for member in members:
            path = PurePosixPath(member.filename.replace("\\", "/"))
            relative = PurePosixPath(*path.parts[1:]) if root else path
            if member.file_size > MAX_ADVENTURE_FILE_BYTES:
                raise ValueError("adventure package expands beyond the size limit")
            total += member.file_size
            if total > MAX_ADVENTURE_PACKAGE_BYTES:
                raise ValueError("adventure package expands beyond the size limit")
            raw = archive.read(member)
            if len(raw) != member.file_size:
                raise ValueError("adventure package expands beyond the size limit")
            try:
                files[relative.as_posix()] = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid adventure JSON: {relative.as_posix()}") from exc
    manifest = files.get("manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("adventure package is missing manifest.json")
    adventure_id = _validate_package_id(manifest.get("adventure_id"))
    _ensure_identity_available(dependencies, adventure_id)
    wanted_directory = _validate_directory_id(directory_id or root)
    target = dependencies.adventure_loader.adventures_dir / wanted_directory
    if target.exists():
        raise ValueError(f"adventure directory already exists: {wanted_directory}")
    manifest["custom"] = True
    files["manifest.json"] = manifest
    staged, temporary = _validated_stage(
        dependencies, wanted_directory, files,
    )
    try:
        shutil.move(str(staged), str(target))
    finally:
        temporary.cleanup()
    imported = dependencies.adventure_loader.resolve(adventure_id, "")
    return {
        "ok": True,
        "adventure_id": adventure_id,
        "directory_id": wanted_directory,
        "content_digest": imported.content_digest,
    }


def resolve_binding(
    dependencies: AdventureDependencies,
    adventure_id: str,
    rule_id: str,
    world_id: str,
    language: str,
) -> dict[str, Any]:
    runtime = _runtime_for_rule(dependencies, rule_id, language)
    return resolve_binding_for_runtime(
        dependencies, adventure_id, runtime, world_id, language,
    )


def resolve_binding_for_runtime(
    dependencies: AdventureDependencies,
    adventure_id: str,
    runtime: Any | None,
    world_id: str, language: str,
) -> dict[str, Any]:
    wanted = str(adventure_id or "").strip()
    if not wanted:
        return {}
    try:
        bundle = dependencies.adventure_loader.resolve(wanted, language)
    except AdventureBundleError as exc:
        raise ValueError(str(exc)) from exc
    status, reasons = _compatibility(bundle, runtime, world_id)
    if status != "compatible":
        raise ValueError(f"adventure package is incompatible: {', '.join(reasons)}")
    return bundle.binding(world_id)
