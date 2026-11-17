"""DF Agent adapters over explicit application services, never a WebAPI locator."""
from __future__ import annotations

from src.engine import module_hooks

import inspect
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.engine.modules import progression_state, session_stats
from src.webui.services._common import is_game_gm
from src.webui.services.assistant_agent import AgentTool, _digest

ROUTES = ("overview", "create", "play", "characters", "library", "creator", "map", "lorebook",
          "worlds", "adventures", "modules", "memory", "logs", "rules", "settings", "plugins")


def obj(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


def string(limit=200, choices=None, minimum=0):
    return {"type": "string", "maxLength": limit, "minLength": minimum, **({"enum": list(choices)} if choices else {})}


@dataclass(frozen=True)
class AgentApplicationDependencies:
    catalogs: dict[str, Callable]
    get_game: Callable
    read_game: Callable
    create_game: Callable
    content: Any
    set_player_control: Callable
    submit_action: Callable
    generate_rule: Callable
    generate_image: Callable
    invoke_plugin_tool: Callable
    update_plugin: Callable
    runtime_logs: Callable
    creator_projects: Any | None = None
    build_creator_project: Callable | None = None
    worker_jobs: Any | None = None
    run_creator_worker: Callable | None = None


async def _call(callback, *args, **kwargs):
    value = callback(*args, **kwargs)
    return await value if inspect.isawaitable(value) else value


def application_tools(deps: AgentApplicationDependencies) -> tuple[AgentTool, ...]:
    def game(key, owner):
        instance = deps.get_game(key)
        if not instance:
            raise ValueError("Game does not exist")
        if not is_game_gm(instance, owner, owner_authenticated=True):
            raise PermissionError("GM access required")
        return instance

    def game_guard(args, owner):
        instance = game(args["game_key"], owner)
        state = getattr(instance, "state", "")
        return _digest({
            "state": str(getattr(state, "value", state)),
            "run_id": instance.run_id,
            "round_number": progression_state.peek_round_value(instance),
            "last_activity": session_stats.last_activity(instance),
            "players": deepcopy(instance.players),
            "scene": deepcopy(module_hooks.narrative_view(instance)["scene"]),
        })

    def catalog_guard(args, owner):
        # Capture the selected canonical resources, not an entire unrelated
        # catalogue. A source edit/removal invalidates the approval.
        selected = resources(args)
        return _digest(selected)

    def game_creation_preview(args, owner):
        selected = resources(args)
        world = selected["world"]
        return {"target_name": args.get("game_name") or world.get("world_name") or world.get("name") or args["world_id"],
            "world_name": world.get("world_name") or world.get("name") or args["world_id"],
            "characters": [card["character_name"] for card in selected["cards"]]}

    def rows(kind):
        value = deps.catalogs[kind]()
        if not isinstance(value, dict):
            raise ValueError("Invalid catalogue result")
        return next((items for key, items in value.items() if isinstance(items, list)), [])

    def resources(args):
        world = next((w for w in rows("world_templates") if str(w.get("world_id") or w.get("id")) == args["world_id"]), None)
        if world is None:
            raise ValueError("World is unavailable; read world_templates first")
        rule_id = args.get("rule_id") or world.get("default_rule") or ""
        rule = None
        if rule_id:
            rule = next((r for r in rows("rules") if (r.get("id") or r.get("rule_id")) == rule_id), None)
            if rule is None:
                raise ValueError("Rule is unavailable; read rules first")
        adventure = None
        if args.get("adventure_id"):
            adventure = next((r for r in rows("adventures") if r.get("adventure_id") == args["adventure_id"]
                and str(r.get("source_kind") or "") == str(args.get("adventure_source_kind") or "")
                and str(r.get("source_id") or "") == str(args.get("adventure_source_id") or "")), None)
            if adventure is None:
                raise ValueError("Adventure source is unavailable; read adventures first")
        cards = []
        for card_id in args.get("character_card_ids", []):
            card = next((r for r in rows("characters") if r.get("id") == card_id), None)
            if card is None:
                raise ValueError("Character card is unavailable")
            cards.append(card)
        books = []
        for book_id in args.get("lorebook_ids", []):
            book = next((r for r in rows("lorebooks") if r.get("id") == book_id), None)
            if not book or not isinstance(book.get("ref"), dict):
                raise ValueError("Lorebook source is unavailable; read lorebooks first")
            books.append(book)
        if not cards:
            raise ValueError("Select at least one existing character card, or draft and save one first")
        return {"world": world, "rule": rule, "adventure": adventure, "cards": cards, "books": books}

    async def read_catalog(args, owner):
        return deps.catalogs[args["kind"]]()

    async def read_game(args, owner):
        game(args["game_key"], owner)
        return deps.read_game(args["game_key"], viewer_uid=owner, viewer_is_gm=True)

    async def navigate(args, owner):
        query = {key: args[key] for key in ("game", "section", "world", "book") if args.get(key)}
        return {"ok": True, "navigation": {"route": args["route"], **({"query": query} if query else {})}}

    async def create_game(args, owner):
        selected = resources(args)
        request = {key: value for key, value in args.items() if key not in {"character_card_ids", "lorebook_ids"}}
        request.update(gm_uid=owner, solo=args.get("solo", True), room_password=None)
        request["players"] = deepcopy(selected["cards"])
        request["play_mode"] = "adventure" if selected["adventure"] else "free"
        request["book_bindings"] = [{"ref": deepcopy(book["ref"]), "role": "runtime", "order": 110 + index}
                                    for index, book in enumerate(selected["books"])]
        # Existing game creation validates ruleset compatibility, source refs
        # and bindings, then rolls back the complete transaction on failure.
        result = await _call(deps.create_game, **request)
        if result.get("ok") and result.get("game_key"):
            result = {**result, "navigation": {"route": "play", "query": {"game": result["game_key"]}}}
        return result

    async def player_control(args, owner):
        instance = game(args["game_key"], owner)
        if args["user_id"] not in instance.players:
            raise ValueError("Player does not exist in this game")
        return await _call(deps.set_player_control, args["game_key"], args["user_id"], args["mode"])

    async def submit_action(args, owner):
        instance = game(args["game_key"], owner)
        if owner not in instance.players:
            raise PermissionError("Select your own actor in Play before submitting an action")
        result = await _call(deps.submit_action, args["game_key"], owner, args["text"], server_roll=True)
        return {"ok": result["status"] < 400, **result["payload"]}

    async def create_rule(args, owner):
        if args.get("source_rule_id") and not any((r.get("id") or r.get("rule_id")) == args["source_rule_id"] for r in rows("rules")):
            raise ValueError("Source rule unavailable")
        return await _call(deps.generate_rule, args["prompt"], args.get("source_rule_id", ""), args.get("language", "zh-CN"))

    async def generate_image(args, owner):
        # Owner is bound by the server, not chosen in model arguments.
        return await _call(deps.generate_image, prompt=args["prompt"], purpose="scene", owner_type="agent",
                           owner_id=owner, aspect_ratio=args.get("aspect_ratio", ""), style=args.get("style", ""))

    async def invoke_plugin(args, owner):
        plugin_descriptor(args, owner)
        result = await _call(deps.invoke_plugin_tool, args["plugin_id"], args["tool_name"], args["arguments"])
        payload = result.get("result")
        if isinstance(payload, dict) and payload.get("ok") is False:
            return {**result, "ok": False, "error": str(payload.get("error") or "Plugin tool reported failure")}
        return result

    def plugin_descriptor(args, owner):
        from src.webui.services.assistant_plugin_policy import require_agent_read_only_tool
        return require_agent_read_only_tool(rows('plugin_tools') if 'plugin_tools' in deps.catalogs else [],
                                           args['plugin_id'], args['tool_name'])

    async def update_plugin(args, owner):
        return await _call(deps.update_plugin, args["plugin_id"], {"enabled": args["enabled"]})

    async def diagnose(args, owner):
        text, count = deps.runtime_logs()
        return {"ok": True, "files": count, "redacted_logs": text}

    gk = {"game_key": string(500, minimum=1)}
    language = string(16, ("zh-CN", "en", "de", "ja", "ru"))
    tools = [
        AgentTool("read_catalog", "Read available application content", obj({"kind": string(40, deps.catalogs)}, ("kind",)), read_catalog),
        AgentTool("read_game", "Read the selected game", obj(gk, ("game_key",)), read_game),
        AgentTool("navigate", "Open a DiceFrame workspace", obj({"route": string(40, ROUTES), "game": string(500),
            "section": string(60), "world": string(), "book": string()}, ("route",)), navigate),
        AgentTool("create_game", "Create and start a game", obj({"world_id": string(minimum=1), "game_name": string(),
            "rule_id": string(), "solo": {"type": "boolean"}, "difficulty": string(20, ("轻松", "标准", "硬核")),
            "language": language, "adventure_id": string(), "adventure_source_kind": string(30), "adventure_source_id": string(),
            "character_card_ids": {"type": "array", "items": string(), "minItems": 1, "maxItems": 6},
            "lorebook_ids": {"type": "array", "items": string(), "maxItems": 16}}, ("world_id", "character_card_ids")), create_game,
            mutation=True, guard=catalog_guard, preview=game_creation_preview),
        AgentTool("set_player_control", "Change a player's human / AI control", obj({**gk, "user_id": string(minimum=1),
            "mode": string(20, ("human", "ai"))}, ("game_key", "user_id", "mode")), player_control, mutation=True, guard=game_guard),
        AgentTool("submit_action", "Submit an action as your own actor using server dice", obj({**gk, "text": string(4000, minimum=1)},
            ("game_key", "text")), submit_action, mutation=True, guard=game_guard),
        AgentTool("create_rule", "Generate a new validated custom rule", obj({"prompt": string(8000, minimum=1),
            "source_rule_id": string(), "language": language}, ("prompt",)), create_rule, mutation=True),
        AgentTool("generate_image", "Generate a scene image", obj({"prompt": string(12000, minimum=1),
            "style": string(500), "aspect_ratio": string(20)}, ("prompt",)), generate_image, mutation=True, effect='external'),
        AgentTool("invoke_plugin_tool", "Run an installed plugin tool through its host", obj({"plugin_id": string(minimum=1),
            "tool_name": string(minimum=1), "arguments": {"type": "object"}}, ("plugin_id", "tool_name", "arguments")), invoke_plugin, mutation=True,
            effect='external', guard=lambda args, owner: _digest(plugin_descriptor(args, owner)),
            preview=lambda args, owner: {'target_name': plugin_descriptor(args, owner)['title'], 'effect': 'read_only'}),
        AgentTool("set_plugin_enabled", "Enable or disable an installed plugin", obj({"plugin_id": string(minimum=1),
            "enabled": {"type": "boolean"}}, ("plugin_id", "enabled")), update_plugin, mutation=True, effect='external'),
        AgentTool("diagnose_runtime", "Read recent redacted runtime logs for diagnosis", obj(), diagnose),
    ]
    if deps.content:
        async def draft(args, owner):
            return await deps.content.draft(args["kind"], args["prompt"], args.get("language", "zh-CN"))

        async def save(checked, owner):
            result = deps.content.commit(checked)
            if result.get("ok"):
                targets = {r["target"] for r in result.get("results", [])}
                kind = next(iter(targets)) if len(targets) == 1 else "mixed"
                route = {"world": "worlds", "lorebook": "lorebook", "adventure": "adventures", "character_template": "characters"}.get(kind, "creator")
                result = {**result, "navigation": {"route": route}}
            return result

        tools.extend([
            AgentTool("draft_content", "Write a reviewable world, lorebook, adventure, character or other text", obj({
                "kind": string(30, ("world", "lorebook", "adventure", "character_template", "text")),
                "prompt": string(8192, minimum=1), "language": language}, ("kind", "prompt")), draft, creates_draft=True),
            AgentTool("save_content", "Save the reviewed content draft", obj({"draft_id": string(32, minimum=32)}, ("draft_id",)),
                      save, mutation=True, uses_draft=True),
        ])
    if deps.creator_projects is not None:
        project_guard = lambda _args, owner: _digest(deps.creator_projects.list(owner))

        async def creator_project_list(args, owner):
            return deps.creator_projects.list(owner)

        async def creator_project_create(args, owner):
            return deps.creator_projects.create(
                owner, args["name"], source=args.get("source"),
            )

        async def creator_project_read(args, owner):
            return deps.creator_projects.read(owner, args["project_id"])

        async def creator_project_validate(args, owner):
            return deps.creator_projects.validate(owner, args["project_id"])

        async def creator_project_preview(args, owner):
            preview = getattr(deps.creator_projects, "preview", None)
            if not callable(preview):
                raise ValueError("Creator 工程预览器尚未就绪")
            return preview(owner, args["project_id"])

        async def creator_project_execution_capabilities(args, owner):
            # Report the deployment boundary explicitly so the agent cannot
            # infer that a bounded local worker is an upload sandbox.
            deps.creator_projects.read(owner, args["project_id"])
            return {
                "ok": True,
                "project_id": args["project_id"],
                "worker_available": deps.run_creator_worker is not None,
                "sandbox_available": False,
                "receipt_recovery_available": deps.worker_jobs is not None,
                "accepted_source_trust": ["trusted"],
                "reason": "untrusted source requires an isolated deployment backend",
            }

        async def creator_project_build(args, owner):
            if deps.build_creator_project is None:
                raise ValueError("Creator 工程构建器尚未就绪")
            result = await _call(
                deps.build_creator_project,
                owner,
                args["project_id"],
                expected_revision=args["expected_revision"],
            )
            if isinstance(result, dict) and "payload" in result:
                result = {key: value for key, value in result.items() if key != "payload"}
            return result

        async def creator_project_submission(args, owner):
            if deps.build_creator_project is None:
                raise ValueError("Creator 工程构建器尚未就绪")
            result = await _call(
                deps.build_creator_project,
                owner,
                args["project_id"],
                expected_revision=args["expected_revision"],
            )
            if not isinstance(result, dict):
                raise ValueError("Creator 工程构建回执无效")
            if not result.get("ok"):
                return result
            manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else {}
            version = str(manifest.get("version") or "0.0.0")
            return {
                key: value for key, value in result.items() if key != "payload"
            } | {
                "submission": {
                    "filename": result.get("filename", ""),
                    "release_tag": f"v{version}",
                    "artifact_sha256": result.get("artifact_sha256", ""),
                    "revision": result.get("revision"),
                    "requires_manual_upload": True,
                    "requires_user_review": True,
                },
            }

        async def creator_project_update(args, owner):
            return deps.creator_projects.update(
                owner,
                args["project_id"],
                expected_revision=args["expected_revision"],
                files=args.get("files"),
                remove=args.get("remove"),
                name=args.get("name"),
            )

        async def creator_project_delete(args, owner):
            return deps.creator_projects.delete(owner, args["project_id"])

        tools.extend([
            AgentTool(
                "creator_project_list",
                "List the author's Creator Studio projects",
                obj(),
                creator_project_list,
                domain="creator",
            ),
            AgentTool(
                "creator_project_create",
                "Create a scoped Creator Studio project",
                obj({
                    "name": string(160, minimum=1),
                    "source": {"type": "object"},
                }, ("name",)),
                creator_project_create,
                mutation=True,
                domain="creator",
            ),
            AgentTool(
                "creator_project_read",
                "Read one scoped Creator Studio project",
                obj({"project_id": string(64, minimum=1)}, ("project_id",)),
                creator_project_read,
                domain="creator",
            ),
            AgentTool(
                "creator_project_validate",
                "Validate the selected Creator Studio project revision",
                obj({"project_id": string(64, minimum=1)}, ("project_id",)),
                creator_project_validate,
                domain="creator",
            ),
            AgentTool(
                "creator_project_preview",
                "Preview public Creator Studio project metadata and resource counts",
                obj({"project_id": string(64, minimum=1)}, ("project_id",)),
                creator_project_preview,
                domain="creator",
            ),
            AgentTool(
                "creator_project_execution_capabilities",
                "Report whether this deployment can safely execute Creator source",
                obj({"project_id": string(64, minimum=1)}, ("project_id",)),
                creator_project_execution_capabilities,
                domain="creator",
            ),
            AgentTool(
                "creator_project_update",
                "Apply a revision-checked Creator Studio project edit",
                obj({
                    "project_id": string(64, minimum=1),
                    "expected_revision": {"type": "integer", "minimum": 0},
                    "files": {"type": "object"},
                    "remove": {"type": "array", "items": string(240), "maxItems": 256},
                    "name": string(160),
                }, ("project_id", "expected_revision")),
                creator_project_update,
                mutation=True,
                guard=project_guard,
                domain="creator",
            ),
            AgentTool(
                "creator_project_delete",
                "Delete the selected Creator Studio project after explicit approval",
                obj({"project_id": string(64, minimum=1)}, ("project_id",)),
                creator_project_delete,
                mutation=True,
                effect="destructive",
                domain="creator",
            ),
        ])
        if deps.worker_jobs is not None:
            async def creator_project_worker_jobs(args, owner):
                deps.creator_projects.read(owner, args["project_id"])
                return deps.worker_jobs.list(owner, args["project_id"])

            tools.append(AgentTool(
                "creator_project_worker_jobs",
                "Read durable Creator worker job receipts and interrupted work",
                obj({"project_id": string(64, minimum=1)}, ("project_id",)),
                creator_project_worker_jobs,
                domain="creator",
            ))
        if deps.run_creator_worker is not None:
            async def creator_project_worker_run(args, owner):
                return await _call(
                    deps.run_creator_worker,
                    owner,
                    args["project_id"],
                    expected_revision=args["expected_revision"],
                    command=args["command"],
                    cwd=args.get("cwd", "."),
                    source_trust=args.get("source_trust", "trusted"),
                    idempotency_key=args.get("idempotency_key") or None,
                )

            tools.append(AgentTool(
                "creator_project_worker_run",
                "Run one bounded command for the selected Creator project revision",
                obj({
                    "project_id": string(64, minimum=1),
                    "expected_revision": {"type": "integer", "minimum": 0},
                    "command": {"type": "array", "items": string(4096), "maxItems": 64, "minItems": 1},
                    "cwd": string(240),
                    "source_trust": string(16, choices=("trusted", "untrusted")),
                    "idempotency_key": string(160),
                }, ("project_id", "expected_revision", "command")),
                creator_project_worker_run,
                mutation=True,
                guard=project_guard,
                domain="creator",
            ))
        if deps.build_creator_project is not None:
            tools.append(AgentTool(
                "creator_project_build",
                "Build and validate a fixed Creator Studio project revision",
                obj({
                    "project_id": string(64, minimum=1),
                    "expected_revision": {"type": "integer", "minimum": 0},
                }, ("project_id", "expected_revision")),
                creator_project_build,
                mutation=True,
                guard=project_guard,
                domain="creator",
            ))
            tools.append(AgentTool(
                "creator_project_submission",
                "Prepare a reviewed Creator Studio release receipt without publishing",
                obj({
                    "project_id": string(64, minimum=1),
                    "expected_revision": {"type": "integer", "minimum": 0},
                }, ("project_id", "expected_revision")),
                creator_project_submission,
                mutation=True,
                guard=project_guard,
                domain="creator",
            ))
    return tuple(tools)
