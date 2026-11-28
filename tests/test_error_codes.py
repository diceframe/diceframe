"""ADR-0015 §4: an error carries its code from the raise site, not from its text.

Three contracts are protected here:

1. `error_payload()` derives a stable code from the catalog key, so the code
   never depends on the rendered wording or the request language;
2. the legacy「中文文案 → error_code」table is frozen — it may shrink, not grow,
   and the ratchet in `scripts/audit_error_codes.py` blocks new undeclared
   `"error": t(...)` payloads;
3. the HTTP exit keeps a declared code even when it has no wording of its own,
   which is what lets community locales translate through `apiErrors.<code>`.
"""

from __future__ import annotations

import ast
import importlib.util
import warnings
import json
import re
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.webui.errors import (
    ERROR_CODE_MAP,
    FROZEN_ERROR_CODES,
    LEGACY_DECLARED_CODES,
    error_code_for_key,
    error_payload,
    error_code_middleware,
    validate_error_code,
)
from src.webui.request_language import request_language_middleware

ROOT = Path(__file__).resolve().parents[1]
SNAKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


# ------------------------------------------------------------ code derivation


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("webui.services.worlds.name_empty", "worlds_name_empty"),
        ("webui.services.characters.payer_missing", "characters_payer_missing"),
        ("webui.routes.plugins.package_too_large", "route_plugins_package_too_large"),
        ("webui.routes._common.confirm_header_missing", "route_common_confirm_header_missing"),
        ("webui.services.asr.asr_test_admin_only", "asr_asr_test_admin_only"),
    ],
)
def test_the_code_is_derived_from_the_key_not_the_text(key: str, expected: str) -> None:
    assert error_code_for_key(key) == expected


@pytest.mark.parametrize("key", ["", "game_not_found", "webui.services", "elsewhere.src.x.y"])
def test_a_key_outside_the_webui_catalogs_cannot_define_a_code(key: str) -> None:
    with pytest.raises(ValueError):
        error_code_for_key(key)


def test_new_codes_must_be_snake_case() -> None:
    assert validate_error_code("worlds_name_empty") == "worlds_name_empty"
    # 历史 UPPER 写法仍然合法，但不允许出现新的。
    assert validate_error_code("ROUND_PROCESSING") == "ROUND_PROCESSING"
    for bad in ("New_Code", "BAD_CODE", "9lives", "has space", "trailing_"):
        with pytest.raises(ValueError):
            validate_error_code(bad)


def test_payload_carries_code_and_rendered_text() -> None:
    payload = error_payload("webui.services.worlds.name_empty")
    assert payload["ok"] is False
    assert payload["error_code"] == "worlds_name_empty"
    assert payload["error"]

    rendered = error_payload("webui.services.worlds.entry_too_long", max_lorebook_chars=42)
    assert rendered["error_code"] == "worlds_entry_too_long"
    assert "42" in rendered["error"]


def test_a_shared_semantic_code_can_be_declared_explicitly() -> None:
    payload = error_payload("webui.services.worlds.world_id_missing", code="invalid_world_id")
    assert payload["error_code"] == "invalid_world_id"


# ------------------------------------------------------------- frozen legacy


def test_the_legacy_text_table_is_frozen() -> None:
    """Any change here must be deliberate: it is a shrinking migration artifact."""

    assert len(ERROR_CODE_MAP) == 65
    assert len(FROZEN_ERROR_CODES) == 51
    assert all(code.islower() for code in FROZEN_ERROR_CODES)


def _declared_codes() -> dict[str, str]:
    """Every code literal written at a raise site, with its file."""

    found: dict[str, str] = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)  # 被扫描文件自身的转义告警
            tree = ast.parse(path.read_bytes().decode("utf-8-sig"))
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                # `code` 只在带 error 的错误载荷里算错误码；它曾经绕过前端。
                names = ("error_code", "code") if "error" in keys else ("error_code",)
                for name in names:
                    if name in keys:
                        value = node.values[keys.index(name)]
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):
                            found.setdefault(value.value, rel)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "error_payload"
            ):
                for keyword in node.keywords:
                    if keyword.arg == "code" and isinstance(keyword.value, ast.Constant):
                        found.setdefault(str(keyword.value.value), rel)
    return found


def test_no_new_upper_case_codes_appear_at_raise_sites() -> None:
    declared = _declared_codes()
    assert declared, "应至少扫描到一处错误码声明"
    offenders = {
        code: where
        for code, where in declared.items()
        if not SNAKE.match(code) and code not in LEGACY_DECLARED_CODES
    }
    assert not offenders, f"新增错误码必须是 snake_case：{offenders}"


