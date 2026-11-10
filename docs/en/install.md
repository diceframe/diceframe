# Install DiceFrame

English | [简体中文](../zh/install.md) · [Documentation](../README.md)

Choose an installation below, then follow [First game](content-and-play.md#first-game). Before upgrading, read [application updates](updates.md), including the [AI configuration contract](updates.md#ai-configuration-contract).

## Windows Portable

Download the latest `DiceFrame-vX.Y.Z-windows-portable.zip` from [Releases](https://github.com/diceframe/diceframe/releases/latest), extract it, and run `DiceFrame.exe`. Open **Manage → Settings → Model API**, then add an AI provider with its name, API format, Base URL, API key, and model catalog. Use **Manage → Settings → Model Routing** to assign the main model, fallbacks, embedding, TTS, ASR, and image-generation roles.

Windows portable builds can check for and apply updates from the Version Update section under **Manage → Settings → About**.

`DiceFrame.exe` opens DiceFrame in its own window using the Microsoft Edge WebView2 runtime that ships with Windows 11 and nearly all Windows 10 installations. Once the window is open the console closes and launcher messages go to `logs/launcher.log`; on first launch the initial access password is filled in for you. Closing the window stops DiceFrame. Without WebView2 the launcher falls back to your default browser and keeps the console; run `DiceFrame.exe --browser` to always use the browser. Other players still join through links in a browser or the mobile app.

## From Source

Requirements:

- Python 3.10+
- Node.js 20.19+ or 22.12+
- An OpenAI-compatible Chat Completions API endpoint

Clone the repository first, or extract a source release:

```bash
git clone https://github.com/diceframe/diceframe.git
```

From source:

```bash
cd diceframe

cd frontend-v2
npm ci
npm run build
cd ..

pip install -r requirements.txt
python web_server.py
```

Open:

```text
http://localhost:18000
```

On first launch, add the endpoint and model catalog under **Manage → Settings → Model API → AI Providers**, then assign the main model, fallbacks, and optional AI roles under **Model Routing**.

On Windows, `web_ui.bat` can start the Web UI. It checks Python runtime dependencies and, if `static-v2/` is missing, runs `npm ci` and `npm run build` inside `frontend-v2/` before starting `web_server.py`.

## Standalone Web Frontend

If the browser frontend should always use HTTPS while the backend keeps running on a NAS, home machine, or server, build the WebUI separately and deploy it to Cloudflare Pages or another static host:

```bash
cd frontend-v2
npm ci
npm run build:standalone
```

See the [standalone WebUI deployment guide](https://diceframe.com/en/docs?doc=standalone) for Cloudflare Pages settings, backend HTTPS, the CORS allowlist, security guidance, and troubleshooting. Packaged Windows builds, Docker, and the server-served WebUI keep using the default same-origin mode and need no standalone configuration.

## Mobile App

DiceFrame offers a dedicated Android client. Its source lives in the [diceframe-mobile](https://github.com/diceframe/diceframe-mobile) repository and installers are published on its [Releases](https://github.com/diceframe/diceframe-mobile/releases) page; please direct usage questions, feature requests, and contributions there.

## Docker

Run these commands from a cloned repository or extracted source release directory that contains `docker-compose.yml`:

```bash
cp .env.example .env
# edit .env as needed
docker compose pull
docker compose up -d
```

Open:

```text
http://localhost:9876
```

Runtime data is stored in `data/`.

For managed application updates, data retention and rollback, see [application updates](updates.md#direct-docker-upgrades-and-retained-data).

The `latest` image always tracks stable releases. Preview images are published under their explicit version tags and never replace `latest`. To try one, copy the full preview version from GitHub Releases, for example:

```bash
docker pull ghcr.io/diceframe/diceframe:2.3.0-beta.2
# Docker Hub: docker pull falconku/diceframe:2.3.0-beta.2
```

See the [Docker deployment guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/deploy.md) for ports, volumes, secrets, and NapCat networking.

To update a Compose deployment:

```bash
cd /path/to/diceframe
docker compose pull
docker compose up -d
```

If the container was originally started with `docker run`, do not switch to Compose from an arbitrary directory. Recreate it with the original ports, volumes, and environment variables as described in the deployment guide. `no configuration file provided: not found` means the current directory has no Compose file.

## Data And Privacy

Runtime data is stored in:

```text
data/
```

This may include API keys, access tokens, saves, plugin data, logs, and private campaign content. Treat it as your own table notebook: back it up if you need it, and check it before sharing it with anyone.

Custom worlds and rules are stored under `data/templates/`, so copying the complete `data/` directory also carries them to a new installation. User-installed plugin source code is stored under `data/plugin-packages/` and is preserved across application version switches.
