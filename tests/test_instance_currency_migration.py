"""Currency base-unit 迁移（schema v11 → v12）：内置 CoC 美元 → 美分 ×100。"""

from __future__ import annotations

from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    migrate_game_state_payload,
)


def _coc_payload() -> dict:
    """一个典型的 CoC 存档：余额、待确认提案、流水、奖励上限与回合快照。"""

    return {
        "instance_schema_version": 11,
        "rule_id": "freeform_coc",
        "game_key": ["web", "local", "acc"],
        "economy_reward_policy": {"mode": "auto_small_cash", "auto_reward_cap": 50},
        "players": {
            "p1": {
                "character_name": "张三",
                "character_sheet": {"gold": 100, "currency": {"amount": 100, "base_unit": "unit"}},
            },
        },
        "economy": {
            "schema_version": 2,
            "proposals": [{"id": "eco_1", "kind": "purchase", "amount": 25, "status": "pending"}],
            "outcomes": [{"id": "outcome_1", "amount": 25, "status": "committed"}],
            "transactions": [{
                "id": "tx_1",
                "status": "committed",
                "entries": [
                    {"account": "character:p1", "delta": -5, "before": 80, "after": 75},
                    {"account": "system:world", "delta": 5, "before": None, "after": None},
                ],
            }],
        },
        "round_start_snapshot": {
            "p1": {"gold": 60, "currency": {"amount": 60}},
        },
        "log": [{
            "round": 3,
            "round_start_snapshot": {"p1": {"gold": 80, "currency": {"amount": 80}}},
            "pre_state_snapshot": {"p1": {"gold": 70, "currency": {"amount": 70}}},
        }],
    }


def test_coc_save_amounts_scale_by_100_once():
    payload = migrate_game_state_payload(_coc_payload())

    assert payload["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    sheet = payload["players"]["p1"]["character_sheet"]
    assert sheet["currency"]["amount"] == 10_000
    assert sheet["gold"] == 10_000
    assert payload["economy"]["proposals"][0]["amount"] == 2_500
    assert payload["economy"]["outcomes"][0]["amount"] == 2_500
    entries = payload["economy"]["transactions"][0]["entries"]
    assert entries[0] == {"account": "character:p1", "delta": -500, "before": 8_000, "after": 7_500}
    assert entries[1]["delta"] == 500
    assert payload["economy_reward_policy"]["auto_reward_cap"] == 5_000
    assert payload["round_start_snapshot"]["p1"]["currency"]["amount"] == 6_000
    assert payload["log"][0]["round_start_snapshot"]["p1"]["gold"] == 8_000
    assert payload["log"][0]["pre_state_snapshot"]["p1"]["gold"] == 7_000

    # 二次 load 不重复 ×100（幂等）。
    again = migrate_game_state_payload(payload)
    assert again["players"]["p1"]["character_sheet"]["currency"]["amount"] == 10_000
    assert again["economy_reward_policy"]["auto_reward_cap"] == 5_000


def test_non_coc_rules_are_not_migrated():
    for rule_id in ("freeform_fantasy", "freeform_wuxia", "dnd5e", "freeform_cyberpunk", ""):
        payload = migrate_game_state_payload({
            "instance_schema_version": 11,
            "rule_id": rule_id,
            "players": {"p1": {"character_sheet": {"gold": 100, "currency": {"amount": 100}}}},
            "economy": {"proposals": [{"amount": 25}]},
            "economy_reward_policy": {"mode": "auto_small_cash", "auto_reward_cap": 50},
        })
        assert payload["players"]["p1"]["character_sheet"]["gold"] == 100
        assert payload["economy"]["proposals"][0]["amount"] == 25
        assert payload["economy_reward_policy"]["auto_reward_cap"] == 50
        assert payload["instance_schema_version"] == 12


def test_pre_rule_id_save_is_not_guessed():
    """无法证明规则身份的存档 fail closed：不猜测、不改金额。"""

    payload = migrate_game_state_payload({
        "instance_schema_version": 11,
        "rule_id": "",
        "players": {"p1": {"character_sheet": {"gold": 100}}},
    })
    assert payload["players"]["p1"]["character_sheet"]["gold"] == 100


def test_older_save_passes_through_v11_migration_chain():
    """v1 存档按顺序迁移到 v12，CoC 金额同样只缩放一次。"""

    payload = migrate_game_state_payload({
        "instance_schema_version": 1,
        "rule_id": "freeform_coc",
        "players": {"p1": {"character_sheet": {"gold": 30}}},
    })
    assert payload["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert payload["players"]["p1"]["character_sheet"]["gold"] == 3_000


def test_unsupported_future_version_fails_closed():
    import pytest

    with pytest.raises(ValueError, match="unsupported game instance schema version"):
        migrate_game_state_payload({
            "instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1,
            "rule_id": "freeform_coc",
        })
