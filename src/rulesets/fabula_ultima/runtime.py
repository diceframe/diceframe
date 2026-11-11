"""Fabula Ultima 一等规则运行时。

职责：
- 建卡契约（属性骰 / 双职业 / 技能 / 初始资金）
- 冲突场景（先攻、轮次、行动、伤害、相性、危机、命刻、归零）
- 反派终结点与升格
- 版本化状态与 intent 协议

状态存放在 ``ruleset_runtime.state["fabula_ultima"]``，由本模块独占。
"""

from __future__ import annotations

import json
import random
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.adventures import progress as adventure_progress
from src.adventures.graph_v2 import ADVENTURE_GRAPH_FORMAT_V2
from src.rulesets.fabula_ultima.dice import fu_high_roll
from src.engine.modules import adventure_runtime_state, ruleset_runtime
from src.engine.game_state import GameState
from src.engine.player_control import control_mode as seat_control_mode
from src.engine.player_control import is_ai_controlled
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.fabula_ultima import conflict as conflict_mod
from src.rulesets.fabula_ultima import content_store, gm_tools, paths
from src.rulesets.fabula_ultima.content import (
    ENEMIES,
    VILLAIN_FATE,
)
from src.rulesets.fabula_ultima.i18n import language_of, render
from src.rulesets.fabula_ultima.runtime_messages import (
    ATTRIBUTE_LABELS,
    MESSAGES,
    VERDICT_LABELS,
)
from src.rulesets.fabula_ultima.skills import known_skills, skill_for
from src.rulesets.legacy_adapter import LegacyRulesetAdapter

STATE_KEY = "fabula_ultima"
STATE_SCHEMA_VERSION = 1
RULE_ID = "fabula_ultima"
_BESTIARY_CACHE: list[dict[str, Any]] | None = None
_ITEM_CACHE: dict[str, list[dict[str, Any]]] = {}
ATTR_KEYS = ("dex", "ins", "mig", "wlp")
DICE_OPTIONS = (6, 8, 10, 12)
ATTRIBUTE_TOTAL = 32
CLASS_LEVELS_TOTAL = 5
SKILL_POINT_TOTAL = 5
MAX_SKILL_VALUE = 5
MAX_SKILLS = 5
STARTING_CURRENCY = 500


#: 冲突的发起与行动者编排只归 GM；身份由 ``prepare_intent_submission`` 盖章。
GM_ONLY_INTENTS = frozenset({
    "conflict.start", "conflict.select_actor", "conflict.enemy_action", "conflict.ai_action",
}) | gm_tools.GM_ONLY_TOOL_INTENTS


def default_state() -> dict[str, Any]:
    return {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "conflict": None,
        "villains": {},
        "world": {"mysteries": [], "threats": []},
        "log": [],
    }


def fu_state(instance: Any) -> dict[str, Any]:
    state = ruleset_runtime.state(instance)
    value = state.get(STATE_KEY)
    if not isinstance(value, dict):
        value = default_state()
        state[STATE_KEY] = value
    value.setdefault("state_schema_version", STATE_SCHEMA_VERSION)
    value.setdefault("conflict", None)
    value.setdefault("villains", {})
    value.setdefault("world", {"mysteries": [], "threats": []})
    value.setdefault("log", [])
    return value


def _append_log(state: dict[str, Any], text: str) -> None:
    if not text:
        return
    state["log"].append(text)
    state["log"] = state["log"][-60:]


def _text(instance: Any, key: str, /, **params: Any) -> str:
    """按对局语言渲染一条运行时文案。"""
    return render(language_of(instance), MESSAGES, key, **params)


def _rule_language(rule: Any) -> str:
    """只拿得到规则对象时的语言：locale overlay 生效时取 ``active_locale``，否则取模板默认语言。"""
    template = getattr(rule, "template", None)
    if not isinstance(template, dict):
        return ""
    return str(template.get("active_locale") or template.get("default_locale") or "")


def _attribute_label(language: object, key: str) -> str:
    return render(language, ATTRIBUTE_LABELS, key) if key in ATTRIBUTE_LABELS else key


def _verdict_label(language: object, verdict: str) -> str:
    """判定结论的显示名；``verdict`` 本身是 canonical 值，比较与落库都用原值。"""
    return render(language, VERDICT_LABELS, verdict) if verdict in VERDICT_LABELS else verdict


def _class_names(rule: Any) -> list[str]:
    try:
        return list(rule.get_class_names())
    except Exception:
        return []


