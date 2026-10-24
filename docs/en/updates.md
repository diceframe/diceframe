# DiceFrame Application Updates

[中文](../zh/updates.md) | English

This page explains how to update DiceFrame itself. Plugins are updated separately through the Plugin Store under **Manage → Plugins**.

## Installation Methods

| Installation | How to update |
|---|---|
| Windows portable | Check, download, and apply updates from Settings |
| Extracted source release | Apply the update from Settings, then restart manually |
| Git checkout | Run `git pull` after receiving a new-version notification |
| Managed Docker / NAS | Download, apply, and automatically roll back from Settings |
| Legacy Docker image or base-runtime update | Pull the latest image and recreate the container |

Updating the application does not delete saves or settings. Backing up the complete `data/` folder before an upgrade is still recommended.

## Direct Docker upgrades and retained data

Existing runtime-1 managed Docker installations can apply this package from Settings without replacing their image, provided the Python ABI and system runtime are compatible. Ordinary application version numbers do not impose an image-upgrade barrier.

After the launcher stops the old server, the candidate creates a private copy of game/content data, including consistent SQLite backups with committed WAL content. It migrates only that copy. The old program's original data stays readable. The old launcher still checks the actual candidate server's health and probation period; no maintenance stub pretends the application is healthy.

The version/data binding is staged separately and promoted only after the launcher commits the program. Business writes, plugins and external outbox delivery wait until that commitment. The UI shows validation in progress. Copy failure, startup failure or an abrupt candidate exit leaves the previous dataset intact, allowing the existing launcher to restart the old program automatically.

Restarting a committed version reuses its data. A later upgrade copies the currently active dataset; a manual downgrade uses the retained dataset for that older program. The confirmation explains that progress/content changes since the upgrade are not carried back. Host settings, provider keys, passwords, sessions, device revocations and HTTPS certificates remain current. Manual data rollback clears game invitation credentials and closes player entrances; the GM must reopen/reinvite them. Automatic failure rollback does not revoke the original invitations.

Back up the complete mounted `data/` directory. **Do not exclude `data/_updater/data-versions/`, `data-bindings/` or `data-pending/`: these contain business data and its recovery records.** The launcher retains current/previous program payloads; data cleanup retains their paired copies and any copies still referenced by local file paths in shared host settings, then removes verified retired copies. The initial legacy dataset stays intact. Copying needs additional disk space; if preparation cannot complete within the old launcher's startup window, the update fails safely and the old service resumes. Actual Python/system-runtime incompatibility still requires a new image.

Supported game saves migrate to schema 47 and Lorebook databases to schema 12; ambiguous or unsupported inputs are rejected. Source and Windows portable installations retain their existing update mechanisms and need a complete pre-upgrade backup for cross-format recovery. Legacy inline AI configuration is not guessed into providers; configure valid provider/role references when needed.

Save migration 44→45 separates kernel game configuration, narration preferences and dice presentation preferences, and moves reward policy into economy storage; 45→46 establishes a reader fence for the new story, growth and finale write contracts without guessing historical plans, growth marks or reversals. Perspective, `None` versus empty prompt overrides, zero luck timeout, reward amounts and ledger entries retain their values. Migration operates on a copy; unsupported slot versions or mixed layouts reject without overwriting the source save. This storage schema number is not an application release version.

Migration 46→47 adds the one-use resource recovery authority and its reader fence. It does not invent recovery qualifications from historical narration or logs. Beta.2 and older programs cannot read upgraded saves; a downgrade needs the matching retained dataset or pre-upgrade backup. The startup world-identity upgrade also rebinds known legacy world and Book references while preserving display names and content; ambiguous, conflicting or unsupported future data stops the migration before publication.

## Windows Portable

Open **Manage → Settings → About**, then use the Version Update section to download and apply the update. DiceFrame restarts automatically and refreshes the page when the update finishes.

If the new version cannot start correctly, the launcher attempts to return to the previous program. That cannot undo migrated data; use the matching backup at a data-format boundary as described above. Downloaded packages and older unused versions are removed after a successful update.

When a previous payload is available, **Manage → Settings → General → System maintenance** shows **Restore previous version**. Version checks, download, installation, and the update channel remain under **About**. After confirmation, the launcher stops the current server, starts the previous payload with the same health check and probation period, and swaps the current/previous pointers. Both payloads remain available so the switch can be reversed. The action only switches application files and does not delete `data/`; backing up data is still recommended.

