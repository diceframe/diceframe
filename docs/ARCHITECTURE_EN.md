# DiceFrame System Architecture (Developer Edition)

> **Document type: Current Architecture / System Architecture**
>
> This document describes the verifiable architecture of the checked-out source, including the upstream `main` baseline below and local integration implementations. It is not a roadmap, requirements list, PR digest, or future design proposal. A local implementation does not imply an upstream merge or release.
>
> **Upstream verification baseline**
>
> - Repository: `diceframe/diceframe`
> - Branch: `main`
> - Commit: `dbc4b80af77015032007d4e0daa961af13c484d0`
> - Release: `3.0.0-beta.2`
> - Upstream GameInstance persisted schema: `46`; the current local writer writes `47` (45 splits table ownership; 46 fences story/development/finale authority; 46→47 is the reader fence for recovery authority). Keep the upstream baseline separate from the local working-tree writer.
> - Local state ownership: `game_config` is kernel configuration; `room_access` and the economy ledger are mandatory kernel dependencies. `narration_preferences` and `check_presentation` contribute optional projections and new-run preference carry through hooks. Migration 44→45 moves the old table fields and `economy.reward_policy` without rescaling or changing transaction state; opaque extras are retained and unknown/mixed shapes are refused. See [ADR-0007](adr/0007-runtime-state-ownership.md).
> - R9-b: `scene` authority lives in `modules.narrative_notes.scene`, upgrading that slot to v2; migration 39→40 removes the old top-level key and keeps the value verbatim. Reset still clears it. Transactions that write the scene (run opening, round processing, swipe, ruleset batches and Director automation, ruleset transaction restore, reset) preflight the narrative owner through `module_hooks.preflight(..., NARRATIVE_VIEW)`. The `scene` in list, detail, lobby (including the password gate), SSE, recap and the LLM view is unchanged.
> - R9-a: `adventure_progress` and `play_mode` have their authority in `modules.adventure_runtime` under `progress` and `play_mode`; migration 38→39 removes the old top-level keys. Adventure identity stays only in top-level `adventure_binding`. An empty or unknown play mode is still derived from the binding on load; in-place reset clears progress and keeps the play mode; a new run (reset/restart/seed) keeps the play mode and re-initializes v2 progress, all through the module owner after a preflight.
> - R8-e: `ruleset_runtime`, `ruleset_state`, and `event_ledger` have their authority in `modules.ruleset_runtime` under `binding`, `state`, and `event_ledger`; migration 33→34 removes the old top-level keys. Reset preserves binding, rebuilds state, then clears the ledger. Encoding still drops unbound state/ledger without changing live data. Normal and seed creation bind `versioned_state` runtimes through `VersionedStateRuntime.game_binding` before advancement configuration or character joins, rolling creation back if binding fails. Rest, advancement, and ruleset event batches reject writes with missing or mismatched runtime IDs; narrative combat signals return False and log a warning. Existing unbound saves are not automatically bound; schema and persisted shape are unchanged.
> - R8-d: `combat_active`, `combat_enemies`, `combat_state`, `initiative_order`, and `initiative_current` moved into `modules.legacy_combat` (migration 32→33 removes the old top-level keys). Since 41→42 the slot (schema 2) stored only `combat_enemies`; 42→43 removes the whole `modules.legacy_combat` slot (`combat_enemies` is retired and never guessed into NPCs; generic hp_based combat targets player uids and `npc:<id>` only). The four turn fields are a read-only derived view: a ruleset registers `legacy_combat.register_combat_projector(runtime_id, projector)` (D&D 2024 projects its own `ruleset_state["combat"]`), and rulesets without a formal encounter authority read neutral values (`"none"`/`False`/`[]`/`0`). The migration drops the stored copies and never reinterprets them. Only invalid types are repaired; valid list contents are not filtered.
> - R8-c2: `round_start_snapshot`, `round_entity_snapshot`, and `death_save_outcomes` live in `modules.round_safety`; GameInstance property entry points are retired; readers and writers use module owners, and migration 31→32 removes the old top-level keys. Reset clears only the two snapshots and preserves death-save caches.
> - R8-c1: `last_check`, `last_checks`, `round_checks_prepared`, and `manual_roll_requests` live in `modules.checks`; GameInstance property entry points are retired; readers and writers use module owners, and migration 30→31 removes the old top-level keys. Reset continues to preserve manual roll requests.
> - R8-a: `total_llm_calls`, `total_tokens`, `started_at`, and `last_activity` live in `modules.session_stats`; GameInstance property entry points are retired; readers and writers use module owners, and migration 29→30 removes the old top-level keys.
> - R10: `modules.resource_recovery` (schema 1) is a mandatory authority slot for the current writer. It stores one-use recovery qualifications, receipts, consumption scopes, operation idempotency and command principals; character sheets still own current SAN/Luck values, `WorldState` owns the logical clock, and `StagedGameTransaction` publishes atomically. `src/rulesets/resource_recovery.py` exposes only an explicit fixed v1 strategy (`coc_light` / `coc7e_style` plus Luck `check`/`off`); an absent policy declaration disables the feature, while a declared malformed or future/unsupported policy fails closed with a validation error. The service persists the policy snapshot, run, seed and principal; positive legacy SAN/Luck narrator tags are rejected when a policy is declared, and ordinary recovery cannot revive 0 SAN or permanent madness. HTTP recovery routes enforce member/GM authority, while P2P forwards only the `recovery.read`, `recovery.request` and `recovery.claim` semantic operations. Settled receipts fence swipe/rewind; the 46→47 reader fence validates current and historical slots, rejects pending qualifications crossing an imported run, and never creates historical qualifications.
> - R11: The local world-identity upgrade runs at the front of a cold-start `common_factory` build (`reuse` does not run it). It supports legacy Lorebook SQLite v4 and canonical v5–v12 world/book/binding layouts, plus the local writer's recognized save schemas. A stable hash of each old identity produces an ASCII canonical world ID, so same-named worlds remain distinct; known world/book references in `templates`, all `saves/state*.json` files and known JSON payloads are rebound while display titles, entry IDs, `game_key`, `memory_namespace` and chat/log text remain unchanged. SQLite receipts and a durable journal roll the upgrade forward after interruption. User edits, target collisions and invalid or future schemas fail closed without overwrite. New AI world generation also keeps IDs ASCII and budgets room for the `world:` primary-book prefix while prefixing generated entry IDs.
> - Current Lorebook SQLite schema (`PRAGMA user_version`): `12`
> - Document verification date: 2026-10-18
>
> **Explicitly excluded from the current architecture**
>
> - PRs / feature branches absent from the checked-out source;
> - Local working-tree drafts;
> - Capabilities that exist only in design notes but are not implemented in the checked-out source.
>
> These items may inform future architecture, but they must not be described in this document as current behavior.

---

# 0. Developer Quick Start

This chapter is for developers who are entering the repository for the first time and are about to change code.

All of the full architecture chapters that follow are retained. This chapter does not replace them; it only provides **feature entry points, key files, and authority boundaries**.

## 0.1 Remember These Three Questions First

Before taking any issue or PR, ask:

```text
1. Where is the final authority for this feature?
2. Where does the user request enter, and which services / runtimes does it pass through?
3. Am I changing the presentation layer, application orchestration, or authoritative state?
```

The most common way to break DiceFrame is not “writing incorrect code,” but:

```text
Writing correct logic in the wrong layer
```

For example:

```text
The frontend calculates D&D rules by itself
LLM text directly modifies HP
AI hosting creates a duplicate copy of a character
WorldState and memory each keep their own copy of the “current world”
Services import each other and form dependency cycles
```

All of these can appear to work in the short term, but they create a second authority.

---

## 0.2 Feature → First Entry Point → Core Owner

| What I want to change | First entry point | Follow it to | Final Authority / Owner |
|---|---|---|---|
| Player action submission | `src/webui/services/turns.py` | `GameInstance` → `RoundProcessor` | `GameInstance` / Ruleset |
| Multiplayer action barrier | `turns.py` | `GameInstance.human_actions_ready()` / `try_advance()` | `GameInstance` |
| AI GM round | `src/commands/round_processor.py` | Check Planner / Prompt / State Applier | Server + Ruleset |
| Check planning | `src/commands/check_planner.py` | normalize / dice / rules | Server RNG / Rules |
| AI-hosted PC (exploration) | `src/commands/ai_player.py` | `turns.py` | Player Control + canonical action queue |
| Player control | `src/webui/services/game_controls.py` | `src/engine/player_control.py` | `players[uid].control` |
| Away / temporary AI takeover | `game_controls.py` | away policy / control revision | Player Control |
| AI-hosted PC (D&D combat) | `src/rulesets/automation.py` | `src/rulesets/dnd2024/combat/` | D&D Runtime |
| Current world facts | `src/engine/world_state.py` | world ops | `GameInstance.world_state` |
| Location / route legality | `src/engine/world_legality.py` | planner requirement / RoundProcessor | WorldState |
| World time / scheduled events | `src/engine/world_events.py` | `world_state.scheduled_events` | WorldState |
| Long-term memory | `src/memory/delta.py` | `src/engine/memory_outbox.py` | MemoryStore |
| D&D 2024 Runtime | `src/rulesets/dnd2024/runtime.py` | individual domains | D&D Runtime |
| D&D combat | `src/rulesets/dnd2024/combat/` | validate / resolve / reducer | D&D Combat State |
| D&D Campaign / Session 0 | `src/rulesets/dnd2024/campaign/` | EventBatch / reducer | D&D Ruleset State |
| D&D characters | `src/rulesets/dnd2024/character/` | ruleset character lifecycle | `ruleset_character` |
| D&D rest | `src/webui/services/ruleset_rest.py` | D&D resting domain | D&D Runtime |
| Recovery / rest, treatment, story rewards, intermission Luck | `src/webui/services/resource_recovery.py` | `src/engine/modules/resource_recovery.py`, `src/rulesets/resource_recovery.py`, `src/webui/services/recovery_projection.py` | Character resources, WorldState clock and one-use recovery ledger |
| D&D advancement | `src/webui/services/ruleset_advancement.py` | progression / character reconciliation | D&D Runtime |
| Ruleset capability | `src/rulesets/contracts.py` | `registry.py` | Runtime capability contract |
| Generic combat extension | `src/webui/services/combat_extension.py` | `src/engine/combat_*` | Generic combat primitives |
| Economy / payments | `src/engine/economy.py` | proposal / settlement / outbox | Economy |
| Currency model | `src/engine/currency/` | frontend currency utility | CurrencySpec |
| Save encode/decode | `src/engine/game_state_codec.py` | migrations | GamePersistedState |
| Save migration | `src/migrations/instance.py` | schema sequence | Migration layer |
| reset / restart | lifecycle service | aggregate replacement / run_id | GameInstance |
| rollback / swipe | lifecycle / GameHandler | authority lock / snapshots | GameInstance |
| Lorebook content | `src/lorebook/store.py` | locale view / CRUD / canonical entries | LorebookStore |
| Lorebook retrieval | `src/lorebook/retrieval.py` | KeywordMatcher / optional EmbeddingClient / context projection | LoreRetriever (retrieval orchestration; does not own mechanics authority) |
| World templates | `src/content/` / worlds service | Content V2 | World Content |
| Adventure Bundle | `src/adventures/` | ruleset adventure binding | Adventure package |
| Plugins | `src/plugin_host/host.py` | descriptors / capabilities | PluginHost |
| Bot | `src/bots/bridge_core/` | DiceFrame HTTP API | Server API |
| Web login / Access | `src/webui/access_control.py` | middleware / session | Web Access |
| Mobile QR sign-in | `src/webui/pairing.py` | routes / device token | Access Control |
| WebUI startup | `web_server.py` | composition / application / bootstrap | Composition Root |
| Runtime config reload | `src/webui/config_controller.py` | RuntimeConfig / composition | ConfigStore |
| Frontend room creation | `frontend-v2/src/features/create/CreateView.vue` | API / server validation | Server |
| Frontend main play view | `frontend-v2/src/features/play/PlayView.vue` | play components / ruleset UI | Projection only |
| D&D combat frontend | `frontend-v2/src/features/rulesets/dnd2024/combat/` | gameplay projection | Server projection |
| Image generation | `src/imagegen/` | generated image service / assets | Image service |
| TTS / ASR | `src/tts/` / `src/asr/` | webui services | Media service |
| Updater | updater service | launcher / docker launcher | Deployment boundary |

---

## 0.3 Recommended Code-Tracing Order

When investigating a feature problem, do not start by merely searching for button text across the repository.

Recommended order:

```text
UI / Bot entry point
↓
HTTP route
↓
WebAPI delegate
↓
WebUI service
↓
GameInstance / Engine / Ruleset Runtime
↓
Persistence / Migration
↓
Relevant tests
```

If you have already found the “final authority” halfway through the trace, do not copy that logic back into the presentation layer.

---

## 0.4 Real Request Paths for Common Operations

### Ordinary Player Action

```text
ActionComposer / Bot
↓
HTTP action route
↓
turns.submit_action
↓
validate actor / run / state
↓
GameInstance.add_action
↓
save action
↓
multiplayer barrier
↓
fill AI-hosted seat actions (if needed)
↓
try_advance
↓
RoundProcessor.process_round
↓
Check Planner
↓
server normalize / rules / dice
↓
PromptComposer
↓
LLM GM
↓
stale fence
↓
state/economy/deferred effect gate
↓
authoritative save
↓
memory outbox
↓
SSE / Bot projection
```

### Player Control Handover

```text
Web / Bot control request
↓
GameControlService
↓
control_change_block
↓
players[uid].control
↓
revision++
↓
save
↓
resume_after_control_change on human → ai
↓
reuse the existing turns progression path
```

### WorldState Legality

```text
Player free-text action
↓
Check Planner proposes world_requirements
↓
world_legality reads authoritative WorldState
↓
Unknown fact: do not guess, do not block
Proven contradiction: structured rejection / trusted ruling
↓
Only a legal move writes back the canonical location
```

### D&D Authoritative Combat

```text
Intent
↓
D&D validation
↓
resolution
↓
EventBatch
↓
combat reducer
↓
canonical combat state
↓
projection
↓
LLM only narrates confirmed results
```

---

## 0.5 “Where Is the File?” and “Who Decides?” Are Different Questions

For example:

```text
frontend-v2/src/features/play/PlayView.vue
```

may be where a button lives, but it is not the combat authority.

Likewise:

```text
src/commands/ai_player.py
```

produces AI-player action text, but it does not own character HP, equipment, world position, or dice results.

Developers should distinguish between:

```text
entry-point file
orchestration file
state owner
rules authority
persistence owner
```

---

## 0.6 Recently Added Architecture-Level Capabilities on Current `main`

From the historical 2026-09-16 baseline of the original document to the current 2026-09-18 `main`, the following **architecture-level** capabilities have been merged:

```text
WorldState v1
├─ authoritative facts
├─ public / gm visibility
├─ logical world clock
├─ scheduled events
├─ basic location / route legality
└─ atomic world-ops write entry point

Player Control
├─ human
├─ ai
├─ unclaimed
├─ revision
├─ temporary
├─ resume_mode
└─ away_control_policy

AI-hosted PC
├─ automatic exploration action fill
├─ human readiness gate
├─ immediate resume after control changes
├─ control revision stale guard
├─ character-sheet persona constraint
└─ deterministic automatic intent in D&D combat

D&D Class Feature Runtime v1
├─ src/rulesets/dnd2024/features/
├─ parameterized class_feature_catalog
├─ class-resource projection and resize policy
├─ equipment prerequisites / capability projection
└─ continues to reuse existing combat / rest / advancement authority

Hybrid Lore Retrieval
├─ one LoreRetriever shared by all standard Rulesets
├─ action + scene + canonical location + present-NPC anchors
├─ KeywordMatcher + optional semantic retrieval
├─ reuses MemoryStore.embedding_client
├─ derived lorebook_embeddings cache
├─ automatic lexical fallback when embeddings are unavailable or fail
└─ WorldState / Ruleset authority outranks Lorebook

Access / UX supporting architecture
├─ owner device token
├─ one-time pairing code
├─ mobile QR sign-in
├─ per-character control mode during game creation
└─ QR sharing entry point
```

Dedicated developer chapters later in this document cover these additions.

---

## 0.7 Capabilities That Explicitly Do **Not** Exist Yet

Do not assume the following already exist when developing features:

```text
Generic Entity Registry
Generic Relation Graph
Full long-running Process Engine
Map topology / Pathfinding
Unified Confirmed Event → World Memory Manager
Cross-Ruleset generic Class Feature Runtime
(currently only the D&D 2024-specific `features/` runtime exists)
```

WorldState v1 already has facts / clock / scheduled events, but that does not mean Entity / Relation / Process have been implemented.

---

## 1. Document Goals

DiceFrame is no longer “a TRPG page that calls an LLM.” It is a self-hosted TRPG system that includes:

- Multiplayer and single-player game runtimes;
- World, rules, character, and adventure content systems;
- Authoritative WorldState (facts / visibility / logical clock / scheduled events / basic action legality);
- Player Control and AI-hosted PCs (`human / ai / unclaimed`);
- LLM GM, structured checks, and long-term memory;
- Generic Hybrid Lore Retrieval (scene anchors + keywords + optional semantic retrieval);
- Authoritative D&D 2024 runtime and Class Feature Runtime v1;
- Compatibility paths for legacy CoC / Freeform and similar rules;
- Authoritative combat and generic combat primitives;
- Economy settlement, rollback, and cross-store outbox handling;
- Plugin host and channel Bots;
- Web frontend, SSE, and player sharing entry points;
- Supporting media services such as image generation, TTS, and ASR;
- Three deployment/update paths: Source / Windows Portable / Docker.

The core purpose of this architecture document is therefore not to list features, but to answer:

1. **Which module owns which state and authority?**
2. **Which layers does a request pass through after entering from a client?**
3. **What may the LLM decide, and what may it not decide?**
4. **How do multiplayer concurrency, restart, rollback, and settlement prevent stale writes from contaminating new state?**
5. **How do legacy paths coexist with the new Ruleset Runtime?**
6. **How do rules, worlds, adventures, and plugin content separate identity, locale, and mechanics?**
7. **How are external side effects kept consistent across crashes, retries, and rollback?**
8. **Which boundary should a new capability extend instead of adding more logic to a large file?**

This document is organized around those questions.

---
# 2. Architecture Overview

## 2.1 System Context

```mermaid
flowchart LR
    GM[GM / Owner]
    P[Players]
    BOT[Channel Bot / Plugin]
    HUB[DiceFrame Hub]
    LLM[LLM / Embedding / Image / Speech Providers]

    subgraph DF[DiceFrame Server]
        WEB[WebUI / HTTP / SSE]
        APP[Application Services]
        CORE[Game / Rules / Runtime Core]
        PLUG[Plugin Host]
        STORE[(Saves / SQLite / Assets)]
    end

    GM --> WEB
    P --> WEB
    BOT --> PLUG
    PLUG --> WEB

    WEB --> APP
    APP --> CORE
    CORE --> STORE
    APP --> STORE

    APP --> LLM
    CORE --> LLM
    PLUG --> HUB
```

DiceFrame's key boundaries are:

- **The browser, Bot, plugins, and the LLM are not the final authority for game state.**
- `GameInstance`, rules runtimes, and server-side reducers / resolvers are the authority for mechanics and persisted state.
- The LLM is primarily responsible for:
  - narration;
  - semantic understanding;
  - candidate structured plans;
  - low-trust proposals.
- Any operation that changes:
  - HP;
  - balances;
  - spell slots;
  - status;
  - combat order;
  - campaign facts;
  - persisted identity;
  - player permissions;
  - rules mechanics  
  must return to server-side legalization, validation, and persistence paths.

---

## 2.2 Layers and Dependency Direction

The application's core dependency direction can be summarized as:

```text
Frontend / Bot / Plugin Client
          │
          ▼
     HTTP Routes / SSE
          │
          ▼
         WebAPI
          │
          ▼
   WebUI Application Services
          │
          ├─────────────┐
          ▼             ▼
      GameHandler    Ruleset Runtime
          │             │
          ▼             ▼
      Generic Engine / Rules / Content
          │
          ├───────────────┬───────────────┐
          ▼               ▼               ▼
       Saves          Lorebook DB       Memory DB
```

`tests/architecture/test_dependencies.py` protects the real dependency direction through AST-level import analysis. The following boundaries are explicitly enforced:

- `engine / generation / lorebook / memory / rules`
  - must not depend on `webui`;
  - must not depend on the concrete `dnd2024` runtime;
  - must not casually depend on legacy compat;
- `rulesets`
  - must not depend on `webui / compat`;
- `dnd2024/combat`
  - must not depend on `campaign / webui`;
- `adventures`
  - must not depend on a concrete ruleset / webui / compat;
- `web_transport`
  - must not reverse-depend on game, rules, or WebUI business logic;
- the game / rules / AI layers
  - must not be aware of TLS, certificates, or a concrete transport.

This is one of the most important current **hard architecture boundaries**.

`tests/architecture/test_game_instance_facades.py` records module-state facade debt: after R7–R9 moved fields into `src/engine/modules`, `GameInstance` still keeps a compatibility property for each one, and every `src/` caller that reads or writes `instance.<field>` is counted per file + property in a shrink-only baseline. The facade set is detected from the AST (properties whose body references `src.engine.modules`), and its size may only shrink too; when callers move to the module API, the same PR lowers or removes the matching baseline entries.

