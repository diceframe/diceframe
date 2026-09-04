"""Intent layer: structured player intents independent of AI output.

Intent 层回答"玩家想做什么"。它只解析玩家自己的行动文本（规则解析 +
轻量匹配，不消耗 LLM），把结果记录为待处理购买请求：

```text
Player action → PurchaseIntent → purchase_request → explicit GM order
```

AI 叙事仍不是交易事实；只有显式 GM 订单和付款人确认才会动钱。
"""
