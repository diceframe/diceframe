"""Creator-owned content package export; legacy plugin API delegates here."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.content.contracts import canonical_id
from src.content.rule_locale import materialize_rule
from src.content_modules.projection import entries_for_world
from src.engine.language import normalize_language
from src.plugin_host.content import safe_id_part
from src.rules.loader import RuleBundleLoader

@dataclass(frozen=True)
class PluginExportMediaDependencies:
    package_scene_image: Callable[
        [Any, dict[str, str | bytes]], dict[str, str] | None
    ]
    package_content_map: Callable[..., Any]
    avatar_file: Callable[[str], Path | None]
    generated_image_file: Callable[[str], Path | None]
    plugin_asset_path: Callable[[str, str], Path]


@dataclass(frozen=True)
class PluginExportDependencies:
    plugin_host: Any | None
    lorebook: Any | None
    rules_dir: Path
    list_character_cards: Callable[[], dict[str, Any]]
    media: PluginExportMediaDependencies
    # Optional owner dependencies for creator-studio module exports.  Keeping
    # this at the export boundary avoids making the generic plugin service own
    # adventure authoring or runtime resolution.
    adventure_resolver: Any | None = None


def _scene_image_references(
    data: dict[str, Any], override: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Read a legacy singular image or the newer ordered image pool."""

    if isinstance(override, dict):
        return [override]
    raw_pool = data.get("scene_images")
    if isinstance(raw_pool, list) and raw_pool:
        return [item for item in raw_pool if isinstance(item, dict)]
    single = data.get("scene_image")
    return [single] if isinstance(single, dict) else []


