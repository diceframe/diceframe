"""角色卡库服务：列表 / 保存 / 更新 / 删除 / SillyTavern 卡导入。"""

from __future__ import annotations

import base64
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.engine.character_utils import parse_tavern_card

if TYPE_CHECKING:
    from src.webui.api import WebAPI

logger = logging.getLogger("trpg")


def _read_cards(api: "WebAPI") -> list[dict[str, Any]]:
    path = api._character_cards_path
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        logger.exception("读取角色卡库失败: %s", path)
        return []


def _write_cards(api: "WebAPI", cards: list[dict[str, Any]]) -> None:
    path = api._character_cards_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _card_signature(card: dict[str, Any]) -> tuple[str, str, str, str]:
    """同一张仓库卡的稳定指纹，用于避免 AI 生成后微调产生重复卡。"""
    return (
        str(card.get("character_name") or "").strip().lower(),
        str(card.get("race") or "").strip().lower(),
        str(card.get("class") or "").strip().lower(),
        str(card.get("background") or "").strip().lower(),
    )


def _dedupe_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        sig = _card_signature(card)
        if not sig[0]:
            sig = (str(card.get("id") or f"anon_{len(order)}"), "", "", "")
        if sig not in seen:
            order.append(sig)
        seen[sig] = card
    return [seen[sig] for sig in order]


def _to_character_card(character: dict, source: str = "") -> dict[str, Any]:
    cs = character.get("character_sheet", {}) if isinstance(character.get("character_sheet"), dict) else character
    name = character.get("character_name") or cs.get("character_name") or "冒险者"
    attrs = cs.get("attributes", {}) if isinstance(cs.get("attributes", {}), dict) else {}
    return {
        "id": character.get("card_id") or character.get("id") or cs.get("card_id") or cs.get("id") or f"card_{int(time.time_ns())}",
        "character_name": name,
        "race": cs.get("race", character.get("race", "人类")),
        "class": cs.get("class", character.get("class", "冒险者")),
        "attributes": attrs,
        "skills": cs.get("skills", character.get("skills", [])),
        "background": cs.get("background", character.get("background", "")),
        "equipment": cs.get("equipment", character.get("equipment", [])),
        "gold": cs.get("gold", character.get("gold", 30)),
        "source": source,
    }


def list_character_cards(api: "WebAPI") -> dict[str, Any]:
    cards = _read_cards(api)
    deduped = _dedupe_cards(cards)
    if len(deduped) != len(cards):
        _write_cards(api, deduped)
        cards = deduped
    return {"cards": cards, "total": len(cards)}


def save_character_card(api: "WebAPI", character: dict) -> dict[str, Any]:
    card = _to_character_card(character, source="角色卡库")
    cards = _read_cards(api)
    sig = _card_signature(card)
    for existing in cards:
        if existing.get("id") == card["id"] or _card_signature(existing) == sig:
            card["id"] = existing.get("id") or card["id"]
            break
    cards = [
        c for c in cards
        if c.get("id") != card["id"] and _card_signature(c) != sig
    ]
    cards.append(card)
    cards = _dedupe_cards(cards)
    _write_cards(api, cards)
    return {"ok": True, "card": card}


def update_character_card(api: "WebAPI", card_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    cards = _dedupe_cards(_read_cards(api))
    for idx, old in enumerate(cards):
        if old.get("id") != card_id:
            continue
        updated = {**old}
        for key in ("character_name", "race", "class", "background", "gold", "source"):
            if key in patch:
                updated[key] = patch[key]
        for key in ("attributes", "skills", "equipment", "inventory", "key_items"):
            if key in patch and isinstance(patch[key], (dict, list)):
                updated[key] = patch[key]
        updated["id"] = card_id
        cards[idx] = updated
        _write_cards(api, cards)
        return {"ok": True, "card": updated}
    return {"ok": False, "error": f"角色卡不存在: {card_id}"}


def delete_character_card(api: "WebAPI", card_id: str) -> dict[str, Any]:
    cards = _dedupe_cards(_read_cards(api))
    kept = [c for c in cards if c.get("id") != card_id]
    if len(kept) == len(cards):
        return {"ok": False, "error": f"角色卡不存在: {card_id}"}
    _write_cards(api, kept)
    return {"ok": True, "card_id": card_id}


def _tavern_to_character_card(tavern: dict, file_name: str = "") -> dict[str, Any]:
    background_parts = []
    for label, key in (("描述", "description"), ("性格", "personality"),
                       ("场景", "scenario"), ("初次发言", "first_mes")):
        value = (tavern.get(key) or "").strip()
        if value:
            background_parts.append(f"{label}: {value}")
    source = f"SillyTavern: {file_name}" if file_name else "SillyTavern"
    if tavern.get("character_book"):
        source += f"（含 {len(tavern['character_book'])} 条角色世界书）"
    return {
        "id": f"st_{int(time.time_ns())}",
        "character_name": tavern.get("name") or "未命名",
        "race": "人类",
        "class": "冒险者",
        "attributes": {},
        "skills": [],
        "background": "\n".join(background_parts),
        "equipment": [],
        "gold": 30,
        "source": source,
        "raw_sillytavern": tavern,
    }


async def import_character_card(api: "WebAPI", file_data: str = "", file_name: str = "card.json") -> dict[str, Any]:
    if not file_data:
        return {"ok": False, "error": "未提供文件数据"}
    raw_bytes = base64.b64decode(file_data)
    safe_name = Path(file_name).name or "card.json"
    tmp_path = Path(tempfile.gettempdir()) / f"trpg_card_import_{int(time.time_ns())}_{safe_name}"
    tmp_path.write_bytes(raw_bytes)
    try:
        tavern = parse_tavern_card(str(tmp_path))
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    if "error" in tavern:
        return {"ok": False, "error": tavern["error"]}
    card = _tavern_to_character_card(tavern, safe_name)
    cards = _read_cards(api)
    cards.append(card)
    _write_cards(api, cards)
    return {"ok": True, "card": card}
