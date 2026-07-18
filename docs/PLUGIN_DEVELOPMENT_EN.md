# DiceFrame Plugin Development Guide

[中文](PLUGIN_DEVELOPMENT_CN.md) | English

This guide defines DiceFrame plugin packages, manifests, settings, permissions, and extension boundaries. The capabilities available today are channel adapters, content packs, filtered theme variables, structured tools, and the location/asset subset of map packs. Import/export and Provider plugins remain reserved types without a business runtime.

**Only capabilities marked Supported or Partial below have an active integration. Reserved types may be recognized in a development directory, but they do not participate in their intended workflows and cannot be installed from the store. Plugin documentation must describe actual behavior without implying unavailable features.**

## 1. Plugin-System Goals

DiceFrame's plugin model covers channel adapters, content packs, themes, maps, import/export formats, model or media Providers, and focused utilities. The last three categories remain future extension points rather than current runtime capabilities.

## 2. Current Support

| Type | `plugin_type` | Current status |
|------|---------------|----------------|
| Channel adapter | `channel-adapter` | Supported: managed process, settings, start/stop, and DiceFrame HTTP API access |
| Content pack | `content-pack` | Supported: rules, worlds, content catalogs, and user-triggered imports |
| Theme | `theme` | Supported: filtered CSS custom properties |
| Map pack | `map-pack` | Partial: locations and icon/scene/grid assets; no live tabletop or editor |
| Import/export | `import-export` | Reserved: no unified task API; store installation disabled |
| Provider | `provider` | Reserved: no Provider runtime; store installation disabled |
| Tool | `tool` | Supported: process handshake, registration, structured invocation, timeout, and manual testing UI |

`content-pack`, `theme`, and `map-pack` are declarative and may omit a background process. `channel-adapter` and `tool` require an `entrypoint`.

## 3. Plugin Boundaries

- Each installed plugin occupies `plugins/<plugin-id>/` and each package contains exactly one `plugin.json`.
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
| Paper Theme | `plugins/examples/paper-theme` | `theme` | Safe CSS-variable themes |
| Echo Tool | `plugins/examples/echo-tool` | `tool` | Process handshake, registration, JSON arguments, and structured results |

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
- `docs` points to documentation inside the package.

Known types are `channel-adapter`, `content-pack`, `theme`, `map-pack`, `import-export`, `provider`, and `tool`.

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
| `map.assets` | Register map locations and static assets |
| `tool.execute` | Register and execute structured tool calls |

## 6.1 Security Boundaries

The host validates archive paths and budgets, identity, type, schema, entrypoint, contributions, and permissions. It separates secrets, confines declarative assets to declared paths, copies user imports out of plugin storage, and filters theme values.

Process plugins inherit only a small operating-system variable allowlist. A plugin declaring `diceframe.http` receives a DiceFrame URL and a host-generated token that belongs only to that plugin. Authors and users do not configure this token. The global Bot API token in Settings is reserved for external programs that are not managed as DiceFrame plugins. There is no complete OS sandbox: an entrypoint still executes as the same OS user as DiceFrame.

## 6.2 config.schema.json

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

### 7.2 Content Packs

Supported contributions are `rules`, `world_templates`, `character_templates`, `npcs`, `items`, `spells`, and `classes`. Enabled rules and worlds enter normal selectors. Other resources appear in the read-only plugin catalog and may be copied by the user into the card library or a selected lorebook. Imported copies remain after disabling or uninstalling the plugin.

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
    "classes": ["content/classes/*.json"]
  }
}
```

Use stable IDs and avoid built-in IDs. Content is never imported automatically. Worlds and catalog records declare `language`; world text is not automatically translated. Rules use `<rule_id>.json` for Chinese and `<rule_id>_en.json` for English, with Chinese fallback. Protocol fields and GM tags remain language-neutral.

### 7.3 Themes

Themes register JSON through `contributes.theme` or `contributes.themes`. The frontend applies filtered CSS custom properties and stores the selected theme in the current browser. Variable names begin with `--`; suspicious values containing `url(`, semicolons, or braces are ignored. Scripts, components, layouts, and arbitrary CSS are unsupported.

### 7.4 Map Packs

Map packs register `locations`, `icons`, `scenes`, and `grids`. Enabled locations enter `/api/games/{game_key}/map`; assets receive restricted URLs. Locations may use `world_id` or `worlds` filters. The runtime does not provide live tabletop play, editing, collision, layers, collaboration, or grid rules.

## 7.5 Capabilities Not Yet Implemented

- Content imports do not automatically enter a running game.
- Themes cannot inject components, layouts, scripts, or arbitrary CSS.
- Maps have no editor, tabletop rules, layers, or real-time collaboration.
- `import-export` has no unified task API.
- `provider` has no registration or selection runtime.
- `tool` supports short calls but not long-task progress, cancellation, result-file downloads, or automatic AI tool selection.

### 7.6 Import/Export Plugins

Reserved for character-card, world-book, and lorebook transformations. A future runtime must declare formats and versions, validate without overwriting user data, and avoid exposing private paths or content.

### 7.7 Provider Plugins

Reserved for LLM, embeddings, TTS, and image generation. Future Providers must keep API keys in secrets, use timeouts, handle network errors, and avoid logging prompts, responses, or tokens.

### 7.8 Tool Plugins

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

When installing, DiceFrame resolves the repository's latest stable GitHub Release to an exact commit, downloads GitHub's source archive for that commit, and validates the manifest again. Declarative plugins can update automatically when their permissions and runtime type do not expand. Process plugins notify the user and require confirmation. Any permission or runtime expansion is approval-required. `official`, `verified`, and `community` describe source/review status, not a security guarantee. See [PLUGIN_REGISTRY_EN.md](PLUGIN_REGISTRY_EN.md).

## 9. Install, Update, and Uninstall Semantics

- Store install: resolve the latest stable GitHub Release to an exact commit, extract to a temporary directory, validate, then move to `plugins/<id>/`.
- Local install: select a `.dfplugin` file created by `scripts/package_plugin.py`.
- Overwrite: explicit only; stop the old process before replacement.
- Update: resolve and validate the latest stable Release again. Safe declarative updates may run automatically; process or permission-expanding updates require confirmation.
- Uninstall: stop and remove plugin source while preserving plugin data by default.
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
