"""provider 型插件运行时：能力校验、SDK 往返与宿主调用。"""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pytest

from src.plugin_host.descriptors import validate_provider_capabilities
from src.plugin_host.host import PluginHost
from src.plugin_host.runtime_protocol import PluginProtocolError
from src.plugin_sdk.provider_runtime import ProviderRuntime


def _caps(**overrides):
    capability = {
        "kind": "image-generation",
        "version": 1,
        "methods": {"generate": "provider.image-generation.generate"},
    }
    capability.update(overrides)
    return {"protocol_version": 1, "capabilities": [capability]}


def test_validate_provider_capabilities_accepts_declaration():
    capabilities = validate_provider_capabilities(_caps())
    assert capabilities[0]["kind"] == "image-generation"
    assert capabilities[0]["methods"]["generate"] == "provider.image-generation.generate"


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

    @runtime.capability(kind="image-generation", version=1)
    def generate(arguments, context):
        if arguments.get("prompt") == "fail":
            return {"ok": False, "error": "boom"}
        return {"ok": True, "image_base64": str(arguments.get("prompt"))}

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocol_version": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "provider.request", "params": {
            "capability": "image-generation", "method": "generate",
            "arguments": {"prompt": "harbor"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "provider.request", "params": {
            "capability": "image-generation", "method": "generate",
            "arguments": {"prompt": "fail"}}},
    ]
    runtime.stdin = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
    runtime.stdout = io.StringIO()
    runtime.run()
    responses = [json.loads(line) for line in runtime.stdout.getvalue().splitlines()]

    assert responses[0]["result"]["capabilities"][0]["kind"] == "image-generation"
    assert responses[1]["result"] == {"ok": True, "image_base64": "harbor"}
    assert responses[2]["result"] == {"ok": False, "error": "boom"}


def test_builtin_imagegen_reuses_ai_provider_catalog():
    plugin_dir = Path(__file__).resolve().parents[1] / "plugins" / "imagegen-openai"
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    schema = json.loads((plugin_dir / "config.schema.json").read_text(encoding="utf-8"))
    properties = schema["properties"]

    assert "base_url" not in properties
    assert "api_key" not in properties
    assert "group" not in properties["enabled"]["ui"]
    assert properties["provider_ref"]["ui"]["options_source"] == "ai_providers"
    assert properties["model"]["ui"]["options_source"] == "provider_models"
    assert "ai.providers" in manifest["permissions"]


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
        "permissions": ["network.client", "plugin.config", "process.spawn", "plugin.data"],
    }), encoding="utf-8")
    (folder / "config.schema.json").write_text(json.dumps({
        "type": "object",
        "properties": {"enabled": {"type": "boolean", "default": False, "ui": {"control": "switch"}}},
    }), encoding="utf-8")
    (folder / "main.py").write_text(textwrap.dedent('''
        from src.plugin_sdk import ProviderRuntime

        runtime = ProviderRuntime()

        @runtime.capability(kind="image-generation", version=1)
        def generate(arguments, context):
            prompt = str(arguments.get("prompt") or "")
            if prompt == "fail":
                return {"ok": False, "error": "upstream down"}
            return {"ok": True, "image_base64": prompt.upper()}

        if __name__ == "__main__":
            runtime.run()
    '''), encoding="utf-8")
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()

    detail = await host.update_config("stub-provider", {"enabled": True})

    assert detail["status"] == "running"
    assert host.find_provider("image-generation") == "stub-provider"
    assert host.find_provider("unknown-capability") is None
    result = await host.call_provider(
        "stub-provider", "image-generation", "generate", {"prompt": "harbor"}, timeout=10,
    )
    assert result == {"ok": True, "image_base64": "HARBOR"}
    # 插件侧业务失败以 ok:false 的正常返回送达（不抛 RPC 错误），由门面层转译
    failed = await host.call_provider(
        "stub-provider", "image-generation", "generate", {"prompt": "fail"}, timeout=10,
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

    # 握手失败不向外抛：插件状态落为 failed（fail-closed）
    detail = await host.update_config("bad-provider", {"enabled": True})

    assert detail["status"] == "failed"
    assert "capability" in detail.get("error", "")
    assert host.find_provider("image-generation") is None
    await host.cleanup()
