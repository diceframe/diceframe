"""Localized prompt blocks for authoritative world truth (#284).

World truth and its legality verdicts are server-assembled trusted context
blocks: players cannot inject them, and the GM must follow them.  They live in
one module so the GM context and the player-safe context render the same facts
from the same projection -- the difference is only the viewer, never a second
opinion about what is true.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from src.engine.language import localized_text
from src.engine.world.read import project_visible_state

_HEADING = {
    "en": (
        "## World Truth · Must Follow\n"
        "These are the authoritative world facts for this round. Never narrate anything that "
        "contradicts them, and never reveal facts marked GM only to players:"
    ),
    "zh-CN": (
        "【世界真相·必须遵循】\n"
        "以下是本轮的权威世界事实：叙事不得与之矛盾；标注为 GM 私有的事实不得透露给玩家："
    ),
    "ja": (
        "【世界の事実・必ず従うこと】\n"
        "以下はこのラウンドの権威ある世界事実である。矛盾する叙述をしてはならず、"
        "GM 専用と記された事実をプレイヤーに明かしてはならない："
    ),
    "de": (
        "## Welttatsachen · Muss befolgt werden\n"
        "Dies sind die verbindlichen Welttatsachen dieser Runde. Erzähle nichts, was ihnen "
        "widerspricht, und enthülle keine als „nur GM“ markierten Tatsachen:"
    ),
}
_GM_ONLY_SUFFIX = {
    "en": "  (GM only, players cannot see this)",
    "zh-CN": "（GM 私有，玩家不可见）",
    "ja": "（GM 専用・プレイヤーには見えない）",
    "de": "  (nur GM, für Spieler unsichtbar)",
}
_CLOCK = {
    "en": "World time: day {day}, minute {minute}",
    "zh-CN": "世界时间：第 {day} 天 {minute} 分",
    "ja": "世界時間：{day} 日目 {minute} 分",
    "de": "Weltzeit: Tag {day}, Minute {minute}",
}
_LEGALITY_HEADING = {
    "en": (
        "## Action Legality · Must Follow\n"
        "These declared actions contradict authoritative world truth. Narrate them as an attempt "
        "that requires moving first or cannot complete; never narrate them as already done, and "
        "never move a character implicitly:"
    ),
    "zh-CN": (
        "【行动合法性·必须遵循】\n"
        "以下行动与权威世界真相矛盾：必须叙述为「需要先移动」或「未能完成」，"
        "不得叙述为已经完成，也不得让角色隐式瞬移："
    ),
    "ja": (
        "【行動の適法性・必ず従うこと】\n"
        "以下の行動は権威ある世界事実と矛盾する。まず移動が必要、または完了できない試みとして"
        "叙述し、すでに完了したかのように叙述してはならず、暗黙の移動も認めない："
    ),
    "de": (
        "## Handlungslegitimität · Muss befolgt werden\n"
        "Diese erklärten Aktionen widersprechen der verbindlichen Welttatsache. Erzähle sie als "
        "Versuch, der zuerst Bewegung erfordert oder nicht abgeschlossen werden kann; niemals als "
        "bereits erledigt, und niemals als stillschweigende Bewegung:"
    ),
}
_LEGALITY_NOTE = {
    "ROUTE_IMPASSABLE": {
        "en": "{name} cannot pass {location}: the world marks it impassable",
        "zh-CN": "{name} 无法通过 {location}：世界事实标记其不可通行",
        "ja": "{name} は {location} を通過できない：世界事実が通行不可としている",
        "de": "{name} kann {location} nicht passieren: die Welt markiert es als unpassierbar",
    },
    "ACTION_LOCATION_MISMATCH": {
        "en": (
            "{name} is at {current}, but this action is declared at {location}: "
            "they must move first or the action cannot complete"
        ),
        "zh-CN": (
            "{name} 当前位于 {current}，但本行动声明发生在 {location}："
            "必须先移动，否则该行动无法完成"
        ),
        "ja": (
            "{name} は現在 {current} にいるが、この行動は {location} で宣言されている："
            "先に移動しなければ行動は完了できない"
        ),
        "de": (
            "{name} ist in {current}, aber diese Aktion wird in {location} erklärt: "
            "erst bewegen, sonst kann die Aktion nicht abgeschlossen werden"
        ),
    },
}


_EVENTS_HEADING = {    "en": (
        "## Settled World Events · Must Follow\n"
        "Logical world time advanced and these scheduled events took effect this round. Narrate "
        "their consequences; they are settled facts, not suggestions:"
    ),
    "zh-CN": (
        "【世界时间推进·已结算事件】\n"
        "世界逻辑时间已经推进，以下定时事件在本轮生效：请叙述它们的后果，"
        "它们是已结算事实，不是建议："
    ),
    "ja": (
        "【世界時間の進行・確定したイベント】\n"
        "世界の論理時間が進み、以下の予定イベントがこのラウンドで確定した。"
        "その結果を叙述すること。これは提案ではなく確定事実である："
    ),
    "de": (
        "## Abgeschlossene Weltereignisse · Muss befolgt werden\n"
        "Die logische Weltzeit ist vorangeschritten und diese geplanten Ereignisse sind in dieser "
        "Runde eingetreten. Erzähle ihre Folgen; sie sind feststehende Tatsachen, keine Vorschläge:"
    ),
}
_EVENT_LINE = {
    "en": "{label} ({event_id}) took effect at day {day}, minute {minute}",
    "zh-CN": "{label}（{event_id}）于第 {day} 天 {minute} 分生效",
    "ja": "{label}（{event_id}）が {day} 日目 {minute} 分に発動",
    "de": "{label} ({event_id}) ist an Tag {day}, Minute {minute} eingetreten",
}
_EVENT_FAILED_LINE = {
    "en": "{label} ({event_id}) could not be settled: {error}",
    "zh-CN": "{label}（{event_id}）未能结算：{error}",
    "ja": "{label}（{event_id}）は確定できなかった：{error}",
    "de": "{label} ({event_id}) konnte nicht abgeschlossen werden: {error}",
}


# FIX-05 §7.3：有界 viewer-safe 投影的其余三段（相关实体 / 关系 / 进程）。
_ENTITY_HEADING = {
    "en": "Relevant world entities (only what this viewer may see):",
    "zh-CN": "相关世界实体（仅当前视角可见）：",
    "ja": "関連する世界エンティティ（この視点で見えるもののみ）：",
    "de": "Relevante Weltentitäten (nur was diese Sicht sehen darf):",
}
_RELATION_HEADING = {
    "en": "Relevant world relations:",
    "zh-CN": "相关世界关系：",
    "ja": "関連する世界の関係：",
    "de": "Relevante Weltbeziehungen:",
}
_PROCESS_HEADING = {
    "en": "Relevant world processes:",
    "zh-CN": "相关世界进程：",
    "ja": "関連する世界プロセス：",
    "de": "Relevante Weltprozesse:",
}
_TRUNCATED_NOTE = {
    "en": "(more world records exist; only the most relevant are shown)",
    "zh-CN": "（还有更多世界记录，这里只列出最相关的部分）",
    "ja": "（他にも世界記録があるが、関連するもののみ表示）",
    "de": "(es existieren weitere Weltdatensätze; nur die relevantesten werden gezeigt)",
}


def _entity_line(entity_id: str, entity: dict[str, Any]) -> str:
    parts = [entity_id]
    for key in ("kind", "status"):
        value = str(entity.get(key) or "")
        if value:
            parts.append(value)
    location = str(entity.get("location") or "")
    if location:
        parts.append(f"@{location}")
    return "- " + " · ".join(parts)


def _relation_line(relation_id: str, relation: dict[str, Any]) -> str:
    parts = [relation_id]
    kind = str(relation.get("kind") or "")
    if kind:
        parts.append(kind)
    endpoints = f"{relation.get('from_ref') or ''}→{relation.get('to_ref') or ''}"
    parts.append(endpoints)
    status = str(relation.get("status") or "")
    if status:
        parts.append(status)
    return "- " + " · ".join(part for part in parts if part and part != "→")


def _process_line(process_id: str, process: dict[str, Any]) -> str:
    parts = [process_id]
    for key in ("kind", "status"):
        value = str(process.get(key) or "")
        if value:
            parts.append(value)
    due = process.get("due_at") if isinstance(process.get("due_at"), dict) else None
    if due:
        parts.append(f"due day {due.get('day', 1)} minute {due.get('minute', 0)}")
    return "- " + " · ".join(parts)


def format_world_state_block(
    instance: Any, *, viewer_is_gm: bool, viewer_uid: str = "",
    location: str = "", participants: Iterable[str] = (),
) -> str:
    """Render the bounded, viewer-safe world projection（FIX-05 §7.3）。

    内容 = 权威事实 + 相关实体 / 关系 / 进程 + 逻辑时钟；玩家视角不包含
    ``gm`` 可见性记录，且投影本身有上界（不会 dump 全世界）。世界为空时返回空串。
    """

    projection = project_visible_state(
        instance, viewer_uid=viewer_uid, viewer_is_gm=bool(viewer_is_gm),
        location=location, participants=participants,
    )
    facts = projection.get("facts") or {}
    entities = projection.get("entities") or {}
    relations = projection.get("relations") or {}
    processes = projection.get("processes") or {}
    if not (facts or entities or relations or processes):
        return ""
    language = getattr(instance, "language", "zh-CN")
    lines = []
    for key, fact in facts.items():
        value = json.dumps(fact.get("value"), ensure_ascii=False)
        suffix = ""
        if str(fact.get("visibility") or "") == "gm":
            suffix = localized_text(language, _GM_ONLY_SUFFIX)
        lines.append(f"- {key} = {value}{suffix}")
    if entities:
        lines.append(localized_text(language, _ENTITY_HEADING))
        lines.extend(_entity_line(key, item) for key, item in entities.items())
    if relations:
        lines.append(localized_text(language, _RELATION_HEADING))
        lines.extend(_relation_line(key, item) for key, item in relations.items())
    if processes:
        lines.append(localized_text(language, _PROCESS_HEADING))
        lines.extend(_process_line(key, item) for key, item in processes.items())
    if projection.get("truncated"):
        lines.append(localized_text(language, _TRUNCATED_NOTE))
    clock = projection.get("clock") or {}
    clock_line = localized_text(language, _CLOCK).format(
        day=clock.get("day", 1), minute=clock.get("minute", 0),
    )
    heading = localized_text(language, _HEADING)
    return f"{heading}\n{clock_line}\n" + "\n".join(lines)


def format_world_legality_block(instance: Any) -> str:
    """Render this round's proven world contradictions (empty when none)."""

    notes = list(getattr(instance, "last_world_legality", []) or [])
    if not notes:
        return ""
    language = getattr(instance, "language", "zh-CN")
    players = getattr(instance, "players", None) or {}
    lines = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        template = _LEGALITY_NOTE.get(str(note.get("code") or ""))
        if template is None:
            continue
        uid = str(note.get("player") or "")
        name = (players.get(uid) or {}).get("character_name", uid) if uid else ""
        lines.append("- " + localized_text(language, template).format(
            name=str(name or uid),
            location=str(note.get("location") or ""),
            current=str(note.get("current") or ""),
        ))
    if not lines:
        return ""
    return f"{localized_text(language, _LEGALITY_HEADING)}\n" + "\n".join(lines)


def format_world_events_block(instance: Any) -> str:
    """Render the scheduled events settled by this round's time advance."""

    events = list(getattr(instance, "last_world_events", []) or [])
    if not events:
        return ""
    language = getattr(instance, "language", "zh-CN")
    lines = []
    for event in events:
        if not isinstance(event, dict):
            continue
        due = event.get("due_at") if isinstance(event.get("due_at"), dict) else {}
        values = {
            "label": str(event.get("label") or event.get("event_id") or ""),
            "event_id": str(event.get("event_id") or ""),
            "day": due.get("day", 1),
            "minute": due.get("minute", 0),
            "error": str(event.get("error") or ""),
        }
        template = (
            _EVENT_FAILED_LINE if event.get("status") == "failed" else _EVENT_LINE
        )
        lines.append("- " + localized_text(language, template).format(**values))
    if not lines:
        return ""
    return f"{localized_text(language, _EVENTS_HEADING)}\n" + "\n".join(lines)


__all__ = [
    "format_world_events_block",
    "format_world_legality_block",
    "format_world_state_block",
]
