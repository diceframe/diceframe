# DiceFrame Architecture Source of Truth

This document describes the current implementation, not a roadmap. The dependency direction is `routes -> WebAPI -> services -> core`; core code must not import `src.webui`, WebAPI methods are delegates, and cross-service calls go through API delegates.

## Scene images and portrait references

Scene generation follows `routes/generated_images -> WebAPI -> services/generated_images -> src/imagegen`; manual generation and storyboard analysis remain GM/server-only operations, with no new player/P2P write surface. The optional boolean `combine_avatar_references` defaults to `true` and only applies when portrait references are enabled by the user. `src/imagegen/reference_sheet.py` combines selected portraits in input order into one ephemeral numbered sheet; the prompt maps `Ref N` to stable player IDs and public names. A single portrait remains unchanged; explicitly disabling combination preserves separate uploads. The sheet is not an output layout, and image inputs are not persisted. Generation metadata retains character IDs, source count, upload count and combination status.

Omitting manual `panel_count` selects automatic mode; explicit values must be integers 1–6. Fixed mode has separate model instructions and at most one correction, validating raw count, effective content and evidence before returning a candidate. Truncation, blank filler and automatic fallback cannot satisfy a fixed count. Candidates and caches are scoped to story revision and requested count. Changing the frontend target preserves the applied draft and invalidates mismatched candidates. Generation validates count again, includes every panel in the prompt and fails explicitly if the budget cannot preserve each location, subject and action. The image model chooses geometry; software adds no dividers and does not treat the requested count as visual verification. The automatic-storyboard toggle and `SCENE_IMAGE` trigger semantics are unchanged.

## WebUI Startup and Configuration

`web_server.py` remains the stable source, Windows portable, and Docker entrypoint. It primarily loads the project environment, composes the explicit WebUI owners, and starts the aiohttp listener. Responsibilities are owned by:

- `src/webui/runtime_config.py`: `RuntimeConfig` / `ConfigStore`, the single `env > secrets.json > config.json` precedence boundary plus secret splitting, redaction, and atomic persistence;
- `src/webui/composition.py`: core subsystem and `WebAPI` construction from explicit paths, state, and factories;
- `src/webui/application.py`: `create_app`, middleware, and route composition without starting a listener;
- `src/webui/bootstrap.py`: template synchronization, plugin/Hub startup, background tasks, save recovery, and cleanup;
- `src/webui/access_control.py`: owner, Bot, SSE ticket, player-share, and room-password access control;
- `src/webui/config_controller.py`: transactional runtime reload and provider connection tests.

AI application configuration uses only `ai_providers` and capability `*_provider_ref` fields, with credentials stored separately as `ai_provider_key_<id>`. Legacy capability endpoints, keys, API formats and AI capability environment inputs are not resolved; updates containing removed fields are explicitly rejected, without migration or provider creation. Missing/unknown references cannot activate residual settings. Browser, edge-tts and disabled ASR need no reference, and local providers may use empty keys. Internal `*_base_url` / `*_api_key` fields produced by composition remain valid service runtime contracts.

Template synchronization and migrated-default persistence happen only during real application startup, not when importing the individual owner modules. A runtime configuration reload fully constructs the candidate runtime before persistence and swaps active state only after persistence succeeds; construction or persistence failure keeps the previous runtime active.

A WebUI service does not import another service directly. Cross-domain business calls use callables or protocols injected by the composition root. Pure contracts and projections shared by multiple domains but performing no business orchestration live at the `src/webui/` root boundary, including lifecycle transaction context, ruleset draft-shape validation, read-only rest projection, and character-card identity/deduplication. Type-checking-only imports are not runtime dependencies.

## Access Credentials and QR Pairing

Owner access accepts two peer credentials, both sent as `Authorization: Bearer` and resolved in `src/webui/access_control.py`:

- the access password, stored in `STATE["access_token"]` as a PBKDF2 hash only — the server never holds the plaintext, and no endpoint may hand it back;
- device tokens (`src/webui/device_tokens.py`), high-entropy random strings persisted as sha256 digests only (random tokens need no KDF, and they are verified on every request), revocable per device without touching the master password or other devices.

QR sign-in lives in `src/webui/pairing.py` and `src/webui/routes/pairing.py`: an owner session calls `POST /api/pairing` for a single-use, short-TTL pairing code (also stored as a digest only), and the mobile client anonymously calls `POST /api/pairing/claim` to exchange it for a device token. The claim endpoint must stay anonymous (the phone holds no credential yet), so it shares the abuse-guard login bucket with `/api/login` and writes to the same login audit; pairing codes are single-use, expire, and are never renewed. The device list `GET /api/devices` and revocation `DELETE /api/devices/{id}` / `POST /api/devices/revoke-all` are owner-only, and the listing returns no field usable for authentication.