def _parse_class_levels(draft: dict[str, Any], rule: Any) -> dict[str, int]:
    raw = draft.get("class_levels")
    levels: dict[str, int] = {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            try:
                levels[str(name)] = int(value)
            except (TypeError, ValueError):
                continue
    elif isinstance(draft.get("class"), str):
        label = draft["class"]
        if "/" in label:
            for part in label.split("/"):
                bits = part.strip().rsplit(" ", 1)
                if len(bits) == 2 and bits[1].isdigit():
                    levels[bits[0].strip()] = int(bits[1])
        else:
            bits = label.strip().rsplit(" ", 1)
            if len(bits) == 2 and bits[1].isdigit():
                levels[bits[0].strip()] = int(bits[1])
    return levels


def _class_label(levels: dict[str, int]) -> str:
    return " / ".join(f"{name} {value}" for name, value in levels.items())


def _stamp_duration(conflict: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    entry["created_step"] = int(conflict.get("step") or 0)
    return entry


def _item_entries(language: object = "") -> list[dict[str, Any]]:
    stored = content_store.load("items", include_disabled=False)
    if stored:
        return stored
    cache_key = str(language or "")
    cached = _ITEM_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        item_path = paths.rule_path(RULE_ID)
        data = json.loads(item_path.read_text(encoding="utf-8")) if item_path else {}
        raw = data.get("items") if isinstance(data, dict) else None
    except (OSError, ValueError):
        raw = None
    rows: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            name = str(value.get("name") or key)
            item_type = str(value.get("type") or "common")
            row = {
                "id": name,
                "name": name,
                "category": "equipment" if item_type in {"weapon", "armor", "shield"} else "common",
            }
            row.update({k: v for k, v in value.items() if k != "name"})
            row["category"] = row.get("category") or "common"
            if item_type == "weapon":
                row["slot"] = "main_hand"
                row.setdefault("effect", render(language, MESSAGES, "item_effect_weapon", damage=int(value.get('damage') or 0)))
            elif item_type == "armor":
                row["slot"] = "armor"
                row.setdefault("effect", render(language, MESSAGES, "item_effect_armor", armor=int(value.get('armor') or 0)))
            rows.append(row)
    rows.extend([
        {"id": "potion", "name": render(language, MESSAGES, "item_potion_name"), "category": "consumable", "description": render(language, MESSAGES, "item_potion_description"), "ip_cost": 2, "effect": {"type": "heal", "amount": 20, "target": "ally"}, "action": "action"},
        {"id": "antidote", "name": render(language, MESSAGES, "item_antidote_name"), "category": "consumable", "description": render(language, MESSAGES, "item_antidote_description"), "ip_cost": 2, "effect": {"type": "cleanse", "target": "ally"}, "action": "action"},
        {"id": "bomb", "name": render(language, MESSAGES, "item_bomb_name"), "category": "consumable", "description": render(language, MESSAGES, "item_bomb_description"), "ip_cost": 1, "effect": {"type": "damage", "amount": 10, "damage_type": "physical"}, "action": "action"},
    ])
    _ITEM_CACHE[cache_key] = rows
    return rows


def _bestiary_entries() -> list[dict[str, Any]]:
    rows = content_store.load("monsters", include_disabled=False)
    if rows:
        return [
            {
                "id": str(row.get("id") or ""),
                "profile_id": str(row.get("id") or ""),
                "name": str(row.get("name") or row.get("id") or ""),
                "rank": str(row.get("rank") or "minion"),
            }
            for row in rows
            if row.get("id")
        ]
    global _BESTIARY_CACHE
    if _BESTIARY_CACHE is not None:
        return _BESTIARY_CACHE
    try:
        data = json.loads(paths.table_path("bestiary").read_text(encoding="utf-8"))
        rows = data.get("monsters") if isinstance(data, dict) else None
        entries = [row for row in rows if isinstance(row, dict) and row.get("profile_id")] if isinstance(rows, list) else []
    except (OSError, ValueError):
        entries = []
    _BESTIARY_CACHE = [
        {
            "id": str(row.get("id") or row["profile_id"]),
            "profile_id": str(row["profile_id"]),
            "name": str(row.get("name") or row["profile_id"]),
            "rank": str(row.get("rank") or "minion"),
        }
        for row in entries
    ]
    return _BESTIARY_CACHE


def _monster_template(template_id: str) -> dict[str, Any]:
    for row in content_store.load("monsters", include_disabled=False):
        if str(row.get("id") or "") == template_id:
            return dict(row)
    return ENEMIES.get(template_id, ENEMIES["goblin"])


class FabulaUltimaRuntime(LegacyRulesetAdapter):
    runtime_id = "fabula_ultima"
    runtime_version = 1
    capabilities = RulesetCapabilities(
        experience_profile="fabula_ultima",
        # 建卡 UI 仍走现有向导；专业建卡面板在后续阶段接入。
        character_builder="legacy",
        character_lifecycle="legacy",
        authoritative_intents=True,
        deterministic_combat=True,
        versioned_state=True,
        session_zero=False,
        narrative_turns=True,
        combat_scheduler="initiative",
        combat_action_effects=False,
        combat_resource_pools=False,
        adventure_formats=(ADVENTURE_GRAPH_FORMAT_V2,),
    )

    def __init__(self, adventures_dir: str | Path | None = None) -> None:
        self._adventures_dir = Path(adventures_dir) if adventures_dir else None

    # ------------------------------------------------------------------ 规则加载

    def _rule(self, instance: Any, rule: Any | None = None) -> Any:
        if rule is not None:
            return rule
        from src.rules.rule_system import RuleSystem

        rule_id = str(getattr(instance, "rule_id", "") or "")
        path = paths.rule_path(rule_id) if rule_id else None
        return RuleSystem.load(path) if path is not None else None

    # ------------------------------------------------------------------ 冒险包

    def _adventure_bundle(self, instance: Any) -> Any | None:
        """按存档绑定的 adventure_id 载入 v2 冒险包（带缓存）。"""
        binding = getattr(instance, "adventure_binding", None)
        if not isinstance(binding, dict):
            return None
        adventure_id = str(binding.get("adventure_id") or binding.get("id") or "")
        if not adventure_id:
            return None
        cache = getattr(self, "_adventure_cache", None)
        if cache is None:
            cache = self._adventure_cache = {}
        if adventure_id in cache:
            return cache[adventure_id]
        from src.adventures.bundle import AdventureBundleLoader

        default_dir = paths.adventures_dir()
        roots = [self._adventures_dir, default_dir] if self._adventures_dir else [default_dir]
        for root in roots:
            if root is None or not Path(root).is_dir():
                continue
            try:
                bundles = AdventureBundleLoader(root).list()
            except Exception:
                continue
            for bundle in bundles:
                if str(bundle.manifest.adventure_id) == adventure_id:
                    cache[adventure_id] = bundle
                    return bundle
        cache[adventure_id] = None
        return None

    def _adventure_nodes(self, instance: Any) -> dict[str, dict[str, Any]]:
        bundle = self._adventure_bundle(instance)
        if bundle is None:
            return {}
        return {str(node.get("id")): node for node in (bundle.adventure.get("nodes") or [])}

    def _adventure_view(self, instance: Any) -> dict[str, Any] | None:
        bundle = self._adventure_bundle(instance)
        if bundle is None:
            return None
        progress = adventure_runtime_state.progress(instance)
        nodes = self._adventure_nodes(instance)
        active = []
        for node_id in progress.get("active_nodes") or []:
            node = nodes.get(str(node_id))
            if not node:
                continue
            choices = []
            if node.get("choice_mode") == "exclusive":
                for transition in node.get("transitions") or []:
                    choices.append({
                        "choice_id": transition.get("choice_id"),
                        "label": transition.get("choice_label") or transition.get("choice_id"),
                        "to": transition.get("to"),
                    })
            active.append({
                "id": node.get("id"),
                "type": node.get("type"),
                "name": node.get("name"),
                "description": node.get("description"),
                "encounter_ref": node.get("encounter_ref"),
                "choices": choices,
            })
        return {
            "id": bundle.manifest.adventure_id,
            "name": bundle.adventure.get("name"),
            "active": active,
            "ending": progress.get("ending_node_id") or "",
            "suggestions": [
                {"proposal_id": row.get("proposal_id"), "node_id": row.get("node_id"), "reason": row.get("reason")}
                for row in (progress.get("completion_suggestions") or [])
                if isinstance(row, dict)
            ],
        }

    def round_context_suffix(self, instance: Any) -> str:
        """规则模板里的 ``narration_policy``（叙事纪律）：每轮拼在 user context 末尾。

        按对局语言取规则模板，内容语言 overlay 里的译文因此生效。
        """
        from src.rules.loader import RuleBundleLoader

        rule_id = str(getattr(instance, "rule_id", "") or "")
        path = paths.rule_path(rule_id) if rule_id else None
        if path is None:
            return ""
        language = language_of(instance)
        return RuleBundleLoader().load_system(path, language).get_narration_policy(language)

    def round_prompt_block(self, instance: Any) -> str:
        """每轮注入 GM 输入的当前冒险节点块（强制模型判断是否完成）。"""
        view = self._adventure_view(instance)
        active = (view or {}).get("active") or []
        if not active:
            return ""
        from src.engine.modules import progression_state

        try:
            round_number = int(progression_state.round_value(instance) or 0)
        except Exception:
            round_number = 0
        lines = [
            _text(instance, "adventure_prompt_heading"),
            _text(instance, "adventure_prompt_active"),
        ]
        for node in active:
            lines.append(_text(
                instance, "adventure_prompt_node",
                node_id=node.get('id'), name=node.get('name'), node_type=node.get('type'),
                description=str(node.get('description') or '').strip(),
            ))
        lines.append("")
        lines.append(_text(instance, "adventure_prompt_judge"))
        lines.append(_text(instance, "adventure_prompt_complete", round_number=round_number))
        lines.append(_text(instance, "adventure_prompt_incomplete"))
        return "\n".join(lines)

    def _encounter_preset(self, instance: Any, encounter_ref: str, preset_id: str = "") -> dict[str, Any] | None:
        bundle = self._adventure_bundle(instance)
        if bundle is None:
            return None
        kind, _, catalog_id = str(encounter_ref or "").partition(":")
        if kind != "encounter_catalog":
            return None
        catalog = (bundle.entities.get("encounter_catalog") or {}).get(catalog_id)
        if not isinstance(catalog, dict):
            return None
        presets = catalog.get("presets") or []
        if preset_id:
            return next((item for item in presets if str(item.get("id")) == preset_id), None)
        return presets[0] if presets else None

    def _grant_node_loot(self, instance: Any, node_id: str, summary: list[str]) -> None:
        state = fu_state(instance)
        granted = state.setdefault("loot_granted", [])
        if node_id in granted:
            return
        node = self._adventure_nodes(instance).get(node_id) or {}
        loot = node.get("loot") or {}
        if not loot:
            return
        ip_gain = int(loot.get("ip") or 0)
        items = [dict(item) for item in (loot.get("items") or []) if isinstance(item, dict)]
        for uid in self._player_uids(instance):
            sheet = instance.get_character_sheet(uid)
            if ip_gain:
                maximum = int(sheet.get("max_ip") or sheet.get("ip") or 0)
                sheet["ip"] = (min(maximum, int(sheet.get("ip") or 0) + ip_gain) if maximum else int(sheet.get("ip") or 0) + ip_gain)
                resources = sheet.get("resources")
                if isinstance(resources, dict) and isinstance(resources.get("ip"), dict):
                    resources["ip"]["current"] = sheet["ip"]
            inventory = sheet.setdefault("inventory", [])
            for item in items:
                for _ in range(max(1, int(item.get("quantity") or 1))):
                    inventory.append(dict(item))
            instance.set_character_sheet(uid, sheet)
        if ip_gain:
            summary.append(_text(instance, "loot_party_ip", amount=ip_gain))
        for item in items:
            summary.append(_text(instance, "loot_item", name=item.get('name'), quantity=int(item.get('quantity') or 1)))
        granted.append(node_id)

    def _complete_adventure_node(self, instance: Any, node_id: str, summary: list[str]) -> None:
        bundle = self._adventure_bundle(instance)
        if bundle is None or not node_id:
            return
        progress = adventure_runtime_state.progress(instance)
        try:
            activated = adventure_progress.complete_node(
                progress, bundle.adventure, node_id, instance=instance,
            )
            summary.append(
                _text(instance, "adventure_node_completed_next", node_id=node_id, nodes=", ".join(activated))
                if activated else _text(instance, "adventure_node_completed", node_id=node_id)
            )
        except Exception:
            return

    # ------------------------------------------------------------------ 绑定

    def game_binding(self, rule: Any, locale: str) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "rule_id": str(getattr(rule, "rule_id", "") or ""),
            "content_version": str(getattr(rule, "template", {}).get("rule_version") or ""),
            "state_schema_version": STATE_SCHEMA_VERSION,
        }

    def migrate_state(self, payload: dict[str, Any], from_version: int) -> dict[str, Any]:
        value = deepcopy(payload) if isinstance(payload, dict) else default_state()
        value["state_schema_version"] = STATE_SCHEMA_VERSION
        value.setdefault("conflict", None)
        value.setdefault("villains", {})
        value.setdefault("world", {"mysteries": [], "threats": []})
        value.setdefault("log", [])
        return value

    def describe_experience(self, rule: Any, locale: str) -> dict[str, Any]:
        return {
            "profile": "fabula_ultima",
            "rule_id": str(getattr(rule, "rule_id", "") or ""),
            "builder_mode": "professional",
            "combat": "conflict_scene",
        }

    # ------------------------------------------------------------------ 建卡

    def builder_choices(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]:
        return {
            "attributes": [
                {"key": key, "name": _attribute_label(_rule_language(rule), key), "options": list(DICE_OPTIONS)}
                for key in ATTR_KEYS
            ],
            "attribute_total": ATTRIBUTE_TOTAL,
            "classes": _class_names(rule),
            "class_levels_total": CLASS_LEVELS_TOTAL,
            "skills": {
                "mode": "total",
                "total": SKILL_POINT_TOTAL,
                "max_per_skill": MAX_SKILL_VALUE,
                "max_count": MAX_SKILLS,
                "pool": list((getattr(rule, "skill_pools", {}) or {}).keys()),
            },
            "starting_currency": STARTING_CURRENCY,
            "start_level": 5,
        }

    def validate_character(self, rule: Any, draft: dict[str, Any]) -> list[str]:
        return self._validate_character(rule, draft, _rule_language(rule))

    def _validate_character(self, rule: Any, draft: dict[str, Any], language: object) -> list[str]:
        errors: list[str] = []
        attrs = draft.get("attributes")
        if not isinstance(attrs, dict):
            errors.append(render(language, MESSAGES, "char_attributes_not_object"))
        else:
            missing = [key for key in ATTR_KEYS if key not in attrs]
            if missing:
                errors.append(render(language, MESSAGES, "char_attributes_missing", names=render(language, MESSAGES, "list_separator").join(missing)))
            total = 0
            for key in ATTR_KEYS:
                try:
                    value = int(attrs.get(key, 0))
                except (TypeError, ValueError):
                    errors.append(render(language, MESSAGES, "char_attribute_not_number", key=key))
                    continue
                if value not in DICE_OPTIONS:
                    errors.append(render(language, MESSAGES, "char_attribute_bad_die", key=key))
                total += value
            if not missing and total != ATTRIBUTE_TOTAL:
                errors.append(render(language, MESSAGES, "char_attribute_total", expected=ATTRIBUTE_TOTAL, total=total))

        levels = _parse_class_levels(draft, rule)
        names = _class_names(rule)
        if not levels:
            errors.append(render(language, MESSAGES, "char_class_required"))
        else:
            unknown = [name for name in levels if names and name not in names]
            if unknown:
                errors.append(render(language, MESSAGES, "char_class_unknown", names=render(language, MESSAGES, "list_separator").join(unknown)))
            if len(levels) != 2:
                errors.append(render(language, MESSAGES, "char_class_two"))
            if sum(levels.values()) != CLASS_LEVELS_TOTAL:
                errors.append(render(language, MESSAGES, "char_class_levels_total", expected=CLASS_LEVELS_TOTAL))
            if any(value < 1 for value in levels.values()):
                errors.append(render(language, MESSAGES, "char_class_level_min"))

        skills = draft.get("skills")
        if skills is None:
            skills = []
        if not isinstance(skills, list):
            errors.append(render(language, MESSAGES, "char_skills_not_list"))
        else:
            rows = [row for row in skills if isinstance(row, dict) and str(row.get("name") or "").strip()]
            if len(rows) > MAX_SKILLS:
                errors.append(render(language, MESSAGES, "char_skills_max", limit=MAX_SKILLS))
            total_points = 0
            for row in rows:
                try:
                    value = int(row.get("value") or 0)
                except (TypeError, ValueError):
                    errors.append(render(language, MESSAGES, "char_skill_level_invalid", name=row.get('name')))
                    continue
                if value < 1 or value > MAX_SKILL_VALUE:
                    errors.append(render(language, MESSAGES, "char_skill_level_range", name=row.get('name'), maximum=MAX_SKILL_VALUE))
                total_points += value
            if total_points > SKILL_POINT_TOTAL:
                errors.append(render(language, MESSAGES, "char_skill_points_max", limit=SKILL_POINT_TOTAL))
        return errors

    def derive_character(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]:
        return self._derive_character(rule, draft, _rule_language(rule))

    def _derive_character(self, rule: Any, draft: dict[str, Any], language: object) -> dict[str, Any]:
        attrs = {key: int((draft.get("attributes") or {}).get(key, 6)) for key in ATTR_KEYS}
        levels = _parse_class_levels(draft, rule)
        label = _class_label(levels) if levels else str(draft.get("class") or "")
        skills = [
            {"name": str(row.get("name") or "").strip(), "value": int(row.get("value") or 0)}
            for row in (draft.get("skills") or [])
            if isinstance(row, dict) and str(row.get("name") or "").strip()
        ]
        hp = int(attrs["mig"]) * 5 + 20
        mp = int(attrs["wlp"]) * 5
        sheet = {
            "attributes": attrs,
            "class": label,
            "class_levels": levels,
            "level": 5,
            "xp": 0,
            "skills": skills,
            "hp": hp,
            "max_hp": hp,
            "mp": mp,
            "max_mp": mp,
            "fabula": 3,
            "ip": 6,
            "gold": STARTING_CURRENCY,
            "currency": {"amount": STARTING_CURRENCY, "base_unit": "unit", "label": render(language, MESSAGES, "currency_label")},
            "equipment": [],
            "inventory": [],
            "identity": {"archetype": label},
            "resources": {
                "hp": {"label": render(language, MESSAGES, "resource_hp"), "current": hp, "max": hp, "min": 0},
                "mp": {"label": render(language, MESSAGES, "resource_mp"), "current": mp, "max": mp, "min": 0},
                "ip": {"label": render(language, MESSAGES, "resource_ip"), "current": 6, "max": 6, "min": 0},
                "fabula": {"label": render(language, MESSAGES, "resource_fabula"), "current": 3, "max": 99, "min": 0},
            },
        }
        return sheet

    def finalize_character(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]:
        errors = self.validate_character(rule, draft)
        if errors:
            raise ValueError(render(_rule_language(rule), MESSAGES, "sentence_separator").join(errors))
        return self.derive_character(rule, draft)

    def normalize_character_submission(
        self, rule: Any, character: dict[str, Any], locale: str = "",
    ) -> dict[str, Any]:
        # 调用方传入的是对局语言；没有传时退回规则对象上能看到的语言。
        language = locale or _rule_language(rule)
        errors = self._validate_character(rule, character, language)
        if errors:
            raise ValueError(render(language, MESSAGES, "sentence_separator").join(errors))
        derived = self._derive_character(rule, character, language)
        # 保留玩家自填的姓名 / 种族 / 背景 / 头像。
        for key in ("character_name", "name", "race", "background", "portrait"):
            if key in character and character[key]:
                derived[key] = character[key]
        return derived

    # ------------------------------------------------------------------ 意图

    def available_intents(self, instance: Any, actor_id: str) -> list[dict[str, Any]]:
        state = fu_state(instance)
        conflict = state.get("conflict")
        if isinstance(conflict, dict) and conflict.get("status") == "active":
            actor = conflict_mod.current_actor(conflict)
            if actor == f"player:{actor_id}" or actor_id == str(getattr(instance, "gm_uid", "")):
                return [
                    {"type": "conflict.action", "id": item["id"], "label": conflict_mod.action_label(language_of(instance), item["id"]), "needs_target": item["needs_target"]}
                    for item in conflict_mod.ACTIONS
                ] + [{"type": "conflict.end", "label": _text(instance, "intent_conflict_end")}]
            return []
        adventure = self._adventure_view(instance)
        intents: list[dict[str, Any]] = []
        if adventure:
            for node in adventure.get("active") or []:
                if node.get("type") == "encounter" and node.get("encounter_ref"):
                    intents.append({
                        "type": "conflict.start",
                        "label": _text(instance, "intent_encounter_start", name=node.get('name')),
                        "encounter_ref": node.get("encounter_ref"),
                        "node_id": node.get("id"),
                    })
                elif node.get("type") == "decision":
                    for choice in node.get("choices") or []:
                        intents.append({
                            "type": "adventure.node.complete",
                            "label": _text(instance, "intent_choice", label=choice.get('label')),
                            "node_id": node.get("id"),
                            "choice_id": choice.get("choice_id"),
                        })
                else:
                    intents.append({
                        "type": "adventure.node.complete",
                        "label": _text(instance, "intent_node_complete", name=node.get('name')),
                        "node_id": node.get("id"),
                    })
        return intents + [
            {"type": "conflict.start", "label": _text(instance, "intent_conflict_start"), "gm_only": True},
            {"type": "rest.take", "label": _text(instance, "intent_rest")},
            {"type": "fabula.spend", "label": _text(instance, "intent_fabula_spend")},
            {"type": "levelup.class", "label": _text(instance, "intent_class_levelup")},
        ]

    def next_automatic_intent(self, instance: Any) -> dict[str, Any] | None:
        """AI 席位的自动行动：优先治疗残血队友，其次挑可用技能，最后普攻/物资。"""
        state = fu_state(instance)
        conflict = state.get("conflict")
        if not (isinstance(conflict, dict) and conflict.get("status") == "active"):
            return None
        self._normalize_conflict_turn(conflict)
        actor = conflict_mod.current_actor(conflict)
        if not actor.startswith("player:"):
            return None
        uid = actor.split(":", 1)[1]
        if not is_ai_controlled(instance, uid):
            return None
        enemies = [eid for eid, row in conflict["enemies"].items() if not row.get("defeated")]
        if not enemies:
            return None
        participant = conflict["participants"].get(actor) or {}
        used_action = bool(participant.get("used_action"))
        used_quick = bool(participant.get("used_quick"))
        if used_action and used_quick:
            return None
        sheet = instance.get_character_sheet(uid)
        mp = int(sheet.get("mp") or 0)
        hp = int(sheet.get("hp") or 0)
        max_hp = int(sheet.get("max_hp") or 0) or 1

        skills: list[dict[str, Any]] = []
        for row in sheet.get("skills") or []:
            if not isinstance(row, dict):
                continue
            entry = skill_for(str(row.get("name") or ""), int(row.get("value") or 1))
            if entry is not None:
                skills.append(entry)

        def usable(entry: dict[str, Any]) -> bool:
            is_quick = entry.get("action") == "quick"
            if is_quick and used_quick:
                return False
            if not is_quick and used_action:
                return False
            if int(entry.get("mp") or 0) > mp:
                return False
            if int(entry.get("hp") or 0) >= hp:
                return False
            return True

        # 1) 治疗：队友低于半血时优先。
        allies = [key for key, row in conflict["participants"].items() if not row.get("down")]
        wounded = []
        for key in allies:
            ally_uid = key.split(":", 1)[1]
            ally_sheet = instance.get_character_sheet(ally_uid)
            ally_hp = int(ally_sheet.get("hp") or 0)
            ally_max = int(ally_sheet.get("max_hp") or 0) or 1
            if ally_hp * 2 <= ally_max:
                wounded.append(key)
        for entry in skills:
            if not any(str(effect.get("type")) == "heal" for effect in entry.get("effects") or []):
                continue
            if not usable(entry):
                continue
            scope = str((entry.get("target") or {}).get("scope") or "self")
            if scope == "self":
                if hp * 2 <= max_hp:
                    return {"type": "conflict.action", "action": "skill", "skill": entry["name"], "actor_id": actor}
            elif wounded:
                return {"type": "conflict.action", "action": "skill", "skill": entry["name"],
                        "targets": [wounded[0]], "actor_id": actor}

        # 2) 伤害技能：优先多目标、高消耗。
        damage_skills = [
            entry for entry in skills
            if (entry.get("multiattack") or any(str(effect.get("type")) == "damage" for effect in entry.get("effects") or []))
            and str(entry.get("kind") or "active") in {"active", "spell"}
            and usable(entry)
        ]
        damage_skills.sort(key=lambda entry: (int((entry.get("target") or {}).get("count") or 1), int(entry.get("mp") or 0)), reverse=True)
        if damage_skills:
            entry = damage_skills[0]
            count = max(1, int((entry.get("target") or {}).get("count") or 1))
            scope = str((entry.get("target") or {}).get("scope") or "enemy")
            if scope in ("enemy", "enemies"):
                return {"type": "conflict.action", "action": "skill", "skill": entry["name"],
                        "targets": enemies[:count], "actor_id": actor}
            if scope in ("all_enemies",):
                return {"type": "conflict.action", "action": "skill", "skill": entry["name"], "actor_id": actor}

        # 3) 兜底：残血喝物资，否则普攻。若本回合已用过主动行动，不能再出招。
        if used_action:
            return None
        if hp * 4 <= max_hp and int(sheet.get("ip") or 0) > 0:
            return {"type": "conflict.action", "action": "resource", "actor_id": actor}
        return {"type": "conflict.action", "action": "attack", "actor_id": actor, "target": enemies[0]}

    def validate_intent(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        intent_type = str(intent.get("type") or "")
        if intent_type in GM_ONLY_INTENTS and not intent.get("requester_is_gm"):
            return {"ok": False, "code": "GM_ONLY", "error": _text(instance, "gm_only")}
        state = fu_state(instance)
        conflict = state.get("conflict")
        if intent_type == "conflict.start":
            if isinstance(conflict, dict) and conflict.get("status") == "active":
                return {"ok": False, "code": "CONFLICT_ACTIVE", "error": _text(instance, "conflict_already_active")}
            return {"ok": True}
        if intent_type in {"conflict.action", "conflict.select_actor", "conflict.enemy_action", "conflict.ai_action", "conflict.reroll", "conflict.end"}:
            if not isinstance(conflict, dict) or conflict.get("status") != "active":
                return {"ok": False, "code": "NO_CONFLICT", "error": _text(instance, "no_conflict")}
            return {"ok": True}
        if intent_type in {"villain.escape", "villain.surrender", "villain.escalate"}:
            target = str(intent.get("villain_id") or "")
            if target not in state.get("villains", {}):
                return {"ok": False, "code": "NO_VILLAIN", "error": _text(instance, "no_villain")}
            return {"ok": True}
        if intent_type in {"rest.take", "fabula.spend", "levelup.class"}:
            return {"ok": True}
        if intent_type in gm_tools.TOOL_INTENTS:
            # 具体参数由各工具在结算时校验（失败抛 ValueError，宿主整体回滚）。
            return {"ok": True}
        return {"ok": False, "code": "UNKNOWN_INTENT", "error": _text(instance, "unknown_intent", intent_type=intent_type)}

    def resolve_intent(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        rng = rng or random.Random()
        intent_type = str(intent.get("type") or "")
        # 宿主的 /intents 入口不会替运行时调用 validate_intent：身份与前置条件
        # 必须在结算前由运行时自己把关，未通过时原样返回、不做任何修改。
        validation = self.validate_intent(instance, intent)
        if not validation.get("ok"):
            return validation
        if intent_type in gm_tools.TOOL_INTENTS:
            return self._resolve_gm_tool(instance, intent, rng)
        if intent_type in {"conflict.start", "conflict.action"} and getattr(instance, "state", None) == GameState.PAUSED:
            instance.state = GameState.ACTIVE_ACTION
        if intent_type == "conflict.start":
            return self._resolve_conflict_start(instance, intent, rng)
        if intent_type == "conflict.action":
            return self._resolve_conflict_action(instance, intent, rng)
        if intent_type == "conflict.select_actor":
            return self._resolve_conflict_select(instance, intent)
        if intent_type == "conflict.enemy_action":
            return self._resolve_conflict_enemy_action(instance, intent, rng)
        if intent_type == "conflict.ai_action":
            return self._resolve_conflict_ai_action(instance, intent, rng)
        if intent_type == "conflict.reroll":
            return self._resolve_conflict_reroll(instance, intent)
        if intent_type == "conflict.end":
            return self._resolve_conflict_end(instance)
        if intent_type in {"villain.escape", "villain.surrender", "villain.escalate"}:
            return self._resolve_villain(instance, intent)
        if intent_type == "fabula.spend":
            return self._resolve_fabula(instance, intent)
        if intent_type == "rest.take":
            return self._resolve_rest(instance, rng)
        if intent_type == "levelup.class":
            return self._resolve_levelup(instance, intent)
        raise ValueError(_text(instance, "unknown_intent", intent_type=intent_type))

    def apply_event_batch(
        self, instance: Any, batch: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(batch, dict):
            return {"applied": False}
        state = fu_state(instance)
        for event in batch.get("events") or []:
            if not isinstance(event, dict):
                continue
            if event.get("kind") == "log" and event.get("text"):
                _append_log(state, str(event["text"]))
        return {"applied": True, "batch_id": batch.get("batch_id"), "summary": batch.get("summary") or []}

    # ------------------------------------------------------------------ 意图实现

    def prepare_intent_submission(
        self, intent: dict[str, Any], requester_id: str, requester_is_gm: bool,
    ) -> dict[str, Any]:
        prepared = deepcopy(intent)
        # 服务端认定的身份，覆盖客户端可能自带的同名字段；validate_intent 据此
        # 拒绝非 GM 发起的冲突编排。
        prepared["requester_is_gm"] = bool(requester_is_gm)
        if not requester_is_gm and str(prepared.get("type") or "") in {
            "conflict.action", "fabula.spend", "levelup.class", "rest.take",
        }:
            # 非 GM 只能代表自己的席位行动；不能通过 actor_id 替别人操作。
            prepared["actor_id"] = f"player:{requester_id}"
        return prepared

    def memory_deltas_from_event_batch(
        self, batch: dict[str, Any], instance: Any,
    ) -> list[dict[str, Any]]:
        del batch, instance
        return []

    def _resolve_gm_tool(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        """商人 / 战利品 / 旅行 / 机会：规则逻辑在 ``gm_tools``，这里只做分发。"""
        state = fu_state(instance)
        intent_type = str(intent.get("type") or "")
        if intent_type == "merchant.start":
            summary = gm_tools.merchant_start(instance, state, intent, rng, _item_entries(language_of(instance)))
        elif intent_type == "merchant.buy":
            summary = gm_tools.merchant_buy(instance, state, intent, _item_entries(language_of(instance)))
        elif intent_type == "merchant.skip":
            summary = gm_tools.merchant_skip(instance, state, intent)
        elif intent_type == "loot.distribute":
            summary = gm_tools.loot_distribute(instance, state, intent, rng, _item_entries(language_of(instance)))
        elif intent_type == "travel.roll":
            summary = gm_tools.travel_roll(instance, state, intent, rng)
        elif intent_type in gm_tools.CLOCK_INTENTS:
            summary = gm_tools.clock_edit(instance, intent)
        elif intent_type == "narration.add":
            summary = gm_tools.narration_add(instance, intent)
        else:
            summary = gm_tools.opportunity_apply(instance, state, intent)
        return self._batch(intent_type, [{"kind": "log", "text": _text(instance, "sentence_separator").join(summary)}], summary)

    def _batch(self, intent_type: str, events: list[dict], summary: list[str]) -> dict[str, Any]:
        batch = {
            "batch_id": uuid.uuid4().hex,
            "intent_type": intent_type,
            "events": events,
            "summary": summary,
            "applied": True,
        }
        return {"ok": True, "event_batch": batch, "summary": summary}

    def _player_uids(self, instance: Any) -> list[str]:
        return [
            uid for uid, player in (getattr(instance, "players", {}) or {}).items()
            if isinstance((player or {}).get("character_sheet"), dict)
            and not (player or {}).get("character_sheet", {}).get("deceased")
        ]

    def _resolve_conflict_start(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        state = fu_state(instance)
        rule = self._rule(instance)
        # 「等待认领」的席位不进入冲突：没人负责，也不该占轮次。
        players = [
            uid for uid in self._player_uids(instance)
            if seat_control_mode(instance, uid) in {"human", "ai"}
        ]
        if not players:
            raise ValueError(_text(instance, "conflict_no_characters"))
        # 铁壁：最大 HP 永久 +技能等级×3（只应用一次，记录在角色卡上）。
        for uid in players:
            player_sheet = instance.get_character_sheet(uid)
            wall_level = self._skill_level(player_sheet, "铁壁")
            if wall_level and not player_sheet.get("_iron_wall_bonus"):
                bonus = wall_level * 3
                player_sheet["max_hp"] = int(player_sheet.get("max_hp") or 0) + bonus
                player_sheet["hp"] = int(player_sheet.get("hp") or 0) + bonus
                player_sheet["_iron_wall_bonus"] = bonus
                resources = player_sheet.get("resources")
                if isinstance(resources, dict) and isinstance(resources.get("hp"), dict):
                    resources["hp"]["max"] = int(resources["hp"].get("max") or 0) + bonus
                    resources["hp"]["current"] = int(resources["hp"].get("current") or 0) + bonus
                instance.set_character_sheet(uid, player_sheet)
        specs = intent.get("enemies")
        encounter_ref = str(intent.get("encounter_ref") or "")
        encounter_node = ""
        if (not isinstance(specs, list) or not specs) and encounter_ref:
            preset = self._encounter_preset(instance, encounter_ref, str(intent.get("preset_id") or ""))
            if preset is not None:
                specs = [
                    {"template_id": str(enemy.get("profile_id") or "goblin"), "name": str(enemy.get("name") or "")}
                    for enemy in (preset.get("enemies") or [])
                    if isinstance(enemy, dict)
                ]
                encounter_node = str(intent.get("node_id") or "")
                if not encounter_node:
                    view = self._adventure_view(instance) or {}
                    for node in view.get("active") or []:
                        if node.get("encounter_ref") == encounter_ref:
                            encounter_node = str(node.get("id") or "")
                            break
        if not isinstance(specs, list) or not specs:
            specs = [{"template_id": "goblin"}]
        if not encounter_ref and len(specs) > 5:
            raise ValueError(_text(instance, "conflict_manual_enemy_limit"))
        enemies: list[dict[str, Any]] = []
        for index, spec in enumerate(specs, start=1):
            spec = spec if isinstance(spec, dict) else {}
            template_id = str(spec.get("template_id") or "goblin")
            template = _monster_template(template_id)
            enemies.append(conflict_mod.build_enemy(
                template, f"enemy:{index}", name=str(spec.get("name") or ""), language=language_of(instance),
            ))

        # 先攻：队长掷【敏捷+洞察】，其余玩家各 +1；DL = 敌人最高先攻值。
        captain = players[0]
        sheet = instance.get_character_sheet(captain)
        die_a = conflict_mod.effective_die(sheet, "dex", [])
        die_b = conflict_mod.effective_die(sheet, "ins", [])
        rolls = conflict_mod.roll_pair(rng, die_a, die_b)
        total = sum(rolls) + max(0, len(players) - 1)
        dc = max(int(enemy.get("initiative") or 8) for enemy in enemies)
        first_side = "players" if total >= dc else "enemies"

        conflict = conflict_mod.new_conflict(
            state, players, enemies, objective=intent.get("objective"), first_side=first_side,
            language=language_of(instance),
        )
        if encounter_node:
            conflict["adventure_node_id"] = encounter_node
        state["conflict"] = conflict
        for enemy in enemies:
            if enemy.get("villain"):
                state["villains"][enemy["id"]] = {
                    "id": enemy["id"], "name": enemy["name"], "tier": enemy["tier"],
                    "fate": VILLAIN_FATE.get(enemy["tier"], 5),
                }
        events = [{"kind": "log", "text": _text(
            instance, "conflict_started_log",
            rolls=rolls, bonus=len(players) - 1, total=total, dc=dc, first_side=first_side,
        )}]
        summary = [_text(
            instance, "initiative_players_first" if first_side == 'players' else "initiative_enemies_first",
            roll_a=rolls[0], roll_b=rolls[1], bonus=len(players) - 1, total=total, dc=dc,
        )]
        # 步骤制：冲突开始后不自动结算敌方回合，由 GM 选择第一个行动者。
        self._apply_events(instance, state, events)
        end_events = self._check_end(conflict, summary, language_of(instance))
        if end_events:
            self._apply_events(instance, state, end_events)
            events.extend(end_events)
        return self._batch("conflict.start", events, summary)

    def _enemy_turn(self, instance: Any, conflict: dict[str, Any], enemy: dict[str, Any], rng: Any) -> dict[str, Any]:
        cooldowns = enemy.setdefault("skill_cooldowns", {})
        for name in list(cooldowns):
            cooldowns[name] = max(0, int(cooldowns[name]) - 1)
        available = [
            skill for skill in enemy.get("skills") or []
            if int(cooldowns.get(str(skill.get("name")), 0)) <= 0
        ]
        if available and rng.random() < 0.4:
            return self._enemy_skill(instance, conflict, enemy, rng.choice(available), rng)
        return self._enemy_attack(instance, conflict, enemy, rng)

    def _enemy_skill(self, instance: Any, conflict: dict[str, Any], enemy: dict[str, Any], skill: dict[str, Any], rng: Any, *, targets_override: list[str] | None = None) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        summary: list[str] = []
        name = str(skill.get("name") or "技能")
        cooldowns = enemy.setdefault("skill_cooldowns", {})
        cooldowns[name] = int(skill.get("cooldown") or 3)
        players = [key for key, row in conflict["participants"].items() if not row.get("down")]
        if str(skill.get("scope") or "") == "all_players":
            targets = players
        elif targets_override:
            targets = [key for key in targets_override if key in players][:1]
        else:
            targets = [rng.choice(players)] if players else []
        if str(skill.get("scope") or "") == "self":
            buff = skill.get("buff")
            if isinstance(buff, dict):
                enemy.setdefault("buffs", []).append(_stamp_duration(conflict, {
                    "stat": str(buff.get("stat") or "damage"),
                    "value": int(buff.get("value") or 1),
                    "remaining": int(buff.get("duration") or 1),
                }))
                summary.append(_text(instance, "enemy_skill_buff", enemy=enemy['name'], skill=name))
            return {"events": events, "summary": summary}
        if not targets:
            return {"events": events, "summary": summary}
        rolls = conflict_mod.roll_pair(rng, 8, 8)
        total = sum(rolls)
        high = fu_high_roll(rolls)
        summary.append(_text(instance, "enemy_skill_roll", enemy=enemy['name'], skill=name, roll_a=rolls[0], roll_b=rolls[1], total=total))
        targets_history: list[str] = []
        target_dcs: dict[str, int] = {}
        effect_history: list[dict[str, Any]] = []
        for target_key in targets:
            target_uid = target_key.split(":", 1)[1]
            target_name = str(instance.players.get(target_uid, {}).get("character_name") or target_uid)
            targets_history.append(target_name)
            sheet = instance.get_character_sheet(target_uid)
            armor = 0
            for item in sheet.get("equipment") or []:
                if isinstance(item, dict) and str(item.get("type") or "") == "armor":
                    try:
                        armor += int(item.get("armor") or 0)
                    except (TypeError, ValueError):
                        pass
            row = conflict["participants"].get(target_key) or {}
            dex_die = conflict_mod.effective_die(sheet, "dex", list(row.get("conditions") or []))
            dc = dex_die + armor + conflict_mod.buff_total(row, "defense")
            target_dcs[target_name] = dc
            if total < dc:
                summary.append(_text(instance, "defense_miss", target=target_name, dc=dc))
                continue
            damage = high + int(skill.get("bonus") or 5)
            damage += conflict_mod.buff_total(enemy, "damage")
            if row.get("defending"):
                damage = max(1, damage - 2)
            events.append({"kind": "player_damage", "uid": target_uid, "amount": damage})
            effect_history.append({"type": "damage", "target": target_name, "amount": damage})
            summary.append(_text(instance, "target_takes_damage", target=target_name, damage=damage))
            status_id = str(skill.get("status") or "")
            if status_id:
                duration = int(skill.get("duration") or 2)
                row.setdefault("statuses", []).append(_stamp_duration(conflict, {"id": status_id, "remaining": duration}))
                conditions = row.setdefault("conditions", [])
                if status_id not in conditions:
                    conditions.append(status_id)
                effect_history.append({"type": "status", "target": target_name, "status": status_id})
                summary.append(_text(instance, "target_status_duration", target=target_name, status=status_id, duration=duration))
        self._record_history(
            conflict,
            actor=enemy.get("id"), actor_name=enemy.get("name"), action="skill", skill=name,
            targets=targets_history,
            rolls=rolls, total=total,
            dc=(max(target_dcs.values()) if target_dcs else None),
            target_dcs=target_dcs,
            verdict="命中" if effect_history else "未命中",
            effects=effect_history,
        )
        return {"events": events, "summary": summary}

    def _enemy_attack(self, instance: Any, conflict: dict[str, Any], enemy: dict[str, Any], rng: Any, *, target_key: str = "") -> dict[str, Any]:
        players = [key for key, row in conflict["participants"].items() if not row.get("down")]
        if not players:
            return {"events": [], "summary": []}
        target_key = target_key if target_key in players else rng.choice(players)
        target_uid = target_key.split(":", 1)[1] if target_key.startswith("player:") else target_key
        target_name = str(instance.players.get(target_uid, {}).get("character_name") or target_uid)
        # 挺身守护：把这次攻击转移给守护者。
        for key, guardian_row in conflict["participants"].items():
            if guardian_row.get("guard_target") == target_key and not guardian_row.get("down"):
                target_key = key
                target_uid = key.split(":", 1)[1]
                target_name = str(instance.players.get(target_uid, {}).get("character_name") or target_uid)
                guardian_row["guard_target"] = None
                break
        attack = (enemy.get("attacks") or [{"name": _text(instance, "default_attack_name"), "damage": 6}])[0]
        die_a = die_b = 8
        rolls = conflict_mod.roll_pair(rng, die_a, die_b)
        total = sum(rolls)
        sheet = instance.get_character_sheet(target_uid)
        armor = 0
        for item in sheet.get("equipment") or []:
            if isinstance(item, dict) and str(item.get("type") or "") == "armor":
                try:
                    armor += int(item.get("armor") or 0)
                except (TypeError, ValueError):
                    pass
        row = conflict["participants"].get(target_key) or {}
        dex_die = conflict_mod.effective_die(sheet, "dex", list(row.get("conditions") or []))
        dc = dex_die + armor
        dc += conflict_mod.buff_total(row, "defense")
        events: list[dict[str, Any]] = []
        summary: list[str] = []
        is_crit = rolls[0] == rolls[1] and rolls[0] >= 6
        # 干涉火力：消耗 5+结算值 MP 使远程攻击自动失败（大成功无效）。
        intercept = self._skill_level(sheet, "干涉火力")
        if intercept and not is_crit:
            cost = 5 + total
            if int(sheet.get("mp") or 0) >= cost:
                sheet["mp"] = int(sheet.get("mp") or 0) - cost
                instance.set_character_sheet(target_uid, sheet)
                events.append({"kind": "log", "text": _text(instance, "intercept_log", target=target_name, skill="干涉火力", cost=cost)})
                summary.append(_text(instance, "intercept_summary", target=target_name, skill="干涉火力"))
                self._record_history(
                    conflict, actor=enemy.get("id"), actor_name=enemy.get("name"), action="attack",
                    targets=[target_name], rolls=rolls, total=total, dc=dc, verdict="被干涉",
                    effects=[], note="干涉火力",
                )
                return {"events": events, "summary": summary}
        if total >= dc:
            high = fu_high_roll(rolls)
            damage = high + int(attack.get("damage") or 6)
            damage += conflict_mod.buff_total(enemy, "damage")
            if row.get("defending"):
                damage = max(1, damage - 2)
                summary.append(_text(instance, "defend_reduction"))
            if any(str(buff.get("stat")) == "resist_all" for buff in row.get("buffs") or []):
                damage = max(1, damage // 2)
                summary.append(_text(instance, "cover_halved", target=target_name))
            events.append({"kind": "log", "text": _text(
                instance, "enemy_attack_hit_log",
                enemy=enemy['name'], target=target_name, rolls=rolls, total=total, dc=dc, damage=damage,
            )})
            events.append({"kind": "player_damage", "uid": target_uid, "amount": damage})
            summary.append(_text(instance, "enemy_attack_hit", enemy=enemy['name'], target=target_name, damage=damage))
            # 反击：命中结算值为偶数时，对攻击者进行一次顺势攻击（高值视为 0）。
            counter_level = self._skill_level(sheet, "反击")
            if counter_level and total % 2 == 0:
                weapon = conflict_mod.weapon_from_sheet(sheet)
                counter_damage = int((weapon or {}).get("damage") or 1) + conflict_mod.buff_total(row, "damage")
                events.append({"kind": "enemy_damage", "target": enemy.get("id"), "amount": counter_damage,
                               "damage_type": "physical", "affinity": ""})
                summary.append(_text(instance, "counterattack", target=target_name, enemy=enemy['name'], damage=counter_damage))
        else:
            events.append({"kind": "log", "text": _text(
                instance, "enemy_attack_miss_log",
                enemy=enemy['name'], target=target_name, rolls=rolls, total=total, dc=dc,
            )})
            summary.append(_text(instance, "enemy_attack_miss", enemy=enemy['name'], target=target_name))
        self._record_history(
            conflict,
            actor=enemy.get("id"), actor_name=enemy.get("name"), action="attack",
            targets=[target_name], rolls=rolls, total=total, dc=dc,
            verdict="成功" if total >= dc else "失败",
            effects=[{"type": "damage", "target": target_name, "amount": int(event.get("amount") or 0)}
                     for event in events if event.get("kind") == "player_damage"],
        )
        return {"events": events, "summary": summary}

    def _resolve_conflict_select(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        state = fu_state(instance)
        conflict = state.get("conflict")
        if not isinstance(conflict, dict) or conflict.get("status") != "active":
            raise ValueError(_text(instance, "no_conflict"))
        actor = str(intent.get("actor_id") or intent.get("actor") or "")
        if not actor:
            raise ValueError(_text(instance, "actor_not_specified"))
        state.pop("last_conflict_action", None)
        conflict_mod.select_actor(conflict, actor, language_of(instance))
        events = [{"kind": "turn_state", "step": int(conflict.get("step") or 0), "current": actor}]
        return self._batch("conflict.select_actor", events, [_text(instance, "current_actor", actor=actor)])

    def _resolve_conflict_enemy_action(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        state = fu_state(instance)
        conflict = state.get("conflict")
        if not isinstance(conflict, dict) or conflict.get("status") != "active":
            raise ValueError(_text(instance, "no_conflict"))
        current = conflict_mod.current_actor(conflict)
        if not current.startswith("enemy:"):
            raise ValueError(_text(instance, "current_actor_not_enemy"))
        enemy = conflict["enemies"].get(current)
        if not isinstance(enemy, dict) or enemy.get("defeated"):
            raise ValueError(_text(instance, "enemy_cannot_act"))
        state.pop("last_conflict_action", None)
        action = str(intent.get("action") or "attack")
        target = str(intent.get("target") or "")
        events: list[dict[str, Any]] = []
        summary: list[str] = []
        if action == "attack":
            result = self._enemy_attack(instance, conflict, enemy, rng, target_key=target)
            events.extend(result["events"])
            summary.extend(result["summary"])
        elif action == "skill":
            skill_name = str(intent.get("skill") or "")
            skill = next((row for row in (enemy.get("skills") or []) if str(row.get("name") or "") == skill_name), None)
            if not isinstance(skill, dict):
                raise ValueError(_text(instance, "enemy_skill_missing"))
            cooldowns = enemy.setdefault("skill_cooldowns", {})
            if int(cooldowns.get(skill_name, 0) or 0) > 0:
                raise ValueError(_text(instance, "enemy_skill_cooldown", skill=skill_name))
            result = self._enemy_skill(instance, conflict, enemy, skill, rng, targets_override=[target] if target else None)
            events.extend(result["events"])
            summary.extend(result["summary"])
        elif action == "defend":
            enemy["defending"] = True
            events.append({"kind": "log", "text": _text(instance, "enemy_defend_log", enemy=enemy['name'])})
            summary.append(_text(instance, "enemy_defend", enemy=enemy['name']))
        elif action == "hinder":
            target_key = target if target in conflict["participants"] else ""
            row = conflict["participants"].get(target_key)
            if not isinstance(row, dict):
                raise ValueError(_text(instance, "hinder_target_invalid"))
            if "shaken" not in (row.setdefault("conditions", [])):
                row["conditions"].append("shaken")
            row.setdefault("statuses", []).append(_stamp_duration(conflict, {"id": "shaken", "remaining": None}))
            events.append({"kind": "log", "text": _text(instance, "enemy_hinder_log", enemy=enemy['name'], target=target_key)})
            summary.append(_text(instance, "enemy_hinder", enemy=enemy['name']))
        elif action == "objective":
            clocks = conflict.get("clocks") or {}
            clock = next(iter(clocks.values()), None) if isinstance(clocks, dict) else None
            if isinstance(clock, dict):
                clock["filled"] = min(int(clock.get("segments") or 6), int(clock.get("filled") or 0) + 1)
                summary.append(_text(instance, "enemy_objective_clock", enemy=enemy['name']))
            else:
                summary.append(_text(instance, "enemy_objective", enemy=enemy['name']))
            events.append({"kind": "log", "text": summary[-1]})
        elif action == "investigate":
            events.append({"kind": "log", "text": _text(instance, "enemy_investigate_log", enemy=enemy['name'])})
            summary.append(_text(instance, "enemy_investigate", enemy=enemy['name']))
        else:
            raise ValueError(_text(instance, "enemy_action_unavailable"))
        step = conflict_mod.finish_step(conflict)
        events.append({"kind": "turn_state", "step": step, "current": ""})
        self._apply_events(instance, state, events)
        end_events = self._check_end(conflict, summary, language_of(instance))
        if end_events:
            self._apply_events(instance, state, end_events)
            events.extend(end_events)
        return self._batch("conflict.enemy_action", events, summary)

    def _resolve_conflict_ai_action(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        state = fu_state(instance)
        conflict = state.get("conflict")
        if not isinstance(conflict, dict) or conflict.get("status") != "active":
            raise ValueError(_text(instance, "no_conflict"))
        current = conflict_mod.current_actor(conflict)
        if current.startswith("enemy:"):
            enemy = conflict["enemies"].get(current)
            if not isinstance(enemy, dict) or enemy.get("defeated"):
                raise ValueError(_text(instance, "enemy_cannot_act"))
            state.pop("last_conflict_action", None)
            result = self._enemy_turn(instance, conflict, enemy, rng)
            events = list(result["events"])
            summary = list(result["summary"])
            step = conflict_mod.finish_step(conflict)
            events.append({"kind": "turn_state", "step": step, "current": ""})
            self._apply_events(instance, state, events)
            end_events = self._check_end(conflict, summary, language_of(instance))
            if end_events:
                self._apply_events(instance, state, end_events)
                events.extend(end_events)
            return self._batch("conflict.ai_action", events, summary)
        if current.startswith("player:"):
            ai_intent = self.next_automatic_intent(instance)
            if not ai_intent:
                raise ValueError(_text(instance, "ai_no_action"))
            ai_intent["actor_id"] = current
            return self._resolve_conflict_action(instance, ai_intent, rng)
        raise ValueError(_text(instance, "no_current_actor"))

    def _capture_conflict_reroll(self, instance: Any, conflict: dict[str, Any], intent: dict[str, Any], actor: str) -> None:
        if not str(actor).startswith("player:"):
            return
        state = fu_state(instance)
        state["last_conflict_action"] = {
            "actor": str(actor),
            "intent": deepcopy(intent),
            "conflict": deepcopy(conflict),
            "sheets": {
                str(uid): deepcopy(instance.get_character_sheet(uid))
                for uid in instance.players
            },
        }

    def _resolve_conflict_reroll(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        state = fu_state(instance)
        conflict = state.get("conflict")
        last = state.get("last_conflict_action")
        if not isinstance(conflict, dict) or conflict.get("status") != "active":
            raise ValueError(_text(instance, "no_conflict"))
        if not isinstance(last, dict):
            raise ValueError(_text(instance, "reroll_nothing"))
        actor = str(last.get("actor") or "")
        if not actor.startswith("player:"):
            raise ValueError(_text(instance, "reroll_player_only"))
        uid = actor.split(":", 1)[1]
        requester = str(intent.get("submitted_by") or "")
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        if requester and requester != gm_uid and requester != uid:
            raise ValueError(_text(instance, "reroll_own_only"))
        sheet = instance.get_character_sheet(uid)
        if int(sheet.get("fabula") or 0) <= 0:
            raise ValueError(_text(instance, "fabula_short"))
        state["conflict"] = deepcopy(last.get("conflict") or conflict)
        for snap_uid, snap_sheet in (last.get("sheets") or {}).items():
            if snap_uid in instance.players and isinstance(snap_sheet, dict):
                instance.set_character_sheet(snap_uid, deepcopy(snap_sheet))
        sheet = instance.get_character_sheet(uid)
        sheet["fabula"] = int(sheet.get("fabula") or 0) - 1
        resources = sheet.get("resources")
        if isinstance(resources, dict) and isinstance(resources.get("fabula"), dict):
            resources["fabula"]["current"] = sheet["fabula"]
        instance.set_character_sheet(uid, sheet)
        result = self._resolve_conflict_action(instance, deepcopy(last.get("intent") or {}), random.Random())
        summary = list(result.get("summary") or [])
        summary.insert(0, _text(instance, "reroll_spent"))
        result["summary"] = summary
        return result

    def _resolve_conflict_action(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]:
        state = fu_state(instance)
        conflict = state.get("conflict")
        if not isinstance(conflict, dict) or conflict.get("status") != "active":
            raise ValueError(_text(instance, "no_conflict"))
        self._normalize_conflict_turn(conflict)
        action_id = str(intent.get("action") or "")
        actor = str(intent.get("actor_id") or "")
        if actor.startswith("player:"):
            uid = actor.split(":", 1)[1]
        else:
            uid = actor
        if not uid:
            uid = next((key.split(":", 1)[1] for key in conflict["participants"] if not conflict["participants"][key].get("down")), "")
        pkey = f"player:{uid}"
        current = conflict_mod.current_actor(conflict)
        if conflict.get("manual_steps"):
            if not current:
                raise ValueError(_text(instance, "gm_select_actor_first"))
            if actor and actor != current:
                raise ValueError(_text(instance, "not_current_actor", current=current))
        elif actor and current and actor != current and pkey != current and actor != f"player:{current}":
            raise ValueError(_text(instance, "not_your_turn", actor=actor, current=current))
        if action_id not in {item["id"] for item in conflict_mod.ACTIONS}:
            raise ValueError(_text(instance, "unknown_action", action=action_id))
        if conflict.get("manual_steps") and current.startswith("player:"):
            self._capture_conflict_reroll(instance, conflict, intent, current)

        events: list[dict[str, Any]] = []
        summary: list[str] = []
        sheet = instance.get_character_sheet(uid)
        participant = conflict["participants"].setdefault(pkey, conflict_mod.new_participant(pkey))
        for field, default in (
            ("conditions", []), ("statuses", []), ("buffs", []),
            ("used_action", False), ("used_quick", False),
            ("defending", False), ("down", False),
        ):
            participant.setdefault(field, default)
        conditions = list(participant.get("conditions") or [])
        is_quick = False
        history_entry: dict[str, Any] = {
            "actor": pkey,
            "actor_name": str(instance.players.get(uid, {}).get("character_name") or uid),
            "action": action_id,
        }

        if action_id == "skill":
            skill_name = str(intent.get("skill") or intent.get("skill_name") or "")
            level = 1
            for row in sheet.get("skills") or []:
                if isinstance(row, dict) and str(row.get("name") or "") == skill_name:
                    level = int(row.get("value") or 1)
                    break
            entry = skill_for(skill_name, level)
            if entry is None:
                raise ValueError(_text(instance, "skill_invalid"))
            is_quick = entry.get("action") == "quick"
        if is_quick and participant.get("used_quick") and not conflict.get("manual_steps"):
            raise ValueError(_text(instance, "quick_action_used"))
        if not is_quick and participant.get("used_action") and not conflict.get("manual_steps"):
            raise ValueError(_text(instance, "main_action_used"))

        if action_id == "attack":
            target_id = str(intent.get("target") or "")
            enemy = conflict["enemies"].get(target_id)
            if not enemy or enemy.get("defeated"):
                raise ValueError(_text(instance, "attack_target_invalid"))
            weapon = conflict_mod.weapon_from_sheet(sheet)
            a_key, b_key = conflict_mod.attack_attributes(intent, weapon)
            die_a = conflict_mod.effective_die(sheet, a_key, conditions)
            die_b = conflict_mod.effective_die(sheet, b_key, conditions)
            rolls = conflict_mod.roll_pair(rng, die_a, die_b)
            total = sum(rolls) + conflict_mod.buff_total(participant, "check")
            try:
                pending_bonus = int(sheet.get("pending_check_bonus") or 0)
            except (TypeError, ValueError):
                pending_bonus = 0
            if pending_bonus > 0:
                total += min(20, pending_bonus)
                sheet["pending_check_bonus"] = 0
                sheet.pop("pending_check_bonus_reason", None)
                instance.set_character_sheet(uid, sheet)
                summary.append(_text(instance, "opportunity_bonus", bonus=pending_bonus))
            dc = int(enemy.get("defense") or 10) + (2 if enemy.get("defending") else 0)
            verdict = conflict_mod.verdict_of(rolls, total, dc)
            hit = verdict in {"成功", "大成功"}
            history_entry.update({
                "targets": [enemy["name"]], "rolls": rolls, "total": total,
                "dc": dc, "verdict": verdict,
            })
            summary.append(_text(
                instance, "attack_roll",
                attr_a=_attribute_label(language_of(instance), a_key),
                attr_b=_attribute_label(language_of(instance), b_key),
                roll_a=rolls[0], roll_b=rolls[1], total=total, dc=dc,
                verdict=_verdict_label(language_of(instance), verdict),
            ))
            events.append({"kind": "log", "text": _text(
                instance, "attack_log",
                enemy=enemy['name'], rolls=rolls, total=total, dc=dc,
                verdict=_verdict_label(language_of(instance), verdict),
            )})
            if hit:
                high = fu_high_roll(rolls)
                base = high + int((weapon or {}).get("damage") or 1) + conflict_mod.buff_total(participant, "damage") + self._crisis_damage(sheet)
                multiplier, label = conflict_mod.affinity_multiplier(enemy.get("affinities") or {}, "physical", language_of(instance))
                damage = max(0, int(base * multiplier)) if multiplier >= 0 else 0
                if multiplier < 0:
                    damage = 0
                    events.append({"kind": "log", "text": _text(instance, "enemy_absorbed", enemy=enemy['name'])})
                events.append({
                    "kind": "enemy_damage", "target": target_id, "amount": damage,
                    "damage_type": "physical", "affinity": label,
                })
                history_entry.setdefault("effects", []).append({
                    "type": "damage", "target": enemy["name"], "amount": damage,
                    "affinity": label, "multiplier": multiplier,
                })
                summary.append(_text(
                    instance, "attack_hit_affinity" if label else "attack_hit",
                    enemy=enemy['name'], damage=damage, affinity=label,
                ))
        elif action_id == "defend":
            conflict["participants"].setdefault(pkey, {})["defending"] = True
            cover_key = str(intent.get("target") or "")
            if cover_key:
                if not cover_key.startswith("player:"):
                    cover_key = f"player:{cover_key}"
                cover_row = conflict["participants"].get(cover_key)
                if isinstance(cover_row, dict):
                    cover_row.setdefault("buffs", []).append(_stamp_duration(conflict, {"stat": "resist_all", "value": 1, "remaining": 1}))
                    summary.append(_text(instance, "defend_cover", actor=uid, target=cover_key))
            history_entry["text"] = _text(instance, "history_defend_cover" if cover_key else "history_defend")
            summary.append(_text(instance, "defend", actor=uid))
            events.append({"kind": "log", "text": _text(instance, "defend", actor=uid)})
        elif action_id == "hinder":
            target_id = str(intent.get("target") or "")
            enemy = conflict["enemies"].get(target_id)
            if not enemy or enemy.get("defeated"):
                raise ValueError(_text(instance, "hinder_target_invalid"))
            die_a = conflict_mod.effective_die(sheet, "ins", conditions)
            die_b = conflict_mod.effective_die(sheet, "wlp", conditions)
            rolls = conflict_mod.roll_pair(rng, die_a, die_b)
            total = sum(rolls)
            condition = str(intent.get("condition") or "slow")
            success = total >= 10
            history_entry.update({
                "targets": [enemy["name"]], "rolls": rolls, "total": total,
                "dc": 10, "verdict": "成功" if success else "失败",
            })
            if success:
                history_entry.setdefault("effects", []).append({
                    "type": "status", "target": enemy["name"], "status": condition,
                })
            summary.append(_text(
                instance, "hinder_roll",
                enemy=enemy['name'], roll_a=rolls[0], roll_b=rolls[1], total=total,
                verdict=_verdict_label(language_of(instance), "成功" if success else "失败"),
            ))
            if success:
                events.append({"kind": "enemy_condition", "target": target_id, "condition": condition})
                summary.append(_text(instance, "target_status", target=enemy['name'], status=condition))
        elif action_id == "objective":
            clock_id = str(intent.get("clock_id") or next(iter(conflict.get("clocks") or {}), ""))
            if not clock_id:
                raise ValueError(_text(instance, "objective_no_clock"))
            dc = int(intent.get("dc") or 10)
            die_a = conflict_mod.effective_die(sheet, "ins", conditions)
            die_b = conflict_mod.effective_die(sheet, "wlp", conditions)
            rolls = conflict_mod.roll_pair(rng, die_a, die_b)
            total = sum(rolls)
            delta = 0
            if total >= dc:
                delta = 1 + (1 if total >= dc + 3 else 0) + (1 if total >= dc + 6 else 0)
            history_entry.update({
                "rolls": rolls, "total": total, "dc": dc,
                "verdict": "成功" if total >= dc else "失败",
                "effects": ([{"type": "clock", "amount": delta}] if delta else []),
            })
            summary.append(_text(instance, "objective_roll", roll_a=rolls[0], roll_b=rolls[1], total=total, dc=dc, delta=delta))
            if delta:
                events.append({"kind": "clock", "clock_id": clock_id, "delta": delta})
        elif action_id == "investigate":
            die_a = conflict_mod.effective_die(sheet, "ins", conditions)
            die_b = conflict_mod.effective_die(sheet, "ins", conditions)
            rolls = conflict_mod.roll_pair(rng, die_a, die_b)
            total = sum(rolls)
            target_id = str(intent.get("target") or "")
            enemy = conflict["enemies"].get(target_id) if target_id else None
            if enemy:
                history_entry["targets"] = [enemy.get("name")]
            history_entry.update({"rolls": rolls, "total": total, "dc": 10,
                                  "verdict": "成功" if total >= 10 else "失败"})
            summary.append(_text(
                instance, "investigate_success" if total >= 10 else "investigate_failure",
                roll_a=rolls[0], roll_b=rolls[1], total=total,
            ))
            insight = self._skill_level(sheet, "灵光洞见")
            if total >= 10 and enemy:
                facts: list[tuple[str, str]] = [
                    (_text(instance, "fact_max_hp"), str(enemy.get("max_hp") or enemy.get("hp") or "")),
                    (_text(instance, "fact_defense"), str(enemy.get("defense") or "")),
                    (_text(instance, "fact_magic_defense"), str(enemy.get("mdef") or "")),
                ]
                weak = [key for key, value in (enemy.get("affinities") or {}).items() if value == "weak"]
                resist = [key for key, value in (enemy.get("affinities") or {}).items() if value == "resist"]
                if weak:
                    facts.append((_text(instance, "fact_weakness"), _text(instance, "list_separator").join(weak)))
                if resist:
                    facts.append((_text(instance, "fact_resistance"), _text(instance, "list_separator").join(resist)))
                reveal_count = 1 + (1 if insight and total >= 13 else 0)
                for _ in range(min(reveal_count, len(facts))):
                    label, value = facts.pop(rng.randrange(len(facts)))
                    summary.append(_text(instance, "investigate_reveal", enemy=enemy.get('name'), fact=label, value=value))
                    history_entry.setdefault("effects", []).append(
                        {"type": "reveal", "target": enemy.get("name"), "status": f"{label} {value}"}
                    )
        elif action_id == "resource":
            if int(sheet.get("ip") or 0) <= 0:
                raise ValueError(_text(instance, "ip_short"))
            item = str(intent.get("item") or "potion")
            history_entry["item"] = item
            sheet["ip"] = int(sheet.get("ip") or 0) - 1
            instance.set_character_sheet(uid, sheet)
            if item == "bomb":
                target_key = str(intent.get("target") or "")
                enemy = conflict["enemies"].get(target_key)
                if not enemy or enemy.get("defeated"):
                    raise ValueError(_text(instance, "bomb_target_invalid"))
                history_entry["targets"] = [enemy["name"]]
                history_entry.setdefault("effects", []).append(
                    {"type": "damage", "target": enemy["name"], "amount": 10}
                )
                events.append({"kind": "enemy_damage", "target": target_key, "amount": 10,
                               "damage_type": "fire", "affinity": ""})
                summary.append(_text(instance, "resource_bomb", enemy=enemy['name']))
            else:
                target_key = str(intent.get("target") or pkey)
                if target_key and not target_key.startswith("player:"):
                    target_key = f"player:{target_key}"
                target_row = conflict["participants"].get(target_key)
                if not isinstance(target_row, dict) or target_row.get("down"):
                    raise ValueError(_text(instance, "resource_target_invalid"))
                target_uid = target_key.split(":", 1)[1]
                history_entry["targets"] = [
                    str(instance.players.get(target_uid, {}).get("character_name") or target_uid)
                ]
                if item == "remedy":
                    target_row["conditions"] = []
                    target_row["statuses"] = []
                    history_entry.setdefault("effects", []).append(
                        {"type": "cleanse", "target": target_uid}
                    )
                    summary.append(_text(instance, "resource_remedy", target=target_uid))
                else:
                    history_entry.setdefault("effects", []).append(
                        {"type": "heal", "target": target_uid, "amount": 20}
                    )
                    events.append({"kind": "player_heal", "uid": target_uid, "amount": 20})
                    summary.append(_text(instance, "resource_potion", target=target_uid))
        elif action_id == "equip":
            item_name = str(intent.get("item") or intent.get("equipment") or "")
            history_entry["text"] = _text(instance, "equip", item=item_name)
            inventory = sheet.setdefault("inventory", [])
            match = next(
                (row for row in inventory
                 if isinstance(row, dict) and str(row.get("name") or "") == item_name),
                None,
            )
            if match is None:
                raise ValueError(_text(instance, "equip_missing"))
            inventory.remove(match)
            item_type = str(match.get("type") or "misc")
            equipment = sheet.setdefault("equipment", [])
            replaced = None
            for index, row in enumerate(list(equipment)):
                if isinstance(row, dict) and str(row.get("type") or "") == item_type:
                    replaced = equipment.pop(index)
                    break
            equipment.append(match)
            if replaced is not None:
                inventory.append(replaced)
            instance.set_character_sheet(uid, sheet)
            events.append({"kind": "log", "text": _text(instance, "equip_log", actor=uid, item=item_name)})
            summary.append(
                _text(instance, "equip_replaced", item=item_name, replaced=replaced.get('name'))
                if replaced else _text(instance, "equip", item=item_name)
            )
        else:
            self._resolve_conflict_skill_v2(instance, conflict, uid, pkey, sheet, intent, rng, events, summary, history_entry)

        # 痛楚：每回合一次，对敌人造成伤害后回复技能等级×2 HP/MP。
        if any(str(effect.get("type")) == "damage" for effect in history_entry.get("effects") or []):
            pain_level = self._skill_level(sheet, "痛楚")
            if pain_level and participant.get("pain_healed_round") != conflict.get("round"):
                participant["pain_healed_round"] = conflict.get("round")
                heal = pain_level * 2
                sheet["hp"] = min(int(sheet.get("max_hp") or 0), int(sheet.get("hp") or 0) + heal)
                sheet["mp"] = min(int(sheet.get("max_mp") or 0), int(sheet.get("mp") or 0) + heal)
                instance.set_character_sheet(uid, sheet)
                summary.append(_text(instance, "skill_recover_hp_mp", skill="痛楚", amount=heal))
                events.append({"kind": "player_heal", "uid": uid, "amount": heal})

        self._record_history(conflict, **history_entry)

        if conflict.get("manual_steps"):
            participant["used_quick" if is_quick else "used_action"] = True
            step = conflict_mod.finish_step(conflict)
            events.append({"kind": "turn_state", "step": step, "current": ""})
        elif is_quick:
            participant["used_quick"] = True
            events.append({"kind": "log", "text": _text(instance, "quick_action_log", actor=uid)})
            summary.append(_text(instance, "quick_action_summary"))
        else:
            participant["used_action"] = True
            conflict_mod.advance_turn(conflict)
            events.append({"kind": "turn_state", "turn_index": conflict["turn_index"], "round": conflict["round"]})
            guard = 0
            while conflict.get("status") == "active" and guard < 20:
                guard += 1
                current = conflict_mod.current_actor(conflict)
                if not current.startswith("enemy:"):
                    break
                conflict_mod.begin_turn(conflict)
                enemy = conflict["enemies"].get(current)
                if not enemy or enemy.get("defeated"):
                    conflict_mod.advance_turn(conflict)
                    events.append({"kind": "turn_state", "turn_index": conflict["turn_index"], "round": conflict["round"]})
                    continue
                result = self._enemy_turn(instance, conflict, enemy, rng)
                events.extend(result["events"])
                summary.extend(result["summary"])
                conflict_mod.advance_turn(conflict)
                events.append({"kind": "turn_state", "turn_index": conflict["turn_index"], "round": conflict["round"]})
            if conflict.get("status") == "active":
                conflict_mod.begin_turn(conflict)
        # 先落伤害/状态，再判断胜负；否则会在敌人 HP 尚未扣除时误判"仍在战斗"。
        self._apply_events(instance, state, events)
        end_events = self._check_end(conflict, summary, language_of(instance))
        if end_events:
            self._apply_events(instance, state, end_events)
            events.extend(end_events)
            if any(event.get("result") == "victory" for event in end_events):
                node_id = str(conflict.get("adventure_node_id") or "")
                if node_id:
                    self._complete_adventure_node(instance, node_id, summary)
        return self._batch("conflict.action", events, summary)

    def _skill_targets(self, conflict: dict[str, Any], pkey: str, intent: dict[str, Any], entry: dict[str, Any], language: object = "") -> list[str]:
        target = entry.get("target") or {}
        scope = str(target.get("scope") or "enemy")
        count = max(1, int(target.get("count") or 1))
        raw = intent.get("targets")
        if raw is None:
            single = intent.get("target")
            raw = [single] if single else []
        raw = [str(item) for item in raw if item]
        enemies = [key for key, row in conflict["enemies"].items() if not row.get("defeated")]
        allies = [key for key, row in conflict["participants"].items() if not row.get("down")]
        others = [key for key in allies if key != pkey]
        if scope == "self":
            return [pkey]
        if scope == "all_enemies":
            return enemies
        if scope == "all_allies":
            return others
        if scope in ("enemy", "enemies"):
            valid = enemies
        elif scope == "self_or_ally":
            valid = allies
        elif scope in ("ally", "allies"):
            valid = others
        else:
            valid = enemies + others
        chosen: list[str] = []
        for key in raw:
            if key in valid and key not in chosen:
                chosen.append(key)
        if not chosen:
            raise ValueError(render(language, MESSAGES, "skill_target_invalid"))
        if len(chosen) > count:
            raise ValueError(render(language, MESSAGES, "skill_target_limit", count=count))
        return chosen

    def _resolve_conflict_skill(
        self, instance: Any, conflict: dict[str, Any], uid: str, pkey: str,
        sheet: dict[str, Any], intent: dict[str, Any], rng: Any,
        events: list[dict[str, Any]], summary: list[str],
        history_entry: dict[str, Any] | None = None,
    ) -> None:
        name = str(intent.get("skill") or intent.get("skill_name") or "")
        level = 1
        for row in sheet.get("skills") or []:
            if isinstance(row, dict) and str(row.get("name") or "") == name:
                level = int(row.get("value") or 1)
                break
        entry = skill_for(name, level)
        if entry is None:
            raise ValueError(_text(instance, "skill_invalid"))
        if history_entry is not None and str(entry.get("class") or "") == "generic":
            history_entry["note"] = _text(instance, "note_generic_skill")
        participant = conflict["participants"].setdefault(pkey, conflict_mod.new_participant(pkey))
        targets = self._skill_targets(conflict, pkey, intent, entry, language_of(instance))
        if history_entry is not None:
            history_entry["skill"] = name
            history_entry["targets"] = [
                (conflict["enemies"].get(key) or {}).get("name")
                or str(instance.players.get(key.split(":", 1)[-1], {}).get("character_name") or key)
                for key in targets
            ]
        mp = int(entry.get("mp") or 0)
        hp_cost = int(entry.get("hp") or 0)
        if mp and int(sheet.get("mp") or 0) < mp:
            raise ValueError(_text(instance, "mp_short"))
        if hp_cost and int(sheet.get("hp") or 0) <= hp_cost:
            raise ValueError(_text(instance, "hp_short"))
        if mp:
            sheet["mp"] = int(sheet.get("mp") or 0) - mp
            instance.set_character_sheet(uid, sheet)
            summary.append(_text(instance, "mp_spent", amount=mp))
        if hp_cost:
            events.append({"kind": "player_damage", "uid": uid, "amount": hp_cost})
            summary.append(_text(instance, "hp_cost", amount=hp_cost))

        attributes = entry["check"]["attributes"]
        conditions = list(participant.get("conditions") or [])
        die_a = conflict_mod.effective_die(sheet, attributes[0], conditions)
        die_b = conflict_mod.effective_die(sheet, attributes[1], conditions)
        rolls = conflict_mod.roll_pair(rng, die_a, die_b)
        total = sum(rolls) + conflict_mod.buff_total(participant, "check")
        try:
            pending_bonus = int(sheet.get("pending_check_bonus") or 0)
        except (TypeError, ValueError):
            pending_bonus = 0
        if pending_bonus > 0:
            total += min(20, pending_bonus)
            sheet["pending_check_bonus"] = 0
            sheet.pop("pending_check_bonus_reason", None)
            instance.set_character_sheet(uid, sheet)
            summary.append(_text(instance, "opportunity_bonus", bonus=pending_bonus))
        high = fu_high_roll(rolls)
        dl_kind = str(entry["check"].get("dl") or "defense")
        if history_entry is not None:
            history_entry.update({"rolls": rolls, "total": total})
        label_a = _attribute_label(language_of(instance), attributes[0])
        label_b = _attribute_label(language_of(instance), attributes[1])
        summary.append(_text(
            instance, "skill_roll",
            skill=name, attr_a=label_a, attr_b=label_b, roll_a=rolls[0], roll_b=rolls[1], total=total,
        ))

        for target_key in targets:
            if target_key.startswith("enemy:"):
                enemy = conflict["enemies"].get(target_key)
                if not enemy or enemy.get("defeated"):
                    continue
                dc = int(enemy.get("mdef") if dl_kind == "mdef" else enemy.get("defense") or 10)
                if total < dc:
                    summary.append(_text(instance, "defense_any_miss", target=enemy['name'], dc=dc))
                    continue
                summary.append(_text(instance, "defense_any_hit", target=enemy['name'], dc=dc))
                for effect in entry.get("effects") or []:
                    effect_type = str(effect.get("type") or "")
                    if effect_type == "damage":
                        bonus = int(effect.get("bonus") or 0) + conflict_mod.buff_total(participant, "damage")
                        base = high + bonus
                        damage_type = str(effect.get("damage_type") or "physical")
                        multiplier, label = conflict_mod.affinity_multiplier(enemy.get("affinities") or {}, damage_type, language_of(instance))
                        damage = max(0, int(base * multiplier)) if multiplier >= 0 else 0
                        events.append({
                            "kind": "enemy_damage", "target": target_key, "amount": damage,
                            "damage_type": damage_type, "affinity": label,
                        })
                        if history_entry is not None:
                            history_entry.setdefault("effects", []).append(
                                {"type": "damage", "target": enemy["name"], "amount": damage,
                                 "affinity": label, "multiplier": multiplier}
                            )
                        summary.append(_text(
                            instance, "skill_damage_affinity" if label else "skill_damage",
                            enemy=enemy['name'], damage=damage, affinity=label,
                        ))
                    elif effect_type == "status":
                        status_id = str(effect.get("id") or "weak")
                        duration = int(effect.get("duration") or 2)
                        enemy.setdefault("statuses", []).append(_stamp_duration(conflict, {"id": status_id, "remaining": duration}))
                        conditions = enemy.setdefault("conditions", [])
                        if status_id not in conditions:
                            conditions.append(status_id)
                        if history_entry is not None:
                            history_entry.setdefault("effects", []).append(
                                {"type": "status", "target": enemy["name"], "status": status_id}
                            )
                        summary.append(_text(instance, "target_status_duration", target=enemy['name'], status=status_id, duration=duration))
                    elif effect_type == "clock" and conflict.get("clocks"):
                        clock_id = str(effect.get("clock_id") or next(iter(conflict["clocks"])))
                        clock = conflict["clocks"].get(clock_id)
                        if clock:
                            clock["filled"] = min(int(clock.get("segments") or 1), int(clock.get("filled") or 0) + int(effect.get("segments") or 1))
            else:
                target_row = conflict["participants"].setdefault(target_key, conflict_mod.new_participant(target_key))
                target_uid = target_key.split(":", 1)[1] if target_key.startswith("player:") else target_key
                for effect in entry.get("effects") or []:
                    effect_type = str(effect.get("type") or "")
                    if effect_type == "heal":
                        amount = high + int(effect.get("bonus") or 0)
                        events.append({"kind": "player_heal", "uid": target_uid, "amount": amount})
                        if history_entry is not None:
                            history_entry.setdefault("effects", []).append(
                                {"type": "heal", "target": target_uid, "amount": amount}
                            )
                        summary.append(_text(instance, "target_recovers_hp", target=target_uid, amount=amount))
                    elif effect_type == "buff":
                        stat = str(effect.get("stat") or "check")
                        value = int(effect.get("value") or 1)
                        if history_entry is not None:
                            history_entry.setdefault("effects", []).append(
                                {"type": "buff", "target": target_uid, "stat": stat, "value": value}
                            )
                        duration = int(effect.get("duration") or 1)
                        target_row.setdefault("buffs", []).append(_stamp_duration(conflict, {"stat": stat, "value": value, "remaining": duration}))
                        summary.append(_text(instance, "target_buff_duration", target=target_uid, stat=stat, value=value, duration=duration))
                    elif effect_type == "status":
                        status_id = str(effect.get("id") or "weak")
                        duration = int(effect.get("duration") or 2)
                        target_row.setdefault("statuses", []).append(_stamp_duration(conflict, {"id": status_id, "remaining": duration}))
                        conditions = target_row.setdefault("conditions", [])
                        if status_id not in conditions:
                            conditions.append(status_id)
                        summary.append(_text(instance, "target_status_duration", target=target_uid, status=status_id, duration=duration))
        if history_entry is not None and not history_entry.get("verdict"):
            history_entry["verdict"] = "命中" if history_entry.get("effects") else "未命中"

    def _skill_level(self, sheet: dict[str, Any], name: str) -> int:
        for row in sheet.get("skills") or []:
            if isinstance(row, dict) and str(row.get("name") or "") == name:
                try:
                    return int(row.get("value") or 1)
                except (TypeError, ValueError):
                    return 1
        return 0

    def _weapon_is_ranged(self, weapon: dict[str, Any] | None) -> bool:
        name = str((weapon or {}).get("name") or "")
        return any(key in name for key in ("弓", "枪", "弩", "投", "镖", "铳", "炮"))

    def _attack_bonus(self, sheet: dict[str, Any], weapon: dict[str, Any] | None) -> int:
        return self._skill_level(sheet, "远程武器精通" if self._weapon_is_ranged(weapon) else "近战武器精通")

    def _normalize_conflict_turn(self, conflict: dict[str, Any]) -> None:
        """修复强制推进/跳过造成的回合指针越界或当前行动者已行动的死锁。"""
        if conflict.get("manual_steps"):
            return
        order = conflict_mod.order_for(conflict)
        if not order:
            return
        index = int(conflict.get("turn_index") or 0)
        if index >= len(order):
            conflict["turn_index"] = 0
            conflict["round"] = int(conflict.get("round") or 1) + 1
            conflict_mod.begin_turn(conflict)
            return
        current = conflict_mod.current_actor(conflict)
        if not current:
            return
        row = (conflict.get("participants") or {}).get(current) or (conflict.get("enemies") or {}).get(current) or {}
        if row.get("used_action"):
            conflict_mod.advance_turn(conflict)
            conflict_mod.begin_turn(conflict)

    def _damage_reduction(self, sheet: dict[str, Any]) -> int:
        return self._skill_level(sheet, "防御精通")

    def _crisis_damage(self, sheet: dict[str, Any]) -> int:
        hp = int(sheet.get("hp") or 0)
        max_hp = int(sheet.get("max_hp") or 1) or 1
        if hp * 2 <= max_hp:
            return self._skill_level(sheet, "肾上腺素") * 2
        return 0

    def _skill_cost(self, instance: Any, uid: str, sheet: dict[str, Any], entry: dict[str, Any], target_count: int = 1) -> None:
        mp = int(entry.get("mp") or 0)
        if entry.get("kind") == "spell":
            mp *= max(1, target_count)
        if mp and int(sheet.get("mp") or 0) < mp:
            raise ValueError(_text(instance, "mp_short"))
        if mp:
            sheet["mp"] = int(sheet.get("mp") or 0) - mp
            instance.set_character_sheet(uid, sheet)

    def _roll_skill(self, sheet: dict[str, Any], participant: dict[str, Any], entry: dict[str, Any], rng: Any, *, weapon: bool = False, spell: bool = False) -> tuple[list[int], int, int]:
        if weapon:
            attrs = conflict_mod.attack_attributes({}, conflict_mod.weapon_from_sheet(sheet))
        else:
            attrs = entry["check"]["attributes"]
        conditions = list(participant.get("conditions") or [])
        die_a = conflict_mod.effective_die(sheet, attrs[0], conditions)
        die_b = conflict_mod.effective_die(sheet, attrs[1], conditions)
        rolls = conflict_mod.roll_pair(rng, die_a, die_b)
        total = sum(rolls) + conflict_mod.buff_total(participant, "check")
        if spell:
            total += self._skill_level(sheet, "魔法炮击") * 2
        return list(rolls), total, fu_high_roll(rolls)

    def _resolve_card_skill(self, instance: Any, conflict: dict[str, Any], uid: str, pkey: str,
                            sheet: dict[str, Any], intent: dict[str, Any], entry: dict[str, Any],
                            targets: list[str], rng: Any, events: list[dict[str, Any]],
                            summary: list[str], history_entry: dict[str, Any] | None) -> None:
        name = str(entry.get("name") or "")
        participant = conflict["participants"].setdefault(pkey, conflict_mod.new_participant(pkey))
        if name == "治疗":
            cost = 10 * max(1, len(targets))
            if int(sheet.get("mp") or 0) < cost:
                raise ValueError(_text(instance, "mp_short"))
            sheet["mp"] = int(sheet.get("mp") or 0) - cost
            instance.set_character_sheet(uid, sheet)
            effects: list[dict[str, Any]] = []
            for target_key in targets:
                target_uid = target_key.split(":", 1)[1]
                target_name = str(instance.players.get(target_uid, {}).get("character_name") or target_uid)
                events.append({"kind": "player_heal", "uid": target_uid, "amount": 40})
                effects.append({"type": "heal", "target": target_name, "amount": 40})
                summary.append(_text(instance, "target_restores_hp", target=target_name, amount=40))
            if history_entry is not None:
                history_entry.update({"effects": effects, "verdict": "成功"})
            return
        if name == "元素武器":
            if int(sheet.get("mp") or 0) < 10:
                raise ValueError(_text(instance, "mp_short"))
            sheet["mp"] = int(sheet.get("mp") or 0) - 10
            instance.set_character_sheet(uid, sheet)
            element = str(intent.get("element") or "fire")
            participant["weapon_element"] = element
            summary.append(_text(instance, "weapon_element", actor=uid, element=element))
            if history_entry is not None:
                history_entry.update({"effects": [{"type": "buff", "target": uid, "stat": "weapon_element", "value": element}], "verdict": "成功"})
            return
        if name == "翱翔击":
            if int(sheet.get("mp") or 0) < 10:
                raise ValueError(_text(instance, "mp_short"))
            sheet["mp"] = int(sheet.get("mp") or 0) - 10
            instance.set_character_sheet(uid, sheet)
            weapon = conflict_mod.weapon_from_sheet(sheet)
            rolls, total, high = self._roll_skill(sheet, participant, entry, rng, weapon=True)
            total += self._attack_bonus(sheet, weapon)
            enemy = conflict["enemies"].get(targets[0]) if targets else None
            if enemy:
                dc = int(enemy.get("defense") or 10) + (2 if enemy.get("defending") else 0)
                if total >= dc:
                    damage = high + int((weapon or {}).get("damage") or 1) + 5 + conflict_mod.buff_total(participant, "damage")
                    damage += self._crisis_damage(sheet)
                    events.append({"kind": "enemy_damage", "target": targets[0], "amount": damage, "damage_type": str(participant.get("weapon_element") or "physical"), "affinity": ""})
                    summary.append(_text(instance, "target_hit_damage", target=enemy['name'], damage=damage))
                    if history_entry is not None:
                        history_entry.setdefault("effects", []).append({"type": "damage", "target": enemy["name"], "amount": damage})
                else:
                    summary.append(_text(instance, "defense_miss", target=enemy['name'], dc=dc))
            if history_entry is not None:
                history_entry.update({"rolls": rolls, "total": total, "verdict": "命中" if history_entry.get("effects") else "未命中"})

    def _resolve_conflict_skill_v2(
        self, instance: Any, conflict: dict[str, Any], uid: str, pkey: str,
        sheet: dict[str, Any], intent: dict[str, Any], rng: Any,
        events: list[dict[str, Any]], summary: list[str],
        history_entry: dict[str, Any] | None = None,
    ) -> None:
        name = str(intent.get("skill") or "")
        level = self._skill_level(sheet, name) or 1
        entry = skill_for(name, level)
        if entry is None:
            raise ValueError(_text(instance, "skill_invalid"))
        kind = str(entry.get("kind") or "active")
        if kind == "passive":
            raise ValueError(_text(instance, "skill_passive", skill=name))
        if kind == "reaction" and name != "挺身守护":
            raise ValueError(_text(instance, "skill_reaction", skill=name))
        participant = conflict["participants"].setdefault(pkey, conflict_mod.new_participant(pkey))
        targets = self._skill_targets(conflict, pkey, intent, entry, language_of(instance))
        if history_entry is not None:
            history_entry["skill"] = name
            history_entry["targets"] = [
                (conflict["enemies"].get(key) or {}).get("name")
                or str(instance.players.get(key.split(":", 1)[-1], {}).get("character_name") or key)
                for key in targets
            ]
            if kind == "reaction":
                history_entry["note"] = _text(instance, "note_reaction_skill")

        if name == "挺身守护":
            target_key = targets[0] if targets else ""
            if not target_key:
                raise ValueError(_text(instance, "ally_required"))
            participant["guard_target"] = target_key
            history_entry and history_entry.update({"text": "挺身守护", "targets": [str(instance.players.get(target_key.split(":", 1)[-1], {}).get("character_name") or target_key)]})
            summary.append(_text(instance, "guard_ready", actor=uid, target=target_key))
            return

        if name in {"治疗", "元素武器", "翱翔击"}:
            self._resolve_card_skill(instance, conflict, uid, pkey, sheet, intent, entry, targets, rng, events, summary, history_entry)
            return
        if kind == "spell":
            self._resolve_spell_skill(instance, conflict, uid, pkey, sheet, intent, entry, targets, rng, events, summary, history_entry)
            return
        if entry.get("multiattack"):
            self._resolve_multiattack(instance, conflict, uid, pkey, sheet, intent, entry, targets, rng, events, summary, history_entry)
            return
        if entry.get("no_damage"):
            self._resolve_no_damage(instance, conflict, uid, pkey, sheet, intent, entry, targets, rng, events, summary, history_entry)
            return
        if name == "暗影击":
            self._resolve_shadow_strike(instance, conflict, uid, pkey, sheet, intent, entry, targets, rng, events, summary, history_entry)
            return

        # 默认：带效果的技能（目前是鹰眼等增益）
        self._skill_cost(instance, uid, sheet, entry, len(targets))
        if entry.get("no_check"):
            rolls, total, high = [], 0, 0
        else:
            rolls, total, high = self._roll_skill(sheet, participant, entry, rng)
            if history_entry is not None:
                history_entry.update({"rolls": rolls, "total": total})
        for effect in entry.get("effects") or []:
            effect_type = str(effect.get("type") or "")
            if effect_type == "buff":
                value = int(effect.get("value") or 1)
                if effect.get("scale") == "level":
                    value *= level
                duration = int(effect.get("duration") or 1)
                to_ally = str(effect.get("target") or "") == "ally"
                target_key = targets[0] if (to_ally and targets) else pkey
                target_uid = target_key.split(":", 1)[1]
                target_name = str(instance.players.get(target_uid, {}).get("character_name") or target_uid)
                target_row = conflict["participants"].setdefault(target_key, conflict_mod.new_participant(target_key))
                stat = str(effect.get("stat") or "check")
                if stat == "attribute_die_up":
                    attribute = str(intent.get("attribute") or "dex")
                    if attribute not in {"dex", "ins", "mig", "wlp"}:
                        attribute = "dex"
                    target_row.setdefault("buffs", []).append(_stamp_duration(conflict, {"stat": "die_up", "attribute": attribute, "remaining": duration}))
                    summary.append(_text(instance, "attribute_die_up", target=target_name, attribute=attribute))
                    if history_entry is not None:
                        history_entry.setdefault("effects", []).append(
                            {"type": "attribute_die_up", "target": target_name, "stat": attribute}
                        )
                else:
                    target_row.setdefault("buffs", []).append(_stamp_duration(conflict, {"stat": stat, "value": value, "remaining": duration}))
                    summary.append(_text(instance, "target_buff", target=target_name, stat=stat, value=value))
                    if history_entry is not None:
                        history_entry.setdefault("effects", []).append(
                            {"type": "buff", "target": target_name, "stat": stat, "value": value}
                        )
            elif effect_type == "heal":
                target_key = targets[0] if targets else pkey
                target_uid = target_key.split(":", 1)[1]
                target_name = str(instance.players.get(target_uid, {}).get("character_name") or target_uid)
                amount = level * 10 if effect.get("scale") == "level*10" else int(effect.get("bonus") or 0)
                events.append({"kind": "player_heal", "uid": target_uid, "amount": amount})
                summary.append(_text(instance, "target_restores_hp", target=target_name, amount=amount))
                if history_entry is not None:
                    history_entry.setdefault("effects", []).append({"type": "heal", "target": target_name, "amount": amount})
        if history_entry is not None:
            history_entry["verdict"] = "成功"

    def _resolve_multiattack(self, instance: Any, conflict: dict[str, Any], uid: str, pkey: str,
                             sheet: dict[str, Any], intent: dict[str, Any], entry: dict[str, Any],
                             targets: list[str], rng: Any, events: list[dict[str, Any]],
                             summary: list[str], history_entry: dict[str, Any] | None) -> None:
        self._skill_cost(instance, uid, sheet, entry, len(targets))
        participant = conflict["participants"].get(pkey) or {}
        weapon = conflict_mod.weapon_from_sheet(sheet)
        rolls, total, high = self._roll_skill(sheet, participant, entry, rng, weapon=True)
        total += self._attack_bonus(sheet, weapon)
        if history_entry is not None:
            history_entry.update({"rolls": rolls, "total": total})
        summary.append(_text(instance, "skill_roll_plain", skill=entry['name'], roll_a=rolls[0], roll_b=rolls[1], total=total))
        for target_key in targets:
            enemy = conflict["enemies"].get(target_key)
            if not enemy or enemy.get("defeated"):
                continue
            dc = int(enemy.get("defense") or 10) + (2 if enemy.get("defending") else 0)
            if total < dc:
                summary.append(_text(instance, "defense_miss", target=enemy['name'], dc=dc))
                continue
            damage = high + int((weapon or {}).get("damage") or 1) + conflict_mod.buff_total(participant, "damage")
            damage += int(enemy.get("vulnerable") or 0)
            multiplier, label = conflict_mod.affinity_multiplier(enemy.get("affinities") or {}, "physical", language_of(instance))
            damage = max(0, int(damage * multiplier)) if multiplier >= 0 else 0
            events.append({"kind": "enemy_damage", "target": target_key, "amount": damage, "damage_type": "physical", "affinity": label})
            summary.append(_text(
                instance, "target_hit_damage_affinity" if label else "target_hit_damage",
                target=enemy['name'], damage=damage, affinity=label,
            ))
            if history_entry is not None:
                history_entry.setdefault("effects", []).append({"type": "damage", "target": enemy["name"], "amount": damage, "affinity": label, "multiplier": multiplier})
        if history_entry is not None:
            history_entry["verdict"] = "命中" if history_entry.get("effects") else "未命中"

    def _resolve_no_damage(self, instance: Any, conflict: dict[str, Any], uid: str, pkey: str,
                           sheet: dict[str, Any], intent: dict[str, Any], entry: dict[str, Any],
                           targets: list[str], rng: Any, events: list[dict[str, Any]],
                           summary: list[str], history_entry: dict[str, Any] | None) -> None:
        self._skill_cost(instance, uid, sheet, entry, len(targets))
        participant = conflict["participants"].get(pkey) or {}
        weapon = conflict_mod.weapon_from_sheet(sheet)
        rolls, total, _ = self._roll_skill(sheet, participant, entry, rng, weapon=True)
        total += self._attack_bonus(sheet, weapon)
        choice = str(intent.get("choice") or (entry.get("choices") or ["weak"])[0])
        level = self._skill_level(sheet, entry["name"]) or 1
        if history_entry is not None:
            history_entry.update({"rolls": rolls, "total": total, "choice": choice})
        for target_key in targets:
            enemy = conflict["enemies"].get(target_key)
            if not enemy or enemy.get("defeated"):
                continue
            dc = int(enemy.get("defense") or 10) + (2 if enemy.get("defending") else 0)
            if total < dc:
                summary.append(_text(instance, "defense_miss", target=enemy['name'], dc=dc))
                continue
            if choice in {"daze", "weak", "shaken", "slow"}:
                enemy.setdefault("statuses", []).append(_stamp_duration(conflict, {"id": choice, "remaining": 2}))
                if choice not in enemy.setdefault("conditions", []):
                    enemy["conditions"].append(choice)
                summary.append(_text(instance, "target_status", target=enemy['name'], status=choice))
                if history_entry is not None:
                    history_entry.setdefault("effects", []).append({"type": "status", "target": enemy["name"], "status": choice})
            elif choice == "mp":
                summary.append(_text(instance, "enemy_mp_loss", enemy=enemy['name'], amount=level * 10))
                if history_entry is not None:
                    history_entry.setdefault("effects", []).append({"type": "status", "target": enemy["name"], "status": "mp_burn"})
            elif choice == "shield":
                enemy["defense"] = max(1, int(enemy.get("defense") or 10) - 2)
                summary.append(_text(instance, "enemy_shield_destroyed", enemy=enemy['name']))
                if history_entry is not None:
                    history_entry.setdefault("effects", []).append({"type": "buff", "target": enemy["name"], "stat": "defense", "value": -2})
            elif choice == "armor":
                enemy["defense"] = max(1, int(enemy.get("defense") or 10) - 2)
                summary.append(_text(instance, "enemy_armor_destroyed", enemy=enemy['name']))
                if history_entry is not None:
                    history_entry.setdefault("effects", []).append({"type": "buff", "target": enemy["name"], "stat": "defense", "value": -2})
            elif choice == "vulnerable":
                enemy["vulnerable"] = int(enemy.get("vulnerable") or 0) + level * 2
                summary.append(_text(instance, "enemy_vulnerable", enemy=enemy['name'], amount=level * 2))
                if history_entry is not None:
                    history_entry.setdefault("effects", []).append({"type": "buff", "target": enemy["name"], "stat": "vulnerable", "value": level * 2})
        if history_entry is not None:
            history_entry["verdict"] = "命中" if history_entry.get("effects") else "未命中"

    def _resolve_shadow_strike(self, instance: Any, conflict: dict[str, Any], uid: str, pkey: str,
                               sheet: dict[str, Any], intent: dict[str, Any], entry: dict[str, Any],
                               targets: list[str], rng: Any, events: list[dict[str, Any]],
                               summary: list[str], history_entry: dict[str, Any] | None) -> None:
        level = self._skill_level(sheet, "暗影击") or 1
        mig_die = conflict_mod.effective_die(sheet, "mig", list((conflict["participants"].get(pkey) or {}).get("conditions") or []))
        mig_roll = rng.randint(1, mig_die)
        hp = int(sheet.get("hp") or 0)
        if hp <= mig_roll:
            raise ValueError(_text(instance, "hp_short_for_skill", skill="暗影击"))
        sheet["hp"] = hp - mig_roll
        instance.set_character_sheet(uid, sheet)
        events.append({"kind": "player_damage", "uid": uid, "amount": mig_roll})
        summary.append(_text(instance, "skill_hp_loss", skill="暗影击", amount=mig_roll))
        if history_entry is not None:
            history_entry.update({"hp_cost": mig_roll})
        participant = conflict["participants"].get(pkey) or {}
        weapon = conflict_mod.weapon_from_sheet(sheet)
        rolls, total, high = self._roll_skill(sheet, participant, entry, rng, weapon=True)
        total += self._attack_bonus(sheet, weapon)
        enemy = conflict["enemies"].get(targets[0]) if targets else None
        if enemy:
            dc = int(enemy.get("defense") or 10) + (2 if enemy.get("defending") else 0)
            if total >= dc:
                damage = high + int((weapon or {}).get("damage") or 1) + level + mig_roll
                multiplier, label = conflict_mod.affinity_multiplier(enemy.get("affinities") or {}, "dark", language_of(instance))
                damage = max(0, int(damage * multiplier)) if multiplier >= 0 else 0
                events.append({"kind": "enemy_damage", "target": targets[0], "amount": damage, "damage_type": "dark", "affinity": label})
                summary.append(_text(
                    instance, "target_hit_dark_damage_affinity" if label else "target_hit_dark_damage",
                    target=enemy['name'], damage=damage, affinity=label,
                ))
                if history_entry is not None:
                    history_entry.setdefault("effects", []).append({"type": "damage", "target": enemy["name"], "amount": damage, "affinity": label, "multiplier": multiplier})
            else:
                summary.append(_text(instance, "defense_miss", target=enemy['name'], dc=dc))
        if history_entry is not None:
            history_entry.update({"rolls": rolls, "total": total, "verdict": "命中" if history_entry.get("effects") else "未命中"})

    def _resolve_spell_skill(self, instance: Any, conflict: dict[str, Any], uid: str, pkey: str,
                             sheet: dict[str, Any], intent: dict[str, Any], entry: dict[str, Any],
                             targets: list[str], rng: Any, events: list[dict[str, Any]],
                             summary: list[str], history_entry: dict[str, Any] | None) -> None:
        element = str(intent.get("element") or "fire")
        boost = int(intent.get("boost") or 0)
        level = self._skill_level(sheet, "元素魔法") or 1
        extra_mp = max(0, min(level * 10, boost))
        base_mp = int(entry.get("mp") or 10) * max(1, len(targets))
        if int(sheet.get("mp") or 0) < base_mp + extra_mp:
            raise ValueError(_text(instance, "mp_short"))
        sheet["mp"] = int(sheet.get("mp") or 0) - base_mp - extra_mp
        instance.set_character_sheet(uid, sheet)
        summary.append(_text(instance, "mp_spent", amount=base_mp + extra_mp))
        participant = conflict["participants"].get(pkey) or {}
        weapon = conflict_mod.weapon_from_sheet(sheet)
        use_weapon = bool(self._skill_level(sheet, "以械引咒")) and weapon and not str(weapon.get("magic") or "").lower() == "true" and len(targets) == 1
        if use_weapon:
            attrs = conflict_mod.attack_attributes({}, weapon)
            conditions = list(participant.get("conditions") or [])
            rolls = conflict_mod.roll_pair(
                conflict_mod.effective_die(sheet, attrs[0], conditions),
                conflict_mod.effective_die(sheet, attrs[1], conditions),
            )
            total = sum(rolls) + self._skill_level(sheet, "以械引咒")
            if "dex" in attrs:
                total += self._skill_level(sheet, "以械引咒")
        else:
            rolls, total, _ = self._roll_skill(sheet, participant, entry, rng)
        high = fu_high_roll(rolls)
        damage = high + 15 + (extra_mp // 10) * 5
        for target_key in targets:
            enemy = conflict["enemies"].get(target_key)
            if not enemy or enemy.get("defeated"):
                continue
            dc = int(enemy.get("mdef") or 10)
            if total < dc:
                summary.append(_text(instance, "magic_defense_miss", target=enemy['name'], dc=dc))
                continue
            multiplier, label = conflict_mod.affinity_multiplier(enemy.get("affinities") or {}, element, language_of(instance))
            dealt = max(0, int(damage * multiplier)) if multiplier >= 0 else 0
            events.append({"kind": "enemy_damage", "target": target_key, "amount": dealt, "damage_type": element, "affinity": label})
            summary.append(_text(
                instance, "target_element_damage_affinity" if label else "target_element_damage",
                target=enemy['name'], damage=dealt, element=element, affinity=label,
            ))
            if history_entry is not None:
                history_entry.setdefault("effects", []).append({"type": "damage", "target": enemy["name"], "amount": dealt, "affinity": label, "multiplier": multiplier})
        if history_entry is not None:
            history_entry.update({"rolls": rolls, "total": total, "element": element, "verdict": "命中" if history_entry.get("effects") else "未命中"})

    def _record_history(self, conflict: dict[str, Any], **entry: Any) -> None:
        """把一次冲突行动写入可回看的战斗记录（主时间线出演用）。"""
        history = conflict.setdefault("history", [])
        entry.setdefault("round", conflict.get("round"))
        entry.setdefault("step", int(conflict.get("step") or 0))
        entry["seq"] = len(history) + 1
        history.append(entry)
        if len(history) > 200:
            del history[:-200]

    def _check_end(self, conflict: dict[str, Any], summary: list[str], language: object = "") -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if all(enemy.get("defeated") for enemy in conflict["enemies"].values()):
            conflict["status"] = "ended"
            events.append({"kind": "conflict_end", "result": "victory"})
            summary.append(render(language, MESSAGES, "conflict_victory"))
        elif all(row.get("down") for row in conflict["participants"].values()):
            conflict["status"] = "ended"
            events.append({"kind": "conflict_end", "result": "defeat"})
            events.append({"kind": "party_defeat"})
            events.append({"kind": "log", "text": render(language, MESSAGES, "conflict_defeat_log")})
            summary.append(render(language, MESSAGES, "conflict_defeat"))
        return events

    def _apply_events(self, instance: Any, state: dict[str, Any], events: list[dict[str, Any]]) -> None:
        conflict = state.get("conflict")
        for event in events:
            kind = event.get("kind")
            if kind == "enemy_damage" and isinstance(conflict, dict):
                enemy = conflict["enemies"].get(str(event.get("target") or ""))
                if enemy:
                    enemy["hp"] = max(0, int(enemy.get("hp") or 0) - int(event.get("amount") or 0))
                    if enemy["hp"] <= 0:
                        enemy["defeated"] = True
            elif kind == "enemy_condition" and isinstance(conflict, dict):
                enemy = conflict["enemies"].get(str(event.get("target") or ""))
                if enemy:
                    condition = str(event.get("condition") or "")
                    if condition and condition not in enemy["conditions"]:
                        enemy["conditions"].append(condition)
            elif kind == "player_damage":
                uid = str(event.get("uid") or "")
                if uid.startswith("player:"):
                    uid = uid.split(":", 1)[1]
                if uid:
                    sheet = instance.get_character_sheet(uid)
                    hp = max(0, int(sheet.get("hp") or 0) - int(event.get("amount") or 0))
                    sheet["hp"] = hp
                    resources = sheet.get("resources")
                    if isinstance(resources, dict) and isinstance(resources.get("hp"), dict):
                        resources["hp"]["current"] = hp
                    instance.set_character_sheet(uid, sheet)
                    if hp <= 0 and isinstance(conflict, dict):
                        row = conflict["participants"].get(f"player:{uid}")
                        if row:
                            row["down"] = True
            elif kind == "party_defeat":
                for key, row in (conflict or {}).get("participants", {}).items():
                    uid = key.split(":", 1)[1]
                    sheet = instance.get_character_sheet(uid)
                    sheet["hp"] = max(1, int(sheet.get("hp") or 0))
                    instance.set_character_sheet(uid, sheet)
                    row["down"] = False
            elif kind == "player_heal":
                uid = str(event.get("uid") or "")
                if uid:
                    sheet = instance.get_character_sheet(uid)
                    max_hp = int(sheet.get("max_hp") or 0)
                    hp = min(max_hp, int(sheet.get("hp") or 0) + int(event.get("amount") or 0))
                    sheet["hp"] = hp
                    instance.set_character_sheet(uid, sheet)
            elif kind == "clock" and isinstance(conflict, dict):
                clock = (conflict.get("clocks") or {}).get(str(event.get("clock_id") or ""))
                if clock:
                    clock["filled"] = min(int(clock.get("segments") or 0), int(clock.get("filled") or 0) + int(event.get("delta") or 0))
            elif kind == "log":
                _append_log(state, str(event.get("text") or ""))

    def _resolve_conflict_end(self, instance: Any) -> dict[str, Any]:
        state = fu_state(instance)
        conflict = state.get("conflict")
        if isinstance(conflict, dict):
            conflict["status"] = "ended"
        return self._batch("conflict.end", [{"kind": "conflict_end", "result": "manual"}], [_text(instance, "conflict_ended")])

    def _resolve_villain(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        state = fu_state(instance)
        villain_id = str(intent.get("villain_id") or "")
        villain = state.get("villains", {}).get(villain_id)
        if not villain:
            raise ValueError(_text(instance, "no_villain"))
        kind = str(intent.get("type"))
        summary: list[str] = []
        if kind == "villain.escape":
            villain["fate"] = max(0, int(villain.get("fate") or 0) - 1)
            summary.append(_text(instance, "villain_escape", villain=villain['name'], fate=villain['fate']))
        elif kind == "villain.surrender":
            villain["surrendered"] = True
            summary.append(_text(instance, "villain_surrender", villain=villain['name']))
        elif kind == "villain.escalate":
            villain["tier"] = "ultimate" if villain.get("tier") == "major" else "major"
            villain["fate"] = VILLAIN_FATE.get(villain["tier"], 10)
            summary.append(_text(instance, "villain_escalate", villain=villain['name'], tier=villain['tier'], fate=villain['fate']))
        return self._batch(kind, [{"kind": "log", "text": summary[-1]}], summary)

    def _resolve_fabula(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        uid = str(intent.get("uid") or intent.get("actor_id") or "").split(":")[-1]
        sheet = instance.get_character_sheet(uid)
        points = int(sheet.get("fabula") or 0)
        if points <= 0:
            raise ValueError(_text(instance, "fabula_short"))
        sheet["fabula"] = points - 1
        resources = sheet.get("resources")
        if isinstance(resources, dict) and isinstance(resources.get("fabula"), dict):
            resources["fabula"]["current"] = sheet["fabula"]
        instance.set_character_sheet(uid, sheet)
        summary = [_text(instance, "fabula_spent", actor=uid, use=intent.get('use') or _text(instance, "fabula_default_use"))]
        return self._batch("fabula.spend", [{"kind": "log", "text": summary[0]}], summary)

    def _resolve_rest(self, instance: Any, rng: Any) -> dict[str, Any]:
        state = fu_state(instance)
        conflict = state.get("conflict")
        if isinstance(conflict, dict) and conflict.get("status") == "active":
            raise ValueError(_text(instance, "rest_in_conflict"))
        summary: list[str] = []
        for uid in self._player_uids(instance):
            sheet = instance.get_character_sheet(uid)
            if uid in getattr(instance, "away_players", set()):
                continue
            if bool(sheet.get("deceased")):
                continue
            if int(sheet.get("hp") or 0) <= 0:
                continue
            sheet["hp"] = int(sheet.get("max_hp") or sheet.get("hp") or 0)
            sheet["mp"] = int(sheet.get("max_mp") or sheet.get("mp") or 0)
            sheet.pop("pending_check_bonus", None)
            sheet.pop("pending_check_bonus_reason", None)
            resources = sheet.get("resources")
            if isinstance(resources, dict):
                if isinstance(resources.get("hp"), dict):
                    resources["hp"]["current"] = sheet["hp"]
                if isinstance(resources.get("mp"), dict):
                    resources["mp"]["current"] = sheet["mp"]
            instance.set_character_sheet(uid, sheet)
            row = (conflict.get("participants") or {}).get(f"player:{uid}") if isinstance(conflict, dict) else None
            if isinstance(row, dict):
                row["conditions"] = []
                row["statuses"] = []
                row["buffs"] = []
            summary.append(_text(instance, "rest_recovered", actor=uid))
        progress = adventure_runtime_state.progress(instance)
        if "n_aftermath" in (progress.get("active_nodes") or []):
            self._complete_adventure_node(instance, "n_aftermath", summary)
        return self._batch("rest.take", [{"kind": "log", "text": _text(instance, "sentence_separator").join(summary)}], summary)

    def _resolve_levelup(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        uid = str(intent.get("uid") or intent.get("actor_id") or "").split(":")[-1]
        klass = str(intent.get("class") or "")
        sheet = instance.get_character_sheet(uid)
        levels = dict(sheet.get("class_levels") or {})
        if klass not in levels:
            raise ValueError(_text(instance, "levelup_unknown_class"))
        if int(levels[klass]) >= 10:
            raise ValueError(_text(instance, "levelup_mastered"))
        levels[klass] = int(levels[klass]) + 1
        sheet["class_levels"] = levels
        sheet["class"] = _class_label(levels)
        sheet["level"] = int(sheet.get("level") or 5) + 1
        sheet["skill_points"] = int(sheet.get("skill_points") or 0) + 1
        instance.set_character_sheet(uid, sheet)
        summary = [_text(instance, "levelup_done", actor=uid, klass=klass, level=levels[klass])]
        return self._batch("levelup.class", [{"kind": "log", "text": summary[0]}], summary)

    # ------------------------------------------------------------------ 视图

    def gameplay_view(
        self, instance: Any, viewer_id: str = "", viewer_is_gm: bool = False,
    ) -> dict[str, Any]:
        state = fu_state(instance)
        conflict = state.get("conflict")
        view: dict[str, Any] = {
            "world": state.get("world"),
            "villains": list(state.get("villains", {}).values()),
            "encounter_catalog": _bestiary_entries(),
            "item_catalog": _item_entries(language_of(instance)),
        }
        adventure = self._adventure_view(instance)
        if adventure:
            view["adventure"] = adventure
        merchant = state.get("merchant")
        if isinstance(merchant, dict) and merchant.get("active"):
            order = [str(uid) for uid in (merchant.get("order") or [])]
            index = int(merchant.get("index") or 0)
            current = order[index] if 0 <= index < len(order) else ""
            view["merchant"] = {
                "active": True,
                "goods": [dict(row) for row in (merchant.get("goods") or []) if isinstance(row, dict)],
                "order": order,
                "index": index,
                "round": int(merchant.get("round") or 1),
                "skipped": list(merchant.get("skipped") or []),
                "current": current,
                "current_name": str((instance.players.get(current) or {}).get("character_name") or current),
                "participants": [
                    {"user_id": uid, "name": str((instance.players.get(uid) or {}).get("character_name") or uid)}
                    for uid in order
                ],
            }
        if isinstance(conflict, dict) and conflict.get("status") == "active":
            if "manual_steps" not in conflict:
                conflict["manual_steps"] = True
                conflict["step"] = 0
                conflict["current"] = ""
            current = conflict_mod.current_actor(conflict)
            actor_uid = current.split(":", 1)[1] if current.startswith("player:") else ""
            actor_name = ""
            actor_skills: list[dict[str, Any]] = []
            actor_row = conflict["participants"].get(current) or {}
            if actor_uid:
                actor_name = str(instance.players.get(actor_uid, {}).get("character_name") or actor_uid)
                try:
                    actor_skills = known_skills(instance.get_character_sheet(actor_uid))
                except Exception:
                    actor_skills = []
            elif current:
                actor_name = str((conflict["enemies"].get(current) or {}).get("name") or current)
            actor_items: list[dict[str, Any]] = []
            if actor_uid:
                for item in instance.get_character_sheet(actor_uid).get("inventory") or []:
                    if isinstance(item, dict):
                        actor_items.append({
                            "name": str(item.get("name") or ""),
                            "type": str(item.get("type") or ""),
                            "damage": item.get("damage"),
                            "armor": item.get("armor"),
                        })
            targets = {
                "enemies": [
                    {"id": eid, "name": row.get("name"), "hp": row.get("hp"), "max_hp": row.get("max_hp")}
                    for eid, row in conflict["enemies"].items() if not row.get("defeated")
                ],
                "allies": [
                    {"id": key, "name": str(instance.players.get(key.split(":", 1)[1], {}).get("character_name") or key),
                     "hp": int(instance.get_character_sheet(key.split(":", 1)[1]).get("hp") or 0),
                     "max_hp": int(instance.get_character_sheet(key.split(":", 1)[1]).get("max_hp") or 0)}
                    for key, row in conflict["participants"].items() if not row.get("down")
                ],
            }
            view["conflict"] = {
                "round": conflict.get("round"),
                "step": int(conflict.get("step") or 0),
                "manual_steps": bool(conflict.get("manual_steps")),
                "can_reroll": bool(state.get("last_conflict_action")),
                "reroll_actor": str((state.get("last_conflict_action") or {}).get("actor") or ""),
                "selectable": [
                    {"id": key, "name": str(instance.players.get(key.split(":", 1)[1], {}).get("character_name") or key), "side": "player"}
                    for key, row in conflict["participants"].items() if not row.get("down")
                ] + [
                    {"id": key, "name": str(row.get("name") or key), "side": "enemy"}
                    for key, row in conflict["enemies"].items() if not row.get("defeated")
                ],
                "order": conflict_mod.order_for(conflict),
                "current": current,
                "current_name": actor_name,
                "actor": {
                    "key": current,
                    "name": actor_name,
                    "control": (seat_control_mode(instance, actor_uid) if actor_uid else "enemy"),
                    "skills": actor_skills,
                    "items": actor_items,
                    "used_action": bool(actor_row.get("used_action")),
                    "used_quick": bool(actor_row.get("used_quick")),
                },
                "targets": targets,
                "participants": conflict["participants"],
                "enemies": conflict["enemies"],
                "clocks": conflict.get("clocks") or {},
                "paused": str(getattr(getattr(instance, "state", ""), "value", getattr(instance, "state", ""))).lower() == "paused",
                "history": list((conflict.get("history") or []))[-100:],
                "actions": [
                    {"id": item["id"], "label": conflict_mod.action_label(language_of(instance), item["id"]), "needs_target": item["needs_target"]}
                    for item in conflict_mod.ACTIONS
                ],
            }
            view["actions"] = [
                {"type": "conflict.action", "id": item["id"], "label": conflict_mod.action_label(language_of(instance), item["id"]), "needs_target": item["needs_target"]}
                for item in conflict_mod.ACTIONS
            ]
        return {"fabula_ultima": view}

    def build_llm_view(self, instance: Any) -> dict[str, Any]:
        base = super().build_llm_view(instance)
        state = fu_state(instance)
        conflict = state.get("conflict")
        extra: dict[str, Any] = {"world": state.get("world")}
        adventure = self._adventure_view(instance)
        if adventure:
            extra["adventure"] = adventure
        if isinstance(conflict, dict) and conflict.get("status") == "active":
            extra["conflict"] = {
                "round": conflict.get("round"),
                "current": conflict_mod.current_actor(conflict),
                "enemies": [
                    {"id": key, "name": row["name"], "hp": row["hp"], "max_hp": row["max_hp"], "defense": row["defense"]}
                    for key, row in conflict["enemies"].items()
                ],
                "clocks": conflict.get("clocks"),
            }
        if isinstance(base, dict):
            base.setdefault("fabula_ultima", extra)
            return base
        return {"fabula_ultima": extra}

    def project_legacy_character(self, character: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(character)

    def game_detail_projection(self, instance: Any) -> dict[str, Any]:
        """把冲突/反派/世界投影进 game detail，供前端读取。"""
        view = self.gameplay_view(instance, viewer_is_gm=True)
        return {"fabula_ultima": view.get("fabula_ultima") or {}}
