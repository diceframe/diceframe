"""规则系统 —— 从 JSON 模板加载，数据驱动所有规则行为。"""

from __future__ import annotations

import ast
import json
import logging
import operator
from collections.abc import Callable
from pathlib import Path

from src.engine.currency import (
    CurrencySpec,
    is_v2_currency_system,
    legacy_currency_spec,
    validate_currency_system,
    validate_declared_currency_system,
)
from src.engine.language import (
    DEFAULT_LANGUAGE,
    content_locale_candidates,
    field_suffixes,
    lang_suffix,
    localized_field,
    localized_text,
    normalize_language,
)
from src.content.rule_locale import materialize_rule

logger = logging.getLogger("trpg")
SUPPORTED_DICE_SYSTEMS = frozenset({"d20", "d100", "none"})

# 安全的数学表达式求值 —— 仅允许数字和基本算术运算
_SAFE_OPS: dict[type, Callable] = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge rule templates. Dicts merge recursively; other values replace."""
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_rule_template(path: Path, seen: set[Path] | None = None) -> dict:
    path = path.resolve()
    seen = seen or set()
    if path in seen:
        chain = " -> ".join(str(p.name) for p in [*seen, path])
        raise ValueError(f"规则模板继承出现循环: {chain}")
    seen.add(path)

    with open(path, encoding="utf-8") as f:
        template = json.load(f)

    parent = template.get("extends")
    if not parent:
        return template

    parent_path = Path(parent)
    if not parent_path.suffix:
        parent_path = parent_path.with_suffix(".json")
    if not parent_path.is_absolute():
        # 优先相对当前规则目录；找不到时回退主程序 templates/rules/，让插件规则
        # 也能继承主程序词库（如 intents_base），实现"按需继承"而非全局强绑。
        candidates = [path.parent / parent_path]
        builtin_rules = Path(__file__).resolve().parents[2] / "templates" / "rules"
        candidates.append(builtin_rules / parent_path.name)
        parent_path = next((c for c in candidates if c.exists()), candidates[0])
    if not parent_path.exists():
        raise FileNotFoundError(f"规则模板基类不存在: {parent_path}")

    parent_template = _resolve_rule_template(parent_path, seen)
    merged = _deep_merge(parent_template, template)
    if "abstract" not in template:
        merged.pop("abstract", None)
    return merged


# 表达式最大嵌套深度：规则模板公式来自 JSON（正常可信），但用户自定义世界模板
# 可塞深层嵌套表达式触发 RecursionError 崩进程，限制深度防 DoS（见执行报告 P2-A）。
_MAX_EXPR_DEPTH = 50


def _safe_eval(expr: str, variables: dict[str, int]) -> int:
    """安全求值数学表达式，仅支持 + - * / // 和变量引用。"""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
        return _eval_node(tree.body, variables)
    except Exception as exc:
        logger.exception("表达式求值失败: %s, vars=%s", expr, variables)
        raise ValueError(f"表达式求值失败: {expr}") from exc


def _eval_node(node: ast.AST, variables: dict[str, int], depth: int = 0) -> int:
    if depth > _MAX_EXPR_DEPTH:
        raise ValueError("表达式嵌套过深")
    if isinstance(node, ast.Constant):
        return int(node.value)
    if isinstance(node, ast.Name):
        return int(variables.get(node.id, 0))
    if isinstance(node, ast.BinOp):
        op_func = _SAFE_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
        left = _eval_node(node.left, variables, depth + 1)
        right = _eval_node(node.right, variables, depth + 1)
        return int(op_func(left, right))
    if isinstance(node, ast.UnaryOp):
        op_func = _SAFE_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
        return int(op_func(_eval_node(node.operand, variables, depth + 1)))
    if isinstance(node, ast.Call):
        func_name = node.func.id if isinstance(node.func, ast.Name) else "unknown"
        func = {"max": max, "min": min, "abs": abs, "int": int}.get(func_name)
        if func is None:
            raise ValueError(f"不支持的函数: {func_name}")
        args = [_eval_node(arg, variables, depth + 1) for arg in node.args]
        return int(func(*args))
    raise ValueError(f"不支持的表达式节点: {type(node).__name__}")