On a passwordless server `POST /api/pairing` is open to any client that can reach it, so a device token issued then means "whoever can connect is the owner". Configuring an access password for the first time (`access_token` going from unset to set) therefore revokes every device token and pending pairing code as well; changing an already configured password does not — device tokens are credentials that rank alongside the access password, and the settings page has its own per-device / revoke-all entry points.

Only the server knows which address the QR code should carry — the GM's browser origin is usually localhost, which is useless to a phone. `GET /api/system/network` (owner-only) returns reachable local candidates via `src/web_transport/local_addresses.py`, which is also the address source for self-signed certificate SANs.

## Content V2

Inputs cross a compatibility boundary before entering the current canonical model:

```text
Legacy / V1 Rule / Plugin / Save / World / Character
                    ↓
              Compatibility
                    ↓
          Canonical Current Model
                    ↓
            Runtime Mechanics
                    ↓
             Typed Locale
                    ↓
                   UI
```

Canonical identity is a stable reference key: `fighter`, `longsword`, `chain_mail`, `athletics`, `str`, and `npc_innkeeper`. `战士 / Fighter`, `长剑 / Longsword / ロングソード`, and `老汤姆 / Old Tom` are display text only. Changing language never changes an ID.

Canonical rule/content data is the mechanics authority for normal V2 runtime. Legacy tables such as `ARMOR_LITE`, `WEAPON_DAMAGE`, and `WEAPON_DAMAGE_DICE` are compatibility fallbacks for old saves or V1 input only.

## Rule Locale

The rule core owns `dice_system`, `damage_dice`, `ac_base`, `dex_cap`, `attribute_points`, `proficiency`, `combat_model`, `skill_pools`, `item_categories`, damage/death mechanics, permissions, capabilities, and scripts. Profession skill pools use canonical class and skill IDs. Typed locales may translate their display names but cannot replace skill pools or item classifiers. Unknown or mechanics-shaped nested fields are rejected.

## World Locale

The world core owns `world_id`, `default_rule`, `recommended_rules`, `suggested_difficulty`, and the starter lorebook entry set/order, IDs, types, tiers, `unreliable`, `sync_on_enter`, `triggers_recursive`, `visible_to`, `match_mode`, `sticky`, `cooldown`, `delay`, `order`, `probability`, `group`, `group_weight`, `connected_to`, and other deterministic fields.

World locale may change only `world_name`, `description`, `world_setting`, `starter_scene`, and `name`, `keywords`, or `content` for a canonical lore entry ID. World Locale cannot replace `starter_lorebook` entries. Language changes cannot add, remove, or rename canonical lore identities.

For example, core ID `npc_innkeeper` may have `npc_innkeeper.name = 老汤姆` in Chinese and `npc_innkeeper.name = Old Tom` in English. The identity remains `npc_innkeeper`.

The lorebook database stores canonical/core entries. Keyword matching, prompt construction, and puzzle initialization build a read-only localized view for each `GameInstance.language`; translated text is never written back to the shared database.

Every ruleset shares one generic lore retrieval layer (`src/lorebook/retrieval.py`): normal rounds, swipes, and out-of-character questions all go through the same `LoreRetriever`, whose input is this round's actions plus the current scene, canonical location, and present-NPC anchors. `KeywordMatcher` remains the base retrieval; semantic retrieval is an optional enhancement that reuses the existing embedding configuration and the single `EmbeddingClient` owned by `MemoryStore` (no second embedding client), and silently falls back to anchors plus keywords when it is unconfigured or fails — it never blocks a round. Entry vectors live in the derived `lorebook_embeddings` cache in `lorebook.db` (isolated by entry / language / embedding_profile, rebuilt when `content_hash` changes); it is not an authority and can be dropped and rebuilt at any time. A semantic hit only means "possibly relevant": it still passes visibility and budget, never writes WorldState, never changes ruleset verdicts, and never triggers events.

## Plugin Content V2

The manifest currently supports `schema_version = 1`, `content_schema_version = 1 or 2`, `locale_schema_version = 1`, and `default_locale` as the package locale fallback. Locale fallback is exact requested locale -> base locale -> package/default locale -> base(default locale) -> canonical/core display fallback.

`ResourceRef` examples are `core:item:longsword` and `plugin:my-pack:item:moon_blade`. Ordinary V2 item/class/spell/npc/character_template resources can coexist through namespaces. Rules and worlds still primarily use plain `rule_id` / `world_id`, so duplicate Rule/World IDs across V2 plugins are explicitly rejected; there is no first-wins or last-wins behavior.

