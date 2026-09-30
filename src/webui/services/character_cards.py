"""角色卡库服务：列表 / 保存 / 更新 / 删除 / SillyTavern 卡导入。"""

from __future__ import annotations

import base64
import copy
import io
import json
import logging
import tempfile
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.engine.character_utils import parse_character_card_document, parse_tavern_card
from src.content_modules.refs import ContentDraft, collect_content_refs
from src.lorebook.importer import commit_lorebook_import, draft_lorebook_import
from src.webui.character_card_projection import card_signature, dedupe_cards

logger = logging.getLogger("trpg")


def character_content_draft(
    card: dict[str, Any], *, source_id: str = "local-card",
    kind: str = "character_template",
) -> ContentDraft:
    """Produce the shared, side-effect-free draft used by card adapters."""

    safe_source = "".join(ch.lower() if ch.isascii() and (ch.isalnum() or ch in "_-") else "_" for ch in source_id).strip("_") or "local-card"
    safe_external = "".join(
        ch.lower() if ch.isascii() and (ch.isalnum() or ch in "_-") else "_"
        for ch in str(card.get("id") or card.get("character_name") or "card")
    ).strip("_").lower() or "card"
    if not safe_external[0].isalpha() or not safe_external[0].isascii():
        safe_external = f"card-{safe_external}"
    return ContentDraft(
        kind=kind,
        source_kind="device",
        source_id=safe_source[:120],
        external_id=safe_external[:96],
        payload=copy.deepcopy(card),
        references=collect_content_refs(card, default_source=f"device:{safe_source[:120]}"),
        provenance={"adapter": "character_card"},
    )


@dataclass(frozen=True)
class CharacterCardDependencies:
    cards_path: Path
    ruleset_card_metadata: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None
    normalize_ruleset_card: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    is_ruleset_card: Callable[[dict[str, Any]], bool] | None = None
    lorebook: Any | None = None
    rebuild_lorebook_index: Callable[[str], None] | None = None


def _read_cards(dependencies: CharacterCardDependencies) -> list[dict[str, Any]]:
    path = dependencies.cards_path
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        logger.exception("读取角色卡库失败: %s", path)
        return []


def _write_cards(
    dependencies: CharacterCardDependencies,
    cards: list[dict[str, Any]],
) -> None:
    path = dependencies.cards_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _new_card_id(prefix: str) -> str:
    # time_ns() alone collides on coarse clocks (Windows), so two quick saves
    # could share an id and the second would overwrite the first.
    return f"{prefix}_{time.time_ns()}_{uuid.uuid4().hex[:8]}"


def _to_character_card(character: dict, source: str = "") -> dict[str, Any]:
    cs = character.get("character_sheet", {}) if isinstance(character.get("character_sheet"), dict) else character
    name = character.get("character_name") or cs.get("character_name") or "冒险者"
    card: dict[str, Any] = {
        "id": character.get("card_id") or character.get("id") or cs.get("card_id") or cs.get("id") or _new_card_id("card"),
        "schema_version": 2,
        "character_name": name,
        "race": cs.get("race", character.get("race", "人类")),
        "class": cs.get("class", character.get("class", "冒险者")),
        "source": source,
    }
    # A library card is a reusable blueprint, not a snapshot of one running
    # game. Runtime-only HP, XP, death and temporary status are recomputed when
    # the card joins a game under its target rule.
    for key, default in (
        ("identity", {}),
        ("attributes", {}),
        ("skills", []),
        ("background", ""),
        ("equipment", []),
        ("inventory", []),
        ("key_items", []),
        ("gold", 30),
        ("currency", {}),
        ("portrait", {}),
    ):
        value = cs.get(key, character.get(key, default))
        card[key] = copy.deepcopy(value)
    for key in ("rule_id", "rule_name", "rule_version", "mechanics", "language"):
        value = character.get(key, cs.get(key, ""))
        if value not in (None, ""):
            card[key] = str(value)
    # Professional cards retain canonical choices and version binding. Runtime
    # derived values are still discarded on reuse because create_player asks
    # the selected runtime to rebuild the complete submission from these choices.
    for key in ("rule_binding", "ruleset_character"):
        value = cs.get(key, character.get(key))
        if isinstance(value, dict):
            card[key] = copy.deepcopy(value)
    # 插件导入的卡带来源标记（source_plugin / plugin_content_id），保存时必须透传；
    # 否则卸载清理按 source_plugin 过滤会匹配不到，插件卡成了无法清理的残留。
    for key in ("source_plugin", "plugin_content_id", "raw_sillytavern"):
        value = character.get(key, cs.get(key))
        if value not in (None, ""):
            card[key] = copy.deepcopy(value)
    return card


