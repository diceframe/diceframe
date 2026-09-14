from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.commands.check_planner import _planner_context, normalize_check_specs, plan_round_checks
from src.engine.checks import resolve_check_request
from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem

ROOT = Path(__file__).resolve().parents[1]


def make_instance() -> GameInstance:
    instance = GameInstance(game_key=("web", "room", "bot"), rule_id="test")
    instance.players = {
        "p1": {
            "user_id": "p1",
            "character_name": "阿岚",
            "character_sheet": {
                "attributes": {"str": 14, "dex": 12},
                "skills": [{"name": "运动", "value": 2}],
            },
        },
        "p2": {
            "user_id": "p2",
            "character_name": "白露",
            "character_sheet": {
                "attributes": {"str": 8, "dex": 16},
                "skills": [{"name": "潜行", "value": 3}],
            },
        },
    }
    instance.action_queue = [
        {"user_id": "p1", "text": "我用肩膀撞开生锈的铁门"},
        {"user_id": "p2", "text": "我贴着阴影绕到守卫背后"},
    ]
    return instance


def make_rule() -> RuleSystem:
    return RuleSystem({
        "rule_id": "test",
        "name": "Test",
        "dice_system": "d20",
        "mechanics": "dnd5e_core",
        "attributes": [
            {"key": "str", "name": "力量", "name_en": "Strength"},
            {"key": "dex", "name": "敏捷", "name_en": "Dexterity"},
        ],
        "dc_table": {"easy": 8, "normal": 12, "hard": 16},
    })


def test_planner_context_publishes_the_ruleset_dc_bands() -> None:
    """prompt 的档位以 ruleset.dc_table 为准，上下文必须给出一致的表。

    历史问题：prompt 写死「简单 8 / 普通 10 / 困难 15 / 极限 20」，而无规则集时
    的兜底也只有 8/10/15，但真实规则表（base_d20/dnd5e）都是 10/15/20/25，
    模型因此把普通档 DC 15 当成"困难"档来报。
    """
    import json

    from src.commands.check_planner import _planner_context

    instance = make_instance()
    fallback = json.loads(_planner_context(instance, None))["ruleset"]
    assert fallback["dc_table"] == {"easy": 10, "normal": 15, "hard": 20, "extreme": 25}

    rule = _dnd5e_rule()
    published = json.loads(_planner_context(instance, rule))["ruleset"]
    assert published["dc_table"] == rule.dc_table

    for locale in ("zh", "en", "ja"):
        prompt = (ROOT / f"prompts/check_planner_{locale}.md").read_text(encoding="utf-8")
        assert "ruleset.dc_table" in prompt or "`dc_table`" in prompt


def test_d20_target_is_clamped_to_dc_cap() -> None:
    """后期失控 DC（25–30）必须被钳到规则显式硬上限。"""
    from src.engine.dice import d20_dc_cap
    instance = make_instance()
    rule = make_rule()
    planned, errors = normalize_check_specs(instance, rule, [
        {"player": "p1", "attribute": "str", "target": 30},
    ])
    assert errors == []
    assert d20_dc_cap(rule) == 20
    assert planned[0][1]["target"] == 20


def test_builtin_dnd5e_uses_default_dc_cap_twenty() -> None:
    from src.engine.dice import d20_dc_cap

    # dnd5e 显式声明 max_check_dc=30（5e Nearly Impossible）；通用 base_d20 保持 20。
    rule = RuleSystem.load(Path("templates/rules/dnd5e.json"))
    assert rule.dc_table["extreme"] == 25
    assert d20_dc_cap(rule) == 30
    base = RuleSystem.load(Path("templates/rules/base_d20.json"))
    assert d20_dc_cap(base) == 20


def test_custom_d20_rule_can_raise_dc_cap_explicitly() -> None:
    from src.engine.dice import d20_dc_cap

    rule = RuleSystem({
        "rule_id": "heroic_d20",
        "dice_system": "d20",
        "max_check_dc": 30,
        "dc_table": {"normal": 18, "extreme": 28},
    })
    assert d20_dc_cap(rule) == 30


def test_normalize_check_specs_accepts_valid_entries_and_rejects_bad_player() -> None:
    instance = make_instance()
    planned, errors = normalize_check_specs(instance, make_rule(), [
        {"player": "阿岚", "attribute": "str", "skill": "运动", "target": 13},
        {"player": "不存在", "attribute": "dex", "target": 12},
    ])
    assert len(planned) == 1
    action, request = planned[0]
    assert action["user_id"] == "p1"
    assert request["actor_uid"] == "p1"
    assert request["attribute"] == "str"
    assert request["target"] == 13
    assert request["planner_source"] == "llm_tool"
    assert errors == ["checks[1] player 不存在或本轮未行动"]


@pytest.mark.parametrize("attribute", ["str", "力量", "Strength"])
def test_d20_attribute_key_and_localized_names_are_accepted(attribute: str) -> None:
    instance = make_instance()
    planned, errors = normalize_check_specs(instance, make_rule(), [
        {"player": "p1", "attribute": attribute, "target": 13},
    ])

    assert errors == []
    assert planned[0][1]["attribute"] == "str"
    assert planned[0][1]["target"] == 13


def test_d20_skill_accidentally_placed_in_attribute_is_safely_repaired() -> None:
    instance = make_instance()
    instance.action_queue[1]["text"] = "我潜行绕到守卫背后"
    planned, errors = normalize_check_specs(instance, make_rule(), [
        {"player": "p2", "attribute": "潜行", "target": 12},
    ])

    assert errors == []
    assert planned[0][1]["attribute"] == "dex"
    assert planned[0][1]["skill"] == "潜行"
    assert planned[0][1]["planner_source"] == "llm_tool_repaired"


