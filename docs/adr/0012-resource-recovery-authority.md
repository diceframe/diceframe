# ADR-0012: One-use resource recovery authority

- Status: Accepted for 3.0.0-beta.3
- Date: 2026-10-18

## Context

SAN and Luck are character resources, but recovery also needs a server logical
clock, GM authorization, one-use consumption, deterministic retries and a
history boundary. Letting narration tags or a second rest helper write positive
SAN/Luck values would create a second authority and would make old saves unsafe
to replay. The current writer is schema 47; the beta.2 reader uses schema 46.

## Decision

`src/engine/modules/resource_recovery.py` owns a mandatory schema-1 ledger for
sources, policy snapshots, receipts, consumed scopes, operation identities and
command principals. The module participates in reset, round commit/abort and
rewind hooks. `src/webui/services/resource_recovery.py` owns authorization and
orchestration. It reuses the existing character balance helpers, authoritative
`WorldState` clock and `StagedGameTransaction`; it does not create another
balance model, clock or generic rest system.

The permanent consumption ledger is not copied into each round's log snapshot.
Receipts remain authoritative across later rewinds; unsettled qualifications
created in the discarded rounds become rejected. Rewriting across a settled
receipt is refused before any historical mutation. This prevents both replay
of a discarded qualification and ledger size multiplying by the history window.

The ruleset adapter exposes a deliberately fixed v1 MVP: `coc_light` and
`coc7e_style`, with explicit Luck `check` or `off`. A rule must declare the
complete policy and SAN/Luck resources to opt in. If the declaration is absent,
the feature is disabled. If a declaration is malformed or uses a future or
unsupported version, validation fails closed with an error; no case is inferred
or rewritten for an old custom rule. The profiles are lightweight CoC-style
behavior, not a claim to implement the complete official CoC rules. Positive
SAN/Luck narrator tags are rejected once a policy is declared.

The service flow is explicit: a player requests overnight rest or treatment;
the GM approves it; the server advances the logical clock and settles once. A
GM-authorized story reward is claimed by the recipient. After a real completed
intermission, the GM opens Luck recovery; the server resolves d100 above current
Luck for 1d10, and a failed roll consumes the opportunity. Ordinary recovery
cannot revive 0 SAN, permanent madness, death or unconsciousness.

The HTTP route enforces member and GM permissions. The P2P bridge exposes only
the semantic `recovery.read`, `recovery.request` and `recovery.claim` operations;
GM administrative mutations remain server-authorized. Rewind/swipe refuses to
cross settled recovery receipts. Migration 46→47 validates current and
historical slots and refuses pending qualifications during cross-run import; it
does not reconstruct recovery from old logs or tags.

## Consequences

Recovery remains auditable and retry-safe, and the character sheet remains the
canonical balance owner. Rules can opt out without removing the mandatory
authority slot. Future recovery behavior must extend the explicit ruleset
contract and its receipts rather than adding narrator shortcuts or a general
rules DSL.

## Scope

This ADR covers SAN/Luck recovery, treatment, story rewards and intermission
Luck for the fixed v1 contract. It does not replace D&D rest/feature runtimes,
define complete CoC compliance, infer old custom-rule semantics or migrate
legacy positive resource tags into new qualifications.
