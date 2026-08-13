# Kokoro 中文音色预设

中文 | [English](README_EN.md)

这是一个轻量 DiceFrame `voice-pack` 示例，只提供四个 Kokoro-82M 中文 Voice ID：`zf_xiaobei`、`zf_xiaoxiao`、`zm_yunxi` 和 `zm_yunyang`。插件不包含音频、模型权重、Python 环境或 TTS 服务，因此安装包只有很小的 JSON 和文档。

## 先启动 Kokoro-FastAPI

已安装 Docker Desktop 时，可以启动固定版本的 CPU 镜像：

```powershell
docker run --name diceframe-kokoro -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:v0.6.0
```

首次启动会下载镜像和模型。服务就绪后，可打开 `http://127.0.0.1:8880/docs` 检查状态。

## DiceFrame 设置

1. 本地开发时把本目录打包为 `.dfplugin`，在“设置 → 插件 → 本地安装”选择；商店收录后可直接安装。安装完成后启用本插件。
2. 打开“设置 → 高级参数 → 语音朗读”。
3. 语音引擎选“OpenAI 兼容”，Base URL 填 `http://127.0.0.1:8880/v1`。
4. API Key 留空，模型填 `kokoro`，音频格式选 `MP3`。
5. 在角色音色分配里选择本插件提供的音色，点击“保存并试听”。

DiceFrame 运行在 Docker 中时，常见桌面环境要把地址改为 `http://host.docker.internal:8880/v1`；两个容器在同一个自定义网络时也可以使用 Kokoro 服务名。

## 打包测试

从 DiceFrame 主仓库运行：

```powershell
python scripts\package_plugin.py plugins\examples\kokoro-zh-voice-presets --overwrite
```

## 许可与来源

本插件自己的清单、Voice ID 元数据和文档使用 Apache License 2.0。它不重新分发上游文件：

- Kokoro-82M 模型与音色清单：<https://huggingface.co/hexgrad/Kokoro-82M>，Apache-2.0。
- Kokoro-FastAPI：<https://github.com/remsky/Kokoro-FastAPI>，Apache-2.0。

各 JSON 的 `consent: true` 仅确认本包有权按上述许可分发这些标识和说明；本包不包含参考录音，也不声称音色对应某位真实人物。音质、模型下载和运行环境由上游项目负责。
