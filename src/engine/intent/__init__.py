"""Intent layer: structured player intents independent of AI output.

Intent 层回答"玩家想做什么"。它只解析玩家自己的行动文本（规则解析 +
轻量匹配，不消耗 LLM）。关键词解析无法确定价格，因此不再产生任何
持久化经济状态；持久报价的唯一路径是 check_planner 的 AI 工具调用
汇入 ``economy.queue_purchase_offer``。本模块保留作为该路径的解析基础
与 LLM 不可用时的兜底。

AI 叙事仍不是交易事实；只有付款人确认才会动钱。
"""
