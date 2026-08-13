# DiceFrame Plugin Registry and Review Policy

[中文](../zh/plugin-registry.md) | English

DiceFrame uses author-maintained source repositories with a separate public index. Authors retain their source, issues, versions, and Releases. [`diceframe/diceframe-plugins`](https://github.com/diceframe/diceframe-plugins) stores only repository locations, review baselines, and cached store metadata.

## First submission

Authors do not fork the registry, upload a ZIP, or calculate SHA-256.

1. Use a dedicated public GitHub repository with the plugin at its root.
2. Include `plugin.json`, the configuration schema, README, and LICENSE.
3. Publish a non-draft, non-prerelease GitHub Release.
4. Open the registry's “Add plugin” Issue form with the plugin ID and repository URL.
5. Fix automated errors and reply `/recheck` when necessary.
6. After validation, wait for the listing result in the submission Issue.

Validation is bound to the complete Git commit referenced by the Release. If the latest Release changes before listing, the new version must pass validation again.

## Automated validation

Automation checks at least the following:

- The repository is public, active, and uses a standard GitHub HTTPS URL.
- The latest stable Release, tag, fixed commit, and root `plugin.json` are readable.
- Plugin ID, semantic version, type, permissions, and required fields are valid.
- The configuration schema, README, and LICENSE exist.
- File count and size stay within installer limits.
- Obvious secret files such as `.env`, private keys, and credential JSON files are absent.
- The ID and repository are not already registered.
- Effective permissions are inferred from the actual entrypoint and configuration instead of trusting an empty declaration.

Automation never executes the plugin entrypoint and cannot prove that executable code is harmless.

## Content review

Automation covers structure and code safety only; it does not pre-screen content. All plugin text and assets (lorebooks, characters, rules, NPCs, items, portraits, covers, and similar material) must follow the Content Guidelines (section 8.1 of the plugin development guide). Human review checks legal boundaries, community standards, provenance, and licenses; attribution, a copyright notice, or an “unofficial fan work” label alone is not authorization. A rights notice with initial evidence that identifies an exact version may trigger temporary hiding and forwarding to the author, followed by restoration or delisting after evidence and any counter-notice are reviewed.

The community index accepts unofficial fan lorebooks, characters, NPCs, rule adaptations, and artwork created by the submitter. Packages must not directly redistribute portraits, rulebook text or scans, music, video, fonts, or other assets extracted from the source work. Fan packages without proof of permission remain marked `community`; only verified permission or an applicable open license can qualify them for a higher review marker.

## Popularity

The star count shown in the store is taken from the plugin's GitHub repository stars, refreshed daily by the index sync job. It reflects repository popularity and is a reference only, not a DiceFrame quality endorsement.

## Risk and update policy

| Level | Meaning | Updates |
|---|---|---|
| `declarative` | Content pack (optionally including maps), optional voice preset, or theme plugin without a process entrypoint | Checked and prompted when the user opens the plugin store; installing or updating requires user confirmation, while permissions and runtime remain unchanged |
| `unrestricted-process` | Launches Python, Node, an executable, or another process | Notification only; installation requires user confirmation |
| `bundled` | Maintained by the DiceFrame organization and shipped with the application | Updated with DiceFrame |
| `approval-required` | A release expanded permissions or changed runtime behavior | Installation and updates pause for another review |

`declarative` and `unrestricted-process` are determined by the plugin type descriptor (`process_mode` in `src/plugin_host/support.py`): declarative types without a process entrypoint are `declarative`, process types are `unrestricted-process`; `bundled` and `approval-required` are overlaid by release policy. New plugin types are classified automatically by their descriptor, so review rules do not need per-type edits.

A third-party process runs with the operating-system privileges of the current user. Environment filtering and permission declarations are not an operating-system sandbox, so the store displays a prominent high-privilege warning.

## Later releases

Ordinary releases do not require another registry submission. Authors only:

1. Update `plugin.json.version`.
2. Commit, tag, and push the code.
3. Publish a stable GitHub Release.

DiceFrame resolves the latest Release while installing or checking for updates, then downloads the source snapshot at that Release's fixed commit. The registry's daily workflow is only a display cache; client updates continue to work if GitHub suspends scheduled workflows in a long-idle repository.

Another review is required for repository or ID changes, ownership transfer, permission expansion, a change from declarative to process execution, or a new sensitive-data, network, or file-access model.

## Installation formats

- Public plugins from the store: install the repository source snapshot referenced by a GitHub Release into `data/plugin-packages/<id>/`.
- Private, file-sharing, or offline distribution: use one `.dfplugin` file. It is a constrained ZIP container with a fixed `.dfplugin` extension.
- Local development: place the directory at `plugins/<id>/`, then choose “Rescan local folder” in settings.

Local `.dfplugin` installation does not imply registry approval. The installer still enforces path, symlink, duplicate-entry, file-count, size, manifest, and schema checks.

## Registry statement

Inclusion means that registry format and review rules were satisfied. It is not a warranty of security, quality, continued maintenance, or suitability. Listings expose the repository, risk level, permissions, and update policy. Plugins that violate the content guidelines are delisted.
