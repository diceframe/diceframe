import json

import pytest

from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.routes.games import _should_rebind_player_session
from src.webui.services import (
    characters,
    game_controls,
    game_master,
    game_queries,
)
from src.webui.services._common import _GAME_KEY_SEP


class DummyAPI:
    def __init__(self, registry, cards_path=None):
        self._reg = registry
        self._character_cards_path = cards_path
        self._rules_dir = None
        from src.webui.services.character_cards import CharacterCardDependencies

        self._character_card_dependencies = CharacterCardDependencies(
            cards_path=cards_path or registry.save_dir.parent / "character_cards.json",
        )
        self._character_dependencies = characters.CharacterDependencies(
            games=characters.CharacterGameDependencies(
                get_instance=registry.get,
                parse_game_key=self._parse_key,
                save_instance=registry.save,
            ),
            rules=characters.CharacterRuleDependencies(
                rules_dir=None,
                load_rule_by_id=lambda _rule_id, _language: None,
                load_rule_for_game=lambda _instance: None,
                ruleset_registry=RulesetRuntimeRegistry([]),
            ),
            assets=characters.CharacterAssetDependencies(
                lorebook=None,
                load_world_template=lambda _world_id, _language: None,
                avatar_file=lambda _asset_id: None,
                generated_image_file=lambda _asset_id: None,
            ),
            save_character_card=self.save_character_card,
        )

    def _parse_key(self, game_key: str) -> tuple:
        return tuple(game_key.split(_GAME_KEY_SEP))

    def _load_rule_for_game(self, inst):
        return None

    def save_character_card(self, character):
        from src.webui.services.character_cards import save_character_card

        return save_character_card(self._character_card_dependencies, character)


def _game_controls(registry: GameRegistry) -> game_controls.GameControlService:
    return game_controls.GameControlService(game_controls.GameControlDependencies(
        parse_game_key=lambda game_key: tuple(game_key.split(_GAME_KEY_SEP)),
        get_instance=registry.get,
        save_instance=registry.save,
        load_rule=lambda _instance: None,
    ))


def _game_master(registry: GameRegistry) -> game_master.GameMasterService:
    return game_master.GameMasterService(game_master.GameMasterDependencies(
        parse_game_key=lambda game_key: tuple(game_key.split(_GAME_KEY_SEP)),
        get_instance=registry.get,
        save_instance=registry.save,
        load_rule=lambda _instance: None,
    ))


def _game_queries(registry: GameRegistry) -> game_queries.GameQueryDependencies:
    return game_queries.GameQueryDependencies(
        list_instances=registry.list_all,
        get_instance=registry.get,
        parse_game_key=lambda game_key: tuple(game_key.split(_GAME_KEY_SEP)),
        load_world_template=None,
        load_rule_for_game=lambda _instance: None,
        ruleset_registry=RulesetRuntimeRegistry(),
    )


@pytest.mark.asyncio
async def test_set_solo_mode_marks_pending_round_ready(tmp_path):
    registry = GameRegistry(tmp_path)
    key = ("web", "game", "bot")
    inst = GameInstance(game_key=key, state=GameState.ACTIVE_ACTION)
    inst.players["gm"] = {"character_name": "GM", "character_sheet": {"deceased": False}}
    inst.players["p1"] = {"character_name": "玩家", "character_sheet": {"deceased": False}}
    inst.action_queue.append({"user_id": "gm", "text": "继续"})
    registry.register(inst)

    result = await _game_controls(registry).set_solo_mode(
        _GAME_KEY_SEP.join(key), True,
    )

    assert result["ok"]
    assert inst.solo_mode is True
    assert inst.ready_players == {"gm", "p1"}


@pytest.mark.asyncio
async def test_narrative_perspective_is_ruleset_neutral_and_persisted(tmp_path):
    registry = GameRegistry(tmp_path)
    key = ("web", "dnd-game", "bot")
    inst = GameInstance(
        game_key=key,
        rule_id="dnd2024_srd",
        ruleset_runtime={"id": "core:dnd2024"},
    )
    registry.register(inst)

    result = await _game_controls(registry).set_narrative_perspective(
        _GAME_KEY_SEP.join(key), "third_person",
    )

    assert result == {"ok": True, "narrative_perspective": "third_person"}
    persisted = GameInstance.from_dict(json.loads(registry._save_path(key).read_text(encoding="utf-8")))
    assert persisted.narrative_perspective == "third_person"

    generic_key = ("web", "generic-game", "bot")
    generic = GameInstance(game_key=generic_key, rule_id="freeform_fantasy")
    registry.register(generic)
    generic_result = await _game_controls(registry).set_narrative_perspective(
        _GAME_KEY_SEP.join(generic_key), "immersive",
    )
    assert generic_result == {"ok": True, "narrative_perspective": "immersive"}
    assert generic.narrative_perspective == "immersive"


