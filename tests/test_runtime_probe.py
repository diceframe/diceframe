import asyncio
from pathlib import Path

import pytest

from src.rulesets.runtime_probe import (
    RuntimeProbeClient,
    RuntimeProbeError,
    RuntimeProbeLedger,
    normalize_probe_request,
)


def _request(request_id="r1", payload=None):
    return {
        "protocol_version": 1,
        "runtime_id": "external:demo",
        "runtime_version": 1,
        "request_id": request_id,
        "operation": "validate_character",
        "payload": payload or {"draft": {"name": "A"}},
    }


@pytest.mark.asyncio
async def test_runtime_probe_is_json_only_idempotent_and_does_not_mutate_live_state():
    calls = []
    async def transport(payload):
        calls.append(payload)
        return {"request_id": payload["request_id"], "ok": True, "events": []}

    state = {"hp": 10}
    client = RuntimeProbeClient(transport)
    first = await client.call(_request(), live_state=state)
    again = await client.call(_request(), live_state=state)
    assert first == again
    assert len(calls) == 1
    assert state == {"hp": 10}


@pytest.mark.asyncio
async def test_runtime_probe_rejects_request_id_conflicts_and_timeout():
    client = RuntimeProbeClient(lambda payload: {"request_id": payload["request_id"], "ok": True})
    await client.call(_request(), live_state={})
    with pytest.raises(RuntimeProbeError, match="reused"):
        await client.call(_request(payload={"different": True}), live_state={})

    async def slow(_payload):
        await asyncio.sleep(0.05)

    with pytest.raises(RuntimeProbeError, match="timed out"):
        await RuntimeProbeClient(slow, timeout_seconds=0.01).call(_request("slow"))


def test_runtime_probe_rejects_malformed_and_oversized_requests():
    with pytest.raises(RuntimeProbeError):
        normalize_probe_request({"protocol_version": 1})
    with pytest.raises(RuntimeProbeError, match="too large"):
        normalize_probe_request(_request(payload={"text": "x" * (256 * 1024)}))


@pytest.mark.asyncio
async def test_runtime_probe_receipt_ledger_survives_a_restart(tmp_path: Path):
    calls = []

    async def transport(payload):
        calls.append(payload)
        return {"request_id": payload["request_id"], "ok": True, "result": {"accepted": True}}

    ledger_path = tmp_path / "runtime-probe-ledger.json"
    await RuntimeProbeClient(transport, ledger=RuntimeProbeLedger(ledger_path)).call(
        _request(), live_state={},
    )
    restored = RuntimeProbeLedger(ledger_path)
    again = await RuntimeProbeClient(transport, ledger=restored).call(_request(), live_state={})
    assert again["result"]["accepted"] is True
    assert len(calls) == 1
