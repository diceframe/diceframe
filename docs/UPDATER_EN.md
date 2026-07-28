# DiceFrame Application Updates

[中文](UPDATER_CN.md) | English

This page explains how to update DiceFrame itself. Plugins are updated separately through the Plugin Store.

## Installation Modes

| Installation | Settings behavior | Apply behavior |
|---|---|---|
| Windows portable | Download and apply | Automatic restart and health check; automatic rollback on failure |
| Extracted source release | Download and apply | Transactional file replacement; manual restart after success |
| Git development checkout | Check and notify only | Update with `git pull`; the working tree is never overwritten |
| Docker / NAS | Check and notify only | Pull a new image and recreate the container |

The application updater does not replace `data/`. Backing up the complete `data/` directory before an upgrade is still recommended.

## Windows Portable

Portable updates install the candidate beside the running version:

```text
DiceFrame/
  DiceFrame.exe
  app/
  python/
  versions/
    vX.Y.Z/
      app/
      python/
  data/
    _updater/
```

After an update is applied, the launcher:

1. starts the candidate version;
2. calls the public health endpoint and checks the target version;
3. observes the process for another 60 seconds;
4. commits the active-version pointer after success;
5. stops the candidate and restarts the old version if startup, health checking, or probation fails.

The launcher shipped with v1.6.0 does not yet have supervisor support. The first move from v1.6.0 to a release containing the new launcher therefore requires one manual upgrade. Later portable releases can switch and roll back automatically.

## Extracted Source Releases

An extracted release without `.git/` can apply an update from Settings. DiceFrame backs up program files before moving in replacements and attempts to restore the complete backup if any step fails.

The updater preserves:

- `data/`
- `logs/`
- `.git/`
- `.codex/`
- `.claude/`
- `dist/`

Settings asks for a manual restart after a successful replacement. Git checkouts do not use this workflow.

## Docker and NAS

DiceFrame does not replace files inside a running container. For a Compose deployment:

```bash
docker compose pull
docker compose up -d
```

NAS users can instead use the device's container manager to check for a newer image, pull it, and recreate the container. Make sure `data/` is mounted from the host.

For an image built from a local source checkout, pull the new source and run:

```bash
docker compose up -d --build
```

## Download and Safety Checks

- Packages are downloaded under `data/_updater/`.
- SHA-256 is verified when a Release provides a `.sha256` sidecar.
- ZIP extraction rejects absolute paths, drive paths, `..` traversal, symbolic links, abnormal member counts, and abnormal expanded size.
- A portable candidate must contain the Web service, bundled Python runtime, and launcher.
- The health endpoint exposes only `ok`, version, and process ID.

## Troubleshooting

### Update check returns HTTP 403

The anonymous request quota for both mirrors and the GitHub API may be temporarily exhausted. This affects only update notifications; games, saves, and model calls continue to work. Retry later or check the project's Releases page directly.

### Applying an update fails

Keep `data/_updater/state.json` and the relevant logs for diagnosis. Do not delete `data/`. “Rolled back” means the portable launcher has restarted the old version. A source update reports whether its backup was restored.

### Release acceptance

In addition to automated tests, every release that changes the updater should be exercised with real portable packages:

1. one successful upgrade;
2. one candidate that fails to start or exits early;
3. confirmation that the failed candidate returns to the old version.

## HTTP API

Application updates use:

- `GET /api/system/update-check`
- `GET /api/system/update/status`
- `POST /api/system/update/download?kind=source|portable`
- `POST /api/system/update/apply`
- `GET /api/system/update/health`

States are `idle`, `downloading`, `verifying`, `staged`, `applying`, `restarting`, `done`, `rolled-back`, and `failed`.
