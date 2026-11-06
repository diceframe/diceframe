# 翻译维护

[English](../en/translations.md) | 简体中文

DiceFrame 官方维护**英语和简体中文**界面及文档。集中式界面文案以英语为来源，两份核心目录必须具有相同的键；新功能补齐这两种语言。

日语、德语和俄语是现有的**社区翻译**，继续保留选择入口，但覆盖程度和后续翻译由对应语言的志愿维护者负责。贡献一个语言不会把长期翻译义务转交给项目负责人；过去贡献过译文也不等于被自动指定为永久维护者。

## 语言维护概览

官方维护英语和简体中文界面及文档。日语、德语和俄语保留为社区翻译，后续译文由对应语言的志愿维护者负责，贡献一个语言不会让项目负责人承担长期翻译义务。

集中式界面缺失或尚未核对新版英文的译文会回退英文；社区语言可以暂停，同时保留语言 ID、存档和机制。维护状态及贡献流程见[翻译维护](translations.md)。

## 后端意图资源

购买、询价、拒绝购买、免费领取和延期付款等自然语言识别使用稳定的 intent ID，资源位于 `src/engine/intent/locales/<locale>/intents.json`。新增语言应新增资源文件，至少声明当前实现需要的稳定键：

```text
purchase_intent
purchase_decline
purchase_offer
free_purchase
deferred_payment
currency_labels
purchase_target_prefix
purchase_target_connector
purchase_target_suffix
purchase_quantity_classifier
purchase_generic_target
purchase_quantity_word
purchase_balance
```

资源只描述语言触发模式；它不能改变 canonical 币种、金额换算、预算、权限、交易状态或规则机制。经济引擎只消费稳定 intent ID，不得把翻译后的 display name 当作 identity。缺失语言资源时，解析器按 locale → 英语 → 简体中文回退；缺失资源不得让系统猜测价格或放行免费物品。

每个新增语言必须通过 intent 资源契约测试、UTF-8 校验和相关经济回归测试。新增语言不应修改 `src/engine/economy.py` 或交易结算逻辑；如果必须改业务代码，说明资源契约没有覆盖新的语言语义，应先补稳定 intent ID 或资源字段。

经济边界提示和经济 planner 补扫提示位于同目录的 `messages.json`，稳定键包括 `unbacked_payment_notice`、`unbacked_purchase_notice`、`unpriced_purchase_notice`、`pending_decision_notice` 和 `economy_repair_prompt`。新增语言必须同时提供这些消息资源；缺失资源按 locale → 英语 → 简体中文回退。

报价语义不依赖翻译后的关键词作为权威。结构化报价可携带 `evidence` 原文证据，服务端校验证据是否来自可信玩家行动或 GM 叙事；无法验证时只保留无价意图，不创建订单。

## 状态与回退

`src/localization/registry.json` 登记语言层级 `core` / `community`、状态 `active` / `paused` 及本人同意登记的维护者。社区维护者列表为空表示欢迎志愿者接手，现有目录暂作为已接受基线继续启用。新增社区语言需要志愿维护者、回退验证和接入 PR。

集中式界面缺失的键回退英文。社区译文对应的英文来源指纹保存在 `frontend-v2/src/i18n/source-review.json`；英文改动后，旧译文不再覆盖新版英文，直到志愿者核对并更新该键。发布不等待所有社区语言翻译完整。

无法确认持续维护或文案正确性时，将社区语言设为 `paused`：界面语言选择器不再提供它，已有界面偏好显示英文。游戏、内容、筛选和存档仍识别原语言 ID；重新启用后恢复已核对的译文。暂停不重命名内容、不转换机制，也不修改用户要求的 AI GM 叙事语言。

来源指纹覆盖集中式前端目录，包括嵌套 API 错误。共享后端文案、字段 helper 和内容 overlay 遵守暂停状态并回退英文。其他功能内和后端的译文暂不自动检测语义过时；停止维护时以明确暂停控制回退。这是显示策略，不承诺所有规则内容都有完整译本。

## 贡献流程

1. 修改 `frontend-v2/src/i18n/messages/<locale>.ts`，保留参数名、协议标签和持久化身份。
2. 英文来源有变化时，先核对译文，再只登记这些已经核对的键：

   ```bash
   cd frontend-v2
   npm run locales:review -- --locale de --keys scriptImportHint,apiErrors.WORLD_NOT_FOUND
   ```

3. 运行 `npm run locales:check`、类型检查和相关测试。核心目录缺少或多余的键会失败；社区缺失、未核对的键会报告并回退英文，不阻止无关功能开发。
4. 译文与核对记录一起提交，不要重新生成整份已接受基线来消除过时提示。

官方用户文档只发布英文和简体中文，其他文档翻译可由社区独立维护，不成为核心发布要求。修改显示语言不得悄悄改写对局语言、规则 ID、角色 ID 或数值机制。
