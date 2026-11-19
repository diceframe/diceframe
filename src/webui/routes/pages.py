"""页面路由 handler：Vue 首页 / 玩家页 / 登录页 / 静态资源。"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from aiohttp import web


NOINDEX_HEADER_VALUE = "noindex, nofollow, noarchive"


async def add_response_security_headers(
    request: web.Request,
    response: web.StreamResponse,
) -> None:
    response.headers["X-Robots-Tag"] = NOINDEX_HEADER_VALUE
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"


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


async def robots_txt(request: web.Request) -> web.Response:
    return web.Response(
        # Keep pages crawlable so search engines can observe the noindex
        # response header and remove URLs that were indexed previously.
        text="User-agent: *\nDisallow:\n",
        content_type="text/plain",
        charset="utf-8",
        headers={"Cache-Control": "no-cache"},
    )


async def v2_static_file(request: web.Request) -> web.FileResponse:
    filename = request.match_info.get("file", "")
    static_dir: Path = request.app["static_v2_dir"]
    path = (static_dir / filename).resolve()
    if static_dir.resolve() not in path.parents:
        raise web.HTTPForbidden(text="invalid static path")
    # Vite 构建产物按内容哈希命名（assets/Name-Hash.js）：同一哈希永远对应
    # 同一内容，升级后 index.html（no-cache）引用的是新哈希，旧缓存条目自然
    # 作废。对它们发 immutable 长缓存，启动时不再逐 chunk 回源重验证——
    # 此前全部 no-cache，更新重启窗口里任何一次重验证失败就会渲染出无样式
    # 页面，且要手动重启才恢复（用户实测复现）。根级非哈希文件仍走 no-cache。
    assets_dir = (static_dir / "assets").resolve()
    cache = ("public, max-age=31536000, immutable" if assets_dir in path.parents
             else "no-cache")
    return web.FileResponse(path, headers={"Content-Type": _guess_content_type(path), "Cache-Control": cache})


_CHARSET_SUFFIXES = (".js", ".mjs", ".css", ".json", ".html", ".htm", ".svg", ".xml", ".txt")
_STATIC_CONTENT_TYPES = {
    # Windows registry MIME associations can label scripts as text/plain.
    # Pin frontend types so browsers can load modules with strict MIME checks.
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".svg": "image/svg+xml",
    ".avif": "image/avif",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}


def _guess_content_type(path: Path) -> str:
    mime = _STATIC_CONTENT_TYPES.get(path.suffix.lower())
    if not mime:
        mime, _ = mimetypes.guess_type(path.name)
    if not mime:
        mime = "application/octet-stream"
    if path.suffix.lower() in _CHARSET_SUFFIXES:
        return f"{mime}; charset=utf-8"
    return mime


def register_pages(app: web.Application) -> None:
    app.router.add_get("/robots.txt", robots_txt)
    app.router.add_get("/", index)
    app.router.add_get("/player", player_page)
    app.router.add_get("/player.html", player_page)
    app.router.add_get("/v2-preview/", index)
    app.router.add_get("/login", login_page)
    app.router.add_get("/v2-assets/{file:.*}", v2_static_file)
