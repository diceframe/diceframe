"""Agent adapters for live gameplay operations.

The adapter deliberately contains no game mechanics.  It turns agent arguments
into the existing application service contracts and supplies the viewer, run
and revision fences those contracts require.  A composition root injects the
small owner callbacks; this module never receives a WebAPI facade.
"""
from __future__ import annotations

import inspect
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.engine.modules import adventure_runtime_state, progression_state
from src.webui.ruleset_rest_projection import public_rest_session
from src.webui.services._common import is_game_gm
from src.webui.services.assistant_agent import AgentTool, _digest
from src.webui.services.assistant_agent_application import obj, string


@dataclass(frozen=True)
class GameplayToolDependencies:
    """Explicit gameplay owner seams used by the agent adapter."""

    get_game: Callable[[str], Any]
    viewer_for: Callable[[Any, str], Any]
    adventure_projection: Callable[[Any, Any], dict] | None = None
    adventure_command: Callable[[Any, dict, str, bool], Any] | None = None
    complete_adventure_node: Callable[..., Any] | None = None
    request_adventure_choice: Callable[..., Any] | None = None
    run_story: Any | None = None
    recovery: Any | None = None
    rest: Any | None = None
    rest_decide: Callable[[str, str, bool, dict], Any] | None = None
    load_rule: Callable[[Any], Any] | None = None
    room_preferences: Callable[[str, Any], Any] | None = None
    set_room_preferences: Callable[[str, Any, dict], Any] | None = None
    available_ruleset_actions: Callable[[str, str, bool], Any] | None = None
    submit_ruleset_intent: Callable[[str, str, bool, dict], Any] | None = None


async def _call(callback: Callable, *args: Any, **kwargs: Any) -> Any:
    result = callback(*args, **kwargs)
    result = await result if inspect.isawaitable(result) else result
    if isinstance(result, dict) and 'status' in result and isinstance(result.get('payload'), dict):
        return {**result['payload'], 'status': result['status']}
    # A few application owners return TurnResult-like values.  Keep failed
    # domain results visible to AgentRuntime instead of treating an object as
    # an implicit success.
    if not isinstance(result, dict) and hasattr(result, "status"):
        status = str(getattr(result, "status", "") or "")
        payload = getattr(result, "payload", None)
        return {"ok": status in {"ok", "success", "completed"}, "status": status, "result": payload}
    return result


def _tool(*args: Any, domain: str, effect: str, automatic: bool = False,
          version: int = 1, **kwargs: Any) -> AgentTool:
    return AgentTool(*args, domain=domain, effect=effect, automatic=automatic, version=version, **kwargs)


def _payload(args: dict, reserved: set[str]) -> dict:
    payload = args.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    overlap = set(payload) & reserved
    if overlap:
        raise ValueError("payload cannot override reserved fields: " + ", ".join(sorted(overlap)))
    return deepcopy(payload)


