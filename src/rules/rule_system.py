"""规则系统 —— 从 JSON 模板加载，数据驱动所有规则行为。"""

from __future__ import annotations

import ast
import json
import logging
import operator
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("trpg")

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
        parent_path = path.parent / parent_path
    if not parent_path.exists():
        raise FileNotFoundError(f"规则模板基类不存在: {parent_path}")

    parent_template = _resolve_rule_template(parent_path, seen)
    merged = _deep_merge(parent_template, template)
    if "abstract" not in template:
        merged.pop("abstract", None)
    return merged


def _safe_eval(expr: str, variables: dict[str, int]) -> int:
    """安全求值数学表达式，仅支持 + - * / // 和变量引用。"""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
        return _eval_node(tree.body, variables)
    except Exception as exc:
        logger.exception("表达式求值失败: %s, vars=%s", expr, variables)
        raise ValueError(f"表达式求值失败: {expr}") from exc


def _eval_node(node: ast.AST, variables: dict[str, int]) -> int:
    if isinstance(node, ast.Constant):
        return int(node.value)
    if isinstance(node, ast.Name):
        return int(variables.get(node.id, 0))
    if isinstance(node, ast.BinOp):
        op_func = _SAFE_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        return int(op_func(left, right))
    if isinstance(node, ast.UnaryOp):
        op_func = _SAFE_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
        return int(op_func(_eval_node(node.operand, variables)))
    if isinstance(node, ast.Call):
        func_name = node.func.id if isinstance(node.func, ast.Name) else "unknown"
        func = {"max": max, "min": min, "abs": abs, "int": int}.get(func_name)
        if func is None:
            raise ValueError(f"不支持的函数: {func_name}")
        args = [_eval_node(arg, variables) for arg in node.args]
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

    @classmethod
    def load(cls, path: str | Path) -> "RuleSystem":
        template = _resolve_rule_template(Path(path))
        logger.info("规则已加载: %s (%s)", template.get("rule_id"), template.get("rule_name"))
        return cls(template)

    @staticmethod
    def path_for(rules_dir: str | Path, rule_id: str) -> Path:
        """构造规则文件路径，统一所有调用点。"""
        return Path(rules_dir) / f"{rule_id}.json"

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
        return self.template.get("dice_system", "d20")

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
    def skill_hint(self) -> str:
        """建卡技能填写说明，用于 WebUI 提示玩家当前规则的技能语义。"""
        return self.template.get("skill_hint", "")

    @property
    def currency(self) -> str:
        return self.template.get("currency", "金币")

    @property
    def currency_system(self) -> dict:
        """Generic currency schema, with legacy currency label compatibility."""
        system = self.template.get("currency_system")
        if isinstance(system, dict):
            return system
        label = self.currency
        return {
            "base_unit": "unit",
            "units": [{"id": "unit", "name": label, "rate": 1}],
        }

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
                resources.append({
                    "key": key,
                    "label": stat.get("name", key),
                    "min": stat.get("min", 0),
                    "max": stat.get("max", 99),
                })
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
        """职业技能池: {"战士": ["基础攻击", ...], ...}"""
        return self.template.get("skill_pools", {})

    def get_skill_pool(self, class_name: str) -> list[str]:
        return self.skill_pools.get(class_name, [])

    def get_class_names(self) -> list[str]:
        return [c["name"] for c in self.classes]

    # ---- 装备品质 ----

    @property
    def valid_qualities(self) -> list[str]:
        return self.template.get("valid_qualities", ["common"])

    # ---- GM Prompt ----

    def get_gm_prompt_appendix(self) -> str:
        return self.template.get("gm_prompt_appendix", "")

    def get_difficulty_instructions(self, difficulty: str) -> str:
        di = self.template.get("difficulty_instructions", {})
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
                skill_spent += max(0, value - int(self.skill_base_values.get(name, 0)))
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


    @classmethod
    def load_for_world(cls, world_data: dict, rules_dir: Path) -> "RuleSystem | None":
        """从世界模板数据加载关联的规则系统。

        Args:
            world_data: 世界模板 JSON 数据（含 default_rule 字段）
            rules_dir: 规则模板目录

        Returns:
            RuleSystem 或 None（未找到规则文件时）
        """
        rule_id = world_data.get("default_rule", "freeform_fantasy")
        rule_path = rules_dir / f"{rule_id}.json"
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
    result = []
    for f in rules_dir.glob("*.json"):
        try:
            template = _resolve_rule_template(f)
            if template.get("abstract", False):
                continue
            result.append({
                "rule_id": template.get("rule_id", f.stem),
                "rule_name": template.get("rule_name", f.stem),
                "description": template.get("description", ""),
                "dice_system": template.get("dice_system", "d20"),
                "combat_model": template.get("combat_model", "hp_based"),
                "attr_count": len(template.get("attributes", [])),
                "custom": bool(template.get("custom", False)),
                "file": str(f),
            })
        except Exception:
            logger.warning("规则模板读取失败: %s", f)
    return result
