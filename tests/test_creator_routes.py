from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from src.webui.routes.creator import register_creator


class FakeCreatorApi:
    def preview_creator_project(self, owner_id, project_id):
        return {"ok": True, "owner": owner_id, "project_id": project_id, "preview": {"name": "Harbor"}}

    def prepare_creator_submission(self, owner_id, project_id, *, expected_revision):
        return {
            "ok": True,
            "owner": owner_id,
            "project_id": project_id,
            "submission": {"release_tag": "v1.0.0", "artifact_sha256": "abc"},
            "revision": expected_revision,
        }


@pytest.fixture
def creator_app():
    @web.middleware
    async def identity(request, handler):
        request["owner_authenticated"] = True
        request["user_id"] = "alice"
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app["api"] = FakeCreatorApi()
    register_creator(app)
    return app


@pytest.mark.asyncio
async def test_creator_preview_and_submission_routes_keep_owner_and_confirmation_boundaries(creator_app):
    async with TestClient(TestServer(creator_app)) as client:
        preview = await client.post("/api/creator/projects/project-harbor/preview")
        assert preview.status == 200
        assert (await preview.json())["owner"] == "alice"

        denied = await client.post(
            "/api/creator/projects/project-harbor/submission",
            json={"expected_revision": 2},
        )
        assert denied.status == 403

        submitted = await client.post(
            "/api/creator/projects/project-harbor/submission",
            json={"expected_revision": 2},
            headers={"X-TRPG-Confirm": "true"},
        )
        assert submitted.status == 200
        payload = await submitted.json()
        assert payload["submission"]["release_tag"] == "v1.0.0"
        assert payload["revision"] == 2