def export_content_pack(
    dependencies: PluginExportDependencies,
    plugin_id: str,
    name: str,
    version: str,
    description: str,
    world_id: str = "",
    card_ids: list[str] | None = None,
    rule_id: str = "",
    flat: bool = False,
    include_portraits: bool = True,
    include_scene_images: bool = True,
    world_scene_image: dict[str, Any] | None = None,
    rule_scene_image: dict[str, Any] | None = None,
    include_map: bool = True,
    map_background: dict[str, Any] | None = None,
    map_icons: list[dict[str, Any]] | None = None,
    language: str = "",
    adventure_ids: list[str] | None = None,
    lorebook_ids: list[str] | None = None,
    adventure_refs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """把应用内的世界/角色卡/规则导出成一个内容包 .dfplugin。

    以世界为锚点：世界的世界书条目 -> world_template.starter_lorebook（无损）；
    自定义规则 -> content/rules/；勾选的角色卡 -> content/characters/。
    返回 {"ok": True, "payload": bytes, "filename": ...}，payload 是 .dfplugin 字节。
    """
    host = dependencies.plugin_host
    if not host:
        return {"ok": False, "error": "插件宿主未启用"}
    plugin_id = (plugin_id or "").strip()
    name = (name or "").strip()
    version = (version or "0.1.0").strip() or "0.1.0"
    description = (description or "").strip()
    world_id = (world_id or "").strip()
    rule_id = (rule_id or "").strip()
    card_ids = [str(c).strip() for c in card_ids if str(c).strip()] if isinstance(card_ids, list) else []
    adventure_ids = [str(c).strip() for c in adventure_ids if str(c).strip()] if isinstance(adventure_ids, list) else []
    lorebook_ids = [str(c).strip() for c in lorebook_ids if str(c).strip()] if isinstance(lorebook_ids, list) else []
    adventure_refs = [
        {
            "adventure_id": str(item.get("adventure_id") or "").strip(),
            "source_kind": str(item.get("source_kind") or "").strip(),
            "source_id": str(item.get("source_id") or "").strip(),
        }
        for item in adventure_refs
        if isinstance(item, dict) and str(item.get("adventure_id") or "").strip()
    ] if isinstance(adventure_refs, list) else []
    if not plugin_id or not name:
        return {"ok": False, "error": "请填写内容包 ID 和名称"}

    files: dict[str, str | bytes] = {}
    has_world = has_rule = has_cards = has_adventures = has_lorebooks = False
    map_package = None
    try:
        if canonical_id(plugin_id) != plugin_id:
            raise ValueError
    except ValueError:
        return {"ok": False, "error": "内容包 ID 必须使用小写英文开头，且只能包含小写英文、数字、下划线或短横线"}
    exported_rule_id = _export_resource_id(rule_id, "rule") if rule_id else ""
    exported_world_id = _export_resource_id(world_id, "world") if world_id else ""
    world_default_rule = exported_rule_id
    pack_locale = normalize_language(language or "zh-CN")
    world: dict[str, Any] | None = None
    if world_id:
        lorebook = dependencies.lorebook
        world = lorebook.get_world(world_id) if lorebook else None
        if not world:
            return {"ok": False, "error": "世界不存在"}
        if not language:
            pack_locale = normalize_language(str(world.get("language") or "zh-CN"))

    if rule_id:
        rule_files = _rule_files(
            rule_id,
            dependencies.rules_dir,
            exported_rule_id=exported_rule_id,
            default_locale=pack_locale,
        )
        for path, raw in list(rule_files.items()):
            try:
                rule_data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not path.startswith("content/rules/"):
                continue
            references = _scene_image_references(rule_data, rule_scene_image)
            if include_scene_images and references:
                packaged_refs = []
                for reference in references:
                    packaged = dependencies.media.package_scene_image(reference, files)
                    if packaged:
                        packaged_refs.append(packaged)
                    elif isinstance(rule_scene_image, dict):
                        return {"ok": False, "error": "无法读取所选规则头图"}
                if packaged_refs:
                    # Keep the legacy singular field as the first pool member;
                    # newer readers may use the full list for random defaults.
                    rule_data["scene_image"] = packaged_refs[0]
                    if len(packaged_refs) > 1 or isinstance(rule_data.get("scene_images"), list):
                        rule_data["scene_images"] = packaged_refs
                else:
                    rule_data.pop("scene_image", None)
                    rule_data.pop("scene_images", None)
            else:
                rule_data.pop("scene_image", None)
                rule_data.pop("scene_images", None)
            rule_files[path] = json.dumps(rule_data, ensure_ascii=False, indent=2)
        files.update(rule_files)
        has_rule = bool(rule_files)

    if world_id:
        assert world is not None
        assert dependencies.lorebook is not None
        entries = entries_for_world(dependencies.lorebook, world_id)
        export_world = dict(world)
        export_world["id"] = exported_world_id
        export_world["world_id"] = exported_world_id
        template = _world_to_template(
            export_world,
            entries,
            world_default_rule,
            default_locale=pack_locale,
        )
        if include_map:
            try:
                map_package = dependencies.media.package_content_map(
                    plugin_id,
                    name,
                    export_world,
                    entries,
                    files,
                    background_selection=map_background,
                    icon_uploads=map_icons,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if map_package and map_package.default_map:
                template["default_map"] = map_package.default_map
        references = _scene_image_references(template, world_scene_image)
        if include_scene_images and references:
            packaged_refs = []
            for reference in references:
                packaged = dependencies.media.package_scene_image(reference, files)
                if packaged:
                    packaged_refs.append(packaged)
                elif isinstance(world_scene_image, dict):
                    return {"ok": False, "error": "无法读取所选世界头图"}
            if packaged_refs:
                template["scene_image"] = packaged_refs[0]
                if len(packaged_refs) > 1 or isinstance(template.get("scene_images"), list):
                    template["scene_images"] = packaged_refs
            else:
                template.pop("scene_image", None)
                template.pop("scene_images", None)
        else:
            template.pop("scene_image", None)
            template.pop("scene_images", None)
        for entry in template.get("starter_lorebook", []):
            _package_record_portrait(
                dependencies.media, entry, files, include=include_portraits,
            )
        files[f"content/worlds/{exported_world_id}.json"] = json.dumps(template, ensure_ascii=False, indent=2)
        has_world = True

    if card_ids:
        selected = set(card_ids)
        for card in dependencies.list_character_cards().get("cards", []):
            if str(card.get("id") or "") not in selected:
                continue
            packaged_portrait = (
                _package_portrait(dependencies.media, card.get("portrait"), files)
                if include_portraits
                else {}
            )
            tmpl = _card_to_character_template(
                card,
                world_id=exported_world_id,
                rule_id=exported_rule_id,
                packaged_portrait=packaged_portrait,
            )
            fname = str(tmpl["id"])
            files[f"content/characters/{fname}.json"] = json.dumps(tmpl, ensure_ascii=False, indent=2)
            has_cards = True

    # Adventure packages are copied through the server-owned resolver.  The
    # exported package keeps each bundle directory intact so installation can
    # validate the same manifest/graph/runtime contract as a hand-authored
    # module.  No arbitrary document is promoted to mechanics here.
    selected_adventures = set(adventure_ids)
    selected_adventure_refs = {
        (item["source_kind"], item["source_id"], item["adventure_id"])
        for item in adventure_refs
    }
    if selected_adventures or selected_adventure_refs:
        resolver = dependencies.adventure_resolver
        if resolver is None:
            return {"ok": False, "error": "冒险来源解析器未启用"}
        resolutions = resolver.list_resolutions(pack_locale)
        if selected_adventures and not selected_adventure_refs:
            ambiguous = sorted({adventure_id for adventure_id in selected_adventures if sum(
                1 for item in resolutions if str(item.bundle.manifest.adventure_id) == adventure_id
            ) > 1})
            if ambiguous:
                return {"ok": False, "error": f"冒险来源不唯一，请重新选择来源：{', '.join(ambiguous)}"}
        adventure_directories: dict[str, tuple[str, str, str]] = {}
        for resolution in resolutions:
            bundle = resolution.bundle
            adventure_id = str(bundle.manifest.adventure_id)
            source_ref = (
                str(getattr(resolution, "source_kind", "")),
                str(getattr(resolution, "source_id", "") or ""),
                adventure_id,
            )
            if selected_adventure_refs:
                if source_ref not in selected_adventure_refs:
                    continue
            elif adventure_id not in selected_adventures:
                continue
            directory = safe_id_part(bundle.root.name)
            identity = source_ref
            previous = adventure_directories.get(directory)
            if previous is not None and previous != identity:
                return {"ok": False, "error": f"冒险目录名冲突：{directory}，请分别导出内容包"}
            adventure_directories[directory] = identity
            for path in sorted(bundle.root.rglob("*.json")):
                relative = path.relative_to(bundle.root).as_posix()
                raw = path.read_bytes()
                if world_id and exported_world_id != world_id:
                    raw = _rewrite_adventure_world_ref(raw, world_id, exported_world_id)
                files[f"adventures/{directory}/{relative}"] = raw
            has_adventures = True
        if selected_adventure_refs:
            available_refs = {
                (
                    str(getattr(item, "source_kind", "")),
                    str(getattr(item, "source_id", "") or ""),
                    str(item.bundle.manifest.adventure_id),
                )
                for item in resolutions
            }
            missing = sorted(
                f"{kind}:{source_id}:{adventure_id}" if source_id else f"{kind}:{adventure_id}"
                for kind, source_id, adventure_id in selected_adventure_refs - available_refs
            )
        else:
            missing = sorted(selected_adventures - {
                str(item.bundle.manifest.adventure_id) for item in resolutions
            })
        if missing:
            return {"ok": False, "error": f"冒险不存在：{', '.join(missing)}"}

    if lorebook_ids:
        if dependencies.lorebook is None:
            return {"ok": False, "error": "资料册存储未启用"}
        books = {str(book.get("id") or ""): book for book in dependencies.lorebook.list_lorebooks()}
        missing = sorted(set(lorebook_ids) - set(books))
        if missing:
            return {"ok": False, "error": f"资料册不存在：{', '.join(missing)}"}
        for book_id in lorebook_ids:
            book = dict(books[book_id])
            book["entries"] = dependencies.lorebook.list_book_entries(book_id)
            book_path = safe_id_part(book_id)
            existing = next((path for path in files if path == f"content/lorebooks/{book_path}.json"), None)
            if existing is not None:
                return {"ok": False, "error": f"资料册文件名冲突：{book_path}，请分别导出"}
            files[f"content/lorebooks/{book_path}.json"] = json.dumps(
                book, ensure_ascii=False, indent=2,
            )
        has_lorebooks = True

    if not (has_world or has_rule or has_cards or has_adventures or has_lorebooks):
        return {"ok": False, "error": "请至少选择一个世界、资料册、冒险、角色卡或规则"}

    has_portraits = any(path.startswith("assets/portraits/") for path in files)
    has_scene_images = any(path.startswith("assets/scenes/") for path in files)
    manifest = build_content_pack_manifest(
        plugin_id, name, version, description, has_world, has_rule, has_cards, has_portraits, has_scene_images,
        bool(map_package and map_package.has_definitions),
        bool(map_package and map_package.has_locations),
        bool(map_package and map_package.has_icons),
        bool(map_package and map_package.has_backgrounds),
        default_locale=pack_locale,
        has_adventures=has_adventures,
        has_lorebooks=has_lorebooks,
        adventure_directories=(
            sorted({path.split("/")[1] for path in files if path.startswith("adventures/") and len(path.split("/")) > 2})
            if has_adventures else []
        ),
    )
    files["plugin.json"] = json.dumps(manifest, ensure_ascii=False, indent=2)
    files["config.schema.json"] = json.dumps(_default_config_schema(name), ensure_ascii=False, indent=2)
    files["README.md"] = _default_readme(
        name,
        description,
        has_world,
        has_rule,
        has_cards,
        bool(map_package and map_package.has_map),
        language=pack_locale,
        has_adventures=has_adventures,
        has_lorebooks=has_lorebooks,
    )

    payload = host.package_files(plugin_id, files, flat=flat)
    filename = f"{plugin_id}-{version}-src.zip" if flat else f"{plugin_id}-{version}.dfplugin"
    return {"ok": True, "payload": payload, "filename": filename}


def build_content_pack_manifest(
    plugin_id: str, name: str, version: str, description: str,
    has_world: bool, has_rule: bool, has_cards: bool, has_portraits: bool = False,
    has_scene_images: bool = False,
    has_map_definitions: bool = False, has_map_locations: bool = False,
    has_map_icons: bool = False, has_map_backgrounds: bool = False,
    default_locale: str = "zh-CN",
    has_adventures: bool = False,
    has_lorebooks: bool = False,
    adventure_directories: list[str] | None = None,
) -> dict[str, Any]:
    contributes: dict[str, list[str]] = {}
    if has_world:
        contributes["world_templates"] = ["content/worlds/*.json"]
    if has_rule:
        contributes["rules"] = ["content/rules/*.json"]
    if has_cards:
        contributes["character_templates"] = ["content/characters/*.json"]
    if has_lorebooks:
        contributes["lorebooks"] = ["content/lorebooks/*.json"]
    if has_portraits:
        contributes["portraits"] = ["assets/portraits/*"]
    if has_scene_images:
        contributes["scene_images"] = ["assets/scenes/*"]
    if has_map_definitions:
        contributes["map_definitions"] = ["maps/definitions/*.json"]
    if has_map_locations:
        contributes["map_locations"] = ["maps/locations/*.json"]
    if has_map_icons:
        contributes["map_icons"] = ["maps/icons/*.webp"]
    if has_map_backgrounds:
        contributes["map_backgrounds"] = ["maps/backgrounds/*.webp"]
    capabilities: list[str] = []
    if has_world:
        capabilities.append("content.world")
    if has_rule:
        capabilities.append("content.rule")
    if has_cards:
        capabilities.append("content.character-template")
    if has_lorebooks:
        capabilities.append("content.lorebook")
    if has_adventures:
        capabilities.append("content.adventure")
    if has_scene_images:
        capabilities.append("content.scene-image")
    if any((has_map_definitions, has_map_locations, has_map_icons, has_map_backgrounds)):
        capabilities.append("content.map")
    manifest = {
        "schema_version": 1,
        "content_schema_version": 2,
        "locale_schema_version": 1,
        "default_locale": normalize_language(default_locale or "zh-CN"),
        "id": plugin_id,
        "name": name,
        "version": version,
        "description": description,
        "plugin_type": "content-pack",
        "config_schema": "config.schema.json",
        "capabilities": capabilities,
        "permissions": ["plugin.config", "content.read", "content.import"],
        "contributes": contributes,
        "docs": "README.md",
    }
    if has_adventures:
        # adventure_packages is the install-time declaration consumed by the
        # module validator; the resource catalog remains read-only.
        manifest["content_profile"] = "adventure-module"
        manifest["content_delivery_mode"] = "catalog"
        manifest["adventure_packages"] = [
            f"adventures/{directory}" for directory in (adventure_directories or [])
        ]
        contributes["adventure_resources"] = ["adventures/*/*.json"]
        manifest["contributes"] = contributes
    return manifest


def _default_config_schema(name: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "title": f"启用{name}",
                "description": "启用后，本包的世界、规则与角色卡会出现在创建游戏界面和插件内容目录。",
                "default": False,
                "ui": {"control": "switch", "order": 10},
            }
        },
    }