def list_character_cards(
    dependencies: CharacterCardDependencies,
) -> dict[str, Any]:
    cards = _read_cards(dependencies)
    deduped = dedupe_cards(cards)
    if len(deduped) != len(cards):
        _write_cards(dependencies, deduped)
        cards = deduped
    visible_cards = []
    for card in cards:
        visible = copy.deepcopy(card)
        metadata = (
            dependencies.ruleset_card_metadata(card)
            if dependencies.ruleset_card_metadata is not None
            else None
        )
        if metadata is not None:
            visible["ruleset_runtime"] = metadata
        visible_cards.append(visible)
    return {"cards": visible_cards, "total": len(visible_cards)}


def save_character_card(
    dependencies: CharacterCardDependencies,
    character: dict,
) -> dict[str, Any]:
    source = str(character.get("source") or "角色卡库")
    # Game creation passes a player wrapper with mechanics nested under
    # ``character_sheet``. Convert that wrapper to the reusable card shape
    # before professional validation so the canonical blueprint is not missed.
    candidate = _to_character_card(character, source=source)
    try:
        if dependencies.normalize_ruleset_card is not None:
            candidate = dependencies.normalize_ruleset_card(candidate)
    except ValueError as exc:
        return {
            "ok": False,
            "error_code": str(getattr(exc, "code", "INVALID_RULESET_CHARACTER")),
            "error": str(exc),
        }
    card = _to_character_card(candidate, source=source)
    cards = _read_cards(dependencies)
    sig = card_signature(card)
    for existing in cards:
        if existing.get("id") == card["id"] or card_signature(existing) == sig:
            card["id"] = existing.get("id") or card["id"]
            break
    cards = [
        c for c in cards
        if c.get("id") != card["id"] and card_signature(c) != sig
    ]
    cards.append(card)
    cards = dedupe_cards(cards)
    _write_cards(dependencies, cards)
    return {"ok": True, "card": card}


