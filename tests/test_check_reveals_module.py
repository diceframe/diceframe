"""Click-to-reveal dice presentation contracts.

覆盖：模块槽行为（幂等首写优先、有界裁剪）、v34→v35 迁移、服务授权
（行动者/GM、第三方拒绝、幂等 already）、日志读取面带回揭示映射与揭示
方式、configure_session 校验。揭示是纯表现状态：请求只带 check_id，
不带骰值，也不参与任何推进判定。
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import check_reveals as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v34_to_v35,
    migrate_game_state_payload,
)
from src.webui.services import check_reveals as service
from src.webui.services.logs import get_log, LogDependencies


def _instance() -> GameInstance:
    return GameInstance(
        game_key=("test", "reveal", "web"),
        world_id="default_fantasy",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )


def test_migration_materializes_empty_slot_and_keeps_existing() -> None:
    old = {"instance_schema_version": 34}
    result = migrate_game_state_payload(old)
    assert result["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert result["modules"][module.MODULE_NAME] == {"schema_version": 1, "records": {}}
    assert migrate_game_state_payload(result) == result

    kept = {"schema_version": 1, "records": {"c1": {"by": "gm", "at": "x"}}}
    single = _migrate_v34_to_v35({"modules": {module.MODULE_NAME: deepcopy(kept)}})
    assert single["modules"][module.MODULE_NAME] == kept

    opaque = {"schema_version": 99, "opaque": True}
    assert _migrate_v34_to_v35({"modules": {module.MODULE_NAME: opaque}})["modules"][module.MODULE_NAME] is opaque


def test_mark_revealed_is_idempotent_and_bounded() -> None:
    inst = _instance()
    first = module.mark_revealed(inst, "c1", "ally")
    assert first["by"] == "ally"
    again = module.mark_revealed(inst, "c1", "gm")
    assert again == first

    for index in range(module.MAX_RECORDS + 10):
        module.mark_revealed(inst, f"c{index}", "gm")
    assert len(module.records(inst)) == module.MAX_RECORDS
    assert "c1" not in module.records(inst)


class _Registry:
    def __init__(self, inst: Any):
        self.inst = inst

    def get(self, key):
        return self.inst

    async def save(self, inst):
        self.saved = inst


def _service(inst: GameInstance) -> tuple[service.CheckRevealService, _Registry]:
    registry = _Registry(inst)
    deps = service.CheckRevealDependencies(
        parse_game_key=lambda key: tuple(key.split("|")),
        get_instance=registry.get,
        save_instance=registry.save,
    )
    return service.CheckRevealService(deps), registry


@pytest.mark.asyncio
async def test_actor_or_gm_can_reveal_and_repeats_are_idempotent() -> None:
    inst = _instance()
    inst.last_checks = [{"check_id": "chk-1", "actor_uid": "ally", "roll": 15}]
    svc, registry = _service(inst)

    by_actor = await svc.reveal("test|reveal|web", "chk-1", "ally")
    assert by_actor["ok"] is True
    assert by_actor["already"] is False
    assert by_actor["reveal"]["by"] == "ally"

    by_gm = await svc.reveal("test|reveal|web", "chk-1", "gm")
    assert by_gm["ok"] is True
    assert by_gm["already"] is True
    assert by_gm["reveal"]["by"] == "ally"
    assert registry.saved is inst


@pytest.mark.asyncio
async def test_third_party_and_unknown_checks_are_refused() -> None:
    inst = _instance()
    inst.log = [{"check_results": [{"check_id": "chk-2", "actor_uid": "ally"}]}]
    svc, _ = _service(inst)

    stranger = await svc.reveal("test|reveal|web", "chk-2", "someone-else")
    assert stranger["ok"] is False
    assert stranger["status"] == 403

    missing = await svc.reveal("test|reveal|web", "nope", "gm")
    assert missing["ok"] is False
    assert missing["status"] == 404


def test_log_response_carries_reveals_and_mode() -> None:
    inst = _instance()
    inst.dice_reveal_mode = "click"
    module.mark_revealed(inst, "chk-1", "ally")
    inst.log = [{
        "round": 1,
        "check_results": [
            {"check_id": "chk-1", "actor_uid": "ally", "roll": 15},
            {"check_id": "chk-2", "actor_uid": "ally", "roll": 3},
        ],
    }]
    deps = LogDependencies(
        registry=SimpleNamespace(get=lambda key: inst),
        parse_game_key=lambda key: tuple(key.split("|")),
    )
    result = get_log(deps, "test|reveal|web")
    assert result["dice_reveal_mode"] == "click"
    # 只包含已揭示的检定；未揭示的不产生条目。
    assert set(result["check_reveals"]) == {"chk-1"}
    assert result["check_reveals"]["chk-1"]["by"] == "ally"


def test_configure_session_validates_and_persists_mode() -> None:
    inst = _instance()
    assert inst.dice_reveal_mode == "click"
    inst.configure_session(dice_reveal_mode="auto")
    assert inst.dice_reveal_mode == "auto"
    with pytest.raises(ValueError):
        inst.configure_session(dice_reveal_mode="ritual")
    assert inst.dice_reveal_mode == "auto"
