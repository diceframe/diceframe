from src.engine.game_instance import GameInstance, GameState
from src.webui.routes.sse import _play_public_signature


def test_public_signature_changes_for_same_round_rollback():
    inst = GameInstance(game_key=("web", "room", "bot"))
    inst.state = GameState.ACTIVE_ACTION
    inst.round_number = 3
    inst.players["player-1"] = {
        "character_name": "AnyMoonS",
        "character_sheet": {"hp": 11, "max_hp": 13, "inventory": [{"name": "彼得森的帆布挎包"}]},
    }
    inst.action_queue.append({
        "user_id": "player-1",
        "text": "检查挎包",
        "timestamp": "before-rollback",
    })
    inst.log.append({"round": 3, "gm_response": "你受了伤。"})
    before = _play_public_signature(inst, "player-1")

    inst.get_character_sheet("player-1")["hp"] = 13
    inst.get_character_sheet("player-1")["inventory"] = []
    inst.action_queue.clear()
    inst.log.clear()
    inst.last_activity = "after-rollback"
    after = _play_public_signature(inst, "player-1")

    assert inst.round_number == 3
    assert after != before