def test_d100_skill_cross_field_uses_character_sheet_threshold() -> None:
    instance = make_instance()
    instance.players["p1"]["character_sheet"] = {
        "attributes": {"dex": 60, "int": 50},
        "skills": [{"name": "侦查", "value": 45}],
    }
    rule = RuleSystem({
        "rule_id": "custom_percentile",
        "rule_name": "自定义百分制",
        "dice_system": "d100",
        "attributes": [
            {"key": "dex", "name": "敏捷"},
            {"key": "int", "name": "智力"},
        ],
    })

    planned, errors = normalize_check_specs(instance, rule, [
        {"player": "p1", "attribute": "侦查", "target": 99},
    ])

    assert errors == []
    request = planned[0][1]
    assert request["skill"] == "侦查"
    assert request["target"] == 45
    assert request["planner_source"] == "llm_tool_repaired"


def test_d100_valid_skill_does_not_require_model_attribute_or_target() -> None:
    instance = make_instance()
    instance.players["p1"]["character_sheet"] = {
        "attributes": {"focus": 65},
        "skills": [{"name": "解码", "value": 48}],
    }
    rule = RuleSystem({
        "rule_id": "custom_d100",
        "rule_name": "Custom d100",
        "dice_system": "d100",
        "attributes": [{"key": "focus", "name": "专注", "name_en": "Focus"}],
    })

    planned, errors = normalize_check_specs(instance, rule, [
        {"player": "p1", "skill": "解码"},
    ])

    assert errors == []
    assert planned[0][1]["skill"] == "解码"
    assert planned[0][1]["target"] == 48


def test_unique_short_npc_opponent_name_is_resolved() -> None:
    instance = make_instance()
    instance.npcs = {
        "department_head": {"name": "考古学系主任", "character_name": "考古学系主任"},
    }
    planned, errors = normalize_check_specs(instance, make_rule(), [{
        "player": "p1",
        "attribute": "str",
        "target": 12,
        "opponent": "系主任",
    }])

    assert errors == []
    assert planned[0][1]["opponent"] == "npc:department_head"


def test_explicit_player_selection_wins_over_model_fields() -> None:
    instance = make_instance()
    instance.action_queue[0]["selected_attribute"] = "dex"
    instance.action_queue[0]["selected_skill"] = "运动"

    planned, errors = normalize_check_specs(instance, make_rule(), [
        {"player": "p1", "attribute": "str", "skill": "不存在", "target": 15},
    ])

    assert errors == []
    assert planned[0][1]["attribute"] == "dex"
    assert planned[0][1]["skill"] == "运动"


@pytest.mark.parametrize("rule_file", [
    "base_d20.json",
    "dnd5e.json",
    "freeform_fantasy.json",
    "freeform_cyberpunk.json",
    "freeform_wuxia.json",
    "freeform_coc.json",
])
def test_every_builtin_dice_ruleset_accepts_its_canonical_attribute(rule_file: str) -> None:
    rule = RuleSystem.load(Path("templates/rules") / rule_file)
    attribute = rule.attribute_keys[0]
    value = 60 if rule.dice_system == "d100" else 14
    instance = make_instance()
    instance.players["p1"]["character_sheet"] = {
        "attributes": {attribute: value},
        "skills": [],
    }

    planned, errors = normalize_check_specs(instance, rule, [
        {"player": "p1", "attribute": attribute, "target": 12},
    ])

    assert errors == []
    assert planned[0][1]["attribute"] == attribute
    assert planned[0][1]["target"] == (60 if rule.dice_system == "d100" else 12)


@pytest.mark.asyncio
async def test_plan_round_checks_uses_single_batched_tool_call() -> None:
    instance = make_instance()

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def call_tools(self, _system, user, **kwargs):
            self.calls += 1
            assert '"player_id":"p1"' in user
            assert kwargs["tools"][0]["function"]["name"] == "dice_checks"
            return SimpleNamespace(
                tool_calls=[{
                    "name": "dice_checks",
                    "arguments": {"checks": [
                        {"player": "p1", "attribute": "str", "skill": "运动", "target": 14},
                        {"player": "p2", "attribute": "dex", "skill": "潜行", "target": 12},
                    ]},
                }],
                total_tokens=37,
                provider_used="fake",
                native_tools=True,
            )

    client = FakeClient()
    planned, metadata = await plan_round_checks(instance, make_rule(), client)
    assert client.calls == 1
    assert [request["actor_uid"] for _, request in planned] == ["p1", "p2"]
    assert metadata == {
        "available": True,
        "native_tools": True,
        "provider": "fake",
        "total_tokens": 37,
        "errors": [],
        "overreach": [],
        "economy_offers": [],
        "unpriced_purchase_intents": [],
    }


@pytest.mark.asyncio
async def test_no_dice_ruleset_skips_model_call() -> None:
    instance = make_instance()
    rule = RuleSystem.load(Path("templates/rules/tavern_free.json"))

    class FailClient:
        async def call_tools(self, *_args, **_kwargs):
            pytest.fail("无骰规则不应调用检定模型")

    planned, metadata = await plan_round_checks(instance, rule, FailClient())

    assert planned == []
    assert metadata["skipped"] == "no_dice_rule"
    assert metadata["total_tokens"] == 0


