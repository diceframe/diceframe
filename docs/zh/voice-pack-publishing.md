# 音色预设发布指南

中文 | [English](../en/voice-pack-publishing.md)

DiceFrame 的 `voice-pack` 是“音色预设”，不是 TTS 引擎安装包。它适合分发 OpenAI 兼容服务的 `voice_id`，或少量、已获授权的 GPT-SoVITS 参考音频。Kokoro、GPT-SoVITS、CUDA、Python 环境和基础模型应由上游项目安装，不应重复塞进插件。

## 先判断能不能发布

发布者必须能证明自己有权分发包内的每一项内容：

- 只有 Voice ID 和说明文字：记录模型、服务、版本、原始链接和许可证；不要暗示 DiceFrame 拥有或训练了该音色。
- 带试听或参考音频：同时确认录音、表演者声音、文本和后续分发许可。仅“网上能下载”或“写了来源”不等于可以重新发布。
- 克隆真人声音：必须取得明确授权，并避免使用可能造成冒充、诈骗或侵犯隐私的素材。
- `consent: true` 表示作者确认有权分发这个音色描述以及它引用的全部音频，不是一个可以随便填写的占位字段。

无法确认权利时，只写一份用户在自己设备上填写 Voice ID 的教程，不要制作公开音色包。

## 推荐目录

公开商店要求一个插件独占一个 GitHub 仓库，并且仓库根目录就是插件根目录：

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

OpenAI 兼容预设只需 JSON；GPT-SoVITS 才需要把已授权的 WAV/试听文件加入 `contributes.voice_assets`。每个音色都要有唯一 ID、显示名称、引擎、语言、许可证和授权确认。

## 清单示例

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
  "docs": "README_CN.md"
}
```

单个 OpenAI 兼容音色：

```json
{
  "schema_version": 1,
  "id": "narrator-zh",
  "name": "中文旁白",
  "engine": "openai-compatible",
  "voice_id": "service-native-voice-id",
  "language": "zh-CN",
  "license": "Apache-2.0",
  "consent": true
}
```

`license` 应描述这个预设实际分发内容的许可；README 和 NOTICE 还要分别列出所依赖模型、服务与音色的上游许可。不要把一个上游项目的许可证误写成所有第三方录音都自动获得授权。

## 本地验证

在 DiceFrame 主仓库运行：

```powershell
python scripts\package_plugin.py C:\path\to\my-voice-presets --overwrite
```

生成的 `.dfplugin` 可在“设置 → 插件 → 本地安装”中选择。至少完成以下测试：

1. 包能够安装、启用和卸载。
2. 设置页的音色列表显示名称、语言和正确引擎。
3. 对应的本地 TTS 服务启动后，“保存并试听”可以生成音频。
4. 服务未启动、Voice ID 不存在时，页面给出可理解的错误且不影响游戏。
5. 包内没有模型权重、`.env`、Token、Cookie、本机绝对路径、日志或用户录音。

`.dfplugin` 只用于本地或离线分享；官方商店不会要求作者上传它。

## 发布到插件商店

1. 把插件放进独立、公开的 GitHub 仓库，检查根目录结构和 `repository_url`。
2. 提交代码并创建与 `plugin.json.version` 对应的 Git tag，例如 `v1.0.0`。
3. 创建一个非草稿、非预发布的 GitHub Release。无需上传 ZIP 或计算 SHA-256。
4. 在 [`diceframe/diceframe-plugins` 的“添加插件”Issue](https://github.com/diceframe/diceframe-plugins/issues/new/choose) 里填写插件 ID 和仓库地址。
5. 自动检查失败时按评论修复，然后回复 `/recheck`；通过后等待维护者审核上架。

普通作者不需要登录 DiceFrame Hub 管理后台。Hub 登录只用于维护者审核、同步和治理；公开投稿以 GitHub Release 和投稿 Issue 为准。

## 后续更新

保持插件 ID、仓库和权限不变时，只需更新三段式版本号，创建新 tag 和正式 Release。商店会从最新 Release 解析固定提交，并在用户确认后更新。增加权限、更换仓库、转移所有权或改成带进程的插件时，必须重新投稿审核。

## Kokoro 示例

仓库内的 `plugins/examples/kokoro-zh-voice-presets` 仅分发四个 Kokoro Voice ID 和说明文字，不包含音频、模型或服务代码。Kokoro-82M 与 Kokoro-FastAPI 均采用 Apache-2.0 许可；实际使用仍需用户单独启动兼容服务。它适合用来学习、打包和做本地安装测试，不代表 DiceFrame 对上游音质、身份或长期兼容性作保证。
