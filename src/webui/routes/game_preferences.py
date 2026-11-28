"""Thin GM-only transport. Shared defaults remain owner configuration."""
from aiohttp import web

from src.webui.routes._common import _get_api
from src.webui.routes.game_route_common import _narration_callbacks
from src.webui.viewer import viewer_for
from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY


async def preferences(request: web.Request) -> web.Response:
    api, key = _get_api(request), request.match_info['game_key']
    inst = api.get_game_instance(key)
    if inst is None:
        return web.json_response({'ok': False, 'error': 'Game not found'}, status=404)
    viewer = viewer_for(request, inst)
    if not viewer.is_gm or request.get('player_preview'):
        return web.json_response({'ok': False, 'error': 'GM only', "error_code": "gm_only"}, status=403)
    can_manage_defaults = bool(request.get('owner_authenticated', False)
                               or request.get(ACCESS_PASSWORD_CONFIGURED_KEY) is False)
    if request.method == 'GET':
        result = api.game_preferences(key, viewer, include_defaults=can_manage_defaults)
        result['can_save_defaults'] = can_manage_defaults
        if not result['can_save_defaults']:
            result.pop('defaults', None)
        return web.json_response(result, status=200 if result.get('ok') else 422)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = None
    if not isinstance(body, dict) or body.keys() - {'preferences', 'apply_defaults'}:
        return web.json_response({'ok': False, 'error': 'Invalid preferences request'}, status=400)
    if body.get('apply_defaults') is True and 'preferences' not in body:
        if not can_manage_defaults:
            return web.json_response({'ok': False, 'error': 'Owner only'}, status=403)
        current = api.game_preferences(key, viewer, include_defaults=True)
        if not current.get('ok'):
            return web.json_response(current, status=422)
        values = current['defaults']
    elif 'preferences' in body and 'apply_defaults' not in body:
        values = body['preferences']
    else:
        return web.json_response({'ok': False, 'error': 'Choose preferences or apply_defaults'}, status=400)
    on_delta, on_reset = _narration_callbacks(request, key)
    result = await api.update_game_preferences(key, viewer, values, on_delta=on_delta, on_reset=on_reset)
    pool = request.app.get('connection_pool')
    if pool and result['payload'].get('ok'):
        await pool.broadcast(key, {'type': 'table_settings_changed'})
    return web.json_response(result['payload'], status=result['status'])


def register(app: web.Application) -> None:
    app.router.add_get('/api/games/{game_key}/preferences', preferences)
    app.router.add_post('/api/games/{game_key}/preferences', preferences)
