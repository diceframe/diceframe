"""稳定错误码与统一出口。

**错误码在抛出点声明，不由文案反推。** `src/webui/errors.py` 曾经维护一张
「中文文案 → error_code」映射表；那张表是过渡方案，只保留冻结的存量条目
（`ERROR_CODE_MAP`，只减不增）。原因是它可以同时错两次：

- 已经迁到 `t()` 的服务按**请求语言**渲染文案，中文键再也匹配不上，于是
  社区语言（ja/de/ru）请求既拿不到 code、又拿到英文回退文案；
- 改一句中文措辞就会静默断码。

新错误请用 :func:`error_payload`：::

    return error_payload("webui.services.worlds.name_empty")
    return error_payload("webui.services.worlds.entry_too_long", max_lorebook_chars=N)

code 由目录键派生（稳定、与文案无关），也可以用 ``code=`` 显式指定语义 code。
前端按 ``apiErrors.<code>`` 渲染，社区译文只登记在那里；后端
``src/localization/locales/webui/errors.<语言>.yml`` 只服务没有前端目录的客户端。

出口 ``error_code_middleware`` 的职责收窄为：已声明的 code 原样保留，能在
``webui/errors.<语言>.yml`` 找到译文时用译文替换 ``error``；找不到就保留抛出点
渲染的文案。它不再从 ``error`` 文本猜 code。
"""

from __future__ import annotations

import json
import logging
import re

from aiohttp import web

from src.engine.language import DEFAULT_LANGUAGE
from src.localization import messages
from src.localization.messages import t

logger = logging.getLogger("trpg")

