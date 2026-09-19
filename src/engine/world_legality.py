"""Server-side action legality against the authoritative world state (#284).

`#284` reported the "implicit teleport": a player says they are in the east
village, then next round declares an action that only makes sense in the west
village, and the table silently accepts that they are already there.  The same
class of problem covers walking over a bridge the world already marked as
impassable.

Legality stays deliberately small and evidence-based.  The planner may *propose*
structured world requirements (where an action happens, where a character wants
to move); only this module decides whether that proposal is consistent with
world truth:

- ``kind: act``  -- the action is performed at ``location``.  A proven mismatch
  with the actor's authoritative location blocks it: the outcome is "you have to
  move first", never a silent teleport.
- ``kind: move``  -- the actor wants to end up at ``location`` (optionally via
  ``via``).  The declared route -- the ``via`` hops plus the destination itself --
  is checked against ``location:<id>.passable = false``.  The actor's *current*
  location is deliberately not part of that check: ``passable = false`` blocks
  entering, passing through and arriving at a place, it does not trap whoever is
  already standing there.  A route with no proven obstacle is applied
  server-side as the actor's new location.

Boundaries:

- No keyword parsing.  A location mentioned only inside free text is never used
  as blocking evidence; requirements come from the structured planner proposal,
  and both sides are canonical world ids.
- No pathfinding, distance, or map topology: only the hops the planner actually
  declared are checked.
- Insufficient information never manufactures a block.  An unknown location, an
  actor whose location was never established, or an empty world all mean "no
  evidence" and are left to the GM/planner.
- Separate from overreach: overreach is about a player claiming authority over
  the world or other characters; legality is about the player's own action
  contradicting authoritative world truth.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from src.engine.world_state import (
    WorldStateError,
    apply_world_ops,
    world_facts,
    world_relations,
)

logger = logging.getLogger("trpg")

# 与 overreach / economy_actions 同一量级：单轮最多 8 条结构化世界要求。
MAX_WORLD_REQUIREMENTS = 8
MAX_ROUTE_HOPS = 8
REQUIREMENT_KINDS = ("act", "move")
# 服务端判定出的矛盾类型（渲染文案由调用方按语言本地化）。
BLOCK_CODES = ("ACTION_LOCATION_MISMATCH", "ROUTE_IMPASSABLE")
_ACTOR_LOCATION_SUFFIX = ".location"
_LOCATION_PREFIX = "location:"
_PASSABLE_SUFFIX = ".passable"


def actor_location_fact_key(uid: str) -> str:
    """Canonical fact key holding one actor's authoritative location."""

    return f"actor:{uid}{_ACTOR_LOCATION_SUFFIX}"


def passable_fact_key(location_id: str) -> str:
    """Canonical fact key marking whether a world location can be passed."""

    return f"{_LOCATION_PREFIX}{location_id}{_PASSABLE_SUFFIX}"


