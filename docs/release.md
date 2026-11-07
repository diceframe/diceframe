# DiceFrame release process

This document describes the public, repository-scoped release mechanism. It is
not a source of credentials or a grant of publishing authority. GitHub and
Docker credentials stay in repository secrets, and maintainer-only planning
documents stay outside this public contract.

## Release entry points

`.github/workflows/release.yml` is the canonical release pipeline.

- A `v*` tag builds the source Windows package, the Windows portable package,
  and the managed Docker update package.
- The workflow validates the package names, SHA-256 sidecars, Docker update
  manifest, and a combined `SHA256SUMS` file before publishing.
- The public GitHub Release assets are the three ZIP files and
  `SHA256SUMS`. The per-ZIP `.sha256` files are workflow verification
  sidecars; they are not uploaded as public release assets.
- A stable tag publishes the versioned GHCR and Docker Hub images and updates
  `latest`. A prerelease tag publishes only its versioned image tags.
- `workflow_dispatch` can build workflow artifacts, but the publish jobs still
  require a tag ref.

`.github/workflows/docker.yml` is a separate, manually authorized repair or
re-publish entry point. It checks out the requested `v<version>` tag, downloads
the Docker update seed from the matching GitHub Release, and does not create a
Release. It currently leaves `latest` unchanged, so it must not be treated as
a replacement for the canonical tag pipeline or run without explicit approval
for the requested version.

## Before publishing

1. Decide the exact version first. `src/version.py` and the tag must agree on
   the `X.Y.Z` or prerelease version.
2. Work from a clean checkout. The package builders reject uncommitted changes
   unless `--allow-dirty` is used for a local test package only.
3. Run the relevant CI checks and review `RELEASE_NOTES.md`. The hand-written
   Chinese and English summary, upgrade notes, backup guidance, and download
   names are the player-facing source of truth. The first line must be
   `# DiceFrame v<version>`; the release workflow fails closed if it does not
   match the tag.
4. Confirm that no credential value, local data, log, database, or internal
   planning document is being added to the release inputs.

## Release notes

The GitHub Release is created with `RELEASE_NOTES.md` as its leading body and
GitHub's generated notes enabled. GitHub appends the merged-PR list, authors,
and comparison link after the hand-written summary. The workflow checks for an
existing Release before creating one; a retry after a partial upload skips the
body-creation step so it does not regenerate or overwrite the existing notes.

The repository currently has no established PR-label policy, so no custom
`.github/release.yml` classification is enabled yet. Establish and verify the
labels used by merged PRs before adding release-note categories; unlabelled
history should remain visible in GitHub's fallback section rather than being
silently excluded.

## Publish and verify

Creating and pushing the tag is the authorization boundary. It can create a
GitHub Release, publish container images, and update `latest`; preparing notes
or running a local package build must never create the tag.

After the workflow completes, check the Release body, the three ZIP assets,
`SHA256SUMS`, the versioned container tags, and (for a stable tag) both
`latest` tags. If a run fails after the Release exists, inspect that Release
before retrying. Asset uploads use replacement semantics, so a retry must use
the same version and verified seed.

There is no automatic rollback. Reverting a published version or image is a
separate, explicitly authorized maintenance action; deleting and recreating a
Release is not the default retry procedure.

## Scope boundary

The public contract is limited to the workflows, build and validation scripts,
release notes, and this runbook. It does not include user-level Codex Skills,
tokens, account permissions, local paths, or `docs/internal/` maintainer
materials.
