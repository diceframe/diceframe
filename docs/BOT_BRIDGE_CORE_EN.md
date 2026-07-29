# Shared Bot Bridge Core

[中文](BOT_BRIDGE_CORE_CN.md) | English

Bot Bridge connects DiceFrame to chat platforms. Adapters for NapCat/QQ, Discord, Telegram, and others handle each platform's messages and presentation while DiceFrame keeps the tabletop logic in one place.

The code lives in `src/bots/bridge_core/`.

## Responsibilities

- **HTTP API client** (`client.py`): calls DiceFrame REST endpoints for game creation and binding, actions, dice, payments, progression, and related operations.
- **Session and player mapping store** (`store.py`): persistently maps a platform chat stream and user to a DiceFrame `game_key` and player UID.
- **Trigger policy** (`triggers.py`): recognizes prefixes and filters events.
- **Shared command matching** (`commands.py`): parses commands and routes them to business operations.
- **Language resolution** (`language.py`): normalizes the game language and recognizes explicit Chinese or English commands before binding.
- **Text presenters** (`presenters.py`): renders platform-neutral Chinese or English text and accepts a configurable `command_prefix`.
- **Service orchestration** (`DiceFrameBridgeService` in `service.py`): coordinates the client, store, commands, and presenters for adapters.

## Adapter Responsibilities

An adapter only:

- Reads platform messages and extracts text, platform users, and chat streams.
- Handles platform configuration, plugin lifecycle, and outgoing replies.
- Implements platform-specific behavior such as NapCat image cards, direct-message delivery, and group-event synchronization.

Incoming events become `BridgeInput` values passed to `DiceFrameBridgeService`; the adapter sends the returned response through the platform.

The HTTP client authenticates through `X-Bot-Token`. The host generates and injects a separate internal token for each managed plugin. Only standalone external bridges copy the global token from Settings → Bot API. `/api/bot/ping` verifies both URL and token independently from whether QQ/NapCat is enabled.

## Language

When binding succeeds, DiceFrame returns the game's language and the bridge stores it with the chat-stream binding. Help, status, recap, map, payment, character-creation, and error messages then use that language. Existing bindings without a language field continue to default to Chinese.

The shared core and QQ/NapCat currently accept both Chinese and English commands. English games can use commands such as `help`, `join`, `status`, `recap`, `map`, `roll`, `pay`, `advance`, `away`, `back`, and `create character`; campaign actions remain natural-language text. The game setting is authoritative, so the bridge does not repeatedly guess from each story action.

## Trigger Policy

Explicit prefixes are supported by default:

- `跑团 ...`
- `/df ...`
- `/diceframe ...`

Each adapter decides whether a bare command after mentioning the bot is safe. Platforms where a mention may trigger another default reply should use `prefix_only`. Legacy bare-mention behavior should require an explicit option such as `mention_bare`, while help text continues to recommend prefixes.

## Adding Another Platform

A Discord, Telegram, or other adapter does not need to reimplement tabletop behavior. It normally only needs to:

1. Convert a platform event into `BridgeInput`, including the channel, user, text, and mention state.
2. Call `DiceFrameBridgeService`.
3. Send the replies from `BridgeResult` back through the platform.
4. Add optional platform presentation such as Discord slash commands, embeds, direct messages, or permission mapping.

An adapter that needs rich cards, buttons, or proactive synchronization can follow the QQ adapter and reuse shared command matchers and presenters. Payment ownership, character claims, progression permissions, language, and DiceFrame API calls remain shared.

## Plugin Extensions

DiceFrame `bot-extension` process plugins can extend chat behavior without modifying `bridge_core` source:

```text
platform message
→ before_message (add commands, change or intercept input)
→ built-in Bridge / game core
→ after_result (transform presentation data)
→ render (replace text, image, or card output)
→ platform delivery
```

NapCat calls the extension route with its host-injected internal Bot token. The MaiBot plugin detects the protocol through `/api/bot/ping`, uses the same route when available, and keeps its legacy behavior otherwise. Failures, timeouts, and unhandled results fall back to built-in presentation.

Outputs may use `text`, `card`, or `image`. Dynamic images must be written under the plugin-specific runtime directory; DiceFrame validates the path, format, and 10 MB size limit before exposing an authenticated asset route. See the [plugin development guide](PLUGIN_DEVELOPMENT_EN.md#711-bot-bridge-extensions) for the protocol and example.

Extensions control chat commands and presentation only. Character state, rolls, balances, payment confirmation, and saved-game changes remain authoritative in DiceFrame's game core.

## Current Status

- NapCat/QQ uses the shared client, store, command matching, and presenters. Rich cards, direct messages, and platform synchronization remain in the QQ adapter.
- The shared core and QQ/NapCat now support Chinese and English player-facing commands, help, and primary responses.
- Presenter command text accepts `command_prefix`; QQ displays platform mentions, while generic examples use `跑团` for Chinese and `/df` for English.
- The Web settings page supports `.dfplugin` installation, local rescanning, and uninstall. Package standards are documented in [PLUGIN_DEVELOPMENT_EN.md](PLUGIN_DEVELOPMENT_EN.md).
- Bot Bridge extensions support `before_message`, `after_result`, and `render` in QQ/NapCat and the external MaiBot bridge.