V2 resource IDs must already be canonical. The registry never silently normalizes case, spaces, or non-ASCII IDs on a plugin's behalf. When V2 locale or content validation fails, catalog APIs return `CONTENT_VALIDATION_FAILED`; they do not omit the broken resource or fall back to unlocalized content. The in-app content-pack exporter always emits a Content V2 core plus typed-locale layout. V1 full copies remain supported only through import adapters.

## Plugin Runtime Extension Boundary

`src/plugin_host/support.py` is the single metadata source for plugin types, process modes, inferred permissions, and contribution mappings. `src/plugin_host/descriptors.py` validates untrusted initialize payloads into typed descriptors; `src/plugin_host/capabilities.py` owns RPC capability initialization, lookup, and projection; `PluginHost` retains package, process, lifecycle, security, and compatibility-facade responsibilities.

Adding a valid provider capability kind requires plugin implementation, SDK contracts, and tests, but no `PluginHost` edit. Only a genuinely new plugin type should prompt changes to the support descriptor, runtime initializer, permissions, cleanup, and public metadata. See `docs/plugins/EXTENDING_EN.md` for contributor paths.

## Migration and Compatibility

`src/migrations/` performs persisted schema upgrades. `src/compat/` adapts old external/runtime shapes to the current canonical model. V1 packages are read through adapters; compatibility branches do not move into normal business logic.

Migrations for loaded persisted `GameInstance` data are orchestrated through the single `src.migrations.migrate_instance` entry point. Domain-specific migration implementations may live in `src/compat/` as pure adapters, but services, routes, and runtimes must not call those adapters directly. Every migration must be idempotent, tested, and bounded by an explicit version/identity/digest contract; uncertain migrations fail closed. New behavior adds a versioned migration step rather than changing the meaning of a released step.

## Currency Model

The single authority for a rule's currency structure is `CurrencySpec` in `src.engine.currency`: `currency_system` (`schema_version: 2`) declares `base_unit` (what the canonical integer counts; its rate is always 1), `display_unit`, and positive-integer unit rates. Rules carrying only a legacy `currency` label normalize to a rate=1 legacy spec and are never reinterpreted by name. `parse_currency_amount` / `format_currency_amount` are the only conversion points between display amounts and canonical integers (Decimal, reject anything not exactly representable); the economy engine only sees canonical base-unit integers and never learns currency names — no scattered ×100 / ÷100 and no floats in business code. Explicit V2 declarations fail fast at `RuleSystem` construction and `RuleBundleLoader` loading; rule CRUD, AI-generated rules, and plugin installation reuse the same validation, and display conversion is centralized in `frontend-v2/src/utils/currency.ts`. `MAX_ECONOMY_AMOUNT` is a technical cap in base units. Persisted amounts are migrated only when a rule's base-unit semantics change (currently only built-in `freeform_coc` dollar → cent, ×100 exactly once via the versioned schema v12 step); legacy and custom rules never move their data, and the editor refuses base-unit semantic changes for rules with existing games.

## GameInstance Aggregate Boundary

`GameInstance` remains the aggregate root for one game. It owns authoritative runtime state, invariants, state transitions, and coordination through `_authority_lock` / `_process_lock` / `_lock`. Lock order is authority → process → state; runtime locks are neither persisted nor copied by persisted-state replacement. Historical rewrites exclusively own the authority gate, while ordinary live writers use the same atomic gate to reject before mutation without a separate boolean-check TOCTOU. Players, combat, rounds, and payments are not split into independent aggregates merely to shorten the source file.

Each save has both a stable `game_key` and a rotatable `run_id`. Process recovery preserves the run; reset and restart build and atomically install a candidate while holding the old aggregate write lock. Waiting old-run writes resume after replacement and fail their stale-run fence, while opening effects already applied to candidate characters are preserved. Historical swipe rewrites share `_process_lock` with normal round processing and hold it through restore → LLM → branch application → authoritative save; player actions are rejected before mutation while the rewrite is active. Economy rollback is whole-round (ADR 0003): rolling back to round N withdraws every settlement from N onward, an offer created before N but settled after it reopens as pending, item recovery projects absolute before-images instead of selective inventory diffs, and a swipe branch-cuts the log after the target round and replays from it. Durable memory is isolated by a persisted `memory_namespace`, so isolation does not depend on destructive cleanup. Save-shape upgrades enter only through the sequential migration owner in `src/migrations/instance.py`.

