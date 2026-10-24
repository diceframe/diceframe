"""Scene-transition narration for confirmed adventure activations (GAL P6).

One short narration when the engine confirms a narrator-proposed node
completion: it bridges the story into the newly activated scene (or the
ending, whose scene context already instructs the narrator to close the
story). Narration-only by design — the narrator gets no state write access
here; every state change already happened inside the completion
transaction, and re-settling narrative tags would double-apply them.
"""

from __future__ import annotations

import logging
from typing import Any

from src.engine.language import DEFAULT_LANGUAGE, localized_text
from src.llm.parser import sanitize_narration

logger = logging.getLogger("trpg")

MAX_TRANSITION_CHARS = 1200
DEFAULT_MAX_TOKENS = 768


def _transition_instruction(language: str, completed_node_id: str) -> str:
    return localized_text(language, {
        "zh-CN": (
            f"【场景过渡】故事刚刚完成了剧情节点「{completed_node_id}」。"
            "请写一段简短的场景过渡叙事（3-6 句），把故事自然地带入下面给出的"
            "当前场景。只做过渡描写：不要替玩家角色做决定，不要开启新的支线，"
            "不要输出任何标签或协议文本。"
        ),
        "en": (
            f"[Scene transition] The story has just concluded the node "
            f"'{completed_node_id}'. Write a short transition narration (3-6 "
            "sentences) that carries the story naturally into the current scene "
            "below. Transition only: do not decide for the player characters, do "
            "not open new branches, and do not output any tags or protocol text."
        ),
        "ja": (
            f"【シーン転換】物語はノート「{completed_node_id}」を終えたばかりです。"
            "短い転換ナレーション（3〜6文）を書き、以下の現在のシーンへ自然に"
            "導いてください。転換の描写のみ：プレイヤーキャラクターの代わりに"
            "決定せず、新しい枝を開かず、タグやプロトコル文本を出力しないで"
            "ください。"
        ),
        "de": (
            f"[Szenenübergang] Die Geschichte hat soeben den Knoten "
            f"'{completed_node_id}' abgeschlossen. Schreibe eine kurze "
            "Übergangserzählung (3-6 Sätze), die die Geschichte natürlich in die "
            "unten angegebene aktuelle Szene überführt. Nur Übergang: triff keine "
            "Entscheidungen für die Spielfiguren, öffne keine neuen Zweige und "
            "gib keine Tags oder Protokolltexte aus."
        ),
    })


class SceneTransitionNarrator:
    """Generates the transition narration for a confirmed node completion."""

    def __init__(
        self,
        llm_client: Any,
        prompt_composer: Any,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.llm_client = llm_client
        self.prompt_composer = prompt_composer
        self.max_tokens = max_tokens

    async def narrate(
        self,
        instance: Any,
        *,
        completed_node_id: str,
    ) -> str:
        """Return the transition text, or ``""`` when generation fails."""

        if self.llm_client is None or self.prompt_composer is None:
            return ""
        language = str(getattr(instance, "language", "") or DEFAULT_LANGUAGE)
        # 完成事务已提交：场景上下文此刻呈现的是新激活的节点（或结局的收束指令）。
        scene_block = self.prompt_composer.scene_context_text(instance)
        system_prompt = self.prompt_composer.load_gm_prompt(language=language)
        context = _transition_instruction(language, completed_node_id)
        if scene_block:
            context += "\n\n" + scene_block
        response = await self.llm_client.call(
            system_prompt,
            context,
            temperature=0.5,
            max_tokens=self.max_tokens,
        )
        text = sanitize_narration(
            str(response.narration or response.content or ""),
        ).strip()
        if len(text) > MAX_TRANSITION_CHARS:
            text = text[:MAX_TRANSITION_CHARS].rstrip() + "…"
        return text
