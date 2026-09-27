"""Unified migrations for persisted game-instance projections.

Domain adapters stay in ``src.compat``; services call this module instead of
knowing which compatibility steps are needed for a loaded instance.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import logging
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from src.compat.dnd2024_adventure_bindings import apply_unreleased_adventure_binding_migration
from src.engine.currency.migration import scale_game_state_payload_for_base_unit_change
from src.engine.module_state import ModuleStateError
from src.engine.modules.lorebook_runtime import fresh as fresh_lorebook_runtime, normalize_timers
from src.engine.player_control import CONTROL_KEY, normalize_control
from src.engine.player_control import normalize_away_control_policy
from src.engine.world_state import fresh_world_state


logger = logging.getLogger("trpg")

CURRENT_INSTANCE_SCHEMA_VERSION = 28

# 内置 freeform_coc 在 Currency Model V2 中把 base_unit 从「美元」升级为
# 「美分」（1 amount = 1 美分），存量 CoC 存档的所有 canonical 金额必须 ×100
# 才能保持同样的现实金额。这是唯一做 base_unit 语义迁移的内置规则；其它规则
# （含从 CoC 复制的自定义规则）的 legacy 单位语义不变，数据不动。
_BASE_UNIT_MIGRATION_RULES = {"freeform_coc"}
_BASE_UNIT_MIGRATION_FACTOR = 100


def _legacy_run_id(payload: Mapping[str, Any]) -> str:
    """Return a deterministic run identity for a pre-versioned save."""

    game_key = "|".join(str(part) for part in (payload.get("game_key") or []))
    started_at = str(payload.get("started_at") or "legacy")
    return f"run_{uuid5(NAMESPACE_URL, f'diceframe:{game_key}:{started_at}').hex}"


def _migrate_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or _legacy_run_id(payload))
    payload["run_id"] = run_id
    # Existing memory rows are keyed by the tuple string. Preserve access to
    # those rows for the current run; reset/restart rotates to a namespaced key.
    payload.setdefault("memory_namespace", str(tuple(payload.get("game_key") or ())))
    for uid, player in (payload.get("players") or {}).items():
        if not isinstance(player, dict):
            continue
        sheet = player.get("character_sheet")
        if not isinstance(sheet, dict):
            continue
        currency_value = sheet.get("currency")
        currency: dict[str, Any] = (
            dict(currency_value) if isinstance(currency_value, dict) else {}
        )
        raw_amount = currency.get("amount", sheet.get("gold", 0))
        try:
            amount = max(0, int(raw_amount or 0))
        except (TypeError, ValueError):
            amount = max(0, int(sheet.get("gold", 0) or 0))
        if currency.get("amount") is not None and sheet.get("gold") is not None:
            try:
                mismatch = int(currency["amount"]) != int(sheet["gold"])
            except (TypeError, ValueError):
                mismatch = True
            if mismatch:
                logger.warning(
                    "迁移存档货币字段不一致，采用 currency.amount: uid=%s",
                    uid,
                )
        sheet["currency"] = {**currency, "amount": amount}
        sheet["gold"] = amount
    # Retired preview payment entries are intentionally not guessed into the
    # new order model.  Their item/price attribution was not authoritative;
    # schema 6 drops them instead of risking an unexpected charge.
    payload.setdefault("economy", {
        "schema_version": 1,
        "run_id": run_id,
        "next_sequence": 1,
        "proposals": [],
        "transactions": [],
        "idempotency_records": {},
        "effect_groups": [],
        "outcomes": [],
        "decision_revision": 0,
    })
    payload["instance_schema_version"] = 2
    return payload


def _migrate_v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    """Add the durable external-effect outbox to the economy aggregate."""

    economy = payload.get("economy")
    if not isinstance(economy, dict):
        economy = {}
        payload["economy"] = economy
    economy.setdefault("schema_version", 2)
    economy["schema_version"] = max(2, int(economy.get("schema_version", 1) or 1))
    economy.setdefault("external_effects_outbox", [])
    payload["instance_schema_version"] = 3
    return payload


def _migrate_v3_to_v4(payload: dict[str, Any]) -> dict[str, Any]:
    """Advance the historical schema; retired quote data is discarded later."""
    payload["instance_schema_version"] = 4
    return payload


def _migrate_v8_to_v9(payload: dict[str, Any]) -> dict[str, Any]:
    payload.setdefault("manual_roll_requests", [])
    payload["instance_schema_version"] = 9
    return payload


def _migrate_v9_to_v10(payload: dict[str, Any]) -> dict[str, Any]:
    binding = payload.get("adventure_binding")
    payload["play_mode"] = "adventure" if isinstance(binding, dict) and binding.get("adventure_id") else "free"
    payload["instance_schema_version"] = 10
    return payload


def _migrate_v10_to_v11(payload: dict[str, Any]) -> dict[str, Any]:
    """Add explicit purpose metadata to manual roll requests.

    Existing requests were record-only rolls, so ``free`` is the only safe
    default. No old result is reinterpreted as a check.
    """
    requests = payload.get("manual_roll_requests")
    if isinstance(requests, list):
        for request in requests:
            if not isinstance(request, dict):
                continue
            request.setdefault("purpose", "free")
            request.setdefault("target", None)
            request.setdefault("comparison", "at_least")
    payload["instance_schema_version"] = 11
    return payload


def _migrate_v4_to_v5(payload: dict[str, Any]) -> dict[str, Any]:
    """Add explicit purchase-request/order collections.

    Existing proposals and purchase quotes remain untouched.  They are read
    through the compatibility path and are not silently converted into new
    orders because their item/price attribution may not be provable.
    """

    economy = payload.get("economy")
    if not isinstance(economy, dict):
        economy = {}
        payload["economy"] = economy
    economy.setdefault("purchase_requests", [])
    economy.setdefault("purchase_orders", [])
    payload["instance_schema_version"] = 5
    return payload


def _migrate_v5_to_v6(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove the retired narration-priced purchase state."""
    economy = payload.get("economy")
    if isinstance(economy, dict):
        for key in ("purchase_quotes", "merchant_offers", "clarifications", "evidence"):
            economy.pop(key, None)
    payload.pop("pending_payments", None)
    payload["instance_schema_version"] = 6
    return payload


