# Plugin Extension Paths

Use this page to decide where a new capability belongs. The full publishing, marketplace, and review contract remains in the [DiceFrame Content plugin development guide](https://github.com/diceframe/diceframe-content/blob/main/docs/en/plugin-development.md).

Every plugin starts with `plugin.json`. `schema_version`, `id`, `name`, `version`, and `plugin_type` form its core identity. The host validates the manifest, permissions, and runtime descriptors before application code consumes them. A TypedDict is not runtime validation.

## A. Content Pack

1. Use `plugin_type: "content-pack"`; new packages should prefer `content_schema_version: 2` and typed locales.
2. Declare relative paths for rules, world templates, characters, NPCs, items, spells, classes, or maps under `contributes`.
3. Keep canonical IDs separate from display names. Locales translate display fields and never change identity or mechanics.
4. Declare rule currency through `currency_system` (`schema_version: 2`): `base_unit` is what the canonical integer counts (its rate must be 1), `display_unit` is the default display unit, and unit rates are positive integers. Loading/installing validates through `RuleBundleLoader` -> `validate_currency_system` and fails closed on bad declarations; rules carrying only a `currency` label keep legacy rate=1 semantics and are never reinterpreted by name. See `plugins/examples/starter-content-v2/content/rules/moonlight.json` for an example.
5. Follow `plugins/examples/starter-content-v2/` and test content validation, locale fallback, and duplicate-ID rejection.
6. Validate and package with:

```bash
python scripts/package_plugin.py path/to/plugin
```

This path does not require a `PluginHost` edit.

## B. Tool Plugin

1. Use `plugin_type: "tool"` and a safe `entrypoint` array. The host requires the `tool.execute` permission.
2. Use `src.plugin_sdk.ToolRuntime` and register name, title, description, and an object `input_schema` through `runtime.tool(...)`.
3. A handler receives `arguments` and `context` JSON objects and returns a JSON object. Do not implement a second stdio protocol.
4. Follow `plugins/examples/echo-tool/` and test the initialize descriptor, `tool.call`, and invalid-input rejection.

## C. Provider Capability

1. Use `plugin_type: "provider"`. Declare network needs through the manifest/configuration and the host permission boundary.
2. Use `src.plugin_sdk.ProviderRuntime` and `runtime.capability(kind=..., version=...)`. The current public SDK method alias is `generate`.
3. `kind` is the stable capability namespace. The handler receives `arguments` / `context` and returns a JSON object.
4. Adding a valid capability kind does not modify `PluginHost`; add the SDK/plugin implementation and behavior tests.

## D. Bot Extension

1. Use `plugin_type: "bot-extension"` and `src.plugin_sdk.BridgeExtensionRuntime`.
2. `runtime.extension(...)` can declare `before_message`, `after_result`, and `render` stages plus priority, timeout, platforms, and kinds.
3. A handler returns a JSON object containing `handled`, a replacement `payload`, or `outputs`. Outputs use host-validated text/card/image shapes and remain subject to path, count, and length limits.
4. Follow `plugins/examples/bridge-customizer/` and test stage selection, priority, platform/kind filtering, and output validation.

## E. A Genuinely New Plugin Type

First verify that the feature is not merely a new capability of content-pack, tool, provider, or bot-extension. Add a plugin type only when its process mode, lifecycle, or security semantics are genuinely different.

Review each of these owners:

- the support/process/permission/contribution/cleanup descriptor in `src/plugin_host/support.py`;
- the initializer in `src/plugin_host/capabilities.py` for an RPC type;
- permission policy, package/lifecycle cleanup, and public support metadata;
- manifest, security, lifecycle, and SDK/descriptor behavior tests;
- frontend metadata and docs only when the type is user-visible.

## Before Submission

Run the affected plugin pytest suite, `python -m ruff check src/`, `python -m mypy`, and `python scripts/package_plugin.py ...`. Tests should protect descriptors, RPC behavior, permissions, path safety, and compatibility—not helper names or file shape.