def test_rule_exposes_derived_check_mechanic() -> None:
    assert make_rule().check_mechanic["comparison"] == "roll_plus_modifier_gte_target"
    coc = RuleSystem({"rule_id": "coc", "name": "CoC", "dice_system": "d100"})
    assert coc.check_mechanic["comparison"] == "roll_lte_target"


class _EmptyPlannerClient:
    async def call_tools(self, *_args, **_kwargs):
        return SimpleNamespace(
            tool_calls=[{"name": "dice_checks", "arguments": {"checks": []}}],
            total_tokens=10,
            provider_used="fake",
            native_tools=False,
        )


@pytest.mark.asyncio
async def test_empty_model_plan_cannot_auto_succeed_explicit_dodge_skill() -> None:
    instance = make_instance()
    instance.players["p1"]["character_sheet"] = {
        "attributes": {"dex": 60, "int": 50},
        "skills": [{"name": "躲避", "value": 85}],
    }
    instance.action_queue = [{"user_id": "p1", "text": "使用躲避"}]
    rule = RuleSystem.load(Path("templates/rules/freeform_coc.json"))

    planned, metadata = await plan_round_checks(instance, rule, _EmptyPlannerClient())

    assert metadata["errors"] == []
    assert len(planned) == 1
    request = planned[0][1]
    assert request["skill"] == "躲避"
    assert request["planner_source"] == "deterministic_safety_net"


@pytest.mark.asyncio
async def test_empty_model_plan_still_allows_routine_public_notice_without_roll() -> None:
    instance = make_instance()
    instance.action_queue = [{"user_id": "p1", "text": "阅读车站外公开告示栏"}]
    rule = RuleSystem.load(Path("templates/rules/freeform_coc.json"))

    planned, _metadata = await plan_round_checks(instance, rule, _EmptyPlannerClient())

    assert planned == []


@pytest.mark.asyncio
async def test_empty_model_plan_cannot_skip_hidden_clue_search() -> None:
    instance = make_instance()
    instance.players["p1"]["character_sheet"] = {
        "attributes": {"int": 60, "dex": 50},
        "skills": [{"name": "侦查", "value": 55}],
    }
    instance.action_queue = [{"user_id": "p1", "text": "寻找货仓里的暗室入口"}]
    rule = RuleSystem.load(Path("templates/rules/freeform_coc.json"))

    planned, _metadata = await plan_round_checks(instance, rule, _EmptyPlannerClient())

    assert len(planned) == 1
    assert planned[0][1]["planner_source"] == "deterministic_safety_net"




def _client_returning(arguments: dict) -> object:
    class _Client:
        async def call_tools(self, *_args, **_kwargs):
            return SimpleNamespace(
                tool_calls=[{"name": "dice_checks", "arguments": arguments}],
                total_tokens=1,
                provider_used="fake",
                native_tools=True,
            )
    return _Client()


@pytest.mark.asyncio
async def test_plan_round_checks_normalizes_economy_offers() -> None:
    instance = make_instance()
    # 中性行动，避免确定性安全网产生无关检定。
    instance.action_queue = [
        {"user_id": "p1", "text": "我在大厅坐着休息"},
        {"user_id": "p2", "text": "我翻看任务板"},
    ]
    planned, metadata = await plan_round_checks(
        instance, make_rule(),
        _client_returning({"checks": [], "economy_actions": [
            {
                "player": "阿岚", "type": "purchase", "target": "长剑",
                "amount": 50, "price_source": "gm_narrated", "note": "矮人摊主报价",
            },
            {
                "player": "p2", "type": "purchase", "target": "口粮",
                "amount": 3, "price_source": "player_stated",
            },
        ]}),
    )
    assert planned == []
    assert metadata["errors"] == []
    assert metadata["economy_offers"] == [
        {"payer_uid": "p1", "amount": 50, "quantity": 1, "amount_scope": "total", "target": "长剑", "note": "矮人摊主报价"},
        {"payer_uid": "p2", "amount": 3, "quantity": 1, "amount_scope": "total", "target": "口粮", "note": ""},
    ]


@pytest.mark.asyncio
async def test_economy_offer_multiplies_unit_price_by_explicit_quantity() -> None:
    instance = make_instance()
    instance.action_queue = [{"user_id": "p1", "text": "我要5瓶治疗药水"}]
    planned, metadata = await plan_round_checks(
        instance, make_rule(),
        _client_returning({"checks": [], "economy_actions": [
            {
                "player": "p1", "type": "purchase", "target": "治疗药水",
                "amount": 30, "quantity": 5, "amount_scope": "unit",
                "price_source": "gm_narrated",
            },
        ]}),
    )
    assert planned == []
    assert metadata["errors"] == []
    assert metadata["economy_offers"] == [{
        "payer_uid": "p1", "amount": 150, "quantity": 5,
        "amount_scope": "unit", "target": "治疗药水", "note": "",
    }]


@pytest.mark.asyncio
async def test_economy_offer_without_stated_price_is_safely_skipped() -> None:
    instance = make_instance()
    planned, metadata = await plan_round_checks(
        instance, make_rule(),
        _client_returning({"checks": [], "economy_actions": [
            {"player": "p1", "type": "purchase", "target": "长剑", "price_source": "none"},
            {"player": "p1", "type": "purchase", "target": "长剑"},
            {"player": "p1", "type": "purchase", "target": "长剑", "amount": 50},
        ]}),
    )
    # none / 缺 amount → 跳过且不报错；有 amount 却没有 price_source → 拒绝。
    assert metadata["economy_offers"] == []
    assert metadata["errors"] == ["economy_actions[2] price_source='' 无效"]


