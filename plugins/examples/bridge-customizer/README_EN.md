# Bot Bridge Extension Example

This example demonstrates three `bot-extension` stages:

- `before_message` adds the `plugin test` command;
- `after_result` appends an optional footer to text replies;
- `render` replaces QQ structured cards with a plugin-generated PNG.

Copy this directory to `plugins/bridge-customizer/` during development, then enable it in Settings. By default it does not modify replies or replace images.

Dynamic files must be written under `DICEFRAME_PLUGIN_DATA_DIR`. A plugin may only return PNG, JPEG, WebP, or GIF files from that directory. DiceFrame validates paths, formats, sizes, and runtime state. If an extension fails or returns `handled: false`, Bot Bridge uses the built-in presentation.
