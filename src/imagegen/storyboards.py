"""Shared multi-scene storyboard normalization and image composition."""

from __future__ import annotations

import json
import hashlib
import re
from typing import Any


MAX_STORYBOARD_PANELS = 6


def storyboard_source_revision(entry: Any) -> str:
    """Stable public-story revision used to reject stale async image jobs."""
    if not isinstance(entry, dict):
        entry = {}
    payload = {
        "gm_response": str(entry.get("gm_response") or ""),
        "actions": entry.get("actions") or [],
        "scene_panels": entry.get("scene_panels") or [],
        "current_swipe": entry.get("current_swipe") or 0,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:16]


def storyboard_panel_metadata(
    panels: list[dict[str, Any]], *, narration: str, actions: Any, current_scene: str,
    source_revision: str,
) -> list[dict[str, Any]]:
    """Build internal validation metadata without changing the public panel shape."""
    evidence = _public_evidence_segments(
        narration=narration, actions=actions, current_scene=current_scene, global_prompt="",
    )
    result: list[dict[str, Any]] = []
    for index, panel in enumerate(panels, 1):
        location = str(panel.get("location") or "")
        ids = [item["id"] for item in evidence if _location_has_evidence(location, [item])]
        result.append({
            "panel_index": index,
            "source_revision": source_revision,
            "validation": "accepted" if ids else "unverified",
            "evidence_ids": ids[:8],
        })
    return result


class StoryboardInferenceError(RuntimeError):
    """Raised when strict automatic storyboard analysis cannot produce valid panels."""