#: 历史遗留的 UPPER_SNAKE code，仍在抛出点声明。新 code 一律 snake_case。
LEGACY_DECLARED_CODES: frozenset[str] = frozenset({
    "ACCESS_PASSWORD_CHANGED",
    "ACTION_NOT_FOUND",
    "ACTION_REJECTED",
    "ACTOR_DEFEATED",
    "ACTOR_FORBIDDEN",
    "ACTOR_NOT_FOUND",
    "ADVANCEMENT_NOT_GRANTED",
    "ADVANCEMENT_NOT_READY",
    "ADVENTURE_CATALOG_INVALID",
    "ADVENTURE_NODE_FAILED",
    "ADVENTURE_NODE_REJECTED",
    "ADVENTURE_RUNTIME_INIT_FAILED",
    "ADVENTURE_RUNTIME_UNAVAILABLE",
    "AGENT_FAILED",
    "AGENT_REJECTED",
    "ALREADY_RESOLVED",
    "ASSISTANT_FAILED",
    "AUTOMATIC_PLAN_STALE",
    "AUTOMATIC_TURN_FAILED",
    "AWAY_POLICY_UNSUPPORTED",
    "BOOK_BINDING_FAILED",
    "BOOK_BINDING_UNAVAILABLE",
    "BOOK_NOT_FOUND",
    "BOT_ACTOR_INVALID",
    "BOT_READ_ONLY",
    "BOT_SURFACE_DISABLED",
    "BOT_SURFACE_UNSUPPORTED",
    "BOT_TOKEN_REVOKED",
    "CARD_NOT_AVAILABLE",
    "CARRY_DELIVERY_FAILED",
    "CHARACTER_NOT_FOUND",
    "CHARACTER_SHEET_INVALID",
    "CHECK_NOT_FOUND",
    "COMBAT_EXTENSION_NOT_CONFIGURED",
    "COMBAT_NOT_ACTIVE",
    "COMBAT_STATE_UNSUPPORTED",
    "CONTENT_NOT_FOUND",
    "CONTENT_UNAVAILABLE",
    "CONTENT_VALIDATION_FAILED",
    "CONTROL_MODE_UNSUPPORTED",
    "DECISIONS_INVALID",
    "ECONOMY_DECISION_PENDING",
    "EFFECT_COMMIT_FAILED",
    "EMBEDDING_SETTINGS_CHANGED",
    "EMPTY_ANSWER",
    "EMPTY_QUESTION",
    "FORBIDDEN",
    "GAMEPLAY_ACTION_UNAVAILABLE",
    "GAME_CHANGED",
    "GAME_CREATE_FAILED",
    "GAME_NOT_FOUND",
    "GAME_PROCESSING",
    "GM_ONLY",
    "GM_SEAT_REQUIRES_OWNER",
    "GM_SEAT_UNCLAIMED",
    "IMPORT_PLAN_STALE",
    "IMPORT_UNRESOLVED_REFERENCES",
    "INCOMPATIBLE_ADVENTURE",
    "INCOMPATIBLE_RULESET_CHARACTER",
    "INSTALLMENT_ALREADY_STARTED",
    "INSTALL_ID_INVALID",
    "INSUFFICIENT_FUNDS",
    "INTENT_ID_CONFLICT",
    "INTENT_RECORD_INVALID",
    "INVALID_ACTION_ID",
    "INVALID_ACTOR_ID",
    "INVALID_ADVANCEMENT",
    "INVALID_ADVANCEMENT_ACTION",
    "INVALID_ADVANCEMENT_MODE",
    "INVALID_ADVANCEMENT_POLICY",
    "INVALID_ADVENTURE_BINDING",
    "INVALID_ADVENTURE_COMMAND",
    "INVALID_ADVENTURE_NODE",
    "INVALID_AMOUNT",
    "INVALID_BOOK_BINDINGS",
    "INVALID_CAMPAIGN_INTENT",
    "INVALID_CHARACTER_REVISION",
    "INVALID_CHOICE_REPAIR",
    "INVALID_DRAFT",
    "INVALID_EVENT_BATCH",
    "INVALID_INTENT",
    "INVALID_INTENT_ID",
    "INVALID_JSON",
    "INVALID_OPERATION_ID",
    "INVALID_PAYMENT_TERMS",
    "INVALID_PLAYER_CONTROL",
    "INVALID_PORTRAIT",
    "INVALID_PREFERENCES",
    "INVALID_PROGRESSION",
    "INVALID_REPAIRS",
    "INVALID_REST",
    "INVALID_RULE_TEMPLATE",
    "INVALID_SPEAKER_ID",
    "INVALID_SPEAKER_MERGE",
    "INVALID_STORY_MODE",
    "INVALID_TARGETS",
    "INVALID_VIEWER",
    "INVALID_VISIBILITY",
    "INVALID_WORLD_REF",
    "INVALID_XP_REWARD",
    "ITEM_MISSING",
    "LEGACY_PIPELINE_REQUIRED",
    "LLM_NOT_CONFIGURED",
    "LLM_REQUEST_FAILED",
    "LOCAL_EMBEDDING_RUNTIME_UNAVAILABLE",
    "LUCK_ALREADY_RESOLVED",
    "LUCK_FORBIDDEN",
    "LUCK_INSUFFICIENT",
    "LUCK_NOT_AVAILABLE",
    "LUCK_NOT_PENDING",
    "MEMBER_REQUIRED",
    "MODULE_IN_USE",
    "MODULE_MARKETPLACE_UNAVAILABLE",
    "MODULE_NOT_FOUND",
    "OPERATION_ID_REUSED",
    "ORDER_INVALID",
    "OWNER_REQUIRED",
    "PAIRED_DEVICE_REVOKED",
    "PAIRING_CODE_INVALID",
    "PAYER_NOT_FOUND",
    "PLAN_DIGEST_REQUIRED",
    "PLAYER_ACCESS_CLOSED",
    "PLAYER_NOT_IN_GAME",
    "PLUGIN_TOKEN_REVOKED",
    "PREVIEW_MODE_FORBIDDEN",
    "PREVIEW_READ_ONLY",
    "PROPOSAL_NOT_FOUND",
    "PUSH_FORBIDDEN",
    "PUSH_INVALID_OR_STALE",
    "PUSH_INVALID_REQUEST",
    "PUSH_NOT_PENDING",
    "QUESTION_TOO_LONG",
    "RECIPIENT_NOT_FOUND",
    "REQUEST_TOO_LARGE",
    "RESUME_FAILED",
    "REWARD_UNSUPPORTED",
    "REWRITE_IN_PROGRESS",
    "ROOM_TOKEN_INVALID",
    "ROUND_NOT_ACCEPTING_ACTIONS",
    "ROUND_NOT_PROCESSED",
    "ROUND_PROCESSING",
    "ROUND_PROCESSING_BUSY",
    "ROUND_PROCESSING_FAILED",
    "ROUND_ROLLBACK_FAILED",
    "RULESET_ADVANCEMENT_UNAVAILABLE",
    "RULESET_BINDING_FAILED",
    "RULESET_BINDING_MISSING",
    "RULESET_BUILDER_UNAVAILABLE",
    "RULESET_CHARACTER_OPERATION_REQUIRED",
    "RULESET_REST_UNAVAILABLE",
    "RULESET_RUNTIME_UNAVAILABLE",
    "RULE_NOT_FOUND",
    "RUN_ENDED",
    "RUN_READ_ONLY",
    "SAVE_FAILED",
    "SCHEDULER_NOT_READY",
    "SCHEDULER_REJECTED",
    "SCHEDULER_STATE_INVALID",
    "SEAT_NOT_BOUND",
    "SEAT_TOKEN_EXISTS",
    "SEAT_TOKEN_INVALID",
    "SEAT_TOKEN_REQUIRED",
    "STALE_CHARACTER_REVISION",
    "STALE_GUARD",
    "STALE_OR_UNSETTLED_RUN",
    "STALE_RUN",
    "STORY_MODE_LOCKED",
    "SUGGESTION_NOT_FOUND",
    "TABLE_TALK_SAVE_FAILED",
    "TARGET_NOT_FOUND",
    "UNKNOWN_SPEAKER",
    "UNKNOWN_TARGET",
    "UNSUPPORTED_GAMEPLAY",
    "UNSUPPORTED_KIND",
    "UNSUPPORTED_MEDIA_SCHEMA",
    "UNSUPPORTED_PREFERENCES",
    "UNSUPPORTED_RUN_STATE",
    "UNSUPPORTED_STORYTELLING_SCHEMA",
    "UPDATE_VALIDATING",
    "WORLD_NOT_FOUND",
})

