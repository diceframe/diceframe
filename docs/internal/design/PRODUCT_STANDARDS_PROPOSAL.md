# Product standards and durable advantages

English | [简体中文](PRODUCT_STANDARDS_PROPOSAL_CN.md)

Status: proposal for maintainer discussion, 2026-10-15. This note does not introduce a schema, API, migration, adopted ADR or release promise. Existing [engineering rules](../../ENGINEERING_RULES.md), [architecture](../../ARCHITECTURE_EN.md) and [accepted ADRs](../../adr/README.md) remain authoritative.

## Positioning

Build an extensible, self-hostable game platform where AI helps create and narrate, the server owns game state, and creators can reuse their content across campaigns and supported interfaces. Make this promise visible in complete user journeys, not only in internal abstractions.

Feature count alone is a weak advantage. Friends & Fables already advertises an AI GM, lore, world-building tools, branching quests, multiplayer and tactical 5e combat. AI Dungeon documents automatic summaries and memory retrieval. Foundry documents package identity, dependency and compatibility metadata. These are facts from their own documentation, not a comparative reliability benchmark. The proposed direction below is our inference from that overlap. Sources checked on 2026-10-15: [Friends & Fables](https://fables.gg/), [AI Dungeon memory](https://help.aidungeon.com/faq/the-memory-system), [Foundry module development](https://foundryvtt.com/article/module-development/).

## Standards to make enforceable

| Area | Product promise | Evidence for acceptance |
| --- | --- | --- |
| Content | World, Book, Adventure and package have distinct roles; a title never substitutes for canonical identity | Same-title resources from different sources remain distinct through import, edit, export and game creation; users can see what each game actually references |
| Rules and modes | Capabilities declare supported mechanics, progression and transport access; every advertised restriction is enforced on the server | A Bot marked read-only cannot mutate; absent optional capabilities have safe defaults; missing access/authority dependencies reject unsafe operations |
| AI operations | Models propose operations; validated services authorize and execute them; users can understand progress, cancellation and cost scope | No unconfirmed persistent write; repeated confirmations are idempotent; cancellation preserves reviewable drafts; per-request output budgets are not mislabeled as total spending caps |
| User data | Updates preserve user-owned content and explain incompatibility before modifying it | Old save fixtures, independent databases, failed upgrade recovery and referenced-content preservation pass at supported version boundaries |
| Interface | Common paths stay compact; hidden settings remain discoverable; every unavailable action explains what is missing | Import → review → publish → start works on desktop and phone; main actions fit the viewport; task panels can fold; errors and completion are observable |
| Extensions | A feature has one owner and a public contract; transports and central views compose it | A new ruleset/mode/provider uses a capability, adapter or registry; it does not add concrete mechanics branches throughout generic paths |
| Maintenance | Community contributions carry a bounded maintenance obligation | Core English/Chinese are maintained; community locales follow the existing active/paused and source-review policy with English fallback; no accidental permanent obligation for the core maintainer |

Do not create a parallel universal manifest now. First inventory and reuse ContentRef, package manifests, ruleset capabilities, module hooks and translation policy. Where a new contract is necessary, name its owner, version and failure behavior explicitly. Package compatibility and ownership metadata must represent evidence, not self-certified marketing claims.

For issue #535, the proposed boundary is: access control is essential; rollback and transaction integrity are essential even if economy gameplay is optional. Keep the minimum authoritative contract required by those promises, and separate optional presentation/policy capabilities. A high count of failing tests after removing a module does not by itself prove the whole module should be kernel. Inspect callers and failure modes before changing `KERNEL_MODULES`. Split `table_settings` only with an explicit persisted-slot plan that preserves existing values.

## Existing formats: preserve their owners

Verified against this checkout on 2026-10-15. These are current implementation facts; later sections explicitly propose work still needed.

| Surface | Current contract and owner | Practical consequence |
| --- | --- | --- |
| Installable package | `.dfplugin`, ZIP-compatible, one `plugin.json`; [PluginHost](../../../src/plugin_host/host.py) validates it | Reuse this distribution envelope for content and executable extensions; risk is determined separately |
| Package manifest | `schema_version: 1`; Content V2 uses `content_schema_version: 2`, typed locale schema 1 | A package release number, an envelope schema and a resource schema are different versions |
| Adventure module | `plugin_type: content-pack`, `content_profile: adventure-module`, `content_delivery_mode: catalog`, explicit `adventure_packages` directories; [profile policy](../../../src/plugin_host/support.py) | Installation registers a catalogue. Launch/materialization is deliberate; legacy packages without the fields retain their legacy auto-import behavior |
| Adventure payload | Its own `manifest.json`, graph v1/v2 and declared runtime requirement; [bundle loader](../../../src/adventures/bundle.py), [graph v2](../../../src/adventures/graph_v2.py) | Chapters, scenes, choices and encounters belong here, not in a second marketplace adventure format |
| Ruleset bundle/catalogue | [Ruleset Bundle](../../../src/rulesets/bundle.py) and runtime-owned catalogue validators | Rules content snapshots and mechanics capabilities are distinct from an adventure's plot and from the installation envelope |
| Portable objects | [ContentRef](../../../src/content_modules/refs.py), per-kind schemas and [synchronization contract](../../content-sync/CONTRACT.md) | World, Book, Adventure and Character keep their own authority; a shared reference does not erase those differences |
| Content V2 resources | [ResourceRef](../../../src/content/contracts.py), registry and display-only locale overlays | Package/resource identity stays independent of translated names; ResourceRef and ContentRef are not interchangeable strings |
| Runtime extensions | [Type descriptors](../../../src/plugin_host/support.py), [SDK](../../../src/plugin_sdk/contracts.py) and public services | Channels, tools, Providers and Bot extensions reuse their existing lifecycle/protocol; `import-export` remains reserved |
| Installation checks | [Shared module validator](../../../src/webui/services/module_validation.py), CLI and installation paths | Declared adventures/catalogues and runtime requirements have real checks. Parser success alone does not prove a playable campaign |
| Marketplace | [Catalogue/release resolver](../../../src/plugin_host/marketplace.py) | Hub catalogue with repository fallback, release-to-commit resolution, permission/risk review and adventure metadata projection already exist |

The content-market category **Adventures** can therefore be a filtered view of existing content packages. A package may contain several adventures plus worlds, books, characters and assets. Package details should list its adventures, and each adventure should have a start action. No separate installer or package extension is needed for this proposal.

Catalog mode now rejects an executable `entrypoint` through the shared profile validator, including adventure modules. Legacy static-type packages without catalog mode can still carry an entrypoint, and [risk policy](../../../src/plugin_host/policy.py) classifies them as `unrestricted-process`. Marketplace presentation must use that actual risk rather than assuming every `content-pack` is pure data.

## Format conventions to adopt incrementally

The following are proposed authoring/admission conventions, not new fields that the current runtime already understands.

1. **Human-readable source, validated data.** New canonical data uses UTF-8 JSON, stable relative paths and consistent two-space formatting in examples/exporters. Markdown carries instructions and prose; images/audio remain referenced assets. A PDF is an import source, not an executable native module. Do not rewrite existing user data merely to normalize whitespace or filename style.
2. **Identity before titles.** Package IDs follow the host's existing grammar; resource and adventure IDs follow their respective validators. Do not impose one new regex across persisted identities. Same titles from two creators remain distinguishable by source and canonical ID. Clone creates new identity; editing a title does not. Resolve structured ContentRefs through the owning resolver, including explicit source/digest when required.
3. **Separate versions.** Recommend [SemVer](https://semver.org/spec/v2.0.0.html) for new package releases after defining that package's public contract: patch for compatible corrections, minor for compatible additions, major for incompatible author-visible changes. Schema/runtime contract integers keep their existing meanings. Increasing a package major version never migrates an existing save automatically. A release version should map to immutable content; corrections get a new version. Enforcement of immutability is proposed, not guaranteed by Git tags.
4. **Dependencies must be real.** Use existing `requires.rulesets` with `id` and integer `minimum_version`, plus each adventure's runtime requirement. Do not equate a ruleset runtime contract version with the DiceFrame application version. Cross-package references must resolve to an explicit compatible source or fail with a useful blocker. A general dependency solver, arbitrary version ranges and automatic downloads are not established by these fields; add them only with an owned, versioned contract and cycle/conflict/rollback behavior.
5. **Locale translates display only.** English and Simplified Chinese are maintained core interfaces/docs. A creator may publish content in one language; truthful language metadata and fallback matter more than requiring every author to translate. Community translations have named ownership and can pause under the existing [maintenance policy](../../en/translations.md). Language must not change mechanics, private visibility or identity.
6. **Describe actual automation.** Existing adventure levels are `reference`, `guided`, `deterministic`. Report them for the tested adventure/feature scope, not as proof that every monster, spell or branch is automated. Prose describing a spell is not an executable spell implementation. Do not guess marketplace automation from a title or an NPC count.
7. **State source rights and responsibilities.** Proposed public admission requires a maintainer/contact route, source repository, instructions, change notes, license and relevant asset attribution. Prefer an [SPDX identifier](https://spdx.org/licenses/) when applicable, with separate notices for differently licensed components. A license string or successful import is not proof of redistribution rights. Private local import and public redistribution have distinct admission requirements. Do not silently relicense or delete existing local packages.

An adventure-containing exporter already emits this envelope fragment; it is **not a complete installable example** and has no new proposed API fields:

```json
{
  "schema_version": 1,
  "content_schema_version": 2,
  "locale_schema_version": 1,
  "plugin_type": "content-pack",
  "content_profile": "adventure-module",
  "content_delivery_mode": "catalog",
  "adventure_packages": ["adventures/lantern-harbor"]
}
```

The real package still needs its identity, version, configuration schema, contributions, assets and validated adventure payload. Use [the exporter](../../../src/webui/services/content_pack_export.py) and actual examples; do not install a documentation fragment.

## Presentation conventions

Reuse the existing theme tokens and feature components; do not create a parallel design system for every editor. Proposed shared acceptance rules:

| Surface | Expected behavior |
| --- | --- |
| Forms | Aligned labels and controls; one place for related help text; advanced choices fold without losing values |
| Actions | One clear primary action per task; related secondary actions grouped; no duplicate entry unless its local context has a useful purpose |
| Cards and filters | Consistent compact density and control heights; actions align; distinguish a Book's title, its world, source and maintenance state |
| Phone layouts | Main content remains readable; overlays stay inside the viewport; task panels fold; navigation does not cover the composer |
| Async operations | Visible pending/success/failure; explain unavailable actions; cancel where supported; describe actual AI cost scope |
| Narrative | Explicit GM paragraph boundaries remain separate blocks in history and streaming; the current two-em first-line style applies to each block rather than only the first visual line |

Keep authored content separate from presentation: do not insert full-width spaces into saved prose to simulate indentation. Escape untrusted narration; protocol/state data must not become arbitrary executable HTML. A renderer fix should preserve authoritative text and stored state.

## Plugin and marketplace admission

Treat these as separate questions: **What does it contain? What can it execute? Which exact version was checked? Who maintains it?**

| Track | Existing basis | Proposed acceptance requirement |
| --- | --- | --- |
| Declarative content | Content packs, adventure/catalogue validation, path budgets | No entrypoint; complete references/assets; actual runtime support; GM-only information stays private; install/start/save/reload works |
| Executable extension | Managed processes, declared permissions, SDK/HTTP contracts | Start/stop/restart and child cleanup; descriptor/input validation; game/actor/privacy checks; timeout/failure behavior; clear process risk |
| Theme/voice assets | Declared contributions and type-specific filtering | Relevant asset/theme checks, source rights and usable previews; do not require unrelated adventure tests |

Process plugins run as the operating-system user; manifest permissions and Agent `read_only` declarations are not an OS sandbox. A package adding process execution, secrets access or another permission must be reviewed as a changed risk, even if its marketing category stays the same. Continue existing permission/risk expansion rejection. Do not turn it into unconditional automatic update approval.

Proposed lifecycle, expressed as product behavior rather than invented API enums:

1. Author prepares a package and runs the shared validator. Blocking diagnostics identify file/resource/reference and remediation; warnings stay distinct from blockers.
2. The content repository/Hub intake validates the exact submitted version and artifact. Separate format, compatibility, executable-risk review and playability evidence. Record which application/runtime versions and which adventure were tested.
3. Publish a catalogue entry only after that admission passes. Show author, languages, target rules/runtime, automation scope, included resources, installation behavior, dependencies, risk and maintenance state.
4. Installation previews changes and known affected saves. On completion show **Installed**, then **View content** / **Use this adventure**. Failures retain a reason and retry path; a disabled button explains its blocker.
5. Updates repeat validation and compare permissions, references and bound saves. Existing game bindings retain their pinned identity/version/digest; missing historical packages or incompatible replacements block rather than silently selecting latest. Side-by-side historical versions and a universal package rollback store are not promised by this proposal.
6. A maintained, unmaintained, deprecated or withdrawn listing states its reason and verified compatibility separately. An unmaintained harmless package need not disappear; a serious risk can block future distribution. Delisting does not silently erase users' installed content or their saves.

SHA-256 calculated **after downloading** identifies those received bytes; without an independently trusted expected digest/signature it is not publisher authentication. A pinned commit gives traceability, not a complete security or gameplay certificate. Proposed future release records should bind the reviewed version, source commit, expected artifact digest, validation report and reviewer scope. Cryptographic publisher signing is a later, separately designed extension.

The app normalizes Hub/catalogue fields, but this checkout does not establish how the separately deployed Hub authenticates publishers or approves uploads. Verify that implementation before calling the whole publication chain enforced. Keep new review records server-owned; never accept an author's `verified` or `official` label as sufficient evidence.

## Keep format documentation and validators aligned

The checked external [plugin guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-development.md) still describes smaller archive budgets and a reserved Provider type; this checkout has larger [package limits](../../../src/plugin_host/package_limits.py) and a supported Provider descriptor. This is a concrete documentation drift to resolve, not a reason to trust one moving page over a running version.

The separate **plugin index repository** is also an implementation owner, not merely documentation. Its [registry validator at the inspected revision](https://github.com/diceframe/diceframe-plugins/blob/e1cc03aec1a1f55aa851991cade4908912a19d73/scripts/registry.cjs) still rejects Provider submissions and the core's `ai.providers` permission. A direct validation probe also accepts `adventure-module` with `legacy_autoimport`, a combination the core rejects. Existing manifest passthrough preserves adventure metadata, so the gap is not simply missing fields: intake needs matching semantic validation and artifact evidence. Its CI currently runs registry tests/validation, without the core's deep module validator.

Align supported types, effective permission policy, module profiles and release verification against an explicit supported core revision. Preserve minimum application version requirements when enabling newer capabilities; do not make older clients appear compatible by just extending the index allowlist. Add shared positive/negative fixtures so intake and installation agree without executing untrusted plugin entrypoints. Keep source identity, approved permissions and changed-risk reapproval intact.

Proposed ownership:

| Owner | Owns | Must not become |
| --- | --- | --- |
| Core repository | Schemas, validators, runtime capabilities, installation and save contracts, versioned examples | A second manually copied market catalogue |
| Plugin index repository | Submission validation, maintainer admission workflow, approved permission baselines, index schema and catalogue synchronization | An independently drifting copy of supported types, permissions or module semantics |
| Content repository | Creator quickstart, original/licensed example packages, publication guidance and compatibility evidence | A separate reimplementation of core validation |
| Hub | Publication authorization, catalogue lifecycle, version/artifact/review records, distribution decisions | The authority for game mechanics or user saves |
| Author/maintainer | Content correctness, source rights, supported versions, extension behavior and translations they volunteer for | An implicit permanent obligation for the core maintainer |

Generate support/limit/schema reference snippets from the actual descriptors/constants where practical. Run example validation against the intended core revision in CI; a schema ID URL alone does not mean that URL is hosted. Long-term contract changes need an ADR and migration/rejection plan; adding optional marketplace metadata needs an owned design/DTO, not an engine-wide schema rewrite. Keep normative contract docs separate from proposals and session handoffs.

## Advantages worth investing in

1. **Trustworthy long campaigns.** Deterministic rules, recoverable updates, state history and multiplayer privacy let users invest time without constantly repairing AI mistakes. Contract tests, saved fixtures and failure recovery accumulate into an advantage; an assertion of reliability alone does not.
2. **A reusable creator ecosystem.** A creator can draft or import material, correct it, publish a versioned package, and prove it works in a game. Good examples, import diagnostics, source identity, dependency closure and author maintenance are more valuable than adding many disconnected editors. Respect creators' rights and publish only licensed or original fixtures.
3. **One game across interfaces and models.** Web, mobile and Bot clients use the same authoritative game. Replaceable model transports and self-hosting reduce provider dependence, but the experience must survive model changes, disconnects and reconnection. Provider-specific protocol behavior belongs in an adapter; API settings keep the existing provider-reference contract.
4. **A public conformance kit.** Reusable sample packs, validator diagnostics and observable behavioral tests help outside authors produce compatible content. A badge should name the tested version and scope; passing an import parser is not proof of gameplay compatibility. Avoid an expensive marketplace or certification bureaucracy before authors can complete the basic workflow.

The accumulating assets are compatible creator content, extension authors, reproducible campaign fixtures, reliable migrations and user trust. Keep user data portable. Do not depend on lock-in, secret prompts, unsupported claims about model intelligence, or collecting private campaign transcripts by default.

## Execution plan with observable completion

These are dependency-ordered work packages, not release dates or a claim that all are implemented. Each change should be independently reviewable/rollbackable where safe; keep a contract change and its necessary migration atomic.

| Stage | Deliverable and owner | Completion evidence |
| --- | --- | --- |
| A. Establish the baseline | Core + plugin index + content maintainers inventory formats and drift; select an original small adventure fixture; record current creator journey and compatibility failures | Same fixture validates, imports, starts, saves and reloads; negative fixtures reproduce known blockers; existing users' supported upgrade path is exercised |
| B. Make the journey dependable | Feature owners fix import/review/publication, cancellation, actionable errors, model compatibility and concurrency before more preview modes | Desktop/phone journey completes without manual data-file repair; cancel preserves drafts; stale/duplicate writes and unauthorized operations are rejected; no private content leaks |
| C. Make authors self-sufficient | Content + core + plugin index owners publish version-matched English/Chinese quickstart, starter pack, shared validator output and migration notes | A contributor can create a package without editing core; malformed refs/runtime/path/private projections fail predictably; intake, CI and install agree |
| D. Connect adventure discovery | Core library/market features + Hub expose Adventures as a content-package view and implement reviewed release metadata | Install → clear result → select included adventure → start; permission expansion re-enters review; existing bound saves survive safe updates or explain the blocking conflict |
| E. Grow the catalogue responsibly | Maintainers add CoC/D&D fixtures and one focused executable extension; Hub records maintenance/review scope | At least one external author completes the workflow without bespoke core patches; stale maintenance can pause without making the core maintainer the automatic owner |

Start with A and B. For the first delivery use one small original adventure with two scenes, one branch, a private clue, a Book, a map/image and declared runtime support. Exercise the same artifact through validation, private import, export/reimport, game creation, player projection, save/restart and attempted incompatible update. A fixture with a claimed deterministic encounter must actually exercise the target rules runtime; a prose-only fixture must say reference/guided. Add further examples only when each brings a new contract, not just a new skin.

The local implementation now includes the original [Lantern Harbor starter](../../../plugins/examples/starter-adventure/README.md).
Its [conformance test](../../../tests/test_starter_adventure_conformance.py) exercises
source and artifact validation, install, game creation, private projection,
save/reload, export/reimport and bound-save update rejection. Negative cases cover
executable entrypoints, path escape, missing runtime, invalid world relations and
locale mechanics overrides. It is a guided example with a graph map, not a
deterministic combat certification. Marketplace admission, release evidence and
external-author milestones in C–E remain separate work.

Existing author commands, run from the matching core revision:

```bash
python scripts/validate_content_module.py path/to/my-content-pack --json
python scripts/package_plugin.py path/to/my-content-pack
```

The module validator reuses installation's deep adventure/catalogue/runtime checks and returns nonzero on blockers. The packager validates the host envelope and creates an archive; packaging alone is not the deep module or gameplay acceptance step. Executable/theme authors use the relevant host/SDK behavior checks rather than the content-only CLI. CI must validate the **produced artifact** as well as the source directory before associating a report with that artifact's digest.

Every work package should identify the observed user failure, owning module, affected stable contract, implementation, behavioral evidence, documentation and recovery/rejection behavior. Proposals do not become mandatory enforcement until their owning implementation and tests land. Do not adopt all proposed fields at once or automatically break older local packages to make a marketplace policy simpler.

Measure completed creator journeys, understandable blockers, recovery success, rejected unauthorized/stale writes, reference preservation, tested model compatibility and cost visibility. Establish a baseline and proposed targets before the relevant milestone. Public badges must state the tested version and scope; never turn a parser check into a blanket “fully compatible” claim. The accumulating advantage is the content, authors and verified long-running campaigns that survive these journeys.
