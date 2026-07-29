# QQ / NapCat Plugin

[中文](README_CN.md) | English

This package contains the manifest and settings documentation for DiceFrame's built-in QQ/NapCat adapter. Its runtime code is supplied by the main DiceFrame application.

1. In NapCat, open Network Settings → Network Configuration → WebSocket Server, enable it, and note the port and access token.
2. Enter those connection values on the DiceFrame plugin settings page and enable the plugin.
3. Copy the Bot binding command from the GM game page and send it to the target group.
4. Players claim a character with `@Bot join CharacterName`, then submit natural-language actions.

The built-in plugin does not require a manually entered DiceFrame Bot API Token. DiceFrame generates and injects it automatically. Plugin settings only need the NapCat WebSocket host, port, and access token.

When a `bot-extension` plugin is installed and enabled, QQ messages automatically pass through the shared command, reply, and rendering extensions. A failed extension falls back to the built-in commands and cards described here.

The Bot follows the bound game's language for help, status, recap, map, payments, character creation, and errors. English games use the commands below; Chinese games continue to use their Chinese equivalents.

Common commands:

- `@Bot help`: show available commands.
- `@Bot bind <game_key> <one-time-code>`: bind the current Web game to the group; the code expires immediately after success.
- `@Bot invite`: send the Web join link and a one-image new-player guide.
- `@Bot invite @player` / `@Bot invite me`: keep the public group link and also attempt a direct message. If direct messaging fails, the group receives a temporary-session/friendship hint and the public-link fallback.
- `@Bot create character`: send character-creation guidance and the creation entry point in the group.
- `@Bot AI character`: when AI-assisted creation is enabled, collect a description in direct messages, generate a draft for confirmation, then post the public draft to the group.
- `@Bot join CharacterName`: claim an existing Web character.
- `@Bot recap`: show the public recap and recent turns.
- `@Bot map`: show the current scene and known lorebook location links.
- `@Bot status`: show the claimed character's HP, gold, inventory, and related summary.
- `@Bot sense`: send recent character-private perceptions by direct message.
- `@Bot pay`: send pending payment confirmations by direct message.
- `@Bot confirm pay` / `@Bot reject pay`: accept or reject a pending payment.
- `@Bot roll`: confirm an action that is waiting for dice.
- `@Bot advance`: let the GM or an authorized account force the round forward.
- `@Bot away` / `@Bot back`: stop or resume blocking the round. A GM may target a named character.
- `@Bot <natural-language action>`: submit an action. If it starts GM generation, the Bot first reports that the GM is thinking.

Without a public Web address, invite, character-creation, and map commands still return readable group instructions or cards. With a public address they also include clickable links.

AI-assisted character creation is enabled by default but starts only after the explicit `AI character` command. It uses the separate character-generation endpoint, does not enter campaign context or turn logs, and does not create a character automatically. The player confirms a public draft; the GM may edit it later in the Web character page.

Away characters remain with the party but do not count toward pending actions. AI context marks them as following the group without initiating major decisions. `@Bot back` restores normal participation.

Only the bound GM may force progression by default. Add assistant GMs or trusted player account IDs under the plugin's authorized progression accounts, one ID per line. NapCat normally uses QQ numbers.

Image-card cache:

- Help, status, and character-guide images are temporarily stored under `data/bot/cards`.
- Settings control retention time and maximum count; creating a card removes old `card_*.png` files.
- The settings page can clear those temporary PNG files immediately without deleting saves, character cards, or unrelated images.
