"""页面路由 handler：Vue 首页 / 玩家页 / 登录页 / 静态资源。"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from aiohttp import web


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(
        request.app["static_v2_dir"] / "index.html",
        headers={
            "Content-Type": "text/html; charset=utf-8",
            # index.html 引用带 hash 的 JS/CSS chunk；升级后旧 chunk 会被删除。
            # 不加 no-cache 时浏览器对 index.html 做启发式缓存，复用旧版会引用已删除的
            # chunk 导致 404 白屏(只有背景、需 F5 才恢复)。no-cache 让浏览器每次
            # 重新验证(配合已有的 ETag/Last-Modified 协商)，确保拿到最新 chunk 引用。
            "Cache-Control": "no-cache",
        },
    )


async def player_page(request: web.Request) -> web.FileResponse:
    return await index(request)


async def login_page(request: web.Request) -> web.FileResponse:
    return await index(request)


async def v2_static_file(request: web.Request) -> web.FileResponse:
    filename = request.match_info.get("file", "")
    static_dir: Path = request.app["static_v2_dir"]
    path = (static_dir / filename).resolve()
    if static_dir.resolve() not in path.parents:
        raise web.HTTPForbidden(text="invalid static path")
    return web.FileResponse(path, headers={"Content-Type": _guess_content_type(path), "Cache-Control": "no-cache"})


_CHARSET_SUFFIXES = (".js", ".mjs", ".css", ".json", ".html", ".htm", ".svg", ".xml", ".txt")


def _guess_content_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if not mime:
        mime = "application/octet-stream"
    if path.suffix.lower() in _CHARSET_SUFFIXES:
        return f"{mime}; charset=utf-8"
    return mime


def register_pages(app: web.Application) -> None:
    app.router.add_get("/", index)
    app.router.add_get("/player", player_page)
    app.router.add_get("/player.html", player_page)
    app.router.add_get("/v2-preview/", index)
    app.router.add_get("/login", login_page)
    app.router.add_get("/v2-assets/{file:.*}", v2_static_file)
