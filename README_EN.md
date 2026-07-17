# DiceFrame

English | [中文](README.md)

![DiceFrame Web UI preview](docs/assets/diceframe-readme-hero.png)

DiceFrame is a self-hostable AI tabletop RPG platform supporting DND/COC/custom rules, with multiplayer WebUI.

It brings the Web UI, character sheets, lorebooks, dice checks, state changes, campaign logs, and optional chat-bot play into one shared game state. Players describe what they want to do in natural language; DiceFrame passes those actions to a GM model, handles dice and state tags, then syncs the result back to the browser.

## What It Does

- Web UI for creating games, joining tables, managing characters, lorebooks, rules, logs, and settings.
- Solo and multiplayer play, with invite links, waiting states, away players, GM force-advance, and SSE updates.
- Dice and state handling for d20 / d100 checks, HP, gold, items, XP, death, revival, and scene changes.
- Lorebook entries for NPCs, locations, items, events, puzzles, and factions.
- Long-session summaries, with optional embedding-based memory recall.
- AI-assisted world, rule, character, and lorebook generation.
- Docker support for Linux deployment, with runtime data mounted under `data/`.

## Quick Start

Requirements:

- Python 3.10+
- Node.js 20+
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
docker compose up -d --build
```

Open:

```text
http://localhost:18000
```

Runtime data is stored in `data/`.

## First Game

1. Open the Web UI.
2. Switch the app language to English if needed.
3. Go to Create.
4. Choose Game Language: English.
5. Pick the English fantasy template, create or import characters, then enter Play.
6. Submit actions in natural language.
7. If DiceFrame asks for a roll, roll first; the GM narration resumes after the result is recorded.

## Languages And Content

- App language controls menus, buttons, settings, and UI messages.
- Game Language controls GM narration, opening scenes, summaries, quick actions, and AI-generated content.
- World templates, lorebooks, and content packs declare their content language with `language`. Create prioritizes matching templates while still allowing other-language content.
- Rule JSON stays as one mechanics file. Add localized display fields such as `rule_name_en`, `name_en`, and `skill_pools_en` when English-facing names or generation hints are needed.

More player-facing help is in [docs/USER_GUIDE_EN.md](docs/USER_GUIDE_EN.md).

## Data And Privacy

Runtime data is stored in:

```text
data/
```

This may include API keys, access tokens, saves, plugin data, logs, and private campaign content. Treat it as your own table notebook: back it up if you need it, and check it before sharing it with anyone.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

You may use, modify, and distribute DiceFrame under the terms of the AGPL-3.0. If you distribute a modified version, or make a modified version available as a network service, you must provide the corresponding source code as required by the license.
