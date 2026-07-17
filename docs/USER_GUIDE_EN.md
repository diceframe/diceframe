# DiceFrame User Guide

This guide is for players and GMs. It explains how to start a game, play turns, handle dice, read state changes, and run multiplayer games in the browser.

## First Launch

Start the Web UI, then open the address printed in the terminal:

```text
http://localhost:18000
```

On first use, open Settings and configure your model:

- API base URL: an OpenAI-compatible Chat Completions endpoint.
- Model: the model name provided by your provider.
- API Key: your provider key.

Use the connection test in Settings before starting a real game.

## Start A Game

1. Open Create.
2. Set Game Language to English.
3. Choose a world template, ask AI to generate a world, or write your own setting.
4. Choose a rule and difficulty.
5. Create characters, import cards, or draft characters with AI.
6. Enter Play.
7. Type a character action, such as “I examine the runes on the wall.”

DiceFrame sends player actions to the GM model. The model returns narration plus structured state tags. DiceFrame parses those tags and updates HP, items, gold, XP, scene state, private messages, and other records.

## Languages And Content

The app language in Settings controls Web UI text. Game Language on Create is saved into the game and controls GM narration, opening scenes, summaries, quick actions, and AI-generated content.

World templates, lorebooks, and content packs have their own content language. Create prioritizes matching templates and shows the content language for other templates or lorebooks. If you choose a Chinese lorebook while using the English UI, its Chinese content remains Chinese; choosing English content in the Chinese UI works the same way.

Rules are split by language: `<rule_id>.json` (Chinese) + `<rule_id>_en.json` (English). The game loads the version matching its language, falling back to Chinese if the English file is missing.

## Turns And Actions

In solo mode, an action usually advances the game immediately.

In multiplayer mode, each active player can submit an action for the current round. When all active players have acted, the game advances. The GM can also force-advance if someone is away or the scene only needs some players to act.

Players may revise their action for the current round up to the table limit. The GM model reads only the latest submitted version.

## Dice Flow

Some actions resolve directly. Some need a check.

When DiceFrame decides an action needs dice, it stops and asks for a roll. After the roll is recorded, the GM model continues narration using that result. This keeps the dice moment visible instead of letting the model silently decide the outcome.

Typical flow:

1. Player submits an action.
2. DiceFrame says the action needs a dice check.
3. Player or GM rolls.
4. The roll is attached to the action.
5. GM narration continues with the roll result.

## State Changes

After GM narration, DiceFrame shows State Changes. These are the actual changes written to the save.

Common state changes include:

- HP damage or healing.
- Items gained, lost, or consumed.
- Gold gained or spent.
- XP and level changes.
- Scene or location changes.
- Private perception messages.

If narration and state changes disagree, trust State Changes. Narration is descriptive; state changes are what the system saved.

## Multiplayer

The GM creates a game and shares invite links with players.

Players can:

- Join the current game.
- Create a character.
- Claim an existing character.
- Submit actions from their own player page.

The GM can see player status in the Play view. Mark a player as Away when they are temporarily absent. Away players do not block the round and are assumed to follow the party without making major decisions.

## Chat Bot Note

DiceFrame can also connect a Web game to group chat through the QQ / NapCat plugin.

## Troubleshooting

### The AI does not respond

Check Settings first. Most failures are caused by an incorrect API key, model name, base URL, provider compatibility issue, or network problem.

### A player cannot act

They may be dead, not joined to the game, waiting during resolution, or viewing as GM preview. Check the player status before refreshing.

### The game seems stuck

In multiplayer, the game may still be waiting for active players. The GM can force-advance from the Web UI.

### State looks wrong

Refresh the game detail. If it still looks wrong, keep the save and avoid deleting `data/`; the save is useful for debugging.

### Can I publish saves or chat logs?

Usually no. `data/` may contain API keys, access tokens, private messages, real group IDs, and full campaign logs. Do not upload it to GitHub.
