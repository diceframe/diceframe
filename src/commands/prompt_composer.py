"""GM prompt 与上下文构造器。

集中处理基础 GM prompt、规则附录、剧情追踪文本和 context_builder 调用，
避免 process_round / generate_swipe 重复拼装。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from src.compat.callbacks import load_world_template as load_world_template_compat
from src.content.gm_style import render_gm_style_section
from src.engine.game_instance import GameInstance
from src.engine.language import DEFAULT_LANGUAGE, gm_language_instruction, localized_text
from src.engine.narrative_perspective import narrative_perspective_instruction
from src.llm.context_builder import (
    build_context,
    build_player_safe_context as build_player_safe_llm_context,
)
from src.memory.delta import MemoryStore
from src.rules.loader import RuleBundleLoader
from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import NarrativeAdvancementRuntime
from src.rulesets.registry import RulesetRuntimeRegistry

logger = logging.getLogger("trpg")
_GM_PROMPT_CACHE: dict[str, str] | None = {}


@dataclass
class RulePromptContext:
    """当前世界规则对 prompt / 判定流程的影响。"""

    world_data: dict | None = None
    rule: RuleSystem | None = None
    rule_appendix: str = ""
    combat_model: str = "hp_based"
    dice_system: str = "d20"


class PromptComposer:
    """构造 GM prompt 和 LLM user context。"""

    def __init__(
        self,
        prompts_dir: Path,
        rules_dir: Path,
        memory_store: MemoryStore | None = None,
        ruleset_registry: RulesetRuntimeRegistry | None = None,
    ):
        self.prompts_dir = prompts_dir
        self.rules_dir = rules_dir
        self.memory_store = memory_store
        self.ruleset_registry = ruleset_registry

    def _runtime(self, instance: GameInstance):
        binding = dict(getattr(instance, "ruleset_runtime", {}) or {})
        runtime_id = str(binding.get("id") or "")
        if self.ruleset_registry is None or not runtime_id:
            return None
        return self.ruleset_registry.get(
            runtime_id, minimum_version=int(binding.get("version", 1) or 1),
        )

    def load_gm_prompt(self, rule_appendix: str = "", language: str = DEFAULT_LANGUAGE) -> str:
        """读取基础 GM prompt，并按需附加当前规则说明。"""
        global _GM_PROMPT_CACHE
        if _GM_PROMPT_CACHE is None:
            _GM_PROMPT_CACHE = {}
        cache_key = localized_text(language, {"en": "en", "zh-CN": "zh-CN", "ja": "ja"})
        if cache_key not in _GM_PROMPT_CACHE:
            filename = (
                "gm_system_en.md" if cache_key == "en"
                else ("gm_system_ja.md" if cache_key == "ja" else "gm_system_zh.md")
            )
            path = self.prompts_dir / filename
            if path.exists():
                _GM_PROMPT_CACHE[cache_key] = path.read_text(encoding="utf-8")
            elif cache_key == "en":
                zh = self.prompts_dir / "gm_system_zh.md"
                _GM_PROMPT_CACHE[cache_key] = (
                    zh.read_text(encoding="utf-8")
                    if zh.exists()
                    else "You are the GM for a TRPG text adventure. Narrate in natural English. The GM prompt file is missing."
                )
            else:
                # ja 等非 en 语言的 prompt 缺失时先回退英文文件，再回退中文。
                # 输出语言仍由 gm_language_instruction 单独控制，不受此回退影响。
                en = self.prompts_dir / "gm_system_en.md"
                zh = self.prompts_dir / "gm_system_zh.md"
                if en.exists():
                    _GM_PROMPT_CACHE[cache_key] = en.read_text(encoding="utf-8")
                elif zh.exists():
                    _GM_PROMPT_CACHE[cache_key] = zh.read_text(encoding="utf-8")
                else:
                    _GM_PROMPT_CACHE[cache_key] = "你是 TRPG 游戏的主持人（GM）。请用流畅中文进行叙述。（GM prompt 文件缺失）"
        prompt = _GM_PROMPT_CACHE[cache_key]
        if rule_appendix:
            heading = localized_text(cache_key, {"en": "## Current Rules", "zh-CN": "## 当前规则", "ja": "## 現在のルール"})
            prompt += f"\n\n{heading}\n{rule_appendix}"
        return prompt

    def load_rule_context(
        self,
        instance: GameInstance,
        load_world_template: Callable[[str, str], dict],
    ) -> RulePromptContext:
        """加载存档实际选择的规则，并构造规则 prompt 附录。

        ``instance.rule_id`` 是开档时已经确定的权威规则。世界模板里的
        ``default_rule`` 只负责创建页默认选择，不能在运行时把用户选择的
        D&D/CoC 规则悄悄换回世界默认规则。
        """
        return self._load_rule_context(instance, load_world_template)

    def _load_rule_context(
        self,
        instance: GameInstance,
        load_world_template: Callable[[str, str], dict],
    ) -> RulePromptContext:
        ctx = RulePromptContext()
        if not instance.world_id:
            return ctx
        try:
            language = getattr(instance, "language", DEFAULT_LANGUAGE)
            world_data = load_world_template_compat(load_world_template, instance.world_id, language)
            ctx.world_data = world_data
            if world_data:
                rule = None
                active_rule_id = str(getattr(instance, "rule_id", "") or "").strip()
                if active_rule_id:
                    core = self.rules_dir / f"{active_rule_id}.json"
                    if core.exists():
                        rule = RuleSystem(RuleBundleLoader().load_rule(self.rules_dir, active_rule_id, language))
                    else:
                        active_path = RuleSystem.path_for(self.rules_dir, active_rule_id, language)
                        if active_path.exists():
                            rule = RuleSystem.load(active_path)
                # 插件规则不一定在内置 rules_dir；缺失时仍由世界贡献路径兜底。
                if rule is None:
                    rule = RuleSystem.load_for_world(world_data, self.rules_dir, language)
                if rule:
                    ctx.rule = rule
                    ctx.rule_appendix = rule.get_gm_prompt_appendix(language)
                    ctx.combat_model = rule.combat_model
                    ctx.dice_system = rule.dice_system
                    difficulty_text = rule.get_difficulty_instructions(instance.difficulty, language)
                    if difficulty_text:
                        ctx.rule_appendix = ctx.rule_appendix + "\n\n" + difficulty_text
                    stat_appendix = rule.resource_tag_appendix(language)
                    if stat_appendix:
                        ctx.rule_appendix = ctx.rule_appendix + "\n\n" + stat_appendix
        except ValueError:
            raise
        except Exception:
            logger.warning("规则上下文加载失败，回退默认值: world_id=%s", instance.world_id, exc_info=True)
        return ctx

    def load_swipe_rule_context(
        self,
        instance: GameInstance,
        load_world_template: Callable[[str, str], dict],
    ) -> RulePromptContext:
        """swipe 与正常回合必须使用同一套存档规则。"""
        return self._load_rule_context(instance, load_world_template)

    def compose_gm_prompt(
        self,
        instance: GameInstance,
        rule_appendix: str = "",
        world_data: dict | None = None,
    ) -> str:
        """构造系统 prompt：基础 prompt + 规则附录 + 世界 GM 风格 + 剧情追踪 + 多人权限范围。"""
        language = getattr(instance, "language", DEFAULT_LANGUAGE)
        gm_prompt = self.load_gm_prompt(rule_appendix, language)
        # 有效风格二选一：对局覆盖非 None 时只渲染覆盖，否则渲染世界 gm_style。
        style_section = render_gm_style_section(
            world_data,
            language,
            override=getattr(instance, "gm_style_override", None),
        )
        if style_section:
            gm_prompt = gm_prompt + "\n\n" + style_section
        plot_text = instance.plot_tracker.format_for_context() if instance.plot_tracker else ""
        if plot_text:
            gm_prompt = gm_prompt + "\n\n" + plot_text
        if len(getattr(instance, "players", {}) or {}) > 1:
            gm_prompt = gm_prompt + "\n\n" + localized_text(language, {
                "en": (
                    "## Multiplayer Authority Scope\n"
                    "Each player line is attributed to a named speaker. A speaker may only act, speak, and "
                    "perceive as their own character. Declarations that move, speak for, or change other "
                    "players' characters are converted into attempts and the other characters' reactions; "
                    "never treat one player's text as authority over another player's character."
                ),
                "zh-CN": (
                    "## 多人权限范围\n"
                    "每条玩家发言都归属具名说话人；说话人只能以自己的角色行动、说话、感知。"
                    "支配、替言或修改其他玩家角色的声明，一律转化为尝试与其他角色的反应，"
                    "不得把任一玩家的文本当作对其他玩家角色的权威。"
                ),
                "ja": (
                    "## マルチプレイヤー権限範囲\n"
                    "各プレイヤー発言は実名の話し手に帰属する。話し手は自分のキャラクターとしてのみ"
                    "行動・発言・知覚できる。他プレイヤーのキャラクターを操作・代弁・変更する宣言は、"
                    "全て試みと他キャラクターの反応に変換すること。あるプレイヤーのテキストを"
                    "他プレイヤーのキャラクターへの権威として扱ってはならない。"
                ),
            })
        gm_prompt = gm_prompt + "\n\n" + narrative_perspective_instruction(instance, language)
        runtime = self._runtime(instance)
        if isinstance(runtime, NarrativeAdvancementRuntime):
            advancement_prompt = runtime.narrative_advancement_prompt(instance, language)
            if advancement_prompt:
                gm_prompt = gm_prompt + "\n\n" + advancement_prompt
        gm_prompt = gm_prompt + "\n\n" + gm_language_instruction(getattr(instance, "language", "zh-CN"))
        return gm_prompt

    async def build_user_context(
        self,
        instance: GameInstance,
        gm_prompt: str,
        lorebook_matches: list[dict],
        actions_text: str,
        provider_name: str = "",
        world_data: dict | None = None,
        history_override: list[dict] | None = None,
        directives_text: str = "",
        overreach_text: str = "",
        authoritative_events_text: str = "",
    ) -> str:
        """调用 context_builder 生成本轮 user context。"""
        state_view = None
        runtime = self._runtime(instance)
        if runtime is not None:
            state_view = runtime.build_llm_view(instance)
        return await build_context(
            instance,
            gm_prompt,
            lorebook_matches,
            actions_text,
            memory_store=self.memory_store,
            provider_name=provider_name,
            lorebook_budget=world_data.get("lorebook_token_budget", 0) if world_data else 0,
            history_override=history_override,
            directives_text=directives_text,
            overreach_text=overreach_text,
            state_view=state_view,
            authoritative_events_text=authoritative_events_text,
        )

    async def build_player_safe_context(
        self,
        instance: GameInstance,
        gm_prompt: str,
        lorebook_matches: list[dict],
        player_message: str,
        actor_uid: str,
        provider_name: str = "",
        world_data: dict | None = None,
        visibility: Literal["private", "party"] = "private",
    ) -> str:
        """Build the restricted context used only by player-facing GM Q&A."""
        return await build_player_safe_llm_context(
            instance,
            gm_prompt,
            lorebook_matches,
            player_message,
            actor_uid,
            provider_name=provider_name,
            lorebook_budget=world_data.get("lorebook_token_budget", 0) if world_data else 0,
            visibility=visibility,
        )
