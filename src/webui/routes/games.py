"""Compatibility facade and stable registration order for game routes."""

from __future__ import annotations

from aiohttp import web

from src.engine.game_instance import MAX_SAVE_PACKAGE_BYTES

from src.webui.routes.game_route_common import (
    _broadcast_ruleset_change,
    _narration_callbacks,
    _gm_only_inst,
    _should_rebind_player_session,
    _can_delete_save,
)
from src.webui.routes.game_query_routes import (
    api_games,
    api_detail,
    api_game_scene_image_file,
    api_game_scene_image_update,
    api_chars,
    api_log,
    api_player_context,
)
from src.webui.routes.game_control_routes import (
    api_claim_gm_session,
    api_multiplayer_status,
    _health_allowed,
    _system_log_allowed,
    api_game_health,
    api_mark_health_event,
    api_set_solo_mode,
    api_set_narrative_perspective,
    api_set_luck_timeout,
    api_set_player_away,
    api_set_player_access,
    api_set_room_password,
    api_private_log,
    api_table_talk,
    api_verify_room_password,
)
from src.webui.routes.game_gameplay_routes import (
    api_gm_command,
    api_rollback,
    api_story_recap,
    api_gm_private_message,
    api_action,
    api_kp_question,
    _ruleset_gameplay_status,
    _ruleset_requester_is_gm,
    api_ruleset_available_actions,
    api_ruleset_submit_intent,
    api_ruleset_resolve_decision,
    api_luck_decision,
    api_advance,
    api_payment_create,
    api_payment_resolve,
    api_swipe,
)
from src.webui.routes.game_character_routes import (
    api_char_update,
    api_ruleset_character_profile_update,
    api_ruleset_character_adopt_card,
    _api_live_character_advancement,
    api_live_character_advancement_preview,
    api_live_character_advancement_apply,
    api_live_advancement_control,
    api_live_character_rest,
    api_char_delete,
    api_npc_portrait_update,
    api_player_create,
)
from src.webui.routes.game_package_routes import (
    _SavePackageTooLarge,
    _read_save_upload as _read_save_upload_impl,
    api_export_game,
    api_import_game,
    api_batch_delete_games,
    api_delete_game,
)
from src.webui.routes.game_lifecycle_routes import (
    api_create_game,
    api_reset_game,
    api_restart_game,
    api_switch_world,
    api_create_from_seed,
)


async def _read_save_upload(reader) -> bytes:
    """Compatibility wrapper preserving the patchable legacy size constant."""

    return await _read_save_upload_impl(
        reader,
        max_bytes=MAX_SAVE_PACKAGE_BYTES,
    )


