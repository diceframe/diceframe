"""Bounded, revisioned Creator Studio project storage.

The store is an authoring workspace, not a second content authority.  It keeps
small source files and provenance so the existing world/adventure/card owners
can later validate and export a selected revision.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import io
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PROJECT_ID_RE = re.compile(r"^project-[a-zA-Z0-9_-]{8,48}$")
_MAX_PROJECTS = 256
_MAX_FILES = 2048
_MAX_FILE_BYTES = 256 * 1024
_MAX_TOTAL_BYTES = 4 * 1024 * 1024
_SECRET_NAME_RE = re.compile(r"(^|/)(?:\.env(?:\.|$)|id_rsa|id_ed25519|secrets?\.json|credentials?\.json)$", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class CreatorProjectStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "projects": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Creator 工程存储损坏，已拒绝继续写入") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("projects"), dict):
            raise ValueError("不支持的 Creator 工程存储版本")
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)

    @staticmethod
    def _owner(owner_id: str) -> str:
        value = str(owner_id or "").strip()
        if not value or len(value) > 200:
            raise PermissionError("Creator 工程需要明确作者身份")
        return value

    @staticmethod
    def _project_id(project_id: str) -> str:
        value = str(project_id or "").strip()
        if not _PROJECT_ID_RE.fullmatch(value):
            raise ValueError("Creator 工程 ID 无效")
        return value

    @staticmethod
    def _file_path(value: str) -> str:
        path = str(value or "").replace("\\", "/").strip("/")
        parts = path.split("/")
        if not path or any(part in {"", ".", ".."} for part in parts) or path.startswith("/") or _SECRET_NAME_RE.search(path):
            raise ValueError("工程文件路径非法或疑似包含凭据")
        if len(path) > 240:
            raise ValueError("工程文件路径过长")
        return path

    @staticmethod
    def _normalize_files(files: Any) -> dict[str, str]:
        if not isinstance(files, dict):
            raise ValueError("工程 files 必须是对象")
        normalized: dict[str, str] = {}
        total = 0
        for raw_path, raw_content in files.items():
            path = CreatorProjectStore._file_path(raw_path)
            if not isinstance(raw_content, str):
                raise ValueError("工程文件内容必须是 UTF-8 文本")
            content = raw_content.replace("\x00", "")
            size = len(content.encode("utf-8"))
            if size > _MAX_FILE_BYTES:
                raise ValueError("单个工程文件超过 256 KiB")
            total += size
            if total > _MAX_TOTAL_BYTES:
                raise ValueError("工程文件总大小超过 4 MiB")
            normalized[path] = content
        if len(normalized) > _MAX_FILES:
            raise ValueError("工程文件数量超过 2048")
        return normalized

    def _owned(self, state: dict[str, Any], owner_id: str, project_id: str) -> dict[str, Any]:
        owner = self._owner(owner_id)
        project = state["projects"].get(self._project_id(project_id))
        if not isinstance(project, dict) or project.get("owner_id") != owner:
            raise KeyError("Creator 工程不存在")
        return project

    def list(self, owner_id: str) -> dict[str, Any]:
        owner = self._owner(owner_id)
        state = self._load()
        rows = [
            {
                "id": project_id,
                "name": row.get("name", ""),
                "revision": int(row.get("revision", 0)),
                "source": deepcopy(row.get("source") or {}),
                "updated_at": row.get("updated_at", ""),
                "file_count": len(row.get("files") or {}),
            }
            for project_id, row in state["projects"].items()
            if isinstance(row, dict) and row.get("owner_id") == owner
        ]
        return {"ok": True, "projects": sorted(rows, key=lambda item: item["id"])}

    def create(self, owner_id: str, name: str, *, source: dict[str, str] | None = None) -> dict[str, Any]:
        owner = self._owner(owner_id)
        title = str(name or "").strip()
        if not title or len(title) > 160:
            raise ValueError("Creator 工程名称不能为空且不能超过 160 个字符")
        state = self._load()
        if len(state["projects"]) >= _MAX_PROJECTS:
            raise ValueError("Creator 工程数量已达上限")
        project_id = "project-" + secrets.token_urlsafe(12).replace("-", "_").replace("=", "")[:16]
        now = _now()
        normalized_source = {}
        if source is not None:
            if not isinstance(source, dict):
                raise ValueError("工程 source 必须是对象")
            normalized_source = {
                "kind": str(source.get("kind") or "").strip()[:64],
                "id": str(source.get("id") or "").strip()[:200],
            }
        state["projects"][project_id] = {
            "owner_id": owner,
            "name": title,
            "revision": 0,
            "files": {},
            "source": normalized_source,
            "checkpoints": [],
            "build_receipts": [],
            "created_at": now,
            "updated_at": now,
        }
        self._save(state)
        return {"ok": True, "project": self.read(owner, project_id)["project"]}

    def read(self, owner_id: str, project_id: str) -> dict[str, Any]:
        state = self._load()
        project = self._owned(state, owner_id, project_id)
        result = deepcopy(project)
        result.pop("owner_id", None)
        result["id"] = self._project_id(project_id)
        digest_source = deepcopy(result)
        # Build receipts are derived evidence, not authored project content;
        # adding/replaying one must not invalidate the source revision digest.
        digest_source.pop("build_receipts", None)
        result["digest"] = _digest(digest_source)
        return {"ok": True, "project": result}

    def update(
        self,
        owner_id: str,
        project_id: str,
        *,
        expected_revision: int,
        files: dict[str, str] | None = None,
        remove: list[str] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision 必须是非负整数")
        state = self._load()
        project = self._owned(state, owner_id, project_id)
        current_revision = int(project.get("revision", 0))
        if current_revision != expected_revision:
            raise ValueError("工程版本已变化，请重新读取后再修改")
        current = self._normalize_files(project.get("files") or {})
        if files is not None:
            current.update(self._normalize_files(files))
        if remove is not None:
            if not isinstance(remove, list):
                raise ValueError("remove 必须是数组")
            for item in remove:
                current.pop(self._file_path(item), None)
        if name is not None:
            title = str(name).strip()
            if not title or len(title) > 160:
                raise ValueError("Creator 工程名称不能为空且不能超过 160 个字符")
            project["name"] = title
        project["files"] = current
        project["revision"] = current_revision + 1
        project["updated_at"] = _now()
        checkpoints = project.setdefault("checkpoints", [])
        checkpoints.append({
            "revision": project["revision"],
            "updated_at": project["updated_at"],
        })
        project["checkpoints"] = checkpoints[-32:]
        self._save(state)
        return self.read(owner_id, project_id)

    def delete(self, owner_id: str, project_id: str) -> dict[str, Any]:
        state = self._load()
        self._owned(state, owner_id, project_id)
        normalized_id = self._project_id(project_id)
        del state["projects"][normalized_id]
        self._save(state)
        return {"ok": True, "project_id": normalized_id, "deleted": True}

    def validate(self, owner_id: str, project_id: str) -> dict[str, Any]:
        project = self.read(owner_id, project_id)["project"]
        files = project.get("files") or {}
        errors: list[str] = []
        manifest: dict[str, Any] = {}
        raw_manifest = files.get("plugin.json")
        if raw_manifest is None:
            errors.append("工程缺少 plugin.json")
        else:
            try:
                parsed = json.loads(raw_manifest)
            except json.JSONDecodeError:
                parsed = None
            if not isinstance(parsed, dict):
                errors.append("plugin.json 必须是 JSON 对象")
            else:
                manifest = parsed
                for field in ("schema_version", "id", "name", "version", "plugin_type"):
                    if field not in manifest or manifest[field] in ("", None):
                        errors.append("plugin.json 缺少 " + field)
        return {
            "ok": not errors,
            "project_id": project["id"],
            "revision": project["revision"],
            "digest": project["digest"],
            "manifest": manifest,
            "errors": errors,
        }

    def preview(self, owner_id: str, project_id: str) -> dict[str, Any]:
        """Return a non-authoritative, user-facing project/store preview."""

        checked = self.validate(owner_id, project_id)
        if not checked["ok"]:
            return {"ok": False, **checked, "preview": None}
        project = self.read(owner_id, project_id)["project"]
        files = project.get("files") or {}
        manifest = checked["manifest"]
        resource_counts: dict[str, int] = {}
        for path in files:
            head = str(path).replace("\\", "/").split("/", 1)[0]
            resource_counts[head] = resource_counts.get(head, 0) + 1
        readme = str(files.get("README.md") or files.get("README.txt") or "")
        declared_rulebooks: list[dict[str, Any]] = []
        raw_rulebooks = manifest.get("rulebooks")
        if isinstance(raw_rulebooks, list):
            for entry in raw_rulebooks[:32]:
                if not isinstance(entry, dict):
                    continue
                path = str(entry.get("path") or "").replace("\\", "/").strip("/")
                declared_rulebooks.append({
                    "id": str(entry.get("id") or ""),
                    "title": str(entry.get("title") or ""),
                    "audience": str(entry.get("audience") or ""),
                    "path": path,
                    "present": bool(path and path in files),
                })
        return {
            "ok": True,
            "project_id": project["id"],
            "revision": project["revision"],
            "digest": project["digest"],
            "preview": {
                "id": str(manifest.get("id") or ""),
                "name": str(manifest.get("name") or ""),
                "version": str(manifest.get("version") or ""),
                "description": str(manifest.get("description") or ""),
                "plugin_type": str(manifest.get("plugin_type") or ""),
                "content_profile": str(manifest.get("content_profile") or ""),
                "content_delivery_mode": str(manifest.get("content_delivery_mode") or ""),
                "supported_locales": [str(item) for item in (manifest.get("supported_locales") or []) if isinstance(item, str)],
                "resource_counts": dict(sorted(resource_counts.items())),
                "readme": readme[:8192],
                "rulebooks": declared_rulebooks,
            },
        }

    def build(self, owner_id: str, project_id: str, *, expected_revision: int) -> dict[str, Any]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision 必须是非负整数")
        checked = self.validate(owner_id, project_id)
        if checked["revision"] != expected_revision:
            raise ValueError("工程版本已变化，请重新读取后再构建")
        if not checked["ok"]:
            return {"ok": False, **checked}
        project = self.read(owner_id, project_id)["project"]
        plugin_id = str(checked["manifest"].get("id") or "").strip()
        version = str(checked["manifest"].get("version") or "0.0.0").strip()
        receipt_id = f"build:{project['revision']}:{project['digest']}"
        files = project.get("files") or {}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative_path in sorted(files):
                info = zipfile.ZipInfo(f"{plugin_id}/{relative_path}")
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, str(files[relative_path]).encode("utf-8"))
        payload = buffer.getvalue()
        artifact_sha256 = hashlib.sha256(payload).hexdigest()
        state = self._load()
        stored = self._owned(state, owner_id, project_id)
        receipts = stored.setdefault("build_receipts", [])
        previous = next((item for item in receipts if isinstance(item, dict) and item.get("receipt_id") == receipt_id), None)
        replayed = previous is not None
        if previous is None:
            receipts.append({
                "receipt_id": receipt_id,
                "revision": project["revision"],
                "digest": project["digest"],
                "artifact_sha256": artifact_sha256,
                "filename": f"{plugin_id}-{version}.dfplugin",
            })
            stored["build_receipts"] = receipts[-32:]
            self._save(state)
        elif previous.get("artifact_sha256") != artifact_sha256:
            raise ValueError("构建回执与确定性产物不一致")
        return {
            "ok": True,
            "project_id": project["id"],
            "revision": project["revision"],
            "digest": project["digest"],
            "manifest": checked["manifest"],
            "filename": f"{plugin_id}-{version}.dfplugin",
            "artifact_sha256": artifact_sha256,
            "receipt_id": receipt_id,
            "replayed": replayed,
            "payload": payload,
        }

    def submission(self, owner_id: str, project_id: str, *, expected_revision: int) -> dict[str, Any]:
        """Prepare reviewable release metadata without uploading or publishing."""

        built = self.build(owner_id, project_id, expected_revision=expected_revision)
        if not built.get("ok"):
            return built
        manifest = built["manifest"]
        version = str(manifest.get("version") or "0.0.0")
        return {
            key: value for key, value in built.items() if key != "payload"
        } | {
            "submission": {
                "filename": built["filename"],
                "release_tag": f"v{version}",
                "artifact_sha256": built["artifact_sha256"],
                "revision": built["revision"],
                "requires_manual_upload": True,
                "requires_user_review": True,
            },
        }
