# DiceFrame User Guide

[中文](USER_GUIDE_CN.md) | English

Whether you are a GM preparing a new adventure or a player joining the table, this is the place to start. The sections below cover everyday tasks—from creating a game, taking actions, and rolling dice to using chat bots and updating DiceFrame. For code and development details, see the relevant technical guides.

## First Launch

Start the WebUI and open the address printed in the terminal, normally:

```text
http://localhost:18000
```

On the first visit, open Settings and enter:

- API URL for an OpenAI-compatible Chat Completions service.
- Model name, for example `deepseek-v4-pro`.
- API key from that service.

Save the settings and use Test connection to verify that the model can respond.

If you open Create before finishing the model settings, a notice at the top links directly to Settings. You can still fill in the world and characters first; complete the setup before generating content or starting the adventure.

## Start a Game

1. Open Create.
2. Choose the game language.
3. Select a world template, generate one with AI, or enter your own setting.
4. Choose the rules and difficulty.
5. Create a character, or generate a draft and edit it.
6. Enter Play.
7. Submit an action such as “I inspect the runes on the wall.”

DiceFrame sends the action to the GM model, parses narrative state tags, and records changes to HP, inventory, gold, experience, scene, and related state.

## Languages and Content

Interface language changes WebUI text. Game language is stored in the save and controls GM narration, opening scenes, summaries, quick actions, and AI-generated content.

World templates, lorebooks, and content packs have their own `language`. Create prioritizes matching content but still shows other languages. Selecting Chinese content in an English UI does not translate the content, and vice versa.

Rules use `<rule_id>.json` for Chinese and `<rule_id>_en.json` for English. The game loads the matching language and falls back to Chinese when an English file is absent.

## Turns and Actions

Solo games normally progress after an action is submitted.

In multiplayer, each active player may submit one action per round. DiceFrame advances when all active players have submitted, or when the GM forces progression. A player may revise the current action within the configured limit; only the final version is sent to the GM model.

## Multiplayer

The GM creates a game and sends the invite link. Players can:

- Join the current game.
- Create a character.
- Claim an existing character.
- Submit actions from their own player page.

The GM can view player status. Mark a temporarily absent player Away; that player follows the party without initiating major decisions and no longer blocks the round. Restore the player when they return.

## Dice Flow

Some actions resolve directly; others require a check. When a check is needed, DiceFrame pauses before narration and asks for a roll:

1. A player submits an action.
2. DiceFrame requests a check.
3. The player or GM rolls.
4. The result is attached to the pending action.
5. The GM model continues with the known result.

The GM can handle or force progression when a player cannot complete the flow.

## Reading State Changes

After GM narration, the State changes panel shows what was actually committed to the save:

- HP damage or healing.
- Items gained, lost, or consumed.
- Gold gained, paid, or deducted.
- Experience and level changes.
- Scene or situation changes.
- Character-private perceptions.

If prose and recorded state conflict, the recorded state is authoritative.

### Confirming a Purchase

When the story offers something that costs money, DiceFrame asks the paying player to confirm first. No money is deducted and no item is added before confirmation.

In multiplayer, the player who is paying confirms the purchase. The purchase completes only when that character has enough money; rejecting it or having insufficient funds leaves both the balance and inventory unchanged.

## Chat Bot

DiceFrame can connect a Web game to QQ group chat through the built-in QQ/NapCat plugin. The adapter uses HTTP APIs and does not read saves directly.

1. Open plugin settings in the WebUI.
2. Enter the NapCat WebSocket address, port, and token.
3. Enable QQ / NapCat.
4. Copy the Bot binding command from the game page.
5. Send it to the target group.

The built-in plugin receives its DiceFrame Bot API Token automatically. For an external bridge such as MaiBot, copy the DiceFrame URL and token from Settings → Bot API into that bridge. Regenerating the token invalidates the old value.

The Bot follows the current game's language for help, status, recap, map, payment, character-creation, and error messages. The examples below use the English commands; Chinese games retain the corresponding Chinese commands.

## Using the Plugin Store

Open Settings → Plugins → Plugin Store. The store is an index: authors retain their source repositories while DiceFrame pins the latest stable Release to an exact commit during installation.

