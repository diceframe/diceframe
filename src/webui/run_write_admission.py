"""Transport admission for frozen/ended runs, including legacy direct writers."""
from aiohttp import web

from src.engine.game_state import GameState
from src.localization.messages import t

GAME = '/api/games/{game_key}'

# Restoring who you are, delivering or revoking a seat credential, and deleting
# your own memory data never rewrite settled characters or story, so they stay
# available on a frozen or ended run (otherwise an owner on a new device could
# never get the GM view of their archive back).
FREEZE_EXEMPT = frozenset({
    ('POST', f'{GAME}/claim-gm'),
    ('POST', f'{GAME}/seat-token/claim'),
    ('POST', f'{GAME}/players/{{uid}}/seat-token'),
    ('DELETE', f'{GAME}/memories/{{entry_id}}'),
    # Ticket issuance authorizes a role-filtered read stream; it never mutates
    # game state. Frozen archives must remain subscribable after reconnect.
    ('POST', f'{GAME}/sse-ticket'),
})

# Slow model/media calls. They either do not mutate the run (speech,
# transcription, prompt preview) or commit through ``authoritative_write()``
# themselves after the slow part, which re-checks the run. They are admitted
# here but must not hold the run authority for the whole request: that would
# queue every other writer at the table behind an image or a model answer.
SELF_GATED = frozenset({
    ('POST', f'{GAME}/kp-question'),
    ('POST', f'{GAME}/story-recap'),
    ('POST', f'{GAME}/speech'),
    ('POST', f'{GAME}/transcription'),
    ('POST', f'{GAME}/generated-images'),
    ('POST', f'{GAME}/generated-images/current-round'),
    ('POST', f'{GAME}/generated-images/prompt/preview'),
    ('POST', f'{GAME}/generated-images/storyboard/analyze'),
})


def read_only_response(instance) -> web.Response:
    return web.json_response({'ok': False, 'error_code': 'RUN_ENDED' if instance.state == GameState.ENDED else 'RUN_READ_ONLY',
                              'error': t("webui_run_write_admission.run_read_only")}, status=409)


@web.middleware
async def run_write_admission(request, handler):
    key = request.match_info.get('game_key')
    api = request.app.get('api')
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'} or not key or api is None:
        return await handler(request)
    route = request.match_info.route.resource.canonical if request.match_info.route.resource else ''
    # The feature service owns its privileged freeze lifecycle and permissions.
    if route.startswith(f'{GAME}/run-story'):
        return await handler(request)
    if (request.method, route) in FREEZE_EXEMPT:
        return await handler(request)
    instance = api.get_game_instance(key)
    if instance is None:
        return await handler(request)
    if (request.method, route) in SELF_GATED:
        async with instance.authoritative_write() as entered:
            pass
        return await handler(request) if entered else read_only_response(instance)
    # Remaining legacy direct writers stay serialized for the whole request.
    # No reset/restart exception: preserve the original archive and create a
    # separate game to carry characters.
    async with instance.authoritative_write() as entered:
        if not entered:
            return read_only_response(instance)
        return await handler(request)
