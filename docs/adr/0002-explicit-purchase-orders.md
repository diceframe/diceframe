# ADR 0002: Explicit purchase orders

- Status: Accepted
- Date: 2026-09-04

## Context

The pre-202 workflow allowed the narrative pipeline to infer a purchase price
from arbitrary currency mentions, bind that price to a model-emitted item, and
repair the result through intent/evidence/quote layers. In multiplayer turns
this could bind a quest reward or another character's price to the wrong item.

## Decision

Player language creates a persisted `purchase_request` only. A request has an
actor, raw action, item hint, run and round; it has no price and cannot change
currency or inventory.

Only an authenticated GM can create a `purchase_order` with an explicit payer,
recipient, item list, amount and delivery mode. The order creates one linked
authoritative payer proposal. The payer confirmation is serialized by the
aggregate lock and settles the proposal atomically with the ledger:

- `immediate`: debit and deliver in one settlement;
- `deferred`: debit now, mark `paid`, and deliver later through the GM-only
  order delivery endpoint. Delivery is idempotent and never debits again.

Requests remain open when a proposal is declined, rejected for insufficient
funds, or otherwise invalid, so the GM can issue a corrected order without
losing the player's original action.

Narrative amounts, adjacency matching, evidence ladders and AI intent repair
are retired. They are not allowed to create a
chargeable proposal or grant a purchase item in the normal round path.

## Compatibility

This is an intentional breaking change for the single-user preview build.
Migration to schema 6 drops retired `purchase_quotes`, `merchant_offers`,
`clarifications`, `evidence`, and top-level `pending_payments`. Old preview
saves are not guaranteed to retain those obsolete pending decisions; current
authoritative balances and ordinary character state remain intact.

## Consequences

The common flow is deterministic and actor-scoped, including concurrent
multiplayer purchases. AI mistakes no longer reject or lose a player's buying
opportunity: the request stays visible to the GM. Dice, D&D/COC rules and
non-economic state updates remain on their existing owners and are unaffected.