- Supported means the integration exists now. Partial means only the listed subset works. Reserved types cannot be installed from the store.
- Source pinned means installation resolves the latest stable GitHub Release to an exact commit and checks the plugin ID, version, and permissions again. It is not a code-safety guarantee.
- `official`, `verified`, and `community` describe source/review level, not absolute safety. Install process plugins only from trusted authors.
- A disabled Install button is accompanied by a reason. Bundled plugins update with DiceFrame; entries without a public repository or stable Release cannot be installed.
- After installation, review permissions, enter the plugin's own settings, and enable it. QQ/NapCat still needs no manually entered DiceFrame Bot Token.
- Declarative plugins may update automatically when their runtime type and effective permissions do not expand. Process plugins only notify and require confirmation; permission or runtime expansion also requires confirmation.
- Privately shared plugins should use a `.dfplugin` file produced by the packaging script. Select it under Local Install. After manually copying a plugin directory, use Rescan Local Plugins.
- Chat presentation extensions may add commands or replace status, map, and other replies with custom text, images, or cards. If an extension fails, DiceFrame keeps using its built-in presentation; the extension cannot rewrite authoritative character state, rolls, or payment results.

## Common Chat Commands

The examples below use `@bot` for mentioning the Bot:

```text
@bot help
@bot join CharacterName
@bot create character
@bot AI character
@bot invite
@bot recap
@bot map
@bot status
@bot sense
@bot pay
@bot confirm pay
@bot reject pay
@bot roll
@bot I inspect the runes on the wall
@bot advance
@bot away
@bot back
```

- `join CharacterName`: bind the platform account to a Web character.
- `create character`: get character-creation instructions or an entry link.
- `AI character`: generate a character draft for confirmation.
- `invite`: send the player join link.
- `recap`: show the public recap and recent turns.
- `map`: show the current and known locations.
- `status`: show the claimed character summary.
- `sense`: request character-private information, normally by direct message.
- `pay`: view pending payments; use `confirm pay` or `reject pay` to decide.
- `roll`: confirm a pending check.
- `advance`: let the GM or an authorized account advance.
- `away` / `back`: leave temporarily or resume participation.

## Chat Actions

Players may mention the Bot and send a natural-language action:

```text
@bot I circle behind the guard and look for the key on his belt
```

If a check is required, the Bot asks for `@bot roll`. Otherwise DiceFrame progresses and sends the GM narration and recorded state changes back to the group.

## Private Information

Some clues belong to one character, such as hidden doors, hallucinations, dreams, private thoughts, or unique perceptions. In group chat, send:

```text
@bot sense
```

The Bot attempts a direct message. If that fails, it asks the player to check temporary-session or friend settings.

## Updating DiceFrame

Check for a new version under Settings → Version Update. The apply method depends on the installation:

- Windows portable builds can download and apply an update in the WebUI. A failed candidate returns to the old version.
- Extracted source releases can apply an update and then ask for a manual restart.
- Git development checkouts should use `git pull`.
- Docker uses `docker compose pull && docker compose up -d`. NAS users can also pull and recreate the image from the device's container manager.

The first move from v1.6.0 to a release with the new launcher requires one manual upgrade following that release's notes. See [Application Updates](UPDATER_EN.md) for details.

## Troubleshooting

### The AI does not respond

Check model settings and Test connection. Common causes are an incorrect API key, model name, incompatible base URL, or network failure.

### Update check returns HTTP 403

The anonymous request quota used for version checking is temporarily exhausted. Only update notifications are affected; games, saves, and model calls continue to work. Retry later or open the project's Releases page directly.

### A player cannot act

The character may be dead, not joined to the game, waiting during resolution, or viewed through GM preview. Check player status before refreshing.

### The Bot does not respond

Confirm that QQ/NapCat is enabled, then check the NapCat WebSocket host, port, and token. The group must also be bound to a game.

### Group chat does not progress

A multiplayer round may still be waiting for another active player. The GM can send `@bot 推进` or force progression in the WebUI.

### State looks wrong

Refresh the game detail. If narration conflicts with State changes, the recorded changes are authoritative. Preserve the save instead of deleting `data/` if further investigation is needed.

### Will upgrades delete custom worlds or rules?

No. Bundled templates are synchronized into `data/templates/`, while custom and AI-generated content is kept as user data. Upgrades refresh built-ins without overwriting user content. Copy the complete `data/` directory when moving computers.

If `data/config.json` or `data/secrets.json` is damaged, DiceFrame preserves it as `*.corrupt-timestamp.json` and starts with a safe empty configuration. Do not publish the preserved copy because it may contain API keys or credentials.

### Can I publish saves or chat logs?

This is not recommended. `data/` may contain API keys, access credentials, real group IDs, private messages, and complete campaign records. Never commit `data/`, `.env`, logs, or caches to a public repository.
