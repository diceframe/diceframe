# Content Track C handoff — 2026-09-29

This branch is a continuation point for the Content Track C implementation. It is intentionally left reviewable and does not claim that the entire track is complete.

## Current state

- Contract and ownership groundwork is in place for A–H.
- D projection wiring, E canonical game content bindings, F canonical-first UI wiring, G generic import contracts, and the I projection-read portion are implemented in this branch.
- Compatibility paths are retained deliberately while callers are migrated. They are not evidence that the old contracts are safe to delete yet.
- The branch is based on the local `origin/main` snapshot used for this handoff. Rebase onto the latest upstream `main` before continuing when network access is available.

## What landed here

- Source-aware content references and import contracts (`ContentDraft`, `CommitPlan`, reference closure).
- Canonical projections for world, lorebook/book, knowledge, map, character/NPC, and game-scoped content reads.
- Canonical game bindings with module-slot storage, validation, transaction rollback, and lifecycle wiring.
- Lorebook and character-card import callers using the generic draft/preview path.
- Create-game and lorebook UI paths preferring canonical refs/routes while retaining a compatibility fallback.
- Legacy import adapters and receipts kept at an explicit boundary.

The implementation is split across the commits on this branch; do not squash away the commit boundaries before review because they make the ownership and migration sequence easier to audit.

## Deliberately unfinished / next work

1. **I — legacy caller cleanup is not complete.** `src/webui/routes/worlds.py`, `Entry.world_id`, starter-lorebook/template synchronization, and plugin legacy auto-import still have compatibility responsibilities. Remove them only after the remaining caller inventory is migrated and the shrink-only architecture guard reaches zero.
2. **Residual legacy reads remain.** Some round-action, retrieval, character, knowledge, game-creation, plugin, and world adapter paths still accept or project `world_id`. These are compatibility boundaries, not new canonical ownership.
3. **Canonical lifecycle inputs are additive for now.** `world_ref` and `book_bindings` are supported, while legacy inputs such as `lorebook_world_id`, `source_world_id`, `blank_lorebook`, and `create_lorebook` remain for old clients/tests.
4. **G needs a second pass.** Commit-time revalidation, complete cross-content export/import closure, and a user-facing three-way conflict policy/UI are not finished. External module adapters remain on hold until their contracts are agreed.
5. **F UI cleanup is partial.** Canonical-first create/lorebook flows are wired, but broader UI consistency, localized release-note presentation, and the remaining module UX work are outside this handoff.
6. **Known pre-existing persistence bug remains separate.** Unbound-ruleset D&D games can write state that is lost on reload. This branch intentionally does not choose between fail-closed, auto-bind, or direct-persistence semantics.

## Verification completed

- Backend: `5314 passed, 1 skipped, 313 warnings` (`pytest -q`).
- Frontend typecheck: passed.
- Frontend production build: passed (only existing asset-path/chunk-size warnings).
- Frontend Vitest, serialized: `108 passed`, `641 passed` (`npm run test -- --run --maxWorkers=1`).
- `git diff --check`: passed.

The frontend install used the available Node/npm runtime, which reports an engine warning against the package's Node/npm range; typecheck, build, and tests still passed.

## Suggested continuation order

1. Rebase onto current upstream `main` and resolve any conflicts without changing the canonical contract shapes casually.
2. Inventory every remaining legacy world/book read and migrate one owner at a time; keep the compatibility allowlist shrink-only.
3. Add commit-time import-plan revalidation and complete reference-closure tests for G.
4. Finish the F UI cleanup and update architecture/engineering notes with the final removal gates.
5. Re-run the backend and serialized frontend suites before splitting or merging follow-up PRs.

