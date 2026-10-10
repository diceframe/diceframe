"""Ruleset-neutral public narration perspective policy.

The persisted value is a presentation preference. It applies to every
ruleset and never changes canonical identity or mechanics.
"""

from __future__ import annotations

from typing import Any

from src.engine.language import localized_text
from src.engine.modules import table_settings

NARRATIVE_PERSPECTIVE_AUTO = "auto"
NARRATIVE_PERSPECTIVE_IMMERSIVE = "immersive"
NARRATIVE_PERSPECTIVE_THIRD_PERSON = "third_person"
NARRATIVE_PERSPECTIVES = frozenset({
    NARRATIVE_PERSPECTIVE_AUTO,
    NARRATIVE_PERSPECTIVE_IMMERSIVE,
    NARRATIVE_PERSPECTIVE_THIRD_PERSON,
})


def validate_narrative_perspective(value: Any) -> str:
    """Return a canonical value or reject invalid user input."""

    normalized = str(value or NARRATIVE_PERSPECTIVE_AUTO).strip().casefold()
    if normalized not in NARRATIVE_PERSPECTIVES:
        raise ValueError("叙事视角必须是 auto、immersive 或 third_person")
    return normalized


def normalize_narrative_perspective(value: Any) -> str:
    """Return a supported canonical value, preserving old saves as ``auto``."""

    try:
        return validate_narrative_perspective(value)
    except ValueError:
        return NARRATIVE_PERSPECTIVE_AUTO


def resolve_narrative_perspective(instance: Any) -> str:
    """Resolve ``auto`` against the current session mode, including live switches."""

    configured = normalize_narrative_perspective(
        table_settings.narrative_perspective(instance),
    )
    if configured != NARRATIVE_PERSPECTIVE_AUTO:
        return configured
    return (
        NARRATIVE_PERSPECTIVE_IMMERSIVE
        if bool(table_settings.solo_mode(instance))
        else NARRATIVE_PERSPECTIVE_THIRD_PERSON
    )


def narrative_perspective_instruction(instance: Any, language: str) -> str:
    """Build the single ruleset-neutral narration instruction."""

    perspective = resolve_narrative_perspective(instance)
    if perspective == NARRATIVE_PERSPECTIVE_IMMERSIVE:
        return localized_text(language, {
            "en": (
                "## Narrative perspective\n"
                "Use an immersive second-person viewpoint for the focal acting character (‘you’). "
                "Name every other party member in third person. If one public response covers multiple "
                "players, name the new focal character before changing focus so ‘you’ is never ambiguous. "
                "First person is allowed only inside quoted character dialogue. Keep the viewpoint consistent."
            ),
            "zh-CN": (
                "## 叙事视角\n"
                "采用沉浸式第二人称：当前焦点行动角色写作“你”，其他队伍成员用角色名第三人称。"
                "若一条公共回复同时处理多名玩家，切换焦点前必须先点明角色名，不能让“你”的指向含糊。"
                "第一人称只允许出现在角色引号发言中；全文保持视角一致。"
            ),
            "ja": (
                "## 語りの視点\n"
                "焦点となる行動キャラクターには没入型の二人称（あなた）を使い、他の仲間はキャラクター名の"
                "三人称で描写する。ひとつの公開応答で複数人を扱う場合、焦点を切り替える前に名前を明示し、"
                "二人称の指示先を曖昧にしない。一人称は引用符内の台詞だけに使い、視点を統一する。"
            ),
            "de": (
                "## Erzählperspektive\n"
                "Verwende eine immersive zweite Person ('du') für die aktuell handelnde Fokusfigur. "
                "Nenne jedes andere Gruppenmitglied in der dritten Person beim Namen. Wenn eine öffentliche "
                "Antwort mehrere Spieler abdeckt, nenne die neue Fokusfigur beim Namen, bevor der Fokus "
                "wechselt, damit 'du' nie mehrdeutig ist. Erste Person ist nur innerhalb zitierter "
                "Charakterdialoge erlaubt. Behalte die Perspektive durchgehend konsistent bei."
            ),
        })
    return localized_text(language, {
        "en": (
            "## Narrative perspective\n"
            "Use third person and each player character's exact display name in all public narration. "
            "Never use ‘you’ for a specific player character. First person is allowed only inside quoted "
            "character dialogue. Keep the viewpoint consistent across every paragraph."
        ),
        "zh-CN": (
            "## 叙事视角\n"
            "所有公共叙事统一使用每名玩家角色的准确显示名作第三人称，不要把某一名玩家角色写成“你”。"
            "第一人称只允许出现在角色引号发言中；每一段都保持这个视角一致。"
        ),
        "ja": (
            "## 語りの視点\n"
            "公開叙事では各プレイヤーキャラクターの正確な表示名を使う三人称に統一し、特定のキャラクターを"
            "二人称で呼ばない。一人称は引用符内の台詞だけに使い、全段落で視点を統一する。"
        ),
        "de": (
            "## Erzählperspektive\n"
            "Verwende in der gesamten öffentlichen Erzählung die dritte Person mit dem genauen Anzeigenamen "
            "jeder Spielfigur. Verwende niemals 'du' für eine bestimmte Spielfigur. Erste Person ist nur "
            "innerhalb zitierter Charakterdialoge erlaubt. Behalte die Perspektive in jedem Absatz konsistent bei."
        ),
    })