def gameplay_tools(deps: GameplayToolDependencies) -> tuple[AgentTool, ...]:
    game_prop = {"game_key": string(240, minimum=1)}
    run_prop = {**game_prop, "run_id": string(240, minimum=1)}

    def instance(args: dict, owner: str, *, member: bool = True) -> tuple[Any, Any]:
        game = deps.get_game(args["game_key"])
        if game is None:
            raise ValueError("Game does not exist")
        viewer = deps.viewer_for(game, owner)
        if member and not getattr(viewer, "is_member", False):
            raise PermissionError("Game membership required")
        return game, viewer

    def gm_instance(args: dict, owner: str) -> tuple[Any, Any]:
        game, viewer = instance(args, owner)
        # Agent owner authentication does not grant GM authority for a live
        # game.  Only the viewer's established GM role may cross this gate.
        if not getattr(viewer, "is_gm", False) and not is_game_gm(game, owner, owner_authenticated=False):
            raise PermissionError("GM access required")
        return game, viewer

    def game_guard(args: dict, owner: str) -> str:
        game, _viewer = instance(args, owner)
        return _digest({"game_key": args["game_key"], "run_id": game.run_id,
                        "round": progression_state.peek_round_value(game),
                        "activity": getattr(game, "state", "")})

    def gameplay_status(args: dict, owner: str) -> dict:
        game, viewer = instance(args, owner)
        result: dict[str, Any] = {"ok": True, "game_key": args["game_key"], "run_id": game.run_id,
                                  "viewer_uid": getattr(viewer, "uid", ""), "is_gm": bool(getattr(viewer, "is_gm", False)),
                                  "round": progression_state.peek_round_value(game)}
        if deps.adventure_projection:
            result["adventure"] = deps.adventure_projection(game, viewer)
        else:
            result["adventure"] = {"progress": adventure_runtime_state.progress_snapshot(game)}
        if deps.run_story is not None:
            result["story"] = deps.run_story.get(args["game_key"], viewer)
        if deps.recovery is not None:
            result["recovery"] = deps.recovery.get(args["game_key"], viewer)
        if deps.room_preferences is not None:
            result["room_preferences"] = deps.room_preferences(args["game_key"], viewer)
        return result

    tools: list[AgentTool] = [_tool(
        "get_gameplay_status", "Read the live game, adventure, story, recovery and room status available to you",
        obj(game_prop, ("game_key",)), gameplay_status, domain="games", effect="read")]

    choice_props = {**run_prop, "node_id": string(240, minimum=1), "choice_id": string(240, minimum=1)}
    def choice_guard(args: dict, owner: str) -> str:
        game, viewer = instance(args, owner)
        if getattr(viewer, "uid", "") not in game.players:
            raise PermissionError("Player membership required")
        return _digest({"run_id": game.run_id, "progress": adventure_runtime_state.progress_snapshot(game)})

    async def request_choice(args: dict, owner: str) -> Any:
        game, viewer = instance(args, owner)
        if deps.adventure_command is None and deps.request_adventure_choice is None:
            raise ValueError("Adventure choice is unavailable")
        if args["run_id"] != game.run_id:
            raise ValueError("Run changed; refresh first")
        if deps.adventure_command is not None:
            return await _call(deps.adventure_command, game,
                               {"type": "adventure.choice.request", "node_id": args["node_id"],
                                "choice_id": args["choice_id"]}, getattr(viewer, "uid", ""), False)
        return await _call(deps.request_adventure_choice, game, args["node_id"], args["choice_id"], getattr(viewer, "uid", ""))

    tools.append(_tool("request_adventure_choice", "Record your choice for an active adventure node for GM confirmation",
        obj(choice_props, tuple(choice_props)), request_choice, mutation=True, guard=choice_guard,
        domain="adventures", effect="write"))

    complete_props = {**run_prop, "node_id": string(240, minimum=1), "choice_id": string(240), "actor_uid": string(240)}
    def complete_guard(args: dict, owner: str) -> str:
        game, _viewer = gm_instance(args, owner)
        return _digest({"run_id": game.run_id, "progress": adventure_runtime_state.progress_snapshot(game)})

    async def complete_node(args: dict, owner: str) -> Any:
        game, _viewer = gm_instance(args, owner)
        if args["run_id"] != game.run_id or (deps.adventure_command is None and deps.complete_adventure_node is None):
            raise ValueError("Adventure operation is unavailable or stale")
        if deps.adventure_command is not None:
            body = {"type": "adventure.node.complete", "node_id": args["node_id"]}
            if args.get("choice_id"):
                body["choice_id"] = args["choice_id"]
            return await _call(deps.adventure_command, game, body, owner, True)
        return await _call(deps.complete_adventure_node, game, args["node_id"],
                           recipient_uid=args.get("actor_uid", ""), actor_uid=owner,
                           choice_id=args.get("choice_id", ""))

    tools.append(_tool("complete_adventure_node", "GM-confirm an active adventure node through the authoritative runtime",
        obj(complete_props, ("game_key", "run_id", "node_id")), complete_node, mutation=True,
        guard=complete_guard, domain="adventures", effect="write"))

    if deps.run_story is not None:
        def read_story(args: dict, owner: str) -> Any:
            _game, viewer = instance(args, owner)
            return deps.run_story.get(args["game_key"], viewer)
        tools.append(_tool("get_run_story", "Read the canonical run plan, intermission and finale projection",
            obj(game_prop, ("game_key",)), read_story, domain="games", effect="read"))
        story_props = {**run_prop, "action": string(32, choices=("plan", "advance", "intermission", "prepare", "materials", "cancel", "commit")),
                       "payload": {"type": "object", "properties": {}, "additionalProperties": True}}
        def story_guard(args: dict, owner: str) -> str:
            game, viewer = gm_instance(args, owner)
            current = deps.run_story.get(args["game_key"], viewer)
            return _digest({"run_id": game.run_id, "projection": current})
        async def story_mutate(args: dict, owner: str) -> Any:
            game, viewer = gm_instance(args, owner)
            if args["run_id"] != game.run_id:
                raise ValueError("Run changed; refresh first")
            body = {"run_id": args["run_id"]}
            body.update(_payload(args, {"run_id", "operation_id", "expected_revision"}))
            return await _call(deps.run_story.mutate, args["game_key"], viewer, args["action"], body)
        tools.append(_tool("mutate_run_story", "GM perform one confirmed run plan, intermission or finale operation",
            obj(story_props, ("game_key", "run_id", "action")), story_mutate, mutation=True, guard=story_guard,
            domain="games", effect="write"))

    if deps.available_ruleset_actions is not None:
        async def read_ruleset_actions(args: dict, owner: str) -> Any:
            _game, viewer = instance(args, owner)
            result = await _call(deps.available_ruleset_actions, args["game_key"],
                                 getattr(viewer, "uid", ""), bool(getattr(viewer, "is_gm", False)))
            # 自由文本回合流程的规则没有结构化动作：这是明确的产品语义，不是
            # 故障。降级为"空列表 + 说明"，避免运行日志把每次探询都记成失败，
            # 也避免 agent 把 409 语义当成可重试错误。
            if not result.get("ok") and result.get("code") == "RULESET_INTENTS_UNAVAILABLE":
                return {"ok": True, "available": False, "actions": [],
                        "reason": str(result.get("error") or "free-text turn flow")}
            return result
        tools.append(_tool("get_ruleset_available_actions",
            "Read the current ruleset's canonical available actions for this player",
            obj(game_prop, ("game_key",)), read_ruleset_actions, domain="games", effect="read"))

    if deps.submit_ruleset_intent is not None:
        intent_props = {**run_prop, "intent": {
            "type": "object", "properties": {}, "additionalProperties": True,
        }}
        def ruleset_guard(args: dict, owner: str) -> str:
            game, viewer = instance(args, owner)
            return _digest({"run_id": game.run_id,
                            "ruleset_state": deepcopy((getattr(game, "modules", {}) or {}).get("ruleset_runtime", {})),
                            "viewer_uid": getattr(viewer, "uid", "")})
        async def submit_ruleset(args: dict, owner: str) -> Any:
            game, viewer = instance(args, owner)
            if args["run_id"] != game.run_id:
                raise ValueError("Run changed; refresh first")
            intent = deepcopy(args["intent"])
            if not isinstance(intent, dict) or "run_id" in intent:
                raise ValueError("intent must be an object without run_id")
            return await _call(deps.submit_ruleset_intent, args["game_key"],
                               getattr(viewer, "uid", ""), bool(getattr(viewer, "is_gm", False)), intent)
        tools.append(_tool("submit_ruleset_intent",
            "Submit one ruleset-owned structured intent after explicit confirmation; may change automation or policy",
            obj(intent_props, ("game_key", "run_id", "intent")), submit_ruleset,
            mutation=True, guard=ruleset_guard, domain="games", effect="external", version=2))

    recovery_props = {**run_prop, "action": string(32, choices=("request", "approve", "reject", "claim", "reward", "luck")),
                      "payload": {"type": "object", "properties": {}, "additionalProperties": True},
                      "expected_revision": {"type": "integer", "minimum": 0}}
    def recovery_guard(args: dict, owner: str) -> str:
        game, viewer = instance(args, owner)
        status = deps.recovery.get(args["game_key"], viewer)
        return _digest({"run_id": game.run_id, "revision": status.get("revision"), "sources": status.get("sources")})
    async def recovery_mutate(args: dict, owner: str) -> Any:
        game, viewer = instance(args, owner)
        if args["run_id"] != game.run_id:
            raise ValueError("Run changed; refresh first")
        body = {"run_id": args["run_id"], "operation_id": uuid.uuid4().hex,
                "expected_revision": args["expected_revision"]}
        body.update(_payload(args, {"run_id", "operation_id", "expected_revision"}))
        return await _call(deps.recovery.mutate, args["game_key"], viewer, args["action"], body)
    tools.append(_tool("mutate_resource_recovery", "Request, approve or claim an authoritative rest or CoC resource recovery",
        obj(recovery_props, ("game_key", "run_id", "action", "expected_revision")), recovery_mutate,
        mutation=True, guard=recovery_guard, domain="recovery", effect="write"))

    def read_recovery(args: dict, owner: str) -> Any:
        _game, viewer = instance(args, owner)
        return deps.recovery.get(args["game_key"], viewer)
    tools.append(_tool("get_recovery_status", "Read member-safe CoC recovery sources, receipts and intermissions",
        obj(game_prop, ("game_key",)), read_recovery, domain="recovery", effect="read"))

    if deps.room_preferences is not None:
        def read_room(args: dict, owner: str) -> Any:
            game, viewer = gm_instance(args, owner)
            return deps.room_preferences(args["game_key"], viewer)
        tools.append(_tool("get_room_preferences", "Read the current room and game preferences",
            obj(game_prop, ("game_key",)), read_room, domain="settings", effect="read"))
    if deps.set_room_preferences is not None:
        display_fields = {
            "quick_actions_enabled": {"type": "boolean"},
            "dice_reveal_mode": {"type": "string", "enum": ["auto", "click"]},
            "narrative_perspective": {"type": "string", "enum": ["auto", "immersive", "third_person"]},
            "story_mode": {"type": "string", "enum": ["prose", "dialogue"]},
        }
        policy_fields = {
            "advancement_authority": {"type": "string", "enum": ["ai_gm", "gm"]},
            "advancement_mode": {"type": "string", "enum": ["milestone", "xp"]},
            "gm_style_override": {"type": "object", "properties": {}, "additionalProperties": True},
            "push_enabled": {"type": "boolean"},
            "luck_timeout_seconds": {"type": "integer", "minimum": 0, "maximum": 3600},
        }
        def room_props(fields: dict) -> dict:
            return {**run_prop, "preferences": {"type": "object", "properties": fields,
                                                   "additionalProperties": False}}
        def room_guard(args: dict, owner: str) -> str:
            game, viewer = gm_instance(args, owner)
            return _digest(deps.room_preferences(args["game_key"], viewer) if deps.room_preferences else {})
        async def write_room(args: dict, owner: str) -> Any:
            game, viewer = gm_instance(args, owner)
            if args["run_id"] != game.run_id:
                raise ValueError("Run changed; refresh first")
            return await _call(deps.set_room_preferences, args["game_key"], viewer, deepcopy(args["preferences"]))
        def room_preview(args: dict, owner: str) -> dict:
            _game, viewer = gm_instance(args, owner)
            projection = deps.room_preferences(args["game_key"], viewer) if deps.room_preferences else {}
            before = projection.get('preferences', {})
            requested = args["preferences"]
            return {"changed_keys": sorted(key for key, value in requested.items()
                                            if before.get(key) != value),
                    "before": {key: before.get(key) for key in requested},
                    "after": deepcopy(requested)}
        tools.append(_tool("set_room_preferences", "Change explicitly supported reversible room preferences",
            obj(room_props(display_fields), ("game_key", "run_id", "preferences")), write_room, mutation=True,
            guard=room_guard, preview=room_preview, domain="settings", effect="write", automatic=True, version=2))
        tools.append(_tool("set_room_policy", "Change room policy requiring explicit GM confirmation",
            obj(room_props(policy_fields), ("game_key", "run_id", "preferences")), write_room, mutation=True,
            guard=room_guard, preview=room_preview, domain="settings", effect="external", automatic=False))

    if deps.rest is not None:
        rest_props = {**run_prop, "rest": string(16, choices=("short", "long")),
                      "expected_revision": {"type": "integer", "minimum": 0},
                      "hit_dice": {"type": "object", "properties": {}, "additionalProperties": True},
                      "confirm_elapsed_time": {"type": "boolean", "enum": [True]}}
        def rest_guard(args: dict, owner: str) -> str:
            game, viewer = instance(args, owner)
            uid = getattr(viewer, "uid", "")
            if uid not in game.players:
                raise PermissionError("Character membership required")
            character = game.get_character_sheet(uid)
            return _digest({"run_id": game.run_id, "uid": uid, "revision": character.get("ruleset_revision", 0)})
        async def rest_call(args: dict, owner: str) -> Any:
            game, viewer = instance(args, owner)
            if args["run_id"] != game.run_id:
                raise ValueError("Run changed; refresh first")
            if args.get("confirm_elapsed_time") is not True:
                raise ValueError("confirm_elapsed_time must be explicitly true")
            body = {"run_id": args["run_id"], "rest": args["rest"], "expected_revision": args["expected_revision"],
                    "confirm_elapsed_time": args["confirm_elapsed_time"], "operation_id": uuid.uuid4().hex,
                    "hit_dice": args.get("hit_dice") or {}}
            return await _call(deps.rest, args["game_key"], getattr(viewer, "uid", ""), body)
        def rest_preview(args: dict, owner: str) -> dict:
            game, viewer = instance(args, owner)
            uid = getattr(viewer, "uid", "")
            character = game.get_character_sheet(uid)
            return {"rest_type": args["rest"],
                    "target_name": game.players[uid].get('character_name', uid),
                    "target_character": {"uid": uid, "name": game.players[uid].get("character_name", uid)},
                    "time_confirmation": "Explicitly approve elapsed game time under the current ruleset; party rests wait for eligible participants.",
                    "hit_dice": deepcopy(args.get("hit_dice") or {}),
                    "confirm_elapsed_time": args.get('confirm_elapsed_time') is True,
                    "expected_revision": args["expected_revision"],
                    "character_revision": character.get("ruleset_revision", 0)}
        tools.append(_tool("perform_ruleset_rest", "Confirm a server-resolved professional short or long rest",
            obj(rest_props, ("game_key", "run_id", "rest", "expected_revision", "confirm_elapsed_time")), rest_call,
            mutation=True, guard=rest_guard, preview=rest_preview, domain="ruleset", effect="external", automatic=False, version=2))

    if deps.rest_decide is not None:
        # 队伍休息在全员申报后停在 awaiting_approval：没有这个工具，AI GM 局
        # 会永久卡在等一个永不发生的裁决。
        decide_props = {**run_prop, "approve": {"type": "boolean"}}
        def decide_guard(args: dict, owner: str) -> str:
            game, _viewer = gm_instance(args, owner)
            session = public_rest_session(game)
            return _digest({"run_id": game.run_id, "rest_status": session.get("status"),
                            "rest": session.get("rest"), "approve": args["approve"]})
        async def decide_call(args: dict, owner: str) -> Any:
            game, viewer = gm_instance(args, owner)
            if args["run_id"] != game.run_id:
                raise ValueError("Run changed; refresh first")
            body = {"approve": bool(args["approve"]), "operation_id": uuid.uuid4().hex}
            return await _call(deps.rest_decide, args["game_key"], getattr(viewer, "uid", ""), True, body)
        def decide_preview(args: dict, owner: str) -> dict:
            game, _viewer = gm_instance(args, owner)
            session = public_rest_session(game)
            return {"rest_status": session.get("status"), "rest": session.get("rest"),
                    "participants": [row.get("character_name") for row in session.get("participants", [])],
                    "time_advance_minutes": session.get("duration_minutes") if args["approve"] else 0,
                    "approve": bool(args["approve"])}
        tools.append(_tool("decide_party_rest", "Approve or reject a fully submitted party rest; approval advances game time",
            obj(decide_props, ("game_key", "run_id", "approve")), decide_call, mutation=True,
            guard=decide_guard, preview=decide_preview, domain="ruleset", effect="external", automatic=False, version=1))

    return tuple(tools)
