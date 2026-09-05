# DiceFrame Architecture Source of Truth

This document describes the current implementation, not a roadmap. The dependency direction is `routes -> WebAPI -> services -> core`; core code must not import `src.webui`, WebAPI methods are delegates, and cross-service calls go through API delegates.

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

## GameInstance Aggregate Boundary

`GameInstance` remains the aggregate root for one game. It owns authoritative runtime state, invariants, state transitions, and coordination through `_authority_lock` / `_process_lock` / `_lock`. Lock order is authority → process → state; runtime locks are neither persisted nor copied by persisted-state replacement. Historical rewrites exclusively own the authority gate, while ordinary live writers use the same atomic gate to reject before mutation without a separate boolean-check TOCTOU. Players, combat, rounds, and payments are not split into independent aggregates merely to shorten the source file.

Each save has both a stable `game_key` and a rotatable `run_id`. Process recovery preserves the run; reset and restart build and atomically install a candidate while holding the old aggregate write lock. Waiting old-run writes resume after replacement and fail their stale-run fence, while opening effects already applied to candidate characters are preserved. Historical swipe rewrites share `_process_lock` with normal round processing and hold it through restore → LLM → branch application → authoritative save; player actions are rejected before mutation while the rewrite is active. Durable memory is isolated by a persisted `memory_namespace`, so isolation does not depend on destructive cleanup. Save-shape upgrades enter only through the sequential migration owner in `src/migrations/instance.py`.

Generic economy state belongs to `GameInstance`. Narrative tags, lore text, and AI output may create proposals but cannot mutate balances. Committed changes pass server-side permission, balance, run-identity, and idempotency checks and produce an auditable transaction. `currency.amount` is authoritative and `gold` is a compatibility projection. Web, Bot, and alternate transports use the same economy path.

An economy proposal is also a narrative commit barrier. While the current run has an undecided proposal, unresolved effect group, or external effect awaiting delivery or reversal, actions, forced advancement, luck continuation, SSE, and direct round processing cannot begin more narration. Scene, character state, loot, quest, memory, private-information, and quick-action effects from the same model response are persisted as deferred effects and cannot become authoritative before settlement. One proposal commits its effects once; when one response creates multiple proposals, all must commit before the shared effect group applies, while any decline, cancellation, or insufficient-funds result discards it. SQLite memory is a cross-store external effect: it enters a durable game-state outbox, is delivered idempotently under a delivery identity, and records verifiable before/after state. Swipe or rollback persists a reversal request and restores memory still attributable to that delivery; an unpersisted delivery or reversal receipt is retried at startup or before the next progression attempt. A transaction-associated scene-image prompt is removed from staged effect application and may start asynchronous generation only after the first authoritative save succeeds. Terminal outcomes are retained in a bounded ledger and injected as trusted server context that overrides earlier narration. An economy decision revision fences stale AI responses that were already in flight. Reset and restart clear proposals, transactions, outcomes, deferred effects, the outbox, and the revision; restart preserves only balances already committed to character state, while reset also removes characters.

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

## D&D 2024 Authoritative Play State

`core:dnd2024` combat, Session 0, and campaign records share `GameInstance.ruleset_state.version` and one EventBatch ledger. An optional adventure supplies story input through its exact binding but is not part of the Ruleset Bundle. Combat and campaign events have separate reducers; the runtime composition root dispatches explicit intent types without making the generic engine import D&D code.

The mechanics authority for an advanced-rules character is `ruleset_character`. Creation, shared-library import/edit, joining a game, in-game profile editing, advancement, and rest all go through the `character_lifecycle` capability; legacy top-level character fields are compatibility projections only. Profile edits cannot overwrite abilities, HP, AC, advancement history, or runtime/content/state versions. Mechanical changes are revalidated or replayed from canonical choices and history.

Every Session 0 revision clears stale consent and can be locked only after all current players accept. Tasks, clues, facts, important items, and relationships enter a pending proposal before a separate GM intent confirms or rejects them. Chapter summaries are deterministic projections of confirmed events and are copied to long-term memory only after the authoritative save succeeds.

Free-text actions continue through DiceFrame's single `/action` round loop: solo play advances immediately, while multiplayer waits for every active, present character before one combined adjudication and GM response. The D&D runtime only adds read-only authoritative combat, campaign, and current-adventure state to that same LLM context; the selected Worldbook and matched lore still come from the generic narrative pipeline. The LLM cannot create campaign facts, spend resources, or advance authoritative adventure steps.

The frontend retains the generic single timeline, single action composer, character cards, party state, map, scene gallery, rule help, Worldbook, and GM controls. The left-side `DND5E Tools` entry contains only the D&D-specific adventure/campaign and authoritative combat tools; it does not create a second message stream or narrative submission endpoint. An adventure encounter gate opens combat automatically. In free play, either an explicit GM adjudication that initiative has begun or a player attack recognized by the shared check planner creates an advisory `encounter_request` that wakes the tool; encounter selection, initiative, and every mechanical result still require authoritative combat intents. Completion returns to the same public timeline.

The runtime composition root derives story encounter access from canonical adventure steps and passes an `EncounterAccess` capability into combat; campaign and combat engines do not import each other. Combat events persist a canonical encounter instance ID, preset, and origin step, and completion enters bounded history. Campaign gates accept only the matching encounter identity, so a consumed adventure encounter cannot restart. Enemy turns are resolved automatically by the server through the same validation, event, and reducer pipeline; each player can control only their own character and other players receive an explicit waiting state. Scenes, NPCs, and map locations use canonical Adventure Bundle references; locales materialize display fields only. Direct-connect player intents use an explicit field allowlist.