# Character cards are user-authored public data, so keep the image prompt
# projection deliberately small and only select fields that can describe a
# visible person.  In particular, this helper never receives a game log.
_APPEARANCE_KEYS = (
    "appearance", "physical_description", "visual_description", "appearance_description",
    "looks", "外貌", "外貌特征", "外观", "人物外貌",
)
_VISUAL_MARKERS = re.compile(
    r"(?:外貌|外观|外形|头发|发色|眼睛|瞳|身高|体型|肤色|脸|面容|穿着|服装|衣着|裙|斗篷|铠|甲|盔|眼镜|义体|"
    r"appearance|look(?:s)?|hair|eyes?|height|build|skin|face|wear(?:s|ing)?|clothing|outfit|glasses|prosthetic)",
    re.IGNORECASE,
)
_EQUIPMENT_MARKERS = re.compile(r"(?:衣|裙|袍|斗篷|铠|甲|盔|帽|眼镜|服|coat|cloak|armor|armour|dress|uniform|hat|helmet|glasses)", re.IGNORECASE)
_PARTY_MARKERS = re.compile(
    r"(?:队伍|全队|众人|所有人|一行人|the party|the whole party|everyone|all players)",
    re.IGNORECASE,
)


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _participants(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.replace("，", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    return list(dict.fromkeys(_text(item, 80) for item in values if _text(item, 80)))[:8]


def _first_text(mapping: Any, keys: tuple[str, ...], limit: int) -> str:
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return _text(value, limit)
    return ""


def _background_visual_excerpt(value: Any, limit: int = 360) -> str:
    """Extract only visually descriptive lines from a legacy freeform bio."""
    if not isinstance(value, str):
        return ""
    lines = [" ".join(line.split()) for line in value.splitlines() if line.strip()]
    selected: list[str] = []
    for line in lines:
        if _VISUAL_MARKERS.search(line):
            selected.append(line)
        if len(selected) >= 3:
            break
    return _text("；".join(selected), limit)


def _equipment_visual_excerpt(value: Any, limit: int = 240) -> str:
    if not isinstance(value, list):
        return ""
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            name = _text(item, 100)
        elif isinstance(item, dict):
            name = _text(item.get("name"), 100)
            slot = _text(item.get("slot"), 40)
            if name and not (_EQUIPMENT_MARKERS.search(name) or slot in {"armor", "body", "accessory"}):
                continue
        else:
            continue
        if name and name not in names:
            names.append(name)
        if len(names) >= 5:
            break
    return _text("、".join(names), limit)


def _public_character_summary(uid: str, player: Any) -> str:
    if not isinstance(player, dict):
        return ""
    sheet = player.get("character_sheet")
    if not isinstance(sheet, dict):
        sheet = player
    nested = sheet.get("ruleset_character")
    nested = nested if isinstance(nested, dict) else {}
    profile = nested.get("profile")
    profile = profile if isinstance(profile, dict) else sheet.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    name = _text(player.get("character_name") or sheet.get("character_name") or uid, 100)
    traits: list[str] = []
    race = _text(sheet.get("race"), 60)
    role = _text(sheet.get("class"), 80)
    if race:
        traits.append(f"race/species: {race}")
    if role:
        traits.append(f"role: {role}")
    appearance = _first_text(profile, _APPEARANCE_KEYS, 520)
    if not appearance:
        appearance = _first_text(nested, _APPEARANCE_KEYS, 520)
    if not appearance:
        appearance = _first_text(sheet, _APPEARANCE_KEYS, 520)
    if not appearance:
        appearance = _background_visual_excerpt(sheet.get("background"), 360)
    if not appearance:
        appearance = _background_visual_excerpt(nested.get("background"), 360)
    equipment = _equipment_visual_excerpt(sheet.get("equipment"))
    details: list[str] = []
    if appearance:
        details.append(f"appearance: {appearance}")
    if equipment:
        details.append(f"visual equipment: {equipment}")
    if not details and not traits:
        return name
    return f"{name} ({'; '.join(traits + details)})"


def public_character_appearances(players: Any) -> dict[str, str]:
    """Return bounded, public appearance summaries keyed by stable player ID."""
    if not isinstance(players, dict):
        return {}
    result: dict[str, str] = {}
    for uid, player in list(players.items())[:16]:
        key = _text(uid, 80)
        summary = _public_character_summary(key, player)
        if key and summary:
            result[key] = summary[:900]
    return result


def normalize_scene_panels(
    raw: Any,
    *,
    max_panels: int = MAX_STORYBOARD_PANELS,
    merge_same_location: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Normalize untrusted panel data and return ``(panels, compressed_count)``."""

    if not isinstance(raw, (list, tuple)):
        return [], 0
    panels: list[dict[str, Any]] = []
    by_location: dict[str, dict[str, Any]] = {}
    overflow = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        participants = _participants(item.get("participants", item.get("players")))
        location = _text(item.get("location"), 160)
        description = _text(item.get("description", item.get("prompt")), 700)
        if not location and not description:
            continue
        location = location or "当前场景"
        description = description or location
        location_key = re.sub(r"[\s，,。.!！?？:：;；、]+", "", location).casefold()
        existing = by_location.get(location_key) if merge_same_location else None
        if existing is not None:
            existing["participants"] = list(dict.fromkeys(
                [*existing["participants"], *participants]
            ))[:8]
            if description not in existing["description"]:
                existing["description"] = _text(
                    f"{existing['description']}；{description}", 700,
                )
            continue
        if len(panels) >= max(1, int(max_panels)):
            overflow += 1
            continue
        panel = {
            "participants": participants,
            "location": location,
            "description": description,
        }
        panels.append(panel)
        if merge_same_location:
            by_location[location_key] = panel
    return panels, overflow


def _fallback_scene_panel(
    *, narration: str, actions: Any, current_scene: str, players: Any,
    global_prompt: str,
) -> list[dict[str, Any]]:
    participant_ids: list[str] = []
    if isinstance(actions, (list, tuple)):
        participant_ids.extend(
            _text(action.get("user_id"), 80)
            for action in actions
            if isinstance(action, dict) and _text(action.get("user_id"), 80)
        )
    if isinstance(players, dict):
        narration_folded = str(narration or "").casefold()
        for uid, player in players.items():
            uid_text = _text(uid, 80)
            name = _text(player.get("character_name"), 100) if isinstance(player, dict) else ""
            if uid_text and (
                uid_text.casefold() in narration_folded
                or bool(name and name.casefold() in narration_folded)
            ):
                participant_ids.append(uid_text)
        if not participant_ids and _PARTY_MARKERS.search(str(narration or "")):
            participant_ids.extend(_text(uid, 80) for uid in players if _text(uid, 80))
    location = _text(current_scene, 160) or "当前场景"
    description = _text(narration, 700) or _text(global_prompt, 700) or location
    return [{
        "participants": list(dict.fromkeys(participant_ids))[:8],
        "location": location,
        "description": description,
    }]


def _json_object(text: Any) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(raw[start:end + 1])
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _evidence_text(value: Any) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", str(value or "").casefold())


_EVIDENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])|[\r\n]+")
_TRANSITION_MARKERS = (
    "与此同时", "同时", "另一边", "而在", "转眼", "随后", "接着", "然后",
    "紧接着", "片刻后", "稍后", "meanwhile", "elsewhere", "then", "afterward",
    "later", "at the same time",
)

_MOVEMENT_MARKERS = (
    "进入", "走进", "钻进", "退入", "退出", "回到", "返回", "绕回", "来到", "抵达",
    "前往", "赶到", "穿过", "离开", "enter", "return", "arrive", "leave", "move to",
)

_LOCATION_MARKERS = (
    "寄宿屋", "门口", "门链", "后院", "前厅", "厨房", "楼梯口", "空屋",
    "塔底", "船肋", "残骸", "暗道", "窄巷", "巷口", "车站", "码头", "钟塔",
    "宿舍", "地下室", "大厅", "走廊", "屋顶", "庭院", "街口", "海滩",
    "inn", "door", "backyard", "foyer", "kitchen", "stair", "tower", "wreck",
    "passage", "alley", "station", "harbor", "basement", "hall", "corridor",
    "rooftop", "yard", "street", "shore",
)


def _public_evidence_segments(
    *,
    narration: str,
    actions: Any,
    current_scene: str,
    global_prompt: str,
) -> list[dict[str, str]]:
    """Build bounded, numbered evidence from public inputs only."""

    segments: list[dict[str, str]] = []

    def add(kind: str, value: Any, prefix: str, max_chars: int = 520) -> None:
        text = _text(value, max_chars)
        if not text:
            return
        chunks = [chunk.strip() for chunk in _EVIDENCE_SPLIT_RE.split(text) if chunk.strip()]
        if not chunks:
            chunks = [text]
        for chunk in chunks[:12]:
            if len(segments) >= 32:
                return
            segments.append({
                "id": f"{prefix}{sum(1 for item in segments if item['id'].startswith(prefix)) + 1}",
                "kind": kind,
                "text": chunk[:520],
            })

    add("scene", current_scene, "s")
    # Keep the same public narration window used by the generation service.
    # Truncating before sentence splitting used to hide later locations from
    # model evidence in long rounds, causing a valid storyboard to collapse.
    add("narration", narration, "n", 1600)
    if isinstance(actions, (list, tuple)):
        for index, action in enumerate(actions[:16], 1):
            if isinstance(action, dict):
                text = action.get("text")
                if text:
                    add("action", f"{action.get('user_id') or ''}: {text}", f"a{index}-")
    # The image prompt is styling/instructional context, not evidence of a
    # real public place or event; never let it authorize a storyboard claim.
    return segments


def _panel_evidence_ids(item: dict[str, Any]) -> list[str]:
    for key in ("evidence_ids", "evidence_refs", "source_ids", "source_evidence"):
        value = item.get(key)
        if isinstance(value, str):
            value = [value]
        if isinstance(value, (list, tuple, set)):
            return list(dict.fromkeys(
                _text(entry, 40) for entry in value if _text(entry, 40)
            ))[:8]
    return []


def _location_evidence_tokens(location: str) -> list[str]:
    compact = _evidence_text(location)
    if not compact:
        return []
    parts = re.split(r"(?:与|和|及|、|,|，|/|\\|and|&)", location, flags=re.IGNORECASE)
    tokens = [_evidence_text(part) for part in parts if _evidence_text(part)]
    return list(dict.fromkeys([compact, *[token for token in tokens if len(token) >= 2]]))


def _location_has_evidence(location: str, evidence: list[dict[str, str]]) -> bool:
    tokens = _location_evidence_tokens(location)
    if not tokens:
        return False
    evidence_text = " ".join(_evidence_text(item.get("text")) for item in evidence)
    return any(token in evidence_text for token in tokens)


def _panels_within_narration_budget(panels: list[dict[str, Any]], narration: str) -> bool:
    """Keep GM storyboard summaries no longer than the public narration."""
    source_length = len(str(narration or "").strip())
    summary_length = sum(
        len(str(panel.get("location") or ""))
        + len(str(panel.get("description") or ""))
        for panel in panels
    )
    return summary_length <= max(1, source_length)


def _has_multiple_candidates(segments: list[dict[str, str]], narration: str) -> bool:
    narration_text = str(narration or "").casefold()
    transition = any(marker in narration_text for marker in _TRANSITION_MARKERS)
    narration_segments = [item for item in segments if item["kind"] == "narration"]
    return len(narration_segments) >= 2 and (transition or len(narration_segments) >= 3)


def _chunk_location(chunk: str, current_scene: str, index: int) -> str:
    """Return a location only when the chunk establishes a visual scene."""

    folded = chunk.casefold()
    hits = [
        (folded.find(marker.casefold()), marker)
        for marker in _LOCATION_MARKERS
        if marker.casefold() in folded
    ]
    hits.sort(key=lambda item: item[0])
    if not hits:
        return _text(current_scene, 160) if index == 0 else ""

    leading = chunk.lstrip(" \t\r\n，,。.!！?？:：;；、'\"")
    for transition in _TRANSITION_MARKERS:
        if leading.casefold().startswith(transition.casefold()):
            leading = leading[len(transition):].lstrip(" ，,。.!！?？:：;；、")
            break
    leading_folded = leading.casefold()
    leading_hits = [
        marker for marker in _LOCATION_MARKERS
        if 0 <= leading_folded.find(marker.casefold()) <= 10
    ]
    last_move = max((folded.rfind(marker.casefold()) for marker in _MOVEMENT_MARKERS), default=-1)
    moved_hits = [
        marker for position, marker in hits
        if last_move >= 0 and last_move < position <= last_move + 48
    ]
    if moved_hits:
        scene_hits = leading_hits if any(leading_folded.find(marker.casefold()) <= 2 for marker in leading_hits) else []
        return "".join(dict.fromkeys([*scene_hits, *moved_hits][:3]))
    if leading_hits:
        return "".join(dict.fromkeys(leading_hits[:3]))

    if index == 0:
        return "".join(dict.fromkeys(marker for _, marker in hits[:3]))
    return ""


def _chunk_participants(chunk: str, players: list[dict[str, str]]) -> list[str]:
    folded = chunk.casefold()
    return [
        player["id"] for player in players
        if player.get("id") and (
            player["id"].casefold() in folded
            or bool(player.get("name") and player["name"].casefold() in folded)
        )
    ]


def _deterministic_candidate_panels(
    *, narration: str, current_scene: str, players: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Recover obvious public scene beats when the model collapses them to one panel."""

    chunks = [chunk.strip() for chunk in _EVIDENCE_SPLIT_RE.split(_text(narration, 1600)) if chunk.strip()]
    if len(chunks) < 2:
        return []
    candidates: list[dict[str, Any]] = []
    active_location = _text(current_scene, 160)
    for index, chunk in enumerate(chunks):
        established_location = _chunk_location(chunk, active_location, index)
        participants = _chunk_participants(chunk, players)
        if not candidates:
            active_location = established_location or active_location or "当前场景"
            candidates.append({
                "participants": participants,
                "location": active_location,
                "description": chunk[:700],
            })
            continue
        if established_location and _evidence_text(established_location) != _evidence_text(active_location):
            active_location = established_location
            if len(candidates) < MAX_STORYBOARD_PANELS:
                candidates.append({
                    "participants": participants,
                    "location": active_location,
                    "description": chunk[:700],
                })
                continue
        panel = candidates[-1]
        panel["participants"] = list(dict.fromkeys([
            *panel["participants"], *participants,
        ]))[:8]
        panel["description"] = _text(f"{panel['description']} {chunk}", 700)
    return candidates if len(candidates) >= 2 else []


def _resolve_model_panels(
    raw_panels: Any,
    *,
    public_players: list[dict[str, str]],
    evidence_segments: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], int, bool]:
    """Validate model panels against public IDs and referenced evidence."""

    if not isinstance(raw_panels, (list, tuple)):
        return [], 0, False
    allowed_ids = {item["id"] for item in public_players}
    name_to_id = {item["name"].casefold(): item["id"] for item in public_players}
    evidence_by_id = {item["id"]: item for item in evidence_segments}
    candidate_items: list[dict[str, Any]] = []
    for raw in raw_panels[:MAX_STORYBOARD_PANELS + 2]:
        if not isinstance(raw, dict):
            continue
        source_ids = _panel_evidence_ids(raw)
        if source_ids and any(source_id not in evidence_by_id for source_id in source_ids):
            return [], 0, False
        source = [evidence_by_id[source_id] for source_id in source_ids] if source_ids else evidence_segments
        # A key beat may reference the action sentence while its shared
        # location is established by the current-scene evidence segment.
        if source_ids:
            source = source + [item for item in evidence_segments if item["kind"] == "scene"]
        location = _text(raw.get("location"), 160)
        if not location or not _location_has_evidence(location, source):
            return [], 0, False
        participants = _participants(raw.get("participants", raw.get("players")))
        resolved: list[str] = []
        for participant in participants:
            uid = participant if participant in allowed_ids else name_to_id.get(participant.casefold(), "")
            if participant and not uid:
                return [], 0, False
            if uid and uid not in resolved:
                resolved.append(uid)
        if not resolved:
            source_text = _text(raw.get("description", raw.get("prompt")), 700)
            if source_ids:
                source_text = " ".join([
                    *(item.get("text", "") for item in source),
                    source_text,
                ])
            resolved = _chunk_participants(source_text, public_players)
        item = dict(raw)
        item["participants"] = resolved
        item.pop("evidence_ids", None)
        item.pop("evidence_refs", None)
        item.pop("source_ids", None)
        item.pop("source_evidence", None)
        candidate_items.append(item)
    normalized, removed = normalize_scene_panels(
        candidate_items, merge_same_location=False,
    )
    return normalized, removed, bool(candidate_items)


async def infer_scene_panels(
    llm_client: Any,
    *,
    narration: str,
    actions: Any,
    current_scene: str,
    players: Any,
    global_prompt: str = "",
    declared_panels: Any = None,
    force_single: bool = False,
    strict: bool = False,
    requested_panel_count: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Infer public simultaneous/key beats, with one bounded review pass."""

    # Explicit panels are authoritative. Preserve same-location key beats;
    # only model-free automatic fallback uses the single-scene default.
    declared, declared_compressed = normalize_scene_panels(
        declared_panels,
        merge_same_location=False,
    )
    # A user-selected count explicitly requests a fresh re-plan. Existing GM
    # panels remain authoritative only for automatic mode.
    if declared and requested_panel_count is None:
        if strict:
            # GM-provided panels are checked against only the public round
            # evidence before they are accepted as the automatic draft.
            public_ids = {
                _text(uid, 80) for uid in (players.keys() if isinstance(players, dict) else [])
                if _text(uid, 80)
            }
            evidence = _public_evidence_segments(
                narration=narration,
                actions=actions,
                current_scene=current_scene,
                global_prompt=global_prompt,
            )
            visible_text = " ".join(_evidence_text(item.get("text")) for item in evidence)
            for panel in declared:
                if not _location_has_evidence(panel["location"], evidence):
                    raise StoryboardInferenceError("自动分镜包含没有公开依据的地点")
                for participant in panel.get("participants") or []:
                    folded = _evidence_text(participant)
                    if participant not in public_ids and (not folded or folded not in visible_text):
                        raise StoryboardInferenceError("自动分镜包含未知或未出场人物")
            if not _panels_within_narration_budget(declared, narration):
                raise StoryboardInferenceError("自动分镜摘要超过本轮公开正文长度")
        if force_single:
            declared = declared[:1]
        return declared, declared_compressed

    fallback = _fallback_scene_panel(
        narration=narration,
        actions=actions,
        current_scene=current_scene,
        players=players,
        global_prompt=global_prompt,
    )
    if force_single:
        return fallback, 0
    if llm_client is None or not hasattr(llm_client, "call"):
        if strict or requested_panel_count is not None:
            raise StoryboardInferenceError("自动分镜分析不可用：尚未配置文本模型")
        return fallback, 0

    public_players: list[dict[str, str]] = []
    if isinstance(players, dict):
        for uid, player in list(players.items())[:16]:
            uid_text = _text(uid, 80)
            if not uid_text:
                continue
            name = ""
            if isinstance(player, dict):
                name = _text(player.get("character_name"), 100)
            public_players.append({"id": uid_text, "name": name or uid_text})
    if len(public_players) < 2 and requested_panel_count is None:
        deterministic = _deterministic_candidate_panels(
            narration=narration, current_scene=current_scene, players=public_players,
        )
        return (deterministic, 0) if deterministic else (fallback, 0)
    public_actions: list[dict[str, str]] = []
    if isinstance(actions, (list, tuple)):
        for action in actions[:16]:
            if not isinstance(action, dict):
                continue
            text = _text(action.get("text"), 500)
            if text:
                public_actions.append({
                    "player_id": _text(action.get("user_id"), 80),
                    "action": text,
                })

    evidence_segments = _public_evidence_segments(
        narration=narration,
        actions=actions,
        current_scene=current_scene,
        global_prompt=global_prompt,
    )
    if requested_panel_count is not None:
        requested_panel_count = max(1, min(MAX_STORYBOARD_PANELS, int(requested_panel_count)))
    payload = {
        "current_scene": _text(current_scene, 160),
        "players": public_players,
        "public_actions": public_actions,
        "public_gm_narration": _text(narration, 6000),
        "gm_visual_draft": _text(global_prompt, 1800),
        "public_evidence": evidence_segments,
    }
    if requested_panel_count is not None:
        payload["requested_panel_count"] = requested_panel_count
    system_prompt = (
        "You analyze public tabletop RPG narration for scene illustration. "
        "Return only one JSON object with a panels array. "
        "Preserve story order. Each panel must contain participants (exact player IDs), location, "
        "description, and evidence_ids referencing one or more public_evidence IDs. "
        "Every location and beat must be supported by its referenced evidence. Never infer secrets "
        "or private content. Keep descriptions concise; describe visible moments, not commentary. "
        "Do not turn locations merely mentioned in dialogue or future plans into present scenes. "
        "Maximum six panels. Also return compressed_count."
    )
    if requested_panel_count is not None:
        system_prompt += (
            f" The user explicitly selected {requested_panel_count} panels. Return exactly "
            f"{requested_panel_count} panels, reorganizing public beats across that count. "
            "This is a hard output requirement, not a maximum or suggestion. Even when the narration "
            "has fewer natural locations, reach the exact count by separating meaningful actions, "
            "results, reactions, or reveals into consecutive visual beats at the same location. "
            "Do not duplicate content, create empty filler, or mechanically split isolated sentences. "
            "Keep enough context in every panel to preserve continuity."
        )
    else:
        system_prompt += (
            " Use 2-4 panels for distinct simultaneous locations OR visually independent key beats "
            "such as a meaningful action/result, reveal, or clear time/location transition; "
            "use 5-6 only for unusually dense public story. "
            "Do not split ordinary dialogue, tiny consecutive motions, or camera angles. "
            "Merge characters at the same location unless the key beats are visually independent."
        )

    async def call_model(request_system_prompt: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        # Six structured panels need more room than the compact automatic draft.
        token_budget = 900
        if requested_panel_count is not None:
            token_budget = max(900, 300 + requested_panel_count * 360)
        response = await llm_client.call(
            request_system_prompt,
            json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")),
            temperature=0.1,
            max_tokens=token_budget,
            json_mode=True,
        )
        return _json_object(getattr(response, "content", ""))

    if requested_panel_count is not None:
        # Fixed counts have their own bounded path. Neither normalization
        # truncation nor the automatic single-scene fallback can satisfy it.
        request_payload = payload
        actual_count = 0
        for attempt in range(2):
            try:
                parsed = await call_model(system_prompt, request_payload)
            except Exception as exc:
                raise StoryboardInferenceError("分镜分析请求失败，请重试") from exc
            raw_panels = parsed.get("panels")
            actual_count = len(raw_panels) if isinstance(raw_panels, list) else 0
            inferred, removed, valid = _resolve_model_panels(
                raw_panels, public_players=public_players, evidence_segments=evidence_segments,
            )
            complete = isinstance(raw_panels, list) and all(
                isinstance(panel, dict)
                and _text(panel.get("description"), 700)
                and _text(panel.get("location"), 160)
                and _panel_evidence_ids(panel)
                for panel in raw_panels
            )
            unique = len({
                (panel["location"], tuple(panel["participants"]), panel["description"])
                for panel in inferred
            }) == requested_panel_count
            if valid and complete and unique and actual_count == requested_panel_count and len(inferred) == requested_panel_count:
                if strict and not _panels_within_narration_budget(inferred, narration):
                    raise StoryboardInferenceError("自动分镜摘要超过本轮公开正文长度")
                return inferred, removed
            if attempt == 0:
                request_payload = {
                    **payload,
                    "first_pass": parsed,
                    "review_instruction": (
                        f"The first pass produced {len(inferred)} valid panels ({actual_count} raw), "
                        f"but the user requires exactly {requested_panel_count}. "
                        f"Missing panels: {max(0, requested_panel_count - len(inferred))}. "
                        "Discard that plan and return a complete replacement of the required count. "
                        "Split meaningful actions, results, reactions or reveals at the same location "
                        "when necessary, preserving context. Do not duplicate, truncate or add empty filler. "
                        "Every panel needs exact player IDs, supported location, description and evidence_ids."
                    ),
                }
        raise StoryboardInferenceError(
            f"分镜模型未按指定格数返回有效分镜（要求 {requested_panel_count} 格，实际 {actual_count} 格），请重新分析"
        )

    try:
        parsed = await call_model(system_prompt, payload)
        inferred, removed, valid = _resolve_model_panels(
            parsed.get("panels"), public_players=public_players, evidence_segments=evidence_segments,
        )
    except Exception as exc:
        parsed, inferred, removed, valid = {}, [], 0, False
        if strict:
            raise StoryboardInferenceError("自动分镜分析失败") from exc
    if not valid or not inferred:
        if not inferred:
            deterministic = _deterministic_candidate_panels(
                narration=narration, current_scene=current_scene, players=public_players,
            ) if _has_multiple_candidates(evidence_segments, narration) else []
            if strict:
                raise StoryboardInferenceError("自动分镜分析未返回有效公开分镜")
            return (deterministic, 0) if deterministic else (fallback, 0)
    if len(inferred) == 1 and not inferred[0]["participants"]:
        inferred[0]["participants"] = fallback[0]["participants"]

    if len(inferred) == 1 and _has_multiple_candidates(evidence_segments, narration):
        review_payload = {
            **payload,
            "first_pass": parsed,
            "review_instruction": (
                 "Re-evaluate the first-pass single panel. Split only if the public evidence contains "
                 "at least two visually independent key beats or simultaneous locations. Return the "
                 "same schema with evidence_ids; otherwise keep one panel."
            ),
        }
        try:
            reviewed = await call_model(system_prompt, review_payload)
            reviewed_panels, reviewed_removed, reviewed_valid = _resolve_model_panels(
                reviewed.get("panels"), public_players=public_players, evidence_segments=evidence_segments,
            )
            if reviewed_valid and reviewed_panels:
                inferred, removed = reviewed_panels, reviewed_removed
                parsed = reviewed
        except Exception:
            pass
    if len(inferred) == 1 and _has_multiple_candidates(evidence_segments, narration):
        deterministic = _deterministic_candidate_panels(
            narration=narration,
            current_scene=current_scene,
            players=public_players,
        )
        if deterministic:
            inferred, deterministic_removed = normalize_scene_panels(
                deterministic,
                merge_same_location=False,
            )
            removed += deterministic_removed
    try:
        reported_compressed = max(0, int(parsed.get("compressed_count") or 0))
    except (TypeError, ValueError):
        reported_compressed = 0
    if strict and not _panels_within_narration_budget(inferred, narration):
        raise StoryboardInferenceError("自动分镜摘要超过本轮公开正文长度")
    return inferred, reported_compressed + removed


def storyboard_layout(panel_count: int) -> str:
    return {
        1: "single", 2: "two-panel", 3: "three-panel", 4: "four-panel",
        5: "five-panel", 6: "six-panel",
    }.get(max(1, min(MAX_STORYBOARD_PANELS, int(panel_count or 1))), "single")


def storyboard_metadata(panels: Any, compressed_count: int = 0) -> dict[str, Any]:
    # At this boundary the panel list is already authoritative. Two visually
    # independent beats may intentionally share a location, so composition
    # must preserve their order instead of merging them back into one frame.
    normalized, removed = normalize_scene_panels(panels, merge_same_location=False)
    return {
        "layout": storyboard_layout(len(normalized)),
        "panels": normalized,
        "compressed_count": max(0, int(compressed_count or 0)) + removed,
    }


def _character_display_name(uid: str, appearances: Any) -> str:
    if not isinstance(appearances, dict):
        return uid
    summary = _text(appearances.get(uid), 900)
    if not summary:
        return uid
    return summary.split(" (", 1)[0].strip() or uid


def build_storyboard_prompt(
    panels: Any,
    *,
    global_prompt: str = "",
    character_appearances: Any = None,
    max_chars: int = 8000,
) -> tuple[str, dict[str, Any]]:
    """Build a provider-neutral prompt and metadata for one shared image."""

    metadata = storyboard_metadata(panels)
    normalized = metadata["panels"]
    max_chars = max(256, min(int(max_chars or 8000), 8000))
    if not normalized:
        return _text(global_prompt, max_chars), metadata
    if len(normalized) == 1:
        lines = [
            "Create one continuous wide tabletop RPG scene illustration in a single frame.",
            "This is one location and one uninterrupted camera view. Do not create panels, "
            "comic grids, collages, split screens, gutters, borders, or multiple separate images.",
        ]
    else:
        layout_instruction = (
            "Choose a balanced adaptive arrangement for the scene importance while preserving the exact region count. "
            "The visual model may freely choose the geometry, relative sizes, spacing, and transitions between regions."
        )
        lines = [
            "Create one shared tabletop RPG storyboard image with exactly "
            f"{len(normalized)} distinct panels, in the listed order.",
            layout_instruction,
            f"Do not merge, omit, add, or repeat panels. The finished image must visibly contain exactly {len(normalized)} regions.",
        ]
    included_appearance_uids: set[str] = set()
    for index, panel in enumerate(normalized, 1):
        people = ", ".join(
            _character_display_name(uid, character_appearances)
            for uid in panel["participants"]
        ) or "only people explicitly named in this panel description; no other party members"
        label = "Scene" if len(normalized) == 1 else f"Panel {index}"
        lines.append(
            f"{label}: location {panel['location']}; subjects {people}; "
            f"visual description {panel['description']}."
        )
        if isinstance(character_appearances, dict):
            selected = panel["participants"]
            candidates = (
                [
                    (uid, character_appearances[uid])
                    for uid in selected
                    if uid in character_appearances and uid not in included_appearance_uids
                ]
                if selected else []
            )
            summaries = []
            for uid, value in candidates:
                summary = _text(value, 900)
                if summary:
                    summaries.append(summary)
                    included_appearance_uids.add(uid)
            if summaries:
                lines.append(
                    label + " public character appearance references: "
                    + " | ".join(summaries[:8]) + ". Keep these identities and clothing consistent."
                )
    if global_prompt.strip():
        lines.append(f"Overall context and style: {_text(global_prompt, 1800)}")
    if len(normalized) == 1:
        lines.append(
            "Use a natural horizontal scene composition with restrained colors and a readable environment. "
            "No text, names, labels, watermark, speech bubbles, UI, or internal divider lines."
        )
    else:
        lines.append(
            "Compose one coherent multi-scene image with one clear visual region per panel and keep each "
            "subject inside its intended region. Let the requested style decide whether regions use spacing, "
            "soft transitions, borders, gutters, or other separators; the software will not add or enforce "
            "any divider color or line. Use restrained colors and readable environments, with no text, names, "
            "labels, watermark, speech bubbles, or UI."
        )
    return _fit_storyboard_prompt(lines, len(normalized), max_chars), metadata


def _fit_storyboard_prompt(lines: list[str], panel_count: int, max_chars: int) -> str:
    """Shorten descriptions, never panel identities or later panel markers."""

    prompt = "\n".join(lines)
    if len(prompt) <= max_chars:
        return prompt

    compact = [
        line for line in lines
        if "appearance references:" not in line and not line.startswith("Overall context and style:")
    ]
    prompt = "\n".join(compact)
    if len(prompt) <= max_chars:
        return prompt

    header = (
        f"Create one image with exactly {panel_count} distinct panels in listed order; "
        f"exactly {panel_count} regions. Choose an adaptive layout. Do not merge, omit, add or repeat panels. No text."
        if panel_count > 1 else
        "Create one continuous scene in a single frame. No panels, grids, collages, internal dividers or text."
    )
    parts = [
        line.replace(
            "only people explicitly named in this panel description; no other party members",
            "only explicitly described people",
        ).partition("; visual description ")
        for line in compact if line.startswith(("Scene:", "Panel "))
    ]
    prefixes = [prefix + separator for prefix, separator, _ in parts]
    descriptions = [description for _, _, description in parts]
    available = max_chars - len(header) - len(parts) - sum(map(len, prefixes))
    minimums = [min(16, len(description)) for description in descriptions]
    if available < sum(minimums):
        raise StoryboardInferenceError("提示词预算不足以保留全部分镜的地点、人物和动作，请缩短内容")
    allowances = minimums[:]
    remaining = available - sum(allowances)
    while remaining and any(allowances[i] < len(text) for i, text in enumerate(descriptions)):
        for index, text in enumerate(descriptions):
            if remaining and allowances[index] < len(text):
                allowances[index] += 1
                remaining -= 1
    shortened = [
        prefix + (text if len(text) <= allowance else text[:allowance - 1] + "…")
        for prefix, text, allowance in zip(prefixes, descriptions, allowances)
    ]
    return "\n".join([header, *shortened])