def _default_readme(
    name: str,
    description: str,
    has_world: bool,
    has_rule: bool,
    has_cards: bool,
    has_map: bool = False,
    language: str = "zh-CN",
    has_adventures: bool = False,
    has_lorebooks: bool = False,
) -> str:
    """按内容语言生成兜底 README；作者自带 README 时不会被调用。"""
    lang_key = "en" if (language or "").lower().startswith("en") else ("ja" if (language or "").lower().startswith("ja") else "zh")
    copy = {
        "zh": {
            "content": "## 内容",
            "world": "- 世界模板（含世界书条目，启用后自动灌入）",
            "rule": "- 规则",
            "cards": "- 角色模板（可在插件页「内容导入」里导入角色卡库）",
            "map": "- 场景地图（地点、地图定义及所选图标/底图）",
            "adventures": "- 冒险包（安装后从冒险工作台选择并开局）",
            "lorebooks": "- 资料册（可在创建游戏时逐册选择）",
            "usage": "## 用法",
            "step1": "1. 打开「内容包」页，在「本地导入」里选择本 .dfplugin 并安装",
            "step2": "2. 在本内容包的详情里启用它",
            "step3": "3. 创建游戏时选择本世界与规则",
        },
        "en": {
            "content": "## Contents",
            "world": "- World template (with lorebook entries, auto-imported when enabled)",
            "rule": "- Rule",
            "cards": "- Character templates (importable from Plugins → Content import)",
            "map": "- Scene map (locations, map definitions, icons/backgrounds)",
            "adventures": "- Adventure packages (select one in the adventure workbench after installation)",
            "lorebooks": "- Lorebooks (choose them per game during game creation)",
            "usage": "## Usage",
            "step1": "1. Open Content packages and install this .dfplugin from Local import",
            "step2": "2. Enable this content pack from its details",
            "step3": "3. Pick this world and rule when creating a game",
        },
        "ja": {
            "content": "## 内容",
            "world": "- ワールドテンプレート（ロアブックエントリを含み、有効化時に自動で取り込みます）",
            "rule": "- ルール",
            "cards": "- キャラクターテンプレート（プラグイン画面からインポート可能）",
            "map": "- シーンマップ（地点・マップ定義・選択したアイコン/背景）",
            "adventures": "- 冒険パッケージ（インストール後に冒険ワークベンチで選択）",
            "lorebooks": "- ロアブック（ゲーム作成時に冊ごとに選択）",
            "usage": "## 使い方",
            "step1": "1. 「コンテンツパック」ページの「ローカルインポート」で本 .dfplugin をインストール",
            "step2": "2. 詳細画面で本コンテンツパックを有効化",
            "step3": "3. ゲーム作成時に本ワールドとルールを選択",
        },
    }[lang_key]
    lines = [f"# {name}", ""]
    if description:
        lines += [description, ""]
    lines.append(copy["content"])
    if has_world:
        lines.append(copy["world"])
    if has_rule:
        lines.append(copy["rule"])
    if has_cards:
        lines.append(copy["cards"])
    if has_map:
        lines.append(copy["map"])
    if has_adventures:
        lines.append(copy["adventures"])
    if has_lorebooks:
        lines.append(copy["lorebooks"])
    lines += ["", copy["usage"], copy["step1"], copy["step2"], copy["step3"], ""]
    return "\n".join(lines)


