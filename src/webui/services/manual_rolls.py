"""GM initiated, independent dice requests."""
from __future__ import annotations
from dataclasses import dataclass
import inspect
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4
from src.engine.dice_rng import parse_dice_expression, roll
from src.engine.modules import checks, progression_state, session_stats
from src.engine.visibility_rules import manual_roll_visible_to
from src.localization.messages import t

# 防止自由掷骰无限堆积导致存档膨胀；超限时仅裁剪最旧的已完成 quick 自由掷骰。
MAX_QUICK_ROLLS_PER_INSTANCE = 200

@dataclass(frozen=True)
class ManualRollDependencies:
    parse_game_key: Callable[[str], tuple[str, ...]]
    get_instance: Callable[[tuple[str, ...]], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule: Callable[[Any], Any | None] | None = None
    # G4：技能/属性检定由规则集结算。通用服务只转发，不认 D&D 的属性表。
    # None 表示当前组合没有运行时，技能检定不可用（fail closed）。
    resolve_check: Callable[[Any, str, dict], Any] | None = None


def _validated_check(value: object) -> dict[str, Any]:
    """One table-facing check: an ability or a skill, plus the DC the GM set."""

    if not isinstance(value, dict) or set(value) - {"ability", "skill", "dc"}:
        raise ValueError("check must be an object with ability, skill and dc")
    ability = str(value.get("ability") or "").strip()
    skill = str(value.get("skill") or "").strip()
    if not ability and not skill:
        raise ValueError("check needs an ability or a skill")
    if ability and skill:
        raise ValueError("check cannot specify both an ability and a skill")
    dc = value.get("dc")
    if isinstance(dc, bool) or not isinstance(dc, int) or not 1 <= dc <= 100:
        raise ValueError("check dc must be an integer from 1 to 100")
    return {"ability": ability, "skill": skill, "dc": int(dc)}

def _now() -> str: return datetime.now(timezone.utc).isoformat()

class ManualRollService:
    def __init__(self, deps: ManualRollDependencies): self.d = deps
    def _inst(self, key): return self.d.get_instance(self.d.parse_game_key(key))
    def _rule(self, inst): return self.d.load_rule(inst) if self.d.load_rule else None
    @staticmethod
    def _visible(req, uid, gm_uid=""):
        return manual_roll_visible_to(req, viewer_uid=uid, viewer_is_gm=bool(uid) and uid == gm_uid)
    def list(self, key, uid):
        inst=self._inst(key)
        if not inst: return None
        return [r for r in checks.manual_roll_requests(inst) if self._visible(r, uid, inst.gm_uid)]

    @staticmethod
    def _purpose(value: object) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"free", "check", "contest"} else "free"

    @staticmethod
    def _include_in_ai_context(purpose: str, value: object) -> bool:
        # AI 上下文契约：检定/对抗结果服务端强制收录；自由投掷默认不收录，
        # 仅当客户端发来 JSON 真布尔 true 时才收录（字符串 "true"/数字 1 等
        # 一律视为 False，存严格布尔）。
        # 旧存档缺失字段时由读取方按同一规则解释（context_builder.format_manual_roll_context）。
        if purpose in {"check", "contest"}: return True
        return value is True

    @staticmethod
    def _comparison(value: object, rule: Any | None) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"at_least", "at_most"}:
            return raw
        # CoC percentile checks succeed below the target; d20-style checks
        # succeed at or above it. This remains a generic dice contract.
        return "at_most" if str(getattr(rule, "dice_system", "d20")).lower() == "d100" else "at_least"
    async def create(self,key,uid,body):
        inst=self._inst(key)
        if not inst: return {"ok":False,"error":"游戏不存在", "error_code": "game_not_found"}
        if uid != inst.gm_uid: return {"ok":False,"error":"GM only", "error_code": "gm_only"}
        if str(body.get("run_id") or "") != inst.run_id: return {"ok":False,"error":t("webui.services.manual_rolls.run_changed"), "error_code": "manual_rolls_run_changed"}
        try: formula = parse_dice_expression(body.get("formula","d20")).formula
        except ValueError as e: return {"ok":False,"error":str(e)}
        targets=[str(x).strip() for x in (body.get("target_uids") or []) if str(x).strip() in inst.players]
        if not targets: return {"ok":False,"error":t("webui.services.manual_rolls.targets_required"), "error_code": "manual_rolls_targets_required"}
        purpose = self._purpose(body.get("purpose"))
        check = body.get("check")
        try:
            check = _validated_check(check) if check is not None else None
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if check is not None and purpose != "check":
            return {"ok": False, "error": "check block requires purpose=check"}
        if check is not None and self.d.resolve_check is None:
            return {"ok": False, "error": "this ruleset does not support ability checks"}
        rule = self._rule(inst)
        comparison = self._comparison(body.get("comparison"), rule)
        if check is not None and comparison == "at_most":
            return {"ok": False, "error": "D&D ability checks use at_least comparison"}
        target = None
        if purpose == "check":
            # check 块自带 DC；旧的裸公式检定仍从 body.target 取目标值。
            raw_target = check["dc"] if check is not None else body.get("target")
            try:
                target = int(raw_target)
            except (TypeError, ValueError):
                return {"ok": False, "error": t("webui.services.manual_rolls.target_required"), "error_code": "manual_rolls_target_required"}
            if not 1 <= target <= 10000:
                return {"ok": False, "error": t("webui.services.manual_rolls.target_out_of_range"), "error_code": "manual_rolls_target_out_of_range"}
        op=str(body.get("operation_id") or "").strip()
        if not op or len(op)>128: return {"ok":False,"error":t("webui.services.manual_rolls.operation_id_invalid"), "error_code": "manual_rolls_operation_id_invalid"}
        for old in checks.manual_roll_requests(inst):
            if old.get("operation_id")==op:
                return {"ok":True,"request":old,"idempotent":True}
        session_stats.require_writable(inst)
        checks.require_writable(inst)
        req={"check":check,"id":f"mr_{uuid4().hex}","operation_id":op,"run_id":inst.run_id,"round_number":progression_state.round_value(inst),"created_by":uid,"created_at":_now(),"label":str(body.get("label") or "")[:200],"formula":formula,"purpose":purpose,"target":target,"comparison":comparison,"include_in_ai_context":self._include_in_ai_context(purpose,body.get("include_in_ai_context")),"visibility":"private" if body.get("visibility")=="private" else "party","target_uids":targets,"target_names":{u:inst.players[u].get("character_name") or u for u in targets},"status":"pending","results":{}}
        checks.add_manual_roll_request(inst, req); session_stats.touch(inst); await self.d.save_instance(inst)
        return {"ok":True,"request":req}
    async def resolve(self,key,uid,rid,body):
        inst=self._inst(key)
        if not inst: return {"ok":False,"error":"游戏不存在", "error_code": "game_not_found"}
        req=next((r for r in checks.manual_roll_requests(inst) if r.get("id")==rid),None)
        if not req or req.get("run_id")!=body.get("run_id"): return {"ok":False,"error":t("webui.services.manual_rolls.request_missing"), "error_code": "manual_rolls_request_missing"}
        target=str(body.get("target_uid") or uid)
        if target not in req.get("target_uids",[]) or (uid!=target and uid!=inst.gm_uid): return {"ok":False,"error":t("webui.services.manual_rolls.not_allowed_to_roll"), "error_code": "manual_rolls_not_allowed_to_roll"}
        if req.get("status") == "cancelled": return {"ok":False,"error":t("webui.services.manual_rolls.request_cancelled"), "error_code": "manual_rolls_request_cancelled"}
        if target in req["results"]:
            # An earlier save may have failed after the result was recorded.
            await self.d.save_instance(inst)
            return {"ok":True,"result":req["results"][target],"idempotent":True}
        session_stats.require_writable(inst)
        checks.require_writable(inst)
        check = req.get("check")
        if isinstance(check, dict) and self.d.resolve_check is not None:
            resolved = self.d.resolve_check(inst, target, check)
            resolved = await resolved if inspect.isawaitable(resolved) else resolved
            if not isinstance(resolved, dict) or "total" not in resolved:
                return {"ok": False, "error": "ruleset returned an invalid check result"}
            value = {**resolved, "rolled_by": uid, "rolled_at": _now()}
            value["target"] = int(check["dc"])
            value["comparison"] = str(req.get("comparison") or "at_least")
            value["verdict"] = "success" if value.get("success") else "failure"
            value.setdefault("formula", "d20")
        else:
            result=roll(req["formula"]); value={"formula":result.formula,"rolls":result.rolls,"modifier":result.modifier,"total":result.total,"natural":result.natural,"faces":list(result.faces or []),"rolled_by":uid,"rolled_at":_now()}
        if check is None and req.get("purpose") == "check" and req.get("target") is not None:
            value["target"] = int(req["target"])
            value["comparison"] = str(req.get("comparison") or "at_least")
            value["verdict"] = "success" if (
                value["total"] >= value["target"]
                if value["comparison"] == "at_least" else value["total"] <= value["target"]
            ) else "failure"
        req["results"][target]=value
        if all(u in req["results"] for u in req["target_uids"]):
            if req.get("purpose") == "contest":
                totals = {u: int(item.get("total", 0)) for u, item in req["results"].items()}
                # 百分制（at_most）规则低者为佳；d20 式（at_least）高者为佳。
                pick = min if str(req.get("comparison") or "at_least") == "at_most" else max
                best = pick(totals.values()) if totals else 0
                winners = {u for u, total in totals.items() if total == best}
                for target_uid, item in req["results"].items():
                    item["verdict"] = "winner" if target_uid in winners else "loss"
            req["status"]="resolved"
        session_stats.touch(inst); await self.d.save_instance(inst); return {"ok":True,"result":value,"request":req}
    async def cancel(self,key,uid,rid,body):
        inst=self._inst(key)
        if not inst or uid!=getattr(inst,"gm_uid",None): return {"ok":False,"error":"GM only", "error_code": "gm_only"}
        req=next((r for r in checks.manual_roll_requests(inst) if r.get("id")==rid),None)
        if not req or req.get("run_id")!=body.get("run_id"): return {"ok":False,"error":t("webui.services.manual_rolls.request_missing"), "error_code": "manual_rolls_request_missing"}
        # A fully rolled request is final: cancelling it would drop its results from the AI context.
        if req.get("status") == "resolved": return {"ok":False,"error":t("webui.services.manual_rolls.request_already_resolved"),"request":req}
        if req.get("status") == "cancelled": return {"ok":True,"request":req,"idempotent":True}
        session_stats.require_writable(inst)
        checks.require_writable(inst)
        req["status"]="cancelled"; req["cancel_reason"]=str(body.get("reason") or "")[:200]; session_stats.touch(inst); await self.d.save_instance(inst); return {"ok":True,"request":req}

    @staticmethod
    def _prune_old_quick_rolls(inst: Any) -> None:
        items = checks.manual_roll_requests(inst)
        quick_indices = [
            idx for idx, item in enumerate(items)
            if isinstance(item, dict)
            and item.get("source") == "quick"
            and item.get("purpose") == "free"
            and item.get("status") == "resolved"
        ]
        overflow = len(quick_indices) - MAX_QUICK_ROLLS_PER_INSTANCE
        if overflow <= 0:
            return
        drop = set(quick_indices[:overflow])
        checks.replace_manual_roll_requests(inst, [item for idx, item in enumerate(items) if idx not in drop])

    async def quick_roll(self, key: str, uid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Any table member (player or GM) performs an immediate one-step free roll."""
        inst = self._inst(key)
        if not inst:
            return {"ok": False, "error": "游戏不存在", "error_code": "game_not_found"}
        actor_uid = str(uid or "").strip()
        is_member = bool(actor_uid) and (actor_uid == getattr(inst, "gm_uid", None) or actor_uid in inst.players)
        if not is_member:
            return {"ok": False, "error": t("webui.services.manual_rolls.not_allowed_to_roll"), "error_code": "manual_rolls_not_allowed_to_roll"}
        if str(body.get("run_id") or "") != inst.run_id:
            return {"ok": False, "error": t("webui.services.manual_rolls.run_changed"), "error_code": "manual_rolls_run_changed"}
        try:
            formula = parse_dice_expression(body.get("formula", "d20")).formula
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        op = str(body.get("operation_id") or "").strip()
        if not op or len(op) > 128:
            return {"ok": False, "error": t("webui.services.manual_rolls.operation_id_invalid"), "error_code": "manual_rolls_operation_id_invalid"}
        for old in checks.manual_roll_requests(inst):
            if old.get("operation_id") == op:
                existing_result = (old.get("results") or {}).get(actor_uid)
                return {"ok": True, "request": old, "result": existing_result, "idempotent": True}
        session_stats.require_writable(inst)
        checks.require_writable(inst)
        result = roll(formula)
        now = _now()
        player = inst.players.get(actor_uid) if isinstance(inst.players, dict) else None
        actor_name = (
            str(player.get("character_name") or "").strip()
            if isinstance(player, dict) else ""
        ) or ("GM" if actor_uid == getattr(inst, "gm_uid", None) else actor_uid)
        value = {
            "formula": result.formula,
            "rolls": result.rolls,
            "modifier": result.modifier,
            "total": result.total,
            "natural": result.natural,
            "faces": list(result.faces or []),
            "rolled_by": actor_uid,
            "rolled_at": now,
        }
        rule = self._rule(inst)
        req = {
            "check": None,
            "id": f"mr_{uuid4().hex}",
            "operation_id": op,
            "run_id": inst.run_id,
            "round_number": progression_state.round_value(inst),
            "created_by": actor_uid,
            "created_at": now,
            "label": str(body.get("label") or "")[:200],
            "formula": formula,
            "purpose": "free",
            "source": "quick",
            "target": None,
            "comparison": self._comparison(None, rule),
            "include_in_ai_context": self._include_in_ai_context("free", body.get("include_in_ai_context")),
            "visibility": "private" if body.get("visibility") == "private" else "party",
            "target_uids": [actor_uid],
            "target_names": {actor_uid: actor_name},
            "status": "resolved",
            "results": {actor_uid: value},
        }
        checks.add_manual_roll_request(inst, req)
        self._prune_old_quick_rolls(inst)
        session_stats.touch(inst)
        await self.d.save_instance(inst)
        return {"ok": True, "request": req, "result": value}
