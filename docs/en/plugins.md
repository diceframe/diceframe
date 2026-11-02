# Plugins and chat adapters

English | [简体中文](../zh/plugins.md) · [Documentation](../README.md)

The built-in QQ/NapCat plugin receives its DiceFrame Bot API Token automatically; users configure only the NapCat connection under **Manage → Plugins**. External bridges such as MaiBot copy the service URL and token from **Manage → Settings → Bot API**.

The Bot follows the bound game's language for help and primary operation messages, with native Chinese and English commands available.

Players can ask the GM an out-of-character question with `@Bot ask kp <question>` or `@Bot ask: <question>`. Answers use only the public story, current rules, and information known to the claimed character; they do not submit an action, advance the story, trigger a check, or consume the turn's action. Plain `ask the guard ...` remains an in-character action. Chinese games use `@Bot 询问 <问题>`.

## Plugin store and privacy

The DiceFrame plugin store lets you browse and install community plugins published by their authors through GitHub Releases. Installing or updating always requires confirmation, with an additional risk warning when a plugin requests external-process access or expanded permissions. Locally or privately shared plugins can also be installed from `.dfplugin` files.

DiceFrame Hub provides review information, version status, and details for the plugin store under **Manage → Plugins**. Installed plugins and local games continue to work if Hub is temporarily unavailable. Anonymous usage statistics are disabled by default and can be managed under **Manage → Settings → Security → DiceFrame Hub and privacy**.

To develop or publish a plugin, see the [plugin development guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-development.md) and [plugin registry and review rules](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-registry.md).
