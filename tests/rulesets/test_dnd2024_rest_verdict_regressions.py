"""Durable party verdict isolation and all-or-nothing publication."""
from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.webui.api import WebAPI
from src.webui.services import ruleset_rest
from tests.rulesets.test_dnd2024_character_lifecycle import professional_context  # noqa: F401
from tests.rulesets.test_dnd2024_rest_decision import (
    KEY, _party, _both_submit, _clock, _revision, _submit_body,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
async def test_old_verdict_cannot_decide_a_new_session(professional_context, approve):
    api, instance = _party(professional_context)
    await _both_submit(api, instance, "long", tag="first")
    verdict = {"approve": approve, "operation_id": "old-verdict"}
    assert (await WebAPI.ruleset_rest_decide(api, KEY, "gm", True, verdict))["ok"]
    revision = 1 if approve else 0
    for uid in ("gm", "ally"):
        result = await WebAPI.ruleset_rest_submit_live(
            api, KEY, uid, _submit_body("short", f"next-{uid}", revision),
        )
        assert result["ok"]
    # Exercise the persisted codec rather than relying on process-local IDs.
    restored = GameInstance.from_dict(instance.to_dict())
    deps = replace(api._live_ruleset_rest_dependencies, get_instance=lambda _key: restored)
    before = deepcopy(restored.to_dict())
    replay = await ruleset_rest.decide_live(deps, KEY, "gm", True, verdict)
    assert replay["ok"] and replay["duplicate"]
    assert restored.to_dict() == before
    assert replay["rest_session"]["status"] == "awaiting_approval"


@pytest.mark.asyncio
async def test_nonparticipant_gm_gets_success(professional_context):
    api, instance = _party(professional_context)
    instance.away_players.add("gm")
    await WebAPI.ruleset_rest_submit_live(api, KEY, "ally", _submit_body("long", "ally"))
    before = _clock(instance)
    result = await WebAPI.ruleset_rest_decide(
        api, KEY, "gm", True, {"approve": True, "operation_id": "away-gm"},
    )
    assert result["ok"] and result["resolved"]
    assert _revision(instance, "ally") == 1 and _revision(instance, "gm") == 0
    assert _clock(instance) == before + 480


@pytest.mark.asyncio
async def test_failed_final_save_leaves_no_partial_party_or_consumed_verdict(professional_context):
    api, instance = _party(professional_context)
    await _both_submit(api, instance, "long", tag="atomic")
    before = deepcopy(instance.to_dict())
    saves = []

    async def fail_save(inst):
        saves.append(deepcopy(inst.to_dict()))
        raise OSError("durable save failed")

    deps = replace(api._live_ruleset_rest_dependencies, save_instance=fail_save)
    verdict = {"approve": True, "operation_id": "retryable"}
    with pytest.raises(OSError):
        await ruleset_rest.decide_live(deps, KEY, "gm", True, verdict)
    assert len(saves) == 1
    assert saves[0]["players"]["gm"]["character_sheet"]["ruleset_revision"] == 1
    assert saves[0]["players"]["ally"]["character_sheet"]["ruleset_revision"] == 1
    assert instance.to_dict() == before
    result = await WebAPI.ruleset_rest_decide(api, KEY, "gm", True, verdict)
    assert result["ok"] and not result.get("duplicate")
    assert _revision(instance, "gm") == _revision(instance, "ally") == 1
