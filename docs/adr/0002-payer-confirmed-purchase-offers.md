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
payment that is awaiting confirmation.

## Amendment (2026-09-05): same-round price pass closes the priceless grant hole

The originally accepted limitation — "if no price has ever been stated, the
model may grant an item unchallenged in the same round; that is the GM's
narration, not a bug" — is superseded. The incident that forced the change:
a player asked to buy five potions, the planner (running before narration)
correctly reported `price_source=none`, and the GM then narrated the deal and
handed the items over through a `LOOT` tag in the same round, bypassing
payment entirely.

The fix keeps every decision in this ADR intact — one entity, one state
machine, prices only from verbatim human-stated numbers, no resurrection of
the dropped `purchase_request` / `purchase_order` entities:

- The planner's priceless purchase intents (`price_source=none` or missing
  amount) are no longer silently dropped. They are held on the live instance
  as in-memory round state (`round_unpriced_purchase_intents`), never
  persisted, never carrying an amount.
- After the narration is generated, a single cheap planner pass
  (`price_unpriced_purchase_intents`) re-reads this round's narration and may
  emit an offer per intent whose price a human verbatim stated in that text
  (`price_source=gm_narrated`). Such offers flow through the same
  `queue_purchase_offer` entry point and become ordinary payer-confirmed
  proposals — the payment dialog now appears in the same round the deal was
  narrated.
- Intents that stay priceless after the pass are passed to
  `filter_unconfirmed_purchase_grants()`, which strips same-round model
  grants of the matching items, so a purchase can no longer be delivered
  through narrative LOOT before any price exists. The player simply states
  the purchase again once the price is on the table, and the normal planner
  path prices it from `recent_narration`.

No persisted schema changes: the intent list lives and dies with the round's
prepared checks, and instance schema stays at 7.

## Amendment (2026-09-05b): the same-round price pass is retired

The same-day amendment above added a second planner pass
(`price_unpriced_purchase_intents`) that re-read the narration after the LLM
call and created offers for intents whose price a human had narrated. It did
close the same-round dialog gap, but it duplicated the planner's pricing
responsibility in a second LLM call on every purchase round and added a
compensation stage to an already deep pipeline. It is superseded:

- The planner keeps single-pass pricing only: offers come from
  `economy_actions` with `price_source=player_stated` / `gm_narrated`,
  priced from the current context (including `recent_narration`).
- Unpriced intents (`price_source=none`) remain in-memory round state and
  still feed `filter_unconfirmed_purchase_grants()`, so a purchase whose
  price nobody has stated can never be delivered through narrative LOOT.
  What is retired is only the same-round offer creation.
- A price the GM narrates in round N becomes quotable by the planner in
  round N+1 through `recent_narration`. The GM who wants the dialog
  immediately prices the offer through the manual purchase composer
  (`source="gm_manual"`), which was already the uniform confirmation path.
- The price-pass prompts and `price_unpriced_purchase_intents` are removed.

The fail-safe direction is unchanged: no stated price means no charge and no
free item delivery — the cost of retiring the pass is at most a one-round
delay of the payment dialog.

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