class RuleSystem:
    """单一实现类，行为完全由加载的 JSON 模板数据驱动。

    切换规则 = load() 不同的 JSON 文件。
    """

    def __init__(self, template: dict):
        self.template = template
        self.rule_id: str = template["rule_id"]
        self.rule_name: str = template.get("rule_name", self.rule_id)
        self._dice_system = str(template.get("dice_system") or "d20").lower()
        if self._dice_system not in SUPPORTED_DICE_SYSTEMS:
            supported = ", ".join(sorted(SUPPORTED_DICE_SYSTEMS))
            raise ValueError(f"不支持的检定骰制: {self._dice_system}（当前支持 {supported}）")
        # 显式声明版本的货币 schema（含非法/未支持版本）在构造时 fail-fast；
        # 无版本键的旧规则与显式 legacy（schema_version: 1）继续按 legacy 处理。
        validate_declared_currency_system(template.get("currency_system"))

    def mechanics_snapshot(self) -> str:
        """Return a locale-invariant representation of deterministic mechanics."""
        import copy
        import json
        value = copy.deepcopy(self.template)
        for field in ("rule_name", "name", "description", "attr_hint", "skill_hint", "gm_prompt_appendix", "difficulty_instructions", "currency", "active_locale", "default_locale", "locale_schema_version"):
            value.pop(field, None)
        for item in value.get("attributes", []):
            if isinstance(item, dict):
                item.pop("name", None)
        for item in value.get("classes", []):
            if isinstance(item, dict):
                item.pop("name", None)
                item.pop("description", None)
                item.pop("starter_equipment", None)
                if "starter_equipment_ids" in item:
                    item.pop("starter_equipment", None)
        for item in value.get("items", {}).values():
            if isinstance(item, dict):
                item.pop("name", None)
                item.pop("description", None)
        for item in value.get("special_stats", []):
            if isinstance(item, dict):
                item.pop("name", None)
                item.pop("description", None)
        for item in value.get("skills", {}).values():
            if isinstance(item, dict):
                for field in ("aliases", "name", "description", "label", "hint"):
                    item.pop(field, None)
        value.pop("skill_names", None)
        value.pop("canonical_skill_names", None)
        value.pop("difficulty_instructions", None)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def load(cls, path: str | Path) -> "RuleSystem":
        source = Path(path)
        raw = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("locale_schema_version") and raw.get("target"):
            target = raw.get("target") or {}
            core = source.parents[2] / f"{target.get('id')}.json"
            template = materialize_rule(_resolve_rule_template(core), raw)
        else:
            template = _resolve_rule_template(source)
        logger.info("规则已加载: %s (%s)", template.get("rule_id"), template.get("rule_name"))
        return cls(template)

    @staticmethod
    def path_for(rules_dir: str | Path, rule_id: str, language: str = "") -> Path:
        """构造规则文件路径。language 非空时优先 <rule_id>_<suffix>.json（不存在回退原版）。"""
        base = Path(rules_dir) / f"{rule_id}.json"
        suffix = lang_suffix(language) if language else ""
        if suffix:
            candidates = content_locale_candidates(language)
            for candidate in candidates:
                v2_locale = Path(rules_dir) / "locales" / candidate / f"{rule_id}.json"
                if v2_locale.exists():
                    return v2_locale
            for candidate in candidates:
                localized = Path(rules_dir) / f"{rule_id}_{candidate}.json"
                if localized.exists():
                    return localized
        return base

    # ---- 检定意图（intents）----

    @property
    def intents(self) -> dict:
        """检定意图表：{intent_id: {aliases, skill_candidates, default_attribute, priority}}。

        从规则模板读取（可经 extends 继承），缺失时返回空 dict。
        """
        return self.template.get("intents") or {}

    def intent_aliases(self, intent: str, language: str = "") -> tuple[str, ...]:
        """某意图在指定语言下的触发词。按模板里登记的别名顺序返回。

        优先取该语言的别名键（如 zh-CN / en）；缺失时回退到任意已有的语言键，
        保证中英混写也能命中。找不到返回空元组。
        """
        block = self.intents.get(intent) or {}
        aliases = block.get("aliases") or {}
        lang = normalize_language(language)
        if lang in aliases:
            return tuple(aliases[lang])
        # 回退：任意语言的别名都可用，避免纯中文规则在英文局完全失效。
        for value in aliases.values():
            if value:
                return tuple(value)
        return ()

    def intent_skill_candidates(self, intent: str, language: str = "") -> tuple[str, ...]:
        """某意图的技能候选词，用于按角色卡技能名做子串匹配。"""
        block = self.intents.get(intent) or {}
        candidates = block.get("skill_candidates") or {}
        lang = normalize_language(language)
        if lang in candidates:
            return tuple(candidates[lang])
        for value in candidates.values():
            if value:
                return tuple(value)
        return ()

    def intent_default_attribute(self, intent: str) -> str:
        """某意图的默认属性键；无默认时返回空串。"""
        return str((self.intents.get(intent) or {}).get("default_attribute") or "")

    def intent_applies_to(self, intent: str, dice_system: str) -> bool:
        """某意图是否适用于当前骰制（d20 / d100）。"""
        block = self.intents.get(intent) or {}
        allowed = block.get("applies_to_dice_systems")
        if allowed is None:
            allowed = (self.intents.get("defaults") or {}).get("applies_to_dice_systems")
        if not allowed:
            return True
        return str(dice_system).lower() in allowed

    def intent_match_mode(self, intent: str, language: str) -> str:
        """语言对应的匹配方式：'word'（整词边界，英文）或 'substring'（中文）。"""
        defaults = self.intents.get("defaults") or {}
        if normalize_language(language) == "en":
            return defaults.get("en_match", "word")
        return defaults.get("zh_match", "substring")

    def prefer_longest_match(self) -> bool:
        return bool((self.intents.get("defaults") or {}).get("prefer_longest", True))

    def generic_intent_id(self) -> str:
        """通用检定意图 id；词表里没有则回退 'generic'。"""
        return "generic" if "generic" in self.intents else "generic"

    def find_intent(self, text: str, language: str = "", dice_system: str = "") -> str:
        """扫描词表，返回命中的第一个意图 id（按 priority 升序，即先匹配者胜）。

        返回空串表示没有命中任何意图。
        """
        source = str(text or "")
        lang = normalize_language(language)
        if lang != "en":
            # 中文按去掉空白后的子串匹配（与 checks.py _normalized 一致）。
            haystack = source.replace(" ", "").lower()
            is_word_match = False
        else:
            haystack = source
            is_word_match = True
        entries = [
            (intent, self.intents.get(intent) or {})
            for intent in self.intents
            if intent != "defaults"
        ]
        if not entries:
            return ""
        entries.sort(
            key=lambda item: int(item[1].get("priority", 99)),
        )
        prefer_longest = self.prefer_longest_match()
        best_intent = ""
        best_len = 0
        for intent, block in entries:
            if not block.get("aliases"):
                continue
            if dice_system and not self.intent_applies_to(intent, dice_system):
                continue
            mode = self.intent_match_mode(intent, language)
            matched_len = 0
            for alias in self.intent_aliases(intent, language):
                if not alias:
                    continue
                if is_word_match:
                    import re

                    if re.search(rf"\b{re.escape(alias)}\b", haystack):
                        matched_len = max(matched_len, len(alias))
                elif alias in haystack:
                    matched_len = max(matched_len, len(alias))
            if matched_len > 0:
                if not prefer_longest or matched_len > best_len:
                    best_len = matched_len
                    best_intent = intent
        return best_intent

    # ---- 属性 ----

    @property
    def attributes(self) -> list[dict]:
        """属性列表: [{"key":"str","name":"力量","min":3,"max":18}, ...]"""
        return self.template.get("attributes", [])

    @property
    def attribute_keys(self) -> list[str]:
        return [a["key"] for a in self.attributes]

    @property
    def special_stats(self) -> list[dict]:
        """特殊属性: [{"key":"sanity","name":"理智值","max":99}, ...]"""
        return self.template.get("special_stats", [])

    @property
    def attribute_points(self) -> int:
        return self.template.get("attribute_points", 60)

    @property
    def attr_hint(self) -> str:
        """建卡属性分配说明，用于 WebUI 提示玩家当前规则的数值含义。"""
        return self.template.get("attr_hint", "")

    # ---- 骰子与战斗 ----

    @property
    def dice_system(self) -> str:
        return self._dice_system

    @property
    def check_mechanic(self) -> dict:
        """规则无关的检定元数据，供系统执行器与阶段 1 裁判共用。"""
        declared = self.template.get("check_mechanic")
        if isinstance(declared, dict) and declared:
            return dict(declared)
        if self.dice_system == "d100":
            return {
                "dice": "d100",
                "comparison": "roll_lte_target",
                "critical": {"success": 1, "failure_rule": "coc7e"},
            }
        if self.dice_system == "none":
            return {"dice": "none", "comparison": "none", "critical": {}}
        return {
            "dice": "d20",
            "comparison": "roll_plus_modifier_gte_target",
            "critical": {"success": 20, "failure": 1},
        }

    @property
    def advantage_mechanic(self) -> dict:
        """返回奖惩骰/优势能力声明，并兼容尚未升级的旧规则模板。

        ``type`` 目前支持 ``d20_keep_high_low`` 与
        ``coc_bonus_penalty``；空字符串表示规则不提供该能力。
        ``assistance_grants`` 用来声明多人协助如何影响主检定。
        """
        declared = self.check_mechanic.get("advantage")
        if isinstance(declared, dict):
            kind = str(declared.get("type") or "").strip()
            assistance = str(declared.get("assistance_grants") or "").strip()
            return {
                "type": kind,
                "allow_explicit": bool(declared.get("allow_explicit", bool(kind))),
                "assistance_grants": assistance if assistance in {"advantage", "disadvantage"} else "",
            }

        # 旧插件没有声明 capability 时保持原行为；新模板应显式配置，避免
        # 上层再根据 rule_id/mechanics 猜测规则能力。
        if self.mechanics == "coc7e_core" or bool(self.template.get("bonus_dice")):
            return {
                "type": "coc_bonus_penalty",
                "allow_explicit": True,
                "assistance_grants": "",
            }
        if self.mechanics == "dnd5e_core":
            return {
                "type": "d20_keep_high_low",
                "allow_explicit": True,
                "assistance_grants": "advantage",
            }
        return {"type": "", "allow_explicit": False, "assistance_grants": ""}

    def supports_advantage_mode(self, mode: str) -> bool:
        """规则是否允许结构化请求使用 advantage/disadvantage。"""
        return (
            str(mode or "") in {"advantage", "disadvantage"}
            and self.advantage_mechanic.get("type") in {
                "d20_keep_high_low",
                "coc_bonus_penalty",
            }
        )

    @property
    def damage_mechanic(self) -> dict:
        """伤害结算能力声明；未配置时保持旧版通用 hp_based 行为。

        - ``armor_reduces_damage``：护甲是否在命中后固定减伤（D&D 式为 False，护甲只影响 AC）。
        - ``degree_affects_damage``：攻击总值超出目标是否追加伤害（D&D 式为 False）。
        - ``critical_damage``：``double_total``（旧版总值×2）或
          ``double_damage_dice``（只翻倍伤害骰，固定修正不翻倍）。
        """
        declared = self.template.get("damage_mechanic")
        if not isinstance(declared, dict):
            declared = {}
        critical = str(declared.get("critical_damage") or "double_total").strip()
        return {
            "armor_reduces_damage": bool(declared.get("armor_reduces_damage", True)),
            "degree_affects_damage": bool(declared.get("degree_affects_damage", True)),
            "critical_damage": critical if critical in {"double_total", "double_damage_dice", "none"} else "double_total",
            "minimum_damage": max(0, int(declared.get("minimum_damage", 1) or 0)),
        }

    @property
    def armor_model(self) -> str:
        """护甲解读模型：sum=旧版累加；category_lite=按类别的 D&D 式护甲等级。"""
        model = str(self.template.get("armor_model") or "sum").strip()
        return model if model in {"sum", "category_lite"} else "sum"

    @property
    def death_mechanic(self) -> dict:
        """HP 归零语义：dead=旧版即死；downed_death_saves=D&D 式昏迷+死亡豁免。"""
        declared = self.template.get("death_mechanic")
        hp_zero = str((declared or {}).get("hp_zero") or "dead").strip() if isinstance(declared, dict) else "dead"
        return {"hp_zero": hp_zero if hp_zero in {"dead", "downed_death_saves"} else "dead"}

    @property
    def combat_model(self) -> str:
        return self.template.get("combat_model", "hp_based")

    @property
    def conflict_model(self) -> dict:
        """Generic conflict model. Falls back to legacy combat_model."""
        model = self.template.get("conflict_model")
        if isinstance(model, dict):
            return model
        return {"type": self.combat_model}

    @property
    def growth_system(self) -> str:
        """成长系统：xp_level（D&D式升级）或 skill_improvement（CoC式技能成长）。"""
        return self.template.get("growth_system", "xp_level")

    @property
    def hp_formula(self) -> str:
        return self.template.get("hp_formula", "10 + con * 5")

    @property
    def mechanics(self) -> str:
        return self.template.get("mechanics", "freeform_d20_core")

    @property
    def ruleset_level(self) -> str:
        return self.template.get("ruleset_level", "assisted")

    @property
    def dc_table(self) -> dict[str, int]:
        return self.template.get("dc_table", {"easy": 10, "normal": 15, "hard": 20, "extreme": 25})

    @property
    def max_check_dc(self) -> int:
        """AI 情境 d20 检定上限；规则可显式配置 1..40，默认 20。"""
        try:
            value = int(self.template.get("max_check_dc", 20))
        except (TypeError, ValueError):
            value = 20
        return max(1, min(40, value))

    @property
    def difficulty_dc_modifiers(self) -> dict[str, int]:
        return self.template.get("difficulty_dc_modifiers", {"轻松": -2, "标准": 0, "硬核": 2})

    def dc_for_difficulty(self, difficulty: str, level: str = "normal") -> int:
        base = int(self.dc_table.get(level, self.dc_table.get("normal", 15)))
        return base + int(self.difficulty_dc_modifiers.get(difficulty, 0))

    @staticmethod
    def attribute_modifier(value: int) -> int:
        return (int(value) - 10) // 2

    def proficiency_bonus(self, level: int = 1) -> int:
        expr = self.template.get("proficiency_formula", "max(2, 2 + (level - 1) // 4)")
        return _safe_eval(expr, {"level": int(level)})

    def skill_bonus(self, skill_value: int = 0) -> int:
        table = self.template.get("skill_value_to_bonus", {})
        if not table:
            return 0
        value = int(skill_value or 0)
        best = 0
        for threshold, bonus in table.items():
            if value >= int(threshold):
                best = int(bonus)
        return best

    # ---- 职业与技能 ----

    @property
    def classes(self) -> list[dict]:
        return self.template.get("classes", [])

    @property
    def max_skills(self) -> int:
        return self.template.get("max_skills", 3)

    @property
    def skill_point_total(self) -> int:
        return self.template.get("skill_point_total", 0)

    @property
    def max_skill_value(self) -> int:
        return self.template.get("max_skill_value", 0)

    @property
    def skill_point_spend_mode(self) -> str:
        return self.template.get("skill_point_spend_mode", "total_value")

    @property
    def skill_mode(self) -> str:
        """技能选择模式：narrative / proficiency / point_buy。"""
        return self.template.get("skill_mode", "narrative")

    @property
    def attack_proficiency(self) -> bool:
        """规则是否把普通武器攻击视为熟练攻击。"""
        return bool(self.template.get("attack_proficiency", False))

    @property
    def skill_hint(self) -> str:
        """建卡技能填写说明，用于 WebUI 提示玩家当前规则的技能语义。"""
        return self.template.get("skill_hint", "")

    @property
    def currency(self) -> str:
        return self.template.get("currency", "金币")

    @property
    def currency_spec(self) -> CurrencySpec:
        """规则货币结构的唯一权威（CurrencySpec）。

        V2 ``currency_system`` 严格校验；legacy 规则归一化为 rate=1 的
        兼容 spec，绝不根据货币名称猜测单位。内部新代码统一使用本属性。
        """

        raw = self.template.get("currency_system")
        # __init__ 已对显式声明做过 fail-fast：这里要么是 V2（严格校验），
        # 要么是无版本/显式 legacy（宽松归一化）。
        if is_v2_currency_system(raw):
            return validate_currency_system(raw)
        return legacy_currency_spec(
            self.currency,
            raw if isinstance(raw, dict) else None,
        )

    @property
    def currency_system(self) -> dict:
        """Generic currency schema dict (compatibility projection of :meth:`currency_spec`)."""
        return self.currency_spec.to_dict()

    @property
    def resource_schema(self) -> list[dict]:
        schema = self.template.get("resource_schema")
        if isinstance(schema, list):
            return schema
        resources = [{
            "key": "hp",
            "label": "生命",
            "formula": self.hp_formula,
            "min": 0,
            "zero_behavior": "downed",
        }]
        for stat in self.special_stats:
            key = stat.get("key")
            if key:
                resource = {
                    "key": key,
                    "label": stat.get("name", key),
                    "min": stat.get("min", 0),
                    "max": stat.get("max", 99),
                }
                if stat.get("aliases"):
                    resource["aliases"] = stat["aliases"]
                resources.append(resource)
        return resources

    @property
    def identity_schema(self) -> list[dict]:
        schema = self.template.get("identity_schema")
        if isinstance(schema, list):
            return schema
        return [
            {"key": "origin", "label": "种族", "type": "text", "legacy_field": "race"},
            {"key": "archetype", "label": "职业", "type": "text", "legacy_field": "class"},
            {"key": "background", "label": "背景", "type": "text", "legacy_field": "background"},
        ]

    @property
    def progression_schema(self) -> dict:
        schema = self.template.get("progression_schema")
        if isinstance(schema, dict):
            return schema
        return {"type": self.growth_system}

    @property
    def ui_schema(self) -> dict:
        schema = self.template.get("ui_schema")
        if isinstance(schema, dict):
            return schema
        return {
            "primary_resources": ["hp"],
            "secondary_resources": [s.get("key") for s in self.special_stats if s.get("key")],
            "identity_labels": {
                "origin": "种族",
                "archetype": "职业",
                "background": "背景",
            },
            "show_level": self.growth_system == "xp_level",
            "show_xp": self.growth_system == "xp_level",
            "currency_label": self.currency,
            "equipment_label": "装备",
        }

    @property
    def item_categories(self) -> dict[str, list[str]]:
        return self.template.get("item_categories", {})

    @property
    def skill_base_values(self) -> dict[str, int]:
        """技能基础值: {"侦查": 25, "图书馆使用": 20, ...}"""
        return self.template.get("skill_base_values", {})

    @property
    def skill_pools(self) -> dict[str, list[str]]:
        """Localized profession skill pools for presentation and input matching."""
        raw = self.canonical_skill_pools
        classes = {
            str(item.get("id")): str(item.get("name") or item.get("id"))
            for item in self.classes
            if isinstance(item, dict) and item.get("id")
        }
        skills = self.template.get("skills")
        skill_names = self.template.get("skill_names")
        if not isinstance(skills, dict):
            skills = {}
        if not isinstance(skill_names, dict):
            skill_names = {}

        def display_skill_name(skill_id: object) -> str:
            identity = str(skill_id)
            definition = skills.get(identity)
            if isinstance(definition, dict) and definition.get("name"):
                return str(definition["name"])
            return str(skill_names.get(identity) or identity)

        return {
            classes.get(str(class_id), str(class_id)): [
                display_skill_name(skill_id)
                for skill_id in identities
            ]
            for class_id, identities in raw.items()
        }

    @property
    def canonical_skill_pools(self) -> dict[str, list[str]]:
        """Locale-invariant class ids mapped to canonical skill identities."""
        value = self.template.get("skill_pools", {})
        return value if isinstance(value, dict) else {}

    def get_skill_pool(self, class_name: str) -> list[str]:
        pools = self.skill_pools
        if class_name in pools:
            return pools[class_name]
        class_display = next(
            (
                str(item.get("name") or item.get("id"))
                for item in self.classes
                if isinstance(item, dict) and str(item.get("id") or "") == class_name
            ),
            class_name,
        )
        return pools.get(class_display, [])

    def get_class_names(self) -> list[str]:
        return [c["name"] for c in self.classes]

    # ---- 装备品质 ----

    @property
    def valid_qualities(self) -> list[str]:
        return self.template.get("valid_qualities", ["common"])

    # ---- GM Prompt ----

    def get_gm_prompt_appendix(self, language: str = DEFAULT_LANGUAGE) -> str:
        return str(localized_field(self.template, "gm_prompt_appendix", language) or "")

    # 有专属标签/结算通道的资源不进 STAT 通道
    _STAT_EXCLUSIVE_KEYS = {"sanity", "luck", "mana"}

    def resource_tag_appendix(self, language: str = DEFAULT_LANGUAGE) -> str:
        """GM 提示词附录：列出本规则可用 STAT 标签结算的特殊资源与阈值触发器。"""
        rows: list[str] = []
        for stat in self.special_stats:
            key = str(stat.get("key") or "")
            if not key or key in self._STAT_EXCLUSIVE_KEYS:
                continue
            name = localized_field(stat, "name", language) or stat.get("name") or key
            rows.append(f"{name}({key}, 0-{int(stat.get('max', 99) or 99)})")
        if not rows:
            return ""
        trigger_note = ""
        if any(stat.get("triggers") for stat in self.special_stats):
            trigger_note = localized_text(language, {
                "en": " Thresholds declared by this rule are auto-flagged by the system.",
                "zh-CN": "规则声明的结局阈值由系统自动提醒，命中后按提示推进。",
                "ja": "ルールが宣言した閾値はシステムが自動で通知します。",
                "de": " Von dieser Regel deklarierte Schwellenwerte werden vom System automatisch markiert.",
            })
        return localized_text(language, {
            "en": "Rule resources (settle with STAT:playerID:resourceKey:delta; "
                  f"do NOT use STAT for HP/Gold/Mana/Sanity/Luck): {', '.join(rows)}.{trigger_note}",
            "zh-CN": "本规则特殊资源（用 STAT:玩家ID:资源key:变化量 结算增减；"
                     f"HP/金币/法力/理智/幸运请用各自专属标签，不要走 STAT）：{'、'.join(rows)}。{trigger_note}",
            "ja": "本ルールの特殊リソース（STAT:プレイヤーID:リソースkey:増減 で処理；"
                  f"HP/通貨/マナ/正気度/幸運は専用タグを使用）：{'、'.join(rows)}。{trigger_note}",
            "de": "Regelspezifische Ressourcen (abrechnen mit STAT:SpielerID:RessourcenKey:Delta; "
                  f"verwende STAT NICHT für HP/Gold/Mana/Stabilität/Glück): {', '.join(rows)}.{trigger_note}",
        })

    def get_difficulty_instructions(self, difficulty: str, language: str = DEFAULT_LANGUAGE) -> str:
        di = localized_field(self.template, "difficulty_instructions", language)
        if isinstance(di, dict):
            return di.get(difficulty, "")
        return ""

    # ---- HP 计算 ----

    def calculate_hp(self, attributes: dict[str, int], class_name: str = "") -> int:
        """根据 hp_formula 安全求值计算 HP。注入 con_mod 和 class_hp_die 供 dnd5e 等公式使用。"""
        variables = dict(attributes)
        con = attributes.get("con", 10)
        variables["con_mod"] = (con - 10) // 2
        variables["class_hp_die"] = 8  # 默认 d8，职业名匹配到时覆盖
        if class_name:
            for c in self.classes:
                if c.get("name") == class_name:
                    variables["class_hp_die"] = c.get("hp_die", 8)
                    break
        try:
            result = _safe_eval(self.hp_formula, variables)
            return max(1, result)
        except Exception as exc:
            logger.exception("HP 公式计算失败: %s, attrs=%s", self.hp_formula, attributes)
            raise ValueError(f"HP 公式计算失败: {self.hp_formula}") from exc

    # ---- 校验 ----

    def validate_character(self, character_sheet: dict) -> list[str]:
        """校验角色卡，返回错误列表（空列表 = 通过）。"""
        errors: list[str] = []
        attrs = character_sheet.get("attributes", {})

        # 属性点总和
        total = sum(attrs.get(a["key"], 0) for a in self.attributes)
        if total > self.attribute_points:
            errors.append(f"属性点总和 {total}/{self.attribute_points}，超出上限")
        for a in self.attributes:
            val = attrs.get(a["key"], 0)
            if val < a.get("min", 3) or val > a.get("max", 18):
                errors.append(f"{a['name']} {val} 不在 [{a['min']},{a['max']}] 范围内")

        # 技能数量
        skills = character_sheet.get("skills", [])
        if len(skills) > self.max_skills:
            errors.append(f"技能数量 {len(skills)}/{self.max_skills}，超出上限")
        skill_spent = 0
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            name = skill.get("name", "")
            value = int(skill.get("value", 0) or 0)
            if self.max_skill_value and value > self.max_skill_value:
                errors.append(f"技能 {name} {value}/{self.max_skill_value}，超出单技能上限")
            if self.skill_point_spend_mode == "above_base":
                skill_spent += max(0, value - self._skill_base_value(name))
            else:
                skill_spent += value
        if self.skill_point_total and skill_spent > self.skill_point_total:
            errors.append(f"技能点 {skill_spent}/{self.skill_point_total}，超出上限")

        # 装备品质
        for eq in character_sheet.get("equipment", []):
            if eq.get("quality", "common") not in self.valid_qualities:
                errors.append(f"装备 {eq.get('name','?')} 品质 '{eq.get('quality')}' 不在允许范围")

        # 职业合法性：不匹配时放行自定义职业，仅记录 warning
        class_name = str(character_sheet.get("class", "")).strip()
        valid_classes = self.get_class_names()
        if valid_classes and class_name and class_name not in valid_classes:
            logger.warning("职业 %r 不在规则建议列表 %s 内，放行自定义职业", class_name, valid_classes)
        return errors

    def _skill_base_value(self, name: str) -> int:
        """技能基础值：优先 skill_base_values；否则若属于某职业技能池按 0（池内无基础值），
        自定义技能按房规兜底 5，防 above_base 模式下凭空全算超基导致超模建卡（P2-E）。"""
        if name in self.skill_base_values:
            return int(self.skill_base_values[name])
        skills = self.template.get("skills") or {}
        canonical_names = self.template.get("canonical_skill_names") or {}
        for identity, definition in skills.items():
            display_name = definition.get("name") if isinstance(definition, dict) else ""
            if name not in {str(identity), str(display_name)}:
                continue
            canonical_name = str(canonical_names.get(identity) or display_name or identity)
            if canonical_name in self.skill_base_values:
                return int(self.skill_base_values[canonical_name])
        in_pool = any(name in pool for pool in self.skill_pools.values())
        return 5 if not in_pool else 0


    @classmethod
    def load_for_world(
        cls,
        world_data: dict,
        rules_dir: Path,
        language: str = "",
    ) -> "RuleSystem | None":
        """从世界模板数据加载关联的规则系统。

        Args:
            world_data: 世界模板 JSON 数据（含 default_rule 字段）
            rules_dir: 规则模板目录

        Returns:
            RuleSystem 或 None（未找到规则文件时）
        """
        plugin_rule_data = world_data.get("_diceframe_rule_data")
        if isinstance(plugin_rule_data, dict):
            return cls(plugin_rule_data)
        plugin_rule_path = world_data.get("_diceframe_rule_path")
        if plugin_rule_path:
            path = Path(plugin_rule_path)
            if path.exists():
                return cls.load(path)
        rule_id = world_data.get("default_rule", "freeform_fantasy")
        active_language = language or world_data.get("active_locale") or world_data.get("language", DEFAULT_LANGUAGE)
        rule_path = cls.path_for(rules_dir, rule_id, active_language)
        if rule_path.exists():
            return cls.load(rule_path)
        return None

    @classmethod
    def load_for_world_path(cls, world_path: Path, rules_dir: Path) -> "RuleSystem | None":
        """从世界模板文件路径加载关联的规则系统。"""
        if not world_path.exists():
            return None
        try:
            world_data = json.loads(world_path.read_text(encoding="utf-8"))
            return cls.load_for_world(world_data, rules_dir)
        except Exception:
            logger.warning("世界模板读取失败: %s", world_path)
            return None


