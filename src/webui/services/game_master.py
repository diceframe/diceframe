"""GM-only rollback, recap, directive, resource, and private-message operations."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from src.commands.resource_triggers import check_resource_triggers
from src.engine.character_utils import (
    apply_resource_delta,
    get_resource,
    revive_character,
    wake_character,
)
from src.engine.currency import (
    CurrencySystemError,
    format_currency_amount,
    legacy_currency_spec,
    parse_currency_amount,
)
from src.engine.currency.models import CurrencySpec, CurrencyUnit
from src.engine.game_instance import GameState
from src.engine.health import record_health_event
from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import CharacterRevivalRuntime

GameKey = tuple[str, ...]
RecapGenerator = Callable[[Any], Awaitable[dict[str, Any]]]
EconomyOutboxDrainer = Callable[[Any], Awaitable[bool]]

_GM_RESOURCE_ALIASES = {
    "生命值": "hp",
    "生命": "hp",
    "血量": "hp",
    "hp": "hp",
    "金币": "currency",
    "金钱": "currency",
    "货币": "currency",
    "gold": "currency",
    "幸运值": "luck",
    "幸运": "luck",
    "luck": "luck",
    "理智值": "sanity",
    "理智": "sanity",
    "san": "sanity",
    "法力值": "mana",
    "法力": "mana",
    "mana": "mana",
    "经验值": "xp",
    "经验": "xp",
    "xp": "xp",
    "内力": "qi",
    "人性": "humanity",
    "热度": "heat",
}

# 泛化的“钱”称呼：始终解析为规则的展示单位，不做任何数值换算。
_CURRENCY_GENERIC_ALIASES = ("金币", "金钱", "货币", "gold")


@dataclass(frozen=True)
class GameMasterDependencies:
    parse_game_key: Callable[[str], GameKey]
    get_instance: Callable[[GameKey], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule: Callable[[Any], RuleSystem | None]
    load_runtime: Callable[[Any], Any | None] | None = None
    generate_recap: RecapGenerator | None = None
    drain_economy_outbox: EconomyOutboxDrainer | None = None


def _resource_aliases_for_rule(rule: RuleSystem | None) -> dict[str, str]:
    aliases = dict(_GM_RESOURCE_ALIASES)
    if rule:
        for spec in rule.resource_schema:
            key = str(spec.get("key") or "").strip()
            if not key:
                continue
            for alias in spec.get("aliases") or []:
                normalized = str(alias).strip().lower()
                if normalized:
                    aliases[normalized] = key
            label = str(spec.get("label") or "").strip()
            if label:
                aliases[label.lower()] = key
            aliases[key.lower()] = key
    return aliases


def _resource_display_label(
    resource_key: str, matched_alias: str, rule: RuleSystem | None,
) -> str:
    if resource_key == "currency" and rule:
        return rule.currency_spec.display.name
    if rule:
        for spec in rule.resource_schema:
            if str(spec.get("key") or "") == resource_key:
                label = str(spec.get("label") or "").strip()
                if label:
                    return label
    if resource_key == "currency":
        return "金币"
    return matched_alias


def _currency_aliases_for_rule(
    rule: RuleSystem | None,
) -> tuple[CurrencySpec, dict[str, CurrencyUnit]]:
    """GM 指令可用的货币单位别名 → CurrencyUnit（name/symbol/id + 泛化称呼）。"""

    spec = rule.currency_spec if rule else legacy_currency_spec("")
    aliases: dict[str, CurrencyUnit] = {}
    for unit in spec.units:
        for label in (unit.name, unit.symbol, unit.id):
            key = str(label or "").strip().lower()
            if key:
                aliases.setdefault(key, unit)
    for generic in _CURRENCY_GENERIC_ALIASES:
        aliases.setdefault(generic, spec.display)
    return spec, aliases


def _parse_currency_delta(
    match: re.Match[str],
    *,
    sign: int,
    spec: CurrencySpec,
    unit: CurrencyUnit,
) -> int:
    """把 GM 指令里的金额经 CurrencyCodec 换算为 canonical base-unit 整数。"""

    raw_delta = match.groupdict().get("delta")
    if raw_delta:
        delta_sign = -1 if raw_delta.startswith("-") else 1
        magnitude = raw_delta[1:]
    else:
        delta_sign = sign
        magnitude = match.group("amount")
    canonical = parse_currency_amount(magnitude, unit.id, spec)
    return delta_sign * canonical


def _parse_gm_resource_change(
    instance: Any, command: str, rule: RuleSystem | None,
) -> dict[str, Any] | None:
    compact = re.sub(r"\s+", "", command).lower()
    resource_aliases = _resource_aliases_for_rule(rule)
    aliases = sorted(resource_aliases, key=len, reverse=True)
    resource_group = "|".join(re.escape(alias) for alias in aliases)

    # 货币走专属分支：金额允许小数（0.25 / 12.50），单位别名来自规则货币
    # 结构（美元/美分/$/灵石……），换算统一经 CurrencyCodec，本层不做 ×100。
    # 同时兼容两种语序：「增加0.25美元」（金额在前）与「加金币5」（单位在前）。
    currency_spec, currency_aliases = _currency_aliases_for_rule(rule)
    currency_group = "|".join(
        re.escape(alias)
        for alias in sorted(currency_aliases, key=len, reverse=True)
    )
    decimal_amount = r"\d+(?:\.\d+)?"
    currency_patterns: tuple[tuple[re.Pattern[str], int], ...] = (
        (re.compile(rf"^给(?P<target>.+?)(?:加|增加|恢复|补充|给予)(?P<amount>{decimal_amount})点?(?P<resource>{currency_group})$"), 1),
        (re.compile(rf"^给(?P<target>.+?)(?:加|增加|恢复|补充|给予)(?P<resource>{currency_group})(?P<amount>{decimal_amount})点?$"), 1),
        (re.compile(rf"^(?P<target>.+?)(?:的)?(?P<resource>{currency_group})(?P<delta>[+-]{decimal_amount})点?$"), 1),
        (re.compile(rf"^(?:扣除|减少)(?P<target>.+?)(?P<amount>{decimal_amount})点?(?P<resource>{currency_group})$"), -1),
        (re.compile(rf"^(?:扣除|减少)(?P<target>.+?)(?P<resource>{currency_group})(?P<amount>{decimal_amount})点?$"), -1),
        (re.compile(rf"^(?:give|add)(?P<amount>{decimal_amount})(?P<resource>{currency_group})(?:to)?(?P<target>.+)$"), 1),
        (re.compile(rf"^(?:remove|subtract)(?P<amount>{decimal_amount})(?P<resource>{currency_group})(?:from)?(?P<target>.+)$"), -1),
    )

    currency_match: re.Match[str] | None = None
    currency_sign = 1
    for pattern, candidate_sign in currency_patterns:
        currency_match = pattern.fullmatch(compact)
        if currency_match:
            currency_sign = candidate_sign
            break

    match: re.Match[str] | None = None
    sign = 1
    delta = 0
    unit: CurrencyUnit | None = None
    if currency_match and currency_match.group("resource") in currency_aliases:
        resource_alias = currency_match.group("resource")
        unit = currency_aliases[resource_alias]
        resource_key = "currency"
        try:
            delta = _parse_currency_delta(
                currency_match, sign=currency_sign, spec=currency_spec, unit=unit,
            )
        except CurrencySystemError as exc:
            return {"error": str(exc)}
    else:
        currency_match = None
        generic_patterns: tuple[tuple[re.Pattern[str], int], ...] = (
            (re.compile(rf"^给(?P<target>.+?)(?:加|增加|恢复|补充|给予)(?P<resource>{resource_group})(?P<amount>\d+)点?$"), 1),
            (re.compile(rf"^(?P<target>.+?)(?:的)?(?P<resource>{resource_group})(?P<delta>[+-]\d+)点?$"), 1),
            (re.compile(rf"^(?:扣除|减少)(?P<target>.+?)(?P<resource>{resource_group})(?P<amount>\d+)点?$"), -1),
            (re.compile(rf"^(?:扣除|减少)(?P<target>.+?)(?P<amount>\d+)点?(?P<resource>{resource_group})$"), -1),
            (re.compile(rf"^(?:give|add)(?P<amount>\d+)(?P<resource>{resource_group})(?:to)?(?P<target>.+)$"), 1),
            (re.compile(rf"^(?:remove|subtract)(?P<amount>\d+)(?P<resource>{resource_group})(?:from)?(?P<target>.+)$"), -1),
        )
        for pattern, candidate_sign in generic_patterns:
            match = pattern.fullmatch(compact)
            if match:
                sign = candidate_sign
                break
        if not match:
            return None
        resource_alias = match.group("resource")
        resource_key = resource_aliases[resource_alias]
        raw_delta = match.groupdict().get("delta")
        delta = int(raw_delta) if raw_delta else sign * int(match.group("amount"))
    if delta == 0:
        return {"error": "修正值不能为 0"}

    target_source = currency_match or match
    user_id, error = _resolve_gm_command_target(
        instance, target_source.group("target")
    )
    if error:
        return {"error": error}
    character_sheet = instance.get_character_sheet(user_id)
    allowed_resources = {
        "currency",
        *(
            str(spec.get("key") or "")
            for spec in (rule.resource_schema if rule else [])
        ),
    }
    if (
        resource_key not in allowed_resources
        and get_resource(character_sheet, resource_key) is None
    ):
        return {"error": f"当前规则没有资源：{resource_alias}"}
    before_resource = (
        None
        if resource_key == "currency"
        else get_resource(character_sheet, resource_key)
    )
    before = (
        int(character_sheet.get("gold", 0) or 0)
        if resource_key == "currency"
        else int(
            (before_resource or {}).get(
                "current", character_sheet.get(resource_key, 0)
            )
            or 0
        )
    )
    after = apply_resource_delta(character_sheet, resource_key, delta, rule)
    actual_delta = after - before
    revived = False
    if resource_key == "hp" and after > 0 and character_sheet.get("deceased"):
        character_sheet["deceased"] = False
        character_sheet.pop("death_round", None)
        revived = True
    if resource_key == "hp" and after > 0:
        wake_character(character_sheet)
    result = {
        "uid": user_id,
        "character_name": (
            instance.players.get(user_id, {}).get("character_name") or user_id
        ),
        "resource": resource_key,
        "resource_label": _resource_display_label(
            resource_key, resource_alias, rule
        ),
        "requested_delta": delta,
        "actual_delta": actual_delta,
        "before": before,
        "after": after,
        "revived": revived,
    }
    if currency_match and unit is not None:
        # 小数货币只透出格式化后的显示金额，避免把 canonical base-unit
        # 整数当成展示值（25 美分显示为 $0.25）。
        result["resource_label"] = unit.name
        result["display_before"] = format_currency_amount(before, currency_spec)
        result["display_after"] = format_currency_amount(after, currency_spec)
        result["display_delta"] = format_currency_amount(actual_delta, currency_spec)
    return result


def _resolve_gm_command_target(
    instance: Any,
    raw_target: str,
    *,
    prefer_deceased: bool = False,
) -> tuple[str, str] | tuple[None, str]:
    target = re.sub(r"\s+", "", raw_target).strip("的")
    for pattern in (
        r"^(?:名为|名字是|名字叫|叫做|叫|角色名为)(.+?)(?:的)?(?:人|玩家|角色)?$",
        r"^(?:玩家|角色)(?:名为|叫做|叫)(.+)$",
    ):
        wrapped = re.fullmatch(pattern, target, flags=re.IGNORECASE)
        if wrapped:
            target = wrapped.group(1).strip("的")
            break
    for user_id, player in instance.players.items():
        name = str(player.get("character_name") or "")
        if target in {str(user_id), re.sub(r"\s+", "", name)}:
            return str(user_id), ""
    generic_targets = {
        "用户", "玩家", "冒险者", "角色", "当前玩家", "当前角色",
        "user", "player", "adventurer", "currentplayer", "currentcharacter",
    }
    if target in generic_targets:
        if len(instance.players) == 1:
            user_id = next(iter(instance.players))
            return user_id, ""
        if prefer_deceased:
            deceased = [
                str(user_id)
                for user_id in instance.players
                if bool(instance.get_character_sheet(user_id).get("deceased"))
            ]
            if len(deceased) == 1:
                return deceased[0], ""
        names = [
            str(player.get("character_name") or user_id)
            for user_id, player in instance.players.items()
        ]
        return None, (
            "当前有多名玩家，请写明角色名（可用："
            + "、".join(names)
            + "），例如“复活" + names[0] + "”"
        )
    names = [
        str(player.get("character_name") or user_id)
        for user_id, player in instance.players.items()
    ]
    suffix = f"；可用角色：{'、'.join(names)}" if names else ""
    return None, f"找不到角色：{raw_target}{suffix}"


def _normalize_revive_method(raw: str) -> str:
    method = re.sub(r"\s+", "", raw or "").lower()
    if method in {"法术", "魔法", "治疗术", "spell", "magic", "heal"}:
        return "法术"
    if method in {"npc", "npc治疗", "治疗者", "医师", "healer"}:
        return "NPC"
    if method in {"自然", "自然恢复", "natural", "rest"}:
        return "自然"
    return (raw or "法术").strip() or "法术"


def _parse_gm_revive_command(
    instance: Any, command: str,
) -> dict[str, Any] | None:
    compact = re.sub(r"\s+", "", command)
    match = re.fullmatch(
        r"(?:复活|救活)(?P<target>.+?)(?:[:：(\-（](?P<method>[^:：()（）]+)[)）]?)?",
        compact,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.fullmatch(
            r"revive(?P<target>.+?)(?:[:(\-](?P<method>[^:()]+)\)?)?",
            compact,
            flags=re.IGNORECASE,
        )
    if not match:
        return None
    user_id, error = _resolve_gm_command_target(
        instance,
        match.group("target"),
        prefer_deceased=True,
    )
    if error:
        return {"error": error}
    method = _normalize_revive_method(
        match.groupdict().get("method") or "法术"
    )
    return {"uid": user_id, "method": method}


class GameMasterService:
    """GM transactions with explicit instance, rule, recap, and save dependencies."""

    def __init__(self, dependencies: GameMasterDependencies) -> None:
        self._dependencies = dependencies

    def _instance(self, game_key: str) -> Any | None:
        return self._dependencies.get_instance(
            self._dependencies.parse_game_key(game_key)
        )

    def _sync_revived_character(self, instance: Any, user_id: str) -> None:
        if self._dependencies.load_runtime is None:
            return
        runtime = self._dependencies.load_runtime(instance)
        if isinstance(runtime, CharacterRevivalRuntime):
            runtime.on_character_revived(instance, str(user_id))

    async def rollback_round(self, game_key: str) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        async with instance.authoritative_write() as write_entered:
            if not write_entered:
                return self._rewrite_conflict()
            if self._instance(game_key) is not instance:
                return self._stale_run()
            return await self._rollback_round_authority(instance)

    async def _rollback_round_authority(self, instance: Any) -> dict[str, Any]:
        round_number = await instance.rollback_last_round()
        if round_number is None:
            return {"ok": False, "error": "没有可撤回的上一轮"}
        record_health_event(
            instance,
            component="gm_control",
            code="GM_ROLLBACK",
            severity="info",
            title="GM 回退",
            message=f"已撤回到第 {round_number} 轮开始前的玩家状态。",
            impact="上一轮叙事日志已移除，玩家公开状态已恢复到快照。",
            fallback="rollback_snapshot",
            repair_hint="如果仍不满意，可继续用 GM 指令修正下一次判定。",
        )
        await self._dependencies.save_instance(instance)
        external_effects_committed = True
        if self._dependencies.drain_economy_outbox is not None:
            external_effects_committed = await self._dependencies.drain_economy_outbox(
                instance,
            )
        return {
            "ok": True,
            "message": f"已撤回到第 {round_number} 轮开始前的玩家状态",
            "external_effects_committed": external_effects_committed,
        }

    async def generate_story_recap(self, game_key: str) -> dict[str, Any]:
        """Generate and persist a public recap without a synthetic round."""

        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        if self._dependencies.generate_recap is None:
            return {"ok": False, "error": "系统未就绪"}
        result = await self._dependencies.generate_recap(instance)
        if result.get("ok"):
            await self._dependencies.save_instance(instance)
        return result

    async def command(
        self, game_key: str, command: str, mode: str = "note",
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        async with instance.authoritative_write() as write_entered:
            if not write_entered:
                return self._rewrite_conflict()
            if self._instance(game_key) is not instance:
                return self._stale_run()
            return await self._command_authority(instance, game_key, command, mode)

    async def _command_authority(
        self, instance: Any, game_key: str, command: str, mode: str,
    ) -> dict[str, Any]:
        command = (command or "").strip()
        if mode == "rollback":
            return await self._rollback_round_authority(instance)
        if not command:
            return {"ok": False, "error": "请输入 GM 指令"}

        rule = self._dependencies.load_rule(instance)
        resource_change = _parse_gm_resource_change(instance, command, rule)
        if resource_change:
            if resource_change.get("error"):
                return {"ok": False, "error": resource_change["error"]}
            if resource_change.get("revived"):
                self._sync_revived_character(instance, str(resource_change["uid"]))
            record_health_event(
                instance,
                component="gm_control",
                code="GM_RESOURCE_CHANGE",
                severity="info",
                title="GM 数值修正",
                message=(
                    f"{resource_change['character_name']} · "
                    f"{resource_change['resource_label']} "
                    f"{resource_change['before']} → {resource_change['after']}"
                    + ("，已复活" if resource_change["revived"] else "")
                ),
                impact="数值已由服务端直接写入角色卡，不依赖模型输出。",
                fallback="direct_state_update",
                repair_hint="如修正有误，可提交相反数值恢复。",
            )
            await self._dependencies.save_instance(instance)
            triggered = check_resource_triggers(
                instance, str(resource_change.get("uid") or ""), rule
            )
            return {
                "ok": True,
                "message": (
                    f"{resource_change['character_name']}的"
                    f"{resource_change['resource_label']}："
                    f"{resource_change['before']} → {resource_change['after']}"
                    + ("，已复活" if resource_change["revived"] else "")
                ),
                "kind": "resource_update",
                "resource_update": resource_change,
                "triggered": triggered,
            }

        revive = _parse_gm_revive_command(instance, command)
        if revive:
            if revive.get("error"):
                return {"ok": False, "error": revive["error"]}
            character_sheet = instance.get_character_sheet(revive["uid"])
            if not revive_character(character_sheet, revive["method"]):
                return {"ok": False, "error": "该角色当前并未死亡"}
            instance.set_character_sheet(revive["uid"], character_sheet)
            self._sync_revived_character(instance, str(revive["uid"]))
            await self._dependencies.save_instance(instance)
            name = (
                instance.players[revive["uid"]].get("character_name")
                or revive["uid"]
            )
            record_health_event(
                instance,
                component="gm_control",
                code="GM_REVIVE",
                severity="info",
                title="GM 复活",
                message=f"{name} 已复活（{revive['method']}）",
                impact="角色死亡状态已清除，HP 按复活方式恢复。",
                fallback="direct_state_update",
            )
            return {
                "ok": True,
                "message": f"{name} 已复活（{revive['method']}）",
                "kind": "revive",
            }

        target_round = int(instance.round_number or 0)
        if instance.state != GameState.ACTIVE_ACTION:
            target_round += 1
        instance.add_gm_directive({
            "id": uuid.uuid4().hex,
            "text": command,
            "created_at": time.time(),
            "target_round": target_round,
        })
        record_health_event(
            instance,
            component="gm_control",
            code="GM_COMMAND",
            severity="info",
            title="GM 私密叙事指令",
            message=command,
            impact="下一轮会私密注入 GM 上下文；不参与行动检定，也不出现在玩家日志中。",
            fallback="private_directive",
            repair_hint="如果指令写错，可撤回上一轮或追加新的 GM 修正指令覆盖。",
        )
        await self._dependencies.save_instance(instance)
        return {
            "ok": True,
            "kind": "directive",
            "message": "GM 私密指令已加入下一轮",
        }

    async def private_message(
        self, game_key: str, user_id: str, text: str,
    ) -> dict[str, Any]:
        instance = self._instance(game_key)
        if not instance:
            return {"ok": False, "error": "游戏不存在"}
        async with instance.authoritative_write() as write_entered:
            if not write_entered:
                return self._rewrite_conflict()
            if self._instance(game_key) is not instance:
                return self._stale_run()
            return await self._private_message_authority(instance, user_id, text)

    async def _private_message_authority(
        self, instance: Any, user_id: str, text: str,
    ) -> dict[str, Any]:
        user_id = (user_id or "").strip()
        text = (text or "").strip()
        if user_id not in instance.players:
            return {"ok": False, "error": "目标玩家不存在"}
        if not text:
            return {"ok": False, "error": "请输入悄悄话内容"}
        instance.append_private_message(user_id, {
            "round": instance.round_number,
            "text": text,
            "source": "gm",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await self._dependencies.save_instance(instance)
        return {"ok": True, "message": "悄悄话已发送"}

    @staticmethod
    def _rewrite_conflict() -> dict[str, Any]:
        return {
            "ok": False,
            "code": "REWRITE_IN_PROGRESS",
            "error": "GM 正在重写历史回合，请等待完成后重试",
        }

    @staticmethod
    def _stale_run() -> dict[str, Any]:
        return {"ok": False, "code": "STALE_RUN", "error": "对局已重开，请刷新后重试"}
