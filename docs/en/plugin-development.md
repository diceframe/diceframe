# DiceFrame Plugin Development Guide

[中文](../zh/plugin-development.md) | English

This guide defines DiceFrame plugin packages, manifests, settings, permissions, and extension boundaries. The capabilities available today are channel adapters, Bot Bridge command/hook/render extensions, content packs, optional voice presets, filtered theme variables, structured tools, and read-only map definitions, locations, icons, and backgrounds. Import/export and generic Provider plugins remain reserved; core TTS already includes browser, OpenAI-compatible, and GPT-SoVITS adapters.

**Only capabilities marked Supported or Partial below have an active integration. Reserved types may be recognized in a development directory, but they do not participate in their intended workflows and cannot be installed from the store. Plugin documentation must describe actual behavior without implying unavailable features.**

## 1. Plugin-System Goals

DiceFrame's plugin model covers channel adapters, content packs, themes, maps, import/export formats, model or media Providers, and focused utilities. The last three categories remain future extension points rather than current runtime capabilities.

## 2. Current Support

| Type | `plugin_type` | Current status |
|------|---------------|----------------|
| Channel adapter | `channel-adapter` | Supported: managed process, settings, start/stop, and DiceFrame HTTP API access |
| Bot Bridge extension | `bot-extension` | Supported: command interception, message/result hooks, text/image/card rendering, and failure fallback |
| Content pack | `content-pack` | Supported: rules, worlds, content catalogs, read-only map contributions, and user-triggered imports |
| Theme | `theme` | Supported: v2 semantic theme tokens |
| Voice preset | `voice-pack` | Optional OpenAI-compatible voice IDs and GPT-SoVITS reference WAV/transcript bundles |
| Import/export | `import-export` | Reserved: no unified task API; store installation disabled |
| Provider | `provider` | Reserved: no Provider runtime; store installation disabled |
| Tool | `tool` | Supported: process handshake, registration, structured invocation, timeout, and manual testing UI |

`content-pack`, `theme`, and `voice-pack` are declarative and may omit a background process. `channel-adapter`, `bot-extension`, and `tool` require an `entrypoint`.

## 3. Plugin Boundaries

- Each user-installed plugin occupies `data/plugin-packages/<plugin-id>/` and each built-in or example plugin occupies `plugins/<plugin-id>/`; each package contains exactly one `plugin.json`.
- Plugins do not read or modify DiceFrame's general `data/` storage directly and must not import `src.webui`.
- Missing capabilities should use a formal HTTP API or a type-specific registration mechanism.
- Normal settings live in `data/plugins/<id>/config.json`; secrets live in `secrets.json` and are masked by public APIs.
- Runtime files belong in the plugin-specific data directory supplied by the host, not the source directory.
- Uninstalling preserves `data/plugins/<id>/` by default.

## 4. Package Layout

```text
<plugin-id>/
  plugin.json
  config.schema.json
  README.md or README_CN.md
```

For local or private sharing, package the directory as a `.dfplugin` file. A `.dfplugin` file is a ZIP-compatible archive with a DiceFrame-specific extension; do not rename an arbitrary ZIP and assume it is valid. It may place the files at its root or inside one top-level plugin directory. The installer rejects absolute paths, `..` traversal, symbolic links, encrypted entries, duplicate paths, and multiple manifests. Limits are 20 MB compressed, 100 MB unpacked, 25 MB per file, and 2,048 entries. Replacing an installed ID requires explicit overwrite confirmation.

## 4.1 Starting from an Example

| Example | Path | Type | Demonstrates |
|---------|------|------|--------------|
| Starter Content | `plugins/examples/starter-content` | `content-pack` | Rules, worlds, characters, NPCs, items, spells, and classes |
| Paper Theme | `plugins/examples/paper-theme` | `theme` | Safe v2 semantic-token themes |
| Echo Tool | `plugins/examples/echo-tool` | `tool` | Process handshake, registration, JSON arguments, and structured results |
| Bridge Customizer | `plugins/examples/bridge-customizer` | `bot-extension` | Custom commands, result hooks, and QQ image rendering |

Recommended workflow:

1. Copy an example to a new directory.
2. Update manifest identity, version, description, capabilities, and permissions.
3. Keep only relevant settings in `config.schema.json`.
4. Add resources or process code inside the plugin directory.
5. Build and locally install the package:

