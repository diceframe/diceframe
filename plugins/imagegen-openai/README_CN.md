# imagegen-openai：图像生成（OpenAI 兼容）

DiceFrame 内置的 `provider` 型插件：通过 OpenAI 兼容的 `/images/generations`
接口生成场景图。启用后，GM 在剧情发生重大场景切换（输出 `SCENE_IMAGE` 标签）时
自动生成一张场景图，展示在叙事流、场景卡与画廊中，并可一键设为地图背景。

## 配置

| 配置 | 说明 |
| --- | --- |
| AI 服务商 | 从“设置 → 模型接口”选择已保存的 OpenAI 兼容服务商，统一继承 Base URL 与 API Key |
| 模型 | 从所选服务商已保存的模型目录选择；模型列表也只在“模型接口”中维护 |
| 图片尺寸 | 如 `1792x1024`（横幅，推荐）、`1024x1024` |
| 风格前缀 | 拼接在每条画面描述前的统一风格，如 `fantasy oil painting, dramatic lighting` |
| 超时（秒） | 单次生图最长等待，默认 120 |

## 工作方式

- 插件以独立进程运行，宿主通过 JSON-RPC stdio 调用其 `image-generation` capability。
- 生成的图片经宿主归一化为 WebP 资产（内容寻址存储），与上传的场景头图共用管线。
- 停用本插件即可关闭自动生图；GM 提示词中的 `SCENE_IMAGE` 标签会被忽略。
- 生图仅在回合判定完成后于后台进行，不阻塞叙事推送。
