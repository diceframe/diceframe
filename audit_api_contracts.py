"""巡检 WebUI 路由与返回契约。

这个脚本不替代集成测试，只用于快速发现：
- 新增路由是否没有登记到契约清单
- handler 是否直接返回 404/原始数据，方便后续统一前端处理
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERVER_PATH = ROOT / "web_server.py"
ROUTES_DIR = ROOT / "src" / "webui" / "routes"
SOURCES = [SERVER_PATH] + sorted(ROUTES_DIR.glob("*.py"))

DATA_ENDPOINTS = {
    "/api/games",
    "/api/games/{game_key}/characters",
    "/api/games/{game_key}/log",
    "/api/worlds",
    "/api/world-templates",
    "/api/lorebook/{world_id}",
    "/api/rules",
    "/api/character-cards",
    "/api/config",
    "/api/games/{game_key}/map",
}

STREAM_ENDPOINTS = {
    "/api/games/{game_key}/stream",
    "/api/games/{game_key}/stream-action",
    "/api/games/{game_key}/sse",
}


ROUTE_RE = re.compile(
    r"\b\w+\.router\.add_(?P<method>get|post|put|delete)\((?P<args>.+)\)|"
    r"\b\w+\.router\.add_route\((?P<route_args>.+)\)",
)


def parse_routes(source: str) -> list[tuple[str, str, str]]:
    routes: list[tuple[str, str, str]] = []
    for line in source.splitlines():
        line = line.strip()
        match = ROUTE_RE.search(line)
        if not match:
            continue
        try:
            if match.group("route_args"):
                expr = ast.parse(f"f({match.group('route_args')})", mode="eval").body
                args = expr.args
                method = ast.literal_eval(args[0]).upper()
                path = ast.literal_eval(args[1])
                handler = args[2].id if hasattr(args[2], "id") else ast.unparse(args[2])
            else:
                expr = ast.parse(f"f({match.group('args')})", mode="eval").body
                args = expr.args
                method = match.group("method").upper()
                path = ast.literal_eval(args[0])
                handler = args[1].id if hasattr(args[1], "id") else ast.unparse(args[1])
            routes.append((method, path, handler))
        except Exception:
            continue
    return routes


def main() -> int:
    handler_bodies: dict[str, str] = {}
    routes: list[tuple[str, str, str]] = []
    for path in SOURCES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                handler_bodies.setdefault(node.name, "\n".join(lines[node.lineno - 1: node.end_lineno]))
        routes.extend(parse_routes(source))
    print("API route contract audit:")
    for method, path, handler in routes:
        body = handler_bodies.get(handler, "")
        has_ok = '"ok"' in body or "'ok'" in body
        has_error = '"error"' in body or "'error'" in body
        has_status = "status=" in body
        marker = "ok"
        if path in DATA_ENDPOINTS:
            marker = "data"
        elif path in STREAM_ENDPOINTS:
            marker = "stream"
        elif "_get_api(request)." in body or "api.resolve_payment(" in body:
            marker = "delegated"
        elif path.startswith("/api/") and not (has_ok or has_error or has_status):
            marker = "review"
        print(f"  - [{marker}] {method:6} {path} -> {handler}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