`tests/architecture/test_module_pluggability.py` records where the core flow calls capability modules by name (#515; master plan §7.1, "deleting any module still leaves a playable game"). References to a capability module's API from the core create / round / snapshot / persistence / projection / context files are counted per file + module in a shrink-only baseline; every module under `src/engine/modules` except `KERNEL_MODULES` (`adventure_runtime_state`, `checks`, `content_binding`, `economy_state`, `game_config`, `progression_state`, `room_access`, `round_safety`, `ruleset_runtime`, `session_stats`) is a capability, so new modules are covered automatically. When a module moves onto a lifecycle hook, the same PR lowers or removes its entries. The runtime side is measured by `scripts/audit_module_pluggability.py` (stubs one module's capability API at a time, runs the core-flow tests, and reports new failures plus the first core caller that hit a stub); it is a report, not a CI gate.

Capability modules join the core flow through `src/engine/module_hooks.py`: the core calls fixed hook points (`round.reset`, `round.processing.begin`, `instance.reset.preflight`, `instance.reset`, `round.recovery.preflight`, `round.abort`, `round.rewind.begin`, `round.rewind.restore`, `round.commit`, `module.state.record`, `module.state.read`) and never names a module; a point with no registered handler is a no-op, and reads answer empty. `module.state.record` / `module.state.read` store and read module-held data by kind (e.g. `quick_actions`, `health_events`), answered by the module that owns the kind. The set of points is closed (registering an unknown point raises), and only modules under `src/engine/modules` may register (architecture guard). Modules on hooks so far: `world_reports` (overreach, world legality and world event round reports) and `round_presentation` (quick actions, recap, token budget bump, pending combat results, private GM directives) and `lorebook_runtime` (Lorebook sticky / cooldown / delay timers, ticked at `round.processing.begin`) and `health` (GM-visible degradation events and component status, which `engine.health` reads and writes as `health_events` / `health_status` module state), `check_reveals` (check reveal records), `player_control_state` (away-control policy, defaulting to `pause`) `private_channels` (private messages and table talk) and `seat_activity` (the "seat has acted" lock, via `seat.preflight` / `seat.acted` / `seat.forgotten` / `seat.has_acted`; without it no seat is locked, the behaviour before #480) and `legacy_combat` (the read-only combat turn view, read via `module_hooks.combat_view`; without it every game reads the neutral view) and `combat_extension_state` (the generic combat-extension payload and its per-round snapshots; the snapshot code moved from `round_snapshots` into the module and joins round commit / abort / rewind through hooks, with log entry fields unchanged) and `media` (scene-image reference and map background, read and written as `scene_image` / `map_background` module state; `payload_container`, used when importing a save package, is a storage contract and stays a direct call); removing any of them leaves a playable game that simply lacks those extras, with API payloads keeping their shape (empty list or `null`).

---

`resource_recovery` is an authority exception here: its slot and lifecycle hooks are required by the current writer and must remain readable, validated and rewindable even when a ruleset declares no recovery policy. The feature is optional at the ruleset boundary; the authority module is not. An absent policy declaration disables the feature, but does not permit unloading or dropping the authority slot. A declared malformed or future/unsupported policy fails closed with a validation error. Luck recovery itself has only the explicit `check` / `off` setting.

The creation group now follows [ADR0007](adr/0007-runtime-state-ownership.md): `room_access`, the transaction ledger `economy_state`, and `game_config` are kernel. `narrative_notes`, `narration_preferences` and `check_presentation` join read/write and preflight hooks. A new run preflights every participating source slot before creating a candidate, then owners deep-copy preferences. In-place reset preserves preferences. Disabled optional owners return neutral views and ignore their writes while preserving opaque storage.

`src/game_preferences.py` validates the application contract stored at `config.json.game_defaults`.
`src/engine/game_preferences.py` composes owners through module hooks and
`LiveAdvancementPolicyRuntime`. New games copy defaults; explicit creation choices win.
Loading/importing a save never reads the current defaults. The optional `check_policy` v1
slot owns `push_enabled`; missing old-save slots retain enabled behavior, with hook-based
reads/writes and new-run carry rather than new GameInstance fields.

The `round_presentation` v1 owner also stores `quick_actions_enabled`, defaulting to true
when absent. State read/write/preflight and new-run carry hooks preserve this display
preference; in-place reset clears round suggestions while retaining the preference.
Historical migration output is unchanged; module normalization adds the default.
The public game projection includes the flag, and settings changes use the existing SSE refresh chain.
This flag only supplies initial suggestion visibility; it neither controls generation nor
overrides a player's personal choice. The frontend's `features/play/preferences/useQuickActionVisibility.ts`
owns device-local visibility in localStorage, keyed by canonical game key and actor ID without
run/round scope. It never writes server preferences, and explicit personal choices take priority.
The raw preferences response advertises field support: a stale backend disables the default
switch with a restart hint, and saving other defaults does not inject the unsupported field.

The check domain closes unfinished Push choices without rerolling or changing Luck, then the existing round authority
chain attempts continuation after persistence. `src/webui/services/game_preferences.py` uses
the authoritative write gate, state lock and a staged transaction; in-flight generation and
unknown owner schemas are refused, and failed persistence rolls back. Later narration failure
does not undo saved preferences. `/api/games/{game_key}/preferences` is GM-only; on access-protected
installations global profiles are owner-only and public configuration omits their custom narration instructions. Frontend
feature components own Settings' separate Game page, the GM check-flow panel and profile actions.

Narrative HP delegates through the optional `NarrativeHealthRuntime` contract: generic player updates pass bounded net deltas and receive actual accepted changes. D&D settles out-of-combat changes through a versioned EventBatch; dead characters require explicit revival and narrative HP never duplicates active combat settlement. Rejection repairs legacy health projections and informs the GM. Round snapshots protect characters, ruleset state and ledger together. Re-projection reuses `merge_live_character_projection` to preserve live inventory and currency.

## 2.3 Authority Model: Who May Decide What

| Participant / layer | May do | Must not directly do |
|---|---|---|
| Browser frontend | Submit intents, action text, choices, confirmations, and render projections | Submit trusted damage, balances, permission conclusions, or combat outcomes |
| Bot / Channel Adapter | Call the HTTP API on behalf of a valid actor | Bypass actor / game / room authorization |
| LLM GM | Narrate; generate candidate tags / check plans / proposals | Become persisted-mechanics authority directly |
| Check Planner LLM | Propose which checks may be needed | Make the final decision on legal actor, numerical values, or dice results |
| Ruleset Runtime | Interpret rules, validate intents, resolve, apply events | Control WebUI / transport in reverse |
| GameInstance | Own authoritative state and lifecycle for one game | Know about HTTP, TLS, Vue, or other presentation concerns |
| Economy Engine | Own balances, proposals, transactions, rollback | Let model text directly deduct money |
| Memory Store | Store external long-term memory | Write before authoritative settlement |
| Plugin | Declare contributions / capabilities / RPC | Bypass host permissions and become core authority directly |
| Web Transport | HTTP/HTTPS, listeners, certs, endpoints | Know game rules |

---

# 3. Repository and Module Map

## 3.1 Major Backend Directories

The important architecture domains under `src/` are:

```text
src/
├─ adventures/          standalone Adventure Bundle loader / graph contracts
├─ asr/                 speech-recognition capabilities
├─ bots/                shared Bot bridge logic
├─ commands/            application/domain orchestration for rounds, checks, LLM, state apply, etc.
├─ compat/              legacy-format compatibility boundary
├─ content/             Content V2, GM style, and other content semantics
├─ db/                  database helpers
├─ docker_launcher/     Managed Docker install-commit and rollback boundary
├─ engine/              GameInstance, dice, economy, combat primitives, persistence projection
├─ generation/          content generation
├─ imagegen/            image-generation backend / asset contracts
├─ knowledge/           assistant / knowledge capabilities
├─ launcher/            Windows Portable launcher
├─ llm/                 LLM client / context / parser / tools
├─ lorebook/            lore storage, keyword matching, Hybrid retrieval, derived embedding cache
├─ memory/              long-term memory storage, retrieval, delta
├─ migrations/          persisted-schema migration
├─ plugin_host/         plugin host, processes, descriptors, capabilities
├─ plugin_sdk/          plugin SDK
├─ rules/               Legacy / generic RuleSystem and bundle loader
├─ rulesets/            versioned Ruleset Runtime
├─ tts/                 TTS capabilities
├─ web_transport/       HTTP/HTTPS / TLS / listener / endpoint
└─ webui/               aiohttp application, routes, services, WebAPI
```

Important distinctions:

- `src/commands/` is **not** the HTTP layer;
- `src/webui/services/` is the Web application-service layer;
- `src/engine/` should not continue absorbing concrete D&D semantics;
- `src/rulesets/dnd2024/` is the D&D-specific rules authority;
- `src/compat/` is the boundary through which old worlds enter the new model, not the default dependency layer for new code.

---

## 3.2 Web Backend Structure

```text
src/webui/
├─ application.py       aiohttp app + middleware + route composition
├─ bootstrap.py         startup / recovery / plugin / background jobs / cleanup
├─ composition.py       runtime composition root
├─ runtime_config.py    configuration loading and persistence
├─ access_control.py    owner / player-share / bot / SSE auth
├─ api.py               WebAPI delegation facade
├─ routes/              HTTP layer
└─ services/            application-service layer
```

`services/` is already split by functional domain, including but not limited to:

- `turns.py`
- `game_lifecycle.py`
- `game_queries.py`
- `game_master.py`
- `game_controls.py`
- `ruleset_gameplay.py`
- `ruleset_characters.py`
- `ruleset_advancement.py`
- `ruleset_rest.py`
- `combat_extension.py`
- `characters.py`
- `character_cards.py`
- `manual_rolls.py`
- `generated_images.py`
- `memory.py`
- `plugins.py`
- `worlds.py`
- `rules.py`
- `adventures.py`
- `updater.py`
- `security.py`
- `speech.py`
- `asr.py`

The architecture intent is: **routes own HTTP shape, services own use cases, WebAPI owns delegation/composition, and the core owns authoritative mechanics.**

---

## 3.3 Frontend Structure

The current frontend lives under `frontend-v2/` and follows a feature-oriented Vue/TypeScript structure:

```text
frontend-v2/src/
├─ api/                 backend API client
├─ components/          shared UI
├─ composables/         reusable state / behavior
├─ features/
│  ├─ admin/
│  ├─ auth/
│  ├─ create/
│  ├─ legal/
│  ├─ lorebook/
│  ├─ overview/
│  ├─ peer/
│  ├─ play/
│  ├─ player/
│  ├─ plugins/
│  ├─ rulesets/
│  └─ worlds/
├─ i18n/
├─ navigation/
├─ peer/
├─ router/
├─ App.vue
└─ main.ts
```

Frontend rules:

- Do not duplicate backend Content V2 logic;
- Do not infer rules capabilities because a name “looks like D&D”;
- Use backend `ruleset capabilities / available_intents` for professional-rules capabilities;
- Do not treat whether a button is visible as authorization;
- UI capability gating is an experience layer only; the server must validate again.

Global styles load through the single `frontend-v2/src/styles/index.css` entry.
`shared.css` owns document and control primitives, `navigation.css` owns application
and section navigation, and `pages/<feature>.css` owns each feature's global layout,
states and media queries. Component styles remain with their feature; tokens,
Tailwind and Markdown keep separate boundaries. The old `styles.css` / `styles/v2.css`
cascade and historical patch sheets are retired. Edit the relevant owner instead of
adding another late override sheet. See the [style guide](../frontend-v2/src/styles/README.md).

Content and management sections share the [navigation layout contract](ENGINEERING_RULES.md#141-navigation-layout-contract), including header handoff, nested sticky menu spacing and browser acceptance criteria.

AI organization of imported scripts is owned by `src/webui/services/adventure_ai_import.py`. `POST /api/adventures/script/ai` requires owner access and token-use consent, reuses the main-model generation service, and streams actual work progress as NDJSON; disconnecting cancels the active model call. Output is limited to story and reference material, preserving adventure identity, rules binding, and GM visibility, and is validated by the native Adventure owner before client preview. Adopting the draft and publishing remain separate; persistence still uses the existing review/commit contract.

---

# 4. Startup and the Composition Root

## 4.1 Why `web_server / composition / application / bootstrap` Are Separate

DiceFrame's startup path has been split out of the former “large entry script” into distinct owners:

| Module | Owner |
|---|---|
| `web_server.py` | Environment entry point and actual listener startup |
| `runtime_config.py` | Configuration sources and persistence |
| `composition.py` | Runtime dependency graph |
| `application.py` | aiohttp app / middleware / routes |
| `bootstrap.py` | startup / recover / plugin / background jobs / cleanup |

The primary value is not simply “shorter files,” but avoiding problems such as:

- importing a module causes configuration migration/persistence;
- listeners and game business logic reference each other;
- runtime hot reload replaces only half of the dependency graph;
- routes construct business objects;
- startup tests must open real ports.

---

## 4.2 Startup Sequence

```mermaid
sequenceDiagram
    participant WS as web_server.py
    participant RC as RuntimeConfig/ConfigStore
    participant CP as composition.py
    participant APP as application.py
    participant BS as bootstrap.py
    participant CF as common_factory.py
    participant PH as PluginHost
    participant REG as GameRegistry

    WS->>RC: load config / secrets / env
    WS->>CP: build composition state
    CP->>APP: create_app(dependencies)
    APP-->>WS: aiohttp Application
    WS->>WS: build listener plan
    WS->>WS: start HTTP/HTTPS listeners

    APP->>BS: on_startup()
    BS->>BS: sync built-in rules/worlds/adventures
    BS->>CF: build_subsystems()
    CF-->>BS: TRPGSubsystems
    BS->>PH: discover plugins
    BS->>CP: make_api(...)
    CP-->>BS: WebAPI
    BS->>REG: recover_all()
    BS->>WebAPI: recover economy outboxes
    BS->>PH: start_enabled()
    BS->>BS: start periodic save / embedding / docs / cert renewal
```

---

## 4.3 `TRPGSubsystems`

`src/common_factory.py` defines the current core subsystem bundle:

```text
TRPGSubsystems
├─ registry            GameRegistry
├─ llm_client          LLMClient
├─ lorebook_store      LorebookStore
├─ lorebook_matcher
├─ memory_store        MemoryStore
├─ ruleset_registry    RulesetRuntimeRegistry
└─ handler             GameHandler
```

This is a very important composition seam.

During a WebUI runtime reload, DiceFrame can:

- retain the existing registry;
- retain lorebook / memory;
- rebuild the LLM client / provider configuration;
- switch only after the candidate runtime has been fully constructed successfully.

This prevents “hot-reloading model configuration clears active game instances.”

Application startup and cleanup own the `HubClient` lifecycle. Both model-runtime
and API-only rebuilds explicitly pass the application's existing Hub client to the
new API facade, preserving rendezvous signaling and marketplace access when model,
speech or generation-budget settings are saved.

Note that `EmbeddingClient` is currently not a top-level field on `TRPGSubsystems`. After the candidate runtime is fully built, `common_factory.py` switches the new client into:

```text
MemoryStore.embedding_client
```

`GameHandler.lore_retriever` then reuses **that same client** through a lazy provider. Long-term memory and semantic Lorebook retrieval therefore share:

```text
embedding_enabled
embedding_provider_ref
embedding_model
embedding_max_input
```

They do not each maintain a second configuration or a second client. `LoreRetriever` is also not a new state authority; it is only the retrieval-orchestration component inside `GameHandler`.

---
# 5. Web Transport and Listener Topology

## 5.1 Transport Is Not a Business Layer

`src/web_transport/` specifically manages:

- HTTP / HTTPS;
- certificates such as self-signed / Let's Encrypt;
- endpoints;
- listener topology;
- graceful lifecycle.

Business modules must not import it.

---

## 5.2 ListenerPlan

Each listener is described by:

```text
ListenerPlan
- host
- port
- tls
```

For example, one instance may simultaneously expose:

```text
0.0.0.0:8000  HTTP
0.0.0.0:8443  HTTPS
[::]:8000      HTTP
[::]:8443      HTTPS
```

Each site starts independently:

- a bind failure on one host does not automatically mean other listeners failed;
- failures must be logged;
- an IPv6 failure must not cause a healthy IPv4 listener to be treated as failed.

---

## 5.3 Internal Plugin API Address

Plugins call DiceFrame's own HTTP API through `TRPG_API_BASE`.

The difficulty is that:

- the external primary listener may be self-signed HTTPS;
- an ordinary aiohttp client inside a plugin may not trust that certificate;
- the service may listen only on IPv6;
- a planned listener may fail to bind in reality.

Internal address selection therefore happens in two stages:

```text
Planning stage:
internal_api_listener(plan)
    ↓
prefer HTTP
    ↓
within the same scheme prefer loopback → wildcard → concrete host

After startup:
resolve_internal_listener(plan, started)
    ↓
verify the original choice actually started successfully
    ↓
if not, choose a fallback from started + emit warning
```

If the actual internal base URL after startup differs from the value plugins received when they were started:

```text
web_server
  └─ update PluginHost.base_env["TRPG_API_BASE"]
       └─ restart_api_consumers()
```

Only plugins that:

- are already running; and
- declare use of `diceframe.http`

are restarted. DiceFrame does not restart every plugin globally.

---

# 6. aiohttp Application and the HTTP Boundary

## 6.1 Middleware Order

The primary middleware / response hooks currently composed by `create_app()` include:

```text
CORS
  ↓
Session
  ↓
Abuse Guard
  ↓
Authentication / Access Control
  ↓
Error Code Normalization
  ↓
Response Security Headers
```

The application also owns:

- `SessionManager`
- `AbuseGuard`
- `LoginAuditStore`
- `ConnectionPool`
- `SseTicketStore`
- runtime control
- web transport
- security transport service

These are Web application state and do not belong in `GameInstance`.

---

## 6.2 Route Ownership

HTTP routes are registered by domain, for example:

- games
- auth
- bot
- plugins
- security
- hub
- system
- updater
- speech / ASR
- generated images
- worlds
- rules
- adventures
- character cards
- avatars
- scene images
- maps
- generation
- SSE
- memory

The intended shape is:

```text
request parsing
    ↓
identity / auth extraction
    ↓
call WebAPI
    ↓
service / core
    ↓
serialize response
```

A route should not itself:

- settle economy state;
- decide D&D damage;
- operate migrations;
- construct an entirely new LLM pipeline.

---

# 7. WebAPI and Service Architecture

## 7.1 Role of WebAPI

`src/webui/api.py` is still a fairly large facade, but architecturally its role is to:

- expose a unified JSON-safe application API;
- assemble service dependencies;
- delegate complex use cases to `src/webui/services/*`;
- inject shared registry / lorebook / memory / ruleset registry / handler capabilities into services.

It should not continue absorbing concrete business implementations.

---

## 7.2 Calls Between Services

The current principle is:

> **A WebUI service does not directly import another service to perform cross-domain orchestration.**

Cross-domain calls use:

- dependency dataclasses;
- callables;
- protocols;
- composition-root injection.

This prevents implicit cycles such as:

```text
characters -> turns -> payments -> characters -> ...
```

---

## 7.3 Typical Dependency Injection

For example, WebAPI constructs the character service using:

```text
CharacterDependencies
├─ games
│  ├─ get_instance
│  ├─ parse_game_key
│  └─ save_instance
├─ rules
│  ├─ load_rule_by_id
│  ├─ load_rule_for_game
│  └─ ruleset_registry
├─ assets
│  ├─ lorebook
│  ├─ world template loader
│  ├─ avatar resolver
│  └─ generated image resolver
└─ economy hooks
   ├─ commit deferred effects
   ├─ schedule deferred scene image
   ├─ apply memory
   └─ reverse memory
```

A service receives only the capabilities it actually needs instead of treating the entire `WebAPI` as a service locator.

---

# 8. GameHandler: Game Orchestration Layer

`src/commands/game_handler.py` is the core game-orchestration facade.

It composes:

```text
GameHandler
├─ CombatResolver            legacy/generic combat path
├─ DiceResolver
├─ PuzzleProcessor
├─ PromptComposer
├─ LoreRetriever             shared by round / swipe / KP Q&A
├─ GameFactory
├─ StateUpdateApplier
├─ ProgressionResolver
├─ RoundProcessor
├─ SwipeGenerator
├─ GameLifecycle
├─ StoryRecapGenerator
└─ KPQuestionResponder
```

## 8.1 Why It Is Not the Aggregate Root

`GameHandler` does not own per-game state.

Per-game state belongs to `GameInstance`.

GameHandler is closer to an:

> **Application / Domain Orchestrator**

It connects:

- LLM;
- rules;
- lore retrieval / projection;
- memory;
- combat;
- state applier;
- lifecycle

into complete flows.

---

## 8.2 Safe Entry Points vs. Internal Entry Points

One critical architecture fact:

- `process_round()` is the protected entry point;
- `_process_round_impl()` / `process_round_impl()` are only internal implementation seams.

The internal implementation does not guarantee:

- acquiring the correct process lock;
- failure rollback;
- stale-run guarding;
- failure persistence.

External transports / services therefore must not bypass the safe entry point and call the internal round implementation directly.

---

# 9. GameInstance: Per-Game Aggregate Root

## 9.1 Core Responsibilities

`GameInstance` is the authoritative Aggregate Root for one running game.

It owns:

- game identity;
- run identity;
- players and NPCs;
- round state;
- action queues;
- legacy combat state;
- ruleset binding / state;
- adventure binding;
- economy;
- checks;
- manual rolls;
- narrative state;
- private/public logs;
- media references;
- rollback snapshots;
- health/degraded state;
- runtime concurrency locks.

---

## 9.2 GameState

Current state machine:

```mermaid
stateDiagram-v2
    [*] --> CREATED

    CREATED --> WAITING
    WAITING --> ACTIVE_ACTION

    ACTIVE_ACTION --> ACTIVE_JUDGMENT: player actions complete / forced advance
    ACTIVE_JUDGMENT --> ACTIVE_ACTION: checks + narration commit succeeds
    ACTIVE_JUDGMENT --> ACTIVE_ACTION: abort_round_processing

    ACTIVE_ACTION --> PUZZLE
    PUZZLE --> ACTIVE_ACTION

    ACTIVE_ACTION --> PAUSED
    ACTIVE_JUDGMENT --> PAUSED
    PAUSED --> ACTIVE_ACTION

    CREATED --> ENDED
    WAITING --> ENDED
    ACTIVE_ACTION --> ENDED
    ACTIVE_JUDGMENT --> ENDED
    PUZZLE --> ENDED
```

Two concepts must be kept separate here:

### Judgment Abort

The current round already entered adjudication, but processing failed:

```text
ACTIVE_JUDGMENT
    ↓
abort_round_processing
    ↓
restore the snapshot from before this round entered judgment
    ↓
ACTIVE_ACTION
```

This is not “rolling back an already completed historical round.”

### Historical Rollback / Swipe

This rewrites a round that had already been committed:

```text
authoritative history
    ↓
restore target snapshot
    ↓
cut discarded branch
    ↓
regenerate / apply new branch
    ↓
persist
```

These two lifecycles must not be mixed.

---

# 10. Per-Game Concurrency Model

## 10.1 Three Lock Layers

Current `GameInstance` lock order:

```text
_authority_lock
      ↓
_process_lock
      ↓
_lock
```

Their rough meaning is:

### `_authority_lock`

Protects:

- historical rewrite;
- aggregate replacement;
- high-level authority gates.

### `_process_lock`

Protects:

- `process_round`;
- `generate_swipe`;
- the same game from running two full narrative-processing pipelines concurrently.

### `_lock`

Protects:

- finer-grained state mutation;
- aggregate-internal operations such as action queues and state transitions.

The fixed order reduces deadlock risk and reverse-order acquisition.

---

## 10.2 `run_id`

`game_key` means:

> “Which game slot is this?”

`run_id` means:

> “Which run currently occupies that slot?”

For example:

```text
game_key = web:roomA:default

run_1    ---- restart ----> run_2
            same game_key
```

An old request may still hold `run_1`.

Mutation paths must therefore check:

```text
request.expected_run_id == current_instance.run_id
```

Otherwise the stale write is rejected.

---

## 10.3 Aggregate Replacement

Restart/reset does not clear the old object field by field.

The correct semantics are:

```text
Old GameInstance
     │
     ├─ lock authority
     │
     ├─ construct candidate
     │
     ├─ initialize candidate
     │
     └─ atomic registry replacement
                    ↓
             New GameInstance
```

This means an old writer that had been waiting for the lock still points at the old object / old run when it resumes, allowing the stale fence to reject it.

## 10.4 R5-c Progression Boundary Decisions

**c2: implement.** `GameInstance.finish_judgment` validates progression writability
and the economy module, then calls synchronous
`round_recovery.finish_judgment_locked` and `turn_state.start_round_locked` in the
same `_lock` section. There is no `await` between committing the log and opening
the next round, and no call to public `start_round` that would reacquire the lock.
Queued state-lock writers therefore observe the new log, next round number, and
`ACTIVE_ACTION` together. Cancellation while waiting for the lock commits no log;
a queued canceller after commit cannot leave the game with a committed log but
still in judgment. This is atomicity under the state lock, not a rollback
transaction for the entire LLM pipeline. Existing owners retain responsibility
for log, snapshot, pending-action, check/timer, and SSE projection semantics.

The baseline characterization queued `finish_judgment` in round 3, followed by a
state-lock writer. That writer observed the new round-3 log with
`ACTIVE_JUDGMENT` before round 4 opened. Regression coverage includes the real
WebAPI → GameHandler → RoundProcessor pipeline, cancellation while waiting,
queued writers, and unchanged state on refusal of unknown progression modes or
schemas and economy/combat module schemas.

Public `start_round(expected_run_id=...)` retains its run check under the lock;
stale tokens return before module validation. `finish_judgment` retains its
internal commit contract without a token, while existing pipeline fences still
check run / registry identity. c2 adds no fence parameter and makes no guarantee
against arbitrary direct writes that bypass authority or pipeline fences.

The remaining decisions are recorded without new behavior:

- **c3: retain.** Authoritative intents advance the public log timeline through
  `progression.advance_for_public_timeline` to keep numbering continuous. This
  does not traverse the narrative state machine or open an ordinary narrative round.
- **c4: retain.** `GameState.PUZZLE` currently has no runtime entry path, but remains
  a persisted enum value. Removing it would require migration without a benefit
  in this change; a comment beside the enum explains why it remains.
- **c5: preserve legacy/imported `game_time` values.** The field is not always empty:
  construction, the codec, and imports can preserve nonempty text; reset clears it.
  No display format or locale-to-logical-clock rule has been approved, so existing
  text must not be derived over, deleted, or migrated on that assumption.
  WorldState's day/minute clock continues to own time advancement independently;
  the two existing context reads remain unchanged.
- **c6: unchanged.** Lorebook delay gates retain the existing round tick. A second
  progression mode and content-track clock adaptation need a separate design;
  R6 is outside this change.

---

# 11. Persistence Architecture

## 11.1 Save Projection

The owner of persisted projection is:

```text
src/engine/game_state_codec.py
```

Serialization logic should not continue accumulating inside `GameInstance.to_dict()`.

Flow:

```mermaid
flowchart LR
    GI[GameInstance] --> ENC[GameStateCodec.encode]
    ENC --> SAVE[Persisted Game State]

    SAVE --> MIG[normalize / migrate]
    MIG --> DEC[GameStateCodec.decode]
    DEC --> GI2[GameInstance]
```

---

## 11.2 Main Persisted State

Major categories currently covered by the codec:

| Category | Examples |
|---|---|
| identity | game_key, run_id, memory_namespace |
| binding | world_id, rule_id, ruleset_runtime, adventure_binding |
| runtime state | ruleset_state, event_ledger |
| world state | world_state: facts / clock / scheduled_events / revision |
| player control | players[uid].control / away_control_policy |
| game lifecycle | state, round_number |
| participants | players, npcs, ready/away |
| actions | action_queue, pending_actions |
| legacy combat | combat_active, enemies, initiative |
| generic combat extension | combat_extension, round snapshots |
| narrative | scene, game_time, log, summary, key_facts |
| presentation prefs | language, difficulty, narrative_perspective, gm_style_override |
| economy | proposals / transactions / effects / outbox |
| checks | last_check, last_checks, manual_roll_requests |
| rollback safety | round_start_snapshot, round_entity_snapshot, death save outcomes |
| access | GM, player access, bot bind, room credentials |
| media | scene_image, map_background |
| health | health_events, health_status |
| private channels | private_log, table_talk |

Not every runtime-only object is serialized. Examples include:

- asyncio locks;
- active tasks;
- timer task handles;
- transient in-flight context.

---

## 11.3 Persisted Schema Migration

Current version:

```text
CURRENT_INSTANCE_SCHEMA_VERSION = 15
```

Migrations must run sequentially:

```text
v1 → v2 → v3 → ... → v15
```

not:

```text
if old_version:
    guess latest shape
```

Important migration history:

| Version | Key change |
|---|---|
| 1→2 | `run_id`, memory namespace, base economy shape |
| 2→3 | durable external-effect outbox |
| 4→5 | introduced purchase request/order at the time |
| 5→6 | removed deprecated narration-priced purchase state |
| 6→7 | converged on payer-confirmed proposals |
| 7→8 | removed transfer / fee / all_contributors surface |
| 8→9 | persisted manual roll requests |
| 9→10 | explicit `play_mode` |
| 10→11 | manual roll purpose / target / comparison |
| 11→12 | Currency V2; built-in CoC dollar→cent |
| 12→13 | WorldState core; materialize an explicit empty world-state container for old saves |
| 13→14 | Player Control contract; legacy seats default to `human` without guessing |
| 14→15 | `away_control_policy`; old saves default to `pause` rather than silently handing control to AI |

Migration behavior:

- `deepcopy` first;
- do not mutate the caller's original object;
- reject unsupported future versions directly;
- do not guess uncertain monetary semantics.

---

## 11.4 Import and Identity Rebind

Importing a save does not mean “continue using the original run identity.”

The import path performs:

```text
old game_key
old run_id
old memory namespace
       │
       ▼
rebind_imported_game_state_payload
       │
       ├─ new local game_key
       ├─ new run_id
       ├─ new memory namespace
       └─ rebind economy run_id
```

The external memory store itself is not contained in the save package, so:

- undelivered pending payloads may move into the new namespace;
- already-delivered receipts must not masquerade as deliveries in the new game.

---
# 12. Full Player Action / Round Pipeline

This is DiceFrame's most central runtime path.

```mermaid
sequenceDiagram
    participant P as Player
    participant R as HTTP Route
    participant T as turns.py
    participant GI as GameInstance
    participant CP as Check Planner
    participant DR as Dice / Rules
    participant RP as RoundProcessor
    participant LR as LoreRetriever
    participant PC as PromptComposer
    participant L as LLM
    participant AP as State Applier
    participant E as Economy
    participant S as Save
    participant M as Memory Outbox
    participant SSE as SSE / Clients

    P->>R: POST /action
    R->>T: submit_action
    T->>GI: validate actor/run/state
    T->>GI: persist action immediately

    alt multiplayer still waiting
        T-->>P: waiting
    else ready to adjudicate
        T->>GI: ACTIVE_JUDGMENT
        T->>RP: process_round()

        RP->>CP: plan structured checks
        CP-->>RP: candidate checks
        RP->>DR: server normalize / roll / validate

        alt luck decision pending
            RP-->>T: RoundNotProcessed(luck_pending)
        else checks complete
            RP->>LR: action + scene + location + present NPC
            LR->>LR: keyword + optional semantic + visibility + merge/rank
            LR-->>RP: matched lore entries
            RP->>PC: compose prompt + authoritative context
            PC->>L: GM request / stream
            L-->>RP: narration + protocol tags

            RP->>RP: stale run/economy/instance fence
            RP->>E: proposals / deferred effect gate
            RP->>AP: apply permitted state updates
            RP->>S: authoritative save
            S-->>RP: success

            RP->>M: drain committed memory effects
            RP-->>SSE: broadcast committed result
        end
    end
```

---

## 12.1 Why Actions Are Persisted Before the Barrier

In multiplayer, one player may submit an action and then need to wait for the others.

If the action exists only in a local variable inside the HTTP handler:

```text
player A submits
server crash
player B submits later
```

A's action would be lost.

Therefore the Web/Bot action flow writes the action into `GameInstance` / the save before waiting on the barrier.

---

## 12.2 Multiplayer Barrier

Multiplayer does not mean “every message causes one LLM reply.”

The basic semantics are:

```text
current alive/present participants
       ↓
collect actions
       ↓
all required actors ready
       ↓
one judgment phase
       ↓
checks + one GM narrative
```

This ensures that:

- multiple actions from the same round can be adjudicated together;
- the first person to post does not become an implicit initiative authority;
- the GM prompt can see the complete set of declarations for the round.

---

# 12A. Player Control and AI-Hosted PCs

This section documents an important architecture change merged into `main` on 2026-09-17.

## 12A.1 The Controller Is Not the Character

The authoritative record is:

```text
players[uid].control
```

Current control modes:

```text
human
ai
unclaimed
```

The record also contains:

```text
revision
temporary
resume_mode
```

It answers:

> **“Who is playing this character?”**

not:

> “What is this character?”

The character itself still exists exactly once:

```text
GameInstance.players[uid]
character_sheet
ruleset_character
player:<uid> combat actor
```

Switching controllers does not copy:

```text
HP
equipment
conditions
spell slots
world position
combat actor
```

Therefore AI hosting must never create:

```text
companion:<same-character>
```

Doing so would create duplicated state.

### Developer Entry Points

```text
src/engine/player_control.py
src/webui/services/game_controls.py
src/engine/game_instance.py
src/engine/game_state_codec.py
src/migrations/instance.py
```

---

## 12A.2 `human`

A real player is responsible for the seat.

Human identity is still resolved through the existing Web session / share / Bot mappings.

Player Control does not create a second account system.

---

## 12A.3 `ai`

The server is responsible for producing actions for that PC.

The character remains:

```text
player:<uid>
```

Exploration actions and authoritative D&D combat use different generation paths, but both ultimately return to the existing authority.

---

## 12A.4 `unclaimed`

The seat exists, but currently has neither a human controller nor server AI control.

It must not:

```text
block the human ready barrier
automatically generate exploration actions
```

Whether it participates in a specific authoritative combat is decided by combat-participant rules, not because the control record silently adds an actor.

---

## 12A.5 Safe Boundary for Control Changes

Control cannot be changed at arbitrary times.

The server uses:

```text
control_change_block
```

to check the current phase and whether processing is in flight.

A typical safe window is:

```text
ACTIVE_ACTION
and no full processing flow is currently running
```

Otherwise the caller receives:

```text
CONTROL_CHANGE_BUSY
```

and must retry later.

---

## 12A.6 `control.revision`

Every real control change increments the revision.

Before issuing an AI action request, DiceFrame captures:

```text
run_id
round
uid
control.revision
phase
```

All of them are re-checked when the LLM returns.

For example, if a player claims a character while the AI is still thinking:

```text
ai → human
revision changed
↓
old AI output is discarded
```

This prevents a result from the old controller from contaminating the new controller's state.

---

# 12B. AI-Hosted Exploration Actions

Entry point:

```text
src/commands/ai_player.py
```

An AI player in exploration generates only:

```text
ordinary action text
```

It does not directly generate trusted:

```text
DC
attack hit result
damage
dice result
world fact
balance change
```

Flow:

```text
human_actions_ready()
↓
AI generates actions for ai seats that still lack one
↓
GameInstance.add_action
↓
enter the same Check Planner / WorldState / RoundProcessor path as human actions
```

---

## 12B.1 Human Gate

AI does not “notice that it has no action and immediately race ahead.”

AI action fill happens only after the human gate is satisfied.

Example:

```text
A has acted
B is still human-controlled and has not acted
C is AI-controlled
```

At this point:

```text
human_actions_ready() == false
```

C must not generate an action early and push the round forward.

Only after every human seat that must be waited for is ready are AI seats filled.

---

## 12B.2 Per-Seat Isolation

An AI-player prompt may use only information visible to that character:

```text
its own character sheet
public story
its own private perceptions
information explicitly visible to that character
actions already declared this round
```

It must not read:

```text
GM-private facts
gm_directives
another player's private_log
future plot
```

---

## 12B.3 Persona

AI hosting means:

> **Role-play this character.**

It does not mean:

> “Compute the globally optimal tactical move for the human.”

The system prompt should prioritize:

```text
identity / background
personality
values
goals / motivations
relationships
personal history
known clues
current physical / resource state
```

The character sheet remains the single persona source.

Do not create a second store such as:

```text
ai_persona
```

that can drift away from the character sheet.

---

# 12C. Immediate Resume After a Control Change

After a successful `human → ai` write, the system does not require a human to send another message just to “wake up” the round.

Entry point:

```text
GameControlService.set_player_control
        ↓
turns.resume_after_control_change
```

It reuses the existing progression path:

```text
check phase
↓
check human gate
↓
fill AI actions
↓
try_advance
↓
prepare checks / process round
```

The frontend must not trigger this by submitting:

```text
empty actions
fake actions
```

---

# 12D. Authoritative D&D Combat for AI-Hosted PCs

Exploration AI and D&D combat AI do not produce the same kind of output.

In authoritative combat:

```text
AI-hosted PC
↓
AutomaticIntentRuntime
↓
structured intent
↓
validate
↓
resolve
↓
apply EventBatch
```

Entry points:

```text
src/rulesets/automation.py
src/rulesets/dnd2024/combat/
```

The current automation ladder is deterministic rules logic and does not use LLM tactics.

Typical behavior:

```text
heal a dying ally
↓
attack nearest hostile
↓
legal movement
↓
Dodge
↓
End Turn
```

When the current combat actor is handed to AI control:

```text
ruleset_gameplay.resume_authoritative_combat
```

reuses the same automatic-intent loop until:

```text
a human's turn
combat ends
no automatic intent is available
```

Do not create a second AI combat loop.

R4-b b1 closes a verified narrative-fill admission gap: with authoritative combat
active, both mixed and all-AI tables could enqueue generated free text. The turns
service now passes a synchronous, read-only capability query through GameHandler
and the AI command to the action gate. It requires structured intents when the
runtime supports authoritative intents and either disables narrative turns or has
active combat. Generation preflight avoids unnecessary model calls; commit checks
the current rule, runtime capabilities and combat status again under the existing
authority/state locks, after stale, human and duplicate checks, with no await before
enqueue. Unknown/incompatible runtimes and missing rules for bound instances fail
closed. The engine has no ruleset-specific branch. Optional `False` / `None` inputs
retain the direct-call contract; guarded callers must forward the live predicate.
R4-b b3 adds no AI economy gate (the progression barrier already owns settlement).
R4-b b4 is implemented by R5-c1 below. See the Chinese architecture document §12E
for the ordered policy table and decisions.

R5-c1 decision: implement. On R5b `d1d64927`, the actual
`RoundProcessor.process_round_impl` paused at a fake LLM await while a concurrent
real D&D `combat.start` was accepted by the gameplay service. Both narrative
submissions with and without a run token moved round 3 to 5 and produced two log
entries numbered 4. The existing run/economy fences do not isolate these writes
within the same run.

`STRUCTURED_INTENT_POLICY` now checks membership, then `check_not_judging`.
Only `ACTIVE_JUDGMENT` is newly rejected; GM bypass applies to membership only.
No new control, death, economy or other phase restrictions are added.
`submit_intent` evaluates live admission inside the actual state lock before
binding migration or transaction writes. The lock remains held across binding
save awaits, event reduction, public timeline projection and attached automatic
intents, closing the lock-wait race. The special `adventure.node.complete` command
uses the same admission check after its existing GM-only authorization. Rejection
returns service `code=ROUND_PROCESSING`, mapped to HTTP 409 by the route, without
state, log, resource, save or memory writes. Shared `_context` remains membership
only, preserving available-actions and temporary encounter proposal behavior,
including existing binding compatibility handling.

Control-triggered `resume_authoritative_combat` checks the same judgment condition
under the state lock before binding handling or automatic progression. It returns
the existing resume shape with `error_code=ROUND_PROCESSING`, `handled=True` and
`resumed=False`. A control change already saved remains successful; only its
subsequent combat resume is refused. Runtime-internal intent mechanics and the
narrative processor's own director automation are unchanged. R4's membership-only
test expectation is explicitly replaced by c1 coverage, retaining missing-seat
priority and GM bypass of membership only. R5-c1 retains instance schema 21 from
R5b and does not change `game_time`. R5-c2 now commits judgment and opens the next
round in one state-lock section; see §10.4 for that boundary and the c3–c6 decisions.

---

## Public Log Projection (R6-a2)

`services/logs.get_log(include_internal=False)` filters GM directives and limits
entries to `PUBLIC_LOG_FIELDS`. Nested `actions` and list-form `player_actions`
are further limited to `PUBLIC_ACTION_FIELDS`: `user_id` and `text`. Historical
renderers parse dice display from text and resolve names from the player roster;
live-action revision and pending-dice status belong to a separate contract.
Unknown ActionRecord fields remain internal by default. User-to-text mappings in
`player_actions` retain their existing shape. Swipes contain narration strings;
check results are separately constructed records, not raw ActionRecords. The GM
path retains full records with existing narration sanitization. Projection does
not mutate the source log or persisted state.

---

# 13. Check Planner and Server-Side Adjudication

## 13.1 Role of the Planner

`src/commands/check_planner.py` uses a model to understand:

- which actions may require checks;
- who the actor is;
- target / opponent;
- intent type;
- possibly related skills / items / NPCs;
- economy purchase intents, etc.

But its output is only a **candidate plan**.

The final result must pass through:

```text
normalize_check_specs
    ↓
server actor resolution
    ↓
rule/dice validation
    ↓
server RNG
    ↓
CheckResult
```

---

## 13.2 Actor Resolution

The planner currently supports explicit actors:

```text
player:<uid>
companion:<id>
```

and exact matching against player / companion names.

Rules:

- unique match: usable;
- duplicate names: reject;
- no match: reject;
- an ordinary narrative NPC cannot become a mechanical actor just because its name “looks like a companion.”

---

## 13.3 Context Is Not Authority

The planner may see compressed:

- inventory;
- equipment;
- NPCs;
- relationships;
- recent purchases;
- companions;
- recent narration.

This information exists only to improve semantic understanding.

For example:

> Seeing “there is a crowbar in the backpack” does not give the Planner authority to directly set the crowbar quantity to 0.

Real mutations still go through the state applier / ruleset runtime.

---

## 13.4 Skill `effect`

Character skills may contain:

```json
{
  "name": "Investigation",
  "value": 60,
  "effect": "Good at identifying unnatural gaps in a cluttered scene"
}
```

`effect` is currently:

> **descriptive metadata, not mechanics.**

The Planner only attaches an effect when:

- the player's action text clearly matches the skill; or
- the client explicitly selected `selected_skill`.

It cannot change:

- the skill value;
- DC;
- advantage/disadvantage;
- dice;
- damage;
- HP;
- status;
- resources;
- inventory.

If a skill needs a real mechanical effect in the future, that effect should enter the ruleset runtime / rule catalog rather than expanding the authority of free-text `effect`.

---

# 14. Manual Roll Subsystem

Manual rolling is not merely frontend randomness.

Current owner:

```text
src/webui/services/manual_rolls.py
```

Requests are persisted in:

```text
GameInstance.manual_roll_requests
```

Each request contains:

- request id;
- idempotent `operation_id` identity;
- `run_id`;
- round;
- created_by;
- targets;
- formula;
- purpose;
- target/comparison;
- visibility;
- results.

---

## 14.1 Purpose

Current values:

```text
free
check
contest
```

### free

An ordinary independent roll.

By default it is not injected into later AI-GM context.

It enters AI context only when the API explicitly sends the JSON boolean:

```json
true
```

The string `"true"` or the number `1` does not count.

### check

Has a target/comparison; the server produces success/failure.

### contest

After all targets complete their rolls, the server compares totals and produces winner/loss information.

`check / contest` results are always injected into subsequent AI context because they have become authoritative table events.

---
# 15. LLM Subsystem

## 15.1 Module Boundary

```text
src/llm/
├─ client.py
├─ context_builder.py
├─ parser.py
├─ protocol.py
└─ tools.py
```

`LLMClient` owns provider-facing capabilities.

Prompt / turn orchestration primarily lives in:

```text
src/commands/
├─ prompt_composer.py
├─ round_llm.py
├─ round_processor.py
└─ ...
```

---

## 15.2 PromptComposer

`PromptComposer` is the centralized owner of the GM prompt.

The final prompt is assembled from layers such as:

```text
base GM system prompt
    +
current rule appendix
    +
difficulty instructions
    +
resource protocol appendix
    +
effective GM narration style
    +
plot tracker
    +
multiplayer authority scope
    +
narrative perspective
    +
ruleset advancement instructions
    +
language instruction
```

One especially important rule:

> `instance.rule_id` is the authoritative rules selection after the game has started.

A World template's `default_rule` only provides the default choice on the creation page. Runtime code must not silently replace the player's chosen rules with the world's default rules.

---

## 15.3 Runtime LLM Projection

A professional ruleset may add an authoritative projection to generic LLM context through:

```text
runtime.build_llm_view(instance)
```

The correct relationship is therefore:

```text
Generic game context
      +
Ruleset authoritative read-only view
      +
WorldState / Hybrid Lore projection
      +
Memory
      +
Current actions/checks
      ↓
LLM
```

The context builder must not import D&D itself and calculate D&D state.

---

## 15.4 Player-Safe Q&A

Player-facing GM/KP Q&A is separate from the full GM context.

`build_player_safe_context()` uses a restricted context so that:

- GM-private directives;
- hidden facts;
- content that should not be public to that player

do not leak out of the full GM prompt.

Out-of-character Q&A does not have a second Lorebook retrieval system. `KPQuestionResponder` reuses `GameHandler.lore_retriever`, but runs it from the player's point of view:

```text
viewer_is_gm = false
mutate_timers = false
```

Candidate Lore is filtered by `visible_to` before semantic ranking, so hidden entries cannot enter the player-safe candidate set merely because they are semantically similar. Timed state is copied, so asking an out-of-character question does not advance sticky / cooldown / delay state. The player-safe projection still retains:

```text
type
tier
unreliable
name
content
```

but does not expose the internal canonical Lore entry ID. This prevents IDs such as `npc_traitor_mary` or `clue_real_murderer_john` from leaking secret information by themselves. Full GM context may still keep canonical IDs for diagnostics and consistency.

---

### 15.4.1 Current Adventure scenes and objectives

`src/adventures/scene_context.py` owns the Adventure v2 scene projection. The
WebAPI composition root injects the application-owned resolver into
GameHandler / PromptComposer. The projection verifies package source, version,
digest and world binding, and reads the module's non-mutating
`progress_snapshot()`. It never creates missing slots, generates a plan or
advances nodes. Regular turns, openings, resume narration and player questions
share this seam.

The GM view contains active nodes, related objectives and one-hop successor
candidates; candidates are not asserted to be unlocked. Players and AI seats
receive only active public nodes, objectives related to known nodes and their
completion status. Canonical IDs, NPC/encounter references, transition conditions
and outcomes are omitted. Public visibility alone does not reveal inactive,
unvisited future nodes to the model. Existing private/party Q&A knowledge
boundaries remain in force. The game Adventure query exposes the same projection
as `adventure.scene_objectives`.

The context builder budgets at most 10% of its window, capped at 4096 characters,
for this block, retaining objectives before optional descriptions. Dedicated
opening/resume narration requests also cap the block at 4096 characters. No
additional model call is introduced. Historical swipe uses the owning module's rewind hook to restore the target
round's `pre_adventure_progress` before projecting; v2 histories without the
progress or character snapshot cannot be regenerated using later progress. Resolution failures produce
an empty player projection and an unavailable notice for the GM. Existing binding
and gameplay authority paths continue to own admission.

v1 uses optional `CurrentSceneRuntime` to project the bound runtime's current
step. The D&D adapter reads a copy and omits future steps for players. Unbound
games use the run plan below; a broken Adventure binding never falls back to it.

### 15.4.1.1 Universal narrative packages

`src/adventures/compatibility.py` owns Adventure admission. Graph v2 can require
the independent `core:adventure >= 1` narrative profile, not a character ruleset.
Legacy and D&D advertise v2 through `adventure_formats`; CoC/custom rules need no
display-name branches. `narrative_validation.py` rejects future profiles, v1,
rules gates, executable encounters/rewards and mechanical fields disguised as
reference records. Bounded inert foreign source records remain allowed. Specific
packages retain exact runtime/version/format and existing bindings/digests.
Catalogue, creation, binding and D&D loading share admission. Module installation
recognizes the profile only for Adventure requirements; plugin `requires` still
needs actual rulesets. Implicit module-catalogue ownership is unchanged. An
explicit user directory retains ownership before it exists, preventing writes
into bundled templates.

Only known `adventure.node.complete` and `adventure.choice.request` application
commands bypass the combat-intent capability requirement. Membership, GM/actor,
phase, binding and locked persistence checks remain; failures/cancellation restore
world, progress and economy. Other legacy structured intents remain unavailable.
Creation/library clients share one compatibility helper. See
[ADR-0009](adr/0009-universal-adventures.md) and the [contract](adventures/UNIVERSAL_ADVENTURES.md).

### 15.4.2 Free-game plans, intermissions and finales

`modules.run_story` (schema 1) owns a per-run plan, revision, current/revealed/
completed scenes, transitions, rejected-NPC warnings and stalled-round count.
A GM writes a plan or requests an AI original draft and explicitly adopts it.
Historical games receive no guessed plan. At most 24 scenes declare theme,
party goal, guided/sandbox mode and strict/open NPC policy. Adoption is not
Adventure publication and cannot change world sources, rewards or bound progress.
The GM may explicitly copy a plan through `story_adventure` into a reusable
narrative draft. `story_draft` accepts validated authored fields only, preserves
the authored start and exclusive successor choices, and defaults every copied
scene/objective to GM-only. Exit conditions/hints stay descriptive and must be
removed before public disclosure. Logs, live NPCs, characters, world state,
credentials and source run identifiers are excluded. Existing review/digest/commit
owns publication. The bridge checks GM/current run/revision, never publishes or
mutates the source run, and does not broadcast a game-change event.

Players receive only current public scene/objective data. GM context adds exit
conditions, adjacent candidates and progressive hints. A model-authored
`plot_update.story_transition` is advisory: format_version 1, run_id, revision,
from_id, to_id and evidence_rounds. GM confirmation and domain validation of
run/revision, adjacency and real committed rounds are mandatory. Narration
cannot commit progress. Strict NPC admission rejects unadopted new state and
records warnings; prose remains subject to GM review. Stall hints offer routes
or costs without changing dice results. Diagnostics show the actual scene block
and its 4096-character budget.

`modules.skill_growth` (schema 1) owns successful-check marks, intermission
receipts and internal randomness reservations. Rules opt in through
`growth_system=skill_improvement` and the supported version 1 policy. Built-in
Lite CoC declares d100 above skill or at least 96, d10 gain, cap 99. Attributes,
failed checks, luck-spent successes, explicitly non-improvable and excluded
skills do not mark. Marks deduplicate by actor and stable skill-row identity;
ambiguous names are not guessed. `SKILL_GROWTH` narrator tags have no authority.
Settlement validates all targets on an isolated aggregate, then publishes
updated sheets and a receipt together. Repeated receipts return the original
result. The service persists randomness before evaluating rolls; failed or
cancelled persistence retries reproduce the same marked-check result instead
of rerolling. Historical increases remain and old logs do not create new marks.

`modules.run_finale` (schema 1) owns preparation, an immutable archive and a
separate character-delivery outbox. Idle ACTIVE_ACTION/PAUSED games may prepare
after actions, combat, dice, luck/Push, economy proposals, external effects and
runtime-specific pending work are resolved. Preparation saves an ID, source
fingerprint, whole-run public transcript and player-safe current scene. The
original GameState remains, with writes frozen. Materials may be manual or AI
drafts. Whole-run summarization uses segments rather than the incremental recap's
last-ten-round limit. Whitelisting precedes model calls: private Q&A, GM
conditions, hidden snapshots and future nodes never enter public material.
Private GM notes are separate and never AI-generated here.

NDJSON reports real stages, requires token consent, supports cancellation and
disconnect cancellation, and times out after 600 seconds. A draft does not save
materials or settle. GMs save, review and confirm; optimistic material revisions
reject stale multi-window approval. Settlement publishes supported rule results,
character snapshots, a receipt and ENDED atomically, rolling back failed saves.
Optional `RunFinaleRuntime` owns specific-rule audit: D&D adds zero extra XP or
loot; Lite CoC uses deferred improvement. SAN/money awards are not inferred.
Preparation can be cancelled; committed settlement cannot. Restart preserves
freeze/archive state without scheduling another round.

Finale barriers and growth receipts are mandatory kernel authority even when
presentation hooks are detached. HTTP `run_write_admission` and aggregate
authority/state gates cover Web, same-route Bot and direct-connect requests.
The finale service owns administrative writes. Read-only SSE ticket issuance,
GM identity recovery, seat-credential issue/claim (administrative, still
serialized) and deleting the owner's memory entries remain available on
frozen/ended archives. Slow model/media routes (KP questions, recaps, speech,
transcription, image generation and storyboard analysis) are admitted at entry
but do not hold run authority while they wait; their short commit step takes
`authoritative_write()` and re-checks the run. Reset/restart cannot overwrite
frozen/ended archives.
Synchronous domain setters remain trusted transaction primitives. Rewind/swipe
cannot cross settled intermissions; pending marks and plans use owner snapshots.

`RunStoryService` composes explicit dependencies behind thin routes. UI components
live in `features/play/run-story/`, mounted by the GM scene workspace and read-only
play surface. A data-free `run_story_changed` event invalidates role-filtered
views, including same-round plan, preparation and material revision changes.
Owners contribute invalidation values through `gm.workspace.revisions`; the
generic hook produces an opaque, GM-only `gm_workspace_revision`. The GM SSE
cursor includes it to cover same-round changes, subscription gaps and reconnects;
player cursors exclude it. Panels reload on relevant versions, fence delayed reads
after writes and show known freeze status while public materials load.
Players export public recaps and their own character only. GM save
ZIPs include separate epilogue, recap, private notes, source, receipt, character
JSON and a SHA-256 manifest, with room_access credential scrubbing retained.
Carry creates a separate game/run from settled full sheets, verifies runtime
initialization leaves them unchanged, and issues fresh invitations. Old plans,
NPCs and secrets do not carry. A durable outbox reuses the same destination on
retry; the Lorebook owner materializes canonical selections for it. Ordinary
character-library import still follows its creation-field contract and is not
equivalent to full-snapshot carry.

Migration 45→46 is the story/development/finale reader fence; 46→47 is the
recovery-authority reader fence. Neither reconstructs historical plans, marks or
recovery.
Explicitly damaged module containers are refused before defaults can erase authority.
Unsupported/future module versions reject writes. Persisted non-object slots
are rejected before generic defaults can silently discard their authority.
Preparing saves cannot import
into another run until cancelled. Ended imports remain read-only and preserve
receipt provenance. Plan state and historical plan snapshots rebind at import.
This does not implement #446's full CoC Adventure runtime or guarantee arbitrary
model prose never invents facts. See [ADR-0008](adr/0008-run-plans-and-finales.md)
and the [user guide](en/run-story-and-finale.md).

### 15.4.2.1 Resource recovery and intermissions

`modules.resource_recovery` (schema 1) is the sole authority for recovery
qualifications, receipts, consumption scopes and command idempotency. Character
sheets continue to own current SAN/Luck values, `WorldState` continues to own
the logical clock, and the service publishes through `StagedGameTransaction`
with the existing bounded character-balance update. This does not create a
second balance, clock or rest system.

Recovery is a fixed ruleset-declared MVP contract, not a general DSL. A complete
`resource_recovery` v1 declaration enables the feature: `coc_light` permits
GM-approved overnight SAN recovery and treatment, while `coc7e_style` uses
Psychoanalysis treatment over 30 days and does not restore SAN from overnight
rest. Both profiles can explicitly select Luck `check` or `off`. An absent
declaration disables the feature and does not infer or force a policy. A
declared incomplete, malformed, future or unsupported policy fails closed with
a validation error. This is a lightweight CoC-style service contract; it does
not claim to implement the complete official CoC rules.

Players request overnight rest or treatment from the recovery/rest panel in the
character rail. After GM approval the server advances its logical clock for the
required duration and settles the result. The service compares the required
duration with elapsed time since the request and advances only the remaining
difference, so an approval or retry never adds another full duration. Treatment uses a declared
`psychoanalysis` skill (the light profile may also declare `psychology`). The GM
can grant a SAN/Luck story reward, which the character player claims. After a
real completed intermission the GM opens Luck recovery; the server rolls d100,
and only a result above current Luck grants 1d10. A failed roll consumes that
intermission opportunity as well. A character at 0 SAN or permanent madness,
dead, unconscious, away or in combat cannot be revived or settled by ordinary
recovery.

When a policy is declared, positive legacy SAN/Luck narrator tags are never
recovery authority and are rejected. Recovery must use the service path with a
policy snapshot, server seed, run/principal identity, operation idempotency and
one-use consumption ledger. Web routes enforce member and GM authority; P2P
exposes only `recovery.read`, `recovery.request` and `recovery.claim`, while GM
administrative actions remain server-authorized. Settled receipts fence
swipe/rewind across the settled round. The 46→47 reader fence validates current
and historical module slots, rejects pending qualifications crossing an imported
run, and never reconstructs qualifications, resources or rewards from old logs.

---

## 15.5 Preventing Reasoning Leakage

Some model providers mix reasoning into content:

```text
<think>...</think>
```

DiceFrame currently filters at both boundaries.

### Non-Streaming

`src/llm/client.py`

cleans:

- complete think blocks;
- isolated closing tags;
- unclosed reasoning.

### Streaming

`src/commands/round_llm.py`

must handle chunk splits such as:

```text
chunk1 = "<thi"
chunk2 = "nk>..."
```

Filtering is therefore stateful. An unclosed think block must not be flushed to the player at the end.

---

# 16. Stale Fence After an LLM Response

Calling an LLM is a long-running operation.

During those seconds or tens of seconds, the following may happen:

- player settlement;
- GM rollback;
- restart;
- reset;
- a new proposal;
- instance replacement.

The response therefore cannot be applied directly when it returns.

`RoundProcessor` compares again:

```text
registry current instance identity
run_id
economy fingerprint
```

If the old request has become stale:

```text
LLM response
    ↓
stale fence
    ↓
discard
```

instead of overwriting newer state.

---

# 17. Economy Architecture

## 17.1 Basic Principle

Model text cannot say:

```text
"You spent 20 gold."
```

and then cause the system to execute:

```python
gold -= 20
```

The authoritative path is:

```text
Narrative / Planner
      ↓
proposal
      ↓
server validation
      ↓
payer / GM decision
      ↓
transaction
      ↓
balance mutation
```

---

## 17.2 Currency V2

A rule defines currency through:

```text
CurrencySpec
├─ base_unit
├─ display_unit
└─ positive integer rates
```

For example, CoC:

```text
base_unit = cent
display_unit = dollar
```

The engine processes canonical integers only:

```text
$12.34
  ↓ parse
1234
  ↓ engine
1234 cents
  ↓ format
$12.34
```

Business code must not scatter operations such as:

```python
amount * 100
amount / 100
float(amount)
```

---

## 17.3 Unknown Price != Free

If DiceFrame has already recognized:

> “I want to buy this bottle of medicine.”

but cannot canonicalize the price/unit, the system will neither:

- charge the wrong amount; nor
- grant the item for free.

Instead it records for the current round:

```text
round_unpriced_purchase_intents
```

and blocks the related item grant.

---

## 17.4 FREE_GRANT

Only when the narrative explicitly states that an item is genuinely free / gifted / rewarded may DiceFrame produce a transient:

```text
FREE_GRANT
```

It:

- deducts no money;
- adds no money;
- does not itself grant the item;
- creates no proposal;
- is not persisted;
- acts only as authorization evidence for the item-grant gate.

And:

> If a real pending paid proposal exists, FREE_GRANT cannot bypass payment.

---

# 18. Narrative Commit Barrier

Economy in DiceFrame is not merely “bookkeeping.”

It also acts as:

> **the authoritative commit barrier for one model response.**

Suppose one model response simultaneously produces:

```text
NPC gives the player a sword
player pays 100
quest advances to the next stage
memory records "deal completed"
scene changes
```

If DiceFrame first applies:

```text
sword granted
quest advanced
memory written
```

and payment is then declined, state becomes inconsistent.

The current model is therefore:

```mermaid
flowchart TD
    LLM[LLM response]
    PARSE[Parse candidate effects]
    PROP[Create proposal]
    DEFER[Persist deferred effect group]
    DEC{Decision}
    COMMIT[Commit shared effects]
    DROP[Discard shared effects]

    LLM --> PARSE
    PARSE --> PROP
    PARSE --> DEFER
    PROP --> DEC
    DEC -->|accepted| COMMIT
    DEC -->|declined / insufficient / cancelled| DROP
```

As long as the current run still has any of the following:

- pending proposal;
- pending effect group;
- memory delivery;
- memory reversal;

the next authoritative narration is blocked by the barrier.

---

# 19. Memory Outbox and Cross-Store Consistency

The game-state save and memory SQLite database are two storage domains.

DiceFrame cannot perform a real database transaction spanning both systems.

It therefore uses a durable outbox.

```mermaid
sequenceDiagram
    participant G as Game Save
    participant O as economy.external_effects_outbox
    participant M as Memory Store

    G->>O: persist pending memory_delta
    G->>G: authoritative save succeeds

    O->>M: idempotent delivery
    M-->>O: applied
    O->>G: mark delivered
    G->>G: persist receipt

    Note over O,M: crash before receipt? retry by delivery identity
```

---

## 19.1 Outbox Owner

Outbox data is stored in:

```text
economy.external_effects_outbox
```

because:

- it must be persisted together with the economy rollback window;
- effect groups and settlements have explicit identities.

However:

> the delivery state machine belongs to the memory domain, not the economy ledger.

---

## 19.2 Rollback Reversal

Memory already written into the memory store cannot be ignored during historical rollback.

Flow:

```text
delivered
   ↓ historical rollback
reversal_pending
   ↓
reverse memory delta
   ↓
reversed
```

If reversal succeeds but the receipt is not saved:

- recovery continues next time;
- identity keeps the operation idempotent.

---

# 20. Item / Equipment State Protocol

Older single-value fields cannot express:

> obtaining three items, equipping one, and consuming one in the same round.

The current structured state uses:

```text
item_gains[]
equipment_ops[]
item_uses[]
```

For example:

```json
{
  "item_gains": [
    {"name": "Leather Armor", "category": "equipment", "qty": 1},
    {"name": "Healing Potion", "category": "consumable", "qty": 2}
  ],
  "equipment_ops": [
    {"op": "equip", "name": "Leather Armor", "slot": "body"}
  ],
  "item_uses": [
    {"name": "Healing Potion"}
  ]
}
```

Older fields such as:

```text
equip_gain
weapon_gain
...
```

exist only as compatibility fallbacks.

---

# 21. Overall Ruleset Runtime Architecture

## 21.1 Why Ruleset Runtime Exists

Legacy `RuleSystem` can handle many custom-rule needs:

- d20 / d100;
- skills;
- resources;
- generic combat models;
- prompt appendices.

But complete D&D requires:

- canonical class/spell identity;
- slots;
- concentration;
- conditions;
- advancement;
- Session 0;
- adventure encounter binding;
- authoritative event ledger;
- deterministic combat.

These cannot continue to be placed inside generic `RuleSystem`.

Therefore DiceFrame introduces:

```text
src/rulesets/
```

---

## 21.2 Registry

Current default registry:

```text
RulesetRuntimeRegistry
├─ LegacyRulesetAdapter
└─ Dnd2024Runtime
```

A rule template binds through:

```json
{
  "runtime": {
    "id": "core:dnd2024",
    "minimum_version": 1
  }
}
```

If `runtime` is absent, DiceFrame uses:

```text
core:legacy
```

It does not guess the runtime from:

- rule name;
- display language;
- `dice_system == d20`;
- a `rule_id` prefix.

---

## 21.3 Main Runtime Protocol

The current `RulesetRuntime` protocol is still relatively broad and includes:

```text
describe_experience
builder_choices
validate_character
derive_character
finalize_character
normalize_character_submission

available_intents
validate_intent
resolve_intent
apply_event_batch

gameplay_view
build_llm_view
project_legacy_character

migrate_state
```

This means the architecture has successfully extracted a rules runtime, but the protocol is not yet the final minimal interface.

---

## 21.4 Optional Capability Protocols

To avoid forcing every runtime to implement an ever-growing method set, additional abilities are exposed through optional Protocols such as:

- `VersionedStateRuntime` (required for `versioned_state=True`; games and character sheets share binding identity)
- `AuthoritativeIntentHooks`
- `NarrativeStatePolicyRuntime`
- `NarrativeCombatSignalRuntime`
- `NarrativeCheckPolicyRuntime`
- `NarrativeAdvancementRuntime`
- `NarrativeDirectorRuntime`
- `NarrativeDirectorAutomationRuntime`
- `NarrativeDirectorPlanningRuntime`
- `TemporaryEncounterPlannerRuntime`
- `AutomaticIntentRuntime`
- `GameDetailProjectionRuntime`
- `PlayerJoinRuntime`
- `CharacterRevivalRuntime`
- `LiveAdvancementPolicyRuntime`
- `LiveAdvancementTransactionRuntime`
- `AdventureBindingMigrationRuntime`
- `RunLifecycleRuntime`
- `PublicTimelineProjectionRuntime`

The correct extension pattern is usually:

```text
add one explicit optional capability
```

rather than placing D&D special cases into:

```text
engine
WebAPI generic method
frontend generic component
```

---

# 22. Generic Combat Extension

The goal of the generic combat extension is not to implement “one universal TRPG ruleset.”

It provides only sufficiently stable primitives:

```text
Combat Contracts
       ↓
Formula DSL
Resource Pools
Effect Engine
Schedulers
       ↓
Ruleset Adapter
       ↓
Concrete Ruleset
```

---

## 22.1 Formula DSL

This is not `eval()`.

Formulas are represented as a restricted JSON AST.

Constraints include:

- node whitelist;
- depth limit;
- node-count limit;
- dice-node limit;
- bounded result;
- fail closed on unknown references;
- injectable deterministic roller.

A concrete D&D damage expression such as:

```text
1d8 + STR
```

may be translated by a D&D adapter into the generic AST, while:

- spell slots;
- concentration;
- save semantics;
- class features;

remain owned by the D&D runtime.

---

## 22.2 Resource Pools

The generic pool handles:

- current / max;
- atomic multi-pool spend;
- clamped restore/set.

If one action requires:

```text
2 Mana
1 Action Point
```

and either resource is insufficient:

```text
whole cost fails
```

DiceFrame must not deduct Mana first and only then discover that the Action Point is missing.

---

## 22.3 Scheduler

Current generic scheduler primitives support:

- round robin;
- initiative;
- threshold / ATB.

They activate only when a ruleset capability explicitly declares them.

The generic engine does not automatically infer “enable ATB” merely because it sees a `speed` field.

---

# 23. Ruleset Bundle v1

Ruleset Bundle is used for first-party advanced-rules content snapshots.

It is not the same concept as Plugin Content V2.

```text
templates/rulesets/<directory_id>/
```

A bundle binds:

- `bundle_id`
- `runtime_id`
- rules version
- content version
- locale
- owned files

A canonical entity has:

```text
kind:id
source_ref
automation_level
```

---

## 23.1 Separating Locale From Mechanics

Bundle locale may change display content only.

Locale must not:

```text
change Fireball damage from 8d6 to 20d6
```

Any of the following causes the entire bundle to be rejected:

- any code-execution key;
- unknown effect primitive;
- duplicate IDs;
- broken internal references;
- owned-path escape;
- locale mechanics override.

---

# 24. Adventure Bundle v1

## 24.1 Four Independent Inputs

Advanced play is not “one world package that contains everything.”

The current model is:

```text
Ruleset Runtime  → mechanics
Worldbook        → setting + lore
Adventure Bundle → story graph + scene + NPC + encounters
Coach            → local presentation/help
```

These are independent inputs.

Without an Adventure Bundle:

```text
standard free play
```

A fixed tutorial must not be silently activated.

---

## 24.2 Immutable Binding

At game creation, DiceFrame saves:

```text
adventure_id
version
format
content_digest
world_id
```

Restart must verify these again.

If:

- files are missing;
- content changed;
- fixed-world requirements do not match;

DiceFrame fails closed.

It must not silently load “an adventure with the same name but different content.”

---

## 24.3 Adventure and Worldbook

An Adventure step may decide:

> “Which story node are we currently on?”

but it cannot replace the player's selected Worldbook.

LLM context therefore still contains:

```text
actual selected world
+
matched lore
+
current adventure step
```

When the adventure ends:

```text
same world → free play
```

rather than “tutorial complete, game over.”

---
# 25. D&D 2024 Runtime

## 25.1 Top-Level Composition

The current `src/rulesets/dnd2024/` tree is clearly split by domain:

```text
dnd2024/
├─ character/
├─ campaign/
├─ combat/
├─ director/
├─ exploration/
├─ features/
├─ play/
├─ progression/
├─ resting/
├─ spells/
├─ advancement_access.py
├─ adventure_migrations.py
└─ runtime.py
```

`Dnd2024Runtime` is a composition boundary, not a file that contains the entire implementation.

Current identity:

```text
runtime_id = core:dnd2024
runtime_version = 1
```

Capabilities include:

- professional character builder;
- rules-aware lifecycle;
- authoritative intents;
- deterministic combat;
- class feature runtime v1;
- versioned state;
- Session 0;
- coach;
- narrative turns;
- Adventure graph format.

---

## 25.2 Character Authority

The mechanics authority for an advanced D&D character is:

```text
ruleset_character
```

Legacy top-level fields such as:

```text
hp
class
race
level
attributes
skills
...
```

primarily exist for:

- generic UI;
- legacy compatibility;
- generic projections.

They must not be used to overwrite the canonical ruleset character arbitrarily.

---

## 25.3 Character Lifecycle

Professional character flow:

```mermaid
flowchart LR
    D[Draft]
    V[Validate]
    DER[Derive Canonical]
    F[Finalize]
    C[Ruleset Character]
    P[Legacy Projection]

    D --> V --> DER --> F --> C
    C --> P
```

Shared character cards, joining a game, editing, advancement, and rest all pass through the rules-aware lifecycle.

Profile edits cannot directly overwrite:

- abilities;
- HP;
- AC;
- progression history;
- runtime/content/state versions.

Mechanical changes must be revalidated from canonical choices/history.

The generic sheet write for classic (non rules-aware) rules, `PUT /api/games/{game}/character/{uid}`, separates authority the same way (`src/webui/character_sheet_authority.py`):

- the table GM, or the owner acting as itself, may change every editable field;
- a seated player, a bot acting for a player seat, and a P2P guest relayed by the host with `delegate=1` may only change profile fields (name, race, background, identity, portrait) and spend engine-granted `level_up_points` on attributes (increase only);
- HP, level, XP, gold/currency, resources, progression, attributes, skills, class, equipment/inventory/key items and rule special stats (sanity, luck...) are engine / GM authority; a player-side request that changes any of them is rejected as a whole with `403 FIELD_REQUIRES_GM` (listing `fields`), never partially applied;
- adopting a card goes through `POST .../adopt-card`: the server loads the card from the library by `card_id`; the request body carries no mechanical values.

---

## 25.4 Class Feature Runtime v1

The current class-feature boundary is:

```text
src/rulesets/dnd2024/features/
├─ models.py
├─ resolver.py
├─ combat.py
├─ equipment.py
└─ resources.py
```

This boundary answers only:

> **“Which class features, resources, and combat capabilities does this character currently have?”**

It is not a second character system and not a second combat engine.

Authority for acquiring class features still belongs to progression / the character lifecycle. The relationship is:

```text
canonical class + class level + progression history
        ↓
progression_catalog
        ↓
which features are acquired
        ↓
class_feature_catalog
        ↓
parameterize / label / capability projection
```

`class_feature_catalog` does not decide the character's level again, and it cannot bypass progression history to “grant” features by itself.

The class-feature runtime currently projects mainly:

```text
acquired feature IDs
feature scalar parameters
class resource current / max
current combat capabilities
whether equipment prerequisites are satisfied
```

Class resources continue to live in the existing:

```text
ruleset_character.resources.class
```

Creation, advancement, and rest recovery continue to use the existing character / advancement / resting authorities. Class features do **not** introduce:

```text
a second resource table
a second action economy
a second attack resolver
```

The combat side consumes only the capability IDs already projected, action/resource costs, target requirements, and the underlying canonical action. Neither the generic engine nor the frontend infers mechanics from “the class name.”

Equipment prerequisites are handled by:

```text
src/rulesets/dnd2024/features/equipment.py
```

which reads canonical `equipment.item_refs` and weapon profiles from the combat catalog, such as:

```text
weapon category
ranged / melee
canonical properties such as Light
```

When equipment changes, capabilities are re-projected through the existing reconciliation path. In other words, the D&D runtime answers whether a class feature is usable; frontend buttons and free text do not.

When advancement changes the maximum value of a class resource, behavior is controlled by the explicit rest-catalog:

```text
resize_policy
```

The current default is:

```text
preserve_spent
```

which preserves the semantics of already-spent resources. A resource may instead declare:

```text
preserve_current
```

which preserves legal current value while clamping overflow. Code must not contain hidden branches keyed to specific class/resource IDs.

This feature runtime is currently a **D&D 2024-specific domain boundary**. It does not mean a cross-Ruleset generic Class Feature Runtime already exists. If another professional ruleset later has its own class/talent system, it should first demonstrate genuinely shared primitives before anything is promoted upward.

---

# 26. D&D Campaign / Session 0

D&D campaign state and combat state both live under:

```text
GameInstance.ruleset_state
```

and evolve through versioned event batches.

---

## 26.1 Session 0 Consent

Every Session 0 revision:

```text
invalidates old consent
```

Only after all current members accept again:

```text
GM can lock
```

This prevents a situation where:

> the GM changes the setup, but the system still treats consent from the previous version as valid for the new one.

---

## 26.2 Campaign Facts

The following facts do not become campaign authority merely because the LLM said them once:

- tasks;
- clues;
- facts;
- important items;
- relationships.

The flow is:

```text
AI / player narrative
      ↓
pending proposal
      ↓
GM authoritative intent
      ↓
confirmed / rejected
```

---

# 27. D&D Combat

## 27.1 Events and Reducer

Combat does not update HP through LLM free text.

Flow:

```text
Intent
  ↓
validate
  ↓
resolve
  ↓
EventBatch
  ↓
combat reducer
  ↓
ruleset_state
```

Enemy automatic actions use the same chain:

```text
validate → resolve → events → reducer
```

not “the AI said the monster dealt 8 damage, so subtract 8.”

`combat/triggered_spells.py` owns spells added after a melee hit. `divine_smite`
uses a `melee_hit_spell` decision in the existing `pending_decisions` state:
the attack and base damage are rolled once; the server owns the target, critical,
and damage context. Casting or declining commits the damage and applicable
bonus action, spell slot or class use in one EventBatch, with one concentration
check for the attack's combined damage. Automation never answers a human's decision.
Feature/rest catalogs declare the level-two `paladins_smite` grant, one free use,
and Long Rest recovery. Lorebooks and LLMs cannot grant it. Combat spell projections
report unavailable reasons rather than hiding prepared spells with no legal cast.
Monster and NPC profiles optionally declare a canonical `creature_type`; encounters
preserve it, so extra damage against `fiend` / `undead` never depends on display names.

---

## 27.2 Encounter Access

Story encounters and sandbox encounters must be distinguished explicitly.

Priority:

```text
bound story encounter
       >
explicit sandbox
       >
unprepared
```

If the current Adventure node declares combat but has no valid canonical preset:

```text
unprepared
```

DiceFrame does not secretly substitute a generic training encounter.

---

## 27.3 Campaign / Combat Decoupling

The Campaign engine does not import the combat engine.

The combat engine does not import the campaign engine either.

The runtime composition root first projects:

```text
EncounterAccess
```

from story state, then injects it into combat.

This avoids:

```text
campaign <-> combat
```

bidirectional dependencies.

---

# 28. D&D AI Companions

AI companions are not NPCs that exist only inside prompt text.

Canonical companion state lives at:

```text
ruleset_state.party.companions[*].ruleset_character
```

They are not inserted into:

```text
instance.players
```

because `players` continues to represent real player identity/permissions.

---

## 28.1 Actor Identity

Unified actor references:

```text
player:<uid>
companion:<id>
enemy:<id>
```

This resolves questions such as:

- who may control whom;
- who performs a check;
- who appears in initiative;
- whose spell slot is spent;
- who receives a condition.

---

## 28.2 Automatic Actions

A companion's automatic turn is:

```text
server-owned automatic intent
    ↓
normal validation
    ↓
normal resolution
    ↓
normal reducer
```

A player cannot submit a forged “companion intent” to bypass ownership.

---

# 29. D&D Delegated Checks

A player may declare:

> “Have my companion push the door.”

The Planner tries to resolve:

```text
actor_ref = companion:...
```

The subsequent check uses the companion's canonical sheet.

This is different from “use the player's own Strength check, then narrate that the companion did it.”

If companion identity is ambiguous:

```text
fail closed
```

rather than selecting one arbitrarily.

---

# 30. D&D Exploration Spellcasting

Spellcasting outside combat is not a second spell system.

Entry point:

```text
exploration.cast_spell
```

The frontend shows it only when:

```text
available_intents
```

returns that intent.

It shares with combat:

- canonical known/prepared spells;
- spell slots;
- conditions;
- concentration;
- actor identity.

---

## 30.1 What Exploration Spellcasting May Do

Deterministic effects such as:

- heal;
- buff;
- resource spend;

are resolved by the server.

If a spell is:

> a damage spell that requires a hostile combat target

it is rejected in exploration context.

If the spell is known and legal and its resource cost can be spent, but its effect is narrative / not numerically deterministic:

```text
consume legal resource
    ↓
narrative resolution
```

The LLM may narrate the outcome, but cannot rewrite numerical mechanics by itself.

---

# 31. D&D Director and Temporary Encounters

## 31.1 Director

Director is an optional layer of the rules runtime:

```text
current campaign state
      ↓
read-only proposal
      ↓
assist / manual / auto policy
```

It does not mean the LLM may directly write campaign state.

---

## 31.2 Temporary Encounter Proposal

In free-form story play, if there is no formal Adventure encounter, AI may help generate a temporary encounter candidate.

The key point is that:

```text
plan_temporary_encounter
```

is a:

> **read-only proposal.**

It:

- does not directly write combat state;
- does not automatically start combat;
- may be previewed/edited by the GM;
- still passes through authoritative `combat.start` validation in the end.

A formally bound Adventure encounter has priority; a temporary proposal cannot forge a story encounter identity.

---

# 32. Single Narrative Timeline Principle

Even though D&D has:

- campaign tools;
- combat tools;
- adventures;
- companions;
- spells;

DiceFrame still maintains:

```text
ONE public timeline
ONE action composer
ONE round loop
```

DND5E tools are structured tool surfaces inside the same Play page.

They are not:

```text
story chat A
+
combat chat B
+
AI companion chat C
```

This avoids:

- history forks;
- inconsistent context;
- multiple authorities for “what is the latest scene.”

---

# 32A. WorldState: Current World Truth

This is another core authority merged into `main` on 2026-09-17.

Entry points:

```text
src/engine/world_state.py
src/engine/world_legality.py
src/engine/world_events.py
src/llm/world_prompt.py
```

`GameInstance.world_state` is:

> **the single authoritative container for current world truth.**

It still belongs to the `GameInstance` Aggregate. It is not a second standalone aggregate and has no standalone database.

---

## 32A.1 Current Structure

The first version is:

```text
world_state
├─ schema_version
├─ revision
├─ clock
├─ facts
└─ scheduled_events
```

### `facts`

Facts use canonical keys.

Examples:

```text
actor:p1.location
location:old_bridge.passable
ritual:clearing.status
npc:mayor.alive
```

Localized display names must not be used as identity.

Each fact also carries:

```text
visibility
source_round
updated_revision
```

Visibility values:

```text
public
gm
```

---

## 32A.2 Single Write Entry Point

```text
apply_world_ops(instance, ops)
```

Principle:

```text
validate the whole op batch first
↓
everything is valid
↓
commit atomically
```

The following fail closed:

```text
unknown op
unknown field
invalid canonical key
invalid scalar
bad schema
future schema
corrupted scheduled-event op
```

The LLM should not directly do:

```text
instance.world_state["facts"][...]=...
```

---

## 32A.3 Boundary Between WorldState and Other Containers

Current world truth must not be stored in:

```text
ruleset_state
key_facts
lorebook_timed_state
memory
```

These containers may provide information, but they cannot become substitute WorldState authorities.

---

# 32B. WorldState Visibility

Read entry point:

```text
project_visible_state(...)
```

Principle:

```text
World truth
≠
Player-visible truth
```

GM-only facts may enter GM-visible context only.

An ordinary player's projection receives only:

```text
public
```

content.

Do not dump the entire unfiltered `world_state` into player Q&A or an AI player just to “make the AI smarter.”

---

# 32C. World Action Legality

Entry point:

```text
src/engine/world_legality.py
```

The LLM / Planner may only propose:

```text
world_requirements
```

for example:

```text
act at location
move to destination
via location ids
```

Final legality is decided by the server using authoritative facts.

---

## 32C.1 Conservative Fail-Open on Unknown, Fail-Closed on Proven Contradiction

If world state explicitly records:

```text
location:old_bridge.passable = false
```

and the player declares a route through it, the action may be blocked.

If:

```text
WorldState is empty
the location is unknown
the actor's location is unknown
```

DiceFrame must not invent common-sense facts and block the action.

In other words:

```text
unknown ≠ false
```

This is especially important for AI-driven TRPG play.

---

## 32C.2 Writing Back a Legal Move

Only after a move passes authoritative legality checks may the server write:

```text
actor:<uid>.location = ...
```

A GM narrative sentence such as “you arrive at the church” is not allowed to directly change canonical location.

---

# 32D. Logical World Time and Scheduled Events

Entry point:

```text
src/engine/world_events.py
```

World time advances only through:

```text
advance_world_time(+N)
```

Flow:

```text
current logical clock
↓
advance to the new time
↓
stable sort by (day, minute, event_id)
↓
settle every due scheduled_event
↓
applied / failed
```

There is no:

```text
background real-time tick
separate world scheduler process
```

Therefore:

```text
page refresh
repeated save
process restart
```

will not automatically settle the same event twice.

---

## 32D.1 How Far Process Support Goes Today

The current system expresses events such as:

```text
poison takes effect in 20 minutes
ritual completes at 14:00
gate closes tomorrow morning
reinforcements arrive in two hours
```

well.

But it does not yet have a first-class:

```text
Process {
  id
  participants
  progress
  state
  transitions
  end_condition
}
```

Therefore:

```text
an NPC is traveling from A to B
search progress is 63%
fire keeps spreading
a long-running faction war escalates over time
```

are not yet modeled by a generic Process Engine.

Developers must not claim Entity / Relation / Process is “complete” merely because `scheduled_events` exists.

---

# 32E. Future Extension Boundary for Entity / Relation / Process

If DiceFrame later adds:

```text
Entity
Relation
Long-running Process
```

it should continue evolving:

```text
GameInstance.world_state
```

as the foundation of current-world authority.

Do not introduce parallel authorities such as:

```text
WorldState2
EntityStateDB
LLMWorldTruth
```

---

# 33. Lorebook / World / Memory

## 33.1 World and Lorebook

Lorebook is prompt knowledge/retrieval, not current-truth authority. Current truth is
owned by `WorldState`, history by `Memory`, and rule interpretation/rulings by the
Ruleset Runtime. Persistence ownership is:

```text
worlds                 world identity / compatibility
lorebooks              canonical book settings
lorebook_bindings      scope / role bindings
lorebook_entries       book entries
lorebook_embeddings    derived semantic cache
```

External material follows one path:

```text
External Lore → Adapter → Draft/Preview → Canonical Store
→ Binding Resolver → Activation/Keyword/Semantic → Visibility → Budget → Prompt Projection
```

The current product import path is:

```text
LorebookView
→ POST /api/lorebooks/import/preview
→ user confirmation
→ POST /api/lorebooks/import
→ GET/POST /api/lorebooks/{book_id}/entries
→ GET /api/lorebooks/{book_id}/export
→ LorebookStore
```

Book and Binding lifecycles are owned by the same product path: explicit CRUD is
available through POST/PUT/DELETE `/api/lorebooks`, plus
`/api/lorebooks/{book_id}/bindings` and `/api/lorebook-bindings/{binding_id}`. Export uses the
canonical `lorebook_v3` serializer and includes a DiceFrame native backup when requested.
The browser Golden covers import preview/confirmation, Book selection, Activation
Inspector, GM/player-safe views, and the export request contract.

The canonical `lorebook_bindings.scope_kind` values are only `global`, `world`,
`game`, and `character`; the old `viewer` / `actor` names are not part of the new
contract. The resolver merges all books bound to the active runtime context in binding
order. Imported entries receive a book-scoped internal canonical ID; external `uid` /
`id` values are provenance only and cannot overwrite entries in another book. Recreating
a book ID does not use SQLite `REPLACE` cascading away existing bindings or entries.

Content-library links target the canonical `book.id`; a `world` query parameter is
included only for a world in the loaded catalogue. Historical Books may retain a
binding to an unavailable world. They can be browsed, edited and exported by Book
identity with an unavailable-world notice. Cached links naming a missing world and
an existing book still open that book; a missing world alone remains rejected.
Browsing does not create worlds, rewrite bindings or inherit the active game's
world. An entry's compatibility `world_id` projects an existing primary world
binding only, otherwise NULL; canonical `book_id` ownership and primary-book
deletion protection remain effective.

Owner-only `GET /api/lorebooks/usage?game_key=...&viewer=...` exposes read-only
usage statistics. `services/lorebook_usage.py` takes explicit store/instance
dependencies. Library counts use Book `enabled`, including unbound books; game
sources use runtime `resolve_active_books` and the current GM/party/character
perspective, respecting explicit selection, enabled bindings and deduplication.
Counts represent books, with entry counts shown separately; a selected source
does not imply every entry activated this round. The response contains only Book
identity, name, enabled state, scope and entry count, never entry bodies. No
selected game and unavailable game return `none` / `unavailable` with `count: null`,
not a fabricated zero. The Lorebook page shows expandable library/game lists;
browsing another world leaves the game's runtime source unchanged, and enable or
binding mutations refresh the projection.

After starting a game, GM Workbench → Scene → Game lorebooks reads owner-only
`GET /api/games/{game_key}/lorebooks`. `services/game_lorebooks.py` takes explicit
store/get_game/catalog/save dependencies and replaces up to 16 editable game
sources while preserving character sources and other scopes. Canonical game
bindings, portable `content_binding.book_refs` and the explicit selection marker
are saved together through a journaled save saga. Server plans and confirmations
use the Agent approval boundary without requiring an LLM. Judgment/in-flight
rounds reject changes; guards are rechecked after waiting for the game lock.
State and binding fences after asynchronous persistence prevent compensation
from overwriting a newer run, round, phase or actor state. Uncertain results
retain prepared evidence and cannot be blindly replayed. Disk reload preserves
the game selection.

Owning `lorebook/operations.py` stores private `lorebook_operations` records.
`services/content_recovery.py` commits entry edits, enable changes and bounded
archives with before/after snapshots in one SQLite transaction. Restoration
preserves canonical identities, visibility and source metadata, rejecting ID
collisions and later edits. `content_metadata_recovery.py` journals narrative
world/card edits as prepared → owning file write → applied; it does not claim
cross-file/database atomicity. Models and clients receive only operation metadata,
not recovery snapshots. Content → Lorebooks → Content recovery uses the same
approval boundary for new recorded operations only; mechanics, confirmed story
and save rounds are not rewound. Agent has no permanent purge. Archives are
limited to 10 explicit authored entries or an unreferenced non-primary book with
at most 10 entries; builtins, plugin sources and whole primary books are protected.
Named plugin tools require explicit `agent_effect=read_only/mutation/destructive`.
All three native-process classes require concrete manual approval; unknown effects
are not registered. The legacy generic entry remains read-only. Declarations do not
sandbox native processes, and plugin side effects have no generic undo. See
`docs/internal/design/DF_AGENT_DELETE_POLICY.md` for precise limits.

The v4 → v5 → v6 migrations preserve worlds and entry IDs, and create a deterministic
`world:<id>` primary book for every world. `list_entries(world_id)` remains the
compatibility façade for that primary book.

Builtin startup initialization includes zero-entry worlds such as
`freeform_sandbox`, creating their world identity, empty primary Book and primary
binding without fabricated entries. Repeated initialization preserves authored
entries. The gallery's Edit lorebooks entry directly opens existing local worlds;
content-pack worlds and templates without local storage use an explicit Create a
copy and edit lorebooks action through the existing clone boundary. Catalog
browsing never autoimports content packs or changes the active save's world.

World core owns:

- world identity;
- default/recommended rules;
- setting;
- starter scene;
- starter lore entry identities;
- deterministic lore metadata.

The Worlds page's Preview / Edit world entry exposes the complete background and
default opening, with a separate short introduction.
The world gallery and Lorebook page's World actions → New world / Edit world share
the World feature's `WorldDefinitionEditor`. Creation and editing both expose the
name, content language, introduction, full background and default opening.
`POST /api/worlds` saves independent background/opening fields at creation; explicit
empty strings stay empty, while older clients omitting them retain initialization
from the introduction. The Lorebook entry expands the full form in place.
Cloning selects the copied world
for browsing and editing, without changing the active save's world or runtime.
Owner-only
`GET /api/worlds/{world_id}/definition` is a bounded projection of the name,
introduction, background, opening, language and source/editability, never the raw
template or lore entries. User worlds return authored canonical text; built-in
and plugin worlds may use read-only locale text. GET never materializes or writes
templates. Built-in/plugin worlds must be cloned before editing.
`PUT /api/worlds/{world_id}` updates `world_setting` and `starter_scene`
independently: omitted fields are preserved and empty strings clear them. Editing
the introduction preserves the complete background. Updates retain canonical
identity, rules, lore entries, artwork and GM style. New defaults apply to games
created later; existing games keep their already loaded runtime content.

The world core fixes canonical entry semantics such as:

```text
id
type
tier
unreliable
sync_on_enter
triggers_recursive
visible_to
is_constant
match_mode
sticky
cooldown
delay
order
probability
group
group_weight
connected_to
```

Locale owns only display/language text such as:

```text
name
keywords
content
```

The shared `lorebook.db` stores canonical/core entries. Each game materializes a read-only locale view according to:

```text
instance.language
```

Changing language must not add, remove, or rename canonical lore identities.

---

## 33.2 Hybrid Lore Retrieval

All Rulesets that use the standard narrative pipeline currently share:

```text
src/lorebook/retrieval.py
LoreRetriever
```

It is not a D&D-, CoC-, or Freeform-specific implementation. Normal rounds, historical swipes, and out-of-character GM/KP Q&A all use the same retriever; only the following differ:

```text
viewer scope
timer mutation policy
```

The first version of retrieval does not look only at the player's raw text for the current round. Its query is built from:

```text
current actions_text
+
instance.scene
+
canonical location from WorldState
+
NPCs actually present in the current scene
```

By default it does **not** scan multi-round history, which avoids repeatedly activating sticky / cooldown / delay behavior from stale keywords.

To prevent structural labels from contaminating keyword retrieval, the retriever constructs two queries:

```text
lexical_query
  = only the actual values of action / scene / location / NPC
  → KeywordMatcher

semantic_query
  = labeled with [action] / [scene] / [location] / [present_npcs]
  → EmbeddingClient
```

Therefore, if an English Lore entry happens to use `scene`, `location`, or `action` as a keyword, it is not falsely matched every round merely because those labels exist in the semantic query.

The existing `KeywordMatcher` remains the base retrieval system and continues to own:

```text
keyword / regex
any / all / not_any / not_all
constant
recursive trigger
sticky
cooldown
delay
probability
group / group_weight
tier / order
```

Semantic retrieval is only an optional enhancement. It does not replace those semantics.

Lorebook v2 additionally implements secondary keys and ST-style selective logic,
case-sensitive and whole-word matching, entry-level regex, recursive content scanning
with depth and non-recursable guards, and multi-name group competition. These mechanics
remain inside `KeywordMatcher`; the generic retriever only orchestrates loading and
projection.

Three concepts here are stored separately and never derived from each other: legacy
DiceFrame `match_mode` governs primary matching; the canonical `selective` boolean decides
whether `secondary_keys` gates activation at all; `selective_logic` decides how the
secondary keys combine. `selective=false` keeps the keys as data without filtering, and a
missed primary key means the secondary gate is never consulted. SillyTavern and Character
Card regexes are JavaScript while DiceFrame executes Python `re`, so only the
safely-mappable subset runs: an incompatible pattern is preserved verbatim with a preview
warning and is never evaluated through a second runtime.

At runtime the resolver merges global/world/game/character bindings and attaches book
settings (scan depth, recursive scanning, token budget, and the vector default) to each
candidate. A Book's own `enabled` flag is a runtime decision, not a display label: a
disabled Book contributes no candidates at all, and a change to its retrieval settings
bumps `revision` so two same-second edits cannot be swallowed by the cache. Entry
ownership follows the canonical invariant — entries of the primary world book
`world:<id>` carry `world_id`, entries of an independent Book have NULL, and moving an
entry between books re-derives that projection. `off` produces no semantic candidates, `hybrid` runs alongside keywords, and
`vector_only` admits semantic candidates only; every candidate still passes visibility,
timer, group, and budget checks. A dry-run ActivationTrace is kept on the runtime
instance and is queryable through `POST /api/lorebooks/activation-preview`; player
views fail closed for hidden entries and do not disclose their id, name, or reason.

Legacy world projections retain the old fuzzy default; `lorebook_v3`, SillyTavern, and
native Books default fuzzy matching off unless Book settings explicitly enable it. The
fuzzy fallback still honors secondary-key, case, whole-word, and regex boundaries and
cannot bypass the new matcher contract.

---

## 33.3 Embeddings and Derived Cache

Lorebook semantic retrieval reuses the same client already used by long-term memory:

```text
MemoryStore.embedding_client
```

`GameHandler` provides that same client to `LoreRetriever` through a lazy provider. There is therefore no second:

```text
LoreEmbeddingClient
LoreEmbeddingProvider
LoreEmbeddingSettings
```

Configuration remains unified:

```text
embedding_enabled
embedding_provider_ref
embedding_model
embedding_max_input
```

When embeddings are not configured, or provider / query / batch calls fail:

```text
semantic skipped
        ↓
scene/location/NPC anchors + KeywordMatcher continue
        ↓
round continues
```

Embedding failure **must not** fail a normal round. Bad data such as non-numeric values, empty vectors, `NaN`, `Inf`, or dimension mismatch is also handled fail-soft.

Lorebook entry vectors are cached in `lorebook.db`:

```text
lorebook_embeddings
```

Current Lorebook SQLite `user_version = 10`. `vector_activation` is the three-state text field `off` / `hybrid` / `vector_only`; v6 safely converts the old v5 boolean values while preserving `book_id` and entry data. v7 adds `lorebooks.revision`, a monotonic counter bumped by entry and retrieval-setting mutations so cached matcher fingerprints are invalidated; v8 adds `lorebook_entries.selective`, the canonical flag deciding whether `secondary_keys` gates activation at all (default `1` keeps existing behaviour); v9 adds `lorebook_entries.regex_executable`, which an adapter clears when a JavaScript regex has no faithful Python equivalent so the matcher never executes it (default `1`). v10 adds owner-private lorebook_operations journal records without rewriting content or bindings; recovery snapshots are not embedding cache data. Cache key:

```text
(entry_id, language, embedding_profile)
```

Stored fields include:

```text
content_hash
embedding
updated_at
```

`embedding_profile` binds the model / endpoint identity / max input and never contains the API key. `content_hash` is computed from the actual text sent for embedding:

```text
name
type
keywords
content
```

Content changes, language changes, or model/endpoint changes naturally produce a cache miss and lazy batch rebuild.

This table is:

> **a pure derived cache, not world-knowledge authority.**

Deleting the whole table does not destroy knowledge; the system can rebuild it. Cache rows are also cleaned when entries, worlds, or plugin content are deleted.

DiceFrame currently does not introduce:

```text
FAISS
Chroma
Qdrant
Milvus
pgvector
```

At Lorebook scale it still uses SQLite + Python cosine similarity.

---

## 33.4 Hybrid Merge / Visibility / Timer Boundary

When embeddings are available, Keyword and semantic retrieval may both provide candidates in the same round.

Final results are deduplicated by canonical entry ID and stably sorted by:

```text
tier
↓
order
↓
source priority
↓
semantic score
↓
id
```

`core / background / archived` and entry `order` always outrank “was this a keyword hit or a semantic hit?” Source priority is only a tie-breaker when tier and order are equal, preventing low-priority lore from consuming the Lore budget before higher-priority lore.

A pure semantic hit means only:

> **“this existing entry may be relevant.”**

It does not mean:

> “this event just happened in the current world.”

Therefore a semantic hit:

```text
does not write WorldState
does not write Memory
does not change Ruleset state
does not trigger a scheduled event
does not decide combat / checks / economy outcomes
```

For player-view retrieval, `visible_to` filtering happens **before** semantic ranking so a GM-only entry cannot enter the player-safe candidate set just because its vector is similar.

Existing sticky / cooldown / delay semantics in `lorebook_timed_state` remain controlled by KeywordMatcher. A pure semantic candidate does not secretly advance timers. Out-of-character Q&A uses a copy of timed state and therefore does not mutate the real timer state.

At the `GameStateCodec` save/load boundary, legacy `status/remaining` timers are
normalized into independent `sticky_remaining`, `cooldown_remaining`, and
`delay_remaining` counters. Loading an old save does not require a database downgrade.
`GameInstance.update_lorebook_timed_state()` accepts both shapes while later saves
converge to the canonical representation.

---

## 33.5 Lore Prompt Projection and Authority

Lore in GM context is no longer projected only as weakly structured text such as:

```text
【World Knowledge】
[type] name: content
```

The current projection is:

```text
【Currently Relevant World Lore】

[id=...][type=...][tier=...][unreliable?]
name:
content
```

and explicitly tells the model:

```text
WorldState / system rulings / Ruleset Runtime
        >
Lorebook explicit canon
        >
history / memory
        >
LLM improvisation
```

Here `>` means authority / precedence for current truth. It does **not** mean “the Lorebook is a script that must be followed.”

The actual constraints are:

```text
Lorebook constrains only facts it explicitly states
Unstated space remains open and may be improvised reasonably
Lorebook is not a plot script and does not require players to follow a fixed route
Subsequent changes caused by players/system follow current WorldState
unreliable means rumor / NPC belief / subjective claim, not automatically objective truth
```

For example, if the Lorebook says an old bridge was initially passable, but during play WorldState has recorded:

```text
location:old_bridge.passable = false
```

then WorldState defines the current fact.

The goal of stronger Lorebook retrieval is therefore:

```text
reduce forgetting / setting contradictions
```

not:

```text
reduce player freedom
```

Full GM context may retain canonical Lore entry IDs for consistency and diagnostics. Player-safe Q&A does not expose internal IDs and keeps only:

```text
type
tier
unreliable
name
content
```

so internal naming does not itself spoil hidden information.

Lore still obeys the existing token/character budget. `context_builder` logs which entry IDs were ultimately injected and which were trimmed for budget. Combined with retriever keyword / semantic / final debug information, this lets developers distinguish:

```text
not retrieved
retrieved but trimmed by budget
retrieved and injected, but the model did not follow it
```

---

## 33.6 Long-Term Memory

Memory and Lorebook are not the same thing.

### Lorebook

Primarily stores “intrinsic world setting / structured world knowledge.”

### Memory

Primarily stores “what actually happened in this particular game.”

Memory may include:

- embeddings;
- deltas;
- namespaces;
- economy outbox delivery.

Hybrid Lore Retrieval reusing the same `EmbeddingClient` does not merge the two stores. Lorebook remains in `lorebook.db`; long-term memory remains in `memory.db`; their authority and lifecycles remain separate.

---

### Actual Current State of Confirmed Event → World Memory

Current `main` already has:

```text
ordinary memory_delta → MemoryStore
durable memory outbox
rollback reversal
some authoritative D&D EventBatch → memory projection
```

But there is not yet a generic manager for:

```text
all Confirmed Events
↓
unified long-term-value filtering
↓
World Memory
```

Therefore do not treat:

```text
confirmed_items
```

as the same concept as:

```text
MemoryStore long-term memory
```

The former is closer to “confirmed items that should not be repeatedly debated in current/future context.” The latter is an independent long-term storage and retrieval system.

## 33.7 Namespace

Long-term memory is isolated by:

```text
memory_namespace
```

Restart/reset rotates the namespace.

# 34. Content V2

## 34.1 Canonical Identity

For example:

```text
fighter
longsword
athletics
npc_innkeeper
```

are identities.

```text
战士
Fighter
ファイター
```

are display text.

Translated names must not be used as:

- save keys;
- rules references;
- internal join keys.

---

## 34.2 Compatibility Pipeline

```text
Legacy / V1
      ↓
Compatibility Adapter
      ↓
Canonical Current Model
      ↓
Runtime Mechanics
      ↓
Typed Locale
      ↓
UI
```

Support for old shapes should be centralized at the entry adapter.

Business code should not be filled everywhere with:

```python
if old_field:
elif new_field:
elif another_old_field:
```

---

# 35. Rule Locale and World Locale

## 35.1 Rule Locale

Rule locale may translate:

- ability names;
- skill names;
- item display text;
- descriptions.

It must not replace:

- damage formulas;
- skill pools;
- permissions;
- capabilities;
- combat model;
- mechanics categories.

---

## 35.2 World Locale

World locale may modify only:

- world name;
- description;
- setting;
- starter scene;
- Lore `name/keywords/content`.

It must not:

- add or remove Lore entries;
- rename canonical entry identity;
- change deterministic trigger/configuration semantics.

---

# 36. Plugin Architecture

## 36.1 Plugin Host

Primary boundary:

```text
src/plugin_host/
├─ host.py
├─ support.py
├─ descriptors.py
├─ capabilities.py
├─ content.py
├─ contracts.py
├─ marketplace.py
├─ mirrors.py
└─ ...
```

---

## 36.2 Plugin Types

The current single descriptor source in `support.py` includes:

| Type | Mode |
|---|---|
| content-pack | static |
| theme | static |
| voice-pack | static |
| channel-adapter | plain subprocess |
| provider | JSON-RPC provider process |
| tool | JSON-RPC tool process |
| bot-extension | JSON-RPC bridge process |
| import-export | reserved |

---

## 36.3 Process Boundary

```mermaid
flowchart LR
    HOST[PluginHost]

    subgraph Static[Static]
      C[Content Pack]
      T[Theme]
      V[Voice Pack]
    end

    subgraph Proc[Plugin Processes]
      CA[Channel Adapter]
      TOOL[RPC Tool]
      PROV[RPC Provider]
      BOT[RPC Bot Extension]
    end

    HOST --> C
    HOST --> T
    HOST --> V

    HOST <--> CA
    HOST <--> TOOL
    HOST <--> PROV
    HOST <--> BOT
```

A plugin process is not equivalent to arbitrary in-process Python imports.

RPC plugins go through:

- descriptor validation;
- capability initialization;
- permission checks;
- host lifecycle.

---

## 36.4 Plugin Type Descriptor

Plugin-type properties such as:

- support level;
- process mode;
- inferred permissions;
- required permission;
- contribution mapping;
- cleanup semantics;

are centralized in `support.py`.

Adding a plugin type should not mean:

```text
add an if to host.py
add another if to the frontend
add another if to permissions
add another if to the registry
```

Instead, extend the descriptor and the corresponding runtime initializer.

---

# 37. Bot / Channel Adapter Boundary

A Bot is not an independent game engine.

A Channel Adapter is responsible for:

```text
Platform message
      ↓
normalize actor/game
      ↓
DiceFrame HTTP API
      ↓
same service / GameInstance path
```

Therefore:

- Web players;
- QQ / Telegram / other channels;
- plugin transports

should ultimately enter the same authority path.

---

## 37.1 Bot Authentication

Bot requests may authenticate with:

- a global bot token;
- a plugin-specific API token.

If the Bot represents a player, DiceFrame also needs:

- game identity;
- `X-Bot-Actor`;
- actor allowed;
- player access open.

A Bot token proves only:

> “This request came from an authorized Bot/Plugin.”

It does not prove:

> “It may do anything on behalf of any player.”

---

## 37.2 Bot Card Rendering

PNG cards rendered by `src/bots/bridge_core/card_renderer.py` are presentation-side effects.

CJK fonts must pass a real glyph check.

If no suitable font is found:

```text
render fails
    ↓
caller degrades to plain text
```

DiceFrame must never fall back to Pillow's default font without CJK glyphs and produce a screen full of `□□□□`.

This illustrates an important rule:

> Card-rendering failure must not become a game-command failure.

---

# 38. Web Access / Security

## 38.1 Owner

The Owner enters administrative capabilities using an access password / bearer credential.

---

## 38.2 Player Share

Player sharing does not mean handing the Owner token to players.

Access resolves:

- game;
- share user;
- player-access state;
- room password/token;
- endpoint allowlist.

When the Owner previews the player view, viewer identity and acting-player identity remain distinct.

---

## 38.3 Room Password

If a game has a room password:

```text
verify password
   ↓
obtain room_token
   ↓
player API
```

instead of allowing every endpoint to accept the administrative access token directly.

Storage and lifetime (`room_access` slot schema 3):

- the room password is stored only as a salted PBKDF2 hash (same format as the owner access password); the GM can set, replace or clear it but never read it back. New passwords need at least 6 characters; hashes of older, shorter passwords keep working;
- each successful password entry mints its own room token; only its SHA-256 digest and an expiry are stored (default 30 days, `TRPG_ROOM_TOKEN_TTL_DAYS`, max 365). Changing or clearing the password revokes every room token;
- removing a player does **not** rotate room tokens: that would log every other player out. A removed player loses the seat (its seat credential is revoked); the room token only gates the lobby, and the GM changes the password to shut a former player out of the lobby too;
- passwords are never trimmed (create, change or join); a whitespace-only password is refused;
- the room token travels only in the `X-Room-Token` header, never in a URL (SSE authenticates with its one-time ticket). Access logs additionally redact `room_token`, `ticket`, `seat` and `seat_token` query values;
- password attempts are limited per IP **and** (canonical) game with a token bucket: 5 attempts, then one more every 30 s. Only attempts the verify handler marks as a wrong password take a token; correct entries and other errors are free. Attempts for one IP + game run one at a time, so parallel guesses cannot all pass the check before a failure is recorded. The wait comes from the token deficit alone (refused attempts take nothing and never push the next slot back) and never exceeds 30 s — a slow-down, not a lockout. There is no cross-game per-IP cap, so failures on one game never affect another. Players sharing an IP (tunnel, proxy, NAT) share a bucket: the scheme guarantees a bounded wait, not separation within one address — a griefer behind the same IP can race legitimate players for each refilled token. A per-session bucket (in addition to the per-address one) is a possible future step. The body is read (10 s limit) before queueing for the per-IP+game lock and the lock wait is bounded (10 s), so a stalled upload cannot hold the lock. The client identity is the direct peer address, IPv6 keyed by its /64 prefix (in every abuse-guard bucket) so rotating addresses inside one /64 gains nothing; `X-Forwarded-For` is not trusted, as there is no trusted-proxy configuration;
- a token is only issued if the password hash is unchanged after the (off-loop) check, so a password change during verification never yields a token;
- loading a save with plaintext room credentials (schema < 38) rewrites `state.json` and `state.backup.json` immediately with hashes;
- staged commits (`replace_persisted_state_from`) never overwrite the live `room_access` slot;
- exports and imports never carry the password hash, room tokens, seat credentials or the bot bind token; a password-protected save arrives with its player entrance closed until the GM sets a new password and reopens it.

---

## 38.4 SSE Ticket

SSE supports one-time tickets:

```text
obtain ticket
    ↓
GET SSE?ticket=...
    ↓
consume
```

Invalid or expired tickets are rejected.

This prevents a higher-privilege credential from being exposed long-term in an EventSource URL.

---

## 38.5 Owner Device Tokens and QR Pairing

Current Owner access has two peer credential types:

```text
access password
device token
```

Access password:

```text
only a PBKDF2 hash is stored
the server never holds the plaintext
```

Device token:

```text
high-entropy random token
only a SHA-256 digest is persisted
revocable per device
```

### Pairing Entry Points

Backend:

```text
src/webui/pairing.py
src/webui/routes/pairing.py
src/webui/device_tokens.py
```

Approximate flow:

```text
logged-in Owner
↓
POST /api/pairing
↓
generate one-time short-TTL pairing code
↓
phone anonymously POSTs /api/pairing/claim
↓
exchange for device token
↓
use as Authorization: Bearer from then on
```

`claim` happens before the device owns any credential, so it must be anonymously reachable. Its security comes from:

```text
short TTL
one-time use
digest storage
abuse guard
login audit
```

Do not simply place `/pairing/claim` behind “must already be logged in as Owner” middleware, or QR sign-in would deadlock itself.

### Frontend Entry Points

Relevant components:

```text
frontend-v2/src/components/DevicePairingButton.vue
frontend-v2/src/features/admin/settings/DevicePairingModal.vue
frontend-v2/src/features/admin/settings/DevicePairingPanel.vue
frontend-v2/src/components/common/QrCode.vue
```

QR is only transport / UX. Final authority remains in access control and the pairing service.

---

# 39. Generated Images / Scene Media

## 39.1 Separate Assets From Game State

The image backend stores independent assets.

`GameInstance` stores only:

```text
ImageReference
asset_id
```

rather than embedding base64 image data in the save.

---

## 39.2 Scene Image and Map Background Are Separate

These are distinct concepts:

```text
scene_image      visual for the current narration / round
map_background   map background
```

Generating a scene image does not automatically replace the map.

The save card's Save actions → Change cover entry uses `GameSceneImageDialog`,
reusing the GM workbench's `AdventureSceneImagePicker` and existing
`POST /api/games/{game_key}/scene-image` boundary. It supports PNG/JPEG/WebP
uploads (8 MB limit) and restoring the content default, with server-side GM
authorization preserved. The dialog captures the selected canonical game key,
retains drafts on failure and prevents duplicate submission or dismissal during
a write. Success refreshes the archive index and image references; overview
caches follow reference changes. It does not switch the active game or change
world templates or round state. Late default previews release their blob URLs.

---

## 39.3 Manual Current-Round Generation

When the GM manually generates an image for the current round:

```text
read target narration
   ↓
generate image
   ↓
re-fetch current instance
   ↓
verify run_id unchanged
   ↓
verify target round still exists
   ↓
attach reference
   ↓
save
```

If, meanwhile, a:

- restart;
- reset;
- rollback

occurs, the old image is not written into the new run.

---

## 39.4 Save Failure

If the image provider succeeds but:

```text
save_instance failed
```

the service restores the original scene-image references.

This avoids a half-commit where:

```text
memory says new asset
disk save still contains old state
```

---

# 40. Narrative Presentation Controls

Current per-game persisted controls include:

- `play_mode`
- `narrative_perspective`
- `gm_style_override`

---

## 40.1 `play_mode`

DiceFrame explicitly distinguishes:

```text
free
adventure
```

instead of repeatedly guessing the current play mode from “whether `adventure_binding` exists.”

---

## 40.2 GM Style

A World may provide:

```text
gm_style
```

`GameInstance` may hold:

```text
None  → follow world
dict  → explicit override
```

The final prompt renders one effective style only.

GM style may affect only:

- tone;
- verbosity;
- pace;
- custom narration instructions.

It must not affect mechanics.

---

# 41. Frontend Capability Gating

The play view places the `AdventurePanel` trigger beside Checks in the scene header. Its contents use the shared `Modal` teleported to the document body: closing it leaves no panel space in chat, and opening it does not resize the timeline or composer. Adventure projection and node actions retain their existing game-scoped permissions and server endpoints.

Adventure v2 may receive an `ADVENTURE_COMPLETE` suggestion from the GM prompt. After the round log is committed, the server validates the active node, bounded reason, and cited existing GM-log rounds before storing it in Adventure runtime progress; the suggestion never advances a node or runs outcomes. Suggestions and evidence are projected to the GM only. In AdventurePanel the GM can review a suggestion, close its transient notice, or dismiss it; dismissal suppresses repeat suggestions for that node. The node still advances only through the existing GM-authorized `adventure.node.complete` command. Node completion clears its suggestion in the authoritative transaction, and failures restore it with the progress snapshot.

On desktop and mobile the GM workbench closes through its explicit toggle or Escape; outside clicks and nested dialogs leave it open, and unfinished inputs stay mounted. Invite player and the GM-only Room password entry sit together below party members. `RoomPasswordDialog` owns password creation, replacement and explicit removal using the existing server validation and authorization endpoint. Game settings submit only edited reward and away policies and no longer own password fields or password writes.

Version checks, update channels, downloads and installation belong to Settings → About; only the retained-version rollback entry belongs to General → System maintenance. Search and update-notification deep links target the corresponding locations. Rollback retains the existing confirmation and launcher flow.

The frontend should not do:

```ts
if (ruleId === "dnd") showSpellButton()
```

The correct pattern is:

```text
backend ruleset metadata
available_intents
capabilities
        ↓
frontend render
```

For example, the out-of-combat spellcasting button appears only when:

```text
exploration.cast_spell
```

is present in the server-provided available intents.

This means:

- a custom D&D-like ruleset does not need a `rule_id` special case;
- the runtime can expand capabilities incrementally;
- the client does not become the owner of mechanics detection.

---

# 42. Update / Deployment Architecture

DiceFrame currently supports:

```text
Source
Windows Portable
Managed Docker
```

The download/update state machine may be shared, but authority over “who commits an installation” differs.

---

## 42.1 Source

Updates use a local backup transaction.

---

## 42.2 Windows Portable

The Windows launcher controls the switch to a candidate version.

The running application itself must not pretend to perform the launcher's atomic version switch.

When `current.json` contains both verifiable `relative_dir` and `previous_relative_dir` values, the application may write a restart signal with `operation=rollback`. The launcher then runs the previous payload through the same health, probation, and commit flow. A successful switch exchanges the current and previous pointers while retaining both payloads; the application does not edit the pointer or delete `data/`.

---

## 42.3 Managed Docker

The Docker application process:

- does not mount the Docker socket;
- does not directly control the Docker daemon;
- does not overwrite the current version directory.

It may only write:

```text
restart signal + relative candidate path
```

The stable `docker_launcher` is responsible for:

- health checks;
- probation;
- commit;
- rollback.

Manual rollback shares the restart signal and launcher flow with updates and may target only a verifiable previous payload from `current.json`. A Docker payload must satisfy `data_rollback_safe=true` before the old application is pointed at the shared data directory.

---

# 43. Runtime Logging and Diagnostics

The centralized owner is:

```text
src/runtime_logging.py
```

Defaults:

- Portable: `<install>/logs`
- Docker: `data/logs`
- retention: 30 days

Managed Docker keeps logs in the persistent host data directory identified by `TRPG_DOCKER_DATA_HOME`, falling back to the supplied data directory when unset. Writing, status, clearing, export, and Assistant diagnosis share the same directory resolver across business-data version switches. Explicit `TRPG_LOG_DIR` and `TRPG_INSTALL_ROOT` retain their existing precedence.

Business services should not each invent their own log-retention policy.

---
## 43.1 DF Assistant Log Diagnosis

Only when the Owner explicitly requests it does:

```text
runtime_diagnostics.py
```

read DiceFrame's two most recent log files.

Local preprocessing includes:

- credential redaction;
- successful poll filtering;
- duplicate compaction;
- bounded context.

At most about 24,000 characters are sent to the model.

Logs are always treated as data, never as instructions.

---

# 44. Windows Portable Native Crash Forensics

The Windows launcher currently provides native crash-evidence capture for bundled `python.exe`.

During runtime it temporarily configures the current user's:

```text
HKCU\
Software\Microsoft\Windows\Windows Error Reporting\
LocalDumps\python.exe
```

using:

```text
MiniDump
DumpType = 1
Max dumps = 3
```

Output location:

```text
logs/crash-dumps/
```

An abnormal exit also creates:

```text
logs/last-crash.json
```

containing minimal crash metadata.

---

## 44.1 What This Capability Is Not

It is not:

- a root-cause classifier;
- an automatic crash-dump uploader;
- a mechanism that automatically reads user chats;
- a mechanism that automatically sends characters / Lorebook data;
- a diagnostic that says “this exit code definitely means this bug.”

Its responsibility is simply:

> **Turn a native crash from “the window flashed and disappeared” into analyzable evidence.**

---

# 45. Failure Model

DiceFrame's failure handling is not a universal “catch Exception and continue.”

Different boundaries use different strategies:

| Failure | Strategy |
|---|---|
| Unknown persisted future schema | fail closed |
| Unknown ruleset runtime | fail closed |
| Invalid ruleset minimum version | fail closed |
| Adventure digest changed | fail closed |
| Locale mechanics override | fail closed |
| LLM narration failure | roll back current judgment where possible |
| Stale LLM response | discard |
| Image generation failure | degrade / do not roll back mechanics |
| Bot card font missing | degrade to text |
| Memory receipt missing after external success | retry idempotently |
| One listener bind failure | keep other valid listeners + warn |
| Runtime hot-reload candidate build failure | keep old runtime |
| Plugin provider failure | capability-specific failure/degrade |
| Native Python crash | launcher records evidence; process dies |

---

# 46. Rollback Model

DiceFrame has at least three kinds of “undo,” and they must not all be called the same rollback.

## 46.1 Current Judgment Abort

```text
The current round has not committed successfully yet
```

Restore the snapshot from before entering judgment.

---

## 46.2 Historical Swipe

```text
The historical round already exists
```

Restore snapshot + cut branch + regenerate.

---

## 46.3 Economy Whole-Round Rollback

Rolling back to round N means:

> undo the related **settlement era from N onward**.

This cannot look only at:

```text
proposal.origin_round == N
```

because:

- a proposal may be created in round 3;
- payment may not happen until round 5;
- rolling back round 4 must still reverse the round-5 settlement.

---

# 47. Architecture Guardrails / Tests

The repository does not rely on documentation alone to enforce architecture.

It has at least AST-level dependency tests.

Core guards:

```text
core domains !-> webui
core domains !-> dnd2024
rulesets !-> webui
rulesets !-> compat
dnd combat !-> campaign
dnd combat !-> webui
adventures !-> dnd2024
adventures !-> webui
web_transport !-> business
business !-> web_transport
```

The tests parse real import ASTs, including:

- normal imports;
- delayed imports inside functions;
- relative imports.

Changing a comment therefore does not cause a false positive, while a real dependency change is detected.

---

# 48. Data Ownership Matrix

| Data / state | Authoritative Owner | Persistence | Read Projection | Mutation Path |
|---|---|---|---|---|
| Game lifecycle | GameInstance | save | game detail | lifecycle / turns |
| WorldState | GameInstance / world_state engine | save | visible world projection / LLM context | `apply_world_ops` / world events |
| Player control | GameInstance player record / player_control | save | roster / game detail | game controls / claim seat |
| Players | GameInstance | save | generic context / UI | character services |
| Canonical D&D character | D&D runtime | save under player/companion | legacy projection | character lifecycle |
| Ruleset state | Ruleset runtime | save | gameplay / LLM view | intents + reducers |
| Combat event result | Ruleset reducer | event ledger / state | combat UI | authoritative intent |
| Legacy combat | GameInstance + CombatResolver | save | generic UI | round/combat resolver |
| Economy balances | Economy + character state | save | UI / trusted context | proposal settlement |
| Economy proposals | Economy | save | proposal card | planner / GM / payer |
| Memory | MemoryStore | SQLite | context retrieval | outbox delivery |
| Lorebook | LorebookStore | SQLite | localized read view | content/world management |
| Adventure graph | Adventure Bundle | files | runtime read | package management |
| World template | Content V2 | files/DB | localized read | content management |
| Ruleset bundle | Ruleset Bundle | files | runtime catalog | package release |
| Scene image asset | Image backend | asset files | reference | image service |
| scene_image ref | GameInstance | save | timeline/UI | generated image service |
| Plugin state/process | PluginHost | plugin data/config | admin UI | host lifecycle |
| Runtime config | ConfigStore | config/secrets | settings | config controller |
| HTTP listener | web_transport/web_server | runtime only | system status | restart |
| Native crash dump | Windows/WER | logs | diagnostics | OS/launcher |

---

# 49. Extension Matrix: Where New Features Should Go

## 49.1 Adding a New Ruleset

If it is a complete professional ruleset:

```text
src/rulesets/<new_runtime>/
    +
RulesetRuntime implementation
    +
canonical runtime id/version
    +
capabilities
    +
bundle/content
```

Do not:

```text
add if new_rule inside generic d20
```

---

## 49.2 Adding a Generic Combat Primitive

Only put something into:

```text
src/engine/combat_*
```

when multiple rulesets can reasonably share it.

Concrete identities such as:

- spell;
- cultivation technique;
- feat;

still belong in the concrete ruleset catalog.

---

## 49.3 Adding a Persisted GameInstance Field

Check in this order:

```text
GameInstance field
    ↓
GamePersistedState contract
    ↓
GameStateCodec.encode/decode
    ↓
migration/default
    ↓
projection only if UI/LLM needs it
    ↓
rollback/restart/reset semantics
    ↓
tests
```

Do not stop after adding a dataclass field.

---

## 49.4 Adding a Web Feature

```text
route
  ↓
WebAPI delegate
  ↓
service
  ↓
core/ruleset
```

If it is only a transport parameter:

```text
web_transport
```

Do not move it into the engine.

---

## 49.5 Adding a Plugin Capability

Prefer:

```text
plugin implementation
+
SDK contract
+
capability descriptor
+
tests
```

Only a genuinely new plugin type should extend:

```text
support descriptor / initializer / permissions / cleanup
```

---

## 49.6 Adding a Model Provider

The configuration layer continues to use:

```text
ai_providers
*_provider_ref
```

Do not reintroduce scattered capability-specific credential truth such as:

```text
chat_base_url
chat_key
image_base_url
...
```

---

# 50. Current Architecture Debt / Non-Final Boundaries

This section records only “not yet final” boundaries that are clearly visible in current source. It is not criticism.

## 50.1 The Main `RulesetRuntime` Protocol Is Still Broad

It currently covers:

- character;
- intent;
- event;
- projection;
- migration.

Optional protocols are gradually splitting these responsibilities, but the main protocol is not yet fully capability-oriented.

---

## 50.2 Generic Character Projection Still Carries Traditional Fields

For compatibility with:

- older worlds;
- older saves;
- prompts;
- generic UI;

generic state still carries fields such as:

```text
hp
max_hp
class
race
level
attributes
equipment
skills
inventory
```

So the generic state cannot currently be described as “completely ruleset-agnostic.”

---

## 50.3 Legacy and Professional Runtime Paths Still Coexist

The default registry currently contains both:

```text
core:legacy
core:dnd2024
```

This is an intentional compatibility architecture.

The existence of the D&D runtime is not a reason to delete all legacy fallbacks.

---

## 50.4 WebAPI Is Still a Fairly Large Facade

A lot of business logic has moved into services, but `WebAPI.__init__` still performs substantial dependency wiring.

The current direction is:

- preserve facade compatibility;
- continue moving real domain logic down into services/core;
- do not create new cross-service coupling merely because “the file is large.”

---

## 50.5 GameInstance Is Still a Large Aggregate

This is intentional.

Players, rounds, economy, combat, and related domains have strong constraints around:

- atomicity;
- rollback;
- run identity;
- concurrent writes.

Therefore the aggregate should not be split into several independent Aggregates merely because the dataclass is large, at the cost of losing the per-game transaction boundary.

Things that can continue to be extracted include:

- codec;
- projector;
- helper;
- resolver;
- domain service.

Authority itself should not be split casually.

---

## 50.6 WorldState v1 Is Not a Full World Simulator

Current support includes:

```text
facts
visibility
logical clock
scheduled events
simple action legality
```

but not:

```text
generic Entity Registry
Relation Graph
Long-running Process Engine
map topology / pathfinding
```

Future work should therefore enhance the existing WorldState authority incrementally rather than creating a parallel “more advanced world state.”

---

## 50.7 AI-Hosted PCs Have an Authority Model, but AI Player Capability Should Remain Constrained

AI hosting already participates in:

```text
control contract
exploration action fill
authoritative D&D combat automation
Web / Bot control UX
```

but that does not mean an AI controller should gain:

```text
GM-private context
private information across characters
direct mechanics mutation
a duplicate character copy
```

Future AI-behavior enhancements should keep the principle:

```text
same PC
same action authority
same combat authority
different controller
```

---

# 51. Key End-to-End Architecture Diagram

```mermaid
flowchart TB
    subgraph Client
      FE[Vue Frontend]
      CH[Channel Adapter]
    end

    subgraph Transport
      HTTP[aiohttp Routes]
      SSE[SSE]
      TLS[web_transport]
    end

    subgraph Application
      API[WebAPI]
      SVC[WebUI Services]
      GH[GameHandler]
    end

    subgraph Domain
      GI[GameInstance]
      RS[Ruleset Runtime Registry]
      LEG[core:legacy]
      DND[core:dnd2024]
      ECO[Economy]
      CHECK[Checks / Dice]
      COMBAT[Combat Primitives]
    end

    subgraph AI
      PC[PromptComposer]
      CTX[Context Builder]
      LLM[LLM Client]
    end

    subgraph Storage
      SAVES[(Save Packages)]
      LORE[(Lorebook SQLite)]
      MEM[(Memory SQLite)]
      ASSET[(Media / Plugin Assets)]
    end

    subgraph Extensions
      PH[PluginHost]
      PLUG[Plugin Processes]
    end

    FE --> HTTP
    CH --> HTTP
    TLS --> HTTP
    HTTP --> API
    HTTP --> SSE

    API --> SVC
    SVC --> GH
    SVC --> GI
    SVC --> RS

    GH --> GI
    GH --> PC
    GH --> CHECK
    GH --> ECO

    RS --> LEG
    RS --> DND

    DND --> COMBAT

    PC --> CTX
    CTX --> LLM

    GI --> SAVES
    ECO --> SAVES
    CTX --> LORE
    CTX --> MEM

    API --> ASSET

    PH <--> PLUG
    PLUG --> HTTP
```

---

# 52. Real Authority Chain for a D&D Player Attack

To make the abstract architecture concrete, consider a player attacking an enemy in a D&D game.

```text
1. Player submits a natural-language action through Web/Channel
       ↓
2. turns service resolves game / actor / run
       ↓
3. action is written into GameInstance
       ↓
4. multiplayer barrier is satisfied
       ↓
5. round enters ACTIVE_JUDGMENT
       ↓
6. Check Planner interprets whether the declaration maps to a structured attack/check
       ↓
7. D&D runtime / combat-intent boundary identifies authoritative mechanics
       ↓
8. server validates actor ownership, target, current combat, turn, etc.
       ↓
9. server RNG / formula AST computes result
       ↓
10. EventBatch
       ↓
11. D&D combat reducer updates canonical combat state
       ↓
12. authoritative result enters read-only LLM context
       ↓
13. LLM narrates the result that has already happened
       ↓
14. reasoning/tag parse / stale fence
       ↓
15. apply permitted narrative side effects
       ↓
16. save
       ↓
17. SSE/Bot output
```

The most important ordering is:

```text
mechanics result
    BEFORE
narrative description of that mechanics result
```

not the other way around, where LLM narration defines mechanics.

---

# 53. Real Authority Chain for Purchasing an Item

```text
1. Player: "I buy this sword"
2. Planner recognizes a purchase intent
3. Price can be parsed:
       → purchase proposal
   Price cannot be parsed:
       → unpriced intent
4. Model also outputs "you received the sword"
5. filter_unconfirmed_purchase_grants()
       pending paid proposal? block
       unpriced intent? block
       explicit legal FREE_GRANT? allow only matching genuinely free grant
6. payer decision
7. balance validation
8. transaction
9. deferred item effect commit
10. authoritative save
11. memory outbox delivery
12. next narration may proceed
```

This shows that economy and inventory now participate in a linked transaction boundary rather than acting as two independent tag parsers.

---

# 54. Real Boundary for a Historical Swipe

```mermaid
sequenceDiagram
    participant GM
    participant AUTH as Authority Gate
    participant PROC as Process Lock
    participant GI as GameInstance
    participant ECO as Economy
    participant MEM as Memory
    participant LLM
    participant SAVE

    GM->>AUTH: request swipe
    AUTH->>PROC: acquire
    PROC->>GI: restore target snapshot
    GI->>ECO: reverse round-era settlements
    ECO->>MEM: queue reversal(s)
    GI->>GI: cut discarded log branch
    GI->>LLM: regenerate branch
    LLM-->>GI: new narrative/state candidates
    GI->>SAVE: persist new authoritative branch
    SAVE-->>GI: success
    MEM->>MEM: drain pending reversals
    PROC-->>AUTH: release
```

Ordinary live actions must be rejected before writing while a rewrite is in progress.

Otherwise this could happen:

```text
swipe restores old state
player writes new action into old branch
swipe commits new branch
```

causing branch contamination.

---

# 55. Maintenance Rules: Deciding Where Code Belongs

When handling a new requirement, use the following decision tree.

### Is it an HTTP/TLS/certificate problem?

Yes → `web_transport / webui`

### Is it Web use-case orchestration, but not core rules?

Yes → `webui/services`

### Is it a per-game state invariant / generic mechanic?

Yes → `engine`

### Is it specific to D&D?

Yes → `rulesets/dnd2024`

### Is it a primitive that all professional rulesets can reasonably share?

Yes → generic `engine` or `rulesets/contracts`

### Is it only legacy-format adaptation?

Yes → `compat / migrations`

### Is it only presentation or locale?

Yes → Content locale / frontend

### Is it an external plugin capability?

Yes → `plugin_host` descriptor/capability or plugin package

### Is it only narrative-generation assistance?

Yes → commands / llm, but that does not grant persisted-mechanics authority.

---

# 56. Conclusion: The Architectural Center of DiceFrame Today

The architectural center of DiceFrame is not the LLM.

The real center is:

```text
GameInstance Aggregate
        +
Versioned Ruleset Runtime
        +
Server-side Authority
        +
Explicit Persistence / Migration
        +
Transactional Side-effect Boundaries
```

The LLM is the semantic and narrative engine that operates around this authority model.

The current system can be summarized in one sentence:

> **DiceFrame is a self-hosted multiplayer TRPG runtime that uses GameInstance as the per-game transaction boundary, Ruleset Runtime as rules authority, LLMs as constrained narration/planning engines, and versioned content, persistence migrations, outboxes, and capability boundaries to preserve consistency and extensibility.**

---

# 57. Source Verification Index

This document primarily maps to the current source files below:

```text
Startup / Web
web_server.py
src/webui/runtime_config.py
src/webui/composition.py
src/webui/application.py
src/webui/bootstrap.py
src/webui/access_control.py
src/webui/api.py
src/webui/routes/*
src/webui/services/*

Core orchestration
src/common_factory.py
src/commands/game_handler.py
src/commands/round_processor.py
src/commands/check_planner.py
src/commands/prompt_composer.py
src/commands/round_llm.py
src/commands/state_update_applier.py

Aggregate / Persistence
src/engine/game_instance.py
src/engine/game_state_codec.py
src/engine/game_state_contracts.py
src/migrations/instance.py

WorldState
src/engine/world_state.py
src/engine/world_legality.py
src/engine/world_events.py
src/llm/world_prompt.py

Player Control / AI-hosted PC
src/engine/player_control.py
src/commands/ai_player.py
src/webui/services/game_controls.py
src/webui/services/turns.py
src/rulesets/automation.py

Economy / Memory
src/engine/economy.py
src/engine/memory_outbox.py
src/memory/*

LLM
src/llm/client.py
src/llm/context_builder.py
src/llm/parser.py
src/llm/protocol.py
src/llm/tools.py

Rules Runtime
src/rulesets/contracts.py
src/rulesets/registry.py
src/rulesets/builtin.py
src/rulesets/legacy_adapter.py
src/rulesets/dnd2024/*
src/rulesets/dnd2024/features/*

Content
src/rules/*
src/content/*
src/lorebook/store.py
src/lorebook/matcher.py
src/lorebook/retrieval.py
src/migrations/lorebook.py
src/adventures/*

Plugins
src/plugin_host/*
src/plugin_sdk/*

Transport / Deployment
src/web_transport/*
src/docker_launcher/*
src/launcher/*
src/runtime_logging.py
src/runtime_diagnostics.py

Frontend
frontend-v2/src/api/*
frontend-v2/src/features/*
frontend-v2/src/composables/*
frontend-v2/src/i18n/*
frontend-v2/src/router/*

Architecture guard
 tests/architecture/test_dependencies.py
```

---

# 57A. Rules for Developers Maintaining This Document

This developer edition does not require “rewriting the whole document for every PR,” but the following changes must be reflected here:

```text
Authority owner changes
Persisted schema changes
New core GameInstance fields
New ruleset capability
New cross-store outbox
New Player Control mode / semantics
WorldState contract changes
Lore retrieval / prompt authority / embedding cache contract changes
Major end-to-end request-path changes
```

The following usually require only updating developer-entry guidance rather than architecture core:

```text
button repositioning
Modal styling changes
copy changes
ordinary CSS
component refactors that do not change authority
```

If a path in the developer entry table moves to a real new owner, the table must also be updated. An incorrect navigation map is more dangerous than no map at all.

---

# 58. Document Maintenance Strategy

Do not maintain this document by “adding one sentence for every PR.”

Update it only when:

1. an authority owner changes;
2. module dependency direction changes;
3. persisted schema / identity semantics change;
4. a runtime / capability boundary is added or removed;
5. a new cross-store transaction/outbox is introduced;
6. transport / plugin process / deployment topology changes;
7. the Lore retrieval / context authority / semantic cache boundary changes structurally;
8. an end-to-end request sequence changes structurally;
9. a “compatibility path” formally becomes the “normal primary path,” or vice versa.

Pure UI copy, ordinary bug fixes, and small component moves should not pollute the core architecture document.

For major design changes:

```text
ARCHITECTURE_CN.md = current facts
ADR               = why the long-term decision was made
PR / Issue         = implementation process and discussion
```

Do not mix those responsibilities.

---

# Appendix: Relationship Between This Developer Edition and the Original Architecture Document

This file preserves the full body of the original architecture document. It does not compress roughly 73 KB of system facts into a short overview.

The major additions are:

```text
Developer quick start
WorldState added on current main
Player Control / AI-hosted PC
D&D Class Feature Runtime v1
Hybrid Lore Retrieval / Semantic Retrieval / Lore Prompt authority
QR pairing
Current Confirmed Event / World Memory boundary
GameInstance schemas 13 / 14 / 15 + Lorebook SQLite schema 9
Developer maintenance and code-location rules
```

In other words:

```text
original architecture body
+
developer entry-point index
+
latest main architecture facts
=
this developer edition
```

If the document is split in the future, a recommended structure is:

```text
ARCHITECTURE_CN.md          current architecture facts (long-form)
DEVELOPER_GUIDE_CN.md       feature entry points and code navigation
ENGINEERING_RULES.md        engineering constraints
ADR/*                       reasons behind long-term design decisions
```

Until then, this single document can be used directly as the complete onboarding entry point for new developers.

## DF Agent application orchestration

For multi-book visibility, `unconfirmed` means possibly submitted and pending reconciliation; it is not counted as `not-executed`, and `unconfirmed_count` reports it separately.

Each Agent model output uses independent `assistant_agent_max_tokens` (default 4096), while the complete run uses `assistant_agent_task_max_tokens` (default 256000, range 16000..2000000). Both persist and hot-reload through ConfigStore and the configuration update boundary. Content drafts generated through `AgentContentTools` use the independent `text_gen_max_tokens` budget (default 16384), separate from both the Agent reply limit and the complete task budget. The default request/tool-call limit is 24; larger explicit grants may raise it to 64. Remaining write authorization is checked independently, and business generation tools keep their own budgets. A capped task retains its results, distinguishes token and step limits through `budget_reason`, and requires manual continuation. Numeric badges on the launcher and collapsed rows indicate the current session's pending approval; opening it reveals the approval card. The header and General settings share one device-local interface language preference; existing game languages do not change. The library displays canonical Book titles separately from bound worlds; a same-named primary book gets a display label without changing its saved name or identity.

When `generation_defaults_version` advances to 7, a one-time migration raises every legacy `text_gen_max_tokens` value below 16384, including custom low values, to 16384; values already at or above 16384 are preserved. After the migration, users may manually lower the setting, and later loads do not reset it again. Other generation budgets are unchanged by this migration. An explicit `TRPG_TEXT_GEN_MAX_TOKENS` environment override retains precedence and existing configuration persistence semantics; it is outside the automatic increase.

The owner application shell mounts a shared `AssistantShell` across business routes; General settings can hide it. Public/player routes do not mount the owner agent. A single `AssistantPanel` lives in a non-modal floating window that opens beside the launcher; `useAssistantWindow` owns header dragging, edge/corner resizing, viewport clamping and browser-local geometry, switches to chat/task columns once the window is wide enough, and offers a centered maximized window; phones get a full-screen sheet. Switching layouts preserves the component, unsent input and local draft edits. The read-only `/api/assistant/chat` contract remains; `/api/assistant/agent/*` adds owner-only execution, review, confirmation and recovery.

The launcher is a liquid-glass d20 familiar (`AssistantAvatar`): purely presentational moods for thinking, listening, speaking, pending approvals and table reactions. Play surfaces only report live events through `reportAssistantReaction` (check reveals, the combat replay queue and `usePlayReactions`, which diffs live play state against a per-game baseline so loading a table never replays history); `features/assistant/reactions.ts` owns each reaction's category, mood, priority and copy, and browser preferences can disable categories or read reactions aloud. Reactions never feed back into rules or the agent conversation. Conversations are kept per browser by `useAssistantConversations`: each archived transcript remembers its own agent session id so drafts and approvals are restored when it is reopened. Agent `navigate` results are followed while a reply is streaming (a browser preference can turn this off) and remain inline under that reply with a way back; navigation returned by confirmed actions stays an explicit button. Voice uses owner-only `/api/assistant/speech` and `/api/assistant/transcription`, which reuse the configured TTS/ASR providers without a game context and count as AI requests; without configured providers the browser engines are used.

`useAssistantLauncherPosition` owns PointerEvent dragging, browser-local position persistence and viewport clamping, separating a drag from click or keyboard activation; DF Agent settings can reset the mounted launcher to its default position. `useAssistantWorkspace` owns task folding and the browser preference: phones fold by default while retaining a status summary, and a single-column task area occupies at most 45% of the message area. Hiding it preserves mounted drafts and pending approvals. `SectionWorkspaceShell` anchors sticky secondary navigation to the application header's visible bottom edge: it moves to the viewport top as the header scrolls away and stacks beneath it again on return. Sticky settings and log menus share this offset. Batches of registered immediate read/draft tools execute sequentially in the framework. Batches containing deferred writes, final output or unknown tools are rejected as a whole and corrected, preserving one server-held approval at a time. Exhausted format retries return `MODEL_RESPONSE_INVALID` while retaining the session and existing drafts.

`assistant_agent_framework.py` uses Pydantic AI's executor, validation retries, request/tool budgets and deferred approvals over the existing configured transport. `application_actions.py` owns explicit `ApplicationAction/ActionRegistry` contracts: version, parameters, domain, effect, guard and authorization callback, plus owning-module `PreparedAction`/`ActionAssessment` contracts for semantic preparation, live target resolution and actual visibility assessment. Access is not inferred from tool names, routes or Python members. Page context loads relevant capabilities; `search_capabilities/describe_capability` discovers others. Dynamic plugin sources refresh at catalog, planning and confirmation boundaries, and contract changes invalidate old plans.

`assistant_agent.py` owns sessions, server-held grants, plans and receipts. The owner selects Suggest, Handle routine work or Take over this task in the Agent access panel. Grants bind resource/domain scope, expiry and remaining steps. Routine mode permits only explicitly automatic guarded ordinary writes; task mode additionally covers scoped disclosures. Lorebook visibility follows the prepared assessment: low-risk GM narrowing may run within a task covering canonical targets, while audience expansion uses a sealed multi-book plan with concrete approval. System configuration, game policy, generic ruleset intents, professional rests and native plugin calls still require concrete approval. Display preferences are registered separately from policies such as advancement authority; rest adapters cannot supply missing time consent. Chat, documentation and browser preferences cannot grant access. The runtime compact capability catalog carries at most 160 summaries with `has_more` and `domains`; `search_capabilities` pages from the current page start plus returned count, while `describe_capability` lazily loads the exact schema. The library page automatically loads lorebook, worlds and adventures domains, and action instructions are included in tool descriptions. Automatic and manual writes share the same durable plan/receipt path. Cancel discards undispatched plans and is rechecked after start notifications and asynchronous authorization; dispatched writes finish recording, and uncertain outcomes cannot be replayed. Provider-reported tokens accumulate and are checked after responses, not as a hard billing cap; domain generation tools keep separate budgets.

Independent authoring, gameplay, world, settings, library and plugin adapters delegate to their owners. Lorebook visibility follows the actual audience policy: visibility-only writes submit actual-row `visible_to` patches, while ordinary `write_lorebook_entries` prepared payloads contain the entry bodies to be written and perform normal content writes. The single-entry setter and ordinary writer share the same policy, so splitting a change cannot bypass approval. `set_lorebooks_visibility(book_ids, audience, ...)` first resolves an explicit 1..128 canonical book ID list from the live catalog, bounded to 1000 entries per book. Scope binds the owner-resolved book target; `game_key` only validates live characters and does not authorize a book. Low-risk GM narrowing may run under a task covering the canonical targets; expansion to public or new characters uses one sealed multi-book approval. The signed plan binds immutable payload, assessment, version and contract digest; the 4 MiB limit applies only to `prepared.payload`. It reports success, failure, unconfirmed and not-executed books with `unconfirmed_count`, supports cancellation between books, and writes each book in its own transaction: hidden `visibility_plan_book` records idempotently coordinate the parent receipt, while the revocable child records remain `kind=write_entries` with the `visibility_only` marker, without a cross-book transaction. `POST /api/assistant/agent/resume` is the owner's explicit confirmation of the sealed plan's remaining targets; it rechecks plan/contract/TTL and every incomplete target, and automatic execution rechecks the live task grant per book rather than relying on a revoked grant, then skips committed receipts. Repeated confirmation returns the stored response and never retries partial writes. Only registered resumable executors reconcile owner receipts; unknown mutations remain `recovery_required`. Visibility-only prepared payloads contain no authorization body; private-marker scanning is validation only, and recovery ignores derived revision/time fields only for visibility-only records. The current-page scope still requires an explicit All selection when no ID is available. World facts use World Runtime operations. Rest, recovery, story, adventure and rules actions retain domain revisions, identities and server dice. New registered actions become discoverable; unregistered features still need adapters. See [ADR-0011](adr/0011-application-actions-and-agent-delegation.md).

An unapproved proposal expires after 15 minutes. For an already dispatched, paused plan, the owner reviews and explicitly confirms the original remaining targets to obtain a fresh 15-minute execution window. The original approval expiry and plan digest stay unchanged; committed targets are skipped by receipt, and Task grants are neither renewed nor broadened. Sessions retain their existing 24-hour limit since the last persisted activity. Checkpoint completion considers every target in the original plan, including targets not yet initialized, and reports `completed` only when all have succeeded.

HTTP and Agent professional rests share `ruleset_rest.submit_live`: solo games keep single-character resolution, while multiplayer collects eligible characters' submissions before shared settlement. This entry does not skip collection or add world-clock advancement.

`assistant_agent_knowledge.py` registers read-only `search_documentation`, retrieved only when the Agent asks for usage guidance. The current-version mode in `assistant_knowledge.py` uses current local files or a version-matched bundled index and accepts only this repository's versioned document sources. Moving remote guides are excluded and no documentation is injected into every chat. Actual tools and service observations determine capabilities, canonical IDs and executable operations.

`assistant_content.py` generates side-effect-free drafts and delegates to ContentTextReview's ContentDraft/CommitPlan review/commit boundary. Game creation preserves transactions, rules and explicit canonical book bindings; actions use the server actor and server dice. Durable execution receipts live under the data directory's `assistant_agent` folder. Repeated confirmations return the original result; interrupted writes with uncertain outcomes cannot be blindly replayed. See [DF_AGENT_IMPLEMENTATION.md](internal/design/DF_AGENT_IMPLEMENTATION.md) for the complete capability and transport contract.


## Storage branch recognition and integration ownership

GameInstance schema 44 preserves main's migrations through 43 and initializes missing AI-seat/check-grant slots without replacing existing or future slots. The new reader fence protects policies and linked Push consent from older readers that cannot enforce them.

Unreleased local schemas 36-38 used the same numbers for different changes. `src/migrations/local_instance_branch.py` recognizes only the known room_access v1 plus AI/check-slot shape before replaying main's content-binding and credential migrations. Unknown mixed variants are rejected. Historical checks and pending Push data are retained without rolling again.

Lorebook SQLite 12 adds owner-private recovery records after main's external identity and source tracking. `src/migrations/lorebook_branches.py` recognizes the local v10 journal, establishes main's identity columns in the normal SQLite transaction, then applies v11/v12. Existing books, entries, bindings and journals remain intact; failures roll back all steps.

The local world-identity upgrade is a cold-start repair at the front of a fresh
`common_factory` build; a `reuse` build does not run it. It accepts legacy
Lorebook SQLite v4 and canonical v5–v12 world/book/binding layouts, and only
rewrites known world/book references in `templates`, every `saves/state*.json`
file and known JSON payloads for save schemas recognized by the local writer.
The replacement ID is a stable hash of the old identity in an ASCII canonical
form, keeping same-named worlds distinct. Display titles, entry IDs, `game_key`,
`memory_namespace` and chat/log text stay unchanged. SQLite receipts plus a
durable journal roll the plan forward after an interrupted process; user-edited
files, target collisions, invalid schemas and future schemas fail closed without
overwriting data. New AI world generation keeps IDs ASCII, budgets for the
`world:` primary-book prefix and prefixes generated entry IDs.

AI seats and check grants register their cleanup/reset lifecycle hooks in their own modules. `adventure_commands.py` owns node/choice transactions, authoritative admission, current-run fences, module preflight and owner-mediated compensation. Game Lorebook selection also restores through its content-binding owner; preview round reads do not materialize missing slots. The imagegen game projection captures storyboard inputs so core round orchestration does not increase narrative-module coupling.


## Character dialogue and shared story sequences

`modules.storytelling` (schema 1) owns per-game `prose` / `dialogue` generation preferences and public portrait overrides. Creation and the GM workbench use the same setting, effective on subsequent generation. Missing settings preserve prose behavior; unknown future slots reject writes. This setting is independent of Adventure `play_mode` and ruleset mechanics.

The same module owns optional `speaker_aliases`: after explicit GM confirmation, a duplicate guest can be linked to a current public speaker for story and portrait projection. Original dialogue, NPC and player records remain intact; matching names never trigger automatic deduplication. The service validates active identities, holds the write lock and restores preferences on save failure. Guest identity links are cleared for a new run.

`src/engine/story.py` owns version-1 ordered `story.segments`; `src/commands/story_output.py` adapts model output. Dialogue mode emits narration/dialogue NDJSON before the existing `---` state-tag block. Validated segments generate ordinary `gm_response` text for Bot, memory, context and export. Every swipe retains its matching story; historical prose is not heuristically split. SSE adds sanitized `story_update` snapshots alongside plain text, with retries clearing both provisional views. Public logs whitelist nested fields; internal NPC IDs remain private, same-name actors stay distinct, and concealed personas do not inherit a revealed identity or portrait.

`frontend-v2/src/features/play/story/` renders narrator cards, character bubbles and searchable complete-cast portrait management. Explicit portraits are reused; missing images use stable initials/colors with no automatic image generation. Known `s_` identities carry across new runs through a game identity namespace; guest `g_` identities belong to their original run. Future stage renderers can consume the same versioned data; stage placement, expressions and story-route mechanics are not implemented here. See [ADR 0006](adr/0006-structured-story-presentation.md).

## Translation maintenance and display fallback

`src/localization/registry.json` owns maintenance tier/status metadata. English and Simplified Chinese are core; Japanese/German/Russian are community catalogs. Stable locale IDs remain available to saved games and content even when a catalog is paused. The frontend UI picker excludes paused catalogs, while saved UI preferences render English. Game narration language, canonical identities and mechanics are unchanged.

`frontend-v2/src/i18n/policy.ts` filters community messages by the reviewed English source fingerprints, recursively including API errors. Missing/new/stale keys use Vue I18n's English fallback. The accepted baseline is not regenerated on source changes; volunteers record individual reviewed keys through `locales:review`. `locales:check` gates core key parity and reports community gaps without blocking feature releases. Backend shared text/field helpers and typed content overlays honor paused status. Feature-local/backend text outside the central catalog has no automatic source fingerprinting. See [translation maintenance](en/translations.md).

## Managed Docker version/data transactions

The stdlib owner `src/docker_launcher/data_versions.py` is invoked by the server entrypoint before application imports. Runtime API remains 1: deployed launchers accept new packages without image replacement when the Python/system runtime is compatible. `versioned-copy-v1` preserves the old program's dataset, so no irreversible down-migration is needed for automatic startup-failure rollback.

The candidate copies quiescent business data, with SQLite backup including committed WAL, into a private `data/_updater/data-versions/` directory. Host settings/credentials, sessions, device revocations and TLS stay shared. Canonical game/rule/content services use the candidate data directory; they do not contain version compatibility branches. A separate pending binding is atomically promoted after the launcher's program pointer commits. Interrupted candidates cannot overwrite a previously committed binding. Cold-start recovery validates the dataset marker and binding; missing or ambiguous records fail closed.

HTTP business writes and startup plugins/outbox delivery wait for the existing launcher's real health/probation commitment. `update_admission.py` owns admission; bootstrap delegates to the activation owner. The original root dataset remains available to old entrypoints. A forward upgrade copies the active timeline, while a manual downgrade selects the retained older dataset. `src/compat/program_rollback_access.py` delegates credential sanitation to the room-access owner and closes player entrances, preventing historical tokens from becoming valid again. Host security is not reverted. Current/previous data pairs survive pruning; only validated retired copies are removed.

`data_rollback_safe=true` remains mandatory. Versioned packages declare positive instance/Lorebook schema versions and include the data bootstrap. Shared host configuration/security must remain backward compatible under this protocol; incompatible shared or system-runtime changes need a separately reviewed transition. The complete mounted data directory, including versioned data, pending and committed bindings, is part of backup scope. See [application updates](en/updates.md).

## Party rest activity projection

`src/webui/services/rest_activity.py` reads current-run recovery sources/receipts
and the existing cooperative rest session, projecting at most 40 `rest_activity`
items into game detail. Items contain only stable identity, rest kind, request or
settlement status and current roster identities. Treatment reasons, SAN/Luck
values, rolls, seeds, command principals and private rewards remain excluded.
Lobby and unbound P2P visitors retain their allowlists; bound members reuse the
existing game-detail transport. There is no new rest ledger or save migration.

The frontend owner is `features/play/rest-notices/`; `PlayView` only composes the
entry and existing rest/recovery panels. Reminders default on and are a personal
device preference scoped by game and identity. Dismissal/muting never changes
server preferences, other members or settlement. Stable server item IDs deduplicate
notices; entry, refresh and identity changes establish a baseline without replay.
Teleported notices expire after five seconds, do not move focus and do not resize
chat. The party rail provides manual access. Records are a bounded projection of
existing authority, rather than an independent permanent history.