Generic economy state belongs to `GameInstance`. Narrative tags, lore text, and AI output may create proposals but cannot mutate balances. Committed changes pass server-side permission, balance, run-identity, and idempotency checks and produce an auditable transaction. `currency.amount` is authoritative and `gold` is a compatibility projection. Web, Bot, and alternate transports use the same economy path. Narrative rewards belong to the GM; small single-recipient pure-currency rewards may settle through the identical confirmation path without the GM click when the game's reward policy allows it. That policy resolves per game (GM override → rule-template `economy_defaults` → server global fallback) and never auto-settles item rewards, team splits, or over-cap amounts. Reward narration is not filtered by completion-evidence heuristics (that layer silently swallowed deserved rewards); every narrative reward becomes a proposal decided by policy and the GM.

An economy proposal is also a narrative commit barrier. While the current run has an undecided proposal, unresolved effect group, or external effect awaiting delivery or reversal, actions, forced advancement, luck continuation, SSE, and direct round processing cannot begin more narration. Scene, character state, loot, quest, memory, private-information, and quick-action effects from the same model response are persisted as deferred effects and cannot become authoritative before settlement. One proposal commits its effects once; when one response creates multiple proposals, all must commit before the shared effect group applies, while any decline, cancellation, or insufficient-funds result discards it. SQLite memory is a cross-store external effect: it enters a durable game-state outbox, is delivered idempotently under a delivery identity, and records verifiable before/after state. Swipe or rollback persists a reversal request and restores memory still attributable to that delivery; an unpersisted delivery or reversal receipt is retried at startup or before the next progression attempt. A transaction-associated scene-image prompt is removed from staged effect application and may start asynchronous generation only after the first authoritative save succeeds. Terminal outcomes are retained in a bounded ledger and injected as trusted server context that overrides earlier narration. An economy fingerprint comparison (proposal id -> status snapshot) fences stale in-flight AI responses: legitimate settlements during generation do not invalidate them, while newly created proposals or rollbacks do. Reset and restart clear proposals, transactions, outcomes, deferred effects, the outbox, and the revision; restart preserves only balances already committed to character state, while reset also removes characters.

Auxiliary projections have explicit owners: `src/engine/game_state_codec.py` owns the stable save projection and reconstruction, `src/engine/game_context_projector.py` owns the generic LLM/presentation view, and `src/migrations/instance.py` owns normalization of loaded legacy save payloads. Payload normalization runs on a copy before aggregate construction and never mutates caller input. `GameInstance.to_dict()`, `from_dict()`, and `to_llm_view()` remain compatibility delegates rather than implementing those projections. Legacy ability modifiers, armor summation, and string-skill defaults live in the isolated `src/engine/legacy_game_projection.py` and are selected explicitly by `LegacyRulesetAdapter`. A ruleset runtime may extend the generic projection with its authoritative view, but concrete mechanics must not move back into the generic projector.

This is the first codec/projection/migration boundary extraction; it does not mean the generic state shape is final or completely rules-agnostic. To preserve existing worlds, saves, and prompts, the generic projection still carries traditional character fields such as `hp`, `max_hp`, `class`, `race`, `level`, `attributes`, `equipment`, `skills`, and `inventory`. Those compatibility shapes can be narrowed further only while preserving save and ruleset-runtime contracts.

`src/engine/game_state_contracts.py` declares typed contracts for the save top level, generic context, and player rollback snapshots. `ruleset_runtime`, `ruleset_state`, adventure-binding extensions, event payloads, and character extension fields remain intentionally opaque in the generic engine. When adding a persisted field, check each owner in order: authoritative `GameInstance` field → `GamePersistedState` → codec encode/decode → migration/default compatibility → projection only when LLM/UI consumers need it → behavior regression.

## Application Update Boundary

Windows source/portable and managed Docker share the download state machine in `src/webui/services/updater.py`, but installation authority is separated. Source updates use a backup transaction, portable candidates are committed by the Windows launcher, and Docker candidates are committed only by the stable image launcher under `src/docker_launcher/` after health and probation checks pass. A Docker application process may write only a restart signal containing a relative candidate path; it cannot control the Docker daemon, mount the Docker socket, or overwrite the current version directory.

Docker Update schema 1 binds the application version, `linux-amd64`, CPython ABI, launcher schema, base runtime API, and `data_rollback_safe`. The package builder, application updater, and launcher reuse the same contract validation; checksum, platform, ABI, runtime, data-rollback declaration, and path-safety failures are all fail closed. Versioned application payloads live under `data/_updater/docker-versions/`; business-data migrations remain owned by `src/migrations/`, and rolling back application files never pretends to roll back a data schema.

Runtime logs are owned centrally by `src/runtime_logging.py`; launchers and business services must not implement separate rotation or retention policies. Portable logs live under the installation-root `logs/`, managed Docker logs under persistent `data/logs/`, and the default retention is 30 days. The clear operation may remove only DiceFrame runtime logs and must never touch game history, saves, or third-party logs.

