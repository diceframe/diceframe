"""SSE 流式路由 handler：游戏流 / 行动流 / 分用户播放。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from aiohttp import web

from src.engine.game_instance import GameState
from src.webui.connection_pool import ConnectionPool
from src.webui.routes._common import MAX_ACTION_CHARS, _get_api

logger = logging.getLogger("trpg")


async def sse_stream(request: web.Request) -> web.StreamResponse:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"error": "not found"}, status=404)

    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    await response.prepare(request)

    seen_rounds = inst.round_number
    max_idle = 60
    try:
        for _ in range(max_idle * 2):
            current = request.app["subsystems"].registry.get(api._parse_key(gk))
            if not current:
                await response.write(b"event: end\ndata: game_ended\n\n")
                break
            if current.round_number > seen_rounds:
                seen_rounds = current.round_number
                last_log = current.log[-1] if current.log else {}
                gm_text = last_log.get("gm_response", "")
                if gm_text:
                    data = json.dumps({"narration": gm_text, "round": seen_rounds}, ensure_ascii=False)
                    await response.write(f"data: {data}\n\n".encode())
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        return response


async def sse_stream_action(request: web.Request) -> web.StreamResponse:
    api = _get_api(request)
    gk = request.match_info["game_key"]
    body = await request.json()
    text = body.get("text", "")
    if len(text) > MAX_ACTION_CHARS:
        return web.json_response({"error": f"行动文本过长（上限 {MAX_ACTION_CHARS} 字）"}, status=400)
    selected_attribute = str(body.get("selected_attribute", "") or "")
    selected_skill = str(body.get("selected_skill", "") or "")
    target_text = str(body.get("target_text", "") or "")

    inst = request.app["subsystems"].registry.get(api._parse_key(gk))
    if not inst:
        return web.json_response({"error": "not found"}, status=404)
    user_id = request.get("user_id", "")
    if not user_id or user_id not in inst.players:
        return web.json_response({"error": "未加入本局，无法提交行动"}, status=403)
    if inst.is_dead(user_id):
        return web.json_response({"error": "角色已死亡，无法提交行动"}, status=403)
    if inst.state == GameState.ACTIVE_JUDGMENT:
        return web.json_response({"error": "本轮正在推进剧情，请等待下一轮开始"}, status=409)

    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    await response.prepare(request)

    try:
        await response.write(b"event: start\ndata: processing\n\n")

        if inst.state == GameState.PAUSED:
            if inst.round_number <= 0:
                await inst.start_round()
            else:
                await inst.resume()

        await inst.add_action(user_id, text, selected_attribute, selected_skill, target_text)
        handler = request.app["subsystems"].handler
        if await inst.try_advance():
            narration, _ = await handler.process_round(inst)
            parts = narration.split("\n\n") if narration else [""]
            for i, part in enumerate(parts):
                if not part.strip():
                    continue
                data = json.dumps({"narration": part.strip(), "index": i, "total": len(parts)}, ensure_ascii=False)
                await response.write(f"data: {data}\n\n".encode())
            check_result = getattr(inst, "last_check", None)
            if check_result:
                await response.write(f"data: {json.dumps({'check_result': check_result}, ensure_ascii=False)}\n\n".encode())
            recap = getattr(inst, "last_state_update", None)
            if recap:
                await response.write(f"data: {json.dumps({'recap': recap}, ensure_ascii=False)}\n\n".encode())
        else:
            data = json.dumps({"narration": "(已记录，等待推进)", "waiting": True}, ensure_ascii=False)
            await response.write(f"data: {data}\n\n".encode())

        await response.write(b"event: done\ndata: complete\n\n")
    except Exception as e:
        logger.exception("SSE 流处理异常")
        error_data = json.dumps({"error": "处理出错，请查看服务器日志"}, ensure_ascii=False)
        await response.write(f"event: error\ndata: {error_data}\n\n".encode())
    finally:
        return response


async def sse_play(request: web.Request) -> web.StreamResponse:
    """分用户 SSE 推送：叙事 + 私聊 + 状态。"""
    game_key = request.match_info["game_key"]
    user_id = request.get("user_id", "")
    pool: ConnectionPool = request.app["connection_pool"]
    subsystems = request.app["subsystems"]
    api = _get_api(request)
    inst = subsystems.registry.get(api._parse_key(game_key))
    if not inst:
        raise web.HTTPNotFound()
    if not user_id or user_id not in inst.players:
        return web.json_response({"error": "未加入本局，无法订阅"}, status=403)

    resp = web.StreamResponse(
        headers={"Content-Type": "text/event-stream",
                 "Cache-Control": "no-cache",
                 "Connection": "keep-alive"})
    await resp.prepare(request)
    pool.add(game_key, user_id, resp)

    cursor = _parse_event_cursor(request.headers.get("Last-Event-ID", ""))
    last_round = cursor[0]
    last_private_count = cursor[1]
    last_action_signature = ""
    last_player_count = len(inst.players)
    try:
        while True:
            current = subsystems.registry.get(api._parse_key(game_key))
            if not current:
                break
            inst = current
            if inst.round_number > last_round:
                last_round = inst.round_number
                log_last = inst.log[-1] if inst.log else {}
                await _write_play_event(resp, last_round, last_private_count, last_action_signature, {'type':'narration','round':last_round,'text':log_last.get('gm_response','')})
                # 状态
                cs = inst.get_character_sheet(user_id)
                await _write_play_event(resp, last_round, last_private_count, last_action_signature, {'type':'state','hp':cs.get('hp'),'max_hp':cs.get('max_hp'),'gold':cs.get('gold'),'deceased':cs.get('deceased')})
            action_signature = json.dumps(
                [
                    (a.get("user_id", ""), a.get("text", ""), a.get("timestamp", ""))
                    for a in inst.action_queue
                ],
                ensure_ascii=False,
            )
            if action_signature != last_action_signature:
                last_action_signature = action_signature
                await _write_play_event(resp, last_round, last_private_count, last_action_signature, {'type':'public_actions'})
            if len(inst.players) != last_player_count:
                last_player_count = len(inst.players)
                await _write_play_event(resp, last_round, last_private_count, last_action_signature, {'type':'players'})
            priv = inst.private_log.get(user_id, [])
            if len(priv) > last_private_count:
                for p in priv[last_private_count:]:
                    last_private_count += 1
                    await _write_play_event(resp, last_round, last_private_count, last_action_signature, {'type':'private','text':p.get('text','')})
                last_private_count = len(priv)
            await asyncio.sleep(0.5)
    except ConnectionResetError:
        pass
    finally:
        pool.remove(game_key, user_id, resp)
    return resp


def _event_cursor(round_number: int, private_count: int, action_signature: str) -> str:
    digest = hashlib.sha1(action_signature.encode("utf-8")).hexdigest()[:10] if action_signature else "0"
    return f"r{round_number}.p{private_count}.a{digest}"


def _parse_event_cursor(value: str) -> tuple[int, int]:
    try:
        parts = str(value).split(".")
        return max(0, int(parts[0][1:])), max(0, int(parts[1][1:]))
    except (IndexError, ValueError):
        return 0, 0


async def _write_play_event(resp: web.StreamResponse, round_number: int, private_count: int,
                            action_signature: str, payload: dict) -> None:
    event_id = _event_cursor(round_number, private_count, action_signature)
    data = json.dumps(payload, ensure_ascii=False)
    await resp.write(f"id: {event_id}\ndata: {data}\n\n".encode())


def register_sse(app: web.Application) -> None:
    app.router.add_get("/api/games/{game_key}/stream", sse_stream)
    app.router.add_post("/api/games/{game_key}/stream-action", sse_stream_action)
    app.router.add_get("/api/games/{game_key}/sse", sse_play)
