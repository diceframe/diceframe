"""Adapt model story output to the existing plain narration/state-tag contract."""
from __future__ import annotations

import logging
from typing import Any

from src.engine.language import normalize_language
from src.engine.story import StoryFormatError, parse_story, public_story, story_text, trim_story, read_story_mode
from src.llm.parser import normalize_tag_protocol, sanitize_narration

logger = logging.getLogger("trpg")

# Hard ceiling for a whole dialogue-mode story, in ``story_text`` characters
# including speaker names. It only stops runaway output: it sits far above the
# regular length the prompt asks for (3-8 beats), so a normal reply is never
# cut. Chinese and Japanese count characters; English, German and Russian are
# sized by word count (about 600 words). Unlisted languages fall back to the
# Chinese value.
STORY_LIMITS = {"zh-CN": 1500, "ja": 1500, "en": 3600, "de": 3600, "ru": 3600}


def story_limit(language: Any) -> int:
    return STORY_LIMITS.get(normalize_language(language), STORY_LIMITS["zh-CN"])


def decode_story_response(instance: Any, response: Any, *, strict: bool = False,
                          round_number: int | None = None, revision: int = 0,
                          history: list[dict] | None = None) -> None:
    story = parse_story(instance, response.narration, strict=strict,
                        round_number=round_number, revision=revision, history=history)
    story = public_story(instance, story)
    if story is None:
        raise StoryFormatError("Model returned no usable public story content")
    response.story = story
    response.narration = story_text(story)
    _replace_public_body(response)


def bound_story_response(response: Any, data: dict, *, limit: int) -> bool:
    """Bound an overlong story on whole beats; rounds, swipes and openings share it."""
    if len(response.narration) <= limit:
        return False
    total = len(response.story.get("segments", []))
    response.story = trim_story(response.story, limit)
    response.narration = story_text(response.story)
    # Options referring to an omitted story beat must not survive truncation.
    data["quick_actions"] = []
    _replace_public_body(response)
    logger.warning(
        "对话叙事超出长度上限，已按整段截断: 保留 %d/%d 段 (上限 %d 字)",
        len(response.story["segments"]), total, limit,
    )
    return True


def _replace_public_body(response: Any) -> None:
    content = str(response.content or "")
    suffix = "\n---" + content.split("---", 1)[1] if "---" in content else ""
    response.content = response.narration + suffix


async def ensure_story_response(instance: Any, response: Any, llm_client: Any, *,
                                system_prompt: str, user_message: str, max_tokens: int,
                                round_number: int, revision: int = 0,
                                history: list[dict] | None = None) -> Any:
    """Opening/swipe use the same bounded format repair as ordinary rounds."""
    if read_story_mode(instance) != "dialogue":
        return response
    for attempt in range(2):
        response.content = normalize_tag_protocol(response.content)
        response.narration = sanitize_narration(str(response.content).split("---", 1)[0])
        try:
            decode_story_response(instance, response, strict=attempt == 0,
                                  round_number=round_number, revision=revision, history=history)
            return response
        except StoryFormatError:
            if attempt:
                raise
            previous_tokens = int(getattr(response, "total_tokens", 0) or 0)
            response = await llm_client.call(
                system_prompt=system_prompt,
                user_message=user_message + "\n\nRewrite the public body as narration/dialogue NDJSON lines; "
                    "use roster IDs or new:local_handle. Keep the required --- state tags after it.",
                temperature=0.7, max_tokens=max_tokens,
            )
            response.total_tokens += previous_tokens
    return response
