"""provider 型插件运行时：能力校验、SDK 往返与宿主调用。"""

from __future__ import annotations

import io
import json
import textwrap

import pytest

from src.plugin_host.descriptors import validate_provider_capabilities
from src.plugin_host.host import PluginHost
from src.plugin_host.runtime_protocol import PluginProtocolError
from src.plugin_sdk.provider_runtime import ProviderRuntime


def _caps(**overrides):
    capability = {
        "kind": "text-transform",
        "version": 1,
        "methods": {"generate": "provider.text-transform.generate"},
    }
    capability.update(overrides)
    return {"protocol_version": 1, "capabilities": [capability]}


def test_validate_provider_capabilities_accepts_declaration():
    capabilities = validate_provider_capabilities(_caps())
    assert capabilities[0]["kind"] == "text-transform"
    assert capabilities[0]["methods"]["generate"] == "provider.text-transform.generate"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"protocol_version": 2, "capabilities": []}, "协议版本不匹配"),
        ({"protocol_version": 1}, "至少一个 capability"),
        (_caps(kind="Bad Kind"), "kind 非法"),
        (_caps(methods={"unknown": "provider.x.y"}), "未知方法别名"),
        (_caps(methods={"generate": "tool.call"}), "方法名非法"),
        (_caps(version=0), "version"),
    ],
)
def test_validate_provider_capabilities_rejects_bad_declarations(payload, message):
    with pytest.raises(PluginProtocolError, match=message):
        validate_provider_capabilities(payload)


def test_provider_runtime_serves_initialize_and_request_over_stdio():
    runtime = ProviderRuntime()

    @runtime.capability(kind="text-transform", version=1)
    def generate(arguments, context):
        if arguments.get("text") == "fail":
            return {"ok": False, "error": "boom"}
        return {"ok": True, "text": str(arguments.get("text")).upper()}

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocol_version": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "provider.request", "params": {
            "capability": "text-transform", "method": "generate",
            "arguments": {"text": "harbor"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "provider.request", "params": {
            "capability": "text-transform", "method": "generate",
            "arguments": {"text": "fail"}}},
    ]
    runtime.stdin = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
    runtime.stdout = io.StringIO()
    runtime.run()
    responses = [json.loads(line) for line in runtime.stdout.getvalue().splitlines()]

    assert responses[0]["result"]["capabilities"][0]["kind"] == "text-transform"
    assert responses[1]["result"] == {"ok": True, "text": "HARBOR"}
    assert responses[2]["result"] == {"ok": False, "error": "boom"}


@pytest.mark.asyncio
async def test_provider_plugin_registers_and_executes_over_stdio_rpc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plugins = tmp_path / "plugins"
    folder = plugins / "stub-provider"
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "stub-provider",
        "name": "Stub Provider",
        "version": "1",
        "description": "test provider",
        "plugin_type": "provider",
        "entrypoint": ["{python}", "{plugin_dir}/main.py"],
        "permissions": ["network.client", "plugin.config", "process.spawn"],
    }), encoding="utf-8")
    (folder / "config.schema.json").write_text(json.dumps({
        "type": "object",
        "properties": {"enabled": {"type": "boolean", "default": False, "ui": {"control": "switch"}}},
    }), encoding="utf-8")
    (folder / "main.py").write_text(textwrap.dedent('''
        from src.plugin_sdk import ProviderRuntime

        runtime = ProviderRuntime()

        @runtime.capability(kind="text-transform", version=1)
        def generate(arguments, context):
            text = str(arguments.get("text") or "")
            if text == "fail":
                return {"ok": False, "error": "upstream down"}
            return {"ok": True, "text": text.upper()}

        if __name__ == "__main__":
            runtime.run()
    '''), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    detail = await host.update_config("stub-provider", {"enabled": True})

    assert detail["status"] == "running"
    assert host.find_provider("text-transform") == "stub-provider"
    assert host.find_provider("unknown-capability") is None
    result = await host.call_provider(
        "stub-provider", "text-transform", "generate", {"text": "harbor"}, timeout=10,
    )
    assert result == {"ok": True, "text": "HARBOR"}
    failed = await host.call_provider(
        "stub-provider", "text-transform", "generate", {"text": "fail"}, timeout=10,
    )
    assert failed == {"ok": False, "error": "upstream down"}
    with pytest.raises(KeyError, match="未声明 capability"):
        await host.call_provider(
            "stub-provider", "no-such-capability", "generate", {}, timeout=10,
        )
    await host.cleanup()


@pytest.mark.asyncio
async def test_provider_plugin_with_invalid_capability_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plugins = tmp_path / "plugins"
    folder = plugins / "bad-provider"
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "bad-provider",
        "name": "Bad Provider",
        "version": "1",
        "description": "invalid capability payload",
        "plugin_type": "provider",
        "entrypoint": ["{python}", "{plugin_dir}/main.py"],
    }), encoding="utf-8")
    (folder / "config.schema.json").write_text(json.dumps({
        "type": "object",
        "properties": {"enabled": {"type": "boolean", "default": False, "ui": {"control": "switch"}}},
    }), encoding="utf-8")
    (folder / "main.py").write_text(textwrap.dedent('''
        import json
        import sys
        for line in sys.stdin:
            request = json.loads(line)
            result = {"protocol_version": 1, "capabilities": []}
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
    '''), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    detail = await host.update_config("bad-provider", {"enabled": True})

    assert detail["status"] == "failed"
    assert "capability" in detail.get("error", "")
    assert host.find_provider("text-transform") is None
    await host.cleanup()