@pytest.mark.asyncio
async def test_gm_private_message_appends_private_log(tmp_path):
    registry = GameRegistry(tmp_path)
    key = ("web", "game", "bot")
    inst = GameInstance(game_key=key, state=GameState.ACTIVE_ACTION, round_number=3)
    inst.players["p1"] = {"character_name": "艾伦", "character_sheet": {"deceased": False}}
    registry.register(inst)

    result = await _game_master(registry).private_message(
        _GAME_KEY_SEP.join(key), "p1", "你注意到门后有冷风。"
    )
    log = game_queries.private_log(_game_queries(registry), _GAME_KEY_SEP.join(key))

    assert result["ok"]
    assert inst.private_log["p1"][0]["source"] == "gm"
    assert log["messages"][0]["character_name"] == "艾伦"
    assert "冷风" in log["messages"][0]["text"]


def test_private_log_for_user_only_returns_own_messages(tmp_path):
    registry = GameRegistry(tmp_path)
    key = ("web", "game", "bot")
    inst = GameInstance(game_key=key, state=GameState.ACTIVE_ACTION, round_number=3)
    inst.players["p1"] = {"character_name": "艾伦", "character_sheet": {"deceased": False}}
    inst.players["p2"] = {"character_name": "贝拉", "character_sheet": {"deceased": False}}
    inst.private_log["p1"] = [{"round": 1, "text": "你听到门后有冷风。", "source": "gm"}]
    inst.private_log["p2"] = [{"round": 1, "text": "你发现窗边有脚印。", "source": "gm"}]
    registry.register(inst)

    log = game_queries.private_log_for_user(
        _game_queries(registry), _GAME_KEY_SEP.join(key), "p1",
    )

    assert log["ok"] is True
    assert len(log["messages"]) == 1
    assert log["messages"][0]["user_id"] == "p1"
    assert "窗边" not in log["messages"][0]["text"]


def test_gm_session_does_not_rebind_when_opening_player_link():
    assert _should_rebind_player_session(
        "gm_uid",
        "gm_uid",
        "player_1",
        {"ok": True, "user_id": "player_1"},
        False,
    ) is False
    assert _should_rebind_player_session(
        "web_user",
        "gm_uid",
        "player_1",
        {"ok": True, "user_id": "player_1"},
        False,
    ) is True


@pytest.mark.asyncio
async def test_delete_character_cleans_player_runtime_state(tmp_path):
    registry = GameRegistry(tmp_path / "saves")
    key = ("web", "game", "bot")
    inst = GameInstance(game_key=key, state=GameState.ACTIVE_ACTION)
    inst.players["gm"] = {"character_name": "GM", "character_sheet": {"deceased": False}}
    inst.players["p1"] = {"character_name": "Player", "character_sheet": {"deceased": False}}
    inst.ready_players.add("p1")
    inst.action_queue.append({"user_id": "p1", "text": "act"})
    inst.pending_actions.append({"user_id": "p1", "text": "next"})
    inst.private_log["p1"] = [{"text": "secret"}]
    registry.register(inst)

    api = DummyAPI(registry)
    result = await characters.delete_character(
        api._character_dependencies, _GAME_KEY_SEP.join(key), "p1",
    )

    assert result["ok"] is True
    assert "p1" not in inst.players
    assert "p1" not in inst.ready_players
    assert not inst.action_queue
    assert not inst.pending_actions
    assert "p1" not in inst.private_log


def test_gm_target_prioritizes_exact_player_name_over_generic():
    """角色真名叫'冒险者'时，GM 指令应优先命中该玩家而非当作泛指歧义。"""
    inst = GameInstance(("web", "gm_target", "bot"), state=GameState.ACTIVE_JUDGMENT)
    inst.players = {
        "adv": {"character_name": "冒险者", "character_sheet": {"deceased": True, "hp": 0}},
        "wu": {"character_name": "吴川", "character_sheet": {"deceased": False, "hp": 12}},
    }
    uid, err = game_master._resolve_gm_command_target(
        inst, "冒险者", prefer_deceased=True,
    )
    assert uid == "adv"
    assert err == ""

    # 多个死亡玩家时，泛称才歧义报错
    inst.players["adv2"] = {"character_name": "第二个冒险者", "character_sheet": {"deceased": True, "hp": 0}}
    uid2, err2 = game_master._resolve_gm_command_target(
        inst, "玩家", prefer_deceased=True,
    )
    assert uid2 is None
    assert "写明角色名" in err2
