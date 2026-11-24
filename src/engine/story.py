"""Versioned public story sequences shared by chat and future stage renderers.

The GM writes one JSON object per narrative beat before the existing state-tag
separator. This module owns that presentation protocol, opaque cast identities,
and public projection. It never creates a gameplay NPC or applies a rule effect.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from src.llm.parser import sanitize_narration
from src.engine import module_hooks

STORY_VERSION = 1
MAX_SEGMENTS = 80
MAX_TEXT = 12000
MAX_CAST = 120
SPEAKER_ID = re.compile(r"^[sg]_[a-f0-9]{20}$")
NEW_SPEAKER = re.compile(r"^new:[A-Za-z0-9_-]{1,64}$")


class StoryFormatError(ValueError):
    """The model did not produce the declared public story protocol."""


def validate_story_mode(value: Any) -> str:
    if not isinstance(value, str) or value not in {"prose", "dialogue"}:
        raise ValueError("unsupported story presentation mode")
    return value


def read_story_mode(instance: Any) -> str:
    return module_hooks.ask(module_hooks.STATE_READ, instance, module_hooks.STORY_MODE, default="prose")


@runtime_checkable
class StorySink(Protocol):
    """Optional capability on an existing plain narration callback."""

    async def story_update(self, story: dict[str, Any]) -> None: ...


def _opaque(instance: Any, prefix: str, key: str) -> str:
    run_namespace = getattr(instance, "run_id", "")
    if prefix == "s":
        run_namespace = module_hooks.ask(module_hooks.STATE_READ, instance, module_hooks.STORY_IDENTITY_NAMESPACE, default=run_namespace)
    namespace = f"{getattr(instance, 'game_key', '')}|{run_namespace}|{key}"
    return prefix + "_" + hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:20]


def _portrait(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    fields = {"builtin": ("id",), "upload": ("asset_id",), "generated": ("asset_id",), "plugin": ("plugin_id", "path")}
    if kind not in fields:
        return None
    result = {"kind": kind}
    for key in fields[kind]:
        item = value.get(key)
        if not isinstance(item, str) or not item or len(item) > 500:
            return None
        result[key] = item
    return result


def speaker_catalog(instance: Any, history: list[dict] | None = None) -> dict[str, dict[str, Any]]:
    """Build a bounded roster from active history and real NPC identity.

    Display names are never used to merge two actors. Discarded swipe branches
    do not enter this roster. No private canonical ID leaves this function.
    """
    roster: dict[str, dict[str, Any]] = {}
    aliases = module_hooks.read_value(instance, module_hooks.STORY_SPEAKER_ALIASES) or {}
    for entry in reversed(history if history is not None else getattr(instance, "log", [])):
        story = entry.get("story") if isinstance(entry, dict) else None
        if not isinstance(story, dict) or story.get("version") != STORY_VERSION:
            continue
        raw_segments = story.get("segments")
        if not isinstance(raw_segments, list):
            continue
        for segment in reversed(raw_segments):
            if not isinstance(segment, dict) or segment.get("kind") != "dialogue":
                continue
            ref, name = segment.get("speaker_id"), segment.get("speaker_name")
            if isinstance(ref, str) and SPEAKER_ID.fullmatch(ref) and isinstance(name, str) and name.strip():
                ref = aliases.get(ref, ref)
                roster.setdefault(ref, {"name": name[:80], "portrait": _portrait(segment.get("portrait"))})
            if len(roster) >= MAX_CAST:
                return roster
    for key, npc in getattr(instance, "npcs", {}).items():
        if not isinstance(npc, dict):
            continue
        name = str(npc.get("character_name") or npc.get("name") or "").strip()
        if not name:
            continue
        ref = _opaque(instance, "s", f"npc:{key}")
        if ref not in roster or roster[ref]["name"] == name[:80]:
            roster[ref] = {"name": name[:80], "portrait": _portrait(npc.get("portrait"))}
        if len(roster) >= MAX_CAST:
            break
    return roster


def _segment_text(value: Any) -> str:
    return sanitize_narration(value[:MAX_TEXT]).strip() if isinstance(value, str) else ""


def parse_story(
    instance: Any, text: str, *, round_number: int | None = None,
    revision: int = 0, history: list[dict] | None = None, strict: bool = False,
) -> dict[str, Any]:
    """Decode model NDJSON; invalid identities fall back to un-attributed text.

    New cast handles are scoped to one generation, then replaced with server
    IDs. A later generation must explicitly reuse an ID from its roster.
    """
    roster = speaker_catalog(instance, history)
    if round_number is None:
        from src.engine.modules import progression_state
        round_number = progression_state.round_value(instance)
    segments: list[dict[str, Any]] = []
    response_names: dict[str, str] = {}
    malformed = False
    source = str(text or "").strip()
    if source.startswith("```"):
        source = re.sub(r"^```(?:jsonl|ndjson|json)?\s*\n?", "", source)
        source = re.sub(r"\n?```\s*$", "", source)
    lines = source.splitlines()
    structured = any(line.lstrip().startswith(("{", "}", "[", "]", '"')) for line in lines)
    if structured:
        # Formatting repair may return a pretty-printed object or array. Read
        # its schema as a whole; never treat its individual metadata lines as
        # public prose, even while the object is incomplete during streaming.
        try:
            document = json.loads(source)
        except (ValueError, TypeError):
            document = None
        if isinstance(document, dict):
            lines = [json.dumps(document, ensure_ascii=False)]
        elif isinstance(document, list):
            lines = [json.dumps(row, ensure_ascii=False) for row in document]
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            malformed = True
            # JSON fragments may contain private references. Never display them.
            if not structured and not line.lstrip().startswith("```"):
                clean = _segment_text(line)
                if clean:
                    segments.append({"id": f"beat_{len(segments)}", "kind": "narration", "text": clean})
            continue
        if not isinstance(row, dict):
            malformed = True
            continue
        clean = _segment_text(row.get("text"))
        if not clean:
            malformed = True
            continue
        segment: dict[str, Any] = {"id": f"beat_{len(segments)}", "kind": "narration", "text": clean}
        if row.get("kind") == "dialogue":
            ref = row.get("speaker")
            name = _segment_text(row.get("name"))[:80]
            if (not isinstance(ref, str) or not name
                    or (ref not in roster and not NEW_SPEAKER.fullmatch(ref))
                    or (ref in response_names and response_names[ref] != name)):
                malformed = True
                segment["text"] = f"{name}：{clean}" if name else clean
            else:
                response_names[ref] = name
                candidate = roster.get(ref)
                if candidate and candidate["name"] == name:
                    speaker_id = ref
                elif candidate:
                    # A disguise does not expose a known character's identity,
                    # avatar, or a cross-link to their earlier public persona.
                    speaker_id = _opaque(instance, "g", f"alias:{round_number}:{revision}:{ref}")
                else:
                    speaker_id = _opaque(instance, "g", f"new:{round_number}:{revision}:{ref}")
                segment.update(kind="dialogue", speaker_id=speaker_id, speaker_name=name)
                if candidate and candidate["name"] == name and candidate.get("portrait"):
                    segment["portrait"] = copy.deepcopy(candidate["portrait"])
        elif row.get("kind") != "narration":
            malformed = True
        segments.append(segment)
        if len(segments) >= MAX_SEGMENTS:
            break
    if strict and (malformed or not segments):
        raise StoryFormatError("Expected narration/dialogue JSON lines with valid speaker references")
    # Plain historical prose remains one block; do not infer quote ownership.
    if malformed and segments and not structured:
        segments = [{"id": "beat_0", "kind": "narration", "text": _segment_text(source)}]
    return {"version": STORY_VERSION, "segments": segments}


def public_story(instance: Any, raw: Any) -> dict[str, Any] | None:
    """Whitelist every nested field, including uploaded/imported log data."""
    if not isinstance(raw, dict) or raw.get("version") != STORY_VERSION or not isinstance(raw.get("segments"), list):
        return None
    overrides = module_hooks.read_value(instance, module_hooks.STORY_PORTRAITS) or {}
    aliases = module_hooks.read_value(instance, module_hooks.STORY_SPEAKER_ALIASES) or {}
    segments: list[dict[str, Any]] = []
    for row in raw["segments"][:MAX_SEGMENTS]:
        if not isinstance(row, dict):
            continue
        clean = _segment_text(row.get("text"))
        if not clean:
            continue
        segment: dict[str, Any] = {"id": f"beat_{len(segments)}", "kind": "narration", "text": clean}
        ref, name = row.get("speaker_id"), row.get("speaker_name")
        if row.get("kind") == "dialogue" and isinstance(ref, str) and SPEAKER_ID.fullmatch(ref) and isinstance(name, str) and name.strip():
            ref = aliases.get(ref, ref)
            segment.update(kind="dialogue", speaker_id=ref, speaker_name=_segment_text(name)[:80])
            portrait = _portrait(overrides[ref] if ref in overrides else row.get("portrait"))
            if portrait:
                segment["portrait"] = portrait
        segments.append(segment)
    return {"version": STORY_VERSION, "segments": segments} if segments else None


def story_text(story: dict[str, Any]) -> str:
    return "\n\n".join(
        f"{row['speaker_name']}：{row['text']}" if row.get("kind") == "dialogue" else row["text"]
        for row in story.get("segments", [])
    )


_SENTENCE_ENDS = "。！？!?\n"
_CLOSERS = "”’」』）》\"'"


def _speaker_prefix(segment: dict[str, Any]) -> int:
    """Characters ``story_text`` adds before a dialogue beat for its speaker."""
    return len(segment.get("speaker_name", "")) + 1 if segment.get("kind") == "dialogue" else 0


def _cut_at_sentence_end(text: str, room: int) -> str:
    """Shorten one oversized beat at its last sentence end inside ``room``."""
    if room <= 0:
        return "…"
    head = text[:room]
    ends = [head.rfind(mark) for mark in _SENTENCE_ENDS]
    ends += [head.rfind(". "), head.rfind(".\n"), len(head) - 1 if head.endswith(".") else -1]
    index = max(ends)
    if index < 0:
        return text[:room - 1].rstrip() + "…"
    end = index + 1
    while end < len(head) and head[end] in _CLOSERS:
        end += 1
    return head[:end].rstrip()


def trim_story(story: dict[str, Any], limit: int) -> dict[str, Any]:
    """Bound overlong output on whole beats without detaching a line from its speaker.

    Beats are kept in order while they fit. The first beat is always kept; when it
    alone is oversized it is cut at a sentence end rather than mid-sentence.
    """
    result: dict[str, Any] = {"version": STORY_VERSION, "segments": []}
    used = 0
    for source in story.get("segments", []):
        row = copy.deepcopy(source)
        gap = 2 if result["segments"] else 0
        width = _speaker_prefix(row) + len(row["text"])
        if result["segments"] and used + gap + width > limit:
            break
        if not result["segments"]:
            room = max(limit - _speaker_prefix(row), 0)
            if len(row["text"]) > room:
                row["text"] = _cut_at_sentence_end(row["text"], room)
                width = _speaker_prefix(row) + len(row["text"])
        result["segments"].append(row)
        used += gap + width
    return result


def story_prompt(instance: Any, history: list[dict] | None = None) -> str:
    roster = speaker_catalog(instance, history)
    cast = [{"speaker": ref, "name": row["name"]} for ref, row in roster.items()]
    return (
        "## Character dialogue presentation (enabled; required)\n"
        "This replaces the prose paragraph-count requirement only. Before the existing `---` state-tag separator, "
        "write NDJSON: exactly one JSON object per line, with no code fence or surrounding array. "
        "Each object is a narrative beat in story order. Use these exact shapes:\n"
        '{"kind":"narration","text":"public scene or action description"}\n'
        '{"kind":"dialogue","speaker":"roster ID or new:local_handle","name":"public name","text":"spoken words only"}\n'
        "Narration and dialogue may alternate. Keep the response concise (normally 3–8 beats within the existing length limit). "
        "Only NPCs may receive GM-written dialogue. Never speak or choose for a player's character. "
        "Use the exact speaker ID from the roster for a returning character; never identify or merge characters by display name. "
        "For a new NPC use new: followed by a distinct ASCII handle for this response (new:guard1 and new:guard2 may share a public name). "
        "Reuse that handle within this response; later use the server ID supplied in the roster. "
        "Registering an NPC does not create a second persona. Continue the prior speaking ID when the same character returns, "
        "even if a newly registered NPC candidate has the same public name. A new handle is only for an actually different person. "
        "For a concealed identity use a new persona handle and only the revealed public name; never disclose a true identity or private facts. "
        "Do not include portrait, canonical IDs, private notes, mechanics, or player speech in a beat. "
        "All existing checks, state-tag, private-message, and output-language requirements still apply. "
        "The ordinary `---` and state tags must follow the final JSON line.\n"
        "Available NPC speakers (presentation IDs only):\n" + json.dumps(cast, ensure_ascii=False)
    )


class StoryStream:
    """Turn arbitrary JSON chunks into complete, sanitized public beats.

    Ordinary callbacks still receive plain text. SSE callbacks may additionally
    implement StorySink to receive versioned snapshots, without changing the
    existing turn-service callback contract.
    """

    def __init__(self, instance: Any, on_delta: Callable[[str], Awaitable[None]], *,
                 on_story: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.instance = instance
        self.on_delta = on_delta
        self.on_story = on_story if on_story is not None else (on_delta.story_update if isinstance(on_delta, StorySink) else None)
        self.buffer = ""
        self.lines: list[str] = []
        self.sent = ""
        self.last_story: dict[str, Any] | None = None

    async def _publish(self) -> None:
        story = parse_story(self.instance, "\n".join(self.lines))
        public = public_story(self.instance, story)
        if public is None:
            return
        text = story_text(public)
        if text.startswith(self.sent):
            delta = text[len(self.sent):]
            if delta:
                await self.on_delta(delta)
        self.sent = text
        self.last_story = public
        if self.on_story:
            await self.on_story(public)

    async def feed(self, text: str) -> None:
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.lines.append(line)
            await self._publish()

    async def flush(self) -> None:
        if self.buffer:
            self.lines.append(self.buffer)
            self.buffer = ""
        await self._publish()
