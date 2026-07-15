"""DiceFrame —— 结构化跑团引擎。骰子决定命运，引擎驱动世界。"""

import asyncio
import json
import logging
import re
from pathlib import Path

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase

from src.commands.game_handler import GameHandler
from src.common_factory import TRPGSubsystems, create_trpg_subsystems
from src.engine.character_utils import make_default_character
from src.engine.game_instance import GameRegistry, GameState
from src.generation.creator import generate_character, generate_world
from src.llm.client import LLMClient, ProviderConfig
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.store import LorebookStore
from src.memory.delta import MemoryStore

logger = logging.getLogger("trpg")


# ---------- 配置模型 ------------------------------

class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "基础设置"
    enabled: bool = Field(default=True, description="是否启用插件")
    max_cost_per_session: float = Field(default=0.0, description="每局费用上限（元），0=不限制")
    debug_mode: bool = Field(default=False, description="Debug 模式")


class ModelProviderConfig(PluginConfigBase):
    __ui_label__ = "模型供应"
    provider_name: str = Field(default="deepseek", description="供应商标识")
    base_url: str = Field(default="https://api.deepseek.com/v1", description="API 地址")
    api_key: str = Field(default="", description="API 密钥（不回显）")
    model_name: str = Field(default="deepseek-chat", description="模型名")
    fallback: bool = Field(default=False, description="主模型连续失败时作为备用")


class TRPGConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    admin_qq: list[str] = Field(default=[], description="管理员QQ号")
    allow_start_roles: list[str] = Field(default=["admin", "group_owner"])
    game_whitelist_groups: list[str] = Field(default=[])
    model_providers: list[ModelProviderConfig] = Field(default=[ModelProviderConfig()])
    default_provider: str = Field(default="deepseek")
    gm_style: str = Field(default="中式奇幻")
    game_rules: str = Field(default="轻量自由规则")
    max_context_rounds: int = Field(default=10)
    narrative_max_tokens: int = Field(default=1024, description="GM 叙事最大 tokens")
    character_gen_max_tokens: int = Field(default=2048, description="角色/世界生成最大 tokens")
    summary_max_tokens: int = Field(default=400, description="摘要压缩最大 tokens")
    brief_max_tokens: int = Field(default=300, description="续接/简短回复最大 tokens")
    analysis_max_tokens: int = Field(default=512, description="局势分析最大 tokens")
    text_gen_max_tokens: int = Field(default=400, description="文字生成最大 tokens")
    auto_summarize_after: int = Field(default=10)
    dice_visible: bool = Field(default=True)
    max_players: int = Field(default=6)
    difficulty: str = Field(default="标准")
    inventory_size: int = Field(default=6)
    content_warning_message: str = Field(default="")


# ---------- TRPGPlugin -------------------------------------