def _migrate_v6_to_v7(payload: dict[str, Any]) -> dict[str, Any]:
    """Collapse the purchase order/request pair into payer-confirmed proposals.

    Open purchase requests and pending orders are preview-build scratch state;
    they are dropped rather than guessed into proposals.  Authoritative
    balances, items and the transaction ledger are untouched.
    """
    economy = payload.get("economy")
    if isinstance(economy, dict):
        economy.pop("purchase_requests", None)
        economy.pop("purchase_orders", None)
    payload["instance_schema_version"] = 7
    return payload


def _migrate_v7_to_v8(payload: dict[str, Any]) -> dict[str, Any]:
    """Retire the unused transfer/fee/all_contributors proposal surface.

    The PAY/TEAM_PAY tag contract was retired with schema 6, so these kinds
    and the shared-cost approval policy have no live creation path. Pending
    leftovers from pre-retirement saves never charged anyone; they are
    superseded rather than guessed into a kept kind. A pending effect group
    containing a superseded member can never reach all-committed, so it is
    discarded -- its other members keep blocking through their own pending
    proposals. Committed ledger history is preserved untouched.
    """
    economy = payload.get("economy")
    if isinstance(economy, dict):
        retired_kinds = {"transfer", "fee"}
        superseded_ids: set[str] = set()
        for proposal in economy.get("proposals", []) or []:
            if not isinstance(proposal, dict) or proposal.get("status") != "pending":
                continue
            if (
                str(proposal.get("kind") or "") in retired_kinds
                or str(proposal.get("approval_policy") or "") == "all_contributors"
            ):
                proposal["status"] = "superseded"
                proposal["resolution_code"] = "RETIRED_LEGACY_PROPOSAL"
                proposal_id = str(proposal.get("id") or "")
                if proposal_id:
                    superseded_ids.add(proposal_id)
        if superseded_ids:
            for group in economy.get("effect_groups", []) or []:
                if not isinstance(group, dict) or group.get("status") != "pending":
                    continue
                if superseded_ids.intersection(
                    str(item) for item in group.get("proposal_ids", []) or []
                ):
                    group["status"] = "discarded"
                    group.pop("effects", None)
    payload["instance_schema_version"] = 8
    return payload


