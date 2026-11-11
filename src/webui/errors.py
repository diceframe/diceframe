"""后端错误码映射与统一出口。

设计原则（见 ../计划方案/API错误消息国际化实现方案.md）：
- 后端存量错误消息保持中文原文（契约兼容），不逐处迁移；
- 本模块维护「高频错误文案 → error_code」映射表，由 error_code_middleware
  在响应出口统一补 error_code 字段，前端按 locale 映射展示；
- 请求的 Accept-Language 首选语言不是中文时，已登记错误码的 error 文案
  同时换成 src/localization/locales/errors.<语言>.yml 里的译文，没有内置
  错误码表的客户端（移动端、机器人、第三方）也能拿到对应语言；
- 未收录的错误不补码，前端回退显示 error 原文，保证任何错误都能正常显示。
"""

from __future__ import annotations

import json
import logging

from aiohttp import web

from src.engine.language import DEFAULT_LANGUAGE
from src.localization import messages

logger = logging.getLogger("trpg")

# 高频/核心错误文案 → 稳定错误码。新增错误文案时优先在此登记，
# 使前端能提供本地化文案；命名按模块前缀（game_/plugin_/character_/...）。
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


def _localized_error(code: str) -> str | None:
    """按请求语言给出错误码的文案；中文请求或未登记时返回 None（保留原文）。"""
    language = messages.request_language()
    if not language or language == DEFAULT_LANGUAGE:
        return None
    key = f"errors.{code}"
    return messages.t(key) if messages.has(key) else None


@web.middleware
async def error_code_middleware(request: web.Request, handler) -> web.StreamResponse:
    """响应出口：为 4xx/5xx JSON 错误响应补充 error_code 字段。

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
    declared = str(data.get("error_code") or "")
    code = declared or ERROR_CODE_MAP.get(str(data["error"]))
    if not code:
        return response
    localized = _localized_error(code)
    if declared and localized is None:
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
