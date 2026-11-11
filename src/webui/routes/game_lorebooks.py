"""Owner-only catalogue for deterministic game-source selection."""
from aiohttp import web
from src.webui.routes._common import _get_api
from src.localization.messages import t


async def api_game_lorebooks(request):
    if not request.get('owner_authenticated') or not request.get('user_id'):
        return web.json_response({'ok': False, 'error': t("webui.routes.game_lorebooks.host_only")}, status=403)
    try:
        return web.json_response(_get_api(request).game_lorebook_selection(request['user_id'], request.match_info['game_key']))
    except PermissionError as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=403)
    except ValueError as exc:
        return web.json_response({'ok': False, 'error': str(exc)}, status=400)