def _migrate_v11_to_v12(payload: dict[str, Any]) -> dict[str, Any]:
    """Currency Model V2: scale canonical amounts for base-unit semantic changes.

    Only the built-in ``freeform_coc`` rule changed base-unit semantics in this
    release (1 amount = 1 美元 → 1 amount = 1 美分), so only saves bound to that
    rule are scaled (×100), exactly once, gated by the schema version.  Saves
    without a resolvable ``rule_id`` are left untouched: migration correctness
    beats completeness and no amount is reinterpreted by guessing.  Legacy and
    custom rules keep their rate=1 semantics, so their data never moves.
    """
    rule_id = str(payload.get("rule_id") or "").strip()
    if rule_id in _BASE_UNIT_MIGRATION_RULES:
        scale_game_state_payload_for_base_unit_change(
            payload, _BASE_UNIT_MIGRATION_FACTOR,
        )
    payload["instance_schema_version"] = 12
    return payload


def _migrate_v12_to_v13(payload: dict[str, Any]) -> dict[str, Any]:
    """World state core (Issue #284): every save gets an explicit world container.

    Older saves have no world truth at all.  They are not guessed into facts:
    the migration only materializes the empty container (day 1, 00:00, no facts,
    no scheduled events) and a valid existing payload is left untouched, so the
    step is idempotent.
    """

    raw = payload.get("world_state")
    if not isinstance(raw, dict) or not raw:
        payload["world_state"] = fresh_world_state()
    payload["instance_schema_version"] = 13
    return payload


def _migrate_v13_to_v14(payload: dict[str, Any]) -> dict[str, Any]:
    """Player control contract (AI teammate PR1): every seat names its controller.

    Older saves cannot say whether a seat was AI-hosted — nothing was AI-hosted
    before this contract existed — so the only answer that does not guess is
    ``human``, which is exactly the pre-contract behaviour: an upgraded table
    never suddenly finds a character taken over by the server.  A seat whose
    stored record already normalizes to itself is left untouched, so the step is
    idempotent.
    """

    players = payload.get("players")
    if isinstance(players, dict):
        for player in players.values():
            if not isinstance(player, dict):
                continue
            stored = player.get(CONTROL_KEY)
            record = normalize_control(stored)
            if stored != record:
                player[CONTROL_KEY] = record
    payload["instance_schema_version"] = 14
    return payload


def _migrate_v14_to_v15(payload: dict[str, Any]) -> dict[str, Any]:
    """Room away policy (AI teammate PR5): the table says what "away" means.

    A save written before this setting existed had exactly one behaviour: going
    away changed presence only and never handed the character to the AI.  So the
    only answer that does not invent a controller is ``pause``, and an upgraded
    table never finds a character silently taken over by the server.  A stored
    value that already normalizes to itself is left untouched, so the step is
    idempotent.
    """

    stored = payload.get("away_control_policy")
    policy = normalize_away_control_policy(stored)
    if stored != policy:
        payload["away_control_policy"] = policy
    payload["instance_schema_version"] = 15
    return payload


def _migrate_v15_to_v16(payload: dict[str, Any]) -> dict[str, Any]:
    """WorldState v2 containers (World Runtime v2 WR-02).

    v1 world payloads gain the empty ``entities`` / ``relations`` /
    ``processes`` containers and move to world ``schema_version = 2``; facts,
    clock, and scheduled events are preserved verbatim and nothing is guessed
    into the new containers.  A payload that is already v2 (or carries an
    unknown world schema, which the write path rejects) is left untouched, so
    the step is idempotent.
    """

    raw = payload.get("world_state")
    if not isinstance(raw, dict) or not raw:
        payload["world_state"] = fresh_world_state()
    elif raw.get("schema_version") == 1:
        raw["schema_version"] = 2
        for key in ("entities", "relations", "processes"):
            if key not in raw:
                raw[key] = {}
    payload["instance_schema_version"] = 16
    return payload


