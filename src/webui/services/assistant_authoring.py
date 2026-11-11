"""Agent authoring capability pack over canonical content-owner boundaries."""
from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from src.application_actions import ActionAssessment, PreparedAction
from src.knowledge.visibility import classify_audience

from src.webui.services.assistant_agent import AgentTool, _digest
from src.webui.services.assistant_agent_application import obj, string
from src.webui.services.assistant_content import _has_private_markers
from src.webui.services.lorebook_visibility_plans import (
    LorebookVisibilityPlanDependencies, LorebookVisibilityPlans, audience_expands,
)
from src.localization.messages import t


@dataclass(frozen=True)
class AuthoringDependencies:
    list_worlds: Callable
    update_world: Callable
    list_book_entries: Callable
    save_book_entry: Callable
    list_cards: Callable
    update_card: Callable
    adventure_detail: Callable
    get_rule: Callable
    write_entries_with_recovery: Callable | None = None
    write_world_with_recovery: Callable | None = None
    write_card_with_recovery: Callable | None = None
    world_snapshot: Callable | None = None
    card_snapshot: Callable | None = None
    game_players: Callable | None = None
    write_book_visibility_with_recovery: Callable | None = None
    preview_book_visibility_with_recovery: Callable | None = None
    book_snapshot: Callable | None = None
    book_visibility_receipt: Callable | None = None