Only when the owner explicitly asks DF Assistant to inspect runtime logs may `src/runtime_diagnostics.py` read the two most recent DiceFrame log files. Local processing is limited to credential redaction, successful-poll filtering, duplicate compaction, and context bounds; the configured model performs the diagnosis. At most 24,000 characters are sent, arbitrary files are inaccessible, and log content is always treated as data rather than instructions.

## Frontend and Rule Boundaries

The backend materializes V2 locales and the frontend renders the returned payload; the frontend does not reimplement Content V2 locale architecture. D&D using d20 is not the same as changing generic d20 behavior. D&D-specific behavior remains inside the D&D boundary.

The current implementation completes the first ruleset capability-normalization pass: major D&D-specific semantics have moved out of generic layers, and optional runtime capability boundaries now exist for further contraction. The main `RulesetRuntime` protocol still carries a broad base contract spanning character construction and validation, intents, events, projections, and migration. This is not a claim that every ruleset feature is already an independent capability or that the runtime protocol is minimal.

## Adventure Bundle v1

Advanced play has four independent inputs: the Ruleset Runtime supplies mechanics, the Worldbook supplies setting and lore, an optional Adventure Bundle supplies a story graph, scenes, NPCs, map locations, and adventure-specific encounters, and the Coach provides local presentation-only help. With no Adventure Bundle bound, the game is standard free play and must not silently load a fixed tutorial story.

Standalone adventures live at `templates/adventures/<directory_id>/` and use `diceframe:adventure-graph-v1`. Their manifest declares a canonical adventure ID, version, world policy, and minimum runtime contract. Creation validates rules, runtime, format, and world compatibility before immutably storing `adventure_id / version / format / content_digest / world_id`. Restart preserves and revalidates that exact binding; missing or changed content and fixed-world mismatches fail closed. See `docs/adventures/ADVENTURE_BUNDLE_EN.md`.

At startup, bundled adventures are synchronized as complete directories into `data/templates/adventures/`; the D&D runtime, catalogue API, and management API all read that runtime directory. Built-in packages are read-only. Custom packages have independent canonical identities and may be copied, validation-edited, imported/exported as ZIP files, or deleted. A package referenced by any save cannot be edited or deleted because that would break its pinned digest and deterministic restart. Every write is staged and fully validated through the same `AdventureBundleLoader` before replacing the live directory.

An adventure step may replace the current story entry but never the selected Worldbook. Narrative context always includes the actual Worldbook setting, starter scene, and matched lore. Adventure completion returns to standard free play in that same world instead of a terminal tutorial page.

## Generic Combat Extension

The generic combat extension (Issue 212 / ADR 0004) provides ruleset-neutral
formula DSL, resource pool, effect engine, and turn scheduler primitives.
The dependency direction is fixed: contracts -> primitives -> ruleset
adapter -> ruleset catalog -> transport, with no per-ruleset branches in the
generic engine. Actions, effects, and costs are data (generic kind
vocabularies); spell, technique, and consumable identities live in ruleset
action catalogs as canonical `action_id`. Damage and cost amounts are
evaluated through a restricted JSON-AST formula DSL -- whitelisted nodes,
depth/node/dice/result limits, fail-closed unknown references, injectable
deterministic dice source, never `eval`. Resource pools and schedulers
activate only when a ruleset runtime explicitly declares the capability
(`combat_action_effects` / `combat_resource_pools` / `combat_scheduler`);
clients submit intents and render server projections, and damage, speed, or
resource results from clients are ignored. D&D 2024 damage and healing dice
now evaluate through the generic formula AST via a D&D-side adapter, while
spell slots, concentration, saves, and victory detection remain in the D&D
reducer. Scheduler and pool persistence lands with the first consuming
ruleset.

## World State

`GameInstance.world_state` is the single authoritative container for "what is
currently true in the world". It stays inside the per-game aggregate: no second
aggregate, separate database, or background runtime. The first version has a
fixed shape -- `schema_version / revision / clock / facts / scheduled_events`.
A fact is a canonical key (`actor:<uid>.location`, `bridge:old.passable`, never
a translated display name) plus a scalar value and `public | gm` visibility,
recorded with `source_round` and `updated_revision`; `clock` is logical world
time (day plus minute); `scheduled_events` is the persisted container of events
to settle later, indexed by a stable `event_id`.

The only write entry point is `apply_world_ops(instance, ops)` in
`src/engine/world_state.py`: a batch is validated and committed atomically, and
out-of-bounds values, unknown ops or fields, invalid keys/values, and corrupted
or future schemas all fail closed without writing. Fact visibility is decided by
world ops alone -- an update that omits it never downgrades a GM-private fact to
public. World truth never lives in `ruleset_state`, `key_facts`,
`lorebook_timed_state`, or memory, and the LLM cannot write it directly.

