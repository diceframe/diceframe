"""世界编辑器服务：世界书 CRUD + 条目管理 + 索引重建 + 世界模板列表。"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.engine.language import DEFAULT_LANGUAGE, localized_text, normalize_language
from src.content.gm_style import normalize_gm_style
from src.content.worlds import load_world_template as load_content_world
from src.generation import creator
from src.lorebook.bootstrap import ensure_world_from_template
from src.template_catalog import is_user_template_file
from src.webui.services._common import MAX_LOREBOOK_CHARS
from src.content_modules.projection import ContentProjectionService

logger = logging.getLogger("trpg")

_LOREBOOK_ENTRY_TYPES = {"npc", "location", "item", "event", "puzzle", "faction", "spell", "class", "other"}
_LOREBOOK_TIERS = {"core", "background", "archived"}
_MAX_GENERATED_LOREBOOK_CONTENT = 2000
_WORLD_TEMPLATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_LEGACY_GAME_TEMPLATE_ID = re.compile(r"^.+_(?:copy|blank)_\d+$")
_LEGACY_GAME_TEMPLATE_SUFFIXES = (
    "（复制世界书）",
    "（空白世界书）",
    " (Copied Lorebook)",
    " (Blank Lorebook)",
)


@dataclass(frozen=True)
class WorldDependencies:
    lorebook: Any
    worlds_dir: Path | None
    plugin_host: Any | None
    llm_client: Any | None
    character_gen_max_tokens: int
    invalidate_lorebook_index: Callable[[str], None] | None
    list_instances: Callable[[], list[Any]]
    content_projection: ContentProjectionService | None = None


def _user_world_base(name: str) -> str:
    """Build an ASCII display-derived prefix; the UUID remains the identity."""
    return "".join(
        ch if ch.isascii() and ch.isalnum() else "_"
        for ch in str(name or "").lower()
    ).strip("_")[:48] or "world"


def _new_user_world_id(dependencies: WorldDependencies, name: str) -> str:
    """Generate a canonical user-world identity that cannot collide by second."""
    base = _user_world_base(name)
    worlds_dir = dependencies.worlds_dir
    while True:
        world_id = f"custom_book_{base}_{uuid.uuid4().hex}"
        if dependencies.lorebook.get_world(world_id) is not None:
            continue
        if worlds_dir and (worlds_dir / f"{world_id}.json").exists():
            continue
        return world_id


def _template_path(
    worlds_dir: Path,
    template_id: str,
    *,
    require_canonical: bool = False,
) -> Path | None:
    """Resolve one template filename without allowing traversal outside its root."""
    template_id = str(template_id or "").strip()
    if not template_id or (require_canonical and not _WORLD_TEMPLATE_ID_RE.fullmatch(template_id)):
        return None
    root = worlds_dir.resolve()
    candidate = (root / f"{template_id}.json").resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _user_template_from_lore(
    dependencies: WorldDependencies,
    world_id: str,
) -> dict[str, Any] | None:
    world = dependencies.lorebook.get_world(world_id)
    if not world:
        return None
    description = str(world.get("description") or "")
    entries = dependencies.lorebook.list_entries(world_id)
    return {
        "world_id": world_id,
        "world_name": str(world.get("name") or world_id),
        "custom": True,
        "description": description,
        "world_setting": description,
        "starter_scene": description[:120],
        "suggested_difficulty": "标准",
        "language": normalize_language(str(world.get("language") or "")),
        "default_rule": "freeform_fantasy",
        "starter_lorebook": [
            _entry_to_template_entry(entry)
            for entry in entries
            if isinstance(entry, dict)
        ],
    }


def _ensure_user_world_template(
    dependencies: WorldDependencies,
    world_id: str,
) -> Path | None:
    """Materialize legacy lore-only custom worlds at the editable template boundary."""
    worlds_dir = dependencies.worlds_dir
    if not worlds_dir or not world_id.startswith(("custom_", "ai_")):
        return None
    path = _template_path(worlds_dir, world_id)
    if path is None:
        return None
    if path.is_file():
        return path if is_user_template_file(path, "worlds") else None
    template = _user_template_from_lore(dependencies, world_id)
    if template is None:
        return None
    _write_json_atomic(path, template)
    return path


def list_worlds(dependencies: WorldDependencies) -> dict[str, Any]:
    worlds = dependencies.lorebook.list_worlds()
    for w in worlds:
        entries = (
            dependencies.content_projection.for_world_authoring(w["id"])
            if dependencies.content_projection is not None
            else dependencies.lorebook.list_entries(w["id"])
        )
        w["entry_count"] = len(entries)
        w["gm_style"] = _read_user_template_gm_style(
            dependencies, str(w.get("id") or ""),
        )
    return {"worlds": worlds, "total": len(worlds)}


def create_world(dependencies: WorldDependencies, name: str, description: str = "",
                 language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "世界书名称不能为空"}
    world_id = _new_user_world_id(dependencies, name)
    language = normalize_language(language)
    dependencies.lorebook.create_world(
        world_id, name, description=description or "", language=language,
    )
    try:
        if _ensure_user_world_template(dependencies, world_id) is None:
            dependencies.lorebook.delete_world(world_id)
            return {"ok": False, "error": "自建世界模板创建失败"}
    except OSError:
        dependencies.lorebook.delete_world(world_id)
        logger.exception("创建自建世界模板失败: %s", world_id)
        return {"ok": False, "error": "自建世界模板创建失败"}
    return {"ok": True, "world_id": world_id, "name": name, "language": language}


def clone_world_from_template(
    dependencies: WorldDependencies,
    template_id: str,
    name: str = "",
) -> dict[str, Any]:
    """把内置/插件/用户世界模板克隆为用户可编辑世界。

    新世界沿用 custom_book_* id 约定与用户模板文件写入，条目 CRUD 随后
    自动走 _sync_user_template_lorebook 回写，与手动自建世界同一条代码路径。
    读取源模板时不做 locale overlay，克隆结果保持 canonical identity。
    """
    template_id = str(template_id or "").strip()
    if not template_id:
        return {"ok": False, "error": "缺少要克隆的世界模板"}
    if not _WORLD_TEMPLATE_ID_RE.fullmatch(template_id):
        return {"ok": False, "error": "世界模板 id 不合法"}
    worlds_dir = dependencies.worlds_dir
    if not worlds_dir:
        return {"ok": False, "error": "世界模板目录未配置"}
    source = _load_clone_source(dependencies, template_id)
    if source is None:
        return {"ok": False, "error": "世界模板不存在"}
    language = normalize_language(str(source.get("language") or ""))
    new_name = (name or "").strip()
    if not new_name:
        # 缺省名加「克隆」后缀，避免开团页世界下拉与源模板重名不可区分。
        new_name = str(source.get("world_name") or template_id).strip() + localized_text(
            language, {"en": " (Clone)", "zh-CN": "（克隆）", "ja": "（クローン）", "de": " (Klon)"},
        )
    world_id = _new_user_world_id(dependencies, new_name)
    template = {
        key: copy.deepcopy(value)
        for key, value in source.items()
        if not key.startswith("_")
    }
    template.pop("deprecated", None)
    template["world_id"] = world_id
    template["world_name"] = new_name
    template["custom"] = True
    template["language"] = language
    path = _template_path(worlds_dir, world_id)
    if path is None:
        return {"ok": False, "error": "生成的世界模板 id 不合法"}
    _write_json_atomic(path, template)
    ensure_world_from_template(dependencies.lorebook, world_id, template)
    logger.info("已克隆世界模板: %s -> %s", template_id, world_id)
    return {"ok": True, "world_id": world_id, "name": new_name, "language": language}


def update_world_gm_style(
    dependencies: WorldDependencies,
    world_id: str,
    raw: Any,
) -> dict[str, Any]:
    """保存世界级 GM 风格。仅用户模板可改；内置/插件世界提示先克隆。"""
    world_id = str(world_id or "").strip()
    if not world_id:
        return {"ok": False, "error": "缺少世界 id"}
    if dependencies.lorebook.get_world(world_id) is None:
        return {"ok": False, "error": "世界不存在"}
    worlds_dir = dependencies.worlds_dir
    if not worlds_dir:
        return {"ok": False, "error": "世界模板目录未配置"}
    try:
        path = _ensure_user_world_template(dependencies, world_id)
    except OSError:
        logger.exception("物化自建世界模板失败: %s", world_id)
        return {"ok": False, "error": "世界模板创建失败"}
    if path is None or not path.is_file() or not is_user_template_file(path, "worlds"):
        return {"ok": False, "error": "内置或插件世界不可修改，请先克隆为我的世界"}
    normalized = normalize_gm_style(raw)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "世界模板读取失败"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "世界模板格式不正确"}
    data["gm_style"] = normalized
    _write_json_atomic(path, data)
    return {"ok": True, "gm_style": normalized}


_USER_SCENE_IMAGE_KINDS = {"builtin", "upload", "generated"}


def set_user_world_scene_image(
    dependencies: WorldDependencies,
    world_id: str,
    scene_image: Any,
) -> dict[str, Any]:
    """设置用户世界的头图引用。仅用户模板可改；插件世界的头图随插件内容提供，
    请先克隆为我的世界（克隆副本即用户世界，可正常设置）。

    引用形状与创建页上传一致：``{"kind": "upload"|"generated", "asset_id"}``
    或 ``{"kind": "builtin", "id"}``；写回用户模板 JSON，画廊与创建页按
    现有 scene-image 解析路径展示。
    """
    world_id = str(world_id or "").strip()
    if not world_id:
        return {"ok": False, "error": "缺少世界 id"}
    if dependencies.lorebook.get_world(world_id) is None:
        return {"ok": False, "error": "世界不存在"}
    worlds_dir = dependencies.worlds_dir
    if not worlds_dir:
        return {"ok": False, "error": "世界模板目录未配置"}
    try:
        path = _ensure_user_world_template(dependencies, world_id)
    except OSError:
        logger.exception("物化自建世界模板失败: %s", world_id)
        return {"ok": False, "error": "世界模板创建失败"}
    if path is None or not path.is_file() or not is_user_template_file(path, "worlds"):
        return {"ok": False, "error": "内置或插件世界不可修改，请先克隆为我的世界"}
    if not isinstance(scene_image, dict):
        return {"ok": False, "error": "头图引用不合法"}
    kind = str(scene_image.get("kind") or "")
    if kind not in _USER_SCENE_IMAGE_KINDS:
        return {"ok": False, "error": "头图引用不合法"}
    if kind in {"upload", "generated"} and not str(scene_image.get("asset_id") or "").strip():
        return {"ok": False, "error": "头图引用缺少 asset_id"}
    if kind == "builtin" and not str(scene_image.get("id") or "").strip():
        return {"ok": False, "error": "头图引用缺少 id"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "世界模板读取失败"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "世界模板格式不正确"}
    data["scene_image"] = scene_image
    _write_json_atomic(path, data)
    return {"ok": True, "scene_image": scene_image}


def _read_user_template_gm_style(
    dependencies: WorldDependencies,
    world_id: str,
) -> dict[str, str] | None:
    """用户模板的 gm_style（normalized）；无模板文件时 None。"""
    worlds_dir = dependencies.worlds_dir
    if not worlds_dir or not world_id:
        return None
    path = _template_path(worlds_dir, world_id)
    if path is None or not path.is_file() or not is_user_template_file(path, "worlds"):
        return normalize_gm_style(None) if world_id.startswith(("custom_", "ai_")) else None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return normalize_gm_style(data.get("gm_style"))


def _load_clone_source(
    dependencies: WorldDependencies,
    template_id: str,
) -> dict[str, Any] | None:
    """按 id 查找克隆源：优先运行时模板目录的 raw core，其次插件贡献。"""
    worlds_dir = dependencies.worlds_dir
    if worlds_dir:
        path = _template_path(worlds_dir, template_id, require_canonical=True)
        if path is None:
            return None
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            if isinstance(data, dict) and not data.get("deprecated"):
                return data
            return None
    plugin_host = dependencies.plugin_host
    if not plugin_host:
        return None
    for item in plugin_host.contributions.list("world_template"):
        if item.path.stem != template_id:
            continue
        try:
            data = plugin_host.load_world_template(item.key, "") or {}
        except Exception:
            logger.warning("插件世界模板读取失败: %s", item.path, exc_info=True)
            return None
        if isinstance(data, dict) and data and not data.get("deprecated"):
            return data
    return None


def list_entries(
    dependencies: WorldDependencies,
    world_id: str,
    entry_type: str | None = None,
) -> dict[str, Any]:
    entries = dependencies.lorebook.list_entries(world_id, entry_type)
    return {"entries": entries, "total": len(entries)}


def search_entries(
    dependencies: WorldDependencies,
    world_id: str,
    keyword: str,
) -> dict[str, Any]:
    entries = dependencies.lorebook.search_entries(world_id, keyword)
    return {"entries": entries, "total": len(entries)}


def get_entry(
    dependencies: WorldDependencies,
    entry_id: str,
) -> dict[str, Any] | None:
    return dependencies.lorebook.get_entry(entry_id)


def save_entry(
    dependencies: WorldDependencies,
    entry: dict,
) -> dict[str, Any]:
    # 导入/新增入口的防御性校验：缺键时生成 id 或返回 400 级错误，
    # 不让 KeyError 漏成 500（UI 导入的 body 可能完全不带 id 键）。
    prepared, error = _prepare_entry(dependencies, entry, existing_ids=None)
    if error or prepared is None:
        return {"ok": False, "error": error or "世界书条目无效"}
    dependencies.lorebook.add_entry(prepared)
    rebuild_lorebook_index(dependencies, prepared["world_id"])
    _sync_user_template_lorebook(dependencies, prepared["world_id"])
    return {"ok": True, "entry_id": prepared["id"]}


MAX_LOREBOOK_IMPORT_ENTRIES = 500


def import_entries(
    dependencies: WorldDependencies,
    world_id: str,
    entries: list,
) -> dict[str, Any]:
    """Batch-import lorebook entries in one request.

    Importing entry-by-entry makes one write request per entry, which the
    write flood guard legitimately rejects for large lorebooks (issue #213).
    One batched request keeps that guard meaningful; index rebuild and user
    template sync run once for the whole batch.  Best-effort per entry: data
    problems are reported per index instead of aborting the batch.
    """

    world_id = str(world_id or "").strip()
    if dependencies.lorebook.get_world(world_id) is None:
        return {"ok": False, "error": "世界不存在"}
    if not isinstance(entries, list) or not entries:
        return {"ok": False, "error": "条目列表不能为空"}
    if len(entries) > MAX_LOREBOOK_IMPORT_ENTRIES:
        return {"ok": False, "error": f"单次导入条目过多（上限 {MAX_LOREBOOK_IMPORT_ENTRIES} 条）"}
    existing_ids = {
        str(e.get("id")) for e in dependencies.lorebook.list_entries(world_id)
    }
    imported = 0
    failures: list[dict[str, Any]] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            failures.append({"index": index, "error": "世界书条目必须是对象"})
            continue
        if len(str(raw.get("content", ""))) > MAX_LOREBOOK_CHARS:
            failures.append({
                "index": index,
                "error": f"世界书条目过长（上限 {MAX_LOREBOOK_CHARS} 字）",
            })
            continue
        # world_id 来自路径参数：批量端点替客户端统一盖章，避免逐条重复。
        candidate = {**raw, "world_id": world_id}
        if str(candidate.get("id") or "").strip() and str(candidate["id"]) in existing_ids:
            # 批量内/库内撞 id 的条目不得静默替换既有条目：改为重新生成 id，
            # 让重复导入表现为新增而不是覆盖。
            candidate["id"] = ""
        prepared, error = _prepare_entry(
            dependencies, candidate, existing_ids=existing_ids,
        )
        if error or prepared is None:
            failures.append({"index": index, "error": error or "世界书条目无效"})
            continue
        dependencies.lorebook.add_entry(prepared)
        # 冲突集 = 库内既有 id + 本批已导入 id，防止同批后续条目复用。
        existing_ids.add(prepared["id"])
        imported += 1
    if imported:
        rebuild_lorebook_index(dependencies, world_id)
        _sync_user_template_lorebook(dependencies, world_id)
    return {"ok": True, "imported": imported, "failed": failures}


def _prepare_entry(
    dependencies: WorldDependencies,
    entry: dict,
    *,
    existing_ids: set[str] | None,
) -> tuple[dict[str, Any] | None, str]:
    """Validate and normalize one entry; returns (entry, error)."""

    if not isinstance(entry, dict):
        return None, "世界书条目必须是对象"
    world_id = str(entry.get("world_id") or "").strip()
    name = str(entry.get("name") or "").strip()
    if not world_id:
        return None, "缺少 world_id"
    if dependencies.lorebook.get_world(world_id) is None:
        return None, "世界不存在"
    if not name:
        return None, "世界书条目名称不能为空"
    entry = dict(entry)
    entry["world_id"] = world_id
    entry["name"] = name
    entry_type = str(entry.get("type") or "other").strip()
    entry["type"] = entry_type if entry_type in _LOREBOOK_ENTRY_TYPES else "other"
    tier = str(entry.get("tier") or "background").strip()
    entry["tier"] = tier if tier in _LOREBOOK_TIERS else "background"
    if not str(entry.get("id") or "").strip():
        if existing_ids is None:
            existing_ids = {
                str(e.get("id")) for e in dependencies.lorebook.list_entries(world_id)
            }
        entry["id"] = _entry_id_from_name(world_id, name, existing_ids, 0)
    return entry, ""


def _entry_id_from_name(world_id: str, name: str, existing_ids: set[str], index: int) -> str:
    base = "".join(ch if ch.isalnum() else "_" for ch in (name or "entry").lower()).strip("_")
    base = base[:40] or f"entry_{index + 1}"
    entry_id = f"{world_id}_gen_{base}"
    if entry_id not in existing_ids:
        existing_ids.add(entry_id)
        return entry_id
    suffix = int(time.time() * 1000)
    while f"{entry_id}_{suffix}" in existing_ids:
        suffix += 1
    entry_id = f"{entry_id}_{suffix}"
    existing_ids.add(entry_id)
    return entry_id


def _normalize_generated_entry(raw: dict, world_id: str, existing_ids: set[str], index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    content = str(raw.get("content") or "").strip()
    if not name or not content:
        return None
    entry_type = str(raw.get("type") or "other").strip()
    if entry_type not in _LOREBOOK_ENTRY_TYPES:
        entry_type = "other"
    tier = str(raw.get("tier") or "background").strip()
    if tier not in _LOREBOOK_TIERS:
        tier = "background"
    keywords = raw.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = [str(keywords)]
    keywords = [str(k).strip() for k in keywords if str(k).strip()]
    if name not in keywords:
        keywords.insert(0, name)
    entry = {
        "id": _entry_id_from_name(world_id, name, existing_ids, index),
        "world_id": world_id,
        "name": name,
        "type": entry_type,
        "keywords": keywords[:12],
        "content": content[:_MAX_GENERATED_LOREBOOK_CONTENT],
        "tier": tier,
        "unreliable": bool(raw.get("unreliable", False)),
        "match_mode": "any",
        "order": 100 + index,
        "visibility": raw.get("visibility"),
    }
    creator.apply_generated_visibility(entry)
    return entry


async def generate_lorebook_entries(dependencies: WorldDependencies, world_id: str, prompt: str,
                                    language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "请输入要生成的世界书设定"}
    if not dependencies.llm_client:
        return {"ok": False, "error": "当前未配置 AI，无法自动生成世界书条目"}
    world = dependencies.lorebook.get_world(world_id)
    if not world:
        return {"ok": False, "error": "世界书不存在"}
    language = normalize_language(language or world.get("language", DEFAULT_LANGUAGE))

    existing_entries = dependencies.lorebook.list_entries(world_id)
    raw_entries = await creator.generate_lorebook_entries(
        dependencies.llm_client,
        prompt,
        world_name=world.get("name", ""),
        existing_names=[e.get("name", "") for e in existing_entries],
        max_tokens=dependencies.character_gen_max_tokens,
        language=language,
    )
    if not raw_entries:
        return {"ok": False, "error": "AI 返回内容解析失败，请换一种描述重试"}

    existing_ids = {e.get("id", "") for e in existing_entries}
    saved = []
    for index, raw in enumerate(raw_entries[:8]):
        entry = _normalize_generated_entry(raw, world_id, existing_ids, index)
        if not entry:
            continue
        dependencies.lorebook.add_entry(entry)
        saved.append(entry)
    if not saved:
        return {"ok": False, "error": "AI 没有生成可保存的条目，请补充更具体的设定"}
    rebuild_lorebook_index(dependencies, world_id)
    return {"ok": True, "entries": saved, "count": len(saved)}


def update_entry(
    dependencies: WorldDependencies,
    entry_id: str,
    updates: dict,
) -> dict[str, Any]:
    dependencies.lorebook.update_entry(entry_id, updates)
    # 获取条目所属世界以重建索引
    entry = dependencies.lorebook.get_entry(entry_id)
    if entry:
        world_id = entry.get("world_id", "")
        rebuild_lorebook_index(dependencies, world_id)
        _sync_user_template_lorebook(dependencies, world_id)
    return {"ok": True}


def delete_entry(
    dependencies: WorldDependencies,
    entry_id: str,
) -> dict[str, Any]:
    entry = dependencies.lorebook.get_entry(entry_id)
    world_id = entry.get("world_id", "") if entry else ""
    dependencies.lorebook.delete_entry(entry_id)
    if world_id:
        rebuild_lorebook_index(dependencies, world_id)
        _sync_user_template_lorebook(dependencies, world_id)
    return {"ok": True}


def delete_world(
    dependencies: WorldDependencies,
    world_id: str,
) -> dict[str, Any]:
    """删除世界及其所有条目。"""
    dependencies.lorebook.delete_world_cascade(world_id)
    _delete_user_template_file(dependencies, world_id)
    return {"ok": True}


def _delete_user_template_file(
    dependencies: WorldDependencies,
    world_id: str,
) -> None:
    """删除世界书时联动删除对应的用户模板文件（ai_/custom_），内置/插件模板不动。"""
    worlds_dir = dependencies.worlds_dir
    if not worlds_dir:
        return
    path = _template_path(worlds_dir, world_id)
    if path is None or not path.is_file() or not is_user_template_file(path, "worlds"):
        return
    try:
        path.unlink()
    except OSError:
        logger.warning("删除用户模板文件失败: %s", path, exc_info=True)


def _entry_to_template_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """条目 -> 模板 starter_lorebook 条目：保留业务字段，去掉内部追踪字段。

    与 plugins._entry_to_lorebook_entry 保持一致，实现世界书 <-> 模板无损往返。
    """
    skip = {"world_id", "source_plugin", "created_at", "updated_at"}
    keywords = entry.get("keywords")
    if not isinstance(keywords, list):
        keywords = [keywords] if keywords else []
    result = {k: v for k, v in entry.items() if k not in skip}
    result["keywords"] = [str(k).strip() for k in keywords if str(k).strip()]
    return result


def _sync_user_template_lorebook(
    dependencies: WorldDependencies,
    world_id: str,
) -> None:
    """条目 CRUD 后把世界书当前条目回写到用户模板的 starter_lorebook。

    仅对用户模板（ai_/custom_）生效；内置模板只读（启动覆盖）不回写。
    旧版手动世界若尚无模板，会在首次保存 GM 风格时完成物化。
    只同步 starter_lorebook，不动 world_name/default_rule/scene_image 等元信息。
    """
    if not world_id:
        return
    worlds_dir = dependencies.worlds_dir
    if not worlds_dir:
        return
    path = _template_path(worlds_dir, world_id)
    if path is None or not path.is_file() or not is_user_template_file(path, "worlds"):
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = dependencies.lorebook.list_entries(world_id)
        data["starter_lorebook"] = [
            _entry_to_template_entry(e) for e in entries if isinstance(e, dict)
        ]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except (OSError, ValueError):
        logger.warning("回写用户模板 starter_lorebook 失败: %s", path, exc_info=True)


def rebuild_lorebook_index(
    dependencies: WorldDependencies,
    world_id: str,
) -> None:
    """Invalidate the shared index so the next game rebuilds its own locale view."""
    invalidate = dependencies.invalidate_lorebook_index
    if invalidate is None or not world_id:
        return
    try:
        invalidate(world_id)
    except Exception:
        logger.exception("世界书索引失效标记失败: world_id=%s", world_id)


def list_world_templates(
    dependencies: WorldDependencies,
    language: str = "",
) -> dict[str, Any]:
    """列出所有可用的世界模板。"""
    templates = []
    seen: set[str] = set()
    worlds_dir = dependencies.worlds_dir
    if worlds_dir and worlds_dir.is_dir():
        for f in sorted(worlds_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                world_id = data.get("world_id", f.stem)
                if f.stem.endswith("_en") or f.stem.endswith("_ja"):
                    canonical_id = f.stem.rsplit("_", 1)[0]
                    canonical = worlds_dir / f"{canonical_id}.json"
                    if canonical.exists():
                        canonical_data = json.loads(canonical.read_text(encoding="utf-8"))
                        if int(canonical_data.get("world_schema_version", 1) or 1) >= 2:
                            continue
                if data.get("deprecated"):
                    continue
                if "_blank_" in str(world_id) and not data.get("starter_lorebook", []):
                    continue
                if int(data.get("world_schema_version", 1) or 1) >= 2:
                    data = load_content_world(worlds_dir, str(world_id), language) or data
                templates.append(_world_template_summary(data, f.stem))
                seen.add(str(world_id))
            except Exception as exc:
                raise ValueError(f"世界模板读取失败：{f}: {exc}") from exc
    for item in _plugin_world_templates(dependencies, language):
        if str(item.get("world_id") or "") not in seen:
            templates.append(item)
            seen.add(str(item.get("world_id") or ""))
    return {"templates": templates, "total": len(templates)}


def _is_game_scoped_template(data: dict[str, Any], world_id: str) -> bool:
    if data.get("_diceframe_managed") == "game":
        return True
    world_name = str(data.get("world_name") or "")
    return bool(
        data.get("custom") is True
        and _LEGACY_GAME_TEMPLATE_ID.fullmatch(world_id)
        and world_name.endswith(_LEGACY_GAME_TEMPLATE_SUFFIXES)
    )


def cleanup_orphan_game_templates(
    dependencies: WorldDependencies,
    world_id: str = "",
) -> int:
    """Remove generated copy/blank templates after their last game is gone."""
    worlds_dir = dependencies.worlds_dir
    if not worlds_dir or not worlds_dir.is_dir():
        return 0
    referenced = {
        str(getattr(instance, "world_id", "") or "")
        for instance in dependencies.list_instances()
    }
    removed = 0
    candidates = sorted(worlds_dir.glob("*.json"))
    if world_id:
        candidates = [path for path in candidates if path.stem == world_id]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            template_world_id = str(data.get("world_id") or path.stem)
            if world_id and template_world_id != world_id:
                continue
            if template_world_id in referenced or not _is_game_scoped_template(data, template_world_id):
                continue
            path.unlink()
            removed += 1
            logger.info("已清理孤立的对局临时世界模板: %s", path.name)
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("清理对局临时世界模板失败: %s", path, exc_info=True)
    return removed


def _world_template_summary(data: dict[str, Any], fallback_id: str, source: str | None = None) -> dict[str, Any]:
    world_id = data.get("world_id", fallback_id)
    if source is None:
        source = (
            "user"
            if bool(data.get("custom")) or fallback_id.startswith(("custom_", "ai_"))
            else "builtin"
        )
    return {
        "world_id": world_id,
        "world_name": data.get("world_name", fallback_id),
        "description": data.get("description", ""),
        "language": data.get("language", ""),
        "active_locale": data.get("active_locale", ""),
        "suggested_difficulty": data.get("suggested_difficulty", "标准"),
        "default_rule": data.get("default_rule", "freeform_fantasy"),
        "recommended_rules": _recommended_rules(data),
        "scene_image": data.get("scene_image"),
        "lorebook_count": len(data.get("starter_lorebook", [])),
        "source": source,
        "game_scoped": _is_game_scoped_template(data, str(world_id)),
        # 仅用户模板暴露可编辑的 GM 风格；内置/插件只读，前端据此显示编辑区或锁定提示。
        "gm_style": normalize_gm_style(data.get("gm_style")) if source == "user" else None,
    }


def _recommended_rules(data: dict[str, Any]) -> list[str]:
    """世界模板的推荐规则列表（可选字段）；去重、去空，缺失时返回空列表。"""
    raw = data.get("recommended_rules")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw[:6]:
        if not isinstance(item, str):
            continue
        rule_id = item.strip()
        if rule_id and rule_id not in result:
            result.append(rule_id)
    return result


def _plugin_world_templates(
    dependencies: WorldDependencies,
    language: str = "",
) -> list[dict[str, Any]]:
    plugin_host = dependencies.plugin_host
    if not plugin_host:
        return []
    result = []
    for item in plugin_host.contributions.list("world_template"):
        try:
            data = plugin_host.load_world_template(item.key, language) or {}
            summary = _world_template_summary(data, item.path.stem, source="plugin")
            summary["plugin_id"] = item.plugin_id
            summary["plugin_name"] = item.plugin_name
            summary["readonly"] = True
            result.append(summary)
        except Exception as exc:
            raise ValueError(f"插件世界模板读取失败：{item.path}: {exc}") from exc
    return result