class TRPGPlugin(MaiBotPlugin):
    config_model = TRPGConfig

    def __init__(self) -> None:
        super().__init__()
        self._sub: TRPGSubsystems | None = None
        self._worlds_dir: Path | None = None

    @property
    def registry(self) -> GameRegistry | None:
        return self._sub.registry if self._sub else None

    @property
    def llm_client(self) -> LLMClient | None:
        return self._sub.llm_client if self._sub else None

    @property
    def lorebook_store(self) -> LorebookStore | None:
        return self._sub.lorebook_store if self._sub else None

    @property
    def lorebook_matcher(self) -> KeywordMatcher | None:
        return self._sub.lorebook_matcher if self._sub else None

    @property
    def memory_store(self) -> MemoryStore | None:
        return self._sub.memory_store if self._sub else None

    @property
    def handler(self) -> GameHandler | None:
        return self._sub.handler if self._sub else None

    # ---- 生命周期 ----

    async def on_load(self) -> None:
        data_dir = Path(self.ctx.paths.data_dir)
        prompts_dir = Path(__file__).parent / "prompts"
        rules_dir = Path(__file__).parent / "templates" / "rules"
        self._worlds_dir = Path(__file__).parent / "templates" / "worlds"

        providers = [
            ProviderConfig(
                provider_name=p.provider_name,
                base_url=p.base_url,
                api_key=p.api_key or await self._read_mai_key(),
                model_name=p.model_name,
                fallback=p.fallback,
            )
            for p in self.config.model_providers
        ]

        self._sub = create_trpg_subsystems(
            data_dir=data_dir,
            prompts_dir=prompts_dir,
            rules_dir=rules_dir,
            worlds_dir=self._worlds_dir,
            providers=providers,
            default_provider=self.config.default_provider,
            narrative_max_tokens=self.config.narrative_max_tokens,
            character_gen_max_tokens=self.config.character_gen_max_tokens,
            summary_max_tokens=self.config.summary_max_tokens,
            brief_max_tokens=self.config.brief_max_tokens,
            analysis_max_tokens=self.config.analysis_max_tokens,
        )

        recovered = await self._sub.registry.recover_all()
        logger.info("TRPG 已加载，恢复 %d 个对局", len(recovered))

        for inst in recovered:
            asyncio.create_task(self._auto_resume(inst))
        self._save_task = asyncio.create_task(self._periodic_save())

    async def on_unload(self) -> None:
        if hasattr(self, "_save_task") and self._save_task:
            self._save_task.cancel()
        if self._sub:
            await self._sub.registry.save_all_active()
            self._sub.lorebook_store.close()
            self._sub.memory_store.close()
        logger.info("TRPG 已卸载")

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        logger.info("TRPG 配置热更新, scope=%s", scope)

    async def _read_mai_key(self) -> str:
        try:
            return await self.ctx.config.get("model_config.api_key") or ""
        except Exception:
            return ""

    # ---- 命令 ----

    @Command("cmd_trpg_start", description="开始跑团。用法: /跑团开始 [模板名] 或 /跑团开始 自由 [描述] 或 /跑团开始 种子 [种子码]",
             pattern=r"/跑团开始", chat_scope="group")
    async def cmd_start(self, stream_id: str = "", user_id: str = "", group_id: str = "",
                        platform: str = "qq", text: str = "", **kwargs) -> tuple:
        if not self.handler or not self.registry:
            await self.ctx.send.text("系统未就绪", stream_id)
            return True, None, True

        account_id = kwargs.get("account_id", "bot_1")
        game_key = GameRegistry.make_game_key(platform, group_id, account_id)
        instance = self.registry.get_or_create(game_key)
        if instance.state not in (GameState.CREATED, GameState.ENDED):
            await self.ctx.send.text("当前群已有进行中的游戏。", stream_id)
            return True, None, True

        raw = text.replace("/跑团开始", "").strip()
        group_name = kwargs.get("group_name", "")

        # 解析难度关键词
        difficulty = self.config.plugin.difficulty or "标准"
        for kw, diff_val in [("简单", "轻松"), ("轻松", "轻松"), ("容易", "轻松"),
                               ("硬核", "硬核"), ("困难", "硬核"), ("噩梦", "硬核"),
                               ("普通", "标准"), ("标准", "标准")]:
            if raw.startswith(kw):
                difficulty = diff_val
                raw = raw[len(kw):].strip()
                break

        if raw.startswith("种子"):
            seed_code = re.sub(r"^种子\s*", "", raw).strip()
            if not seed_code:
                await self.ctx.send.text("请提供种子码，例如: /跑团开始 种子 brave-dragon-472", stream_id)
                return True, None, True
            target = self._find_game_by_seed(seed_code)
            if not target:
                await self.ctx.send.text(f"未找到种子码「{seed_code}」对应的游戏。", stream_id)
                return True, None, True
            world_id = target.world_id or "default_fantasy"
            world_name = target.world_name
            instance = await self.handler.create_game(
                game_key, world_id=world_id, world_name=world_name,
                group_name=group_name, seed_code=seed_code,
                difficulty=difficulty,
            )
            narration = await self.handler.start_game(instance)
            await self.ctx.send.text(
                f"{narration}\n\n种子码: {instance.seed_code}（之后可用此码重开相同世界）", stream_id)
            return True, None, True

        if raw.startswith("自由") or raw.startswith("生成"):
            world_desc = re.sub(r"^(自由|生成)\s*", "", raw).strip()
            if not world_desc:
                world_desc = "一个经典奇幻冒险世界"
            await self.ctx.send.text(f"正在为你生成世界「{world_desc}」...（约需15-30秒）", stream_id)
            result = await self._ai_generate_world(world_desc)
            if not result["ok"]:
                await self.ctx.send.text(f"世界生成失败：{result.get('error', '未知错误')}\n使用默认世界创建...", stream_id)
                world_id = "default_fantasy"
                world_name = world_desc or "经典奇幻冒险"
            else:
                world_id = result["world_id"]
                world_name = result["world_name"]
        else:
            world_id = raw if raw else "default_fantasy"
            world_name = raw or "经典奇幻冒险"

        instance = await self.handler.create_game(
            game_key, world_id=world_id,
            world_name=world_name, group_name=group_name,
            difficulty=difficulty,
        )
        narration = await self.handler.start_game(instance)
        await self.ctx.send.text(
            (narration or f"游戏「{world_name}」已开始！发 /跑团加入 创建角色，或直接发言参与冒险。")
            + f"\n\n种子码: {instance.seed_code}",
            stream_id)
        return True, None, True

    @Command("cmd_trpg_newbie", description="零基础一键开团：描述你想玩的世界，AI 自动生成一切",
             pattern=r"/跑团新手", chat_scope="all")
    async def cmd_newbie(self, stream_id: str = "", user_id: str = "",
                         text: str = "", **kwargs) -> tuple:
        if not self.handler or not self.registry or not self.llm_client:
            await self.ctx.send.text("系统未就绪", stream_id)
            return True, None, True

        raw = text.replace("/跑团新手", "").strip()
        if not raw:
            await self.ctx.send.text(
                "零基础开团！你只需要告诉我你想玩什么：\n\n"
                "直接描述你想玩的世界即可，例如：\n"
                "  /跑团新手 赛博朋克东京，高科技低生活\n"
                "  /跑团新手 武侠江湖，正邪两道恩怨纠葛\n"
                "  /跑团新手 克苏鲁恐怖，1920年代调查员\n\n"
                "AI 会自动生成世界观、NPC、场景和故事开场！",
                stream_id)
            return True, None, True

        await self.ctx.send.text(f"正在为你创建世界「{raw}」... AI 正在生成世界观和故事，请稍候（约20-40秒）", stream_id)

        result = await self._ai_generate_world(raw)
        if not result["ok"]:
            await self.ctx.send.text(f"生成失败：{result.get('error', '未知错误')}，请重试。", stream_id)
            return True, None, True

        world_id = result["world_id"]
        world_name = result["world_name"]
        scene = result.get("starter_scene", "")

        await self.ctx.send.text(
            f"世界创建完成！\n\n"
            f"  {world_name}\n"
            f"  {result.get('description', '')}\n"
            f"  世界书条目：{result.get('lorebook_count', 0)}条\n\n"
            f"开场场景：\n{scene}\n\n"
            f"在群里发 /跑团开始 启动游戏！\n"
            f"让你的群友发 /跑团加入 开始创建角色。",
            stream_id)
        return True, None, True

    @Command("cmd_trpg_join", description="加入游戏并创建角色。用法: /跑团加入 [角色描述]",
             pattern=r"/跑团加入", chat_scope="group")
    async def cmd_join(self, stream_id: str = "", user_id: str = "",
                       group_id: str = "", platform: str = "qq",
                       text: str = "", **kwargs) -> tuple:
        account_id = kwargs.get("account_id", "bot_1")
        game_key = GameRegistry.make_game_key(platform, group_id, account_id)
        instance = self.registry.get(game_key)
        if not instance:
            await self.ctx.send.text("当前群没有进行中的游戏，请先 /跑团开始。", stream_id)
            return True, None, True
        if len(instance.players) >= instance.max_players:
            await self.ctx.send.text(f"游戏已满员（{instance.max_players}人）。", stream_id)
            return True, None, True
        if user_id in instance.players:
            existing = instance.players[user_id]
            await self.ctx.send.text(
                f"你已经加入了！当前角色：{existing.get('character_name', '?')}\n"
                f"如需重新建卡，请等待后续版本支持。",
                stream_id)
            return True, None, True

        raw = text.replace("/跑团加入", "").strip()

        if raw and self.llm_client:
            await self.ctx.send.text("正在为你生成角色...（约需10-20秒）", stream_id)
            char = await self._ai_generate_character(raw, instance)
            if char:
                instance.players[user_id] = {
                    "character_name": char["character_name"],
                    "character_sheet": char,
                }
                c = char
                skills = "、".join(c.get("skills", [])) or "无"
                eqs = "、".join(e.get("name", "") for e in c.get("equipment", [])) or "无"
                await self.ctx.send.text(
                    f"欢迎 {c['character_name']} 加入冒险！\n"
                    f"  {c.get('race','?')} {c.get('class','?')} Lv.{c.get('level',1)}\n"
                    f"  HP {c.get('hp','?')}/{c.get('max_hp','?')}\n"
                    f"  技能：{skills}\n"
                    f"  装备：{eqs}\n"
                    f"  背景：{c.get('background','无')}",
                    stream_id)
                return True, None, True

        # 默认角色
        nickname = kwargs.get("sender_nickname", f"冒险者_{user_id[-4:]}")
        instance.players[user_id] = {
            "character_name": nickname,
            "character_sheet": self._make_default_character(nickname, instance),
        }
        await self.ctx.send.text(
            f"欢迎 {nickname} 加入冒险！\n"
            f"  提示：使用 /跑团加入 [描述] 可以让 AI 生成一个更有个性的角色哦！",
            stream_id)
        return True, None, True

    @Command("cmd_trpg_newchar", description="创建/重投角色。用法: /跑团建卡 [角色名] 或 /跑团建卡 AI [描述]",
             pattern=r"/跑团建卡", chat_scope="group")
    async def cmd_newchar(self, stream_id: str = "", user_id: str = "",
                          group_id: str = "", platform: str = "qq",
                          text: str = "", **kwargs) -> tuple:
        account_id = kwargs.get("account_id", "bot_1")
        game_key = GameRegistry.make_game_key(platform, group_id, account_id)
        instance = self.registry.get(game_key)
        if not instance:
            await self.ctx.send.text("当前群没有进行中的游戏，请先 /跑团开始。", stream_id)
            return True, None, True
        if len(instance.players) >= instance.max_players and user_id not in instance.players:
            await self.ctx.send.text(f"游戏已满员（{instance.max_players}人）。", stream_id)
            return True, None, True

        raw = text.replace("/跑团建卡", "").strip()
        nickname = kwargs.get("sender_nickname", f"冒险者_{user_id[-4:]}")

        if raw.lower().startswith("ai") or raw.lower().startswith("AI"):
            ai_prompt = raw[2:].strip() or nickname
            if not self.llm_client:
                await self.ctx.send.text("LLM 未配置，无法 AI 生成角色。将使用默认角色。", stream_id)
            else:
                await self.ctx.send.text("正在为你生成角色...（约需10-20秒）", stream_id)
                char = await self._ai_generate_character(ai_prompt, instance)
                if char:
                    instance.players[user_id] = {
                        "character_name": char["character_name"],
                        "character_sheet": char,
                    }
                    skills = "、".join(s.get("name", str(s)) if isinstance(s, dict) else str(s) for s in char.get("skills", [])) or "无"
                    eqs = "、".join(e.get("name", "") for e in char.get("equipment", [])) or "无"
                    await self.ctx.send.text(
                        f"角色重建成功！{char['character_name']}\n"
                        f"  {char.get('race','?')} {char.get('class','?')} Lv.{char.get('level',1)}\n"
                        f"  HP {char.get('hp','?')}/{char.get('max_hp','?')}\n"
                        f"  技能：{skills}\n"
                        f"  装备：{eqs}",
                        stream_id)
                    return True, None, True
                await self.ctx.send.text("AI 生成失败，使用默认角色。", stream_id)

        # 使用指定名称或昵称创建角色
        name = raw if raw else nickname
        instance.players[user_id] = {
            "character_name": name,
            "character_sheet": self._make_default_character(name, instance),
        }
        word = "重建" if user_id in instance.players else "创建"
        await self.ctx.send.text(
            f"角色{word}成功！欢迎 {name} 加入冒险。\n"
            f"  提示：使用 /跑团建卡 AI [描述] 可以让 AI 生成角色哦！",
            stream_id)
        return True, None, True

    @Command("cmd_trpg_reroll", description="删除当前角色重新加入", pattern=r"/跑团重投", chat_scope="group")
    async def cmd_reroll(self, stream_id: str = "", user_id: str = "",
                         group_id: str = "", platform: str = "qq", **kwargs) -> tuple:
        account_id = kwargs.get("account_id", "bot_1")
        game_key = GameRegistry.make_game_key(platform, group_id, account_id)
        instance = self.registry.get(game_key)
        if not instance:
            await self.ctx.send.text("当前群没有进行中的游戏。", stream_id)
            return True, None, True
        if user_id not in instance.players:
            await self.ctx.send.text("你还没有加入游戏，请先 /跑团加入。", stream_id)
            return True, None, True
        name = instance.players[user_id].get("character_name", user_id)
        del instance.players[user_id]
        nickname = kwargs.get("sender_nickname", f"冒险者_{user_id[-4:]}")
        instance.players[user_id] = {
            "character_name": nickname,
            "character_sheet": self._make_default_character(nickname, instance),
        }
        await self.ctx.send.text(
            f"{name} 已重新投胎！新角色：{nickname}\n"
            f"  使用 /跑团建卡 [角色名] 自定义名字，或 /跑团建卡 AI [描述] 让 AI 生成。",
            stream_id)
        return True, None, True

    @Command("cmd_trpg_leave", description="退出当前游戏", pattern=r"/跑团退出", chat_scope="group")
    async def cmd_leave(self, stream_id: str = "", user_id: str = "",
                        group_id: str = "", platform: str = "qq", **kwargs) -> tuple:
        account_id = kwargs.get("account_id", "bot_1")
        game_key = GameRegistry.make_game_key(platform, group_id, account_id)
        instance = self.registry.get(game_key)
        if not instance:
            await self.ctx.send.text("当前群没有进行中的游戏。", stream_id)
            return True, None, True
        if user_id not in instance.players:
            await self.ctx.send.text("你还没有加入游戏。", stream_id)
            return True, None, True
        name = instance.players[user_id].get("character_name", user_id)
        await instance.remove_player(user_id)
        await self.ctx.send.text(f"{name} 退出了冒险。", stream_id)
        return True, None, True

    @Command("cmd_trpg_go", description="推进当前回合", pattern=r"^[!/]?(下一轮|go|推进)$", chat_scope="group")
    async def cmd_go(self, stream_id: str = "", user_id: str = "",
                     group_id: str = "", platform: str = "qq", **kwargs) -> tuple:
        account_id = kwargs.get("account_id", "bot_1")
        game_key = GameRegistry.make_game_key(platform, group_id, account_id)
        instance = self.registry.get(game_key)
        if not instance or not instance.can_accept_actions():
            return False, None, False

        if await instance.try_advance():
            narration, info_asym = await self.handler.process_round(instance)
            await self._send_segmented(narration, stream_id)
            if info_asym:
                await self._send_private_infos(info_asym, platform)
        return True, None, True

    @Command("cmd_trpg_reset", description="重置当前游戏为初始状态（保留世界观，清除进度）。用法: /跑团重置",
             pattern=r"^[!/]?(跑团重置|重置跑团)$", chat_scope="group")
    async def cmd_reset(self, stream_id: str = "", group_id: str = "",
                        platform: str = "qq", **kwargs) -> tuple:
        account_id = kwargs.get("account_id", "bot_1")
        game_key = GameRegistry.make_game_key(platform, group_id, account_id)
        instance = self.registry.get(game_key)
        if not instance:
            await self.ctx.send.text("当前群没有进行中的游戏。", stream_id)
            return True, None, True
        instance = await self.handler.reset_game(instance)
        narration = instance.log[-1].get("gm_response", "") if instance.log else ""
        await self.ctx.send.text(
            f"游戏已重置，种子码: {instance.seed_code}\n\n{narration}\n\n发 /跑团加入 创建角色！",
            stream_id)
        return True, None, True

    @Command("cmd_trpg_seed", description="查看当前游戏的种子码",
             pattern=r"^[!/]?(跑团种子|种子码|seed)$", chat_scope="group")
    async def cmd_seed(self, stream_id: str = "", group_id: str = "",
                       platform: str = "qq", **kwargs) -> tuple:
        account_id = kwargs.get("account_id", "bot_1")
        game_key = GameRegistry.make_game_key(platform, group_id, account_id)
        instance = self.registry.get(game_key)
        if not instance or not instance.seed_code:
            await self.ctx.send.text("当前群没有进行中的游戏或无种子码。", stream_id)
            return True, None, True
        await self.ctx.send.text(
            f"当前游戏种子码: {instance.seed_code}\n"
            f"下次可使用 /跑团开始 种子 {instance.seed_code} 重开相同世界。",
            stream_id)
        return True, None, True

    @Command("cmd_trpg_action", description="玩家行动声明（捕获群内非命令发言）",
             pattern=r"^(?![/!])(.+)$", chat_scope="group")
    async def cmd_action(self, stream_id: str = "", user_id: str = "",
                         group_id: str = "", platform: str = "qq",
                         text: str = "", matched_groups: dict = None, **kwargs) -> tuple:
        account_id = kwargs.get("account_id", "bot_1")
        game_key = GameRegistry.make_game_key(platform, group_id, account_id)
        instance = self.registry.get(game_key)
        if not instance or not instance.can_accept_actions():
            return False, None, False

        action_text = (matched_groups or {}).get("1", text)
        if not action_text or len(action_text) < 2:
            return False, None, False

        await instance.add_action(user_id, action_text)

        if await instance.try_advance():
            narration, info_asym = await self.handler.process_round(instance)
            await self.ctx.send.text(narration, stream_id)
            if info_asym:
                await self._send_private_infos(info_asym, platform)

        return True, None, True

    # ---- 辅助方法 ----

    def _find_game_by_seed(self, seed_code: str):
        for inst in self.registry.list_all():
            if inst.seed_code == seed_code:
                return inst
        return None

    def _make_default_character(self, name: str, instance=None) -> dict:
        rule_id = "freeform_fantasy"
        if instance and instance.world_id:
            try:
                base = Path(__file__).parent / "templates"
                world_path = base / "worlds" / f"{instance.world_id}.json"
                if world_path.exists():
                    world_data = json.loads(world_path.read_text(encoding="utf-8"))
                    rule_id = world_data.get("default_rule", rule_id)
            except Exception:
                pass
        return make_default_character(name, rule_id, Path(__file__).parent / "templates")

    async def _send_segmented(self, text: str, stream_id: str) -> None:
        """将长文本按段落分段发送，避免大段文字糊脸。"""
        parts = re.split(r'\n\n+', text.strip())
        if len(parts) <= 1:
            parts = re.split(r'(?<=[。！？])\s+', text.strip())
        if len(parts) <= 1:
            await self.ctx.send.text(text, stream_id)
            return
        for i, part in enumerate(parts):
            if not part.strip():
                continue
            await self.ctx.send.text(part.strip(), stream_id)
            if i < len(parts) - 1:
                await asyncio.sleep(0.8)

    async def _auto_resume(self, instance) -> None:
        """自动续接恢复的对局：生成续接叙事并发送到群聊。"""
        try:
            platform = instance.game_key[0]
            group_id = instance.game_key[1]
            stream_id = f"{platform}_group_{group_id}"
            resume_msg = await self.handler.resume_game(instance)
            if resume_msg:
                await self.ctx.send.text(
                    f"GM 已重新上线！\n\n{resume_msg}", stream_id)
        except Exception:
            logger.exception("自动续接失败: %s", instance.game_key)

    async def _periodic_save(self):
        """每 60 秒自动保存所有活跃对局，防崩溃丢档。"""
        while True:
            await asyncio.sleep(60)
            if self._sub:
                try:
                    await self._sub.registry.save_all_active()
                except Exception:
                    logger.exception("定时保存失败")

    async def _send_private_infos(self, info_asym: dict, platform: str = "qq") -> None:
        """向指定玩家发送仅自己可见的信息（信息不对称）。"""
        for user_id, private_msg in info_asym.items():
            if not private_msg:
                continue
            try:
                await self.ctx.send.text(
                    f"【你的角色感知】\n{private_msg}",
                    f"{platform}_private_{user_id}",
                )
                logger.info("私聊信息已发送: user=%s", user_id)
            except Exception:
                logger.warning("私聊信息发送失败: user=%s", user_id)

    async def _ai_generate_world(self, prompt: str) -> dict:
        """AI 生成世界模板，返回 {ok, world_id, world_name, description, starter_scene, lorebook_count}。"""
        try:
            return await generate_world(
                self.llm_client, prompt, rule_id="freeform_fantasy",
                worlds_dir=self._worlds_dir, lorebook_store=self.lorebook_store,
                max_tokens=self.config.character_gen_max_tokens,
            )
        except Exception as e:
            logger.exception("AI 生成世界失败")
            return {"ok": False, "error": str(e)}

    async def _ai_generate_character(self, prompt: str, instance=None) -> dict | None:
        """AI 生成角色卡。"""
        try:
            rule = None
            if instance and instance.world_id:
                from pathlib import Path as _Path
                from src.rules.rule_system import RuleSystem
                base = _Path(__file__).parent / "templates"
                world_path = base / "worlds" / f"{instance.world_id}.json"
                if world_path.exists():
                    import json as _json
                    world_data = _json.loads(world_path.read_text(encoding="utf-8"))
                    rules_dir = base / "rules"
                    rule = RuleSystem.load_for_world(world_data, rules_dir)
            return await generate_character(
                self.llm_client, prompt, rule=rule,
                max_tokens=self.config.character_gen_max_tokens,
            )
        except Exception as e:
            logger.exception("AI 生成角色失败")
            return None


# ---------- 工厂函数 ----------------------------------------

def create_plugin() -> TRPGPlugin:
    return TRPGPlugin()