def _world_to_template(
    world: dict[str, Any],
    entries: list[dict[str, Any]],
    default_rule: str = "",
    *,
    default_locale: str = "zh-CN",
) -> dict[str, Any]:
    world_id = str(world.get("id") or world.get("world_id") or "")
    template: dict[str, Any] = {
        "world_schema_version": 2,
        "default_locale": normalize_language(default_locale or world.get("language") or "zh-CN"),
        "world_id": world_id,
        "world_name": str(world.get("name") or world.get("world_name") or world_id),
        "description": str(world.get("description") or ""),
        "language": str(world.get("language") or "zh-CN"),
        "starter_lorebook": [_entry_to_lorebook_entry(e) for e in entries if isinstance(e, dict)],
    }
    if default_rule:
        template["default_rule"] = default_rule
    for key in ("scene_image", "scene_images"):
        value = world.get(key)
        if key == "scene_image" and isinstance(value, dict):
            template[key] = dict(value)
        elif key == "scene_images" and isinstance(value, list):
            template[key] = [dict(item) for item in value if isinstance(item, dict)]
    return template


def _entry_to_lorebook_entry(entry: dict[str, Any]) -> dict[str, Any]:
    # 保留条目全部业务字段，仅去掉内部追踪字段（world_id/source_plugin/时间戳）。
    # starter_lorebook 经 sync_lorebooks 用 dict(raw) 原样写回 add_entry，实现
    # 导出 -> 装回的元数据无损往返（unreliable/match_mode/order/group/sticky 等）。
    # 不把 spell/class 拆到 content/spells|classes：那会改走 _content_to_lore_entry，
    # 其 _content_description 会把 content 包装成 "类型：法术\n内容：..."，反而破坏保真。
    skip = {"world_id", "source_plugin", "created_at", "updated_at"}
    keywords = entry.get("keywords")
    if not isinstance(keywords, list):
        keywords = [keywords] if keywords else []
    result = {k: v for k, v in entry.items() if k not in skip}
    result["keywords"] = [str(k).strip() for k in keywords if str(k).strip()]
    result.setdefault("type", "other")
    result.setdefault("tier", "background")
    return result