@pytest.mark.asyncio
async def test_economy_offer_rejects_unknown_player_and_type() -> None:
    instance = make_instance()
    planned, metadata = await plan_round_checks(
        instance, make_rule(),
        _client_returning({"checks": [], "economy_actions": [
            {"player": "幽灵", "type": "purchase", "target": "长剑",
             "amount": 5, "price_source": "gm_narrated"},
            {"player": "p1", "type": "sell", "target": "长剑",
             "amount": 5, "price_source": "gm_narrated"},
        ]}),
    )
    assert metadata["economy_offers"] == []
    assert metadata["errors"] == [
        "economy_actions[0] player 不存在",
        "economy_actions[1] type 仅支持 purchase",
    ]


@pytest.mark.asyncio
async def test_economy_offers_queue_payer_confirmed_proposals_idempotently() -> None:
    """同一轮重复规划经稳定 source_ref 幂等，不产生重复提案。"""
    from src.engine.economy import queue_purchase_offer

    instance = make_instance()
    source_ref = f"ai:{instance.run_id}:1:p1:长剑"
    first = queue_purchase_offer(
        instance, payer_uid="p1", amount=50, items=["长剑"],
        reason="矮人摊主报价", source="table_offer", source_ref=source_ref,
    )
    second = queue_purchase_offer(
        instance, payer_uid="p1", amount=50, items=["长剑"],
        reason="矮人摊主报价", source="table_offer", source_ref=source_ref,
    )
    assert first["id"] == second["id"]
    assert [p["status"] for p in instance.economy["proposals"]] == ["pending"]
    assert first["approval_policy"] == "payer"
    assert first["source"] == "table_offer"
    assert [reward["name"] for reward in first["rewards"]] == ["长剑"]
    bulk = queue_purchase_offer(
        instance, payer_uid="p1", amount=150, items=["治疗药水"] * 5,
        reason="30 金币一瓶，共五瓶", source="table_offer", source_ref="ai:bulk",
    )
    assert len(bulk["rewards"]) == 5
    with pytest.raises(ValueError):
        queue_purchase_offer(
            instance, payer_uid="p1", amount=50, items=["长剑"],
            source="narrative", source_ref="ai:other",
        )


def test_economy_tool_schema_requires_price_provenance() -> None:
    from src.llm.tools import DICE_CHECKS_TOOL

    actions = (
        DICE_CHECKS_TOOL["function"]["parameters"]["properties"]["economy_actions"]
    )
    props = actions["items"]["properties"]
    assert props["price_source"]["enum"] == ["player_stated", "gm_narrated", "none"]
    assert props["quantity"]["maximum"] == 8
    assert props["amount_scope"]["enum"] == ["unit", "total"]
    assert actions["items"]["required"] == ["player", "type", "target"]
    assert "never invent, estimate, or infer a price" in props["price_source"]["description"]


# ---------------------------------------------------------------------------
# 任务三：消除检定重复惩罚（同一情境事实只能进入一条渠道）
#
# 固定骰值、固定规则与角色卡，不依赖任何模型输出：normalize_check_specs 负责
# 渠道归一，resolve_check_request 负责按请求结算，两层都断言审计字段。
# ---------------------------------------------------------------------------


def _dnd5e_rule() -> RuleSystem:
    # dc_table.normal = 15，即“任务本身难度”的基准 DC。
    return RuleSystem.load(ROOT / "templates" / "rules" / "dnd5e.json")


def _door_instance(*, strength: int = 16) -> GameInstance:
    instance = GameInstance(game_key=("web", "room", "channels"), rule_id="dnd5e")
    instance.players = {
        "p1": {
            "user_id": "p1",
            "character_name": "阿岚",
            "character_sheet": {
                "attributes": {"str": strength, "dex": 12},
                "skills": [],
                "level": 1,
            },
        },
    }
    instance.action_queue = [{"user_id": "p1", "text": "我用力推开沉重的石门"}]
    return instance


def _plan_one(instance: GameInstance, rule: RuleSystem, raw: dict) -> dict:
    planned, errors = normalize_check_specs(instance, rule, [raw])
    assert errors == []
    assert len(planned) == 1
    return planned[0][1]


def _resolve(instance: GameInstance, rule: RuleSystem, request: dict, *, roll: int,
             rolls: list[int] | None = None) -> dict:
    action = {
        "user_id": str(request["actor_uid"]),
        "text": "我用力推开沉重的石门",
        "check_request": request,
        "dice_value": roll,
        "dice_rolls": list(rolls or [roll]),
    }
    result = resolve_check_request(instance, action, rule)
    assert result is not None
    return result


def test_planned_check_without_duplicates_keeps_plain_numbers() -> None:
    """基线：力量 16（+3）、DC 15、modifier 0、normal → total = roll + 3。"""
    instance = _door_instance()
    rule = _dnd5e_rule()
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 15,
        "dc_reason": "石门本身沉重，需要持续发力",
    })

    assert request["target"] == 15
    assert request["circumstance_modifier"] == 0
    assert request["advantage_mode"] == ""
    assert request["dc_reason"] == "石门本身沉重，需要持续发力"
    assert request["planner_notes"] == []
    assert request["planner_dropped"] == {}

    check = _resolve(instance, rule, request, roll=10)
    assert check["modifier"] == 3
    assert check["total"] == 13
    assert check["dc"] == 15
    assert check["advantage_mode"] == ""
    assert check["planner_notes"] == []
    assert "单一渠道" not in (check["modifier_breakdown"] or "")


