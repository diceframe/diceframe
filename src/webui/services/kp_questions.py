"""Application service for read-only player questions to the AI GM."""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol, TypedDict
from uuid import uuid4

from src.engine.modules.private_channels import remove_table_talk_exchange

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
            "error": "游戏不存在，请刷新后重试",
        }, 404)
    if actor_uid not in instance.players:
        return _result({
            "ok": False,
            "code": "PLAYER_NOT_IN_GAME",
            "error": "请先认领本局角色，再向 KP 询问",
        }, 403)
    question = str(question or "").strip()
    if not question:
        return _result({
            "ok": False,
            "code": "EMPTY_QUESTION",
            "error": "请输入要询问 KP 的问题",
        }, 400)
    if visibility not in {"private", "party"}:
        return _result({
            "ok": False,
            "code": "INVALID_VISIBILITY",
            "error": "询问可见范围无效",
        }, 400)
    answerer = dependencies.answer_question
    if answerer is None:
        return _result({
            "ok": False,
            "code": "LLM_NOT_CONFIGURED",
            "error": "KP 模型尚未配置或不可用",
        }, 503)

    if instance._process_lock.locked():
        return _result({
            "ok": False,
            "code": "GAME_PROCESSING",
            "error": "游戏正在推进，请稍后再问",
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
            "error": "KP 暂时无法回答，请稍后重试",
        }, 502)

    answer = str((generated or {}).get("answer") or "").strip()
    if not answer:
        return _result({
            "ok": False,
            "code": "EMPTY_ANSWER",
            "error": "KP 没有返回可显示的回答，请重试",
        }, 502)
    if dependencies.registry.get(parsed_key) is not instance:
        return _result({
            "ok": False,
            "code": "GAME_CHANGED",
            "error": "询问期间游戏已被替换或删除，请刷新后重试",
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
            "round": snapshot.round_number,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "visibility": "party",
        }
        # The LLM call stays outside the lock. Only the small append/save step is
        # serialized, so table talk cannot block or enter the turn pipeline.
        try:
            async with instance._process_lock:
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
                "error": "公开回答暂时无法保存，请稍后重试",
            }, 500)

    return _result({
        "ok": True,
        "kind": "kp_table_talk",
        "answer": answer,
        "visibility": visibility,
        "exchange": exchange,
        "advanced": False,
        "action_consumed": False,
        "round_number": snapshot.round_number,
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
