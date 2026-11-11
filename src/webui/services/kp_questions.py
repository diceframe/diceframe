"""Application service for read-only player questions to the AI GM."""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol, TypedDict
from uuid import uuid4

from src.engine.modules import progression_state
from src.engine.modules.private_channels import remove_table_talk_exchange
from src.localization.messages import t

logger = logging.getLogger("trpg")
GameKey = tuple[str, ...]


class KPQuestionRegistry(Protocol):
    def get(self, game_key: GameKey) -> Any | None: ...
    async def save(self, instance: Any) -> None: ...


KPAnswerer = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class KPQuestionDependencies:
    registry: KPQuestionRegistry
    parse_game_key: Callable[[str], GameKey]
    answer_question: KPAnswerer | None


class KPQuestionResult(TypedDict):
    payload: dict[str, Any]
    status: int


def _result(payload: dict[str, Any], status: int = 200) -> KPQuestionResult:
    return {"payload": payload, "status": status}


async def ask(
    dependencies: KPQuestionDependencies,
    game_key: str,
    actor_uid: str,
    question: str,
    visibility: Literal["private", "party"] = "private",
) -> KPQuestionResult:
    """Answer table talk outside the turn pipeline; persist only party exchanges."""
    parsed_key = dependencies.parse_game_key(game_key)
    instance = dependencies.registry.get(parsed_key)
    if not instance:
        return _result({
            "ok": False,
            "code": "GAME_NOT_FOUND",
            "error": t("webui.services.kp_questions.game_missing_refresh"),
        }, 404)
    if actor_uid not in instance.players:
        return _result({
            "ok": False,
            "code": "PLAYER_NOT_IN_GAME",
            "error": t("webui.services.kp_questions.claim_character_first"),
        }, 403)
    question = str(question or "").strip()
    if not question:
        return _result({
            "ok": False,
            "code": "EMPTY_QUESTION",
            "error": t("webui.services.kp_questions.question_required"),
        }, 400)
    if visibility not in {"private", "party"}:
        return _result({
            "ok": False,
            "code": "INVALID_VISIBILITY",
            "error": t("webui.services.kp_questions.visibility_invalid"),
        }, 400)
    answerer = dependencies.answer_question
    if answerer is None:
        return _result({
            "ok": False,
            "code": "LLM_NOT_CONFIGURED",
            "error": t("webui.services.kp_questions.kp_model_unconfigured"),
        }, 503)

    if instance._process_lock.locked():
        return _result({
            "ok": False,
            "code": "GAME_PROCESSING",
            "error": t("webui.services.kp_questions.game_advancing"),
        }, 409)

    # Capture one coherent, immutable view while holding the per-game lock.
    # The model request runs after releasing it so Q&A never blocks a round.
    async with instance._process_lock:
        snapshot = instance.__class__.from_dict(copy.deepcopy(instance.to_dict()))

    try:
        generated = await answerer(
            snapshot, actor_uid, question, visibility=visibility,
        )
    except Exception:
        logger.exception("KP 桌外询问生成失败: game=%s actor=%s", game_key, actor_uid)
        return _result({
            "ok": False,
            "code": "LLM_REQUEST_FAILED",
            "error": t("webui.services.kp_questions.kp_unavailable"),
        }, 502)

    answer = str((generated or {}).get("answer") or "").strip()
    if not answer:
        return _result({
            "ok": False,
            "code": "EMPTY_ANSWER",
            "error": t("webui.services.kp_questions.kp_empty_answer"),
        }, 502)
    if dependencies.registry.get(parsed_key) is not instance:
        return _result({
            "ok": False,
            "code": "GAME_CHANGED",
            "error": t("webui.services.kp_questions.game_replaced_during_question"),
        }, 409)
    exchange: dict[str, Any] | None = None
    if visibility == "party":
        actor = snapshot.players.get(actor_uid) or {}
        exchange = {
            "id": uuid4().hex,
            "actor_uid": actor_uid,
            "actor_name": str(actor.get("character_name") or actor_uid),
            "question": question,
            "answer": answer,
            "round": progression_state.round_value(snapshot),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "visibility": "party",
        }
        # The LLM call stays outside the lock. Only the small append/save step is
        # serialized, so table talk cannot block or enter the turn pipeline.
        try:
            async with instance.authoritative_write() as entered, instance._process_lock:
                if not entered:
                    return _result({
                        "ok": False,
                        "code": "RUN_READ_ONLY",
                        "error": t("webui.services.kp_questions.run_read_only"),
                    }, 409)
                instance.append_table_talk(exchange)
                try:
                    await dependencies.registry.save(instance)
                except Exception:
                    remove_table_talk_exchange(instance, exchange["id"])
                    raise
        except Exception:
            logger.exception("公开桌边问答保存失败: game=%s actor=%s", game_key, actor_uid)
            return _result({
                "ok": False,
                "code": "TABLE_TALK_SAVE_FAILED",
                "error": t("webui.services.kp_questions.public_answer_save_failed"),
            }, 500)

    return _result({
        "ok": True,
        "kind": "kp_table_talk",
        "answer": answer,
        "visibility": visibility,
        "exchange": exchange,
        "advanced": False,
        "action_consumed": False,
        "round_number": progression_state.round_value(snapshot),
        "provider_used": str((generated or {}).get("provider_used") or ""),
        "total_tokens": int((generated or {}).get("total_tokens", 0) or 0),
    })


class KPQuestionService:
    """Read-only KP questions with optional, transactional party persistence."""

    def __init__(self, dependencies: KPQuestionDependencies) -> None:
        self._dependencies = dependencies

    async def ask(
        self,
        game_key: str,
        actor_uid: str,
        question: str,
        visibility: Literal["private", "party"] = "private",
    ) -> KPQuestionResult:
        return await ask(
            self._dependencies, game_key, actor_uid, question, visibility,
        )
