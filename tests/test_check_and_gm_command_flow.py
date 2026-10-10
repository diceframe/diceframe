from __future__ import annotations

import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.commands.dice_resolver import DiceResolver
from src.commands.round_actions import build_dice_constraint_block, collect_actions_text
from src.commands.round_processor import RoundProcessor
from src.engine.checks import build_check_request
from src.engine.game_instance import GameInstance, GameState
from src.llm.parser import sanitize_narration
from src.rules.rule_system import RuleSystem
from src.webui.services import game_controls, game_master
from src.webui.services.logs import GameLogService, LogDependencies
from src.engine.modules import checks as checks_module
from src.engine.modules import round_presentation


def _coc_instance() -> tuple[GameInstance, RuleSystem]:
    instance = GameInstance(("web", "room", "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    instance.players["p1"] = {
        "character_name": "冒险者",
        "character_sheet": {
            "attributes": {"dex": 60, "int": 50, "pow": 55},
            "skills": [{"name": "潜行", "value": 20}, {"name": "侦查", "value": 45}],
            "luck": 30,
            "max_luck": 99,
        },
    }
    return instance, RuleSystem.load(Path("templates/rules/freeform_coc.json"))


def test_natural_language_action_builds_rule_neutral_check_request():
    instance, rule = _coc_instance()
    request = build_check_request(
        instance,
        {"user_id": "p1", "text": "握紧左轮手枪悄悄上楼"},
        rule,
    )

    assert request is not None
    assert request["dice_system"] == "d100"
    assert request["skill"] == "潜行"
    assert request["attribute"] == "dex"
    assert request["label"] == "潜行检定"


def test_tavern_rule_never_requests_a_roll():
    instance, _ = _coc_instance()
    rule = RuleSystem.load(Path("templates/rules/tavern_free.json"))

    assert build_check_request(
        instance,
        {"user_id": "p1", "text": "悄悄上楼", "selected_skill": "潜行"},
        rule,
    ) is None


def test_legacy_coc_entry_does_not_replace_round_action_queue(monkeypatch):
    class GuardedGameInstance(GameInstance):
        guard_action_queue = False

        def __setattr__(self, name, value):
            if name == "action_queue" and self.guard_action_queue:
                raise AssertionError("骰子适配器不得替换回合行动队列")
            super().__setattr__(name, value)

    instance = GuardedGameInstance(("web", "legacy-coc", "bot"))
    instance.players["p1"] = {
        "character_name": "调查员",
        "character_sheet": {
            "attributes": {"int": 60},
            "skills": [{"name": "侦查", "value": 45}],
        },
    }
    instance.action_queue.append({"user_id": "p1", "text": "原始行动"})
    instance.guard_action_queue = True
    monkeypatch.setattr("src.engine.dice_rng.random.randint", lambda _a, _b: 30)

    block = DiceResolver().roll_coc_check(
        instance,
        "p1",
        instance.players["p1"],
        {"int": 60},
        {"name": "侦查", "value": 45},
    )

    assert "d100=30" in block
    assert instance.action_queue == [{"user_id": "p1", "text": "原始行动"}]


def test_confirmed_roll_is_reused_without_a_second_random_roll(monkeypatch):
    instance, rule = _coc_instance()
    request = build_check_request(
        instance,
        {"user_id": "p1", "text": "悄悄上楼"},
        rule,
    )
    instance.action_queue = [{
        "user_id": "p1",
        "text": "悄悄上楼\n(系统掷骰: d100=54)",
        "check_request": request,
        "dice_value": 54,
        "dice_rolls": [54],
        "dice_pending": False,
    }]
    monkeypatch.setattr("src.engine.dice_rng.random.randint", lambda *_: pytest.fail("不得二次掷骰"))

    block = build_dice_constraint_block(instance, collect_actions_text(instance), rule, "d100", DiceResolver())

    assert "d100=54" in block
    assert checks_module.last_check(instance)["actor_uid"] == "p1"
    assert checks_module.last_check(instance)["skill"] == "潜行"
    assert checks_module.last_check(instance)["roll"] == 54


def test_round_checks_pause_before_narration_when_luck_can_change_the_result():
    instance, rule = _coc_instance()
    request = build_check_request(
        instance,
        {"user_id": "p1", "text": "悄悄上楼", "selected_skill": "潜行"},
        rule,
    )
    instance.state = GameState.ACTIVE_JUDGMENT
    instance.action_queue = [{
        "user_id": "p1",
        "text": "悄悄上楼\n(系统掷骰: d100=22)",
        "check_request": request,
        "dice_value": 22,
        "dice_rolls": [22],
        "dice_pending": False,
    }]
    processor = RoundProcessor.__new__(RoundProcessor)
    processor._prompt = SimpleNamespace(
        load_rule_context=lambda *_args: SimpleNamespace(rule=rule, dice_system="d100"),
    )
    processor._load_world_template = lambda *_args: {}
    processor._dice = DiceResolver()

    checks = processor.prepare_round_checks(instance)

    assert len(checks) == 1
    assert checks[0]["verdict"] == "失败"
    assert checks[0]["luck_cost"] == 2
    assert checks[0]["luck_decision"] == "pending"
    assert instance.pending_luck_checks()[0]["check_id"]
    assert checks_module.last_check(instance)["threshold"] == 20


def test_d100_resolver_ignores_model_target_and_uses_character_skill_value():
    instance, rule = _coc_instance()
    instance.action_queue = [{
        "user_id": "p1",
        "text": "仔细侦查房间",
        "check_request": {
            "check_id": "check-safe-threshold",
            "required": True,
            "actor_uid": "p1",
            "dice_system": "d100",
            "label": "侦查检定",
            "skill": "侦查",
            "attribute": "int",
            "target": 99,
        },
        "dice_value": 50,
        "dice_rolls": [50],
        "dice_pending": False,
    }]

    build_dice_constraint_block(
        instance,
        collect_actions_text(instance),
        rule,
        "d100",
        DiceResolver(),
    )

    assert checks_module.last_check(instance)["threshold"] == 45
    assert checks_module.last_check(instance)["verdict"] == "失败"


def test_d20_advantage_reuses_both_confirmed_rolls():
    instance = GameInstance(("web", "room", "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    instance.players["p1"] = {
        "character_name": "Rogue",
        "character_sheet": {
            "attributes": {"dex": 16},
            "skills": [{"name": "Stealth", "value": 1}],
            "level": 5,
        },
    }
    rule = RuleSystem.load(Path("templates/rules/dnd5e.json"))
    request = build_check_request(
        instance,
        {
            "user_id": "p1",
            "text": "stealth check with advantage",
            "selected_skill": "Stealth",
            "selected_attribute": "dex",
            "advantage_mode": "advantage",
        },
        rule,
    )
    instance.action_queue = [{
        "user_id": "p1",
        "text": "stealth check with advantage",
        "check_request": request,
        "dice_value": 17,
        "dice_rolls": [4, 17],
        "dice_pending": False,
    }]

    build_dice_constraint_block(
        instance,
        collect_actions_text(instance),
        rule,
        "d20",
        DiceResolver(),
    )

    assert checks_module.last_check(instance)["dice"] == "d20"
    assert checks_module.last_check(instance)["rolls"] == [4, 17]
    assert checks_module.last_check(instance)["roll"] == 17
    assert checks_module.last_check(instance)["advantage_mode"] == "advantage"


def test_structured_runtime_can_defer_an_action_before_any_generic_roll():
    instance = GameInstance(("web", "room", "bot"))
    instance.players["p1"] = {
        "character_name": "Fighter",
        "character_sheet": {"attributes": {"str": 16}, "skills": [], "level": 1},
    }
    instance.action_queue = [{
        "user_id": "p1", "text": "I attack the goblin.",
        "check_request": {
            "check_id": "opening-attack", "required": True, "actor_uid": "p1",
            "dice_system": "d20", "label": "Attack", "attribute": "str",
            "target": 12, "kind": "attack",
        },
        "dice_value": 18, "dice_rolls": [18], "dice_pending": False,
    }]
    rule = RuleSystem.load(Path("templates/rules/dnd5e.json"))

    block = build_dice_constraint_block(
        instance,
        collect_actions_text(instance),
        rule,
        "d20",
        DiceResolver(),
        skip_action_indexes={0},
    )

    assert block == ""
    assert "check_request" not in instance.action_queue[0]
    assert "dice_value" not in instance.action_queue[0]
    assert checks_module.last_checks(instance) == []


def test_late_game_d20_target_is_capped_and_nineteen_can_succeed() -> None:
    instance = GameInstance(("web", "room", "bot"))
    instance.players["p1"] = {
        "character_name": "Rogue",
        "character_sheet": {
            "attributes": {"dex": 16},
            "skills": [],
        },
    }
    action = {
        "user_id": "p1",
        "text": "强行打开机关门",
        "check_request": {
            "actor_uid": "p1",
            "dice_system": "d20",
            "attribute": "dex",
            "target": 30,
        },
        "dice_value": 19,
        "dice_rolls": [19],
    }
    base = RuleSystem.load(Path("templates/rules/base_d20.json"))

    DiceResolver().resolve_action_check(instance, action, base)

    # 通用规则仍把失控 DC 30 钳制到 20，19+3 可以成功
    assert checks_module.last_check(instance)["dc"] == 20
    assert checks_module.last_check(instance)["total"] == 22
    assert checks_module.last_check(instance)["verdict"] == "成功"

    dnd = RuleSystem.load(Path("templates/rules/dnd5e.json"))
    DiceResolver().resolve_action_check(instance, action, dnd)

    # dnd5e 显式允许 DC 30（近乎不可能），不再钳制到 20
    assert checks_module.last_check(instance)["dc"] == 30
    assert checks_module.last_check(instance)["verdict"] == "失败"

def test_confirmed_d20_without_target_still_uses_dc_cap() -> None:
    instance = GameInstance(("web", "room", "bot"))
    instance.players["p1"] = {
        "character_name": "Rogue",
        "character_sheet": {"attributes": {"dex": 16}, "skills": []},
    }
    action = {
        "user_id": "p1",
        "text": "尝试兼容路径检定",
        "check_request": {
            "actor_uid": "p1",
            "dice_system": "d20",
            "attribute": "dex",
        },
        "dice_value": 19,
        "dice_rolls": [19],
    }
    rule = RuleSystem({
        "rule_id": "high_default_dc",
        "dice_system": "d20",
        "max_check_dc": 20,
        "dc_table": {"normal": 30},
    })

    DiceResolver().resolve_action_check(instance, action, rule)

    assert checks_module.last_check(instance)["dc"] == 20
    assert checks_module.last_check(instance)["total"] == 22
    assert checks_module.last_check(instance)["verdict"] == "成功"


def test_custom_d20_rule_can_disable_natural_twenty_auto_success() -> None:
    instance = GameInstance(("web", "room", "bot"))
    instance.players["p1"] = {
        "character_name": "Investigator",
        "character_sheet": {"attributes": {"dex": 14}, "skills": []},
    }
    action = {
        "user_id": "p1",
        "text": "尝试完成严格规则检定",
        "check_request": {
            "actor_uid": "p1",
            "dice_system": "d20",
            "attribute": "dex",
            "target": 25,
        },
        "dice_value": 20,
        "dice_rolls": [20],
    }
    rule = RuleSystem({
        "rule_id": "strict_d20",
        "dice_system": "d20",
        "max_check_dc": 30,
        "check_mechanic": {
            "dice": "d20",
            "comparison": "roll_plus_modifier_gte_target",
            "critical": {},
        },
    })

    DiceResolver().resolve_action_check(instance, action, rule)

    assert checks_module.last_check(instance)["total"] == 22
    assert checks_module.last_check(instance)["dc"] == 25
    assert checks_module.last_check(instance)["verdict"] == "失败"


class _Registry:
    def __init__(self, instance: GameInstance):
        self.instance = instance
        self.saved = 0

    def get(self, _key):
        return self.instance

    async def save(self, _instance):
        self.saved += 1


class _Api:
    def __init__(self, instance: GameInstance, rule: RuleSystem):
        self._reg = _Registry(instance)
        self.rule = rule

    @staticmethod
    def _parse_key(_key):
        return ("web", "room", "bot")

    def _load_rule_for_game(self, _instance):
        return self.rule


def _controls(api: _Api) -> game_controls.GameControlService:
    return game_controls.GameControlService(game_controls.GameControlDependencies(
        parse_game_key=api._parse_key,
        get_instance=api._reg.get,
        save_instance=api._reg.save,
        load_rule=api._load_rule_for_game,
    ))


def _game_master(api: _Api) -> game_master.GameMasterService:
    return game_master.GameMasterService(game_master.GameMasterDependencies(
        parse_game_key=api._parse_key,
        get_instance=api._reg.get,
        save_instance=api._reg.save,
        load_rule=api._load_rule_for_game,
    ))


async def gm_command(
    api: _Api, game_key: str, command: str, mode: str = "note",
) -> dict:
    return await _game_master(api).command(game_key, command, mode)


async def resolve_luck_decision(
    api: _Api, game_key: str, check_id: str, actor_uid: str, spend: bool,
) -> dict:
    return await _controls(api).resolve_luck_decision(
        game_key, check_id, actor_uid, spend,
    )


async def decline_pending_luck(api: _Api, game_key: str) -> dict:
    return await _controls(api).decline_pending_luck(game_key)


@pytest.mark.asyncio
async def test_gm_resource_command_updates_luck_directly_without_public_action():
    instance, rule = _coc_instance()
    api = _Api(instance, rule)

    result = await gm_command(api, "web|room|bot", "给用户加幸运20")

    assert result["ok"] is True
    assert result["kind"] == "resource_update"
    assert instance.get_character_sheet("p1")["luck"] == 50
    assert instance.action_queue == []
    assert round_presentation.gm_directives(instance) == []


def _office_like_rule(tmp_path: Path) -> RuleSystem:
    template = {
        "rule_id": "test_office_rule",
        "rule_name": "测试办公规则",
        "dice_system": "d20",
        "attributes": [{"key": "brain", "name": "智商", "min": 3, "max": 18}],
        "special_stats": [
            {"key": "kpi", "name": "KPI", "max": 100, "initial": 42,
             "aliases": ["绩效", "kpi进度"]},
            {"key": "overtime", "name": "加班值", "max": 100, "initial": 0},
        ],
    }
    path = tmp_path / "test_office_rule.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    return RuleSystem.load(path)


def _office_instance(rule: RuleSystem) -> GameInstance:
    instance = GameInstance(("web", "office", "bot"))
    instance.state = GameState.ACTIVE_ACTION
    instance.round_number = 1
    instance.rule_id = rule.rule_id
    instance.players["p1"] = {
        "character_name": "小林",
        "character_sheet": {
            "attributes": {"brain": 16},
            "kpi": 42,
            "max_kpi": 100,
            "overtime": 0,
            "max_overtime": 100,
        },
    }
    return instance


@pytest.mark.asyncio
async def test_gm_command_accepts_rule_special_stat_name_and_key(tmp_path):
    rule = _office_like_rule(tmp_path)
    instance = _office_instance(rule)
    api = _Api(instance, rule)

    by_name = await gm_command(api, "web|office|bot", "给小林加KPI10")
    assert by_name["ok"] is True
    assert by_name["kind"] == "resource_update"
    assert instance.get_character_sheet("p1")["kpi"] == 52
    assert by_name["resource_update"]["resource_label"] == "KPI"

    by_key = await gm_command(api, "web|office|bot", "小林的overtime+5")
    assert by_key["ok"] is True
    assert instance.get_character_sheet("p1")["overtime"] == 5


@pytest.mark.asyncio
async def test_gm_command_accepts_rule_resource_aliases_and_clamps_max(tmp_path):
    rule = _office_like_rule(tmp_path)
    instance = _office_instance(rule)
    api = _Api(instance, rule)

    by_alias = await gm_command(api, "web|office|bot", "给小林加绩效70")
    assert by_alias["ok"] is True
    # 42 + 70 = 112，钳制到 max 100
    assert instance.get_character_sheet("p1")["kpi"] == 100


@pytest.mark.asyncio
async def test_gm_command_rule_resource_alias_not_leaking_across_rules(tmp_path):
    coc_instance, coc_rule = _coc_instance()
    coc_api = _Api(coc_instance, coc_rule)

    unknown = await gm_command(coc_api, "web|room|bot", "给用户加KPI10")
    # KPI 不在 CoC 规则中：不落 resource_update，降级为私密指令
    assert unknown.get("kind") in {None, "directive"}
    assert unknown.get("kind") != "resource_update"


@pytest.mark.asyncio
async def test_gm_revive_command_revives_dead_character():
    instance, rule = _coc_instance()
    instance.players["p1"]["character_sheet"].update({
        "deceased": True, "hp": 0, "max_hp": 100, "death_round": 3,
    })
    api = _Api(instance, rule)

    result = await gm_command(api, "web|room|bot", "复活冒险者")

    assert result["ok"] is True
    assert result["kind"] == "revive"
    sheet = instance.get_character_sheet("p1")
    assert sheet["deceased"] is False
    assert sheet["hp"] == 50
    assert "death_round" not in sheet
    assert instance.action_queue == []
    assert round_presentation.gm_directives(instance) == []


@pytest.mark.asyncio
async def test_gm_revive_natural_method_deducts_xp():
    instance, rule = _coc_instance()
    instance.players["p1"]["character_sheet"].update({
        "deceased": True, "hp": 0, "max_hp": 100, "xp": 100,
    })
    api = _Api(instance, rule)

    result = await gm_command(api, "web|room|bot", "复活冒险者(自然)")

    assert result["ok"] is True
    sheet = instance.get_character_sheet("p1")
    assert sheet["deceased"] is False
    assert sheet["hp"] == 10
    assert sheet["xp"] == 80


@pytest.mark.asyncio
async def test_gm_revive_generic_phrase_selects_only_dead_player() -> None:
    instance, rule = _coc_instance()
    instance.players["p1"]["character_sheet"].update({
        "deceased": True, "hp": 0, "max_hp": 100,
    })
    instance.players["p2"] = {
        "character_name": "学者",
        "character_sheet": {"deceased": False, "hp": 20, "max_hp": 20},
    }
    api = _Api(instance, rule)

    result = await gm_command(api, "web|room|bot", "复活名为冒险者的人")

    assert result["ok"] is True
    assert instance.get_character_sheet("p1")["deceased"] is False


@pytest.mark.asyncio
async def test_gm_revive_ambiguous_target_lists_available_character_names() -> None:
    instance, rule = _coc_instance()
    # p1 真名就叫"冒险者"且存活：点名"冒险者"应精确命中 p1（提示未死亡），
    # 而不是被当泛称歧义。只有真名未匹配时才走泛称歧义列名单。
    instance.players["p2"] = {
        "character_name": "学者",
        "character_sheet": {"deceased": False, "hp": 20, "max_hp": 20},
    }
    api = _Api(instance, rule)

    # 真名匹配：精确指向存活角色，复活时提示"未死亡"
    result = await gm_command(api, "web|room|bot", "复活名为冒险者的人")
    assert result["ok"] is False
    assert "未死亡" in result["error"]

    # 无真名匹配 + 多玩家 → 泛称歧义列出可用名单
    instance.players["p1"]["character_name"] = "由洛拉"
    result2 = await gm_command(api, "web|room|bot", "复活名为冒险者的人")
    assert result2["ok"] is False
    assert "学者" in result2["error"] and "由洛拉" in result2["error"]


@pytest.mark.asyncio
async def test_gm_revive_accepts_natural_language_named_character_wrapper() -> None:
    instance, rule = _coc_instance()
    instance.players["p2"] = {
        "character_name": "学者",
        "character_sheet": {"deceased": True, "hp": 0, "max_hp": 20},
    }
    api = _Api(instance, rule)

    result = await gm_command(api, "web|room|bot", "复活名为学者的人")

    assert result["ok"] is True
    assert instance.get_character_sheet("p2")["deceased"] is False


@pytest.mark.asyncio
async def test_gm_resource_heal_revives_dead_character():
    instance, rule = _coc_instance()
    instance.players["p1"]["character_sheet"].update({
        "deceased": True, "hp": 0, "max_hp": 100, "death_round": 3,
    })
    api = _Api(instance, rule)

    result = await gm_command(api, "web|room|bot", "给冒险者加生命值50")

    assert result["ok"] is True
    assert result["kind"] == "resource_update"
    sheet = instance.get_character_sheet("p1")
    assert sheet["deceased"] is False
    assert sheet["hp"] == 50
    assert "death_round" not in sheet
    assert "已复活" in result["message"]


@pytest.mark.asyncio
async def test_spending_luck_is_atomic_persistent_and_idempotent():
    instance, rule = _coc_instance()
    instance.state = GameState.ACTIVE_JUDGMENT
    checks_module.replace_round_checks_prepared(instance, True)
    check = {
        "check_id": "check-1",
        "actor_uid": "p1",
        "actor_name": "冒险者",
        "dice": "d100",
        "roll": 22,
        "threshold": 20,
        "verdict": "失败",
        "luck_cost": 2,
        "luck_spend_available": True,
        "luck_decision": "pending",
    }
    checks_module.replace_last_checks(instance, [check])
    checks_module.replace_last_check(instance, check)
    api = _Api(instance, rule)

    first = await resolve_luck_decision(api, "web|room|bot", "check-1", "p1", True)
    instance.state = GameState.ACTIVE_ACTION  # 模拟最后一个选择已触发叙事并进入下一轮
    second = await resolve_luck_decision(api, "web|room|bot", "check-1", "p1", True)

    assert first["ready_to_resolve"] is True
    assert first["check_result"]["verdict"] == "成功"
    assert first["check_result"]["luck_spent"] == 2
    assert second["already_resolved"] is True
    assert second["round_already_resolved"] is True
    assert instance.get_character_sheet("p1")["luck"] == 28
    assert checks_module.last_checks(instance)[0]["luck_decision"] == "spent"
    assert api._reg.saved == 2


@pytest.mark.asyncio
async def test_luck_decision_is_rejected_while_historical_rewrite_is_active():
    instance, rule = _coc_instance()
    instance.state = GameState.ACTIVE_JUDGMENT
    checks_module.replace_round_checks_prepared(instance, True)
    checks_module.replace_last_checks(instance, [{
        "check_id": "rewrite-luck",
        "actor_uid": "p1",
        "dice": "d100",
        "roll": 22,
        "threshold": 20,
        "verdict": "失败",
        "luck_decision": "pending",
        "luck_spend_available": True,
    }])
    api = _Api(instance, rule)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold_rewrite() -> None:
        async with instance.historical_rewrite() as acquired:
            assert acquired is True
            entered.set()
            await release.wait()

    rewrite = asyncio.create_task(hold_rewrite())
    await entered.wait()
    result = await asyncio.wait_for(
        resolve_luck_decision(api, "web|room|bot", "rewrite-luck", "p1", True),
        1,
    )
    assert result["code"] == "REWRITE_IN_PROGRESS"
    assert checks_module.last_checks(instance)[0]["luck_decision"] == "pending"
    release.set()
    await asyncio.wait_for(rewrite, 1)


@pytest.mark.asyncio
async def test_gm_force_advance_declines_every_pending_luck_choice():
    instance, rule = _coc_instance()
    instance.state = GameState.ACTIVE_JUDGMENT
    checks_module.replace_round_checks_prepared(instance, True)
    checks_module.replace_last_checks(instance, [{
        "check_id": "check-1",
        "actor_uid": "p1",
        "dice": "d100",
        "roll": 22,
        "threshold": 20,
        "verdict": "失败",
        "luck_cost": 2,
        "luck_spend_available": True,
        "luck_decision": "pending",
    }])
    api = _Api(instance, rule)

    result = await decline_pending_luck(api, "web|room|bot")

    assert result["declined_luck_decisions"][0]["luck_decision"] == "declined"
    assert instance.pending_luck_checks() == []
    assert instance.get_character_sheet("p1")["luck"] == 30


@pytest.mark.asyncio
async def test_gm_narrative_command_is_private_and_cannot_trigger_check_detection():
    instance, rule = _coc_instance()
    api = _Api(instance, rule)

    result = await gm_command(api, "web|room|bot", "让下一次判定伴随更强的死亡风险")

    assert result["kind"] == "directive"
    assert instance.action_queue == []
    assert round_presentation.gm_directives(instance)[0]["text"] == "让下一次判定伴随更强的死亡风险"
    assert "GM指令" not in collect_actions_text(instance)


def test_player_narration_strips_internal_check_block_but_keeps_story():
    raw = """【系统潜行检定·必须遵循】
机制: coc7e_core / 标准
检定: d100=54 vs 潜行20
结果: 失败
要求: 必须遵循

你的鞋跟磕在松动的木板上。"""

    assert sanitize_narration(raw) == "你的鞋跟磕在松动的木板上。"


def test_public_log_filters_legacy_gm_instruction():
    instance, rule = _coc_instance()
    instance.log = [{
        "round": 1,
        "actions": [
            {"user_id": "system", "text": "【GM指令】秘密修正"},
            {"user_id": "p1", "text": "检查房门"},
        ],
        "gm_response": "**SANCheck:p1:1d6** | 门锁生锈了。",
        "swipes": ["**STATE:heat:+1** | 警报声响起。"],
    }]
    api = _Api(instance, rule)

    logs = GameLogService(LogDependencies(
        registry=api._reg,
        parse_game_key=api._parse_key,
    ))
    public = logs.get_log("web|room|bot", include_internal=False)
    internal = logs.get_log("web|room|bot", include_internal=True)

    assert [action["user_id"] for action in public["log"][0]["actions"]] == ["p1"]
    assert len(internal["log"][0]["actions"]) == 2
    assert public["log"][0]["gm_response"] == "门锁生锈了。"
    assert public["log"][0]["swipes"] == ["警报声响起。"]
    assert internal["log"][0]["gm_response"] == "门锁生锈了。"
    assert instance.log[0]["gm_response"].startswith("**SANCheck:")


@pytest.mark.asyncio
async def test_spending_own_luck_marks_the_seat_as_having_acted():
    """Card adoption / deletion become GM-only once the seat is in play."""
    from src.engine.modules import seat_activity

    instance, rule = _coc_instance()
    instance.state = GameState.ACTIVE_JUDGMENT
    checks_module.replace_round_checks_prepared(instance, True)
    check = {
        "check_id": "check-own-luck", "actor_uid": "p1", "dice": "d100",
        "roll": 22, "threshold": 20, "verdict": "失败",
        "luck_spend_available": True, "luck_decision": "pending",
    }
    checks_module.replace_last_checks(instance, [check])
    assert not seat_activity.has_acted(instance, "p1")

    result = await instance.resolve_luck_decision("check-own-luck", "p1", True, rule=rule)

    assert result["ok"] is True, result
    assert seat_activity.has_acted(instance, "p1")