Portable installations keep at most two application payloads. The root-level `app/` and `python/` directories from the original archive count as the first payload and remain as the rollback copy after the first update. After the second and later successful updates, only the current and previous payloads under `versions/` are kept, and the legacy root-level `app/` and `python/` directories are removed. `data/`, `logs/`, and the root launcher are not version payloads and are never removed by this cleanup. User-installed plugin source code lives under `data/plugin-packages/` and is preserved with `data/` across versions; `app/plugins/` holds only built-in and example plugins and is cleared with old version payloads. If the current payload is unavailable, the launcher uses the version pointer to try the previous payload.

DiceFrame keeps console output while also writing persistent runtime logs. Windows portable installations use the root-level `logs/` directory, managed Docker uses persistent `data/logs/`, and source runs default to the project-root `logs/` directory. Logs rotate daily and retain the latest 30 days. They can also be removed from **Manage → Settings → General → Runtime logs**. Runtime logs are independent from game history, chat history, and saves.

If Settings shows a new version but no Apply button, manually download the latest portable package from GitHub Releases once. Later updates can then be applied from Settings.

## Source Release

A source package downloaded and extracted from GitHub Releases can be updated from Settings. Restart DiceFrame manually when prompted.

DiceFrame backs up the previous application files and attempts to restore them if the update fails. Only the latest backup is kept after a successful update, and saves and settings are not overwritten.

If you use a Git checkout, continue updating it with `git pull`.

## Docker and NAS

Docker users must manually pull one baseline image that supports managed updates. Its stable launcher manages the current and previous application payloads under `data/_updater/docker-versions/`. After that migration, ordinary DiceFrame application releases can be downloaded and applied from the Version Update section under **Manage → Settings → About**. The container ID stays unchanged, and a candidate is committed only after its health check and probation period succeed; otherwise the launcher returns to the previous version.

Managed Docker also exposes **Restore previous version** under **Manage → Settings → General → System maintenance**. The stable launcher performs the health check and probation period, then swaps the `current.json` pointers while keeping both application payloads. This is available only for payloads that pass the existing `data_rollback_safe: true` contract.

Docker Update manifests still require `data_rollback_safe: true` and a matching runtime API/Python ABI. Packages using `data_mode: versioned-copy-v1` preserve the previous program’s dataset instead of requiring it to read the candidate’s new format. The bootstrap and declared data schemas are validated. Legacy compatible packages retain their existing contract. Shared host settings/security must remain backward compatible; a change to that shared contract needs a separate transition.

Python ABI, system library, CA, font, or launcher protocol changes remain base-runtime updates and still require pulling a new image. DiceFrame never mounts the Docker socket and does not control the Docker daemon from inside the container.

For a Docker Compose deployment, run:

```bash
cd /path/to/diceframe  # Must contain docker-compose.yml
docker compose pull
docker compose up -d
```

`no configuration file provided: not found` means the current directory has no Compose file. Change to the original deployment directory and retry. If the container was originally started with `docker run`, pull the new image and recreate it with the original ports, volumes, and environment variables instead of applying Compose commands.

NAS users can use the device's container manager to check for updates, pull the latest image, and recreate the container. Make sure `data/` is mounted from the host.

Saves, settings, and user plugins remain under `data/`. `data/_updater/docker-versions/` contains application payloads, while neighboring versioned data and binding records contain persistent business data. Include the entire updater directory in backups; do not remove its pointers or data records independently. Traditional image updates remain supported, and each image uses the byte-identical Docker application update asset from the corresponding GitHub Release as its seed.

For an image built from a local source checkout, pull the new source and run:

```bash
docker compose up -d --build
```

## Troubleshooting

### Update check returns HTTP 403

The temporary anonymous request quota for the mirrors and GitHub may be exhausted. This affects only version checks, not games, saves, or model calls. Retry later or check GitHub Releases directly.

### An update fails

Do not delete `data/`. Portable and managed-Docker installations attempt to return to the previous version automatically, while source installations attempt to restore the previous application files. If DiceFrame still cannot start, download the appropriate package again from GitHub Releases and keep the logs for diagnosis.

Source releases and Git checkouts do not have launcher-managed dual payloads, so the Settings page does not offer automatic rollback; restore the previous application files manually or use the matching GitHub Release after preserving the data backup.
