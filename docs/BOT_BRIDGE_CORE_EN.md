# Shared Bot Bridge Core

[中文](BOT_BRIDGE_CORE_CN.md) | English

Bot Bridge connects DiceFrame to chat platforms. Adapters for NapCat/QQ, Discord, Telegram, and others handle each platform's messages and presentation while DiceFrame keeps the tabletop logic in one place.

The code lives in `src/bots/bridge_core/`.

## Responsibilities

- **HTTP API client** (`client.py`): calls DiceFrame REST endpoints for game creation and binding, actions, dice, payments, progression, and related operations.
- **Session and player mapping store** (`store.py`): persistently maps a platform chat stream and user to a DiceFrame `game_key` and player UID.
- **Trigger policy** (`triggers.py`): recognizes prefixes and filters events.
- **Shared command matching** (`commands.py`): parses commands and routes them to business operations.
- **Text presenters** (`presenters.py`): renders results as platform-neutral text and accepts a configurable `command_prefix`.
- **Service orchestration** (`DiceFrameBridgeService` in `service.py`): coordinates the client, store, commands, and presenters for adapters.

## Adapter Responsibilities

An adapter only:

- Reads platform messages and extracts text, platform users, and chat streams.
- Handles platform configuration, plugin lifecycle, and outgoing replies.
- Implements platform-specific behavior such as NapCat image cards, direct-message delivery, and group-event synchronization.

Incoming events become `BridgeInput` values passed to `DiceFrameBridgeService`; the adapter sends the returned response through the platform.

The HTTP client authenticates through `X-Bot-Token`. The host generates and injects a separate internal token for each managed plugin. Only standalone external bridges copy the global token from Settings → Bot API. `/api/bot/ping` verifies both URL and token independently from whether QQ/NapCat is enabled.

## Trigger Policy

Explicit prefixes are supported by default:

- `跑团 ...`
- `/df ...`
- `/diceframe ...`

Each adapter decides whether a bare command after mentioning the bot is safe. Platforms where a mention may trigger another default reply should use `prefix_only`. Legacy bare-mention behavior should require an explicit option such as `mention_bare`, while help text continues to recommend prefixes.

## Current Status

- NapCat/QQ uses the shared client, store, command matching, and presenters. Rich cards, direct messages, and platform synchronization remain in the QQ adapter.
- Presenter command text accepts `command_prefix`; QQ defaults to `@me`, while the generic service defaults to `跑团`.
- The Web settings page supports `.dfplugin` installation, local rescanning, and uninstall. Package standards are documented in [PLUGIN_DEVELOPMENT_EN.md](PLUGIN_DEVELOPMENT_EN.md).
