"""插件贡献的规则，带 language 的详情请求必须从包自己的目录加载。

回归点：``get_rule_template`` 对 V2 规则走 bundle loader 时曾经硬编码
``dependencies.rules_dir``。内置规则住在那儿，插件规则不住——于是
``GET /api/rules/<plugin-rule>?language=zh-CN`` 去 ``data/rules`` 找一个
不存在的同名文件，直接 500。列表接口走的是插件路径，所以只有详情会炸。
"""

from __future__ import annotations

import json

import pytest

from src.plugin_host import PluginHost
from src.webui.services import rules as rule_service
from tests.plugin_host_common import write_plugin


def _v2_rule(rule_name: str) -> dict:
    return {
        "rule_id": "pack_rule",
        "rule_name": rule_name,
        "description": "From plugin",
        "rule_schema_version": 2,
        "dice_system": "d20",
        "combat_model": "hp_based",
        "attributes": [],
    }


@pytest.mark.asyncio
async def test_plugin_rule_detail_loads_its_own_locale_bundle(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "starter-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"contributes": {"rules": ["content/rules/*.json"]}},
    )
    rule_dir = plugins / "starter-pack" / "content" / "rules"
    locale_dir = rule_dir / "locales" / "zh-CN"
    locale_dir.mkdir(parents=True)
    (rule_dir / "pack_rule.json").write_text(
        json.dumps(_v2_rule("Pack Rule"), ensure_ascii=False), encoding="utf-8",
    )
    (locale_dir / "pack_rule.json").write_text(
        json.dumps(_v2_rule("包规则"), ensure_ascii=False), encoding="utf-8",
    )

    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    await host.update_config("starter-pack", {"enabled": True})

    # 内置规则目录故意留空：插件规则不能依赖它。
    builtin_rules = tmp_path / "data" / "rules"
    builtin_rules.mkdir(parents=True, exist_ok=True)
    dependencies = rule_service.RuleDependencies(rules_dir=builtin_rules, plugin_host=host)

    detail = rule_service.get_rule_template(dependencies, "pack_rule", "zh-CN")

    assert detail["ok"] is True
    assert detail["rule"]["rule_name"] == "包规则"
    assert detail["rule"]["plugin_id"] == "starter-pack"
