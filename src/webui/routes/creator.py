"""Creator Studio project routes (C01)."""

from __future__ import annotations

from aiohttp import web

from src.webui.routes._common import _get_api, _require_confirmed_request


def _owner_id(request: web.Request) -> str:
    if not request.get("owner_authenticated") or not request.get("user_id"):
        raise web.HTTPUnauthorized(text="Creator 工程需要已认证作者")
    return str(request.get("user_id"))


async def api_creator_projects(request: web.Request) -> web.Response:
    return web.json_response(_get_api(request).list_creator_projects(_owner_id(request)))


async def api_creator_project_worker_jobs(request: web.Request) -> web.Response:
    try:
        result = _get_api(request).list_creator_worker_jobs(
            _owner_id(request), request.match_info["project_id"],
        )
    except (KeyError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    return web.json_response(result)


async def api_creator_project_worker_run(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        result = await _get_api(request).run_creator_worker(
            _owner_id(request), request.match_info["project_id"],
            expected_revision=body.get("expected_revision"),
            command=body.get("command"),
            cwd=body.get("cwd", "."),
            source_trust=body.get("source_trust", "trusted"),
            idempotency_key=body.get("idempotency_key"),
        )
    except (KeyError, ValueError, PermissionError, TypeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=409)
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def api_creator_project_create(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        result = _get_api(request).create_creator_project(
            _owner_id(request),
            body.get("name", ""),
            body.get("source"),
        )
    except (TypeError, ValueError, PermissionError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response(result, status=201)


async def api_creator_project(request: web.Request) -> web.Response:
    try:
        result = _get_api(request).read_creator_project(
            _owner_id(request), request.match_info["project_id"],
        )
    except (KeyError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    return web.json_response(result)


async def api_creator_project_update(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        result = _get_api(request).update_creator_project(
            _owner_id(request),
            request.match_info["project_id"],
            expected_revision=body.get("expected_revision"),
            files=body.get("files"),
            remove=body.get("remove"),
            name=body.get("name"),
        )
    except KeyError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    except (TypeError, ValueError, PermissionError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=409 if "版本已变化" in str(exc) else 400)
    return web.json_response(result)


async def api_creator_project_validate(request: web.Request) -> web.Response:
    try:
        result = _get_api(request).validate_creator_project(
            _owner_id(request), request.match_info["project_id"],
        )
    except (KeyError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def api_creator_project_preview(request: web.Request) -> web.Response:
    try:
        result = _get_api(request).preview_creator_project(
            _owner_id(request), request.match_info["project_id"],
        )
    except (KeyError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def api_creator_project_build(request: web.Request) -> web.StreamResponse:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        result = _get_api(request).build_creator_project(
            _owner_id(request),
            request.match_info["project_id"],
            expected_revision=body.get("expected_revision"),
        )
    except (KeyError, ValueError, PermissionError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=409)
    if not result.get("ok"):
        return web.json_response(result, status=422)
    return web.Response(
        body=result["payload"],
        content_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{result["filename"]}"'},
        )


async def api_creator_project_submission(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        result = _get_api(request).prepare_creator_submission(
            _owner_id(request), request.match_info["project_id"],
            expected_revision=body.get("expected_revision"),
        )
    except (KeyError, ValueError, PermissionError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=409)
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def api_creator_project_delete(request: web.Request) -> web.Response:
    denied = _require_confirmed_request(request)
    if denied is not None:
        return denied
    try:
        result = _get_api(request).delete_creator_project(
            _owner_id(request), request.match_info["project_id"],
        )
    except (KeyError, ValueError, PermissionError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    return web.json_response(result)


def register_creator(app: web.Application) -> None:
    app.router.add_get("/api/creator/projects", api_creator_projects)
    app.router.add_post("/api/creator/projects", api_creator_project_create)
    app.router.add_get("/api/creator/projects/{project_id}", api_creator_project)
    app.router.add_get("/api/creator/projects/{project_id}/worker-jobs", api_creator_project_worker_jobs)
    app.router.add_post("/api/creator/projects/{project_id}/worker-run", api_creator_project_worker_run)
    app.router.add_patch("/api/creator/projects/{project_id}", api_creator_project_update)
    app.router.add_post("/api/creator/projects/{project_id}/validate", api_creator_project_validate)
    app.router.add_post("/api/creator/projects/{project_id}/preview", api_creator_project_preview)
    app.router.add_post("/api/creator/projects/{project_id}/build", api_creator_project_build)
    app.router.add_post("/api/creator/projects/{project_id}/submission", api_creator_project_submission)
    app.router.add_delete("/api/creator/projects/{project_id}", api_creator_project_delete)