def _card_to_character_template(
    card: dict[str, Any],
    world_id: str = "",
    rule_id: str = "",
    packaged_portrait: dict[str, str] | None = None,
) -> dict[str, Any]:
    # 去掉应用内部字段：source 是运行期来源标记、plugin_content_id 是插件资源回链、
    # schema_version/raw_sillytavern 是导入元数据、source_plugin 会泄露原插件身份。
    # portrait 是 {kind, id/asset_id} 引用（不是 base64）：builtin 可移植故保留，
    # upload 的 asset_id 在目标用户不存在会变成失效引用，故丢弃。
    skip = {"source", "plugin_content_id", "schema_version", "raw_sillytavern", "source_plugin", "portrait"}
    template = {k: v for k, v in card.items() if k not in skip}
    portrait = packaged_portrait if packaged_portrait is not None else card.get("portrait")
    if isinstance(portrait, dict) and portrait.get("kind") in {"builtin", "asset"}:
        template["portrait"] = dict(portrait)
    name = str(card.get("character_name") or card.get("id") or "character")
    template.setdefault("character_name", name)
    template["id"] = _export_resource_id(str(card.get("id") or name), "character")
    if world_id:
        template["world_id"] = world_id
    if rule_id:
        template["rule_id"] = rule_id
    return template


