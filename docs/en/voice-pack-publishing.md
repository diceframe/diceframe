# Publishing Voice Presets

[中文](../zh/voice-pack-publishing.md) | English

A DiceFrame `voice-pack` is a voice preset, not a TTS engine installer. It can distribute `voice_id` values for an OpenAI-compatible service or small, authorized GPT-SoVITS reference files. Upstream projects should install Kokoro, GPT-SoVITS, CUDA, Python, and base models; do not bundle those large dependencies again.

## Confirm redistribution rights first

The publisher must be able to document the right to distribute every item in the package:

- For voice IDs and documentation only, record the model, service, version, source URL, and license. Do not imply that DiceFrame owns or trained the voice.
- For preview or reference audio, confirm the recording, performer voice, spoken text, and redistribution rights. Attribution alone is not permission.
- For a cloned real-person voice, obtain explicit permission and avoid material that could enable impersonation, fraud, or privacy abuse.
- `consent: true` is the author's confirmation that the voice descriptor and every referenced audio asset may be distributed. It is not a placeholder.

When rights are unclear, publish setup instructions that let users enter a voice ID on their own device instead of publishing a public voice pack.

## Recommended repository layout

The public store requires one plugin per public GitHub repository, with the plugin at the repository root:

```text
my-voice-presets/
├─ plugin.json
├─ config.schema.json
├─ README_CN.md
├─ README_EN.md
├─ LICENSE
├─ NOTICE.md
└─ voices/
   ├─ narrator.json
   └─ companion.json
```

OpenAI-compatible presets need JSON only. GPT-SoVITS packages must list every authorized WAV or preview file under `contributes.voice_assets`. Every voice needs a unique ID, display name, engine, language, license, and authorization confirmation.

## Manifest example

```json
{
  "schema_version": 1,
  "id": "my-voice-presets",
  "name": "My Voice Presets",
  "version": "1.0.0",
  "description": "Small voice-ID presets for an existing local TTS service.",
  "plugin_type": "voice-pack",
  "repository_url": "https://github.com/username/my-voice-presets",
  "config_schema": "config.schema.json",
  "permissions": ["plugin.config", "voice.assets"],
  "contributes": {"voices": ["voices/*.json"]},
  "docs": "README_EN.md"
}
```

One OpenAI-compatible voice:

```json
{
  "schema_version": 1,
  "id": "narrator-zh",
  "name": "Chinese Narrator",
  "engine": "openai-compatible",
  "voice_id": "service-native-voice-id",
  "language": "zh-CN",
  "license": "Apache-2.0",
  "consent": true
}
```

`license` describes the material actually distributed by the preset. README and NOTICE should separately identify upstream licenses for models, servers, and voices. A project-level license must not be treated as automatic permission for unrelated third-party recordings.

## Local validation

Run this from the DiceFrame repository:

```powershell
python scripts\package_plugin.py C:\path\to\my-voice-presets --overwrite
```

Install the resulting `.dfplugin` under Settings → Plugins → Local Install, then verify:

1. Install, enable, disable, and uninstall all work.
2. Voice names, languages, and engines appear correctly.
3. Save and test produces audio while the matching local TTS service is running.
4. A stopped service or missing voice ID produces a readable error without affecting the game.
5. The package contains no model weights, `.env`, token, cookie, absolute local path, log, or user recording.

The `.dfplugin` is for local or offline distribution. The public store does not require authors to upload it.

## Publish to the plugin store

1. Put the plugin in its own public GitHub repository and verify the root layout and `repository_url`.
2. Commit the files and create a Git tag matching `plugin.json.version`, such as `v1.0.0`.
3. Publish a non-draft, non-prerelease GitHub Release. No ZIP upload or SHA-256 is required.
4. Open the [`diceframe/diceframe-plugins` Add plugin issue](https://github.com/diceframe/diceframe-plugins/issues/new/choose) with the plugin ID and repository URL.
5. Fix automated findings and reply `/recheck`; after validation, wait for maintainer approval.

Ordinary authors do not log in to the DiceFrame Hub administration panel. Hub login is only for maintainers performing review, synchronization, and moderation; public submission is based on the GitHub Release and submission issue.

## Later releases

When the ID, repository, and permissions stay unchanged, bump the semantic version and publish a new tag and stable Release. The store resolves the new Release to a fixed commit and updates after user confirmation. Permission expansion, repository replacement, ownership transfer, or a switch to process execution requires another review.

## Kokoro example

`plugins/examples/kokoro-zh-voice-presets` distributes four Kokoro voice IDs and documentation only. It contains no audio, model, or server code. Kokoro-82M and Kokoro-FastAPI use Apache-2.0, while users start the compatible service separately. The example is suitable for learning, packaging, and local installation tests; DiceFrame does not guarantee upstream voice quality, identity, or long-term compatibility.
