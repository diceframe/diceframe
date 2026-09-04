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


logger = logging.getLogger("trpg")

CURRENT_INSTANCE_SCHEMA_VERSION = 7


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
    economy = payload.get("economy")
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