Persistence and lifecycle follow the existing `GameInstance` / codec /
migration pattern: schema 12 -> 13 materializes an empty world container for
older saves (guessing no facts, repeatably); save/load and import/rebind keep
world truth while isolating run identity; reset and restart begin from an empty
world; and world ops belong to the round that wrote them, so whole-round
rollback, aborted judgment, and swipe branch cuts revert them together under the
ADR 0003 whole-round semantics.

World truth is not player-visible truth: `project_visible_state(instance,
viewer_is_gm=...)` is the only read entry point, `gm` facts reach only the GM
context block (marked as invisible to players), and player-facing surfaces get
the `public` projection. Action legality is decided server-side by
`world_legality`: the model may only *propose* structured `world_requirements`
(`act` / `move` plus canonical location ids), and the decision uses provable
evidence only (registered locations, explicit `passable = false`). `passable =
false` blocks entering, passing through, and arriving at a place, so a `move`
checks only the declared `via` hops and the destination -- the actor's current
location is never part of that check, and a character already standing in an
impassable place can still leave. An empty world, an unknown location, or an
actor without an established location never blocks; a proven contradiction
injects a trusted "move first / cannot complete" verdict into the GM context,
and a legal move is written to world truth by the server. This channel stays
separate from overreach: overreach covers a player declaring world facts or
controlling others, while legality covers a player's own action contradicting
authoritative world facts. Logical world time moves only through
`world_events.advance_world_time(+N)`, which advances the clock, settles due
events in stable `(day, minute, event_id)` order, and persists each event as
`applied` or `failed` (its ops no longer apply at settlement time). A persisted
event's `ops` are validated entry by entry under the same structural contract as
the `schedule_event` write path, so corrupted data fails closed instead of being
silently filtered into a false "applied" that executed nothing. There is no
background tick and no separate scheduler; an event can never run twice because
of a retry, duplicate save, or page refresh, and because the settlement lives
inside `world_state` it inherits the whole-round rollback, swipe, reset, and
restart semantics unchanged.

## Player Control