def _migrate_v16_to_v17(payload: dict[str, Any]) -> dict[str, Any]:
    """Move Lorebook runtime timers into a versioned module slot (Track R0).

    Legacy single-counter and independent-counter timers share the domain's
    normalizer. Existing slots, including unknown module schemas, are kept
    verbatim; removing the old top-level key makes this step idempotent.
    """

    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    legacy = payload.pop("lorebook_timed_state", None)
    if not isinstance(modules.get("lorebook_runtime"), dict):
        slot = fresh_lorebook_runtime()
        slot["timers"] = normalize_timers(legacy)
        modules["lorebook_runtime"] = slot
    payload["modules"] = modules
    payload["instance_schema_version"] = 17
    return payload


def _migrate_v17_to_v18(payload: dict[str, Any]) -> dict[str, Any]:
    """Move the room away policy into ``modules.player_control`` (Track R1).

    Normalize the legacy setting with the v14-to-v15 rule: missing or invalid
    values stay ``pause``, so no seat is silently handed to the AI. Existing
    slots are kept verbatim, including unknown module schemas. Idempotent.
    """

    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    legacy = payload.pop("away_control_policy", None)
    if not isinstance(modules.get("player_control"), dict):
        modules["player_control"] = {
            "schema_version": 1,
            "away_control_policy": normalize_away_control_policy(legacy),
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 18
    return payload


def _migrate_v18_to_v19(payload: dict[str, Any]) -> dict[str, Any]:
    """Move the economy ledger verbatim into ``modules.economy.state`` (R2).

    The inner ledger schema and all records stay untouched. Existing slots,
    including unknown schemas, take precedence; the step is idempotent.
    """

    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    legacy = payload.pop("economy", None)
    if not isinstance(modules.get("economy"), dict):
        modules["economy"] = {
            "schema_version": 1,
            "state": legacy if isinstance(legacy, dict) else {},
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 19
    return payload


def _migrate_v19_to_v20(payload: dict[str, Any]) -> dict[str, Any]:
    """Move combat state into its module slot (R3), preserving existing slots.

    The guide says:
    "Both the live payload and the per-round snapshots are moved verbatim."
    Departure: legacy snapshots retain the old codec's string-key and
    dict-value filtering ONLY during this conversion. Current
    v20 module contents stay opaque; existing slots, even unknown/empty ones,
    win over stale legacy fields. Idempotent.
    """

    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    current = payload.pop("combat_extension", None)
    snapshots = payload.pop("combat_extension_round_snapshots", None)
    if not isinstance(modules.get("combat_extension"), dict):
        modules["combat_extension"] = {
            "schema_version": 1,
            "current": current if isinstance(current, dict) else {},
            "round_snapshots": (
                {str(key): dict(value) for key, value in snapshots.items() if isinstance(value, dict)}
                if isinstance(snapshots, dict) else {}
            ),
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 20
    return payload


def _migrate_v20_to_v21(payload: dict[str, Any]) -> dict[str, Any]:
    """Move the narrative round into progression (R5-b), once at this boundary.

    Existing dict slots, even empty/unknown ones, win over the legacy counter.
    Historical migrations and log/snapshot round keys retain their semantics.
    """
    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    legacy = payload.pop("round_number", 0)
    if not isinstance(modules.get("progression"), dict):
        value = legacy if isinstance(legacy, int) and not isinstance(legacy, bool) and legacy >= 0 else 0
        modules["progression"] = {"schema_version": 1, "mode": "narrative_round", "round": value}
    payload["modules"] = modules
    payload["instance_schema_version"] = 21
    return payload


def _migrate_v21_to_v22(payload: dict[str, Any]) -> dict[str, Any]:
    """Move private channels into their slot; existing slots take precedence."""
    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    private_log = payload.pop("private_log", None)
    table_talk = payload.pop("table_talk", None)
    if not isinstance(modules.get("private_channels"), dict):
        modules["private_channels"] = {
            "schema_version": 1,
            "private_log": private_log if isinstance(private_log, dict) else {},
            "table_talk": table_talk if isinstance(table_talk, list) else [],
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 22
    return payload


def _migrate_v22_to_v23(payload: dict[str, Any]) -> dict[str, Any]:
    """Move media references into their slot without overwriting existing slots."""
    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    scene_image = payload.pop("scene_image", None)
    map_background = payload.pop("map_background", None)
    if not isinstance(modules.get("media"), dict):
        modules["media"] = {
            "schema_version": 1,
            "scene_image": scene_image if isinstance(scene_image, dict) else {},
            "map_background": map_background if isinstance(map_background, dict) else {},
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 23
    return payload


def _migrate_v23_to_v24(payload: dict[str, Any]) -> dict[str, Any]:
    """Move health state into its slot; leave event retention to serialization."""
    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    health_events = payload.pop("health_events", None)
    health_status = payload.pop("health_status", None)
    if not isinstance(modules.get("health"), dict):
        modules["health"] = {
            "schema_version": 1,
            "health_events": health_events if isinstance(health_events, list) else [],
            "health_status": health_status if isinstance(health_status, dict) else {},
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 24
    return payload


def _migrate_v24_to_v25(payload: dict[str, Any]) -> dict[str, Any]:
    """Move narrative notes without interpreting the legacy game-time string."""
    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    summary = payload.pop("summary", None)
    key_facts = payload.pop("key_facts", None)
    confirmed_items = payload.pop("confirmed_items", None)
    game_time = payload.pop("game_time", None)
    if not isinstance(modules.get("narrative_notes"), dict):
        modules["narrative_notes"] = {
            "schema_version": 1,
            "summary": summary if isinstance(summary, dict) else {},
            "key_facts": key_facts if isinstance(key_facts, list) else [],
            "confirmed_items": confirmed_items if isinstance(confirmed_items, list) else [],
            "game_time": game_time if isinstance(game_time, str) else "",
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 25
    return payload


def _migrate_v25_to_v26(payload: dict[str, Any]) -> dict[str, Any]:
    """Move round presentation into its slot without changing existing slots."""
    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    gm_directives = payload.pop("gm_directives", None)
    quick_actions = payload.pop("quick_actions", None)
    last_state_update = payload.pop("last_state_update", None)
    last_token_budget_bump = payload.pop("last_token_budget_bump", None)
    pending_combat_results = payload.pop("pending_combat_results", None)
    if not isinstance(modules.get("round_presentation"), dict):
        modules["round_presentation"] = {
            "schema_version": 1,
            "gm_directives": gm_directives if isinstance(gm_directives, list) else [],
            "quick_actions": quick_actions if isinstance(quick_actions, list) else [],
            "last_state_update": last_state_update if isinstance(last_state_update, dict) else None,
            "last_token_budget_bump": last_token_budget_bump if isinstance(last_token_budget_bump, dict) else None,
            "pending_combat_results": pending_combat_results if isinstance(pending_combat_results, list) else [],
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 26
    return payload


def _migrate_v26_to_v27(payload: dict[str, Any]) -> dict[str, Any]:
    """Move world reports into their slot; existing slots take precedence."""
    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    last_overreach = payload.pop("last_overreach", None)
    last_world_legality = payload.pop("last_world_legality", None)
    last_world_events = payload.pop("last_world_events", None)
    if not isinstance(modules.get("world_reports"), dict):
        modules["world_reports"] = {
            "schema_version": 1,
            "last_overreach": last_overreach if isinstance(last_overreach, list) else [],
            "last_world_legality": last_world_legality if isinstance(last_world_legality, list) else [],
            "last_world_events": last_world_events if isinstance(last_world_events, list) else [],
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 27
    return payload


def _migrate_v27_to_v28(payload: dict[str, Any]) -> dict[str, Any]:
    """Move table settings as-is; currency caps were already converted in v12."""
    modules = payload.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    defaults: dict[str, Any] = {
        "difficulty": "标准",
        "narrative_perspective": "auto",
        "gm_style_override": None,
        "solo_mode": False,
        "seed_code": "",
        "entry_point": "web",
        "luck_timeout_seconds": 60,
        "economy_reward_policy": {},
    }
    values = {key: payload.pop(key, None) for key in defaults}
    try:
        timeout = int(values["luck_timeout_seconds"] or 0) if values["luck_timeout_seconds"] is not None else 60
    except (TypeError, ValueError, OverflowError):
        timeout = 60
    if not isinstance(modules.get("table_settings"), dict):
        modules["table_settings"] = {
            "schema_version": 1,
            "difficulty": values["difficulty"] if isinstance(values["difficulty"], str) else defaults["difficulty"],
            "narrative_perspective": values["narrative_perspective"] if isinstance(values["narrative_perspective"], str) else defaults["narrative_perspective"],
            "gm_style_override": values["gm_style_override"] if isinstance(values["gm_style_override"], dict) else None,
            "solo_mode": values["solo_mode"] if isinstance(values["solo_mode"], bool) else False,
            "seed_code": values["seed_code"] if isinstance(values["seed_code"], str) else defaults["seed_code"],
            "entry_point": values["entry_point"] if isinstance(values["entry_point"], str) else defaults["entry_point"],
            "luck_timeout_seconds": timeout,
            "economy_reward_policy": values["economy_reward_policy"] if isinstance(values["economy_reward_policy"], dict) else {},
        }
    payload["modules"] = modules
    payload["instance_schema_version"] = 28
    return payload


def migrate_game_state_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    """Apply sequential, idempotent migrations to one persisted save payload."""

    payload = deepcopy(dict(data))
    version = int(payload.get("instance_schema_version", 1) or 1)
    if version < 1 or version > CURRENT_INSTANCE_SCHEMA_VERSION:
        raise ValueError(f"unsupported game instance schema version: {version}")
    if version == 1:
        payload = _migrate_v1_to_v2(payload)
        version = 2
    if version == 2:
        payload = _migrate_v2_to_v3(payload)
        version = 3
    if version == 3:
        payload = _migrate_v3_to_v4(payload)
        version = 4
    if version == 4:
        payload = _migrate_v4_to_v5(payload)
        version = 5
    if version == 5:
        payload = _migrate_v5_to_v6(payload)
        version = 6
    if version == 6:
        payload = _migrate_v6_to_v7(payload)
        version = 7
    if version == 7:
        payload = _migrate_v7_to_v8(payload)
        version = 8
    if version == 8:
        payload = _migrate_v8_to_v9(payload)
        version = 9
    if version == 9:
        payload = _migrate_v9_to_v10(payload)
        version = 10
    if version == 10:
        payload = _migrate_v10_to_v11(payload)
        version = 11
    if version == 11:
        payload = _migrate_v11_to_v12(payload)
        version = 12
    if version == 12:
        payload = _migrate_v12_to_v13(payload)
        version = 13
    if version == 13:
        payload = _migrate_v13_to_v14(payload)
        version = 14
    if version == 14:
        payload = _migrate_v14_to_v15(payload)
        version = 15
    if version == 15:
        payload = _migrate_v15_to_v16(payload)
        version = 16
    if version == 16:
        payload = _migrate_v16_to_v17(payload)
        version = 17
    if version == 17:
        payload = _migrate_v17_to_v18(payload)
        version = 18
    if version == 18:
        payload = _migrate_v18_to_v19(payload)
        version = 19
    if version == 19:
        payload = _migrate_v19_to_v20(payload)
        version = 20
    if version == 20:
        payload = _migrate_v20_to_v21(payload)
        version = 21
    if version == 21:
        payload = _migrate_v21_to_v22(payload)
        version = 22
    if version == 22:
        payload = _migrate_v22_to_v23(payload)
        version = 23
    if version == 23:
        payload = _migrate_v23_to_v24(payload)
        version = 24
    if version == 24:
        payload = _migrate_v24_to_v25(payload)
        version = 25
    if version == 25:
        payload = _migrate_v25_to_v26(payload)
        version = 26
    if version == 26:
        payload = _migrate_v26_to_v27(payload)
        version = 27
    if version == 27:
        payload = _migrate_v27_to_v28(payload)
        version = 28
    payload["instance_schema_version"] = version
    return payload


def rebind_imported_game_state_payload(
    data: Mapping[str, Any],
    *,
    game_key: tuple[str, ...],
    run_id: str,
) -> dict[str, Any]:
    """Clone an imported save into an isolated local aggregate identity.

    Import keeps the saved play state and ledger, but it must not share the
    source game's live-run or memory namespace. Embedded economy projections
    are rebound together so pending decisions remain internally consistent.
    """

    payload = migrate_game_state_payload(data)
    payload["game_key"] = list(game_key)
    payload["run_id"] = run_id
    payload["memory_namespace"] = f"{game_key!s}::run:{run_id}"
    modules = payload.get("modules")
    slot = modules.get("economy") if isinstance(modules, dict) else None
    if isinstance(slot, dict) and slot.get("schema_version") != 1:
        # Identity rebinding cannot safely interpret an unknown slot. Plain
        # save/load still preserves it verbatim; importing as a new run rejects.
        raise ModuleStateError(f"unsupported economy module schema: {slot.get('schema_version')!r}")
    economy = slot.get("state") if isinstance(slot, dict) else None
    if isinstance(economy, dict):
        economy["run_id"] = run_id
        # External stores are not bundled with a save export. Pending
        # deliveries still carry their payload and may safely target the new
        # namespace; delivered/reversal receipts refer to source-side memory
        # rows that do not exist in the imported game and must not be rebound.
        economy["external_effects_outbox"] = [
            item
            for item in economy.get("external_effects_outbox", []) or []
            if isinstance(item, dict) and item.get("status") == "pending"
        ]
        for collection_name in (
            "proposals",
            "transactions",
            "effect_groups",
            "external_effects_outbox",
            "outcomes",
        ):
            for item in economy.get(collection_name, []) or []:
                if isinstance(item, dict):
                    item["run_id"] = run_id
    return payload


def _referenced_player_ids(log: list[Any]) -> set[str]:
    referenced: set[str] = set()
    for entry in log or []:
        for action in entry.get("actions", []) or []:
            uid = action.get("user_id")
            if uid and uid != "system":
                referenced.add(uid)
        snapshot = entry.get("pre_state_snapshot", {})
        if isinstance(snapshot, dict):
            referenced.update(uid for uid in snapshot if uid and uid != "system")
    return referenced


def normalize_game_state_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a normalized copy of a persisted game-state payload.

    Ghost-player cleanup intentionally requires historical evidence. Waiting
    rooms and unplayed multiplayer sessions therefore keep every participant.
    """
    payload = migrate_game_state_payload(data)
    players = payload.get("players")
    log = payload.get("log")
    if not isinstance(players, dict) or len(players) <= 1 or not isinstance(log, list) or not log:
        return payload
    referenced = _referenced_player_ids(log)
    if not referenced:
        return payload
    ghost_ids = sorted(uid for uid in players if uid not in referenced)
    if not ghost_ids:
        return payload
    payload["players"] = {
        uid: player for uid, player in players.items() if uid not in ghost_ids
    }
    payload["ready_players"] = [
        uid for uid in payload.get("ready_players", []) if uid not in ghost_ids
    ]
    payload["away_players"] = [
        uid for uid in payload.get("away_players", []) if uid not in ghost_ids
    ]
    payload["action_queue"] = [
        action
        for action in payload.get("action_queue", [])
        if action.get("user_id") not in ghost_ids
    ]
    payload["pending_actions"] = [
        action
        for action in payload.get("pending_actions", [])
        if action.get("user_id") not in ghost_ids
    ]
    logger.warning(
        "加载存档时移除幽灵玩家: game_key=%s, players=%s",
        tuple(payload.get("game_key") or ()),
        ghost_ids,
    )
    return payload


def migrate_instance(instance: Any, *, adventure_expected: dict[str, Any] | None = None) -> bool | None:
    """Apply registered instance migrations, failing closed on incompatibility."""
    if adventure_expected is None or not dict(getattr(instance, "adventure_binding", {}) or {}):
        return False
    return apply_unreleased_adventure_binding_migration(instance, adventure_expected)