def authoring_tools(deps: AuthoringDependencies) -> tuple[AgentTool, ...]:
    def live_players(game_key, owner):
        if deps.game_players is None:
            raise ValueError('Live game players unavailable')
        return deps.game_players(game_key, owner)

    visibility = None
    if (deps.book_snapshot is not None and deps.preview_book_visibility_with_recovery is not None
            and deps.write_book_visibility_with_recovery is not None and deps.book_visibility_receipt is not None):
        visibility = LorebookVisibilityPlans(LorebookVisibilityPlanDependencies(
            list_entries=deps.list_book_entries, snapshot=deps.book_snapshot,
            preview=deps.preview_book_visibility_with_recovery, write=deps.write_book_visibility_with_recovery,
            receipt=deps.book_visibility_receipt,
            game_players=live_players,
        ))
    def world(world_id):
        row = next((w for w in deps.list_worlds().get("worlds", []) if w.get("id") == world_id), None)
        if not row:
            raise ValueError("World does not exist")
        return row

    def card(card_id):
        row = next((c for c in deps.list_cards().get("cards", []) if c.get("id") == card_id), None)
        if not row:
            raise ValueError("Character card does not exist")
        return row

    def book_entries(book_id):
        result = deps.list_book_entries(book_id)
        if result.get("ok") is False:
            raise ValueError("Lorebook does not exist")
        return result.get("entries", [])

    async def read(args, owner):
        kind, identity = args["kind"], args["id"]
        if kind == "world":
            return {"ok": True, "world": world(identity)}
        if kind == "lorebook":
            return {"ok": True, "entries": book_entries(identity)}
        if kind == "character_template":
            return {"ok": True, "card": card(identity)}
        if kind == "adventure":
            return deps.adventure_detail(identity)
        return deps.get_rule(identity)

    async def edit_world(args, owner):
        current = world(args["world_id"])
        if any(_has_private_markers(str(args.get(field) or ""))
               for field in ("description", "world_setting", "starter_scene")):
            raise ValueError("World descriptions cannot contain private material")
        patch = {field: args[field] for field in ('name', 'description', 'language', 'world_setting', 'starter_scene')
                 if field in args}
        if not patch:
            raise ValueError('Select at least one world field to update')
        if deps.write_world_with_recovery is not None:
            return deps.write_world_with_recovery(owner, args['world_id'], patch)
        return deps.update_world(args["world_id"], patch.get('name', current.get('name', '')),
                                 patch.get('description', current.get('description', '')),
                                 patch.get('language', current.get('language') or 'zh-CN'),
                                 **{field: value for field, value in patch.items()
                                    if field in {"world_setting", "starter_scene"}})

    def entry_rows(args):
        entries = book_entries(args['book_id'])
        existing = {e["id"]: e for e in entries}
        prepared = []
        seen = set()
        # Validate the complete batch before the first write.
        for entry in args["entries"]:
            identity = entry.get("id")
            if identity and identity not in existing:
                raise ValueError("Entry ID does not exist in the selected book; omit it to create")
            if identity and identity in seen:
                raise ValueError("Duplicate entry update")
            seen.add(identity)
            public = (entry.get('visibility') == 'public' if 'visibility' in entry
                      else classify_audience(existing.get(identity)) == 'public')
            if public and _has_private_markers(entry["content"]):
                raise ValueError("A public entry cannot contain private markers")
            row = {key: deepcopy(value) for key, value in entry.items() if key != "visibility"}
            row["id"] = identity or f"agent_entry_{uuid.uuid4().hex}"
            # Preserve an existing entry's audience unless the user explicitly
            # confirms a visibility change. New entries fail closed to GM.
            if "visibility" in entry or not identity:
                row["visible_to"] = ["*"] if public else []
            prepared.append(row)
        return entries, existing, prepared

    def prepare_entries(args, owner):
        entries, existing, rows = entry_rows(args)
        expands = any(audience_expands(existing.get(row['id'], {}),
            row.get('visible_to', existing.get(row['id'], {}).get('visible_to', []))) for row in rows)
        digest = _digest(entries)
        return PreparedAction(digest, {'book_id': args['book_id'], 'entries': args['entries'],
            'requires_publication_consent': expands},
            {'book_id': args['book_id'], 'rows': rows, 'entries_digest': digest},
            ActionAssessment('disclosure' if expands else 'write', expands, ({'book_id': args['book_id']},)))

    async def execute_entries(payload, owner, context):
        context.check()
        return deps.write_entries_with_recovery(owner, payload['book_id'], payload['rows'],
                                                expected_entries_digest=payload['entries_digest'])

    async def write_entries(args, owner):
        _entries, _existing, prepared = entry_rows(args)
        if deps.write_entries_with_recovery is not None:
            return deps.write_entries_with_recovery(owner, args['book_id'], prepared)
        results = []
        for row in prepared:
            try:
                result = deps.save_book_entry(args["book_id"], row)
            except (ValueError, OSError) as exc:
                result = {"ok": False, "error": str(exc)}
            results.append({"id": row["id"], **result})
            if result.get("ok") is not True:
                return {"ok": False, "partial": len(results) > 1, "results": results,
                        "error": "Some entries could not be saved; inspect results before planning again"}
        return {"ok": True, "results": results, "navigation": {"route": "lorebook", "query": {"book": args["book_id"]}}}

    def prepare_entry_visibility(args, owner):
        existing, _audience, rows = visibility_plan(args, owner)
        expands = any(audience_expands(existing[row['id']], row['visible_to']) for row in rows)
        return PreparedAction(_digest(book_entries(args['book_id'])),
            {'book_id': args['book_id'], 'entry_ids': args['entry_ids'], 'audience': args['audience']}, {},
            ActionAssessment('disclosure' if expands else 'write', expands, ({'book_id': args['book_id']},)))

    def visibility_plan(args, owner):
        entries = book_entries(args["book_id"])
        existing = {str(entry.get("id")): entry for entry in entries
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
        entry_ids = args["entry_ids"]
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError("Duplicate entry ID")
        missing = [identity for identity in entry_ids if identity not in existing]
        if missing:
            raise ValueError("Entry ID does not exist in the selected book")

        audience = args["audience"]
        character_ids = args.get("character_ids", [])
        if audience == "characters":
            if not character_ids:
                raise ValueError("character_ids are required for character visibility")
            if not args.get("game_key") or deps.game_players is None:
                raise ValueError("game_key is required to validate live character identities")
            if len(set(character_ids)) != len(character_ids):
                raise ValueError("Duplicate character ID")
            # The composition callback owns game existence and GM scope
            # validation; do not resolve arbitrary game keys locally.
            players_result = deps.game_players(args["game_key"], owner)
            if isinstance(players_result, dict):
                players = players_result.get("players", players_result)
            else:
                players = players_result
            canonical_ids = (set(players) if isinstance(players, dict) else
                             {str(player.get("user_id")) for player in players
                              if isinstance(player, dict) and isinstance(player.get("user_id"), str)})
            unknown = [identity for identity in character_ids if identity not in canonical_ids]
            if unknown:
                raise ValueError("Unknown canonical character ID")
            visible_to = list(character_ids)
        elif character_ids:
            raise ValueError("character_ids are only valid for character visibility")
        else:
            visible_to = ["*"] if audience == "public" else []

        prepared = []
        for identity in entry_ids:
            row = deepcopy(existing[identity])
            if audience != "gm" and _has_private_markers(str(row.get("content") or "")):
                raise ValueError("A non-GM entry cannot contain private markers")
            row["visible_to"] = deepcopy(visible_to)
            prepared.append(row)
        return existing, visible_to, prepared

    async def set_entry_visibility(args, owner):
        """Change only audience markers on existing canonical entry rows."""
        _existing, _visible_to, prepared = visibility_plan(args, owner)
        if deps.write_entries_with_recovery is not None:
            return deps.write_entries_with_recovery(owner, args["book_id"], [
                {'id': row['id'], 'visible_to': row['visible_to']} for row in prepared])
        results = []
        for row in prepared:
            result = deps.save_book_entry(args["book_id"], row)
            results.append({"id": row["id"], **result})
            if result.get("ok") is not True:
                return {"ok": False, "partial": len(results) > 1, "results": results,
                        "error": "Some entries could not be saved; inspect results before planning again"}
        return {"ok": True, "results": results,
                "navigation": {"route": "lorebook", "query": {"book": args["book_id"]}}}

    async def set_book_visibility(args, owner):
        if deps.write_book_visibility_with_recovery is None:
            raise ValueError("Whole-book visibility is unavailable")
        entries = book_entries(args["book_id"])
        if len(entries) > 1000:
            raise ValueError(t("assistant_authoring.book_visibility_too_many_entries"))
        # Reuse the same audience, canonical-player and private-marker checks
        # as the bounded entry action, while submitting only changed patches.
        _existing, _visible_to, prepared = visibility_plan({
            **args, "entry_ids": [str(row.get("id")) for row in entries]}, owner)
        expected = _digest(entries)
        changed = [{"id": row["id"], "visible_to": row["visible_to"]}
                   for row in prepared if row.get("visible_to") != _existing[row["id"]].get("visible_to")]
        result = deps.write_book_visibility_with_recovery(owner, args["book_id"], changed, expected)
        if hasattr(result, "__await__"):
            result = await result
        return {"ok": True, "book_id": args["book_id"], "audience": args["audience"],
                "total": len(entries), "changed_count": len(changed),
                **(result or {})}

    def book_visibility_preview(args, owner):
        entries = book_entries(args["book_id"])
        if len(entries) > 1000:
            raise ValueError(t("assistant_authoring.book_visibility_too_many_entries"))
        existing, _visible_to, prepared = visibility_plan({
            **args, "entry_ids": [str(row.get("id")) for row in entries]}, owner)
        changed_count = sum(row.get("visible_to") != existing[row["id"]].get("visible_to") for row in prepared)
        changed = [{"id": row["id"], "visible_to": row["visible_to"]}
                   for row in prepared if row.get("visible_to") != existing[row["id"]].get("visible_to")]
        if deps.preview_book_visibility_with_recovery is not None:
            value = deps.preview_book_visibility_with_recovery(owner, args["book_id"], changed, _digest(entries))
            if hasattr(value, "__await__"):
                raise TypeError("book visibility preview callback must be synchronous")
            return {**value, "audience": args["audience"]}
        listing = deps.list_book_entries(args["book_id"])
        return {"target_name": str((listing.get("book") or {}).get("name") or args["book_id"]),
                "total": len(entries), "changed_count": changed_count,
                "audience": args["audience"], "batch_count": (changed_count + 15) // 16}

    def book_visibility_guard(args, owner):
        snapshot = (deps.book_snapshot(args["book_id"]) if deps.book_snapshot is not None
                    else deps.list_book_entries(args["book_id"]))
        return _digest({"snapshot": snapshot, "impact": book_visibility_preview(args, owner)})

    async def edit_card(args, owner):
        card(args["card_id"])
        patch = {key: value for key, value in args.items() if key != "card_id"}
        if any(_has_private_markers(str(value)) for value in patch.values()):
            raise ValueError("Character public fields cannot contain private material")
        if deps.write_card_with_recovery is not None:
            return deps.write_card_with_recovery(owner, args['card_id'], patch)
        # This service rejects professional-card legacy updates. No mechanics
        # fields are offered by the agent schema.
        return deps.update_card(args["card_id"], patch)

    entry = obj({"id": string(), "name": string(minimum=1), "content": string(16000, minimum=1),
        "type": string(30, ("npc", "location", "item", "event", "puzzle", "faction", "spell", "class", "other")),
        "keywords": {"type": "array", "items": string(100), "maxItems": 20},
        "visibility": string(20, ("public", "gm"))}, ("name", "content"))
    visibility_schema = obj({
        "book_id": string(minimum=1),
        "entry_ids": {"type": "array", "minItems": 1, "maxItems": 10,
                      "items": string(200, minimum=1)},
        "game_key": string(200, minimum=1),
        "audience": string(20, ("gm", "public", "characters")),
        "character_ids": {"type": "array", "maxItems": 10,
                          "items": string(200, minimum=1)},
    }, ("book_id", "entry_ids", "audience"))
    tools = [
        AgentTool("read_content", "Read an existing world, book, character, adventure or rule", obj({
            "kind": string(30, ("world", "lorebook", "character_template", "adventure", "rule")), "id": string(minimum=1)}, ("kind", "id")), read),
        AgentTool("edit_world", "Update the name and public setting of your world", obj({"world_id": string(minimum=1),
            "name": string(minimum=1), "description": string(16000), "language": string(16, ("zh-CN", "en", "de", "ja", "ru")),
            "world_setting": string(16000), "starter_scene": string(8192)},
            ("world_id",)), edit_world, mutation=True,
            guard=lambda args, owner: _digest(deps.world_snapshot(args['world_id']) if deps.world_snapshot else world(args["world_id"])),
            preview=lambda args, owner: {"target_name": world(args["world_id"])["name"], "changes": args}),
        AgentTool("write_lorebook_entries", "Add or update reviewed lorebook entries, with explicit public or GM visibility", obj({
            "book_id": string(minimum=1), "entries": {"type": "array", "minItems": 1, "maxItems": 16, "items": entry}},
            ("book_id", "entries")), write_entries, mutation=True, version=2,
            prepare=prepare_entries, execute=execute_entries if deps.write_entries_with_recovery is not None else None,
            guard=lambda args, owner: _digest(book_entries(args["book_id"])),
            preview=lambda args, owner: {"target_name": str((deps.list_book_entries(args["book_id"]).get("book") or {}).get("name") or args["book_id"]),
                "entries": args["entries"]}),
        AgentTool("set_lorebook_entry_visibility", "Change existing lorebook entry visibility without rewriting its authored fields",
            visibility_schema, set_entry_visibility, mutation=True, domain='lorebook', effect='disclosure',
            version=3, prepare=visibility.prepare if visibility is not None else prepare_entry_visibility,
            execute=visibility.execute if visibility is not None else None, resumable=visibility is not None,
            guard=lambda args, owner: _digest(book_entries(args["book_id"])),
            preview=lambda args, owner: {"target_name": str((deps.list_book_entries(args["book_id"]).get("book") or {}).get("name") or args["book_id"]),
                "entry_ids": args["entry_ids"], "audience": args["audience"],
                "character_ids": args.get("character_ids", [])}),
        *([AgentTool("set_lorebook_visibility", "Change visibility for every existing entry in a lorebook",
            obj({"book_id": string(minimum=1), "audience": string(20, ("gm", "public", "characters")),
                 "game_key": string(200, minimum=1), "character_ids": {"type": "array", "maxItems": 10,
                                 "items": string(200, minimum=1)}}, ("book_id", "audience")),
            set_book_visibility, mutation=True, domain="lorebook",
            effect="disclosure" if visibility is not None else "external", version=3,
            prepare=visibility.prepare if visibility is not None else None,
            execute=visibility.execute if visibility is not None else None, resumable=visibility is not None,
            guard=book_visibility_guard,
            preview=book_visibility_preview,
            instructions=("Narrowing to GM-only can execute under a covering Task grant. Audience expansion needs concrete consent. "
                          "For multiple books use set_lorebooks_visibility once with the complete explicit selection. "
                          "Changes only the audience of existing entries, never their text or world runtime facts. "
                          "Resolve the user's selection into explicit canonical book IDs first, then use one grouped command "
                          "for the selection. Ask for the target audience if it is unclear. Each book supports at most "
                          "1000 entries; larger books need explicit entry batches. The preview shows affected counts "
                          "and games; writes are transactional per book and have separate batch undo records."))]
            if deps.write_book_visibility_with_recovery is not None else []),
        *([AgentTool('set_lorebooks_visibility', 'Plan and apply visibility changes across multiple selected lorebooks',
            obj({'book_ids': {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': string(200, minimum=1)},
                 'audience': string(20, ('gm', 'public', 'characters')), 'game_key': string(200, minimum=1),
                 'character_ids': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'items': string(200, minimum=1)}},
                ('book_ids', 'audience')), set_book_visibility, mutation=True, domain='lorebook', effect='disclosure',
            version=1, prepare=visibility.prepare, execute=visibility.execute, resumable=True,
            instructions=('For a request covering multiple or all lorebooks, read the live lorebook catalog to resolve '
                          'the complete explicit book_ids, then call this grouped command once. Do not split the request '
                          'into separate book approvals. GM-only narrowing runs automatically with covering Task access; '
                          'audience expansion presents one complete plan for approval, then all books execute. '
                          'Only visible_to changes. Failures and cancellation retain completed receipts and remaining '
                          'targets for explicit resume; inspect the structured execution report.'))] if visibility is not None else []),
        AgentTool("edit_character_card", "Edit a reusable character's public narrative fields", obj({"card_id": string(minimum=1),
            "character_name": string(minimum=1), "background": string(16000), "race": string(),
            "class": string()}, ("card_id", "character_name")), edit_card, mutation=True,
            guard=lambda args, owner: _digest(deps.card_snapshot(args['card_id']) if deps.card_snapshot else card(args["card_id"])),
            preview=lambda args, owner: {"target_name": card(args["card_id"])["character_name"], "changes": args}),
    ]
    return tuple(tools)