```powershell
python scripts\package_plugin.py plugins\my-plugin --overwrite
```

The output is placed in `dist/plugins/`. The packager applies host validation and rejects caches, logs, databases, symbolic links, and unsafe paths.

## 5. plugin.json

`plugin.json` is UTF-8 JSON:

```json
{
  "schema_version": 1,
  "id": "qq-napcat",
  "name": "QQ / NapCat",
  "version": "1.0.0",
  "description": "Connects NapCat WebSocket group chat to DiceFrame.",
  "plugin_type": "channel-adapter",
  "entrypoint": ["{python}", "-m", "src.bots.qq.main"],
  "config_schema": "config.schema.json",
  "capabilities": ["channel.group", "channel.private", "game.action"],
  "permissions": ["process.spawn", "network.client", "diceframe.http", "plugin.config", "plugin.secrets", "plugin.data"],
  "docs": "README_CN.md"
}
```

- `schema_version` is currently `1`.
- `id` matches `^[a-z0-9]+(?:-[a-z0-9]+)*$` and matches the installed directory.
- `plugin_type` is required; missing or unknown values fail validation.
- `entrypoint` is an argument array. `"{python}"` resolves to the active interpreter; `"{plugin_dir}"` and `"{data_dir}"` resolve to the plugin source and private runtime-data directories. Declarative plugins may omit it.
- `config_schema` defaults to `config.schema.json` and stays inside the plugin.
- `contributes` declares resource paths or globs that register while enabled.
- `capabilities` describes factual business capabilities.
- `permissions` requests known host capabilities and is shown in settings.
- `docs` points to documentation inside the package (e.g. `README_CN.md`). It is rendered in the plugin settings "Guide" tab using lightweight Markdown (headings, lists, bold, inline code). Write it in the language of your users and explain what the plugin does, how to enable it, and how to use it. The tab is hidden when this field is absent.

Known types are `channel-adapter`, `bot-extension`, `content-pack`, `theme`, `voice-pack`, `import-export`, `provider`, and `tool`.

| Permission | Meaning |
|------------|---------|
| `process.spawn` | Start an independent process |
| `network.client` | Access external networks |
| `diceframe.http` | Call the DiceFrame HTTP API |
| `plugin.config` | Read normal settings |
| `plugin.secrets` | Read sensitive settings |
| `plugin.data` | Read/write the plugin data directory |
| `content.read` | Register and read content resources |
| `content.import` | Copy selected content into user storage |
| `theme.tokens` | Register theme variables |
| `voice.assets` | Register voice descriptors, previews, and reference audio |
| `map.assets` | Register map locations and static assets |
| `tool.execute` | Register and execute structured tool calls |
| `bot.extend` | Extend Bot Bridge commands, processing, and presentation |

## 6.1 Security Boundaries

The host validates archive paths and budgets, identity, type, schema, entrypoint, contributions, and permissions. It separates secrets, confines declarative assets to declared paths, copies user imports out of plugin storage, and filters theme values.

Process plugins inherit only a small operating-system variable allowlist. A plugin declaring `diceframe.http` receives a DiceFrame URL and a host-generated token that belongs only to that plugin. Authors and users do not configure this token. The global Bot API token in Settings is reserved for external programs that are not managed as DiceFrame plugins. There is no complete OS sandbox: an entrypoint still executes as the same OS user as DiceFrame.

## 6.2 Process Plugin Lifecycle Requirements

The host creates, restarts, and manages the **plugin process** (exponential backoff restart, reset after stability). Each plugin must handle two things itself to avoid becoming a zombie or leaking child processes:

