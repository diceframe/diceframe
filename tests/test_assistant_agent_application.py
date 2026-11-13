from types import SimpleNamespace

import pytest

from src.webui.services.assistant_agent_application import AgentApplicationDependencies, application_tools
from src.webui.services.assistant_agent import validate_arguments
from src.engine.game_instance import GameInstance
from src.engine.modules import narrative_notes, session_stats
from src.creator.projects import CreatorProjectStore
from src.creator.worker_jobs import WorkerJobStore


def deps(**overrides):
    values = dict(catalogs={"world_templates": lambda: {"templates": [{"world_id": "harbor", "name": "Harbor"}]},
        "characters": lambda: {"cards": [{"id": "hero", "character_name": "Hero"}]}, "lorebooks": lambda: {"books": []}},
        get_game=lambda key: None, read_game=lambda *a, **kw: {}, create_game=lambda **kw: {"ok": True, "game_key": "web|one"},
        content=None, set_player_control=lambda *a: {}, submit_action=lambda *a, **kw: {},
        generate_rule=lambda *a: {}, generate_image=lambda **kw: {}, invoke_plugin_tool=lambda *a: {},
        update_plugin=lambda *a: {}, runtime_logs=lambda: ("", 0))
    values.update(overrides)
    return AgentApplicationDependencies(**values)


def tool(dependencies, name):
    return next(t for t in application_tools(dependencies) if t.name == name)


@pytest.mark.parametrize("change", [
    lambda inst: narrative_notes.replace_scene(inst, "changed scene"),
    lambda inst: session_stats.replace_last_activity(inst, "changed activity"),
])
def test_game_approval_guard_tracks_canonical_module_state(change):
    inst = GameInstance(game_key=("web", "approval", "web_bot"))
    inst.gm_uid = "alice"
    action = tool(deps(get_game=lambda key: inst), "submit_action")
    args = {"game_key": "g", "text": "look around"}
    before = action.guard(args, "alice")
    change(inst)
    assert action.guard(args, "alice") != before


@pytest.mark.asyncio
async def test_create_game_uses_existing_world_and_injects_owner(tmp_path):
    calls = []
    async def create(**kw):
        calls.append(kw)
        return {"ok": True, "game_key": "web|one"}
    dependencies = deps(create_game=create)
    create_tool = tool(dependencies, "create_game")
    assert create_tool.mutation
    args = {"world_id": "harbor", "game_name": "At the harbor", "character_card_ids": ["hero"]}
    result = await create_tool.handler(validate_arguments(args, create_tool.schema), "alice")
    assert calls[0]["gm_uid"] == "alice"
    assert calls[0]["world_id"] == "harbor"
    assert calls[0]["players"][0]["character_name"] == "Hero"
    assert calls[0]["book_bindings"] == []
    assert result["navigation"] == {"route": "play", "query": {"game": "web|one"}}
    with pytest.raises(ValueError, match="World"):
        await create_tool.handler({"world_id": "invented"}, "alice")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_create_game_resolves_actual_adventure_id_and_explicit_book_refs():
    calls = []
    async def create(**kw):
        calls.append(kw)
        return {"ok": True, "game_key": "g"}
    values = deps(create_game=create)
    values.catalogs["adventures"] = lambda: {"adventures": [{"adventure_id": "user:quest", "source_kind": "user", "source_id": "quest"}]}
    ref = {"source_kind": "user", "source_id": "book", "kind": "lorebook", "id": "book", "digest": ""}
    values.catalogs["lorebooks"] = lambda: {"books": [{"id": "book", "ref": ref}]}
    create_tool = tool(values, "create_game")
    await create_tool.handler({"world_id": "harbor", "character_card_ids": ["hero"], "lorebook_ids": ["book"],
        "adventure_id": "user:quest", "adventure_source_kind": "user", "adventure_source_id": "quest"}, "alice")
    assert calls[0]["book_bindings"][0]["ref"] == ref
    assert calls[0]["play_mode"] == "adventure"


@pytest.mark.asyncio
async def test_navigation_only_accepts_local_known_routes():
    nav = tool(deps(), "navigate")
    with pytest.raises(ValueError):
        validate_arguments({"route": "javascript:alert(1)"}, nav.schema)
    assert (await nav.handler({"route": "creator"}, "alice"))["navigation"]["route"] == "creator"


@pytest.mark.asyncio
async def test_game_mutation_checks_game_exists_and_actor_is_owner():
    calls = []
    async def submit(*args, **kwargs):
        calls.append((args, kwargs))
        return {"payload": {"advanced": True}, "status": 200}
    inst = SimpleNamespace(gm_uid="bob", players={"alice": {}}, round_number=2, state=SimpleNamespace(value="active"), run_id="run", last_activity="today", player_controls={})
    act = tool(deps(get_game=lambda key: inst, submit_action=submit), "submit_action")
    assert act.mutation
    await act.handler({"game_key": "g", "text": "look around"}, "alice")
    assert calls[0][0] == ("g", "alice", "look around")
    assert calls[0][1]["server_roll"] is True
    with pytest.raises(ValueError):
        validate_arguments({"game_key": "g", "text": "act", "user_id": "bob", "d20": 20}, act.schema)
    with pytest.raises(PermissionError):
        await act.handler({"game_key": "g", "text": "act"}, "charlie")