def test_disadvantage_with_reason_changes_only_the_roll_mode() -> None:
    instance = _door_instance()
    rule = _dnd5e_rule()
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 15,
        "dc_reason": "石门本身沉重，需要持续发力",
        "advantage": "disadvantage",
        "advantage_reason": "站在齐膝深的泥水里难以发力",
    })

    assert request["advantage_mode"] == "disadvantage"
    assert request["advantage_reason"] == "站在齐膝深的泥水里难以发力"
    assert request["target"] == 15
    assert request["circumstance_modifier"] == 0
    assert request["planner_notes"] == []

    # 劣势固定取 min(rolls)：取值错误会被结算层拒绝。
    with pytest.raises(ValueError):
        _resolve(instance, rule, request, roll=17, rolls=[4, 17])
    check = _resolve(instance, rule, request, roll=4, rolls=[4, 17])
    assert check["roll"] == 4
    assert check["advantage_mode"] == "disadvantage"
    assert check["modifier"] == 3
    assert check["total"] == 7
    assert check["dc"] == 15


def test_modifier_breakdown_lists_attribute_skill_and_circumstance_sources() -> None:
    """明细要能从总额倒推：属性加值 + 熟练加值 + 情境修正。

    否则卡面会出现「modifier=+5」但明细只写「熟练加值 +2」的对不上账情况。
    """
    instance = _door_instance(strength=16)
    sheet = instance.players["p1"]["character_sheet"]
    sheet["attributes"] = {"str": 16, "dex": 12, "wis": 16}
    sheet["skills"] = [{"name": "察觉", "value": 5}]
    rule = _dnd5e_rule()

    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "wis", "skill": "察觉", "target": 15,
        "dc_reason": "泥浆下透光处被泥层与气泡遮蔽，属困难观察",
    })
    check = _resolve(instance, rule, request, roll=7)

    assert check["modifier"] == 5  # 感知 +3 + 察觉熟练 +2
    assert check["modifier_breakdown"] == "感知加值 +3；熟练加值 +2"
    assert check["total"] == 12  # 7 + 5，与明细一致

    stacked = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 15,
        "dc_reason": "石门本身沉重，需要持续发力",
        "modifier": -2,
        "modifier_reason": "火把熄灭后只能摸黑发力",
    })
    stacked_check = _resolve(instance, rule, stacked, roll=10)
    assert stacked_check["modifier"] == 1  # 力量 +3 -2
    assert stacked_check["modifier_breakdown"] == "力量加值 +3；情境修正 -2"


def test_independent_environment_modifier_stacks_on_the_dc() -> None:
    instance = _door_instance()
    rule = _dnd5e_rule()
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 15,
        "dc_reason": "石门本身沉重，需要持续发力",
        "modifier": -2,
        "modifier_reason": "火把熄灭后只能摸黑发力",
    })

    assert request["target"] == 15
    assert request["circumstance_modifier"] == -2
    assert request["modifier_reason"] == "火把熄灭后只能摸黑发力"
    assert request["planner_notes"] == []

    check = _resolve(instance, rule, request, roll=10)
    assert check["modifier"] == 1  # +3 -2
    assert check["total"] == 11
    assert check["dc"] == 15
    assert "情境修正 -2" in check["modifier_breakdown"]


def test_same_fact_in_three_channels_keeps_only_the_disadvantage() -> None:
    """同一事实写进 DC + 劣势 + modifier：只保留劣势，DC 回基线、modifier 归零。"""
    instance = _door_instance()
    rule = _dnd5e_rule()
    fact = "光线昏暗难以看清门闩"
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 18,
        "dc_reason": fact,
        "advantage": "disadvantage",
        "advantage_reason": f"{fact}！",
        "modifier": -6,
        "modifier_reason": f"  {fact.replace('难以', ', 难以')}  ",
    })

    assert request["target"] == 15  # 回到难度基准 DC
    assert request["circumstance_modifier"] == 0
    assert request["advantage_mode"] == "disadvantage"
    assert request["planner_notes"] == ["same_fact_as_advantage", "same_fact_as_advantage"]
    assert request["planner_dropped"] == {"target": 18, "modifier": -6}
    # 审计字段保留模型给出的原始理由，说明为何被折叠。
    assert request["dc_reason"] == fact
    assert request["modifier_reason"].strip().startswith("光线昏暗")

    check = _resolve(instance, rule, request, roll=4, rolls=[4, 17])
    assert check["modifier"] == 3
    assert check["dc"] == 15
    assert check["advantage_mode"] == "disadvantage"
    assert check["total"] == 7
    assert check["planner_notes"] == ["same_fact_as_advantage", "same_fact_as_advantage"]
    assert check["dc_reason"] == fact
    assert check["advantage_reason"] == f"{fact}！"
    assert "情境修正存在重复或无依据的计入项" in check["modifier_breakdown"]