1. **Watch the parent process and the host generation**: the host injects `TRPG_PARENT_PID` (the main process PID) and atomically writes this host process's generation token to `DICEFRAME_PLUGIN_DATA_DIR/.host-generation` before spawning a plugin each time. The plugin should check both, and exit (self-terminate) immediately when either triggers:
   - **Parent PID disappears**: the host process has exited; otherwise the plugin is left as an orphan.
   - **Host generation changes or is missing**: the DiceFrame main process restarts via `os.execv` (PID and starttime stay the same), so a pure PID check wrongly thinks the parent is alive, leaving the old plugin process as an orphan that keeps holding single-instance locks and other resources. A changed or missing generation file means the host has restarted; the plugin should exit immediately.

   See the independently-distributed `cloudflare-tunnel` plugin (repo [`diceframe/cloudflare-tunnel`](https://github.com/diceframe/cloudflare-tunnel), generation detection built in since v0.2.0) for a self-contained `parent_watch.py` reference implementation (cross-platform `pid_exists` + `read_generation_file` + `start_parent_watch`); process plugins can copy it and pass the generation file path and current value:

   ```python
   # self-contained in the plugin, not an SDK public export
   from pathlib import Path
   from parent_watch import read_generation_file, start_parent_watch

   generation_file = Path(os.environ["DICEFRAME_PLUGIN_DATA_DIR"]) / ".host-generation"
   start_parent_watch(
       on_exit=lambda: cleanup_your_subprocess(),
       generation_file=generation_file,
       initial_generation=read_generation_file(generation_file),
   )
   ```

   `on_exit` is the cleanup callback when the host exits or restarts (for example, killing subprocesses the plugin spawned). Plugins with a single-instance lock (such as channel adapters) should also release the lock in `on_exit`, and can follow the `qq-napcat` plugin v1.4.0 lock takeover (orphan detection + SIGTERM→SIGKILL then rebuild the lock) so new instances are not rejected by a stale lock.

   Older hosts (< 2.0.2) do not write the generation file: `read_generation_file` returns an empty string, the generation check is skipped, and the plugin degrades to pure PID detection, working on both new and old hosts.

2. **Clean up child processes on exit**: any subprocess the plugin spawns (such as an external binary) must be terminated when the plugin exits, to avoid leftovers. Kill them after the plugin main loop finishes and provide a fallback via `start_parent_watch(on_exit=...)`.

The host's plugin-process restart (`_monitor_process`) and the plugin's own subprocess recovery are separate layers: a crashed plugin process is restarted by the host; a subprocess the plugin spawned and crashed is the plugin's own responsibility (a retry with exponential backoff capped at 60s is suggested).

Boundary of the convention: reconnect strategies and business-specific backoff are plugin-specific and not standardized. Only "watch the parent process + the host generation + clean up on exit" is the baseline every process plugin should follow.

## 6.3 config.schema.json

The restricted schema uses an object root with `properties`. Supported field types are `boolean`, `string`, `number`, `integer`, and `array`; controls are `switch`, `text`, `secret`, `number`, `select`, and `string-list`.

- Mark secrets with `ui.sensitive: true` or `ui.control: "secret"`.
- `ui.env` injects only that declared field into the filtered process environment.
- `ui.generate: true` is limited to sensitive fields and creates a token when enabled without a value.

```json
{
  "type": "object",
  "required": ["enabled"],
  "properties": {
    "enabled": {"type": "boolean", "default": false, "ui": {"control": "switch"}},
    "base_url": {"type": "string", "ui": {"control": "text", "env": "DICEFRAME_BASE_URL"}},
    "token": {"type": "string", "ui": {"control": "secret", "sensitive": true, "generate": true, "env": "PLUGIN_TOKEN"}}
  }
}
```

## 7. Plugin Types

> The authoritative list of plugin types (support level, runtime mode, inferred permissions, required permission, content contribution mapping) is driven by a single source: the `PLUGIN_TYPE_SUPPORT` descriptor table in `src/plugin_host/support.py`. Adding a type only requires editing that table — the host, policy, registry, and frontend follow automatically. This section is a per-type author guide; some reserved types are not yet wired to a runtime.

### 7.1 Channel Adapters

Channel adapters connect QQ/NapCat, MaiBot, Discord, Telegram, or another chat stream and require an `entrypoint`. They call DiceFrame with `X-Bot-Token`. Managed plugins receive their own generated token through `TRPG_BOT_TOKEN`; an external bridge uses the global value copied from Settings → Bot API.

Recommended modules:

```text
src/bots/<platform>/
  config.py
  transport.py
  api_client.py
  store.py
  adapter.py
  command_matchers.py
  message_utils.py
  presenters.py
  delivery.py
  main.py
```

Adapters use HTTP rather than importing WebUI code, store platform mappings in plugin data, deduplicate persistent message IDs, handle reconnect/rate-limit/formatting behavior, and leave dice, state changes, and narrative progression to DiceFrame.

### 7.1.1 Bot Bridge Extensions

`bot-extension` extends chat processing rather than connecting a new platform. It can add commands, change messages entering the Bridge, transform business results, and replace text, image, or structured-card presentation. QQ/NapCat calls the extension protocol directly. External bridges such as MaiBot detect `bridge_extensions.protocol_version` through `/api/bot/ping`, then use `POST /api/bot/bridge/extensions`; they retain their existing behavior with older DiceFrame versions.

Use `src.plugin_sdk.BridgeExtensionRuntime` and start from `plugins/examples/bridge-customizer`. An extension declares one or more stages:

- `before_message` runs before built-in command handling; it may modify `payload.text` or return `handled: true` with `outputs`.
- `after_result` transforms text or structured fields after built-in business handling.
- `render` selects final presentation; the first extension returning `handled: true` replaces the built-in renderer.

Extensions run by descending `priority`. Invocation errors are logged and skipped; protocol errors or timeouts stop that plugin and the Bridge falls back to built-in behavior.

Outputs use one of these forms:

```json
{"type": "text", "text": "Custom reply"}
{"type": "card", "title": "Status", "subtitle": "", "lines": ["HP 8/10"], "fallback_text": "HP 8/10"}
{"type": "image", "path": "status.png", "caption": "", "alt": "Status", "fallback_text": "HP 8/10"}
```

Images must be written under `DICEFRAME_PLUGIN_DATA_DIR`. PNG, JPEG, WebP, and GIF are accepted up to 10 MB. DiceFrame validates the path and exposes an authenticated asset route; NapCat downloads it into its card cache and MaiBot converts it to base64 for its image-send capability. Delivery failures use `fallback_text`.

The manifest must include at least:

```json
{
  "plugin_type": "bot-extension",
  "entrypoint": ["{python}", "{plugin_dir}/main.py"],
  "permissions": ["process.spawn", "plugin.config", "plugin.data", "bot.extend"]
}
```

Standard output is reserved for JSON-RPC. The 256 KB request/response limit still applies, so image bytes must not be returned as base64. Extensions can change chat commands and presentation, but dice, balances, payments, and saved game state remain authoritative in DiceFrame's game core.

### 7.2 Content Packs

Supported contributions are `rules`, `world_templates`, `character_templates`, `npcs`, `items`, `spells`, `classes`, `portraits`, and `scene_images`. Enabled rules and worlds enter normal selectors. Other resources appear in the read-only plugin catalog and may be copied by the user into the card library or a selected lorebook. Imported copies remain after disabling or uninstalling the plugin.

```json
{
  "schema_version": 1,
  "id": "starter-content",
  "name": "Starter Content",
  "version": "0.1.0",
  "plugin_type": "content-pack",
  "config_schema": "config.schema.json",
  "contributes": {
    "rules": ["content/rules/*.json"],
    "world_templates": ["content/worlds/*.json"],
    "character_templates": ["content/characters/*.json"],
    "npcs": ["content/npc/*.json"],
    "items": ["content/items/*.json"],
    "spells": ["content/spells/*.json"],
    "classes": ["content/classes/*.json"],
    "portraits": ["assets/portraits/*"],
    "scene_images": ["assets/scenes/*"]
  }
}
```

Character templates and NPC records may declare either a built-in portrait or a packaged image asset:

```json
{ "portrait": { "kind": "builtin", "id": "freeform_fantasy:0" } }
{ "portrait": { "kind": "asset", "path": "assets/portraits/mira.webp" } }
```

Every `kind: "asset"` path must stay inside the plugin and match `contributes.portraits`. PNG, JPEG, and WebP are supported, up to 3 MB per portrait. DiceFrame previews the declared asset directly while the pack is enabled; importing the character or NPC copies and normalizes the image into the local portrait library, so the imported copy no longer depends on the plugin. The built-in content-pack exporter performs this packaging automatically when “Package character and NPC portraits” is selected.

Ruleset and world templates may both declare an adventure cover. A world cover represents that specific setting; the ruleset cover is the fallback for worlds without their own image:

```json
{ "scene_image": { "kind": "builtin", "id": "freeform_fantasy" } }
{ "scene_image": { "kind": "asset", "path": "assets/scenes/valley.webp" } }
```

An `asset` reference must match `contributes.scene_images`. PNG, JPEG, and WebP are supported, with an 8 MB source limit and a minimum size of 320×180. When DiceFrame persists an upload or materializes a packaged image, it center-crops to 16:9 and normalizes it to a 1600×900 WebP. Pack authors should only write `builtin` or `asset`; `plugin` and `upload` are host-generated runtime references, not distributable source syntax.

The lifecycle and precedence are standardized:

1. Creation resolves `world.scene_image > ruleset.scene_image > matching built-in ruleset image`. The user can accept that default or upload an adventure-specific image.
2. The selection is persisted in the game save. Pack assets are copied into the local adventure-image store, so disabling, updating, or uninstalling the pack does not break existing saves.
3. The creation confirmation, overview save card, and play page all render the same saved reference.
4. A GM may upload a replacement or restore the current content default at any time. Restore recalculates world first, then ruleset.
5. Save export embeds non-built-in covers in the save zip; importing on another DiceFrame instance materializes the image locally.
6. The WebUI content-pack exporter accepts separate world and ruleset covers, writes the template fields, creates `assets/scenes/`, and completes the manifest.

Put an image on a world when it has distinct art; put it only on the ruleset when several worlds share a visual. Existing packs without `scene_image` require no migration and use the built-in fallback.

Use stable IDs and avoid built-in IDs. Content catalogs are never imported automatically; selected world and ruleset templates do initialize a new save according to the lifecycle above. Worlds and catalog records declare `language`; world text is not automatically translated. Rules use `<rule_id>.json` for Chinese and `<rule_id>_en.json` for English, with Chinese fallback. Protocol fields and GM tags remain language-neutral.

#### 7.2.1 AI check metadata and the offline intent fallback

The normal path no longer triggers a check from keywords in a player's message. After all active players act or the GM manually advances, a phase-one GM calls `dice_checks` to adjudicate the complete batch. The server validates the player, character-sheet attribute/skill, and target before producing one immutable roll. Rule packs must expose stable `attributes[].key`, `dice_system`, and `mechanics` values. An optional `check_mechanic` may declare the dice, comparison, and critical rules; otherwise DiceFrame derives them from `dice_system`.

`intents` remains only as an offline compatibility path when model calls are completely unavailable and as metadata for old clients/saves. It is no longer the authority for the normal online adjudication path.

**Offline fallback precedence** (rule vocabulary first, global fallback last):

1. Rule template defines its own `intents`: fully self-contained.
2. Rule template `extends: "intents_base"` (or a vocabulary base inside the plugin): inherits the main program/base vocabulary; a child rule can override individual intents.
3. Neither: falls back to the global generic vocabulary (`templates/rules/fallback_intents.json`) with limited triggers.

**`intents` field structure**:

```json
{
  "intents": {
    "defaults": {
      "applies_to_dice_systems": ["d20", "d100"],
      "zh_match": "substring",
      "en_match": "word",
      "case_sensitive": false,
      "prefer_longest": true
    },
    "stealth": {
      "aliases": {
        "zh-CN": ["潜行", "潜入", "隐匿", "悄悄"],
        "en": ["sneak", "stealth", "hide", "creep"]
      },
      "skill_candidates": {
        "zh-CN": ["潜行", "隐匿"],
        "en": ["stealth", "dexterity"]
      },
      "default_attribute": "dex",
      "priority": 10
    }
  }
}
```

- `aliases`: offline fallback words, keyed by language (`zh-CN` / `en` / future languages).
- `skill_candidates`: candidate skills matched against the character sheet when the intent hits.
- `default_attribute`: the default attribute for the check (`str` / `dex` / `int` / `wis` / `cha`, etc.).
- `priority`: when several intents match, lower value wins (first match by sort order).
- `applies_to_dice_systems`: which dice systems this intent applies to (`d20` / `d100`). Intents that do not match the current game's dice system are skipped, preventing COC from triggering intents meant only for DND and vice versa.

**`dice_system` controls the dice itself**: AI only proposes check parameters; the dice algorithm (d20 vs d100, success thresholds, criticals) is set by the rule template's top-level `dice_system`, `mechanics`, and optional `check_mechanic`.

**Inheriting the main vocabulary**: a plugin rule's `extends` can reference vocabulary under the main program's `templates/rules/` (for example `intents_base`) by writing `"extends": "intents_base"`. Inheritance is opt-in — rules that do not need the vocabulary can omit it and rely on the global fallback.

**Multi-language extension**: vocabularies are data-driven. Adding a language only requires adding keys (such as `ja`) to `aliases` / `skill_candidates` and registering the language suffix in `engine/language.py`. New languages do not pollute other languages.

### 7.3 Themes

Themes register JSON through `contributes.theme` or `contributes.themes`. Only theme contract v2 is supported; themes without `"schema_version": 2` are ignored and legacy variables are not mapped.

```json
{
  "schema_version": 2,
  "id": "paper-soft",
  "name": "Paper Soft",
  "tokens": {
    "base": {
      "--df-accent": "#c79a45",
      "--df-interactive": "#347d78"
    },
    "dark": {
      "--df-canvas": "#17130f",
      "--df-surface-1": "#231f19",
      "--df-text": "#f0e6d2"
    },
    "light": {
      "--df-canvas": "#eadbb9",
      "--df-surface-1": "#fff7df",
      "--df-text": "#312719"
    }
  }
}
```

- `tokens` accepts only `base`, `dark`, and `light`; the active mode overrides `base`.
- The host accepts a documented allowlist of semantic `--df-*` tokens covering canvas/surfaces, borders, accents and interaction, semantic states, text, fonts, shadows, and radii.
- Legacy variables such as `--page`, `--panel`, and `--gold` are unsupported.
- Values are validated by token type. Unknown tokens, invalid colors, dangerous declarations, and `url()` are ignored.
- Themes cannot inject components, layouts, scripts, or arbitrary CSS.
- Theme background images are not supported. A future implementation must use host-controlled plugin asset references and will not expose arbitrary CSS `url()` injection.
- Theme authors should verify text contrast, focus states, and disabled states in both light and dark modes.

### 7.4 Map Contributions in Content Packs

Content packs may bundle maps with worlds and other content, or declare only map fields to act as standalone map content. The supported contribution keys are `map_definitions`, `map_locations`, `map_icons`, and `map_backgrounds`. Enabled locations enter `/api/games/{game_key}/map`; validated image assets receive restricted URLs. Locations and map definitions may use `world_id` or `worlds` filters.

A `map_definitions` JSON file uses `schema_version: 1` and `mode: "graph"`. It may declare a same-package `map_backgrounds` asset ID, a `default_view`, and `nodes` with `location_ref`, optional `x/y` coordinates in `-50..50`, and same-package map icon or background IDs. A world template can select one explicitly with `default_map: "plugin:<plugin-id>:map:<map-id>"`; otherwise the host automatically matches the current world and falls back to the Lorebook location graph.

Map resource IDs are namespaced by content pack. The map is read-only; only the GM's selected background source is persisted.

### 7.5 Voice Presets

`voice-pack` is a declarative, process-free plugin shown as a “Voice Preset” in the store. It is never required for local TTS: users can enter an upstream OpenAI-compatible `voice_id` directly or save a personal GPT-SoVITS reference WAV/transcript under Settings → My voices. A preset only adds optional one-click metadata and small preview/reference recordings; it does not duplicate GPT-SoVITS, Kokoro, or other base models and never installs Python/CUDA environments.

```json
{
  "schema_version": 1,
  "id": "example-narrator-voice",
  "name": "Example Narrator Voice",
  "version": "1.0.0",
  "plugin_type": "voice-pack",
  "config_schema": "config.schema.json",
  "contributes": {
    "voices": ["voices/*.json"],
    "voice_assets": ["voices/*.wav", "voices/*.mp3"]
  }
}
```

`config.schema.json` must at least expose the usual `enabled` boolean for a declarative plugin. A voice preset has no `entrypoint`.

A GPT-SoVITS voice descriptor uses contract version 1 and declares `id`, `name`, `engine: "gpt-sovits"`, `language`, a WAV `reference_audio`, its exact `prompt_text`/`prompt_language`, `license`, and `consent: true`. An optional `preview_audio` may use another declared audio asset. An OpenAI-compatible preset instead declares `engine: "openai-compatible"` and the upstream `voice_id`.

Every audio path must also match `contributes.voice_assets`. The required license and consent declaration confirms that the author may distribute the descriptor, preview, and reference recording. Base models and trained weights do not belong in a voice preset; the normal 20 MB plugin package limit remains in force. Personal GPT-SoVITS profiles accept either a WAV uploaded to DiceFrame for same-host/shared-filesystem setups or a path visible to the TTS server for containers and remote hosts. An OpenAI-compatible service such as a multi-engine frontend can instead keep native models and voices entirely upstream.

For the complete workflow from open-source voice selection and licensing records through local packaging and store submission, see [Publishing voice presets](voice-pack-publishing.md). `plugins/examples/kokoro-zh-voice-presets` is a lightweight example that ships no weights or audio.

## 7.6 Capabilities Not Yet Implemented

- Content imports do not automatically enter a running game.
- Themes cannot inject components, layouts, scripts, or arbitrary CSS.
- Maps have no editor, layers, or real-time collaboration.
- `import-export` has no unified task API.
- `provider` has no registration or selection runtime.
- `tool` supports short calls but not long-task progress, cancellation, result-file downloads, or automatic AI tool selection.

### 7.7 Import/Export Plugins

Reserved for character-card, world-book, and lorebook transformations. A future runtime must declare formats and versions, validate without overwriting user data, and avoid exposing private paths or content.

### 7.8 Provider Plugins

Reserved for LLM, embeddings, TTS, and image generation. Future Providers must keep API keys in secrets, use timeouts, handle network errors, and avoid logging prompts, responses, or tokens.

### 7.9 Tool Plugins

Tool plugins implement short validation, lookup, conversion, and generation operations over a host-managed JSON-RPC stdio protocol. They complete a version handshake and register at least one tool. Authors should use `src.plugin_sdk.ToolRuntime`; copy `plugins/examples/echo-tool` as a starting point.

```python
from src.plugin_sdk import ToolRuntime

runtime = ToolRuntime()

@runtime.tool(
    name="echo",
    title="Echo",
    description="Return text.",
    input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
)
def echo(arguments, context):
    return {"content": [{"type": "text", "text": str(arguments.get("text") or "")}]}

runtime.run()
```

The host validates names, input schemas, protocol versions, and object results. Calls time out after 30 seconds and each request or response is limited to 256 KB. Standard output is reserved for protocol messages; diagnostics belong on standard error. Running tools can be inspected and manually invoked under **Settings → Plugins → Tools**. HTTP consumers use `GET /api/plugins/tools` and confirmed `POST /api/plugins/tools/{plugin_id}/{tool_name}`.

Tools must document inputs, outputs, and side effects. File writes stay inside the provided data directory or a user-selected location. The current runtime is for work that finishes within 30 seconds; long tasks must wait for a future task/progress protocol.

## 8. Store Listing

The community index is [diceframe/diceframe-plugins](https://github.com/diceframe/diceframe-plugins). It stores metadata only; authors retain their source repositories and publish normal GitHub Releases. No author-provided ZIP or SHA-256 is required.

```json
{
  "id": "example-plugin",
  "repository_url": "https://github.com/username/example-plugin",
  "default_branch": "main",
  "plugin_path": ".",
  "distribution": "github-release-source",
  "update_policy": "automatic",
  "trust_level": "community",
  "tags": ["content-pack"],
  "manifest": {
    "schema_version": 1,
    "id": "example-plugin",
    "name": "Example Plugin",
    "version": "0.1.0",
    "description": "A factual one-line description.",
    "plugin_type": "content-pack",
    "capabilities": [],
    "docs": "README.md"
  }
}
```

When installing, DiceFrame resolves the repository's latest stable GitHub Release to an exact commit, downloads GitHub's source archive for that commit, and validates the manifest again. Declarative plugins are checked and prompted when the user opens the plugin store; installing or updating requires user confirmation, as long as their permissions and runtime type do not expand. Process plugins notify the user and require confirmation. Any permission or runtime expansion is approval-required. `official`, `verified`, and `community` describe source/review status, not a security guarantee. See [plugin-registry.md](plugin-registry.md).

### 8.1 Content Guidelines

Content packs are distributed to a public community. Authors must keep content lawful, healthy, and respectful of others' rights. The lines below apply to every plugin text: lorebook, characters, rules, NPCs, items, and so on.

**Hard lines (violation rejects or delists)**

- No political content.
- No pornography, sexualization of minors, or gambling promotion; no excessive gore, terrorism, or incitement to crime.
- Do not infringe copyright, trademarks, likeness, or privacy rights. Third-party text, rules, portraits, covers, fonts, audio, and other assets require authorization, an applicable open license, public-domain status, or another verifiable lawful basis, plus the attribution and license notices that basis requires. Attribution or an “unofficial” label alone is not permission.
- No defamation or insult of real people; no leaking others' private information.

**Community standards**

- No hate or discrimination (race, gender, religion, region, etc.) and no extremism.
- Descriptions must match real capabilities; do not present "reserved" or "partial" support as complete.
- No unrelated promotion, advertising, or off-topic traffic diversion.

**Derivative works**

- The community index accepts fan-made lorebooks, characters, NPCs, rule adaptations, and artwork created by the submitter. README and description must identify the source work and rights holder, clearly label the package as an unofficial fan work, and use the `community` source level.
- An unauthorized community fan package must not bundle portraits, maps, rulebook text or scans, music, video, fonts, or other assets extracted from the source work. Text and images actually distributed in the package must be created by the submitter or covered by a separately verifiable permission or license.
- A fan package with rights-holder permission, an applicable open license, or another clear lawful basis may apply for a higher review marker after verification. Lack of such proof does not block submission as an unofficial community fan work, but DiceFrame does not endorse its rights status.
- “Copyright belongs to the original creator” and “unofficial fan work” identify provenance but do not grant reproduction, adaptation, or online-distribution rights or excuse copying source assets.
- When a rights notice includes initial evidence and identifies an exact version, DiceFrame may temporarily hide the item, forward the notice to its author, and restore or delist it after reviewing evidence and any counter-notice.

DiceFrame does not pre-screen every community item; maintainers delist violating plugins when found or upon a verified report.

## 9. Install, Update, and Uninstall Semantics

- Store install: resolve the latest stable GitHub Release to an exact commit, extract to a temporary directory, validate, then move to `data/plugin-packages/<id>/`. Local development can place source directly at `plugins/<id>/` in the repository.
- Local install: select a `.dfplugin` file created by `scripts/package_plugin.py`.
- Overwrite: explicit only; stop the old process before replacement.
- Update: resolve and validate the latest stable Release again. The store checks for updates when opened and prompts the user; installing or updating always requires user confirmation. Process or permission-expanding updates require explicit confirmation.
- Uninstall: stop and remove `data/plugin-packages/<id>/` (user-installed source) while preserving `data/plugins/<id>/` runtime data by default.
- Reinstalling the same ID reuses preserved data.

## 10. Release Checklist

- Document purpose, installation, settings, capabilities, dependencies, examples, data handling, and limitations.
- Package one plugin as `.dfplugin` for local/private sharing, without caches, logs, databases, private accounts, secrets, or absolute local paths.
- Keep manifest ID, repository plugin path, and index ID identical.
- Mark sensitive settings as secrets and do not log tokens or private campaign content.
- Release process resources during shutdown.
- Do not claim a reserved capability is implemented.
- Build and locally install the package with `scripts/package_plugin.py`; compile process-plugin modules and run relevant host/frontend checks.

## 11. Common Errors

| Error or symptom | Cause | Resolution |
|------------------|-------|------------|
| Invalid plugin ID | ID pattern is wrong | Use lowercase letters, numbers, and hyphens |
| ID and directory differ | Folder, manifest, or package root differs | Make all three match |
| Unsupported type | Typo or unavailable runtime | Use a current documented type |
| Unknown permission | Manifest requests an unknown value | Use permissions from section 5 |
| Contribution path escapes | Absolute path or `..` | Keep resources inside the plugin |
| Multiple manifests | More than one plugin was packaged | Package one plugin per `.dfplugin` file |
| Local file is rejected | The selected file is not `.dfplugin` | Build it with `scripts/package_plugin.py` |
| Store Release is missing | The repository has no stable GitHub Release | Publish a non-draft, non-prerelease Release |
| Update needs approval | Runtime or effective permissions expanded | Review the change and confirm manually |
| Declarative content is missing | Plugin disabled or glob matched nothing | Enable it and check path case |
| Process fails to start | Invalid entrypoint or missing dependency | Run the entrypoint locally and inspect logs |
