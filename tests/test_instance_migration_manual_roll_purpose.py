from src.migrations.instance import CURRENT_INSTANCE_SCHEMA_VERSION, migrate_game_state_payload


def test_schema10_manual_rolls_default_to_record_only_purpose():
    payload = migrate_game_state_payload({
        "instance_schema_version": 10,
        "manual_roll_requests": [{"id": "mr-1", "results": {}}],
    })
    # 顺序迁移：v10 经过 v11（purpose 默认值）继续走到当前版本（Currency V2 步骤）。
    assert payload["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert payload["manual_roll_requests"][0]["purpose"] == "free"