def list_available_rules(rules_dir: str | Path) -> list[dict]:
    """扫描 templates/rules/ 目录，返回所有可用规则模板摘要。"""
    rules_dir = Path(rules_dir)
    if not rules_dir.is_dir():
        return []
    suffixes = sorted(field_suffixes())
    skip_suffixes = {f"_{s}.json" for s in suffixes}
    result = []
    for f in rules_dir.glob("*.json"):
        if any(f.name.endswith(s) for s in skip_suffixes):
            continue
        try:
            template = _resolve_rule_template(f)
            if template.get("abstract", False):
                continue
            rule_id = template.get("rule_id", f.stem)
            for s in suffixes:
                loc_path = rules_dir / f"{rule_id}_{s}.json"
                if loc_path.exists():
                    try:
                        loc = _resolve_rule_template(loc_path)
                        template[f"rule_name_{s}"] = loc.get("rule_name", "")
                        template[f"description_{s}"] = loc.get("description", "")
                    except Exception as exc:
                        raise ValueError(f"规则语言模板读取失败: {loc_path}: {exc}") from exc
            result.append({
                "rule_id": rule_id,
                "rule_name": template.get("rule_name", f.stem),
                "rule_name_en": template.get("rule_name_en", ""),
                "description": template.get("description", ""),
                "description_en": template.get("description_en", ""),
                "dice_system": template.get("dice_system", "d20"),
                "combat_model": template.get("combat_model", "hp_based"),
                "attr_count": len(template.get("attributes", [])),
                "custom": bool(template.get("custom", False)),
                "source_rule_id": template.get("source_rule_id", ""),
                "scene_image": template.get("scene_image"),
                "file": str(f),
            })
        except Exception as exc:
            raise ValueError(f"规则模板读取失败: {f}: {exc}") from exc
    return result
