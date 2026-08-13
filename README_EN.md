<p align="center">
  <img src="docs/assets/diceframe-logo.svg" width="144" height="144" alt="DiceFrame Logo">
</p>

<h1 align="center">DiceFrame</h1>

<p align="center">English | <a href="README.md">中文</a></p>

<p align="center"><a href="https://github.com/diceframe/diceframe/stargazers"><img src="https://img.shields.io/github/stars/diceframe/diceframe?style=flat-square&logo=github&label=Stars" alt="GitHub Stars"></a> <a href="https://github.com/diceframe/diceframe/releases"><img src="https://img.shields.io/github/v/release/diceframe/diceframe?style=flat-square&logo=github&label=Release" alt="GitHub Release"></a> <a href="https://github.com/diceframe/diceframe/blob/main/LICENSE"><img src="https://img.shields.io/github/license/diceframe/diceframe?style=flat-square&logo=github&label=License" alt="License"></a></p>

<p align="center"><a href="https://diceframe.com">Official Website</a></p>

![DiceFrame Web UI preview](docs/assets/diceframe-readme-hero.jpg)

DiceFrame is a self-hostable **AI tabletop RPG engine** supporting **DND/COC/custom rules**, with **multiplayer WebUI**.

It brings the Web UI, character sheets, lorebooks, dice checks, state changes, campaign logs, and optional chat-bot play into one shared game state. Players describe what they want to do in natural language; DiceFrame passes those actions to a GM model, handles dice and state tags, then syncs the result back to the browser.

## What It Does

- Web UI for creating games, joining tables, managing characters, lorebooks, rules, logs, and settings.
- Solo and multiplayer play, with invite links, waiting states, away players, GM force-advance, and SSE updates.
- Dice and state handling for d20 / d100 checks, HP, gold, items, XP, death, revival, and scene changes.
- Lorebook entries for NPCs, locations, items, events, puzzles, and factions.
- Long-session summaries, with optional embedding-based memory recall.
- AI-assisted world, rule, character, and lorebook generation.
- Text-to-speech with a zero-setup system fallback, online or local OpenAI-compatible endpoints, and GPT-SoVITS. Existing service voice IDs and personal reference WAVs work directly; store voice presets are optional.
- Application updates with side-by-side portable installs, health checks, rollback, and install-specific guidance.
- Docker support for Linux deployment, with runtime data mounted under `data/`.

## Quick Start

### Windows Portable

Download the latest `DiceFrame-vX.Y.Z-windows-portable.zip` from [Releases](https://github.com/diceframe/diceframe/releases/latest), extract it, and run `DiceFrame.exe`. Enter the model base URL, model name, and API key in Settings.

Windows portable builds can check for and apply updates from Settings.

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

On first launch, go to Settings and enter your model base URL, model name, and API key. You can also provide them with environment variables:

```bash
TRPG_LLM_API_KEY=your_key
TRPG_LLM_BASE_URL=https://api.openai.com/v1
TRPG_LLM_MODEL=gpt-4.1-mini
python web_server.py
```

On Windows, `web_ui.bat` can start the Web UI. It checks Python runtime dependencies and, if `static-v2/` is missing, runs `npm ci` and `npm run build` inside `frontend-v2/` before starting `web_server.py`.

## Docker

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

See the [Docker deployment guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/deploy.md) for ports, volumes, secrets, and NapCat networking.

To update a Compose deployment:

```bash
docker compose pull
docker compose up -d
```

## First Game

1. Open the Web UI.
2. Switch the app language to English if needed.
3. Go to Create.
4. Choose Game Language: English.
5. Pick the English fantasy template, create or import characters, then enter Play.
6. Submit actions in natural language.
7. If an action triggers a check, the system adjudicates and rolls once automatically; the GM narration then resumes.

## Languages And Content

- App language controls menus, buttons, settings, and UI messages.
- Game Language controls GM narration, opening scenes, summaries, quick actions, and AI-generated content.
- World templates, lorebooks, and content packs declare their content language with `language`. Create prioritizes matching templates while still allowing other-language content.
- Rules use separate language files: `<rule_id>.json` for Chinese and `<rule_id>_en.json` for English. The stable ID and mechanics fields do not change; missing English files fall back to Chinese.

More player-facing help is in the [DiceFrame user guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/guide.md).

## Plugins and Chat Adapters

The built-in QQ/NapCat plugin receives its DiceFrame Bot API Token automatically; users configure only the NapCat connection. External bridges such as MaiBot copy the service URL and token from Settings → Bot API.

The Bot follows the bound game's language for help and primary operation messages, with native Chinese and English commands available.

The plugin store indexes author-owned repositories. Installation resolves the latest stable GitHub Release to an exact commit; the store checks for updates and notifies; installing or updating always requires user confirmation, and process or permission-expanding updates require explicit confirmation. Local/private sharing uses `.dfplugin`. Supported capabilities include channel adapters, Bot Bridge command/hook/render extensions, content packs, themes, structured tools, and the location/asset subset of map packs. Import/export and Provider types remain reserved and cannot be installed from the store.

## Documentation

| Topic | English | 中文 |
|-------|---------|------|
| User guide | [User guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/guide.md) | [用户手册](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/guide.md) |
| Docker deployment | [Docker deployment](https://github.com/diceframe/diceframe-content/blob/main/docs/en/deploy.md) | [Docker 部署](https://github.com/diceframe/diceframe-content/blob/main/docs/zh/deploy.md) |
| Application updates | [Application updates](docs/en/updates.md) | [应用更新](docs/zh/updates.md) |
| Plugin development | [Plugin development](docs/en/plugin-development.md) | [插件开发](docs/zh/plugin-development.md) |
| Plugin index and review | [Plugin registry](docs/en/plugin-registry.md) | [插件索引与审核](docs/zh/plugin-registry.md) |
| Bot Bridge core | [Bot Bridge Core](docs/en/bot-bridge-core.md) | [Bot Bridge 核心](docs/zh/bot-bridge-core.md) |

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
