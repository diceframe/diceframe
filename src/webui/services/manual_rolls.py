"""GM initiated, independent dice requests."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4
from src.engine.dice_rng import parse_dice_formula, roll
from src.engine.modules import session_stats

@dataclass(frozen=True)
class ManualRollDependencies:
    parse_game_key: Callable[[str], tuple[str, ...]]
    get_instance: Callable[[tuple[str, ...]], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule: Callable[[Any], Any | None] | None = None

def _now() -> str: return datetime.now(timezone.utc).isoformat()

class ManualRollService:
    def __init__(self, deps: ManualRollDependencies): self.d = deps
    def _inst(self, key): return self.d.get_instance(self.d.parse_game_key(key))
    def _rule(self, inst): return self.d.load_rule(inst) if self.d.load_rule else None
    @staticmethod
    def _visible(req, uid):
        return req.get("visibility") == "party" or uid == req.get("created_by") or uid in req.get("target_uids", [])
    def list(self, key, uid):
        inst=self._inst(key)
        if not inst: return None
        return [r for r in inst.manual_roll_requests if self._visible(r, uid)]

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
        if not inst: return {"ok":False,"error":"游戏不存在"}
        if uid != inst.gm_uid: return {"ok":False,"error":"GM only"}
        if str(body.get("run_id") or "") != inst.run_id: return {"ok":False,"error":"对局已更新，请刷新后重试"}
        try: formula,_,_,_=parse_dice_formula(body.get("formula","d20"))
        except ValueError as e: return {"ok":False,"error":str(e)}
        targets=[str(x).strip() for x in (body.get("target_uids") or []) if str(x).strip() in inst.players]
        if not targets: return {"ok":False,"error":"至少选择一名玩家"}
        purpose = self._purpose(body.get("purpose"))
        rule = self._rule(inst)
        comparison = self._comparison(body.get("comparison"), rule)
        target = None
        if purpose == "check":
            try:
                target = int(body.get("target"))
            except (TypeError, ValueError):
                return {"ok": False, "error": "检定用途必须填写目标值"}
            if not 1 <= target <= 10000:
                return {"ok": False, "error": "目标值必须在 1 到 10000 之间"}
        op=str(body.get("operation_id") or "").strip()
        if not op or len(op)>128: return {"ok":False,"error":"operation_id 无效"}
        for old in inst.manual_roll_requests:
            if old.get("operation_id")==op:
                return {"ok":True,"request":old,"idempotent":True}
        session_stats.require_writable(inst)
        req={"id":f"mr_{uuid4().hex}","operation_id":op,"run_id":inst.run_id,"round_number":inst.round_number,"created_by":uid,"created_at":_now(),"label":str(body.get("label") or "")[:200],"formula":formula,"purpose":purpose,"target":target,"comparison":comparison,"include_in_ai_context":self._include_in_ai_context(purpose,body.get("include_in_ai_context")),"visibility":"private" if body.get("visibility")=="private" else "party","target_uids":targets,"target_names":{u:inst.players[u].get("character_name") or u for u in targets},"status":"pending","results":{}}
        inst.manual_roll_requests.append(req); session_stats.touch(inst); await self.d.save_instance(inst)
        return {"ok":True,"request":req}
    async def resolve(self,key,uid,rid,body):
        inst=self._inst(key)
        if not inst: return {"ok":False,"error":"游戏不存在"}
        req=next((r for r in inst.manual_roll_requests if r.get("id")==rid),None)
        if not req or req.get("run_id")!=body.get("run_id"): return {"ok":False,"error":"请求不存在或已过期"}
        target=str(body.get("target_uid") or uid)
        if target not in req.get("target_uids",[]) or (uid!=target and uid!=inst.gm_uid): return {"ok":False,"error":"无权投掷"}
        if target in req["results"]: return {"ok":True,"result":req["results"][target],"idempotent":True}
        session_stats.require_writable(inst)
        result=roll(req["formula"]); value={"formula":result.formula,"rolls":result.rolls,"modifier":result.modifier,"total":result.total,"natural":result.natural,"rolled_by":uid,"rolled_at":_now()}
        if req.get("purpose") == "check" and req.get("target") is not None:
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
                best = max(totals.values()) if totals else 0
                winners = {u for u, total in totals.items() if total == best}
                for target_uid, item in req["results"].items():
                    item["verdict"] = "winner" if target_uid in winners else "loss"
            req["status"]="resolved"
        session_stats.touch(inst); await self.d.save_instance(inst); return {"ok":True,"result":value,"request":req}
    async def cancel(self,key,uid,rid,body):
        inst=self._inst(key)
        if not inst or uid!=getattr(inst,"gm_uid",None): return {"ok":False,"error":"GM only"}
        req=next((r for r in inst.manual_roll_requests if r.get("id")==rid),None)
        if not req or req.get("run_id")!=body.get("run_id"): return {"ok":False,"error":"请求不存在或已过期"}
        session_stats.require_writable(inst)
        req["status"]="cancelled"; req["cancel_reason"]=str(body.get("reason") or "")[:200]; session_stats.touch(inst); await self.d.save_instance(inst); return {"ok":True,"request":req}
