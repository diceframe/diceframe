"""LLM state_update 应用器。

从 game_handler 拆出的场景/战利品状态写入逻辑；
玩家字段更新拆到 player_state_applier，NPC 状态拆到 npc_state_applier，
战利品分类规则加载拆到 item_category_resolver，疯狂状态倒计时拆到 madness_tracker。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from src.compat.callbacks import load_world_template as load_world_template_compat
from src.commands.affection_effects import settle_affection
from src.engine.game_instance import GameInstance
from src.commands.item_category_resolver import ItemCategoryResolver
from src.commands.madness_tracker import MadnessTracker
from src.commands.npc_state_applier import NpcStateApplier
from src.commands.player_state_applier import PlayerStateApplier
from src.commands.state_items import (
    classify_item,
    grant_classified_item,
)
from src.rules.loader import RuleBundleLoader
from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import (
    CharacterItemPreparationRuntime,
    CharacterStateReconciliationRuntime,
    NarrativeHealthRuntime,
)
from src.engine.economy import queue_proposal
from src.engine.modules import checks, progression_state

logger = logging.getLogger("trpg")

_MAX_LOOT_PER_ROUND = 20

# 归 live state 所有的物品字段。reconciliation 失败时只回滚这几项，让"装备没穿
# 上"与"同轮的扣血/资源结算"互不牵连；字段 ownership 的完整定义由后续 lifecycle
# merge helper 收口，这里只覆盖物品事件真正会写的部分。
_LIVE_ITEM_FIELDS = ("equipment", "inventory", "key_items", "cyberware")


def discard_unresolved_player_damage(instance: GameInstance, update: dict) -> None:
    """丢弃没有服务端失败检定依据的模型伤害标签。

    战斗伤害由战斗结算器直接写入 HP；环境、陷阱等不确定伤害则必须先有
    服务端 CheckResult。模型仍可负责叙事，但不能在玩家未失败、甚至没有
    检定时凭空把 HP 扣到 0。函数原地修改解析结果，确保实际状态、日志摘要
    与前端展示一致。
    """
    if not isinstance(update, dict):
        return
    failed_uids = {
        str(check.get("actor_uid") or "")
        for check in (checks.last_checks(instance) or [])
        if str(check.get("verdict") or "") in {"失败", "大失败", "failure", "fumble"}
    }
    players_update = update.get("players")
    if not isinstance(players_update, dict):
        return
    for uid, player_update in list(players_update.items()):
        if not isinstance(player_update, dict):
            continue
        hp_change = player_update.get("hp_change")
        if isinstance(hp_change, (int, float)) and hp_change < 0 and uid not in failed_uids:
            player_update.pop("hp_change", None)
            logger.warning(
                "模型伤害缺少失败检定依据，已丢弃: uid=%s change=%s round=%d",
                uid, hp_change, progression_state.round_value(instance),
            )
        if not player_update:
            players_update.pop(uid, None)


class StateUpdateApplier:
    """将 LLM 输出的 state_update 应用到游戏状态。"""

    def __init__(
        self,
        rules_dir: Path,
        worlds_dir: Path | None,
        load_world_template: Callable[[str, str], dict],
        ruleset_registry: Any = None,
    ):
        self._madness = MadnessTracker()
        self._players = PlayerStateApplier(self._madness)
        self._npcs = NpcStateApplier()
        self._item_cats = ItemCategoryResolver(rules_dir, worlds_dir, load_world_template)
        self._rules_dir = rules_dir
        self._load_world_template = load_world_template
        # 由 GameHandler 注入；不在这里 build_default_ruleset_registry()，避免第二份
        # registry lifecycle。未注入时 reconciliation 整体停用，行为与改造前一致。
        self._ruleset_registry = ruleset_registry

    def _load_rule(self, instance: GameInstance) -> RuleSystem | None:
        try:
            language = str(getattr(instance, "language", "") or "")
            world_data = load_world_template_compat(
                self._load_world_template,
                str(instance.world_id or ""),
                language,
            ) or {}
            return RuleSystem.load_for_world(world_data, self._rules_dir, language)
        except ValueError:
            raise
        except Exception:
            logger.warning("STAT 规则加载失败: world_id=%s", instance.world_id, exc_info=True)
            return None

    def _load_active_rule(self, instance: GameInstance) -> RuleSystem | None:
        """The run's chosen rule (as the GM prompt sees it), not the world default."""
        rule_id = str(getattr(instance, "rule_id", "") or "").strip()
        language = str(getattr(instance, "language", "") or "")
        if rule_id:
            if (self._rules_dir / f"{rule_id}.json").exists():
                return RuleSystem(RuleBundleLoader().load_rule(self._rules_dir, rule_id, language))
            rule_path = RuleSystem.path_for(self._rules_dir, rule_id, language)
            if rule_path.exists():
                return RuleSystem.load(rule_path)
        return self._load_rule(instance)

    def load_item_categories(self, instance: GameInstance) -> dict[str, list[str]]:
        """Expose the same category table the narrative pipeline classifies with."""

        return self._item_cats.load_categories(instance)

    def apply_state_update(
        self,
        instance: GameInstance,
        update: dict,
        allowed_player_uids: set | None = None,
    ) -> list[dict[str, Any]]:
        """将 LLM 输出的 state_update 应用到游戏状态。"""
        queued_proposals: list[dict[str, Any]] = []
        rule = self._load_rule(instance)
        # 先解析 runtime：没有 reconciliation 能力的规则（legacy / freeform）完全
        # 不进入快照与 reconcile 分支，保持原有行为与开销。
        runtime = self._resolve_runtime(rule)
        reconciler = runtime if isinstance(runtime, CharacterStateReconciliationRuntime) else None
        health_runtime = runtime if isinstance(runtime, NarrativeHealthRuntime) else None
        if health_runtime is not None:
            health_runtime.preflight_narrative_health(instance)
        # 旧存档的装备行可能没有 canonical metadata：generic equip 会按"非武器一律
        # body"处理并把真正穿着的护甲顶回背包，而之后的 reconciliation 救不回来。
        # 因此先让绑定的规则集把这次要装备的行 canonical 化（只有实现了该能力的
        # runtime 才会被调用；generic 层仍然不知道任何具体规则）。
        preparer = (
            reconciler
            if isinstance(reconciler, CharacterItemPreparationRuntime)
            else None
        )
        if preparer is not None:
            self._prepare_owned_items(preparer, instance, update.get("players", {}))
        snapshots = (
            self._snapshot_live_items(instance, update) if reconciler is not None else {}
        )
        # 玩家状态更新（带当前规则，供 STAT 资源结算与阈值触发器使用）
        changed_domains: dict[str, set[str]] = {
            uid: set(domains)
            for uid, domains in self._players.apply_players(
                instance,
                update.get("players", {}),
                rule=rule,
                allowed_player_uids=allowed_player_uids,
                health_runtime=health_runtime,
            ).items()
        }

        # NPC 状态更新
        self._npcs.apply_npcs(instance, update.get("npcs", {}))

        # 场景变换
        scene_change = update.get("scene_change")
        if scene_change:
            instance.set_scene(scene_change)

        # 好感提议：在 NPC 登记之后结算，同轮新登记的 NPC 也可以是对象。
        if update.get("affection"):
            active_rule = self._load_active_rule(instance)
            settle_affection(instance, update["affection"], active_rule.affection if active_rule is not None else None)

        # 战利品 - 按规则 JSON 的 item_categories 智能分类；规则未定义时用内置回退
        rule_cats = self._item_cats.load_categories(instance)

        loot_entries = update.get("loot", [])
        if len(loot_entries) > _MAX_LOOT_PER_ROUND:
            logger.warning(
                "单轮战利品（LOOT/KEY_ITEM）共 %d 条，超过上限 %d，已保留前 %d 条",
                len(loot_entries),
                _MAX_LOOT_PER_ROUND,
                _MAX_LOOT_PER_ROUND,
            )
            loot_entries = loot_entries[:_MAX_LOOT_PER_ROUND]
        for loot in loot_entries:
            uid = loot.get("player", "")
            item_name = loot.get("item", "")
            if uid not in instance.players:
                continue
            cs = instance.get_character_sheet(uid)
            # 遍历所有品类关键字匹配
            category = loot.get("category") or classify_item(item_name, rule_cats)
            try:
                quantity = max(1, min(99, int(loot.get("qty", 1) or 1)))
            except (TypeError, ValueError):
                quantity = 1
            grant_classified_item(cs, item_name, category, qty=quantity)
            # 战利品同样是 live inventory mutation，且不经过 PlayerStateApplier；
            # 汇总在这里，保证同角色同轮仍然只 reconcile 一次。
            changed_domains.setdefault(str(uid), set()).add("inventory")

        # 本轮该角色的全部 generic mutation 到此结束，再统一交给 ruleset 重新解释。
        # 放在经济提案入队之前：reconciliation 失败时不会留下半截已排队的提案。
        if reconciler is not None and changed_domains:
            self._reconcile_characters(instance, reconciler, changed_domains, snapshots)

        for proposal_index, proposal in enumerate(update.get("economy_proposals", [])):
            uid = str(proposal.get("uid") or "")
            amount = int(proposal.get("amount", 0) or 0)
            kind = str(proposal.get("kind") or "")
            # Model output is never allowed to create a chargeable purchase or
            # payment.  The only legitimate path for an AI-sourced purchase is
            # check_planner -> economy_offers -> queue_purchase_offer, which
            # normalizes the actor and de-duplicates via a stable source_ref;
            # state_update output has neither, so it stays discarded here.
            # Narrative rewards remain a separate, GM-approved path.
            if kind in {"payment", "purchase"}:
                logger.warning(
                    "忽略模型经济扣款提案: kind=%s uid=%s round=%d",
                    kind, uid, progression_state.round_value(instance),
                )
                continue
            if uid not in instance.players or kind != "reward":
                continue
            reason = str(proposal.get("reason") or "经济提案")[:240]
            source = str(proposal.get("source") or "narrative")
            rewards = [
                {
                    "name": str(item.get("name") or item.get("item") or "").strip()[:120],
                    "category": str(item.get("category") or classify_item(
                        str(item.get("name") or item.get("item") or ""), rule_cats,
                    )),
                }
                for item in (
                    proposal.get("rewards")
                    if isinstance(proposal.get("rewards"), list)
                    else [
                        {"name": item}
                        for item in (proposal.get("items") or [])
                    ]
                )
                if isinstance(item, dict)
                and str(item.get("name") or item.get("item") or "").strip()
            ][:8]
            if kind == "reward":
                # One parsed emission remains idempotent when the same response is
                # retried, while a later round may legitimately grant the same
                # amount for the same recurring cause.
                source_ref = (
                    f"round:{instance.run_id}:{progression_state.round_value(instance)}:reward:"
                    f"{proposal_index}:{uid}:{amount}:{reason.casefold()}"
                )
            queued_proposals.append(queue_proposal(
                instance,
                kind=kind,
                payer_uid="",
                recipient_uid=str(proposal.get("recipient_uid") or uid),
                amount=amount,
                rewards=rewards,
                reason=reason,
                source=source,
                source_ref=source_ref,
                approval_policy="gm",
                visibility="private",
            ))
        return queued_proposals

    def _resolve_runtime(self, rule: RuleSystem | None) -> Any:
        """Resolve the bound runtime only when it opted into reconciliation.

        Generic code must not know which rule this is; the decision is made by
        the registry binding plus a structural Protocol check.  Legacy and
        assisted rules simply do not implement the hook and stay untouched.
        """

        registry = self._ruleset_registry
        if registry is None or rule is None:
            return None
        try:
            runtime = registry.resolve(rule.template)
        except Exception:
            # 绑定缺失/版本不满足都不该让一轮叙事失败：退回改造前的无 hook 行为。
            logger.warning(
                "角色状态 reconciliation runtime 解析失败，本轮跳过: rule_id=%s",
                getattr(rule, "rule_id", ""), exc_info=True,
            )
            return None
        return runtime

    def _prepare_owned_items(
        self, preparer: Any, instance: GameInstance, players_update: Any,
    ) -> None:
        """Ask the bound ruleset to canonicalize the rows this update will equip.

        Only the display names travel: the runtime re-reads the authoritative
        character sheet itself, so the generic layer never carries rule
        knowledge.  Failures are logged and swallowed -- an unprepared old row
        must not abort the round; it simply behaves as it did before.
        """

        if not isinstance(players_update, dict):
            return
        for uid, player_update in players_update.items():
            if not isinstance(player_update, dict):
                continue
            operations = player_update.get("equipment_ops")
            if not isinstance(operations, list):
                continue
            names = {
                str(op.get("name") or "").strip()
                for op in operations
                if isinstance(op, dict) and str(op.get("op") or "") != "unequip"
            }
            names.discard("")
            if not names or str(uid) not in instance.players:
                continue
            try:
                preparer.prepare_owned_items(
                    instance, str(uid), frozenset(names),
                )
            except Exception:
                logger.warning(
                    "装备行 canonical 准备失败，按原行为继续: uid=%s", uid, exc_info=True,
                )

    @staticmethod
    def _snapshot_live_items(
        instance: GameInstance, update: dict,
    ) -> dict[str, dict[str, Any]]:
        """Snapshot the live item fields of every character this update may touch."""

        uids: set[str] = set()
        players_update = update.get("players")
        if isinstance(players_update, dict):
            uids.update(str(uid) for uid in players_update)
        loot_entries = update.get("loot")
        if isinstance(loot_entries, list):
            uids.update(
                str(entry.get("player") or "")
                for entry in loot_entries
                if isinstance(entry, dict)
            )
        snapshots: dict[str, dict[str, Any]] = {}
        for uid in uids:
            if uid not in instance.players:
                continue
            sheet = instance.get_character_sheet(uid)
            snapshots[uid] = {
                field: deepcopy(sheet[field])
                for field in _LIVE_ITEM_FIELDS
                if field in sheet
            }
        return snapshots

    def _reconcile_characters(
        self,
        instance: GameInstance,
        reconciler: Any,
        changed_domains: dict[str, set[str]],
        snapshots: dict[str, dict[str, Any]],
    ) -> None:
        """Let the bound ruleset re-derive its own projection, once per character.

        Only the domain names travel; the runtime re-reads the authoritative
        sheet itself.  A failure rolls the character's live item fields back to
        the pre-mutation snapshot, so the run never keeps "the armor is worn but
        the rules never saw it".  The rollback is deliberately limited to those
        fields: an unrelated HP or resource change settled in the same round is
        not collateral damage, and nothing else in the round is discarded.
        """

        for uid in sorted(changed_domains):
            domains = frozenset(changed_domains[uid])
            if not domains:
                continue
            try:
                reconciler.reconcile_character_state(instance, uid, domains)
            except Exception:
                logger.error(
                    "角色状态 reconciliation 失败，已回滚该角色本轮物品变化: "
                    "uid=%s domains=%s round=%d",
                    uid, sorted(domains), progression_state.round_value(instance), exc_info=True,
                )
                self._rollback_live_items(instance, uid, snapshots.get(uid))

    @staticmethod
    def _rollback_live_items(
        instance: GameInstance, uid: str, snapshot: dict[str, Any] | None,
    ) -> None:
        """Restore one character's live item fields from a pre-mutation snapshot."""

        if snapshot is None or uid not in instance.players:
            return
        sheet = instance.get_character_sheet(uid)
        for field in _LIVE_ITEM_FIELDS:
            if field in snapshot:
                sheet[field] = deepcopy(snapshot[field])
            else:
                # 快照时该字段还不存在：本轮新建的，回滚就该把它去掉。
                sheet.pop(field, None)
        instance.set_character_sheet(uid, sheet)

    def apply_madness(self, instance: GameInstance, uid: str, cs: dict, loss: int) -> None:
        """兼容旧内部调用；实际逻辑已拆到 MadnessTracker。"""
        self._madness.apply_madness(instance, uid, cs, loss)

    def tick_madness(self, instance: GameInstance) -> None:
        """兼容旧内部调用；实际逻辑已拆到 MadnessTracker。"""
        self._madness.tick_madness(instance)
