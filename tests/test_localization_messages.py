"""后端文案多语言入口：文案表完整性、语言回退、请求语言与错误出口。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.localization import messages
from src.localization.messages import (
    LOCALES_DIR,
    REQUIRED_LOCALES,
    catalog_files,
    catalog_keys,
    recognized_language,
    request_language,
    reset_request_language,
    set_request_language,
    t,
)
from src.webui.errors import ERROR_CODE_MAP, error_code_middleware
from src.webui.request_language import preferred_language, request_language_middleware

ROOT = Path(__file__).resolve().parents[1]
_PLACEHOLDER = re.compile(r"%\{([^}]+)\}")


def _flatten(value: object, prefix: str = "") -> dict[str, str]:
    if not isinstance(value, dict):
        return {prefix: str(value)}
    flat: dict[str, str] = {}
    for key, child in value.items():
        flat.update(_flatten(child, f"{prefix}.{key}" if prefix else str(key)))
    return flat


def _namespace_entries(namespace: str, locale: str) -> dict[str, str]:
    paths = {(name, language): path for name, language, path in catalog_files()}
    assert (namespace, locale) in paths, f"{namespace} 缺少 {locale} 文案文件"
    data = yaml.safe_load(paths[namespace, locale].read_text(encoding="utf-8"))
    return _flatten(data[locale])


# ---------------------------------------------------------------- 文案表本身

def test_every_catalog_file_is_rooted_at_its_locale() -> None:
    files = catalog_files()
    assert files, "locales 目录下应至少有一组文案"
    for _namespace, locale, path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert list(data) == [locale], f"{path.name} 的顶层键必须是它的语言标签"


def test_every_catalog_mirrors_the_source_module_that_owns_it() -> None:
    assert LOCALES_DIR.is_dir()
    for namespace, _locale, path in catalog_files():
        module = ROOT.joinpath("src", *namespace.split("."))
        assert module.with_name(f"{module.name}.py").is_file() or (module / "__init__.py").is_file(), (
            f"{path.relative_to(LOCALES_DIR).as_posix()} 没有对应的源码模块 src/{'/'.join(namespace.split('.'))}.py"
        )


def test_required_locales_define_the_same_keys_with_the_same_placeholders() -> None:
    namespaces = sorted({namespace for namespace, _locale, _path in catalog_files()})
    for namespace in namespaces:
        tables = {locale: _namespace_entries(namespace, locale) for locale in REQUIRED_LOCALES}
        reference = tables[REQUIRED_LOCALES[0]]
        for locale, table in tables.items():
            assert set(table) == set(reference), f"{namespace}: {locale} 与 {REQUIRED_LOCALES[0]} 的键不一致"
            for key, text in table.items():
                assert text.strip(), f"{namespace}.{key} 在 {locale} 为空"
                assert set(_PLACEHOLDER.findall(text)) == set(_PLACEHOLDER.findall(reference[key])), (
                    f"{namespace}.{key} 在 {locale} 的占位符不一致"
                )


def test_every_registered_error_code_has_a_message() -> None:
    for locale in REQUIRED_LOCALES:
        keys = catalog_keys(locale)
        missing = sorted(code for code in set(ERROR_CODE_MAP.values()) if f"webui.errors.{code}" not in keys)
        assert not missing, f"{locale} 缺少错误码文案：{missing}"


def test_the_library_is_a_declared_and_hash_pinned_runtime_dependency() -> None:
    assert "python-i18n" in (ROOT / "requirements.txt").read_text(encoding="utf-8")
    lock = (ROOT / "requirements-portable.lock").read_text(encoding="utf-8")
    assert re.search(r"^python-i18n==\S+ --hash=sha256:[0-9a-f]{64}$", lock, re.MULTILINE)
    assert re.search(r"^pyyaml==\S+ --hash=sha256:[0-9a-f]{64}$", lock, re.MULTILINE)


# ---------------------------------------------------------------- 渲染与回退

def test_default_language_keeps_the_original_chinese() -> None:
    assert request_language() == ""
    assert t("webui.errors.game_not_found") == "游戏不存在"
    assert t("webui.errors.game_not_found", "zh-CN") == "游戏不存在"


def test_explicit_language_wins_and_other_locales_fall_back_to_english() -> None:
    assert t("webui.errors.game_not_found", "en") == "Game not found"
    # 社区语言尚未提供译文时回退英文，而不是中文。
    assert t("webui.errors.game_not_found", "ja") == "Game not found"
    assert t("webui.errors.game_not_found", "de") == "Game not found"


def test_missing_key_is_returned_verbatim() -> None:
    assert t("webui.errors.__no_such_key__", "en") == "webui.errors.__no_such_key__"
    assert not messages.has("webui.errors.__no_such_key__", "en")


def test_placeholders_and_plurals_are_rendered(tmp_path, monkeypatch) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "demo.zh-CN.yml").write_text(
        'zh-CN:\n  gold_short: "金币不足（需要 %{price}）"\n  items:\n    one: "%{count} 件物品"\n    many: "%{count} 件物品"\n',
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "demo.en.yml").write_text(
        'en:\n  gold_short: "Not enough gold (%{price} needed)"\n  items:\n    one: "%{count} item"\n    many: "%{count} items"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(messages, "LOCALES_DIR", tmp_path)
    messages.reload()
    try:
        assert t("pkg.demo.gold_short", "zh-CN", price=30) == "金币不足（需要 30）"
        assert t("pkg.demo.gold_short", "en", price=30) == "Not enough gold (30 needed)"
        assert t("pkg.demo.items", "en", count=1) == "1 item"
        assert t("pkg.demo.items", "en", count=3) == "3 items"
    finally:
        monkeypatch.undo()
        messages.reload()
    assert t("webui.errors.game_not_found", "en") == "Game not found"


# ---------------------------------------------------------------- 请求语言

@pytest.mark.parametrize(
    ("raw", "expected"),
    [("en", "en"), ("en-US", "en"), ("zh_CN", "zh-CN"), ("zh-TW", "zh-CN"), ("JA", "ja"), ("fr", ""), ("", ""), (None, "")],
)
def test_client_supplied_tags_are_recognized_or_ignored(raw, expected) -> None:
    # 请求边界不猜：不认识的标签不能悄悄变成某种语言。
    assert recognized_language(raw) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("en", "en"),
        ("en-US,en;q=0.9,zh-CN;q=0.8", "en"),
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh-CN"),
        ("zh-CN;q=0.3, en;q=0.8", "en"),
        ("fr-FR,fr;q=0.9,ja;q=0.5", "ja"),
        ("en;q=0, zh-CN;q=0.3", "zh-CN"),
        ("en;q=abc", ""),
        ("fr-FR,fr;q=0.9", ""),
        ("*", ""),
        ("", ""),
        (None, ""),
        ("en," * 400, ""),
    ],
)
def test_accept_language_picks_the_highest_weighted_supported_language(header, expected) -> None:
    assert preferred_language(header) == expected


def test_request_language_is_scoped_to_its_context() -> None:
    token = set_request_language("en-GB")
    try:
        assert request_language() == "en"
        assert t("webui.errors.gm_only") == "GM only"
        assert t("webui.errors.gm_only", "zh-CN") != "GM only"
    finally:
        reset_request_language(token)
    assert request_language() == ""


def test_concurrent_requests_do_not_share_a_language() -> None:
    async def render(language: str) -> str:
        token = set_request_language(language)
        try:
            await asyncio.sleep(0)
            return t("webui.errors.game_not_found")
        finally:
            reset_request_language(token)

    async def main() -> list[str]:
        return list(await asyncio.gather(render("en"), render("zh-CN"), render("")))

    assert asyncio.run(main()) == ["Game not found", "游戏不存在", "游戏不存在"]


# ---------------------------------------------------------------- 错误出口

def _app() -> web.Application:
    async def missing(_request: web.Request) -> web.Response:
        return web.json_response({"ok": False, "error": "游戏不存在"}, status=404)

    async def specific(_request: web.Request) -> web.Response:
        return web.json_response({"ok": False, "error": "仅 GM 可删除游戏"}, status=403)

    async def unregistered(_request: web.Request) -> web.Response:
        return web.json_response({"ok": False, "error": "一条没有登记错误码的提示"}, status=400)

    async def declared(_request: web.Request) -> web.Response:
        return web.json_response({"ok": False, "error": "原文", "error_code": "game_not_found"}, status=404)

    async def declared_unknown(_request: web.Request) -> web.Response:
        return web.json_response({"ok": False, "error": "原文", "error_code": "SOME_OTHER_CODE"}, status=409)

    app = web.Application(middlewares=[request_language_middleware, error_code_middleware])
    app.router.add_get("/missing", missing)
    app.router.add_get("/specific", specific)
    app.router.add_get("/unregistered", unregistered)
    app.router.add_get("/declared", declared)
    app.router.add_get("/declared-unknown", declared_unknown)
    return app


@pytest.mark.asyncio
async def test_clients_that_do_not_ask_for_another_language_keep_the_original_chinese() -> None:
    async with TestClient(TestServer(_app())) as client:
        # 机器人 / 脚本不带头；中文浏览器；只列了不支持的语言——都保持原文与既有契约。
        for headers in ({}, {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}, {"Accept-Language": "fr-FR,fr;q=0.9"}):
            response = await client.get("/specific", headers=headers)
            assert await response.json() == {"ok": False, "error": "仅 GM 可删除游戏", "error_code": "gm_only"}
            assert "Content-Language" not in response.headers


@pytest.mark.asyncio
async def test_registered_errors_are_localized_for_the_negotiated_language() -> None:
    async with TestClient(TestServer(_app())) as client:
        english = {"Accept-Language": "en-US,en;q=0.9"}
        response = await client.get("/missing", headers=english)
        assert await response.json() == {"ok": False, "error": "Game not found", "error_code": "game_not_found"}
        # 内容随协商语言变化：缓存需要知道，客户端也能看到实际语言。
        assert response.headers["Content-Language"] == "en"
        assert "accept-language" in response.headers["Vary"].lower()
        assert (await (await client.get("/specific", headers=english)).json())["error"] == "GM only"
        # 社区语言没有译文时回退英文。
        japanese = await (await client.get("/missing", headers={"Accept-Language": "ja"})).json()
        assert japanese["error"] == "Game not found"


@pytest.mark.asyncio
async def test_unregistered_and_foreign_codes_pass_through_untouched() -> None:
    async with TestClient(TestServer(_app())) as client:
        english = {"Accept-Language": "en"}
        assert await (await client.get("/unregistered", headers=english)).json() == {
            "ok": False, "error": "一条没有登记错误码的提示",
        }
        assert await (await client.get("/declared-unknown", headers=english)).json() == {
            "ok": False, "error": "原文", "error_code": "SOME_OTHER_CODE",
        }
        # 处理函数自己声明了已登记的错误码：同样按协商语言给文案。
        assert (await (await client.get("/declared", headers=english)).json())["error"] == "Game not found"
