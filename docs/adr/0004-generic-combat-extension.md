# ADR 0004: Generic combat extension capability

- Status: Accepted
- Date: 2026-09-05

## Context

Issue 212 asks for combat features that today have no generic home:
alternative turn scheduling (initiative, ATB gauges, action points),
non-weapon actions (spells, escape techniques, consumables), formula-driven
effects, and independent resource pools (mana, qi, shields, gauges).
The first consumer is a cultivation-style ruleset, but hard-coding
xianxia — or any second ruleset — into the generic engine would repeat the
coupling this architecture spent several ADRs removing.

## Decision

Combat extension lands as a generic capability with a strict dependency
direction:

```text
generic contracts (combat_contracts)
    -> generic primitives (combat_formulas, combat_resources, combat_effects,
       combat_scheduler)
    -> ruleset runtime adapter
    -> ruleset action catalog / reducer
    -> transport
```

- Actions, effects, costs and pools are data: generic ``kind`` vocabularies
  only. Spell ids, techniques, realms and schools live in ruleset action
  catalogs as ``action_id`` / metadata. The generic engine contains no
  per-ruleset branch.
- Damage/cost amounts are a restricted JSON-AST formula DSL evaluated by
  ``combat_formulas``: whitelisted nodes, bounded depth/nodes/dice/results,
  fail-closed on unknown references, injectable dice source for
  deterministic ruleset rng. No ``eval``, no control flow.
- Resource pools are canonical id + current/minimum/maximum value objects
  with atomic multi-pool commit; any insufficiency rejects the whole
  action. Which character fields become consumable resources is declared
  explicitly by the ruleset (``combat_resources`` capability) — free-form
  stats are never auto-promoted.
- Turn order is a scheduler protocol with round-robin, initiative and
  threshold (ATB) implementations. Threshold advancement is
  server-authoritative; tie-breaks are explicit (first-to-reach → speed →
  initiative modifier → canonical id). ATB runs only when a ruleset
  declares the scheduler capability.
- D&D 2024 keeps its own reducer; its damage/healing amounts are now
  evaluated through the generic formula DSL via a ruleset-side adapter
  (spell slots, concentration, half-on-save, conditions, death saves and
  victory detection remain D&D-owned).
- Scheduler and pool state are designed as serializable value objects and
  persist with the first consuming ruleset (through its opaque
  ``ruleset_state`` ownership or a versioned schema step — decided with
  that ruleset, never speculatively).
- Client transports submit intents (action id + targets) and render server
  projections; damage, speed and resource results from clients are ignored.

## Consequences

- New rulesets gain combat features by declaring capabilities and shipping
  action catalogs, not by extending the generic engine.
- The formula DSL is deliberately weak; stronger effects (conditions,
  auras, summons) require new whitelisted nodes with their own limits.
- Frontend combat UI (action buttons, resource bars, gauges) renders
  capability projections only and must not re-implement formulas or
  scheduling.
- Any future persisted combat fields follow the full chain: authority →
  persisted contract → codec → migration → reset/restart/swipe/rollback
  coverage.
