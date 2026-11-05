# ADR 0001: Game run lifecycle and authoritative economy

- Status: Accepted
- Date: 2026-09-01

## Context

`GameInstance` is the authoritative aggregate for one table, but a table may be
reset, restarted, recovered after a process restart, or imported as a new save.
Historically these paths cleared fields individually. Long-term memory was keyed
only by `game_key`, and narrative `GOLD` tags could mutate balances directly.
That made new persisted features easy to omit from reset/restart and allowed a
model response to act as an economic authority.

## Decision

Every playable run has a persisted `run_id` distinct from the stable
`game_key`. Process recovery preserves it; reset and restart rotate it. Delayed
work must carry the run identity and may not write to a different run.

Persisted state is classified as game identity, character state, run state, or
ephemeral process state. Reset and restart construct and validate a candidate
aggregate before replacing the active instance. Ruleset-owned opaque state is
reset through runtime capabilities rather than generic imports.
The transition holds the old aggregate write lock through candidate opening
and replacement. Old requests that were waiting to write resume only after the
swap and fail their registry-identity fence; no post-opening whole-player copy
may overwrite character effects produced by the new opening.

Historical swipe rewrites share the process barrier with normal round
processing. The barrier covers restoring the target snapshot, awaiting the LLM,
applying the candidate branch, and saving the authoritative aggregate. Player
actions are rejected before mutation while the rewrite is active. A runtime-only
authority gate serializes the rewrite with payment, pending-dice, GM, live
character, NPC, advancement, reset, and restart writers. The fixed acquisition
order is authority gate, then process lock, then aggregate state lock. This makes
the rewrite check and acquisition of mutation authority one atomic boundary.

Long-term memory uses a persisted `memory_namespace`. Existing saves retain the
legacy namespace on migration; a new run receives a new namespace, so old
memory is unreachable without requiring destructive deletion first.

Economy changes use server-side proposals and committed transactions. LLM,
worldbook, adventure prose, and locale are proposal sources, never balance
authorities. A transaction is permission-checked, idempotent, atomic with its
item effects, persisted, and auditable. `currency.amount` is the canonical
balance; `gold` remains a compatibility projection during migration.

An economic proposal is a commit barrier for narrative effects emitted by the
same model response. Those effects remain persisted but unapplied until the
proposal commits. If one response contains multiple proposals, they share an
all-or-nothing effect group: all must commit, and any terminal rejection
discards the group. Settlement outcomes enter trusted model context and an
economy decision revision invalidates model responses that were already in
flight when a player decided. A new run always starts with an empty proposal,
transaction, outcome, effect-group, and revision state.

Any unresolved current-run proposal, effect group, or external-effect delivery
blocks every narrative progression entry point before it records an action.
Cross-store memory effects use a durable outbox: game state and the outbox are
saved first, memory delivery is idempotent under a stable delivery identity,
and an unrecorded delivery receipt is retried during recovery or before the
next progression attempt. The memory store journals the verifiable before and
after state of a transaction-associated delivery. Swipe or rollback changes a
delivered record to a durable reversal request; reversal restores only rows
that still match that delivery's result, does not overwrite newer facts, and
is itself idempotent and crash-recoverable. Pending reversals remain part of
the narrative barrier. A transaction-associated scene-image prompt is removed
from staged application and can schedule asynchronous generation only after
the first authoritative game-state save succeeds.

## Consequences

- New persisted domains must declare lifecycle behavior once at their owner.
- Reset/restart, migration, Bot, multiplayer, swipe, and recovery require
  behavior-level contract tests.
- Old `GOLD` tags are compatibility input only and cannot directly deduct money.
- New narrative rewards use the structured `REWARD:player_id:amount:reason` tag.
  The amount is already in the ruleset's canonical currency unit and the exact
  tag line is retained as proposal evidence. `GOLD` remains a compatibility
  alias and follows the same pending-proposal path; neither tag mutates a
  balance directly.
- Model-originated rewards without retained evidence cannot use the automatic
  small-cash settlement path. Malformed amounts, unknown recipients, duplicate
  evidence, and item delivery without a committed receipt fail closed.
- A model cannot advance transaction-dependent narrative state before a player
  decision, or continue from a rejected transaction on a later turn.
- Multi-proposal model responses use a conservative all-or-nothing effect
  barrier because the legacy tag protocol cannot prove per-effect attribution.
- The stable table key can continue to back links and Bot bindings while stale
  actions from an earlier run are rejected.
- Old memory rows may be cleaned later; isolation does not depend on cleanup.