`players[uid].control` is the authoritative record of who controls a seat:
`human` (a real player is responsible), `ai` (the server produces this
character's actions) or `unclaimed` (the seat exists but nobody plays it yet),
carrying `revision`, `temporary` and `resume_mode`. It answers "who plays this
character", not "what this character is": the character itself -- HP, equipment,
spell slots, conditions, world position and the combat actor (`player:<uid>`) --
exists exactly once in every mode, and switching controllers neither copies,
moves nor re-keys anything. The first vocabulary is deliberately closed and does
not include gm / remote_bot / script / hybrid.

The only write entry point is `set_control` in `src/engine/player_control.py`,
and every read goes through the same module: an unknown seat reads as the
conservative default, a corrupted record degrades to `human` (the
pre-contract behaviour), while writes fail closed on an unknown seat, an
unknown mode, or temporary hosting without a resume target. Control is desktop
session state, not a story outcome: `revision` increases only when the record
actually changes, and whole-round rollback, aborted judgment and swipe revert
character sheets and world facts without ever re-assigning a seat.
`temporary = true` hosting must be able to return to `resume_mode` and must not
become permanent after a restart.

Persistence uses schema **13 -> 14**: every seat of an older save becomes
`human`, the migration never guesses an AI controller from online state,
character name or history, and it is repeatable. `control` sits beside
`character_sheet`, so it is part of the player record itself: it round-trips
through save/load and disappears together with the seat when a seat is cleaned
up (for example the ghost-player cleanup on load), leaving no orphan control.

The control mode is now the authoritative admission rule as well.
`submission_block(instance, uid)` decides whether a human may submit an ordinary
action: an `ai` seat returns `PLAYER_AI_CONTROLLED`, an `unclaimed` seat returns
`PLAYER_UNCLAIMED`, and the shared `turns.submit_action` service (used by the
Web endpoint and by SSE) answers 409 for both. It only refuses a human acting
for that seat; it does not change when the server AI acts on its own (see the
hosted-action paragraphs below).

The multiplayer ready barrier counts humans only: `active_human_players` is
alive, present and `control.mode == human`, and `all_alive_ready()` plus the
ready / waiting sets of `multiplayer_status()` are computed from it, so AI-hosted
and unclaimed seats never block the round; they are reported separately as
`ai_players` / `unclaimed_players` with their counts. An away human still does
not block, and `active_alive_players` keeps its previous meaning for call sites
such as the Luck timeout that only need a head count.

Claim transitions go through the same authority: `claim_seat` is the canonical
entry point for joining an existing seat in the Web path, turning `ai` /
`unclaimed` into `human` without moving anything (character sheet, HP, equipment,
spells, world position and combat actor all stay put), and it fails closed with
`CONTROL_STALE` on a stale `expected_revision` and with `CONTROL_NOT_CLAIMABLE`
on a seat that is already human-controlled. A brand-new seat is still born
`human` via `put_player`. `control_change_block` names the safe boundary for a
control change: `""` only while the table is in `ACTIVE_ACTION` with no round in
flight, otherwise `CONTROL_CHANGE_BUSY`.

In ordinary exploration rounds an `ai` seat declares its action through
`src/commands/ai_player.py`. The gate is `GameInstance.human_actions_ready()`
(the human side is complete -- deliberately a different question from
`should_advance()`), and it is invoked exactly once at the single advance entry
point, in `turns.submit_action` after the human gate and before `try_advance()`.
One plain-text call per seat, sequentially in uid order, so a seat sees only its
own character sheet, the player-safe public context and the actions already
declared this round -- never GM-only world facts, `gm_directives`, another
player's `private_log`, future plot, or an extra lorebook channel. The output is
ordinary action prose with no DC, modifier or success flag; it is appended
through `add_action`, the same canonical entry point humans use, and the existing
Check Planner and WorldState legality decide the rest. The action carries
`source` / `control_revision` / `generated_for_round` metadata used for
de-duplication and debugging only. The run, round, seat and `control.revision`
are captured before the call and all re-verified with the phase afterwards: any
change discards the result. Provider errors or unusable output record
`AI_ACTION_SKIPPED` and never block the round. A seat that already declared an
action this round (a human acted first, the GM handed the seat over afterwards)
is never filled again: the pass only fills seats that have not declared yet.

The system prompt explicitly requires **role-playing that character sheet**
rather than playing the tactically optimal move: identity and background,
personality, values, goals and motivations, established relationships, personal
history, the clues the character already knows, private perceptions and its
current body and resources come first, and when tactical optimality conflicts
with staying in character the character wins as long as the behaviour is still
reasonable and legal; inventing a persona, history or relationship that is not on
the sheet is forbidden. The character sheet remains the single source of who the
character is: no second store such as `ai_persona` is introduced, and the
visibility channels are unchanged (own sheet, public story, own private
perceptions, knowledge explicitly visible to that character).

A control change is itself a wake-up call: once `human -> ai` has been written,
`GameControlService.set_player_control` (and an `ai_takeover` away) calls
`turns.resume_after_control_change`, which only asks whether the phase can move
on and whether the human gate is satisfied and then invokes the **same** advance
entry point described above. No human has to submit anything, no empty action is
ever faked, a still-pending human is still waited for, a repeated `set ai` in the
same round stays idempotent, and a hosted action written without the round
advancing is persisted on the spot. `ai -> human` is untouched: the control
revision race guard still discards an in-flight AI result.

Authoritative combat outside exploration takes a different road. An AI-hosted
PC's combat turn is submitted as a **structured intent** by
`next_automatic_intent` under server/GM automation authority, reusing the
companion's existing deterministic ladder (`_allied_automatic_intent`: heal a
downed ally, attack the nearest hostile, move toward it, Dodge, End Turn). No
second combat engine is introduced and no LLM is involved at this stage. When a
control change hands over the seat that is currently the actor, the single entry
point is `ruleset_gameplay.resume_authoritative_combat`: it reuses the same
automatic ladder loop (`advance_automatic_intents` in
`src/rulesets/automation.py`, which `submit_intent` also uses), advances until a
human's turn, the end of combat or no automatic intent is left, and persists the
advance together with the control record. When the current actor is somebody
else, not a single byte of state changes -- no actor is ever seized. A failure
rolls the whole transaction back and returns a structured
`AUTOMATIC_TURN_FAILED` error instead of half-committing. The one
real difference from a companion is 0 HP: a companion makes no death save, but a
player character must, or combat would stall on that seat. Intents still travel
validate / resolve / apply on the same authoritative chain and obey the same
action economy (action / attacks_remaining / movement), reading only that seat's
own character sheet; a candidate that is not legal in the current state falls
back to a legal `end_turn`, so a hosted seat's turn always terminates. Validation
is tightened in step: a `player:` actor may only be submitted by `gm_uid` while
the seat really is `ai`-hosted, otherwise "a player can submit intents only for
their own character" still holds, so a human can neither play an AI seat by hand
nor control it manually. A `human` or `unclaimed` seat never yields an automatic
intent.

