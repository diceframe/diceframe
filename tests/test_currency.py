"""Currency Model V2：CurrencySpec / CurrencyCodec / 校验 / legacy 兼容测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.currency import (
    CurrencySystemError,
    format_currency_amount,
    legacy_currency_spec,
    parse_currency_amount,
    validate_currency_system,
)
from src.engine.currency.validation import is_v2_currency_system
from src.rules.loader import RuleBundleLoader
from src.rules.rule_system import RuleSystem

DOLLAR_SPEC = validate_currency_system({
    "schema_version": 2,
    "base_unit": "cent",
    "display_unit": "dollar",
    "units": [
        {"id": "dollar", "name": "美元", "symbol": "$", "rate": 100},
        {"id": "cent", "name": "美分", "rate": 1},
    ],
})

YUAN_SPEC = validate_currency_system({
    "schema_version": 2,
    "base_unit": "fen",
    "display_unit": "yuan",
    "units": [
        {"id": "yuan", "name": "人民币", "symbol": "¥", "rate": 100},
        {"id": "fen", "name": "分", "rate": 1},
    ],
})

SPIRIT_SPEC = legacy_currency_spec("灵石")


# ===== CurrencyCodec.parse_currency_amount =====

@pytest.mark.parametrize("value,unit,expected", [
    ("0.25", "dollar", 25),
    ("1", "dollar", 100),
    ("1.00", "dollar", 100),
    ("12.50", "yuan", 1250),
    ("25", "cent", 25),
    ("3", "unit", 3),
    (25, "cent", 25),
    (" 0.25 ", "dollar", 25),  # strip 后可解析
])
def test_parse_converts_to_canonical_base_units(value, unit, expected):
    if unit in {"dollar", "cent"}:
        spec = DOLLAR_SPEC
    elif unit in {"yuan", "fen"}:
        spec = YUAN_SPEC
    else:
        spec = SPIRIT_SPEC
    assert parse_currency_amount(value, unit, spec) == expected


@pytest.mark.parametrize("value,unit,spec", [
    ("0.001", "dollar", DOLLAR_SPEC),   # 0.1 cent → 拒绝
    ("0.5", "unit", SPIRIT_SPEC),       # 0.5 灵石 → 拒绝
    ("unknown", "dollar", DOLLAR_SPEC),  # 未知单位
    ("-25", "cent", DOLLAR_SPEC),       # 负数
    ("0", "cent", DOLLAR_SPEC),         # 零
    ("NaN", "cent", DOLLAR_SPEC),
    ("Infinity", "cent", DOLLAR_SPEC),
    ("1e2", "cent", DOLLAR_SPEC),       # 科学计数法
    ("1,000", "cent", DOLLAR_SPEC),     # 千分位
    (0.5, "cent", DOLLAR_SPEC),         # float 输入直接拒绝
    (True, "cent", DOLLAR_SPEC),
])
def test_parse_rejects_invalid_input(value, unit, spec):
    with pytest.raises(CurrencySystemError):
        parse_currency_amount(value, unit, spec)


# ===== CurrencyCodec.format_currency_amount =====

def test_format_dollar_displays_symbol_with_two_decimals():
    assert format_currency_amount(25, DOLLAR_SPEC) == "$0.25"
    assert format_currency_amount(100, DOLLAR_SPEC) == "$1.00"
    assert format_currency_amount(0, DOLLAR_SPEC) == "$0.00"
    assert format_currency_amount(100250, DOLLAR_SPEC) == "$1002.50"


def test_format_yuan_displays_symbol():
    assert format_currency_amount(1250, YUAN_SPEC) == "¥12.50"
    assert format_currency_amount(100, YUAN_SPEC) == "¥1.00"


def test_format_legacy_single_unit():
    assert format_currency_amount(25, SPIRIT_SPEC) == "25 灵石"
    assert format_currency_amount(0, SPIRIT_SPEC) == "0 灵石"


def test_format_multi_unit_greedy_decomposition():
    spec = validate_currency_system({
        "schema_version": 2,
        "base_unit": "low",
        "display_unit": "low",
        "units": [
            {"id": "high", "name": "上品灵石", "rate": 10000},
            {"id": "middle", "name": "中品灵石", "rate": 100},
            {"id": "low", "name": "下品灵石", "rate": 1},
        ],
    })
    assert format_currency_amount(12500, spec) == "1 上品灵石 25 中品灵石"
    assert format_currency_amount(25, spec) == "25 下品灵石"
    assert format_currency_amount(12550, spec) == "1 上品灵石 25 中品灵石 50 下品灵石"


# ===== validate_currency_system =====

def test_validate_rejects_structural_errors():
    cases = [
        {"schema_version": 2, "base_unit": "x", "units": []},
        "not a dict",
        {"schema_version": 1, "base_unit": "cent", "units": [{"id": "cent", "name": "分", "rate": 1}]},
        {"schema_version": 2, "base_unit": "gold", "units": [
            {"id": "gold", "name": "金", "rate": 100}, {"id": "silver", "name": "银", "rate": 1},
        ]},
        {"schema_version": 2, "base_unit": "cent", "display_unit": "yuan", "units": [
            {"id": "cent", "name": "分", "rate": 1},
        ]},
        {"schema_version": 2, "base_unit": "a", "units": [
            {"id": "a", "name": "a", "rate": 1}, {"id": "a", "name": "a2", "rate": 2},
        ]},
        {"schema_version": 2, "base_unit": "a", "units": [{"id": "a", "name": "a", "rate": 1.5}]},
        {"schema_version": 2, "base_unit": "a", "units": [{"id": "a", "name": "a", "rate": 0}]},
        {"schema_version": 2, "base_unit": "a", "units": [{"id": "a", "name": "a", "rate": -5}]},
        {"schema_version": 2, "base_unit": "a", "units": [{"id": "", "name": "a", "rate": 1}]},
        {"schema_version": 2, "base_unit": "a", "units": [{"id": "a", "rate": 1}]},
    ]
    for raw in cases:
        with pytest.raises(CurrencySystemError):
            validate_currency_system(raw)


def test_validate_defaults_display_unit_to_base():
    spec = validate_currency_system({
        "schema_version": 2,
        "base_unit": "unit",
        "units": [{"id": "unit", "name": "信用点", "rate": 1}],
    })
    assert spec.display_unit == "unit"
    assert spec.display.name == "信用点"


# ===== Legacy 兼容 =====

def test_legacy_spec_does_not_guess_units():
    """旧 currency="人民币" 仍是 1 amount = 1 人民币，不得自动变成 1 分。"""

    spec = legacy_currency_spec("人民币")
    assert spec.base_unit == "unit"
    assert spec.units[0].name == "人民币"
    assert spec.units[0].rate == 1
    assert format_currency_amount(100, spec) == "100 人民币"


def test_legacy_v1_dict_passthrough():
    spec = legacy_currency_spec("金币", {
        "base_unit": "credit",
        "units": [{"id": "credit", "name": "Credit", "rate": 1}],
    })
    assert spec.base_unit == "credit"
    assert spec.units[0].name == "Credit"


def test_is_v2_detection():
    assert is_v2_currency_system({"schema_version": 2, "units": []})
    assert not is_v2_currency_system({"base_unit": "gold"})
    assert not is_v2_currency_system("x")
    assert not is_v2_currency_system(None)


# ===== RuleSystem 集成 =====

def test_rule_system_legacy_currency_spec():
    rule = RuleSystem.load("templates/rules/freeform_wuxia.json")
    spec = rule.currency_spec
    assert spec.schema_version == 1
    assert spec.base_unit == "unit"
    assert spec.units[0].name == "灵石"
    assert rule.currency_system["units"][0]["name"] == "灵石"


def test_rule_system_v2_currency_spec(tmp_path):
    (tmp_path / "v2_rule.json").write_text(json.dumps({
        "rule_id": "v2_rule",
        "rule_name": "V2",
        "currency": "美元",
        "currency_system": {
            "schema_version": 2,
            "base_unit": "cent",
            "display_unit": "dollar",
            "units": [
                {"id": "dollar", "name": "美元", "symbol": "$", "rate": 100},
                {"id": "cent", "name": "美分", "rate": 1},
            ],
        },
    }, ensure_ascii=False), encoding="utf-8")
    rule = RuleSystem.load(tmp_path / "v2_rule.json")
    spec = rule.currency_spec
    assert spec.schema_version == 2
    assert spec.base_unit == "cent"
    assert parse_currency_amount("0.25", "dollar", spec) == 25
    assert rule.currency_system["display_unit"] == "dollar"


def test_rule_system_rejects_invalid_v2_currency_system(tmp_path):
    (tmp_path / "bad_rule.json").write_text(json.dumps({
        "rule_id": "bad_rule",
        "rule_name": "Bad",
        "currency_system": {
            "schema_version": 2,
            "base_unit": "missing",
            "units": [{"id": "cent", "name": "分", "rate": 1}],
        },
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="base_unit"):
        RuleSystem.load(tmp_path / "bad_rule.json")


def test_bundle_loader_rejects_invalid_v2_currency_system(tmp_path):
    (tmp_path / "bad_bundle.json").write_text(json.dumps({
        "rule_schema_version": 2,
        "rule_id": "bad_bundle",
        "currency_system": {
            "schema_version": 2,
            "base_unit": "a",
            "units": [{"id": "a", "name": "a", "rate": 0}],
        },
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="rate"):
        RuleBundleLoader().load(tmp_path / "bad_bundle.json")


def test_builtin_coc_rule_is_dollar_cent():
    rule = RuleSystem.load("templates/rules/freeform_coc.json")
    spec = rule.currency_spec
    assert spec.schema_version == 2
    assert spec.base_unit == "cent"
    assert spec.display_unit == "dollar"
    assert parse_currency_amount("0.25", "dollar", spec) == 25


def test_builtin_legacy_rules_keep_rate_one():
    for rule_id in ("freeform_fantasy", "freeform_wuxia", "dnd5e", "freeform_cyberpunk"):
        rule = RuleSystem.load(f"templates/rules/{rule_id}.json")
        spec = rule.currency_spec
        assert spec.schema_version == 1
        assert spec.base_unit == "unit"
        assert spec.units[0].rate == 1


def test_rule_locale_must_not_change_currency(tmp_path):
    """Locale 物化不得改变 canonical 货币结构（identity/mechanics 不变）。"""

    system = RuleBundleLoader().load(Path("templates/rules/freeform_wuxia.json"))
    spec = legacy_currency_spec(system.get("currency", "灵石"), system.get("currency_system"))
    assert spec.units[0].rate == 1


# ===== GM 指令（小数货币 + 单位别名） =====

class _GmInstance:
    """_parse_gm_resource_change 所需的最小实例桩。"""

    def __init__(self) -> None:
        self.players = {"p1": {"character_name": "张三"}}
        self.sheet = {"gold": 1000, "currency": {"amount": 1000}}

    def get_character_sheet(self, uid):
        return self.sheet

    def set_character_sheet(self, uid, sheet):
        self.sheet = sheet


def test_gm_command_decimal_currency_with_units():
    from src.webui.services.game_master import _parse_gm_resource_change

    coc = RuleSystem.load("templates/rules/freeform_coc.json")

    result = _parse_gm_resource_change(_GmInstance(), "给张三增加0.25美元", coc)
    assert result["requested_delta"] == 25
    assert result["before"] == 1000 and result["after"] == 1025
    assert result["display_after"] == "$10.25"

    result = _parse_gm_resource_change(_GmInstance(), "给张三增加25美分", coc)
    assert result["requested_delta"] == 25

    result = _parse_gm_resource_change(_GmInstance(), "扣除张三5金币", coc)
    assert result["requested_delta"] == -500
    assert result["display_after"] == "$5.00"

    result = _parse_gm_resource_change(_GmInstance(), "给张三增加5金币", coc)
    assert result["requested_delta"] == 500

    result = _parse_gm_resource_change(_GmInstance(), "张三金币+2.50", coc)
    assert result["requested_delta"] == 250


def test_gm_command_integer_currency_stays_canonical():
    from src.webui.services.game_master import _parse_gm_resource_change

    spirit = RuleSystem.load("templates/rules/freeform_wuxia.json")
    instance = _GmInstance()

    result = _parse_gm_resource_change(instance, "给张三增加3灵石", spirit)
    assert result["requested_delta"] == 3

    # 0.5 灵石无法变成整数个下品灵石 → 拒绝。
    result = _parse_gm_resource_change(instance, "给张三增加0.5灵石", spirit)
    assert result == {"error": "金额 0.5 灵石 无法精确转换为整数个 灵石"}

    # 存量语序（单位在前）继续工作。
    result = _parse_gm_resource_change(instance, "给张三加金币5", spirit)
    assert result["requested_delta"] == 5

    # 非货币资源继续走通用资源路径（规则未声明 → 明确报错，不影响货币分支）。
    result = _parse_gm_resource_change(instance, "张三的幸运+1", spirit)
    assert result == {"error": "当前规则没有资源：幸运"}


# ===== 规则 CRUD 校验 =====

def _rules_dependencies(tmp_path: Path):
    from src.webui.services.rules import RuleDependencies

    return RuleDependencies(rules_dir=tmp_path)


def test_save_custom_rule_rejects_invalid_source_currency_system(tmp_path):
    from src.webui.services.rules import save_custom_rule

    (tmp_path / "base.json").write_text(json.dumps({
        "rule_id": "base",
        "rule_name": "Base",
        "currency_system": {
            "schema_version": 2,
            "base_unit": "missing",
            "units": [{"id": "cent", "name": "分", "rate": 1}],
        },
    }, ensure_ascii=False), encoding="utf-8")
    deps = _rules_dependencies(tmp_path)
    result = save_custom_rule(deps, {
        "source_rule_id": "base",
        "rule_id": "bad_currency",
        "rule_name": "Bad",
    })
    assert result["ok"] is False
    assert "currency_system 声明非法" in result["error"]
    assert not (tmp_path / "bad_currency.json").exists()


def test_update_custom_rule_rejects_invalid_currency_system(tmp_path):
    from src.webui.services.rules import update_custom_rule

    (tmp_path / "custom_rule.json").write_text(json.dumps({
        "rule_id": "custom_rule", "rule_name": "Custom", "custom": True,
    }, ensure_ascii=False), encoding="utf-8")
    deps = _rules_dependencies(tmp_path)
    result = update_custom_rule(deps, "custom_rule", {
        "rule_id": "custom_rule",
        "rule_name": "Custom",
        "custom": True,
        "currency_system": {"schema_version": 2, "base_unit": "a", "units": [{"id": "a", "name": "a", "rate": 0}]},
    })
    assert result["ok"] is False
    assert "currency_system 声明非法" in result["error"]


def test_update_custom_rule_blocks_base_unit_semantic_change_with_games(tmp_path):
    from src.webui.services.rules import RuleDependencies, update_custom_rule

    (tmp_path / "custom_rule.json").write_text(json.dumps({
        "rule_id": "custom_rule",
        "rule_name": "Custom",
        "custom": True,
        "currency": "人民币",
    }, ensure_ascii=False), encoding="utf-8")

    def has_games(rule_id: str) -> bool:
        return rule_id == "custom_rule"

    deps = RuleDependencies(rules_dir=tmp_path, has_games_using_rule=has_games)
    result = update_custom_rule(deps, "custom_rule", {
        "rule_id": "custom_rule",
        "rule_name": "Custom",
        "custom": True,
        "currency": "人民币",
        "currency_system": {
            "schema_version": 2,
            "base_unit": "fen",
            "display_unit": "yuan",
            "units": [
                {"id": "yuan", "name": "人民币", "symbol": "¥", "rate": 100},
                {"id": "fen", "name": "分", "rate": 1},
            ],
        },
    })
    assert result["ok"] is False
    assert "base_unit 语义" in result["error"]


def test_update_custom_rule_allows_equivalent_single_unit_with_games(tmp_path):
    from src.webui.services.rules import RuleDependencies, update_custom_rule

    (tmp_path / "custom_rule.json").write_text(json.dumps({
        "rule_id": "custom_rule",
        "rule_name": "Custom",
        "custom": True,
        "currency": "人民币",
    }, ensure_ascii=False), encoding="utf-8")

    def has_games(rule_id: str) -> bool:
        return rule_id == "custom_rule"

    deps = RuleDependencies(rules_dir=tmp_path, has_games_using_rule=has_games)
    result = update_custom_rule(deps, "custom_rule", {
        "rule_id": "custom_rule",
        "rule_name": "Custom",
        "custom": True,
        "currency": "人民币",
        # 数值语义不变的显式单单位（1 amount = 1 人民币）→ 允许。
        "currency_system": {
            "schema_version": 2,
            "base_unit": "yuan",
            "display_unit": "yuan",
            "units": [{"id": "yuan", "name": "人民币", "rate": 1}],
        },
    })
    assert result["ok"] is True


# ===== 支付链路（§34.5）：余额 $10.00 买 $0.25 → 1000 → 975 → UI $9.75 =====

class _EconomyInstance:
    """queue_purchase_offer / resolve_proposal 所需的最小实例桩。"""

    def __init__(self) -> None:
        self.run_id = "run_1"
        self.round_number = 1
        self.gm_uid = "gm"
        self.players = {"p1": {"character_name": "张三"}}
        self.sheet = {"gold": 1000, "currency": {"amount": 1000}}
        self.economy = {
            "proposals": [], "transactions": [], "outcomes": [],
            "idempotency_records": {}, "effect_groups": [], "next_sequence": 1,
        }

    def get_character_sheet(self, uid):
        return self.sheet

    def set_character_sheet(self, uid, sheet):
        self.sheet = sheet


def test_coc_v2_payment_flow_settles_in_base_units():
    from src.engine.currency import format_currency_amount
    from src.engine.economy import queue_purchase_offer, resolve_proposal

    coc = RuleSystem.load("templates/rules/freeform_coc.json")
    instance = _EconomyInstance()

    offer = queue_purchase_offer(
        instance, payer_uid="p1", amount=parse_currency_amount("0.25", "dollar", coc.currency_spec),
        items=["手电筒"], reason="购买手电筒", source="table_offer",
    )
    assert offer["amount"] == 25  # canonical 25 美分

    result = resolve_proposal(instance, offer["id"], actor_uid="p1", accepted=True)
    assert result["ok"] is True
    sheet = instance.get_character_sheet("p1")
    assert sheet["currency"]["amount"] == 975
    assert sheet["gold"] == 975

    # 显示层经 formatter：$9.75，绝不直接展示 canonical 975 当作 975 美元。
    assert format_currency_amount(sheet["currency"]["amount"], coc.currency_spec) == "$9.75"

    # 余额不足：$1 买 $1.01 → 100 < 101 → 拒绝。
    broke = _EconomyInstance()
    broke.sheet = {"gold": 100, "currency": {"amount": 100}}
    expensive = queue_purchase_offer(
        broke, payer_uid="p1",
        amount=parse_currency_amount("1.01", "dollar", coc.currency_spec),
        items=["怀表"], reason="怀表", source="table_offer",
    )
    failed = resolve_proposal(broke, expensive["id"], actor_uid="p1", accepted=True)
    assert failed["ok"] is False
    assert failed["code"] == "INSUFFICIENT_FUNDS"
    assert broke.sheet["currency"]["amount"] == 100


# ===== 非十进制 rate 的展示（rate=3：剩余必须落在 base unit） =====

SILVER_COPPER_SPEC = validate_currency_system({
    "schema_version": 2,
    "base_unit": "copper",
    "display_unit": "silver",
    "units": [
        {"id": "silver", "name": "银币", "rate": 3},
        {"id": "copper", "name": "铜币", "rate": 1},
    ],
})


@pytest.mark.parametrize("amount,expected", [
    (1, "1 铜币"),
    (3, "1 银币"),
    (4, "1 银币 1 铜币"),
    (6, "2 银币"),
    (0, "0 铜币"),
    (2, "2 铜币"),
])
def test_format_non_decimal_rate_uses_mixed_decomposition(amount, expected):
    assert format_currency_amount(amount, SILVER_COPPER_SPEC) == expected


# ===== schema_version fail-fast：显式声明版本必须合法且受支持 =====

@pytest.mark.parametrize("version", ["abc", True, False, 0, 3, "3", 2.5])
def test_validate_rejects_any_declared_but_unsupported_version(version):
    with pytest.raises(CurrencySystemError):
        validate_currency_system({
            "schema_version": version,
            "base_unit": "cent",
            "units": [{"id": "cent", "name": "分", "rate": 1}],
        })


def test_versionless_system_stays_legacy():
    """没有 schema_version 的旧 currency_system 继续 legacy，不误伤。"""

    from src.engine.currency import declares_currency_schema

    raw = {"base_unit": "credit", "units": [{"id": "credit", "name": "Credit", "rate": 1}]}
    assert not declares_currency_schema(raw)
    spec = legacy_currency_spec("金币", raw)
    assert spec.schema_version == 1


@pytest.mark.parametrize("version", ["abc", 3])
def test_rule_system_rejects_declared_unsupported_version(tmp_path, version):
    (tmp_path / "versioned_rule.json").write_text(json.dumps({
        "rule_id": "versioned_rule",
        "rule_name": "Versioned",
        "currency_system": {
            "schema_version": version,
            "base_unit": "cent",
            "units": [{"id": "cent", "name": "分", "rate": 1}],
        },
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        RuleSystem.load(tmp_path / "versioned_rule.json")


@pytest.mark.parametrize("version", ["abc", 3])
def test_bundle_loader_rejects_declared_unsupported_version(tmp_path, version):
    (tmp_path / "versioned_bundle.json").write_text(json.dumps({
        "rule_schema_version": 2,
        "rule_id": "versioned_bundle",
        "currency_system": {
            "schema_version": version,
            "base_unit": "cent",
            "units": [{"id": "cent", "name": "分", "rate": 1}],
        },
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        RuleBundleLoader().load(tmp_path / "versioned_bundle.json")


def test_crud_rejects_declared_unsupported_version(tmp_path):
    from src.webui.services.rules import update_custom_rule

    (tmp_path / "custom_rule.json").write_text(json.dumps({
        "rule_id": "custom_rule", "rule_name": "Custom", "custom": True,
    }, ensure_ascii=False), encoding="utf-8")
    deps = _rules_dependencies(tmp_path)
    result = update_custom_rule(deps, "custom_rule", {
        "rule_id": "custom_rule",
        "rule_name": "Custom",
        "custom": True,
        "currency_system": {"schema_version": 3, "base_unit": "a", "units": [{"id": "a", "name": "a", "rate": 1}]},
    })
    assert result["ok"] is False
    assert "currency_system 声明非法" in result["error"]


def test_declared_boundary_accepts_v1_and_rejects_unsupported():
    """显式 schema_version: 1 是受支持的 legacy 声明；0/3/"abc"/bool 拒绝。"""

    from src.engine.currency import validate_declared_currency_system

    # 无版本键 → 直接放行（legacy）。
    validate_declared_currency_system({"base_unit": "gold", "units": [{"id": "gold", "name": "金币", "rate": 1}]})
    validate_declared_currency_system(None)
    # 显式 legacy 声明 → 放行。
    validate_declared_currency_system({
        "schema_version": 1,
        "base_unit": "credit",
        "units": [{"id": "credit", "name": "Credit", "rate": 1}],
    })
    for version in (0, 3, "abc", True, 2.5):
        with pytest.raises(CurrencySystemError):
            validate_declared_currency_system({
                "schema_version": version,
                "base_unit": "a",
                "units": [{"id": "a", "name": "a", "rate": 1}],
            })


def test_rule_system_loads_declared_legacy_version(tmp_path):
    (tmp_path / "legacy_declared.json").write_text(json.dumps({
        "rule_id": "legacy_declared",
        "rule_name": "Legacy",
        "currency": "金币",
        "currency_system": {
            "schema_version": 1,
            "base_unit": "gold",
            "units": [{"id": "gold", "name": "金币", "rate": 1}],
        },
    }, ensure_ascii=False), encoding="utf-8")
    rule = RuleSystem.load(tmp_path / "legacy_declared.json")
    spec = rule.currency_spec
    assert spec.schema_version == 1
    assert spec.base_unit == "gold"
