#!/usr/bin/env python3
"""DiceFrame 命令行跑团调试入口。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.engine.game_instance import GameRegistry, GameState
from src.llm.client import LLMClient, ProviderConfig
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.store import LorebookStore
from src.memory.delta import MemoryStore
from src.commands.game_handler import GameHandler
from src.ai_providers import resolve_provider
from src.network_proxy import effective_proxy_url
from src.webui.runtime_config import ConfigStore, RuntimePaths

# ---------- 配置（与 WebUI 共用服务商配置契约） ----------
DATA_DIR = Path(os.getenv("TRPG_DATA_DIR", str(ROOT / "data")))

# 颜色输出
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_gm(text: str) -> None:
    for line in text.split("\n"):
        print(f"{GREEN}{line}{RESET}")

def print_info(text: str) -> None:
    print(f"{CYAN}{text}{RESET}")

def print_warn(text: str) -> None:
    print(f"{YELLOW}{text}{RESET}")


async def main() -> None:
    print(f"{BOLD}TRPG 独立运行器{RESET}")
    config = ConfigStore(RuntimePaths.from_root(ROOT, os.environ), os.environ).load().state
    provider = resolve_provider(config, config.get("llm_provider_ref", ""))
    model = str(config.get("model") or "").strip()
    if not provider or not provider["base_url"] or not model:
        print("请先在 WebUI 的 AI 服务商与模型配置中设置主模型。")
        return
    print(f"模型: {model}  |  数据目录: {DATA_DIR}")
    print()

    # 初始化模块
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    providers = [ProviderConfig(
        provider_name="default", base_url=provider["base_url"],
        api_key=provider["api_key"], model_name=model, api_format=provider["api_format"],
    )]
    llm_client = LLMClient(
        providers=providers,
        default="default",
        proxy_url=effective_proxy_url(
            bool(config.get("proxy_enabled")), config.get("proxy_url", ""),
        ),
    )

    lorebook_store = LorebookStore(DATA_DIR / "lorebook.db")
    lorebook_store.open()
    lorebook_matcher = KeywordMatcher()

    memory_store = MemoryStore(DATA_DIR / "memory.db")
    memory_store.open()

    registry = GameRegistry(DATA_DIR / "saves")
    recovered = await registry.recover_all()
    if recovered:
        print_info(f"恢复了 {len(recovered)} 个未完成对局")

    prompts_dir = ROOT / "prompts"
    rules_dir = ROOT / "templates" / "rules"

    handler = GameHandler(
        registry=registry, llm_client=llm_client,
        lorebook_matcher=lorebook_matcher,
        lorebook_store=lorebook_store,
        memory_store=memory_store,
        prompts_dir=prompts_dir, rules_dir=rules_dir,
        worlds_dir=ROOT / "templates" / "worlds",
    )

    # 创建测试对局
    game_key = ("local", "cli_user", "test_bot")
    instance = registry.get_or_create(game_key)

    if instance.state in (GameState.CREATED, GameState.ENDED):
        print_info("创建新游戏……")
        instance = await handler.create_game(
            game_key, world_id="test_fantasy",
            world_name="经典奇幻冒险（测试）", group_name="命令行",
        )
        narration = await handler.start_game(instance)
        print_gm("\n" + narration + "\n")
    else:
        print_info("继续已有游戏……")
        await instance.resume()
        await instance.start_round()
        print_gm(f"\n游戏恢复。当前场景: {instance.scene}\n")

    # 命令行交互
    print_info("输入行动描述参与游戏，输入 /go 推进回合，/状态 查看信息，/存档 保存，/退出 结束")
    print_info("例如: 我推开酒馆的门走了进去")
    print()

    while True:
        try:
            user_input = input(f"{BOLD}> {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        instance = registry.get(game_key)
        if not instance:
            break

        # 命令处理
        if user_input in ("/退出", "/quit", "/q"):
            await instance.end()
            await registry.save(instance)
            print("游戏已结束。")
            break

        elif user_input in ("/go", "/下一轮", "/推进"):
            if instance.can_accept_actions():
                if await instance.try_advance():
                    narration, _ = await handler.process_round(instance)
                    print_gm("\n" + narration + "\n")
            else:
                print_warn("当前不能推进（可能已在判定中）")

        elif user_input in ("/状态", "/status"):
            print_info(f"Round {instance.round_number} | 场景: {instance.scene} | "
                        f"状态: {instance.state.value}")
            for uid, p in instance.players.items():
                cs = p.get("character_sheet", {})
                print(f"  {p.get('character_name', uid)}: HP {cs.get('hp','?')}/{cs.get('max_hp','?')}")

        elif user_input in ("/存档", "/save"):
            await registry.save(instance)
            print_info("存档完成。")

        elif user_input.startswith("/"):
            print_warn(f"未知命令: {user_input}")

        else:
            # 视为玩家行动
            uid = "cli_user"
            if uid not in instance.players:
                instance.players[uid] = {
                    "character_name": "冒险者",
                    "character_sheet": {
                        "race": "人类", "class": "冒险者", "level": 1,
                        "attributes": {"str": 12, "dex": 12, "con": 12, "int": 12, "wis": 12, "cha": 12},
                        "hp": 70, "max_hp": 70,
                        "equipment": [{"name": "铁剑", "damage": 6, "slot": "main_hand", "quality": "common"}],
                        "inventory": [{"name": "医疗包", "qty": 2, "effect": "回复20HP"}],
                        "skills": ["重击"], "background": "", "deceased": False,
                    },
                }
            ok = await instance.add_action(uid, user_input)
            if ok and await instance.try_advance():
                narration, _ = await handler.process_round(instance)
                print_gm("\n" + narration + "\n")
            elif ok:
                print_info("（行动已记录，等待更多玩家行动或输入 /go 推进）")

    # 清理
    lorebook_store.close()
    memory_store.close()


if __name__ == "__main__":
    asyncio.run(main())