The room and the table can now *express* who plays a seat. At creation each
character card chooses "I control it / wait for a player to claim it / AI hosted",
with a room-level default for unclaimed cards that a per-card choice overrides;
when neither is given the legacy behaviour stands (every seat `human`), and an
unknown mode fails closed at creation (`INVALID_PLAYER_CONTROL`) instead of
quietly building a default seat. The roster shows four badges: human, AI hosted,
unclaimed, and temporarily AI hosted.

What "away" means is a room setting, `away_control_policy`, defaulting to
`pause`: stepping away changes presence only and **never** hands the character to
the AI. Under `ai_takeover`, stepping away hands the seat to the server AI in its
*temporary* shape (`{mode: ai, temporary: true, resume_mode: human}`) and coming
back returns it, clearing `temporary` / `resume_mode`; temporary hosting is still
returnable after a restart and never becomes permanent. The GM also has "set to
AI / stop AI hosting", which changes only the control record -- it does not copy
the character, touch the Web identity or Bot mapping, reset ready state, reset HP,
or rebuild the combat actor. Every control change only happens at a safe boundary
(`ACTIVE_ACTION` with no round in flight), otherwise the caller gets the retryable
`CONTROL_CHANGE_BUSY`. A disconnect **never** triggers takeover: only an explicit
GM action, an explicit player away, or an explicit room setting can. The setting
is persisted, so the instance schema moves **14 → 15**: every older save becomes
`pause`, which is what it actually did, and a corrupt value degrades to `pause` too.

The chat (Bot) entry point is equivalent to the Web one: `host Character Name` /
`unhost Character Name` let the GM change a seat's controller from the group, over
the same server-side control API -- the bridge holds no control state of its own
and there is no second authority. These are a different contract from
`away` / `back` (which change presence, not the controller), and they require the
GM or an authorized account; the target must match exactly one roster character,
otherwise the bot lists what is available instead of guessing. When the server
answers `CONTROL_CHANGE_BUSY`, the group gets a friendly "retry after this round"
notice rather than a raw failure.

## D&D 2024 Authoritative Play State

`core:dnd2024` combat, Session 0, and campaign records share `GameInstance.ruleset_state.version` and one EventBatch ledger. An optional adventure supplies story input through its exact binding but is not part of the Ruleset Bundle. Combat and campaign events have separate reducers; the runtime composition root dispatches explicit intent types without making the generic engine import D&D code.

The mechanics authority for an advanced-rules character is `ruleset_character`. Creation, shared-library import/edit, joining a game, in-game profile editing, advancement, and rest all go through the `character_lifecycle` capability; legacy top-level character fields are compatibility projections only. Profile edits cannot overwrite abilities, HP, AC, advancement history, or runtime/content/state versions. Mechanical changes are revalidated or replayed from canonical choices and history.

Every Session 0 revision clears stale consent and can be locked only after all current players accept. Tasks, clues, facts, important items, and relationships enter a pending proposal before a separate GM intent confirms or rejects them. Chapter summaries are deterministic projections of confirmed events and are copied to long-term memory only after the authoritative save succeeds.

Free-text actions continue through DiceFrame's single `/action` round loop: solo play advances immediately, while multiplayer waits for every active, present character before one combined adjudication and GM response. The D&D runtime only adds read-only authoritative combat, campaign, and current-adventure state to that same LLM context; the selected Worldbook and matched lore still come from the generic narrative pipeline. The LLM cannot create campaign facts, spend resources, or advance authoritative adventure steps.

The frontend retains the generic single timeline, single action composer, character cards, party state, map, scene gallery, rule help, Worldbook, and GM controls. The left-side `DND5E Tools` entry contains only the D&D-specific adventure/campaign and authoritative combat tools; it does not create a second message stream or narrative submission endpoint. An adventure encounter gate opens combat automatically. In free play, either an explicit GM adjudication that initiative has begun or a player attack recognized by the shared check planner creates an advisory `encounter_request` that wakes the tool; encounter selection, initiative, and every mechanical result still require authoritative combat intents. Completion returns to the same public timeline.

The runtime composition root derives story encounter access from canonical adventure steps and passes an `EncounterAccess` capability into combat; campaign and combat engines do not import each other. Combat events persist a canonical encounter instance ID, preset, and origin step, and completion enters bounded history. Campaign gates accept only the matching encounter identity, so a consumed adventure encounter cannot restart. Enemy turns are resolved automatically by the server through the same validation, event, and reducer pipeline; each player can control only their own character and other players receive an explicit waiting state. Scenes, NPCs, and map locations use canonical Adventure Bundle references; locales materialize display fields only. Direct-connect player intents use an explicit field allowlist.