def test_no_payload_declares_two_different_codes() -> None:
    """One payload, one code. `code` and `error_code` may not disagree.

    Routes whitelist the legacy UPPER spelling through `declared_error_code`,
    which prefers `error_code`; a second, derived value silently turns a 403
    into a 422. Stamping a code next to an existing `code` is therefore only
    allowed when both say the same thing.
    """

    conflicts: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(path.read_bytes().decode("utf-8-sig"))
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if "code" not in keys or "error_code" not in keys:
                continue
            code = node.values[keys.index("code")]
            error_code = node.values[keys.index("error_code")]
            if (
                isinstance(code, ast.Constant) and isinstance(error_code, ast.Constant)
                and isinstance(code.value, str) and isinstance(error_code.value, str)
                and code.value != error_code.value
            ):
                conflicts.append(f"{rel}:{node.lineno}: {code.value!r} vs {error_code.value!r}")
    assert not conflicts, "\n".join(conflicts)


def test_the_legacy_code_list_matches_the_codebase() -> None:
    """The frozen UPPER list is the exact set still in use — no silent growth."""

    in_use = {code for code in _declared_codes() if not SNAKE.match(code)}
    assert in_use == set(LEGACY_DECLARED_CODES)


# ----------------------------------------------------------------- ratchet


def _ratchet_module():
    path = ROOT / "scripts" / "audit_error_codes.py"
    spec = importlib.util.spec_from_file_location("audit_error_codes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_undeclared_interface_errors_never_grow() -> None:
    module = _ratchet_module()
    baseline = json.loads(module.BASELINE_PATH.read_text(encoding="utf-8"))["files"]
    current = {name: len(sites) for name, sites in module.scan().items()}
    grown = {
        name: (baseline.get(name, 0), count)
        for name, count in current.items()
        if count > baseline.get(name, 0)
    }
    assert not grown, f"新增未声明错误码的接口错误（请改用 error_payload）：{grown}"


def test_the_reference_module_still_declares_its_catalog_errors() -> None:
    """A migrated module must not quietly fall back to bare `{"error": t(...)}`."""

    source = (ROOT / "src" / "webui" / "services" / "worlds.py").read_text(encoding="utf-8")
    assert 'error_payload("webui.services.worlds.name_empty")' in source
    assert 'error_payload("webui.services.worlds.world_id_invalid", code="invalid_world_id")' in source
    # 剩下的是动态/字面量载荷（如批量导入的逐条目明细），由 ratchet 守住不增长。


# ------------------------------------------------------------- the exit


def _app() -> web.Application:
    async def declared_without_wording(_request: web.Request) -> web.Response:
        # worlds_* 在 webui/errors.<语言>.yml 里没有译文，code 仍必须存活。
        return web.json_response(error_payload("webui.services.worlds.name_empty"), status=400)

    async def declared_with_wording(_request: web.Request) -> web.Response:
        return web.json_response(error_payload("webui.services.characters.amount_not_integer"), status=400)

    async def legacy_text(_request: web.Request) -> web.Response:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)

    async def internal_code(_request: web.Request) -> web.Response:
        # 编排层旧写法：只有 `code`。客户端只读 error_code，所以出口必须提升它。
        return web.json_response(
            {"ok": False, "code": "GM_ONLY", "error": "只有 GM 可以确认"}, status=403,
        )

    app = web.Application(middlewares=[request_language_middleware, error_code_middleware])
    app.router.add_get("/no-wording", declared_without_wording)
    app.router.add_get("/wording", declared_with_wording)
    app.router.add_get("/legacy", legacy_text)
    app.router.add_get("/internal-code", internal_code)
    return app


@pytest.mark.asyncio
async def test_a_declared_code_survives_every_request_language() -> None:
    async with TestClient(TestServer(_app())) as client:
        for language in ("en", "ja", "de", "ru", "zh-CN"):
            body = await (await client.get("/no-wording", headers={"Accept-Language": language})).json()
            assert body["error_code"] == "worlds_name_empty", language


@pytest.mark.asyncio
async def test_a_registered_code_still_gets_the_exit_wording() -> None:
    async with TestClient(TestServer(_app())) as client:
        body = await (await client.get("/legacy", headers={"Accept-Language": "en"})).json()
        assert body["error_code"] == "game_not_found"
        assert body["error"] == "Game not found"


@pytest.mark.asyncio
async def test_an_unregistered_declared_code_keeps_its_rendered_text() -> None:
    async with TestClient(TestServer(_app())) as client:
        english = await (await client.get("/wording", headers={"Accept-Language": "en"})).json()
        assert english["error_code"] == "characters_amount_not_integer"
        assert english["error"]
        japanese = await (await client.get("/wording", headers={"Accept-Language": "ja"})).json()
        # 没有 ja 目录时后退英文，但 code 始终在，前端 apiErrors 才能接手。
        assert japanese["error_code"] == english["error_code"]

@pytest.mark.asyncio
async def test_an_internal_code_reaches_the_client() -> None:
    """`{"code": ...}` alone is invisible to `apiErrors.<code>`; the exit promotes it."""

    async with TestClient(TestServer(_app())) as client:
        for language in ("zh-CN", "en", "ja"):
            body = await (await client.get("/internal-code", headers={"Accept-Language": language})).json()
            assert body["error_code"] == "GM_ONLY", language
            assert body["code"] == "GM_ONLY", language
            # 没有同码译文时原文不动，也不会被换成别的语言。
            assert body["error"] == "只有 GM 可以确认", language