def test_reason_comparison_uses_the_full_text_before_persisting_the_160_char_cap() -> None:
    """同源比较用完整理由；写入 request 的字段按 schema 声明截断到 160 字符。"""
    instance = _door_instance()
    rule = _dnd5e_rule()
    fact = "光线昏暗难以看清门闩" * 30  # 300 字符，超过 schema 的 maxLength=160
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 18,
        "dc_reason": fact,
        "advantage": "disadvantage",
        "advantage_reason": fact,
        "modifier": -6,
        "modifier_reason": fact,
    })

    # 截断发生在策略之后：300 字符的同一理由依然被判为同源并折叠。
    assert request["planner_notes"] == ["same_fact_as_advantage", "same_fact_as_advantage"]
    assert request["planner_dropped"] == {"target": 18, "modifier": -6}
    assert request["target"] == 15
    assert request["circumstance_modifier"] == 0
    for key in ("dc_reason", "advantage_reason", "modifier_reason"):
        assert len(request[key]) == 160
        assert request[key] == fact[:160]


def test_reasons_differing_only_after_160_chars_are_not_the_same_fact() -> None:
    """截断只在持久化时发生：160 字符之后才不同的理由不算同一事实。"""
    instance = _door_instance()
    rule = _dnd5e_rule()
    shared = "光线昏暗难以看清门闩" * 16  # 160 字符
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 18,
        "dc_reason": f"{shared}{'甲' * 20}",
        "modifier": -3,
        "modifier_reason": f"{shared}{'乙' * 20}",
    })

    assert request["planner_notes"] == []
    assert request["planner_dropped"] == {}
    assert request["target"] == 18
    assert request["circumstance_modifier"] == -3
    assert len(request["dc_reason"]) == 160
    assert len(request["modifier_reason"]) == 160


def test_reasonless_modifier_is_zeroed_but_reasonless_advantage_survives() -> None:
    """非零 modifier 缺理由 → 归零；advantage 缺理由 → 保留，只记审计提示。"""
    instance = _door_instance()
    rule = _dnd5e_rule()
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 15,
        "modifier": -4,
        "advantage": "advantage",
    })

    assert request["circumstance_modifier"] == 0
    assert request["advantage_mode"] == "advantage"
    assert request["planner_notes"] == [
        "modifier_without_reason", "advantage_without_reason",
    ]
    assert request["planner_dropped"] == {"modifier": -4}

    # advantage 仍按两枚 d20 取高结算；modifier 的归零是 material note，
    # 因此 breakdown 会出现说明行（advantage 的提示本身不会触发它）。
    check = _resolve(instance, rule, request, roll=10, rolls=[10, 4])
    assert check["roll"] == 10
    assert check["modifier"] == 3
    assert check["total"] == 13
    assert check["advantage_mode"] == "advantage"
    assert check["planner_notes"] == [
        "modifier_without_reason", "advantage_without_reason",
    ]
    assert "情境修正存在重复或无依据的计入项" in check["modifier_breakdown"]


def test_hard_task_dc_and_independent_penalty_with_distinct_reasons_both_survive() -> None:
    instance = _door_instance()
    rule = _dnd5e_rule()
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 18,
        "dc_reason": "门闩锈死，需要持续蛮力",
        "modifier": -2,
        "modifier_reason": "火把熄灭后只能摸黑发力",
    })

    assert request["target"] == 18
    assert request["circumstance_modifier"] == -2
    assert request["planner_notes"] == []
    assert request["planner_dropped"] == {}

    check = _resolve(instance, rule, request, roll=10)
    assert check["dc"] == 18
    assert check["modifier"] == 1
    assert check["total"] == 11


def test_attack_keeps_server_armor_class_and_drops_duplicate_check_penalty() -> None:
    instance = _door_instance()
    instance.npcs = {
        "goblin": {
            "name": "哥布林",
            "character_name": "哥布林",
            "hp": 30,
            "max_hp": 30,
            "armor": 0,
            "attributes": {"dex": 10},
        },
    }
    instance.action_queue = [{"user_id": "p1", "text": "我挥剑劈向哥布林"}]
    rule = _dnd5e_rule()
    fact = "哥布林举盾格挡，难以命中"
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 20, "kind": "attack",
        "opponent": "哥布林",
        "dc_reason": fact,
        "advantage": "disadvantage",
        "advantage_reason": fact,
        "modifier": -5,
        "modifier_reason": fact,
    })

    assert request["opponent"] == "npc:goblin"
    assert request["target"] == 15
    assert request["circumstance_modifier"] == 0
    assert request["planner_notes"] == ["same_fact_as_advantage", "same_fact_as_advantage"]
    assert request["planner_dropped"] == {"target": 20, "modifier": -5}

    check = _resolve(instance, rule, request, roll=5, rolls=[5, 18])
    # 命中 DC 仍由服务端 AC 决定：既不是模型 DC，也不叠加检定 DC 惩罚。
    assert check["target_source"] == "server_armor_class"
    assert check["dc"] == 10
    assert check["advantage_mode"] == "disadvantage"
    assert check["modifier"] == rule.attribute_modifier(16) + rule.proficiency_bonus(1)
    assert check["total"] == check["roll"] + check["modifier"]
    # 没有任何情境数值修正进入 AC 命中结算，只留下“重复计入被忽略”的解释行。
    assert "情境修正 -5" not in (check["modifier_breakdown"] or "")
    assert "情境修正 +" not in (check["modifier_breakdown"] or "")
    assert "情境修正存在重复或无依据的计入项" in check["modifier_breakdown"]


