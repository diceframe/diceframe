# DiceFrame Plugin Registry and Review Policy

[中文](PLUGIN_REGISTRY_CN.md) | English

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

## Risk and update policy

| Level | Meaning | Updates |
|---|---|---|
| `declarative` | Content, theme, or map plugin without a process entrypoint | Automatic while permissions and runtime remain unchanged |
| `unrestricted-process` | Launches Python, Node, an executable, or another process | Notification only; installation requires user confirmation |
| `bundled` | Maintained by the DiceFrame organization and shipped with the application | Updated with DiceFrame |
| `approval-required` | A release expanded permissions or changed runtime behavior | Installation and updates pause for another review |

A third-party process runs with the operating-system privileges of the current user. Environment filtering and permission declarations are not an operating-system sandbox, so the store displays a prominent high-privilege warning.

## Later releases

Ordinary releases do not require another registry submission. Authors only:

1. Update `plugin.json.version`.
2. Commit, tag, and push the code.
3. Publish a stable GitHub Release.

DiceFrame resolves the latest Release while installing or checking for updates, then downloads the source snapshot at that Release's fixed commit. The registry's daily workflow is only a display cache; client updates continue to work if GitHub suspends scheduled workflows in a long-idle repository.

Another review is required for repository or ID changes, ownership transfer, permission expansion, a change from declarative to process execution, or a new sensitive-data, network, or file-access model.

## Installation formats

- Public plugins from the store: install the repository source snapshot referenced by a GitHub Release into `plugins/<id>/`.
- Private, file-sharing, or offline distribution: use one `.dfplugin` file. It is a constrained ZIP container with a fixed `.dfplugin` extension.
- Local development: place the directory at `plugins/<id>/`, then choose “Rescan local folder” in settings.

Local `.dfplugin` installation does not imply registry approval. The installer still enforces path, symlink, duplicate-entry, file-count, size, manifest, and schema checks.

## Token separation

DiceFrame generates an independent internal token for every process plugin that requires `diceframe.http` and injects it only into that plugin process. Bundled plugins such as QQ / NapCat require no DiceFrame Bot Token configuration. The global Bot API Token in settings is only for external programs connected by the user; regenerating it does not affect bundled plugins.

## Registry statement

Inclusion means that registry format and review rules were satisfied. It is not a warranty of security, quality, continued maintenance, or suitability. Listings expose the repository, risk level, permissions, and update policy.
