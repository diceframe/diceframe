# ADR 0002: Payer-confirmed purchase offers

- Status: Accepted
- Date: 2026-09-04
- Supersedes: the unpublished "Explicit purchase orders" draft of the same number

## Context

The pre-202 workflow allowed the narrative pipeline to infer a purchase price
from arbitrary currency mentions, bind that price to a model-emitted item, and
repair the result through intent/evidence/quote layers. In multiplayer turns
this could bind a quest reward or another character's price to the wrong item.
Those retired layers (evidence, matcher, purchase_quotes, merchant_offers,
clarifications) must stay dead.

A later iteration split a purchase into a `purchase_request` (created when the
player first asked) plus a GM-authored `purchase_order` linked 1:1 to a payer
proposal. Keeping the three representations consistent required dedicated
sync functions (`sync_purchase_order_status`,
`mark_purchase_request_open`, `reopen_purchase_order_request`) and a
source whitelist inside `queue_proposal`. The duplication was the defect: two
status machines for one charge, and a gate that turned the GM into a manual
quoting clerk whose narration could never produce an offer by itself.

## Decision

There is one entity and one state machine. A chargeable purchase is a single
economy proposal with `approval_policy="payer"` and `status="pending"`,
flowing `pending → committed | declined | cancelled | rejected`.

`queue_purchase_offer()` is the sole entry point for chargeable purchase
proposals. It hard-codes payer approval and pending status; its `source`
argument ("gm_manual" from the GM composer, "table_offer" from AI extraction)
is provenance for audit only and never selects behavior. Authorization is
uniform for every source: only `resolve_proposal(accepted=True)` with
`actor_uid == payer_uid` may change currency, inventory, or the ledger. The GM
may cancel but never confirm on the payer's behalf.

Prices come from the table, not from data. The GM narrates prices as usual and
the AI planner may only report numbers a human actually stated this round
(`price_source`: `player_stated`, `gm_narrated`, `none`). When nobody has
stated a price, the planner reports `none` and no proposal is created —
failing safe on the side of not charging. A hallucinated price costs the payer
one rejection click, never a corrupted save.

Deferred delivery is removed. Proposals carry no `delivery_mode` /
`delivery_condition`, and the order-delivery endpoint no longer exists. A
narrative such as "the smith will deliver tomorrow" is handled by the GM
granting the item later through the normal reward path.

The unconfirmed-grant gate survives in proposal scope:
`filter_unconfirmed_purchase_grants()` strips model-emitted items whose names
match a pending chargeable proposal's rewards, so items cannot bypass a
payment that is awaiting confirmation. Known accepted limitation: if no price
has ever been stated (therefore no proposal exists), the model may grant an
item unchallenged in the same round — that is the GM's narration, not a bug.

## Compatibility

This is an intentional breaking change for the single-user preview build.
Migration to schema 7 drops `purchase_requests` and `purchase_orders`.
Open preview purchase requests are not guaranteed to survive; authoritative
balances, items and the transaction ledger are unaffected. Schema 6 already
dropped `purchase_quotes`, `merchant_offers`, `clarifications`, `evidence`,
and top-level `pending_payments`.

## Consequences

The GM narrates; the payer confirms; nothing else can charge. The proposal
card dialog is the single confirmation surface, identical for GM-composed and
AI-extracted offers. Multiplayer misbinding degrades safely: a wrongly
attributed actor simply sees a dialog they can decline. Dice, D&D/COC rules
and non-economic state updates remain on their existing owners and are
unaffected.