@pytest.mark.parametrize("language, expected", [
    ("zh-CN", "情境修正存在重复或无依据的计入项"),
    ("en", "duplicated or unsupported entry"),
    ("ja", "重複または根拠のない計上項目"),
])
def test_duplicate_note_line_follows_the_interface_language(language: str, expected: str) -> None:
    instance = _door_instance()
    instance.language = language
    rule = _dnd5e_rule()
    fact = "光线昏暗难以看清门闩"
    request = _plan_one(instance, rule, {
        "player": "p1", "attribute": "str", "target": 18,
        "dc_reason": fact,
        "modifier": -6,
        "modifier_reason": fact,
    })

    check = _resolve(instance, rule, request, roll=10)
    assert check["planner_notes"] == ["same_fact_as_dc"]
    assert expected in check["modifier_breakdown"]


def test_planner_item_context_is_compact_actor_scoped_and_read_only() -> None:
    instance = make_instance()
    instance.players["p3"] = deepcopy(instance.players["p1"])
    instance.players["p3"]["character_sheet"]["inventory"] = ["未行动者的密信"]
    instance.get_character_sheet("p1").update({
        "equipment": [{"name": "锤子", "type": "tool", "description": "不要传入", "damage": "1d4"}],
        "key_items": ["黄铜钥匙"],
        "inventory": [
            {"name": "绳索", "item_ref": "item:rope", "qty": 2, "quantity": 99, "price": 50},
            {"name": "火把", "quantity": 2},
            "干粮",
        ],
        "background": "不要传入的背景",
    })
    instance.get_character_sheet("p2").update({
        "equipment": [], "key_items": [], "inventory": ["白露的药水"],
    })
    instance.action_queue[0]["text"] = "看看 ITEM: ROPE，再用那把钥匙"
    before = deepcopy((instance.players, instance.action_queue))
    players = json.loads(_planner_context(instance, make_rule()))["players"]
    assert [player["player_id"] for player in players] == ["p1", "p2"]
    assert players[0]["item_context"] == {
        "items": [
            {"source": "inventory", "name": "绳索", "item_ref": "item:rope", "qty": 2},
            {"source": "equipment", "name": "锤子", "type": "tool"},
            {"source": "key_items", "name": "黄铜钥匙"},
            {"source": "inventory", "name": "火把", "qty": 2},
            {"source": "inventory", "name": "干粮"},
        ],
        "partial": False,
    }
    assert players[1]["item_context"]["items"] == [{"source": "inventory", "name": "白露的药水"}]
    assert (instance.players, instance.action_queue) == before


@pytest.mark.parametrize("quantity_fields", [
    {"qty": 0}, {"qty": -1}, {"quantity": 0}, {"quantity": -2},
    {"qty": 0, "quantity": 99},
])
def test_planner_excludes_nonpositive_inventory_even_when_named(quantity_fields) -> None:
    instance = make_instance()
    instance.get_character_sheet("p1").update({
        "equipment": [], "key_items": [],
        "inventory": [{"name": "绳索", **quantity_fields}, {"name": "火把", "qty": 1}],
    })
    instance.action_queue[0]["text"] = "使用绳索"
    before = deepcopy(instance.players)
    context = json.loads(_planner_context(instance, None))["players"][0]["item_context"]
    assert context["items"] == [{"source": "inventory", "name": "火把", "qty": 1}]
    assert context["partial"] is True
    assert instance.players == before


@pytest.mark.parametrize("size", [20, 21])
def test_planner_large_inventory_only_includes_explicit_matches(size: int) -> None:
    instance = make_instance()
    instance.get_character_sheet("p1").update({
        "equipment": [], "key_items": [],
        "inventory": [f"物品{i:02}" for i in range(size - 1)] + ["铜钥匙"],
    })
    instance.action_queue[0].update(text="仔细看它", target_text="铜钥匙")
    context = json.loads(_planner_context(instance, None))["players"][0]["item_context"]
    assert context["items"][0]["name"] == "铜钥匙"
    assert len(context["items"]) == (20 if size == 20 else 1)
    assert context["partial"] is (size > 20)


def test_planner_item_limits_preserve_priority_and_whole_entries() -> None:
    instance = make_instance()
    sheet = instance.get_character_sheet("p1")
    sheet.update({
        "equipment": [f"装备{i:02}" for i in range(21)],
        "key_items": ["钥匙"], "inventory": ["目标物"],
    })
    instance.action_queue[0]["text"] = "使用目标物"
    context = json.loads(_planner_context(instance, None))["players"][0]["item_context"]
    assert len(context["items"]) == 20
    assert [row["name"] for row in context["items"]] == ["目标物", *sheet["equipment"][:19]]
    assert context["partial"] is True

    sheet.update({"equipment": ["长" * 1500, *["工具" + str(i) + "细" * 200 for i in range(9)]],
                  "key_items": [], "inventory": ["目标物"]})
    context = json.loads(_planner_context(instance, None))["players"][0]["item_context"]
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 1500
    assert context["partial"] is True
    assert context["items"][0]["name"] == "目标物"
    assert all(row["name"] in sheet[row["source"]] for row in context["items"])
    assert "长" * 1500 not in [row["name"] for row in context["items"]]


def test_planner_missing_and_malformed_items_are_partial_without_invented_values() -> None:
    instance = make_instance()
    assert json.loads(_planner_context(instance, None))["players"][0]["item_context"] == {
        "items": [], "partial": True,
    }
    instance.get_character_sheet("p1").update({
        "equipment": {"name": "不是清单"}, "key_items": None,
        "inventory": [None, 12, {}, " ", {"name": []},
                      {"name": "药水", "qty": "2", "quantity": 3, "type": {}},
                      {"name": "干粮", "qty": True}, {"item_ref": "item:rope"}],
    })
    context = json.loads(_planner_context(instance, None))["players"][0]["item_context"]
    assert context == {"partial": True, "items": [
        {"source": "inventory", "name": "药水"},
        {"source": "inventory", "name": "干粮"},
        {"source": "inventory", "item_ref": "item:rope"},
    ]}


