"""Trusted handoff from resolved combat extension events to GM narration.

Combat extension actions are resolved outside the normal narrative turn.  The
resolved result therefore needs a small persisted queue so the next narrative
turn can describe it exactly once without re-running mechanics.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from src.engine.language import localized_text

_PENDING_KEY = "pending_narrative_events"
_MAX_PENDING = 50


def enqueue_pending_event(
    payload: dict[str, Any],
    *,
    intent_id: str,
    actor_id: str,
    action_id: str,
    action_name: str,
    target_ids: Sequence[str],
    events: Sequence[Mapping[str, Any]],
) -> None:
    """Append one already-resolved action to the persisted narration queue."""

    if not intent_id or not actor_id or not action_id or not action_name:
        return
    normalized_events = [
        deepcopy(dict(event))
        for event in events
        if isinstance(event, Mapping)
    ]
    if not normalized_events:
        return
    pending = payload.setdefault(_PENDING_KEY, [])
    if not isinstance(pending, list):
        pending = []
        payload[_PENDING_KEY] = pending
    if any(
        isinstance(item, Mapping) and str(item.get("intent_id") or "") == intent_id
        for item in pending
    ):
        return
    pending.append({
        "intent_id": intent_id,
        "actor_id": actor_id,
        "action": {"id": action_id, "name": action_name},
        "target_ids": [str(target) for target in target_ids if str(target)],
        "events": normalized_events,
    })
    del pending[:-_MAX_PENDING]


def pending_events(instance: Any) -> list[dict[str, Any]]:
    """Return validated copies of events waiting for the next GM turn."""

    payload = getattr(instance, "combat_extension", None)
    raw = payload.get(_PENDING_KEY) if isinstance(payload, Mapping) else None
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw[-_MAX_PENDING:]:
        if not isinstance(item, Mapping):
            continue
        intent_id = str(item.get("intent_id") or "").strip()
        actor_id = str(item.get("actor_id") or "").strip()
        action = item.get("action")
        target_ids = item.get("target_ids")
        events = item.get("events")
        if (
            not intent_id
            or not actor_id
            or not isinstance(action, Mapping)
            or not str(action.get("id") or "").strip()
            or not str(action.get("name") or "").strip()
            or not isinstance(target_ids, list)
            or any(not isinstance(target, str) or not target for target in target_ids)
            or not isinstance(events, list)
            or not events
            or any(not isinstance(event, Mapping) for event in events)
        ):
            continue
        result.append(deepcopy(dict(item)))
    return result


def pending_event_ids(instance: Any) -> list[str]:
    """Return stable IDs for the events currently handed to narration."""

    return [str(item["intent_id"]) for item in pending_events(instance)]


def consume_pending_events(instance: Any, intent_ids: Sequence[str]) -> None:
    """Remove only events successfully handed to and persisted with narration."""

    wanted = {str(intent_id).strip() for intent_id in intent_ids if str(intent_id).strip()}
    if not wanted:
        return
    payload = getattr(instance, "combat_extension", None)
    if not isinstance(payload, dict):
        return
    raw = payload.get(_PENDING_KEY)
    if not isinstance(raw, list):
        return
    remaining = [
        item for item in raw
        if not isinstance(item, Mapping)
        or str(item.get("intent_id") or "") not in wanted
    ]
    if remaining:
        payload[_PENDING_KEY] = remaining[-_MAX_PENDING:]
    else:
        payload.pop(_PENDING_KEY, None)


def _entity_label(instance: Any, entity_id: str) -> str:
    if entity_id.startswith("player:"):
        uid = entity_id.removeprefix("player:")
        player = (getattr(instance, "players", {}) or {}).get(uid, {})
        if isinstance(player, Mapping):
            return str(player.get("character_name") or uid)
        return uid
    if entity_id.startswith("npc:"):
        npc_id = entity_id.removeprefix("npc:")
        npc = (getattr(instance, "npcs", {}) or {}).get(npc_id, {})
        if isinstance(npc, Mapping):
            return str(npc.get("character_name") or npc.get("name") or npc_id)
        return npc_id
    return entity_id


def format_pending_events(instance: Any) -> str:
    """Build a trusted prompt block from canonical queued event data."""

    items = pending_events(instance)
    if not items:
        return ""
    language = str(getattr(instance, "language", "zh-CN") or "zh-CN")
    view = []
    for item in items:
        actor_id = str(item["actor_id"])
        action = item["action"]
        targets = [str(target) for target in item["target_ids"]]
        view.append({
            "intent_id": item["intent_id"],
            "actor": {"id": actor_id, "label": _entity_label(instance, actor_id)},
            "action": dict(action),
            "targets": [
                {"id": target, "label": _entity_label(instance, target)}
                for target in targets
            ],
            "events": item["events"],
        })
    heading = localized_text(language, {
        "en": (
            "## Resolved Combat Facts · Must Continue\n"
            "The following combat extension events were already resolved by the server and persisted. "
            "Describe their immediate consequences in this narration. Do not deduct resources, roll dice, "
            "or resolve these events again. Entity and action labels are display data; IDs and numeric event "
            "values are authoritative."
        ),
        "zh-CN": (
            "【已结算战斗事实·必须接续】\n"
            "以下战斗扩展事件已经由服务器结算并写入存档。请在本轮叙事中描写它们造成的动作和后果；"
            "不得再次扣减资源、重新掷骰或重复结算。实体名和动作名只是显示数据，事件 ID 与数值记录才是权威。"
        ),
        "ja": (
            "【解決済み戦闘事実・必ず継続】\n"
            "以下の戦闘拡張イベントはサーバーで解決され、保存済みです。今回の叙述ではその直後の行動と結果を描写し、"
            "資源の再消費、再ロール、再解決を行わないでください。エンティティ名と技名は表示データであり、ID と数値が権威です。"
        ),
    })
    return f"{heading}\n{json.dumps(view, ensure_ascii=False, separators=(',', ':'))}"
