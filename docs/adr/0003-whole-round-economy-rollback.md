# ADR 0003: Whole-round economy rollback

- Status: Accepted
- Date: 2026-09-05

## Context

Rollback (GM "撤回上一轮") and historical swipe both erase a round and
replay from its snapshot. Because a purchase offer can settle in a later
round than the one that created it, the previous model grew two selective
reversal mechanisms:

- `reverse_round_economy` reversed only settlements whose transaction round
  or proposal origin equalled the target round. A settlement from any later
  round survived a historical rewrite even though its narrative era was
  being erased.
- `_remove_reward_delta` recovered granted items by diffing pre/post
  inventory snapshots and removing "the entries this transaction added".
  Stacked, renamed, merged or re-granted items made that attribution
  unprovable; the code itself documented that absolute before-images are
  invalid for selective single-transaction reversal.

Two consistent end states were possible: (a) withdraw every settlement at
or after the rolled-back round, or (b) make committed settlements immutable
and exclude them from replay. This product chose (a): rewinding the story
must also rewind the table's economy, otherwise players keep charges for
fiction that no longer happened.

## Decision

Rolling back to round N erases the whole economic era from N onward:

- `reverse_round_economy(instance, N)` invalidates every proposal whose
  origin round is >= N (committed -> reversed, pending -> superseded,
  idempotency records released) and reverses every committed transaction
  whose settlement round is >= N. An offer whose origin round survives but
  whose settlement does not is reopened as pending, so the payer decides
  again on the replayed branch.
- Effect groups and memory-outbox deliveries with round >= N follow the
  same era cut; delivered memory still goes through the durable reversal
  request defined by ADR 0001 (per-delivery matching is unchanged).
- Item recovery never diffs inventories. The swipe path restores the whole
  player snapshot, and `reconcile_rollback_snapshot` projects each reversed
  settlement's absolute before-image (balances and inventory rows) in
  strict reverse commit order. Inside one erased era that replay is exact,
  which is why the selective-diff machinery (`_remove_reward_delta`,
  `_undo_transaction_rewards`, `_undo_live_transaction_delta`) is deleted.
- A historical swipe to round N also branch-cuts the log: entries after N
  belong to the discarded branch and are dropped, and the game continues
  from round N. Keeping their narratives while withdrawing their economy
  would be incoherent.

## Consequences

- Rewinding to a middle round discards every later round (narrative and
  economy) instead of leaving them attached to a restored state.
- A late purchase whose offer predates the rollback stays pending again;
  the payer is not charged for fiction that was erased.
- Rollback correctness no longer depends on proving which inventory rows a
  specific transaction added. The remaining requirement is that each
  transaction records its pre-settlement state, which settlement already
  guarantees.
- Committed history before the rollback round stays authoritative and is
  never rewritten.