@pytest.mark.asyncio
async def test_plugin_arguments_schema_is_checked_by_host_and_no_model_context_privileges():
    calls = []
    async def invoke(*args):
        calls.append(args)
        return {"ok": True, "result": {}}
    dependencies = deps(invoke_plugin_tool=invoke)
    dependencies.catalogs['plugin_tools'] = lambda: {'tools': [{'plugin_id': 'p', 'name': 't',
        'title': 'Read tool', 'description': '', 'input_schema': {'type': 'object'}, 'agent_effect': 'read_only'}]}
    plugin = tool(dependencies, "invoke_plugin_tool")
    assert plugin.mutation
    await plugin.handler({"plugin_id": "p", "tool_name": "t", "arguments": {}}, "alice")
    assert len(calls[0]) == 3
    dependencies.catalogs['plugin_tools'] = lambda: {'tools': []}
    with pytest.raises(PermissionError):
        await plugin.handler({'plugin_id': 'p', 'tool_name': 't', 'arguments': {}}, 'alice')
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_creator_agent_tools_use_the_selected_author_project_and_revisions(tmp_path):
    project_store = CreatorProjectStore(tmp_path / "projects.json")
    built = []
    async def build(owner, project_id, *, expected_revision):
        built.append((owner, project_id, expected_revision))
        return {"ok": True, "payload": b"secret bytes", "artifact_sha256": "abc"}
    dependencies = deps(creator_projects=project_store, build_creator_project=build)
    create = tool(dependencies, "creator_project_create")
    created = await create.handler({"name": "Agent Harbor"}, "alice")
    project_id = created["project"]["id"]
    update = tool(dependencies, "creator_project_update")
    result = await update.handler({
        "project_id": project_id,
        "expected_revision": 0,
        "files": {"README.md": "draft"},
    }, "alice")
    assert result["project"]["revision"] == 1
    validate = tool(dependencies, "creator_project_validate")
    assert (await validate.handler({"project_id": project_id}, "alice"))["ok"] is False
    preview = tool(dependencies, "creator_project_preview")
    assert (await preview.handler({"project_id": project_id}, "alice"))["ok"] is False
    capabilities = tool(dependencies, "creator_project_execution_capabilities")
    capability_result = await capabilities.handler({"project_id": project_id}, "alice")
    assert capability_result["sandbox_available"] is False
    assert capability_result["accepted_source_trust"] == ["trusted"]
    build_tool = tool(dependencies, "creator_project_build")
    build_result = await build_tool.handler({"project_id": project_id, "expected_revision": 1}, "alice")
    assert build_result == {"ok": True, "artifact_sha256": "abc"}
    assert built == [("alice", project_id, 1)]
    submission = tool(dependencies, "creator_project_submission")
    submission_result = await submission.handler({"project_id": project_id, "expected_revision": 1}, "alice")
    assert submission_result["submission"]["requires_manual_upload"] is True
    read = tool(dependencies, "creator_project_read")
    assert (await read.handler({"project_id": project_id}, "alice"))["project"]["files"]["README.md"] == "draft"
    with pytest.raises(KeyError):
        await read.handler({"project_id": project_id}, "bob")


@pytest.mark.asyncio
async def test_creator_agent_can_read_durable_worker_receipts(tmp_path):
    project_store = CreatorProjectStore(tmp_path / "projects.json")
    jobs = WorkerJobStore(tmp_path / "worker-jobs.json")
    dependencies = deps(creator_projects=project_store, worker_jobs=jobs)
    created = await tool(dependencies, "creator_project_create").handler(
        {"name": "Worker receipts"}, "alice",
    )
    project_id = created["project"]["id"]
    jobs.create("alice", project_id, ["python", "-c", "pass"], idempotency_key="test")
    capability = await tool(dependencies, "creator_project_execution_capabilities").handler(
        {"project_id": project_id}, "alice",
    )
    assert capability["receipt_recovery_available"] is True
    listed = await tool(dependencies, "creator_project_worker_jobs").handler(
        {"project_id": project_id}, "alice",
    )
    assert len(listed["jobs"]) == 1


@pytest.mark.asyncio
async def test_creator_agent_worker_run_is_revision_scoped_and_explicit(tmp_path):
    project_store = CreatorProjectStore(tmp_path / "projects.json")
    jobs = WorkerJobStore(tmp_path / "worker-jobs.json")
    calls = []

    async def run(owner, project_id, **kwargs):
        calls.append((owner, project_id, kwargs))
        return {"ok": True, "result": {"status": "completed"}}

    dependencies = deps(
        creator_projects=project_store,
        worker_jobs=jobs,
        run_creator_worker=run,
    )
    created = await tool(dependencies, "creator_project_create").handler(
        {"name": "Worker run"}, "alice",
    )
    project_id = created["project"]["id"]
    capability = await tool(dependencies, "creator_project_execution_capabilities").handler(
        {"project_id": project_id}, "alice",
    )
    assert capability["worker_available"] is True
    worker = tool(dependencies, "creator_project_worker_run")
    result = await worker.handler({
        "project_id": project_id,
        "expected_revision": 0,
        "command": ["python", "-c", "pass"],
        "source_trust": "trusted",
    }, "alice")
    assert result["ok"] is True
    assert calls[0][0:2] == ("alice", project_id)
    assert calls[0][2]["source_trust"] == "trusted"