def _rule_files(
    rule_id: str,
    rules_dir: Path,
    *,
    exported_rule_id: str = "",
    default_locale: str = "zh-CN",
) -> dict[str, str]:
    """Export one canonical V2 rule plus typed locale overlays."""
    files: dict[str, str] = {}
    base = rules_dir / f"{rule_id}.json"
    if not base.exists():
        return files
    target_id = exported_rule_id or _export_resource_id(rule_id, "rule")
    raw = json.loads(base.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("规则文件必须是 JSON 对象")
    source_is_v2 = int(raw.get("rule_schema_version", 1) or 1) >= 2
    core = RuleBundleLoader().load(base)
    core = dict(core)
    core.pop("extends", None)
    core["rule_id"] = target_id
    core["rule_schema_version"] = 2
    core["default_locale"] = normalize_language(default_locale or raw.get("default_locale") or "zh-CN")
    core["locale_schema_version"] = 1
    files[f"content/rules/{target_id}.json"] = json.dumps(core, ensure_ascii=False, indent=2)

    if source_is_v2:
        for locale_dir in sorted((rules_dir / "locales").glob("*")):
            overlay_path = locale_dir / f"{rule_id}.json"
            if not overlay_path.is_file():
                continue
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            if not isinstance(overlay, dict):
                raise ValueError(f"规则 locale 必须是 JSON 对象：{overlay_path}")
            overlay = dict(overlay)
            overlay["target"] = {"kind": "rule", "id": target_id}
            materialize_rule(core, overlay)
            locale = str(overlay.get("locale") or locale_dir.name)
            files[f"locales/{locale}/rules/{target_id}.json"] = json.dumps(
                overlay, ensure_ascii=False, indent=2,
            )
    else:
        for suffix, locale in (("en", "en"), ("ja", "ja")):
            legacy = rules_dir / f"{rule_id}_{suffix}.json"
            if not legacy.is_file():
                continue
            localized = RuleBundleLoader().load(legacy)
            overlay = _legacy_rule_locale_overlay(core, localized, locale, target_id)
            materialize_rule(core, overlay)
            files[f"locales/{locale}/rules/{target_id}.json"] = json.dumps(
                overlay, ensure_ascii=False, indent=2,
            )
    return files


def _legacy_rule_locale_overlay(
    core: dict[str, Any],
    localized: dict[str, Any],
    locale: str,
    target_id: str,
) -> dict[str, Any]:
    """Adapt a V1 full-language copy into V2 display-only fields."""
    display_fields = (
        "rule_name", "name", "description", "attr_hint", "skill_hint",
        "gm_prompt_appendix", "difficulty_instructions", "currency",
    )
    overlay: dict[str, Any] = {
        "locale_schema_version": 1,
        "locale": locale,
        "target": {"kind": "rule", "id": target_id},
        "rule": {key: localized[key] for key in display_fields if key in localized},
    }
    for collection, identity_key, allowed_fields in (
        ("attributes", "key", ("name", "label", "hint")),
        ("classes", "id", ("name", "description")),
        ("special_stats", "key", ("name", "description", "label", "hint", "flavor")),
    ):
        core_items = [item for item in core.get(collection, []) if isinstance(item, dict)]
        localized_items = [item for item in localized.get(collection, []) if isinstance(item, dict)]
        values: dict[str, dict[str, Any]] = {}
        for index, core_item in enumerate(core_items):
            identity_value = str(core_item.get(identity_key) or "")
            if not identity_value or index >= len(localized_items):
                continue
            display = {
                field: localized_items[index][field]
                for field in allowed_fields if field in localized_items[index]
            }
            if display:
                values[identity_value] = display
        if values:
            overlay[collection] = values
    core_item_map = core.get("items")
    localized_item_map = localized.get("items")
    if isinstance(core_item_map, dict) and isinstance(localized_item_map, dict):
        values = {
            str(identity_value): {
                field: localized_item_map[identity_value][field]
                for field in ("name", "description")
                if isinstance(localized_item_map.get(identity_value), dict)
                and field in localized_item_map[identity_value]
            }
            for identity_value in core_item_map
            if identity_value in localized_item_map
        }
        overlay["items"] = {key: value for key, value in values.items() if value}
    return overlay


def _export_resource_id(value: str, prefix: str) -> str:
    """Keep canonical ids; deterministically hash legacy/non-ASCII identities."""
    text = str(value or "").strip()
    try:
        return canonical_id(text)
    except ValueError:
        ascii_slug = "".join(
            ch.lower() if ch.isascii() and ch.isalnum() else "_"
            for ch in text
        ).strip("_")
        ascii_slug = "_".join(part for part in ascii_slug.split("_") if part)
        if ascii_slug and ascii_slug[0].isalpha():
            try:
                return canonical_id(ascii_slug[:80])
            except ValueError:
                pass
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return canonical_id(f"{prefix}_{digest}")


def _rewrite_adventure_world_ref(raw: bytes, original_world_id: str, exported_world_id: str) -> bytes:
    """Keep a bundled adventure launchable when its selected world was renamed.

    Exported world ids are normally stable.  Legacy/non-canonical ids are
    normalized by the pack exporter, so update only the two declared JSON
    fields that carry the recommended world identity.  Invalid or unrelated
    JSON is returned byte-for-byte and remains subject to the normal bundle
    validator after installation.
    """
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw
    if not isinstance(value, dict):
        return raw
    changed = False
    for key in ("recommended_world_id", "world_id"):
        if str(value.get(key) or "") == original_world_id:
            value[key] = exported_world_id
            changed = True
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") if changed else raw



def _package_record_portrait(
    dependencies: "PluginExportMediaDependencies",
    record: dict[str, Any],
    files: dict[str, str | bytes],
    *,
    include: bool,
) -> None:
    portrait = _package_portrait(dependencies, record.get("portrait"), files) if include else None
    if portrait:
        record["portrait"] = portrait
    else:
        record.pop("portrait", None)


def _package_portrait(
    dependencies: "PluginExportMediaDependencies",
    portrait: Any,
    files: dict[str, str | bytes],
) -> dict[str, str] | None:
    if not isinstance(portrait, dict):
        return None
    kind = str(portrait.get("kind") or "")
    if kind == "builtin":
        portrait_id = str(portrait.get("id") or "").strip()
        return {"kind": "builtin", "id": portrait_id} if portrait_id else None
    source: Path | None = None
    if kind == "upload":
        source = dependencies.avatar_file(str(portrait.get("asset_id") or ""))
    elif kind == "generated":
        source = dependencies.generated_image_file(str(portrait.get("asset_id") or ""))
    elif kind == "plugin":
        plugin_id = str(portrait.get("plugin_id") or "")
        if plugin_id:
            source = dependencies.plugin_asset_path(plugin_id, str(portrait.get("path") or ""))
    if source is None or not source.is_file() or source.stat().st_size > 3 * 1024 * 1024:
        return None
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    suffix = source.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".webp"
    relative_path = f"assets/portraits/{digest}{suffix}"
    files.setdefault(relative_path, payload)
    return {"kind": "asset", "path": relative_path}