def evaluate_world_requirements(
    instance: Any, requirements: Sequence[Mapping[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Evaluate planner-proposed world requirements; never raise on bad input.

    Returns ``{"notes": [...], "applied": [...]}``: ``notes`` are proven
    contradictions the narration must honor, ``applied`` are server-validated
    movements that were written to world truth.
    """

    notes: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    if not isinstance(requirements, Sequence) or isinstance(requirements, (str, bytes)):
        return {"notes": notes, "applied": applied}
    if not requirements:
        return {"notes": notes, "applied": applied}
    try:
        facts = world_facts(getattr(instance, "world_state", None))
        relations = world_relations(getattr(instance, "world_state", None))
    except Exception:  # pragma: no cover - world_facts is already defensive
        logger.warning("世界真相读取失败，本轮不做合法性判定", exc_info=True)
        return {"notes": notes, "applied": applied}
    if not facts and not relations:
        # 空世界（旧游戏、未建立任何事实）没有任何可证明的矛盾。
        return {"notes": notes, "applied": applied}
    known_locations = _known_locations(facts, relations)
    players = getattr(instance, "players", None) or {}
    for raw in list(requirements)[:MAX_WORLD_REQUIREMENTS]:
        if not isinstance(raw, Mapping):
            continue
        uid = str(raw.get("player") or "")
        if uid not in players:
            continue
        kind = str(raw.get("kind") or "act")
        if kind not in REQUIREMENT_KINDS:
            continue
        location = raw.get("location")
        if not isinstance(location, str) or not location:
            continue
        if location not in known_locations:
            # 信息不足：世界没有登记这个地点，不能凭空判非法。
            continue
        current = _actor_location(facts, uid)
        route = _route(raw.get("via"), location)
        if kind == "act":
            if current and current != location:
                notes.append({
                    "player": uid,
                    "code": "ACTION_LOCATION_MISMATCH",
                    "location": location,
                    "current": current,
                })
            continue
        # 只检查声明的路线（via + 目的地）：passable=false 阻止进入/经过/抵达，
        # 不能因为行动者已经身处该地点就永久阻止其离开。
        blocked = _first_impassable(facts, route, relations)
        if blocked:
            notes.append({
                "player": uid,
                "code": "ROUTE_IMPASSABLE",
                "location": blocked,
                "destination": location,
                "current": current,
            })
            continue
        if current == location:
            applied.append({"player": uid, "location": location, "from": current})
            continue
        try:
            apply_world_ops(instance, [{
                "op": "set_fact",
                "key": actor_location_fact_key(uid),
                "value": location,
            }])
        except WorldStateError as exc:
            # 世界容器损坏/版本不受支持：合法性层保持惰性，不阻断这一轮叙事。
            logger.warning("移动写入世界真相被拒绝: %s", exc)
            continue
        applied.append({"player": uid, "location": location, "from": current})
    return {"notes": notes, "applied": applied}


def _known_locations(
    facts: Mapping[str, Mapping[str, Any]],
    relations: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """Canonical locations the world has actually established.

    A location counts as known when some ``location:<id>.*`` fact exists, when
    it appears as the value of a ``*.location`` fact (the actor locations the
    server itself writes), or when it is an endpoint of a ``connects`` /
    ``located_at`` relation (WR-08：世界结构也是地点存在的证据).  Anything
    else is unknown, and unknown means "no evidence" rather than "illegal".
    """

    known: set[str] = set()
    for key, fact in facts.items():
        if key.startswith(_LOCATION_PREFIX):
            location_id = key[len(_LOCATION_PREFIX):].split(".", 1)[0]
            if location_id:
                known.add(location_id)
        elif key.endswith(_ACTOR_LOCATION_SUFFIX):
            value = fact.get("value")
            if isinstance(value, str) and value:
                known.add(value)
    for relation in relations.values():
        if str(relation.get("kind") or "") not in ("connects", "located_at"):
            continue
        for side in ("from_ref", "to_ref"):
            endpoint = str(relation.get(side) or "")
            if endpoint.startswith(_LOCATION_PREFIX):
                location_id = endpoint[len(_LOCATION_PREFIX):]
                if location_id:
                    known.add(location_id)
    return known


def _actor_location(facts: Mapping[str, Mapping[str, Any]], uid: str) -> str:
    fact = facts.get(actor_location_fact_key(uid))
    value = fact.get("value") if isinstance(fact, Mapping) else None
    return value if isinstance(value, str) else ""


def _route(via: Any, destination: str) -> list[str]:
    hops: list[str] = []
    if isinstance(via, Sequence) and not isinstance(via, (str, bytes)):
        for item in list(via)[:MAX_ROUTE_HOPS]:
            if isinstance(item, str) and item and item not in hops:
                hops.append(item)
    if destination not in hops:
        hops.append(destination)
    return hops


def _first_impassable(
    facts: Mapping[str, Mapping[str, Any]],
    route: Sequence[str],
    relations: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """The first location on the route the world proves impassable.

    Evidence sources (both are authoritative world truth; visibility never
    softens legality — GM-only truth still blocks):

    - ``location:<id>.passable = false`` fact（v1 既有路径，原样保留）；
    - 一条 ``severed`` 的 ``connects`` 关系（WR-08）：声明路线中相邻两跳之间
      的连接被切断，则后一跳不可达。方向无关（connects 是无向证据）。
    """

    hops = [str(location_id) for location_id in route if location_id]
    for index, location_id in enumerate(hops):
        fact = facts.get(passable_fact_key(location_id))
        if isinstance(fact, Mapping) and fact.get("value") is False:
            return str(location_id)
        if index + 1 < len(hops) and relations:
            next_hop = hops[index + 1]
            if _severed_connection(relations, location_id, next_hop):
                return next_hop
    return ""


def _severed_connection(
    relations: Mapping[str, Mapping[str, Any]],
    from_location: str,
    to_location: str,
) -> bool:
    """Whether a ``connects`` relation between two locations is proven severed."""

    for relation in relations.values():
        if str(relation.get("kind") or "") != "connects":
            continue
        if str(relation.get("status") or "") != "severed":
            continue
        endpoints = {
            str(relation.get("from_ref") or ""),
            str(relation.get("to_ref") or ""),
        }
        location_pair = {
            f"{_LOCATION_PREFIX}{from_location}",
            f"{_LOCATION_PREFIX}{to_location}",
        }
        if endpoints == location_pair:
            return True
    return False


__all__ = [
    "BLOCK_CODES",
    "MAX_WORLD_REQUIREMENTS",
    "REQUIREMENT_KINDS",
    "actor_location_fact_key",
    "evaluate_world_requirements",
    "passable_fact_key",
]
