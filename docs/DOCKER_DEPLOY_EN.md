# Docker and Linux Deployment

[中文](DOCKER_DEPLOY_CN.md) | English

Docker is optional. If you prefer running DiceFrame directly on your computer, Windows, macOS, and Linux can still start it with:

```bash
pip install -r requirements.txt
python web_server.py
```

The container stores runtime data in `/app/data`; Compose maps it to `./data` in the project directory. Saves, settings, access credentials, and plugin runtime data remain on the host when the image is rebuilt.

## Quick Start

Using the published image:

```bash
cp .env.example .env
# Edit .env and set TRPG_LLM_API_KEY. Override base URL/model only when needed.
docker compose pull
docker compose up -d
```

Open `http://localhost:9876`. To change the host port:

```env
DICEFRAME_HTTP_PORT=8080
```

Then open `http://localhost:8080`. The internal port remains `9876`, keeping WebUI, plugin-host, and internal API addresses stable.

## Common Commands

```bash
docker compose pull
docker compose up -d
docker compose logs -f
docker compose down
```

To rebuild an image after changing the local source:

```bash
docker compose up -d --build
docker compose build --no-cache
```

Settings only notifies Docker/NAS installations about a new version; it does not replace files inside the container. NAS users can check and pull the new image in the device's container manager.

## Data and Secrets

- `./data` is runtime storage and is excluded by `.gitignore` and `.dockerignore`.
- `.env` contains machine-specific deployment settings and is also excluded.
- `.env.example` is public and must not contain real API keys, tokens, group IDs, or private addresses.
- Without `TRPG_ACCESS_TOKEN`, DiceFrame generates an initial access password in `./data/access_token.txt`.
- To reset a forgotten WebUI password, create `./data/reset_access_password.txt`, put the new password inside, and restart. DiceFrame removes the file after a successful reset.

## QQ / NapCat

The Docker deployment uses the same built-in plugin host. When enabled, QQ/NapCat runs as a child process inside the main service container.

1. Start the WebUI with `docker compose up -d`.
2. Enable QQ / NapCat on the WebUI plugin page.
3. If NapCat runs outside the container, use a host or NAS address reachable from the container for `NAPCAT_HOST` and `NAPCAT_PORT`.

The built-in QQ plugin does not require a manually entered DiceFrame Bot API Token. DiceFrame generates and persists it. An external MaiBot bridge copies the value from Settings → Bot API.

Optional initial values:

```env
# Optional fixed global Bot API Token; leave empty for automatic generation.
TRPG_BOT_TOKEN=
NAPCAT_HOST=192.168.1.10
NAPCAT_PORT=3001
NAPCAT_TOKEN=
```

Inside Linux containers, `127.0.0.1` refers to the container itself. For NapCat on the host, use the host LAN address or the mapped `host.docker.internal` name.

## Using Desktop and Docker Data

Direct desktop use reads the project's `data/`; Compose maps the same `./data`. Switching between them therefore keeps the same saves and settings.

For a separate Docker environment:

```bash
TRPG_DATA_DIR=./data-docker docker compose up
```

or change the Compose volume:

```yaml
volumes:
  - ./data-docker:/app/data
```
