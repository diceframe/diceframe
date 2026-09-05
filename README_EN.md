<p align="center">
  <img src="docs/assets/diceframe-logo.svg" width="144" height="144" alt="DiceFrame Logo">
</p>

<h1 align="center">DiceFrame</h1>

<p align="center">English | <a href="README.md">中文</a></p>

<p align="center"><a href="https://github.com/diceframe/diceframe/stargazers"><img src="https://img.shields.io/github/stars/diceframe/diceframe?style=flat-square&logo=github&label=Stars" alt="GitHub Stars"></a> <a href="https://github.com/diceframe/diceframe/releases"><img src="https://img.shields.io/github/v/release/diceframe/diceframe?style=flat-square&logo=github&label=Release" alt="GitHub Release"></a> <a href="https://github.com/diceframe/diceframe/blob/main/LICENSE"><img src="https://img.shields.io/github/license/diceframe/diceframe?style=flat-square&logo=github&label=License" alt="License"></a></p>

<p align="center"><a href="https://diceframe.com">Official Website</a></p>

![DiceFrame Web UI preview](docs/assets/diceframe-readme-hero.jpg)

DiceFrame is a self-hostable **AI tabletop RPG engine** supporting **D&D / CoC / custom rules** with a **multiplayer Web UI**.

It brings the Web UI, character sheets, lorebooks, dice checks, state changes, campaign logs, and optional chat-bot play into one shared game state. Players describe what they want to do in natural language; DiceFrame passes those actions to a GM model, handles dice and state tags, then syncs the result back to the browser.

## What It Does

- A Web UI organized around Overview, Play, Characters, Content, and Management. Content contains Lorebook, Worlds, Adventures, and Rules; Management contains Memory, Logs, Plugins, and Settings.
- Solo and multiplayer play, with invite links, waiting states, away players, GM force-advance, SSE updates, and experimental WebRTC player direct connect through one-time link codes.
- Layered resolution for D&D 5e-inspired Lite, custom d20, CoC 7e-inspired d100, and no-dice narrative rules, including rule-declared advantage/disadvantage, CoC bonus/penalty dice, HP, gold, items, XP, death, revival, and scene changes.
- Lorebook entries for NPCs, locations, items, events, puzzles, and factions.
- Long-session summaries, with optional embedding-based memory recall.
- AI-assisted world, rule, character, and lorebook generation.
- Text-to-speech with a zero-setup system fallback, online or local OpenAI-compatible endpoints, and GPT-SoVITS. Existing service voice IDs and personal reference WAVs work directly; store voice presets are optional.
- Application updates with side-by-side portable installs, health checks, rollback, and install-specific guidance.
- Docker support for Linux deployment, with runtime data mounted under `data/`.

## Contributing

