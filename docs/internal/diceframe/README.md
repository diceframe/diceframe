# DiceFrame platform plan reconciliation

This directory records the internal plan inputs and the implementation slice
that was carried into the `codex/r04-rc01-completion` pull request. It is
maintainer material, not a player-facing contract.

## Documents reconciled

The working documents supplied for this task were:

- `diceframe_implementation_plan_main_2026-11-10.md`
- `diceframe_ecosystem_plan_2026-11-10.md`
- `diceframe_missing_requirements_for_6_1_2026-11-11.md`
- `diceframe_docs_revision_2026-11-11.md`
- `diceframe_embedding_model_switch_reindex_issue.md`
- `diceframe_optional_local_models_plan_2026-11-12.md`

The first four remain the authoritative planning inputs. The last two are
supplemental implementation tracks. Their working copies live outside the
repository; this file records the changes that were applied so a PR reviewer
can audit them without importing local paths, databases, credentials, or the
private FU fork.

## What changed in the implementation plan

- RC-01 is implemented as independent character creation rule selection with
  game-entry compatibility checks.
- SK-01 has a server-owned sequential/composite action contract, staged event
  publication, dependency failure handling, idempotent receipts, and a Play UI
  entry that appears only when a runtime declares the capability.
- Creator work now has project revisions, validation, preview, build and
  submission receipts. The visible Creator taxonomy is: content assets,
  project workspace, unified content extension package, and single-adventure
  backup.
- Plugin installation has dependency planning, atomic group installation,
  install receipts, provider lifecycle guards, marketplace status handling and
  unified discovery.
- Runtime/UI extension seams are explicit and fail closed: DTO/stdio
  transport, runtime probes, gameplay projection, declarative UI isolation,
  and bounded Creator workers.

## What changed in the embedding tracks

- Long-term memory vectors now carry a model profile, source-content hash and
  dimension. Legacy vectors remain readable but are stale until rebuilt and
  cannot participate in a different profile's semantic recall.
- Memory migrations are additive and preserve old rows. The current memory
  schema is v5; known experimental v4 databases upgrade in place.
- Rebuilds run in background batches. The administrator settings page shows
  total, ready and pending counts and provides an explicit rebuild action.
- Optional local embedding has a verified ONNX CPU backend, a fixed catalog,
  SHA-256/size checks, proxy support, Hugging Face primary URLs,
  `hf-mirror.com` mirror fallback and atomic installation.

## FU boundary

FU remains an external ruleset/runtime target. The private FU source directory
was not copied into this repository and is not represented as a first-party
runtime. This PR delivers the host contracts, installation identity, transport,
projection and UI boundaries needed for an external runtime. A separate FU
submission must be sanitized, versioned, stripped of runtime data and packaged
as a valid `.dfplugin` before it can be installed and tested as an external
runtime.

## Validation

The current structural validation result is stored in
`diceframe_docs_validation_2026-11-13.json`: 18 checks passed and 31 task cards
were parsed. The repository gate passed with 146 tests, one skip, frontend
type-check/lint, and the production build.

The 2026-11-11 report remains historical evidence; it is not reused as the
current result.