_SNAKE_CODE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_TRANSPORT_PREFIX = {"services": "", "routes": "route_"}

# 冻结：存量中文文案 → 错误码。**只减不增。**
# 新错误请在抛出点用 error_payload 声明 code；这张表只为还没迁移的调用点保留
# 兼容，它的 key 是文案，所以任何措辞调整都会让条目失效。
ERROR_CODE_MAP: dict[str, str] = {
    # 游戏
    "游戏不存在": "game_not_found",
    "游戏不存在，请刷新页面重新开始": "game_not_found",
    "游戏不存在或存档目录不存在": "game_not_found",
    "未加入本局": "player_not_in_game",
    "未加入本局，无法订阅": "player_not_in_game",
    "未加入本局，无法提交行动": "player_not_in_game",
    "未加入本局，请先通过邀请链接加入": "player_not_in_game",
    "本轮正在推进剧情，请等待下一轮开始": "round_processing",
    "本轮行动已修改 3 次，请等待其他玩家或 GM 推进": "action_revision_limit",
    "角色已死亡，无法提交行动": "character_deceased",
    "没有可回滚的上一轮": "no_previous_round",
    "仅 GM 可强制推进": "gm_only",
    "仅 GM 可导出游戏": "gm_only",
    "仅 GM 可删除游戏": "gm_only",
    "仅 GM 可删除": "gm_only",
    "GM only": "gm_only",
    "仅 GM 可修改 NPC 头像": "gm_only",
    "该游戏未设置房间密码": "room_password_not_set",
    "房间密码错误": "room_password_wrong",
    "当前游戏需要密码": "room_password_required",
    "需要房间密码": "room_password_required",
    "房间密码至少 6 位": "room_password_too_short",
    "房间密码已更改，请重新输入": "room_password_changed",
    "房间密码不能只包含空白字符": "room_password_blank",
    "游戏名过长（上限 40 字）": "game_name_too_long",
    "世界描述过长（上限 2000 字）": "world_description_too_long",
    "行动文本过长（上限 500 字）": "action_text_too_long",
    "当前是房主预览模式，请先开启允许代操作": "preview_mode_forbidden",
    "本局玩家入口已关闭": "player_access_closed",
    "请至少保留一个角色，无法删除最后一个": "cannot_delete_last_character",
    # 孤注一掷
    "只能由该检定的行动者本人处理孤注一掷，主持人只能告知风险或拒绝申请": "push_forbidden",
    "孤注一掷请求格式无效": "push_invalid_request",
    "检定选择已变化，请刷新后重试": "push_invalid_or_stale",
    # 角色
    "角色不存在": "character_not_found",
    "玩家不存在": "player_not_found",
    "目标玩家不存在": "player_not_found",
    "无权修改他人角色卡": "forbidden_edit_character",
    "无权删除他人角色": "forbidden_delete_character",
    "角色背景过长（上限 8000 字）": "background_too_long",
    "该角色当前并未死亡": "character_not_dead",
    # 插件
    "插件宿主未启用": "plugin_host_disabled",
    "插件内容不存在或未启用": "plugin_content_unavailable",
    "该插件没有可管理的角色卡": "plugin_no_character_cards",
    "卡片解析路径非法": "plugin_invalid_card_path",
    "支持包不存在": "plugin_package_not_found",
    "支持包已存在": "plugin_package_exists",
    "请选择要导入的内容包": "plugin_content_not_selected",
    "请填写内容包 ID 再导入": "plugin_content_id_required",
    # 存档
    "存档目录不存在": "save_dir_not_found",
    "存档文件不存在": "save_file_not_found",
    "存档包不能超过 50 MB": "save_package_too_large",
    "缺少存档 zip 文件": "save_zip_missing",
    "存档上传需要 multipart/form-data": "save_multipart_required",
    "未登录，无法删除存档": "save_delete_unauthorized",
    "存档缺少 GM 身份，拒绝删除": "save_missing_gm_identity",
    "未指定合法 world_id": "invalid_world_id",
    "非法 source_world_id": "invalid_world_id",
    "未指定合法的 world_id": "invalid_world_id",
    "目标世界书不存在": "lorebook_not_found",
    "世界不存在": "world_not_found",
    "视角无效": "invalid_viewer",
    # 系统
    "系统未启动": "system_not_started",
    "修正值不能为 0": "delta_zero",
    "请输入 GM 指令": "gm_command_empty",
    "seconds 必须是整数（秒，0=禁用）": "invalid_seconds",
}