@pytest.mark.parametrize(("target", "action", "expected"), [
    ("keeper", "询问另一人", "keeper"),
    ("老汤姆", "看看周围", "keeper"),
    ("主任", "看看周围", "professor"),
    ("", "问老汤姆旅店是否营业", "keeper"),
    ("", "问keeper旅店是否营业", "keeper"),
    ("不存在", "询问老汤姆", None),
    ("敌人", "询问老汤姆", None),
    ("", "看看周围", None),
    ("", "攻击敌人", None),
    ("白露", "询问老汤姆", None),
])
def test_planner_npc_context_requires_an_explicit_resolved_npc(target, action, expected) -> None:
    instance = make_instance()
    instance.npcs = {
        "keeper": {"name": "老汤姆", "relation": "friendly", "hp": 20, "description": "长篇背景"},
        "professor": {"character_name": "考古学系主任"},
    }
    instance.scene = "老汤姆和考古学系主任所在的旅店"
    instance.combat_state = "active"
    instance.combat_enemies = [{"name": "强盗", "hp": 10}]
    instance.action_queue[0].update(text=action, target_text=target)
    before = deepcopy(instance.npcs)
    player = json.loads(_planner_context(instance, None))["players"][0]
    if expected == "keeper":
        assert player["npc_context"] == {
            "reference": "npc:keeper", "name": "老汤姆", "relation": "friendly",
        }
    elif expected == "professor":
        assert player["npc_context"] == {"reference": "npc:professor", "name": "考古学系主任"}
    else:
        assert "npc_context" not in player
    assert instance.npcs == before


@pytest.mark.parametrize(("target", "action", "names"), [
    ("守卫", "询问守卫", ["守卫", "守卫"]),
    ("", "询问守卫", ["守卫", "守卫"]),
    ("主任", "询问守卫", ["考古学系主任", "历史系主任"]),
])
def test_planner_npc_context_rejects_ambiguous_names(target, action, names) -> None:
    instance = make_instance()
    instance.npcs = {f"npc{i}": {"name": name} for i, name in enumerate(names)}
    instance.action_queue[0].update(text=action, target_text=target)
    player = json.loads(_planner_context(instance, None))["players"][0]
    assert "npc_context" not in player


@pytest.mark.parametrize(("action", "target", "expected"), [
    ("问教授老汤姆在哪里", "", None),
    ("问老汤姆教授在哪里", "", None),
    ("问教授老汤姆在哪里", "教授", "professor"),
    ("问教授老汤姆在哪里", "老汤姆", "keeper"),
    ("问教授老汤姆在哪里", "不存在", None),
    ("问教授，professor能帮忙吗", "", "professor"),
])
def test_planner_requires_explicit_target_when_action_names_multiple_npcs(action, target, expected) -> None:
    instance = make_instance()
    instance.npcs = {
        "professor": {"name": "教授", "relation": "neutral"},
        "keeper": {"name": "老汤姆", "relation": "friendly"},
    }
    instance.action_queue[0].update(text=action, target_text=target)
    player = json.loads(_planner_context(instance, None))["players"][0]
    if expected is None:
        assert "npc_context" not in player
    else:
        assert player["npc_context"] == {
            "reference": f"npc:{expected}", **instance.npcs[expected],
        }


def test_planner_npc_context_drops_oversized_fields_without_truncating_identity() -> None:
    instance = make_instance()
    instance.npcs = {"keeper": {"name": "老汤姆", "relation": "长" * 401}}
    instance.action_queue[0]["target_text"] = "keeper"
    context = json.loads(_planner_context(instance, None))["players"][0]["npc_context"]
    assert context == {"reference": "npc:keeper", "name": "老汤姆"}
    instance.npcs["keeper"]["name"] = "长" * 401
    assert "npc_context" not in json.loads(_planner_context(instance, None))["players"][0]


@pytest.mark.asyncio
async def test_new_context_reaches_single_planner_call_without_changing_safety_net() -> None:
    instance = make_instance()
    instance.action_queue = [{"user_id": "p1", "text": "询问老汤姆", "selected_attribute": "str"}]
    instance.get_character_sheet("p1").update({
        "equipment": [], "key_items": ["旅店钥匙"], "inventory": [],
    })
    instance.npcs = {"keeper": {"name": "老汤姆", "relation": "friendly"}}

    class CapturingClient:
        calls = 0

        async def call_tools(self, prompt, context, **kwargs):
            self.calls += 1
            player = json.loads(context)["players"][0]
            assert player["item_context"]["items"][0]["name"] == "旅店钥匙"
            assert player["npc_context"]["reference"] == "npc:keeper"
            assert kwargs["tools"][0]["function"]["name"] == "dice_checks"
            return SimpleNamespace(
                tool_calls=[{"name": "dice_checks", "arguments": {"checks": []}}],
                total_tokens=10, provider_used="fake", native_tools=True,
            )

    client = CapturingClient()
    planned, metadata = await plan_round_checks(instance, make_rule(), client)
    assert client.calls == 1
    assert metadata["errors"] == []
    assert len(planned) == 1
    assert planned[0][1]["planner_source"] == "deterministic_safety_net"