def register_games(app: web.Application) -> None:
    app.router.add_get("/api/games", api_games)
    app.router.add_post("/api/games/import", api_import_game)
    app.router.add_get("/api/games/{game_key}", api_detail)
    app.router.add_get("/api/games/{game_key}/scene-image", api_game_scene_image_file)
    app.router.add_post(
        "/api/games/{game_key}/scene-image", api_game_scene_image_update
    )
    app.router.add_post("/api/games/{game_key}/claim-gm", api_claim_gm_session)
    app.router.add_get("/api/games/{game_key}/multiplayer", api_multiplayer_status)
    app.router.add_get("/api/games/{game_key}/player-context", api_player_context)
    app.router.add_get("/api/games/{game_key}/health", api_game_health)
    app.router.add_post(
        "/api/games/{game_key}/health/{event_id}/{action:resolve|ignore}",
        api_mark_health_event,
    )
    app.router.add_post("/api/games/{game_key}/mode", api_set_solo_mode)
    app.router.add_post(
        "/api/games/{game_key}/settings/narrative-perspective",
        api_set_narrative_perspective,
    )
    app.router.add_post(
        "/api/games/{game_key}/settings/luck-timeout", api_set_luck_timeout
    )
    app.router.add_post(
        "/api/games/{game_key}/players/{user_id}/away", api_set_player_away
    )
    app.router.add_post("/api/games/{game_key}/player-access", api_set_player_access)
    app.router.add_post("/api/games/create", api_create_game)
    app.router.add_post("/api/games/create-from-seed", api_create_from_seed)
    app.router.add_post("/api/games/batch-delete", api_batch_delete_games)
    app.router.add_post("/api/games/{game_key}/action", api_action)
    app.router.add_post("/api/games/{game_key}/kp-question", api_kp_question)
    app.router.add_get("/api/games/{game_key}/table-talk", api_table_talk)
    app.router.add_get(
        "/api/games/{game_key}/available-actions", api_ruleset_available_actions
    )
    app.router.add_post("/api/games/{game_key}/intents", api_ruleset_submit_intent)
    app.router.add_post(
        "/api/games/{game_key}/decisions/{decision_id}",
        api_ruleset_resolve_decision,
    )
    app.router.add_post(
        "/api/games/{game_key}/checks/{check_id}/luck", api_luck_decision
    )
    app.router.add_post("/api/games/{game_key}/advance", api_advance)
    app.router.add_post("/api/games/{game_key}/gm-command", api_gm_command)
    app.router.add_post("/api/games/{game_key}/rollback", api_rollback)
    app.router.add_post("/api/games/{game_key}/story-recap", api_story_recap)
    app.router.add_get("/api/games/{game_key}/private-log", api_private_log)
    app.router.add_post("/api/games/{game_key}/private-message", api_gm_private_message)
    app.router.add_post(
        "/api/games/{game_key}/payments/{payment_id}", api_payment_resolve
    )
    app.router.add_post("/api/games/{game_key}/payments", api_payment_create)
    app.router.add_get("/api/games/{game_key}/characters", api_chars)
    app.router.add_get("/api/games/{game_key}/log", api_log)
    app.router.add_route("DELETE", "/api/games/{game_key}", api_delete_game)
    app.router.add_get("/api/games/{game_key}/export", api_export_game)
    app.router.add_route(
        "PUT", "/api/games/{game_key}/character/{user_id}", api_char_update
    )
    app.router.add_patch(
        "/api/games/{game_key}/character/{user_id}/profile",
        api_ruleset_character_profile_update,
    )
    app.router.add_post(
        "/api/games/{game_key}/character/{user_id}/adopt-card",
        api_ruleset_character_adopt_card,
    )
    app.router.add_post(
        "/api/games/{game_key}/character/{user_id}/advancement/preview",
        api_live_character_advancement_preview,
    )
    app.router.add_post(
        "/api/games/{game_key}/character/{user_id}/advancement/apply",
        api_live_character_advancement_apply,
    )
    app.router.add_post(
        "/api/games/{game_key}/advancement/control",
        api_live_advancement_control,
    )
    app.router.add_post(
        "/api/games/{game_key}/character/{user_id}/rest",
        api_live_character_rest,
    )
    app.router.add_route(
        "DELETE", "/api/games/{game_key}/character/{user_id}", api_char_delete
    )
    app.router.add_route(
        "PUT", "/api/games/{game_key}/npc/{npc_id}/portrait", api_npc_portrait_update
    )
    app.router.add_post("/api/games/{game_key}/players", api_player_create)
    app.router.add_post(
        "/api/games/{game_key}/verify-room-password", api_verify_room_password
    )
    app.router.add_post("/api/games/{game_key}/room-password", api_set_room_password)
    app.router.add_post("/api/games/{game_key}/reset", api_reset_game)
    app.router.add_post("/api/games/{game_key}/restart", api_restart_game)
    app.router.add_post("/api/games/{game_key}/switch-world", api_switch_world)
    app.router.add_post(r"/api/games/{game_key}/swipe/{round:\d+}", api_swipe)
    app.router.add_put(r"/api/games/{game_key}/swipe/{round:\d+}", api_swipe)
