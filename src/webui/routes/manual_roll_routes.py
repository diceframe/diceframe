from aiohttp import web
from src.webui.routes._common import _get_api
from src.webui.routes.game_route_common import _gm_only_inst
from src.localization.messages import t

async def _broadcast_change(request, game_key):
    pool = request.app.get("connection_pool")
    if pool is not None:
        await pool.broadcast(game_key, {"type": "manual_roll_requests_changed"})

async def api_manual_roll_list(request):
    api=_get_api(request); inst=api.get_game_instance(request.match_info["game_key"])
    uid=str(request.get("user_id") or (inst.gm_uid if inst and request.get("owner_authenticated") else ""))
    items=api.manual_roll_requests(request.match_info["game_key"],uid)
    if items is None: return web.json_response({"ok":False,"error":"游戏不存在"},status=404)
    return web.json_response({"ok":True,"run_id":inst.run_id,"requests":items})
async def api_manual_roll_create(request):
    api=_get_api(request); _,err=_gm_only_inst(request,request.match_info["game_key"])
    if err:return err
    inst=api.get_game_instance(request.match_info["game_key"]); uid=request.get("user_id") or (inst.gm_uid if inst else "")
    out=await api.create_manual_roll_request(request.match_info["game_key"],uid,await request.json())
    if out.get("ok"): await _broadcast_change(request, request.match_info["game_key"])
    return web.json_response(out,status=200 if out.get("ok") else 400)
async def api_manual_roll_resolve(request):
    api=_get_api(request); body=await request.json()
    if request.get("player_preview") and not request.get("player_delegate"):
        return web.json_response({"ok":False,"error":t("webui.routes.manual_roll_routes.preview_requires_proxy_permission")},status=403)
    inst = api.get_game_instance(request.match_info["game_key"])
    uid = request.get("user_id") or (inst.gm_uid if inst and request.get("owner_authenticated") else "")
    out=await api.resolve_manual_roll_request(request.match_info["game_key"],uid,request.match_info["request_id"],body)
    if out.get("ok"): await _broadcast_change(request, request.match_info["game_key"])
    return web.json_response(out,status=200 if out.get("ok") else 403)
async def api_manual_roll_cancel(request):
    api=_get_api(request); _,err=_gm_only_inst(request,request.match_info["game_key"])
    if err:return err
    inst=api.get_game_instance(request.match_info["game_key"]); uid=request.get("user_id") or (inst.gm_uid if inst else "")
    out=await api.cancel_manual_roll_request(request.match_info["game_key"],uid,request.match_info["request_id"],await request.json())
    if out.get("ok"): await _broadcast_change(request, request.match_info["game_key"])
    return web.json_response(out,status=200 if out.get("ok") else 400)
