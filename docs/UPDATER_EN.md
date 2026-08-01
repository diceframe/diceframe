# DiceFrame Application Updates

[中文](UPDATER_CN.md) | English

This page explains how to update DiceFrame itself. Plugins are updated separately through the Plugin Store.

## Installation Methods

| Installation | How to update |
|---|---|
| Windows portable | Check, download, and apply updates from Settings |
| Extracted source release | Apply the update from Settings, then restart manually |
| Git checkout | Run `git pull` after receiving a new-version notification |
| Docker / NAS | Pull the latest image after receiving a new-version notification |

Updating the application does not delete saves or settings. Backing up the complete `data/` folder before an upgrade is still recommended.

## Windows Portable

Open **Settings → Version Update**, then follow the prompts to download and apply the update. DiceFrame restarts automatically and refreshes the page when the update finishes.

If the new version cannot start correctly, DiceFrame automatically returns to the previous version. Downloaded packages and older unused versions are removed after a successful update.

Portable installations keep at most two application payloads. The root-level `app/` and `python/` directories from the original archive count as the first payload and remain as the rollback copy after the first update. After the second and later successful updates, only the current and previous payloads under `versions/` are kept, and the legacy root-level `app/` and `python/` directories are removed. `data/`, `logs/`, and the root launcher are not version payloads and are never removed by this cleanup. If the current payload is unavailable, the launcher uses the version pointer to try the previous payload.

If Settings shows a new version but no Apply button, manually download the latest portable package from GitHub Releases once. Later updates can then be applied from Settings.

## Source Release

A source package downloaded and extracted from GitHub Releases can be updated from Settings. Restart DiceFrame manually when prompted.

DiceFrame backs up the previous application files and attempts to restore them if the update fails. Only the latest backup is kept after a successful update, and saves and settings are not overwritten.

If you use a Git checkout, continue updating it with `git pull`.

## Docker and NAS

For a Docker Compose deployment, run:

```bash
docker compose pull
docker compose up -d
```

NAS users can use the device's container manager to check for updates, pull the latest image, and recreate the container. Make sure `data/` is mounted from the host.

For an image built from a local source checkout, pull the new source and run:

```bash
docker compose up -d --build
```

## Troubleshooting

### Update check returns HTTP 403

The temporary anonymous request quota for the mirrors and GitHub may be exhausted. This affects only version checks, not games, saves, or model calls. Retry later or check GitHub Releases directly.

### An update fails

Do not delete `data/`. Portable installations attempt to return to the previous version automatically, while source installations attempt to restore the previous application files. If DiceFrame still cannot start, download the appropriate package again from GitHub Releases and keep the logs for diagnosis.