#: 冻结的存量错误码（来自上面的映射表）。新增错误码必须走 error_payload。
FROZEN_ERROR_CODES = frozenset(ERROR_CODE_MAP.values())


def error_code_for_key(key: str) -> str:
    """Stable code derived from a catalog key — never from its rendered text.

    ``webui.services.worlds.name_empty`` → ``worlds_name_empty``;
    ``webui.routes.plugins.too_large`` → ``route_plugins_too_large``. The prefix
    keeps a service and a route module of the same name from colliding.
    """

    parts = str(key or "").strip().split(".")
    if len(parts) < 3 or parts[0] != "webui" or parts[1] not in _TRANSPORT_PREFIX:
        raise ValueError(
            "an error code is derived from a webui services/routes catalog key, got "
            f"{key!r}"
        )
    assembled = (_TRANSPORT_PREFIX[parts[1]] + "_".join(parts[2:])).replace("_common_", "common_")
    code = re.sub(r"[^a-z0-9]+", "_", assembled.casefold()).strip("_")
    if not code:
        raise ValueError(f"catalog key {key!r} yields an empty error code")
    return code


def validate_error_code(code: str) -> str:
    """Reject a code that is neither snake_case nor a frozen legacy spelling."""

    text = str(code or "")
    if _SNAKE_CODE.match(text) or text in LEGACY_DECLARED_CODES:
        return text
    raise ValueError(
        f"invalid error code {code!r}: new codes must be snake_case "
        "(a-z0-9_) and start with a letter"
    )


