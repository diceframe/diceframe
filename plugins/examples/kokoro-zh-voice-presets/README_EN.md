# Kokoro Chinese Voice Presets

[中文](README_CN.md) | English

This lightweight DiceFrame `voice-pack` example provides four Kokoro-82M Mandarin voice IDs: `zf_xiaobei`, `zf_xiaoxiao`, `zm_yunxi`, and `zm_yunyang`. It contains no audio, model weights, Python environment, or TTS server, so the package consists only of small JSON and documentation files.

## Start Kokoro-FastAPI first

With Docker Desktop installed, start the pinned CPU image:

```powershell
docker run --name diceframe-kokoro -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:v0.6.0
```

The first start downloads the image and model. When ready, open `http://127.0.0.1:8880/docs` to check the service.

## Configure DiceFrame

1. For local development, package this directory as `.dfplugin` and choose it under Settings → Plugins → Local Install. Enable the plugin after installation.
2. Open Settings → Advanced → Text-to-speech.
3. Select OpenAI compatible and set Base URL to `http://127.0.0.1:8880/v1`.
4. Leave API Key empty, set the model to `kokoro`, and choose `MP3`.
5. Select one of the contributed voices under Role voice mapping, then use Save and test.

When DiceFrame runs in Docker, a common desktop setup uses `http://host.docker.internal:8880/v1`. Containers on the same custom network may use the Kokoro service name instead.

## Package for testing

Run from the DiceFrame repository:

```powershell
python scripts\package_plugin.py plugins\examples\kokoro-zh-voice-presets --overwrite
```

## License and provenance

The manifest, voice-ID metadata, and documentation in this plugin use Apache License 2.0. No upstream files are redistributed:

- Kokoro-82M model and voice list: <https://huggingface.co/hexgrad/Kokoro-82M>, Apache-2.0.
- Kokoro-FastAPI: <https://github.com/remsky/Kokoro-FastAPI>, Apache-2.0.

`consent: true` in each JSON confirms only that these identifiers and descriptions may be distributed under the cited license. The package contains no reference recording and makes no claim that a voice represents a real person. Upstream projects remain responsible for model quality, downloads, and runtime support.