def update_character_card(
    dependencies: CharacterCardDependencies,
    card_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    cards = dedupe_cards(_read_cards(dependencies))
    for idx, old in enumerate(cards):
        if old.get("id") != card_id:
            continue
        if (
            dependencies.is_ruleset_card is not None
            and dependencies.is_ruleset_card(old)
        ):
            return {
                "ok": False,
                "error_code": "RULESET_CHARACTER_OPERATION_REQUIRED",
                "error": "专业规则角色卡不能使用旧版通用编辑接口",
            }
        updated = {**old}
        for key in (
            "character_name", "race", "class", "background", "gold", "source",
            "rule_id", "rule_name", "rule_version", "mechanics", "language",
        ):
            if key in patch:
                updated[key] = patch[key]
        for key in (
            "identity", "attributes", "skills", "equipment", "inventory", "key_items",
            "currency", "rule_binding", "ruleset_character",
        ):
            if key in patch and isinstance(patch[key], (dict, list)):
                updated[key] = patch[key]
        if "portrait" in patch:
            if patch["portrait"] is None:
                updated.pop("portrait", None)
            elif isinstance(patch["portrait"], dict):
                updated["portrait"] = patch["portrait"]
        updated["schema_version"] = 2
        updated["id"] = card_id
        cards[idx] = updated
        _write_cards(dependencies, cards)
        return {"ok": True, "card": updated}
    return {"ok": False, "error": f"角色卡不存在: {card_id}"}


def delete_character_card(
    dependencies: CharacterCardDependencies,
    card_id: str,
) -> dict[str, Any]:
    cards = dedupe_cards(_read_cards(dependencies))
    kept = [c for c in cards if c.get("id") != card_id]
    if len(kept) == len(cards):
        return {"ok": False, "error": f"角色卡不存在: {card_id}"}
    _write_cards(dependencies, kept)
    return {"ok": True, "card_id": card_id}


def _tavern_to_character_card(tavern: dict, file_name: str = "") -> dict[str, Any]:
    background_parts = []
    for label, key in (("描述", "description"), ("性格", "personality"),
                       ("场景", "scenario"), ("初次发言", "first_mes")):
        value = (tavern.get(key) or "").strip()
        if value:
            background_parts.append(f"{label}: {value}")
    # 酒馆卡的扮演指令：system_prompt / post_history_instructions 一并带进 background，
    # AI 扮演该角色时能读到行为约束（game_lifecycle 会把 background 送进 prompt）。
    for label, key in (("扮演指令", "system_prompt"), ("后续指令", "post_history_instructions")):
        value = (tavern.get(key) or "").strip()
        if value:
            background_parts.append(f"{label}: {value}")
    source = f"SillyTavern: {file_name}" if file_name else "SillyTavern"
    if tavern.get("character_book"):
        source += f"（含 {len(tavern['character_book'])} 条角色世界书）"
    return {
        "id": _new_card_id("st"),
        "schema_version": 2,
        "character_name": tavern.get("name") or "未命名",
        "race": "人类",
        "class": "冒险者",
        "attributes": {},
        "skills": [],
        "background": "\n".join(background_parts),
        "equipment": [],
        "gold": 30,
        "source": source,
        "rule_id": "",
        "raw_sillytavern": tavern,
    }


_NSFW_MARKERS = ("nsfw", "18+", "成人", "explicit", "lewd", "porn", "erotic", "submissive", "dominant", "bdsm", "sensual", "intimate")


def _tavern_has_nsfw(tavern: dict) -> bool:
    """检测酒馆卡是否带成人内容标记（NSFW/18+）。返回布尔，文案由前端 i18n 按语言显示。"""
    haystack_parts: list[str] = []
    tags = tavern.get("tags")
    if isinstance(tags, list):
        haystack_parts.extend(str(t).lower() for t in tags if str(t).strip())
    for key in ("system_prompt", "post_history_instructions", "description"):
        value = str(tavern.get(key) or "").strip()
        if value:
            haystack_parts.append(value.lower())
    haystack = " ".join(haystack_parts)
    return any(marker in haystack for marker in _NSFW_MARKERS)


#: Embedded character lore is labelled so the product flow can present it as
#: "Imported character lore" instead of an anonymous Book.
CHARACTER_LORE_ROLE = "character_card"
CHARACTER_LORE_LABEL = "Imported character lore"


def commit_character_book(
    lorebook: Any,
    *,
    name: str,
    book: dict[str, Any] | None,
    book_id: str,
    entry_id_prefix: str,
    world_id: str = "",
    character_uid: str = "",
) -> dict[str, Any]:
    """Canonicalize an embedded ``character_book`` through the lorebook_v3 path.

    Single authority for every Character Card / Tavern entry point: importing the
    card body stays separate, and the embedded book always goes

        lorebook_v3 adapter → canonical commit → binding

    Binding precedence follows the work order: a canonical character uid wins,
    otherwise the current world, otherwise the book is left unbound. The keys of
    the book are never renamed, so settings and unknown extensions survive.
    """

    payload = {"spec": "lorebook_v3", "data": {"lorebook": dict(book or {})}}
    draft = draft_lorebook_import(payload)
    if not str(draft.name or "").strip() or draft.name == "Lorebook v3":
        draft.name = f"{name} lore" if str(name or "").strip() else CHARACTER_LORE_LABEL
    draft.source["entry_id_mode"] = "external"
    # Preserve a real source entry id so provenance keeps the external identity;
    # only entries that carry no id get a deterministic synthesized one. The
    # canonical commit still guards against an external id colliding with an
    # entry that already belongs to another book.
    for index, entry in enumerate(draft.entries):
        if not str(entry.external_id or "").strip():
            entry.external_id = f"{entry_id_prefix}_{index}"

    binding: dict[str, Any] | None = None
    # A bogus world must not create a dangling binding: the card import itself
    # already succeeded, so fall back to unbound rather than binding nowhere.
    if str(world_id or "").strip() and hasattr(lorebook, "get_world") and not lorebook.get_world(str(world_id)):
        world_id = ""
    if str(character_uid or "").strip():
        binding = {
            "id": f"binding:{book_id}:character", "scope_kind": "character",
            "scope_id": str(character_uid), "role": CHARACTER_LORE_ROLE,
        }
    elif str(world_id or "").strip():
        binding = {
            "id": f"binding:{book_id}:world", "scope_kind": "world",
            "scope_id": str(world_id), "role": CHARACTER_LORE_ROLE,
        }
    commit_lorebook_import(lorebook, draft, binding, book_id=book_id)
    return {
        "book_id": book_id,
        "name": draft.name,
        "entries": len(draft.entries),
        "role": CHARACTER_LORE_ROLE,
        "label": CHARACTER_LORE_LABEL,
        "binding": binding,
    }


def _import_tavern_as_npc(
    dependencies: CharacterCardDependencies,
    tavern: dict,
    world_id: str,
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把酒馆卡导入为指定世界的 NPC 世界书条目，并拆入内嵌角色世界书。

    酒馆卡本质是「AI 扮演的角色」，落成 NPC 比塞进 TRPG 角色卡（强填 race/class/
    属性）更自然：description/personality/scenario/first_mes 拼成条目 content，
    名字+tags 做 keywords 触发出场；内嵌 character_book 拆成同世界 other 条目。
    """
    if not world_id:
        return {"ok": False, "error": "导入为 NPC 需要选择目标世界"}
    lorebook = dependencies.lorebook
    if not lorebook:
        return {"ok": False, "error": "世界书库未启用"}
    if not lorebook.get_world(world_id):
        return {"ok": False, "error": "目标世界不存在"}
    name = str(tavern.get("name") or "未命名").strip()
    safe_name = name.replace(" ", "_") or "npc"
    entry_id = f"{world_id}_tavern_{safe_name}"
    keywords = [name] + [str(t).strip() for t in (tavern.get("tags") or []) if str(t).strip()]
    content_parts: list[str] = []
    for label, key in (("描述", "description"), ("性格", "personality"),
                       ("背景", "scenario"), ("初次见面", "first_mes")):
        value = str(tavern.get(key) or "").strip()
        if value:
            content_parts.append(f"{label}: {value}")
    # 酒馆卡的扮演指令：system_prompt / post_history_instructions 一并进 content，
    # AI 扮演该 NPC 时能读到行为约束（世界书条目 content 会进 lorebook_matches）。
    for label, key in (("扮演指令", "system_prompt"), ("后续指令", "post_history_instructions")):
        value = str(tavern.get(key) or "").strip()
        if value:
            content_parts.append(f"{label}: {value}")
    npc_entry = {
        "id": entry_id,
        "world_id": world_id,
        "name": name,
        "type": "npc",
        "keywords": keywords[:12],
        "content": "\n".join(content_parts),
        "tier": "core",
    }
    if lorebook.get_entry(entry_id):
        lorebook.update_entry(entry_id, npc_entry)
    else:
        lorebook.add_entry(npc_entry)
    # Embedded character_book is a separate canonical book.  It must retain
    # settings/entry controls through the same adapter/preview/commit path.
    raw_data = (document or {}).get("data") if isinstance(document, dict) else None
    book = raw_data.get("character_book") if isinstance(raw_data, dict) else None
    if not isinstance(book, dict):
        entries = tavern.get("character_book") or []
        book = {"name": f"{name} Lorebook", "entries": entries}
    embedded_book_id = f"character_card:{world_id}:{safe_name}"
    lore = commit_character_book(
        lorebook, name=name, book=book, book_id=embedded_book_id,
        entry_id_prefix=f"{world_id}_tavern_{safe_name}_book", world_id=world_id,
    )
    book_imported = int(lore["entries"])
    if dependencies.rebuild_lorebook_index is not None:
        dependencies.rebuild_lorebook_index(world_id)
    logger.info("酒馆卡已导入为 NPC: %s -> world=%s（含 %d 条世界书）", name, world_id, book_imported)
    result: dict[str, Any] = {
        "ok": True, "imported_as": "npc", "npc_name": name,
        "world_id": world_id, "lorebook_entries": book_imported,
        "content_draft": character_content_draft(
            npc_entry, source_id=f"{world_id}-{safe_name}", kind="npc"
        ).to_portable_dict(),
    }
    if _tavern_has_nsfw(tavern):
        result["nsfw_warning"] = True
    result["lorebook_book_id"] = embedded_book_id
    result["lorebook"] = lore
    return result


def _is_diceframe_card(data: dict) -> bool:
    """判断 JSON 是否为 DiceFrame 自家角色卡格式（vs 酒馆 chara_card 格式）。

    DiceFrame 卡特征：顶层有 schema_version + 至少一个 TRPG 特有字段
    （attributes / skills / rule_id / mechanics）。酒馆卡是 {data: {...}} 包裹
    或含 description/personality 的纯酒馆结构，不带这些字段。
    """
    if not isinstance(data, dict):
        return False
    if "data" in data and isinstance(data["data"], dict):
        return False  # chara_card_v2 用 data 包裹，是酒馆格式
    if int(data.get("schema_version") or 0) != 2:
        return False
    return any(key in data for key in ("attributes", "skills", "rule_id", "mechanics", "equipment", "inventory"))


async def import_character_card(
    dependencies: CharacterCardDependencies,
    file_data: str = "",
    file_name: str = "card.json",
    target: str = "character_card",
    world_id: str = "",
    *,
    include_character_book: bool = True,
    character_uid: str = "",
) -> dict[str, Any]:
    if not file_data:
        return {"ok": False, "error": "未提供文件数据"}
    raw_bytes = base64.b64decode(file_data)
    safe_name = Path(file_name).name or "card.json"

    # DiceFrame 自家卡格式：原样存入卡库（无损），不转酒馆字段
    try:
        as_json = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        as_json = None
    if as_json is not None and _is_diceframe_card(as_json):
        if target == "npc":
            return {"ok": False, "error": "DiceFrame 角色卡不支持导入为 NPC，请选择「导入为角色卡」"}
        card = dict(as_json)
        card.setdefault("character_name", card.get("character_name") or card.get("name") or "未命名")
        saved = save_character_card(dependencies, card)
        if not saved.get("ok"):
            return saved
        return {
            "ok": True,
            "card": saved["card"],
            "imported_as": "character_card",
            "format": "diceframe",
            "content_draft": character_content_draft(saved["card"], source_id=safe_name).to_portable_dict(),
        }

    tmp_path = Path(tempfile.gettempdir()) / f"trpg_card_import_{int(time.time_ns())}_{safe_name}"
    tmp_path.write_bytes(raw_bytes)
    document: dict[str, Any] | None = None
    try:
        document = parse_character_card_document(tmp_path)
        tavern = parse_tavern_card(str(tmp_path))
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    if "error" in tavern:
        return {"ok": False, "error": tavern["error"]}
    if target == "npc":
        return _import_tavern_as_npc(dependencies, tavern, world_id, document=document)
    card = _tavern_to_character_card(tavern, safe_name)
    cards = _read_cards(dependencies)
    cards.append(card)
    _write_cards(dependencies, cards)
    result: dict[str, Any] = {"ok": True, "card": card, "imported_as": "character_card", "format": "tavern",
                              "content_draft": character_content_draft(card, source_id=safe_name).to_portable_dict()}
    # A Character Card's embedded character_book is real lore, not a footnote:
    # when the user keeps it checked it must reach the canonical store through
    # the same adapter/preview/commit path as every other import. Unchecked means
    # card only — a genuine choice, not a disabled button.
    if include_character_book:
        embedded = _embedded_character_book(document, tavern)
        if embedded is not None:
            lore = commit_character_book(
                dependencies.lorebook, name=str(card.get("character_name") or ""),
                book=embedded, book_id=f"character_card:{card['id']}",
                entry_id_prefix=f"{card['id']}_book", world_id=world_id,
                character_uid=character_uid,
            )
            result["lorebook"] = lore
            result["lorebook_book_id"] = lore["book_id"]
            result["lorebook_entries"] = lore["entries"]
            if dependencies.rebuild_lorebook_index is not None and world_id:
                dependencies.rebuild_lorebook_index(world_id)
    if _tavern_has_nsfw(tavern):
        result["nsfw_warning"] = True
    return result


def _embedded_character_book(
    document: dict[str, Any] | None, tavern: dict,
) -> dict[str, Any] | None:
    """The card's embedded ``character_book`` in its rawest available form."""

    raw_data = (document or {}).get("data") if isinstance(document, dict) else None
    book = raw_data.get("character_book") if isinstance(raw_data, dict) else None
    if isinstance(book, dict):
        return book
    entries = tavern.get("character_book") or []
    if not entries:
        return None
    return {"name": f"{str(tavern.get('name') or '').strip()} Lorebook", "entries": entries}


def export_character_cards(
    dependencies: CharacterCardDependencies,
    card_ids: list[str],
) -> dict[str, Any]:
    """批量导出 DiceFrame 角色卡：单张返回 JSON 文本，多张打包 zip。

    导出的是 DiceFrame 自家格式（含 attributes/skills/rule_id 等），
    与原样导入接口无损往返；不转酒馆格式。
    """
    card_ids = [str(c).strip() for c in card_ids if str(c).strip()] if isinstance(card_ids, list) else []
    if not card_ids:
        return {"ok": False, "error": "请选择要导出的角色卡"}
    cards = _read_cards(dependencies)
    selected = [c for c in cards if str(c.get("id") or "") in set(card_ids)]
    if not selected:
        return {"ok": False, "error": "未找到所选角色卡"}

    # 仅去掉运行期插件来源标记；source（人类可读来源）和 raw_sillytavern（酒馆原始数据）
    # 是业务字段，保留以保证导出→导入无损往返（否则酒馆卡丢 raw_sillytavern 后无法还原）。
    skip = {
        "source_plugin", "plugin_content_id",
        "ruleset_revision", "ruleset_operation_log",
    }
    payloads: list[tuple[str, str]] = []
    used_names: dict[str, int] = {}
    for card in selected:
        clean = {k: v for k, v in card.items() if k not in skip}
        name = str(clean.get("character_name") or clean.get("name") or "角色卡")
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_ ").strip() or "character"
        # 批量导出时同名卡用 card_id 短缀区分，避免 zip 内同名条目互相覆盖
        cid = str(card.get("id") or "")
        if cid:
            safe = f"{safe}_{cid[:8]}"
        filename = f"{safe}.json"
        if filename in used_names:
            used_names[filename] += 1
            filename = f"{safe}_{used_names[filename]}.json"
        else:
            used_names[filename] = 0
        payloads.append((filename, json.dumps(clean, ensure_ascii=False, indent=2)))

    if len(payloads) == 1:
        filename, content = payloads[0]
        return {"ok": True, "filename": filename, "content_type": "application/json", "payload": content.encode("utf-8")}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in payloads:
            zf.writestr(name, content)
    return {"ok": True, "filename": "characters_export.zip", "content_type": "application/zip", "payload": buffer.getvalue()}