def declared_error_code(result: object) -> str:
    """The error code of a service/orchestrator result, in either spelling.

    Service payloads moved to `error_code` with `error_payload`; older engine
    results still carry `code`. Route status must not depend on which one a
    given module happens to use, otherwise a 409 silently becomes a 400 —
    read codes through here instead of `result.get("code")`.
    """

    if not isinstance(result, dict):
        return ""
    for key in ("error_code", "code"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def error_fields(key: str, /, *, code: str | None = None, **params: object) -> dict:
    """``{"error_code", "error"}`` only — for bodies that must keep their shape.

    Route handlers that answer with a bare ``{"error": ...}`` object use this
    so adding a code does not also add an ``ok`` field.
    """

    resolved = validate_error_code(code or error_code_for_key(key))
    return {"error_code": resolved, "error": t(key, **params)}


def error_payload(key: str, /, *, code: str | None = None, **params: object) -> dict:
    """An error result that carries its stable code.

    The text is rendered here so callers that display the dict directly (bot,
    CLI, P2P payloads) still have it; the HTTP exit may replace it with the
    ``webui/errors.<language>.yml`` wording when one exists. The code is what
    the frontend and third-party clients translate from.
    """

    return {"ok": False, **error_fields(key, code=code, **params)}


def _localized_error(code: str) -> str | None:
    """按请求语言给出错误码的文案；中文请求或未登记时返回 None（保留原文）。"""
    language = messages.request_language()
    if not language or language == DEFAULT_LANGUAGE:
        return None
    key = f"webui.errors.{code}"
    return messages.t(key) if messages.has(key) else None


@web.middleware
async def error_code_middleware(request: web.Request, handler) -> web.StreamResponse:
    """响应出口：保留已声明的 error_code，按请求语言补齐/替换 error 文案。

    职责刻意收窄：

    - 处理函数已经写了 ``error_code``（``error_payload`` 或显式声明）：
      原样保留；能在 ``webui/errors.<语言>.yml`` 找到同码译文就用译文替换
      ``error``，否则保留抛出点渲染的文案。
    - 没有 ``error_code``：只对冻结的 ``ERROR_CODE_MAP`` 存量中文文案补码；
      匹配不上就不补，前端回退显示原文。

    不解析 SSE/文件等非 JSON 响应；响应体不可读时直接放行。
    """
    response = await handler(request)
    if response.status < 400:
        return response
    if response.content_type != "application/json":
        return response
    try:
        body = response.body
    except Exception:
        return response
    if not body:
        return response
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return response
    if not isinstance(data, dict) or not data.get("error"):
        return response
    # `code` is the spelling the orchestration layer has always used internally
    # (`{"ok": False, "code": "GM_ONLY", ...}`). Clients only ever read
    # `error_code`, so a payload whose code lives under `code` is invisible to
    # `apiErrors.<code>` and to bot/CLI branching. Promote it here rather than
    # depending on every module finishing its migration; the raise-site rule
    # (and the ratchet in scripts/audit_error_codes.py) still applies to new
    # code, and where both keys exist they must agree.
    declared = str(data.get("error_code") or "")
    inherited = str(data.get("code") or "")
    code = declared or inherited or ERROR_CODE_MAP.get(str(data["error"]))
    if not code:
        return response
    localized = _localized_error(code)
    # 已用规范键声明且没有同码译文：载荷本来就是这样，不必重写响应体。
    # （只有 `code` 的不算：必须经过下面的提升。）
    if localized is None and declared:
        return response
    data["error_code"] = code
    if localized is not None:
        data["error"] = localized
        # 响应内容随协商语言变化：告诉缓存，也告诉客户端实际用的语言。
        response.headers["Content-Language"] = messages.request_language()
        vary = [part.strip() for part in response.headers.get("Vary", "").split(",") if part.strip()]
        if not any(part.lower() == "accept-language" for part in vary):
            response.headers["Vary"] = ", ".join([*vary, "Accept-Language"])
    try:
        response.body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        response.headers["Content-Length"] = str(len(response.body))
    except Exception:
        logger.debug("错误码注入失败: %s", response.status, exc_info=True)
    return response
