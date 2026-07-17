"""规则配置服务：规则列表 / 自定义规则 CRUD。"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from src.rules.rule_system import RuleSystem
from src.engine.language import field_suffixes

if TYPE_CHECKING:
    from src.webui.api import WebAPI

logger = logging.getLogger("trpg")

_RULE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

def is_valid_rule_id(rule_id: str) -> bool:
    return bool(_RULE_ID_RE.fullmatch((rule_id or "").strip()))
_ATTR_NAME_EN = {
    "str": "STR", "con": "CON", "dex": "DEX", "int": "INT",
    "edu": "EDU", "app": "APP", "pow": "POW", "siz": "SIZ",
    "wis": "WIS", "cha": "CHA",
}


def _enrich_attributes(attributes: list[dict]) -> list[dict]:
    enriched = []
    for attr in attributes or []:
        item = dict(attr)
        key = item.get("key", "")
        name = item.get("name", key)
        name_en = item.get("name_en") or _ATTR_NAME_EN.get(key, key.upper())
        item["name_en"] = name_en
        item["display_name"] = f"{name} ({name_en})" if name_en else name
        enriched.append(item)
    return enriched


_LOCALIZED_TOP_KEYS = ("rule_name", "description", "attr_hint", "skill_hint",
                       "gm_prompt_appendix", "difficulty_instructions", "skill_pools",
                       "item_categories", "currency", "skill_base_values")


def _merge_localized_fields(template: dict, loc: dict, suffix: str) -> None:
    """把 loc（语言版全文）的字段合并进 template 作为 _<suffix> 后缀字段。"""
    for k in _LOCALIZED_TOP_KEYS:
        if k in loc:
            template[f"{k}_{suffix}"] = loc[k]
    for key in ("attributes", "classes", "special_stats"):
        zh_list = template.get(key, [])
        loc_list = loc.get(key, [])
        for i, zh_item in enumerate(zh_list):
            if i < len(loc_list):
                loc_item = loc_list[i]
                for field in ("name", "description"):
                    if field in loc_item:
                        zh_item[f"{field}_{suffix}"] = loc_item[field]


def list_rules(api: "WebAPI") -> dict[str, Any]:
    from src.rules.rule_system import list_available_rules
    rules = list_available_rules(api._rules_dir)
    seen = {str(rule.get("rule_id") or "") for rule in rules}
    for item in _plugin_rule_items(api):
        rule_id = str(item.get("rule_id") or "")
        if rule_id and rule_id not in seen:
            rules.append(item)
            seen.add(rule_id)
    return {"rules": rules, "total": len(rules)}


def save_custom_rule(api: "WebAPI", data: dict[str, Any]) -> dict[str, Any]:
    source_rule_id = (data.get("source_rule_id") or "").strip()
    rule_id = (data.get("rule_id") or "").strip()
    rule_name = (data.get("rule_name") or "").strip()
    description = (data.get("description") or "").strip()

    if not source_rule_id:
        return {"ok": False, "error": "请选择要复制的基础规则"}
    if not rule_id:
        return {"ok": False, "error": "请输入规则 ID"}
    if not _RULE_ID_RE.fullmatch(rule_id):
        return {"ok": False, "error": "规则 ID 只能包含英文、数字、下划线或短横线"}
    if not rule_name:
        return {"ok": False, "error": "请输入规则名称"}

    source_path = _resolve_rule_path(api, source_rule_id)
    target_path = api._rules_dir / f"{rule_id}.json"
    if not source_path or not source_path.exists():
        return {"ok": False, "error": f"基础规则不存在: {source_rule_id}"}
    if target_path.exists():
        return {"ok": False, "error": f"规则 ID 已存在: {rule_id}"}

    template = json.loads(source_path.read_text(encoding="utf-8"))
    template["rule_id"] = rule_id
    template["rule_name"] = rule_name
    template["rule_name_en"] = data.get("rule_name_en") or rule_id
    template["description"] = description or template.get("description", "")
    template["custom"] = True
    template["source_rule_id"] = source_rule_id
    template["rule_version"] = str(data.get("rule_version") or template.get("rule_version", "1.1"))

    api._rules_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(target_path)
    logger.info("自定义规则已保存: %s (from=%s)", rule_id, source_rule_id)
    return {"ok": True, "rule": {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "description": template.get("description", ""),
        "dice_system": template.get("dice_system", "d20"),
        "combat_model": template.get("combat_model", "hp_based"),
        "custom": True,
    }}


def get_rule_template(api: "WebAPI", rule_id: str) -> dict[str, Any]:
    rule_id = (rule_id or "").strip()
    if not _RULE_ID_RE.fullmatch(rule_id):
        return {"ok": False, "error": "规则 ID 不合法"}
    rule_path = _resolve_rule_path(api, rule_id)
    if not rule_path or not rule_path.exists():
        return {"ok": False, "error": f"规则不存在: {rule_id}"}
    template = json.loads(rule_path.read_text(encoding="utf-8"))
    rule = RuleSystem.load(rule_path)
    template.setdefault("conflict_model", rule.conflict_model)
    template.setdefault("currency_system", rule.currency_system)
    template.setdefault("resource_schema", rule.resource_schema)
    template.setdefault("identity_schema", rule.identity_schema)
    template.setdefault("progression_schema", rule.progression_schema)
    template.setdefault("ui_schema", rule.ui_schema)
    for s in sorted(field_suffixes()):
        loc_path = rule_path.parent / f"{rule_path.stem}_{s}.json"
        if loc_path.exists():
            try:
                loc = json.loads(loc_path.read_text(encoding="utf-8"))
                _merge_localized_fields(template, loc, s)
            except Exception:
                logger.warning("规则语言模板合并失败: %s", loc_path)
    template["attributes"] = _enrich_attributes(template.get("attributes", []))
    if _plugin_rule_path(api, rule_id):
        template["plugin_id"] = _plugin_rule_plugin_id(api, rule_id)
        template["readonly"] = True
    return {"ok": True, "rule": template}


def update_custom_rule(api: "WebAPI", rule_id: str, template: dict[str, Any]) -> dict[str, Any]:
    rule_id = (rule_id or "").strip()
    if not _RULE_ID_RE.fullmatch(rule_id):
        return {"ok": False, "error": "规则 ID 不合法"}
    if not isinstance(template, dict):
        return {"ok": False, "error": "规则内容必须是 JSON 对象"}
    rule_path = RuleSystem.path_for(api._rules_dir, rule_id)
    if not rule_path.exists():
        return {"ok": False, "error": f"规则不存在: {rule_id}"}

    old_template = json.loads(rule_path.read_text(encoding="utf-8"))
    if not old_template.get("custom"):
        return {"ok": False, "error": "内置规则不可在 WebUI 中直接编辑，请先复制为自定义规则"}

    rule_name = (template.get("rule_name") or "").strip()
    if not rule_name:
        return {"ok": False, "error": "规则名称不能为空"}
    if not isinstance(template.get("attributes", []), list):
        return {"ok": False, "error": "attributes 必须是数组"}

    template["rule_id"] = rule_id
    template["custom"] = True
    template["source_rule_id"] = template.get("source_rule_id") or old_template.get("source_rule_id", "")
    tmp_path = rule_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(rule_path)
    logger.info("自定义规则已更新: %s", rule_id)
    return {"ok": True, "rule": {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "description": template.get("description", ""),
        "dice_system": template.get("dice_system", "d20"),
        "combat_model": template.get("combat_model", "hp_based"),
        "custom": True,
    }}


def delete_custom_rule(api: "WebAPI", rule_id: str) -> dict[str, Any]:
    rule_id = (rule_id or "").strip()
    if not _RULE_ID_RE.fullmatch(rule_id):
        return {"ok": False, "error": "规则 ID 不合法"}
    rule_path = RuleSystem.path_for(api._rules_dir, rule_id)
    if not rule_path.exists():
        return {"ok": False, "error": f"规则不存在: {rule_id}"}
    template = json.loads(rule_path.read_text(encoding="utf-8"))
    if not template.get("custom"):
        return {"ok": False, "error": "内置规则不可删除"}
    rule_path.unlink()
    logger.info("自定义规则已删除: %s", rule_id)
    return {"ok": True, "rule_id": rule_id}


def _resolve_rule_path(api: "WebAPI", rule_id: str):
    rule_path = RuleSystem.path_for(api._rules_dir, rule_id)
    if rule_path.exists():
        return rule_path
    return _plugin_rule_path(api, rule_id)


def _plugin_rule_path(api: "WebAPI", rule_id: str):
    plugin_host = getattr(api, "_plugins", None)
    if not plugin_host:
        return None
    return plugin_host.contribution_path("rule", rule_id)


def _plugin_rule_plugin_id(api: "WebAPI", rule_id: str) -> str:
    plugin_host = getattr(api, "_plugins", None)
    if not plugin_host:
        return ""
    item = plugin_host.contributions.find("rule", rule_id)
    return item.plugin_id if item else ""


def _plugin_rule_items(api: "WebAPI") -> list[dict[str, Any]]:
    plugin_host = getattr(api, "_plugins", None)
    if not plugin_host:
        return []
    result = []
    for item in plugin_host.contributions.list("rule"):
        try:
            rule = RuleSystem.load(item.path)
            result.append({
                "rule_id": rule.rule_id,
                "rule_name": rule.rule_name,
                "rule_name_en": rule.template.get("rule_name_en", ""),
                "description": rule.template.get("description", ""),
                "description_en": rule.template.get("description_en", ""),
                "dice_system": rule.dice_system,
                "combat_model": rule.combat_model,
                "attr_count": len(rule.attributes),
                "custom": False,
                "plugin_id": item.plugin_id,
                "plugin_name": item.plugin_name,
                "readonly": True,
                "file": str(item.path),
            })
        except Exception:
            logger.warning("插件规则模板读取失败: %s", item.path, exc_info=True)
    return result