Bug reports and feature requests belong in [GitHub Issues](https://github.com/diceframe/diceframe/issues). Code and documentation contributions should use a Pull Request after reading [CONTRIBUTING.md](CONTRIBUTING.md).

GitHub maintains the [Contributors](https://github.com/diceframe/diceframe/graphs/contributors) page from commit history. It may include CI, release, and coding-assistant bot accounts; those records are kept accurate and are not intended to be a manually curated list of human developers.

## Quick Start

### Windows Portable

Download the latest `DiceFrame-vX.Y.Z-windows-portable.zip` from [Releases](https://github.com/diceframe/diceframe/releases/latest), extract it, and run `DiceFrame.exe`. Open **Management → Settings → Model API**, then add an AI provider with its name, API format, Base URL, API key, and model catalog. Use **Management → Settings → Model routing** to assign the main model, fallbacks, embedding, TTS, ASR, and image-generation roles.

Windows portable builds can check for and apply updates from the Version Update section under **Management → Settings → About**.

### From Source

Requirements:

- Python 3.10+
- Node.js 20.19+ or 22.12+
- An OpenAI-compatible Chat Completions API endpoint

From source:

```bash
cd trpg

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

On first launch, add the endpoint and model catalog under **Management → Settings → Model API → AI Providers**, then assign the main model, fallbacks, and optional AI roles under **Model routing**.

**Breaking AI configuration contract:** connections use only `ai_providers` and capability `*_provider_ref` fields; keys are stored as `ai_provider_key_<id>` in secret storage. Legacy inline endpoints, keys, API formats and `TRPG_LLM_*`, `TRPG_EMBEDDING_*`, `TRPG_TTS_*`, `TRPG_ASR_*`, `TRPG_IMAGEGEN_*` environment inputs are no longer activated. No automatic migration or provider creation occurs. Updates containing old fields return HTTP 400 `unsupported AI config fields`; add providers and assign roles manually in Settings.

Main/fallback models, embedding, images and OpenAI-compatible/GPT-SoVITS speech require their corresponding provider references. Missing or unknown references leave remote capabilities unconfigured and never revive old credentials. Browser voices, edge-tts and disabled ASR need no reference; local providers may use an empty API key. Provider connection tests accept temporary credentials without falling back to old capability settings.

On Windows, `web_ui.bat` can start the Web UI. It checks Python runtime dependencies and, if `static-v2/` is missing, runs `npm ci` and `npm run build` inside `frontend-v2/` before starting `web_server.py`.

### Standalone Web Frontend

If the browser frontend should always use HTTPS while the backend keeps running on a NAS, home machine, or server, build the WebUI separately and deploy it to Cloudflare Pages or another static host:

```bash
cd frontend-v2
npm ci
npm run build:standalone
```

See the [standalone WebUI deployment guide](https://diceframe.com/en/docs?doc=standalone) for Cloudflare Pages settings, backend HTTPS, the CORS allowlist, security guidance, and troubleshooting. Packaged Windows builds, Docker, and the server-served WebUI keep using the default same-origin mode and need no standalone configuration.

### Mobile App

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

After upgrading once to a baseline image with managed updates, ordinary application releases can be applied from the Version Update section under **Management → Settings → About**, with health-checked rollback. Python or system-runtime changes still require pulling a new image. DiceFrame does not mount or control the Docker socket.

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

## First Game

1. Open the Web UI.
2. Switch the app language to English if needed.
3. Add an AI provider and assign the main model in Settings.
4. Go to Create.
5. Choose Game Language: English.
6. Pick the English fantasy template, create or import characters, then enter Play.
7. Submit actions in natural language.
8. If an action triggers a check, the system adjudicates and rolls once automatically; the GM narration then resumes.

More player-facing help is in the [DiceFrame user guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/guide.md).

## Plugins and Chat Adapters

The built-in QQ/NapCat plugin receives its DiceFrame Bot API Token automatically; users configure only the NapCat connection under **Management → Plugins**. External bridges such as MaiBot copy the service URL and token from **Management → Settings → Bot API**.

The Bot follows the bound game's language for help and primary operation messages, with native Chinese and English commands available.

Players can ask the GM an out-of-character question with `@Bot ask kp <question>` or `@Bot ask: <question>`. Answers use only the public story, current rules, and information known to the claimed character; they do not submit an action, advance the story, trigger a check, or consume the turn's action. Plain `ask the guard ...` remains an in-character action. Chinese games use `@Bot 询问 <问题>`.

The DiceFrame plugin store lets you browse and install community plugins published by their authors through GitHub Releases. Installing or updating always requires confirmation, with an additional risk warning when a plugin requests external-process access or expanded permissions. Locally or privately shared plugins can also be installed from `.dfplugin` files.

DiceFrame Hub provides review information, version status, and details for the plugin store under **Management → Plugins**. Installed plugins and local games continue to work if Hub is temporarily unavailable. Anonymous usage statistics are disabled by default and can be managed under **Management → Settings → Advanced → DiceFrame Hub and privacy**.

To develop or publish a plugin, see the [plugin development guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-development.md) and [plugin registry and review rules](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-registry.md).

## Documentation

| Topic | English | 中文 |
|-------|---------|------|
| User guide | [User guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/guide.md) | [用户手册](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md) |
| Docker deployment | [Docker deployment](https://github.com/diceframe/diceframe-content/blob/main/docs/en/deploy.md) | [Docker 部署](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/deploy.md) |
| Standalone WebUI | [Standalone WebUI](https://diceframe.com/en/docs?doc=standalone) | [独立部署 WebUI](https://diceframe.com/docs?doc=standalone) |
| Application updates | [Application updates](docs/en/updates.md) | [应用更新](docs/zh/updates.md) |
| Rules and dice | [Rules and dice](docs/en/rules-and-dice.md) | [规则与骰子](docs/zh/rules-and-dice.md) |
| Player Direct Connect (experimental) | [Player Direct Connect](docs/en/direct-connect.md) | [玩家直连](docs/zh/direct-connect.md) |
| Plugin development | [Plugin development](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-development.md) | [插件开发](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-development.md) |
| Plugin index and review | [Plugin registry](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-registry.md) | [插件索引与审核](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/plugin-registry.md) |
| Bot Bridge core | [Bot Bridge Core](https://github.com/diceframe/diceframe-content/blob/main/docs/en/bot-bridge-core.md) | [Bot Bridge 核心](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/bot-bridge-core.md) |

## Data And Privacy

Runtime data is stored in:

```text
data/
```

This may include API keys, access tokens, saves, plugin data, logs, and private campaign content. Treat it as your own table notebook: back it up if you need it, and check it before sharing it with anyone.

Custom worlds and rules are stored under `data/templates/`, so copying the complete `data/` directory also carries them to a new installation. User-installed plugin source code is stored under `data/plugin-packages/` and is preserved across application version switches.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

You may use, modify, and distribute DiceFrame under the terms of the AGPL-3.0. If you distribute a modified version, or make a modified version available as a network service, you must provide the corresponding source code as required by the license.
