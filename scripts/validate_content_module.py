"""Validate a DiceFrame content module directory (LIFE-04, 母方案 §127/§199).

模块作者 / CI 的打包前校验入口：

\`\`\`bash
python scripts/validate_content_module.py my-module
\`\`\`

校验内容（全部复用产品代码路径，不另立第二套规则）：

- PluginHost manifest 校验（schema / plugin_type / profile 组合 / 权限 /
  contributes / adventure_packages / ruleset_catalogs 声明）；
- 冒险包：AdventureBundleLoader 全量校验（graph / locale / digest / 无可执行内容）；
- ruleset catalog：D&D 契约逐条校验（DNDMOD-00/01，fail closed）。

输出 JSON 校验报告（schema/ref/runtime/license 元数据清单）供 CI 判定；
任何 error 以退出码 1 结束。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adventures.bundle import AdventureBundleError, AdventureBundleLoader  # noqa: E402
from src.plugin_host.host import PluginHost  # noqa: E402
from src.rulesets.dnd2024.content.catalog import load_catalog_dir  # noqa: E402


def validate_module(plugin_dir: Path) -> dict[str, Any]:
    """Run every module-level validation; return a structured report."""

    report: dict[str, Any] = {
        "module": str(plugin_dir),
        "ok": True,
        "errors": [],
        "warnings": [],
        "adventures": [],
        "catalogs": [],
    }
    errors = report["errors"]

    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.is_file():
        report["ok"] = False
        errors.append("missing plugin.json")
        return report
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report["ok"] = False
        errors.append(f"plugin.json is not valid JSON: {exc}")
        return report

    # 1) PluginHost manifest 校验（profile 组合 / 权限 / contributes /
    #    adventure_packages / ruleset_catalogs 声明）。
    host = PluginHost(plugins_dir=plugin_dir.parent, data_dir=plugin_dir.parent / "_validation-data")
    try:
        _, runtime = host._load_runtime(plugin_dir)
    except Exception as exc:  # noqa: BLE001 - 校验器必须收集而非抛出
        report["ok"] = False
        errors.append(f"manifest validation failed: {exc}")
        return report

    # 2) 冒险包全量校验（graph / locale / digest）。
    if runtime.adventure_packages_root is not None:
        loader = AdventureBundleLoader(
            runtime.adventure_packages_root,
            allowed_directory_ids=runtime.adventure_package_directories,
        )
        for bundle in loader.list(""):
            report["adventures"].append({
                "adventure_id": bundle.manifest.adventure_id,
                "format": bundle.manifest.format,
                "content_digest": bundle.content_digest,
            })
    else:
        report["warnings"].append("no adventure_packages declared")

    # 3) ruleset catalog 契约校验（D&D）。
    if runtime.ruleset_catalogs_root is not None:
        for directory in runtime.ruleset_catalog_directories:
            try:
                source = load_catalog_dir(
                    runtime.ruleset_catalogs_root / directory,
                    source_label=f"module:{manifest.get('id')}",
                )
            except Exception as exc:  # noqa: BLE001
                report["ok"] = False
                errors.append(f"catalog {directory}: {exc}")
                continue
            report["catalogs"].append({
                "directory": directory,
                "records": len(source.records),
            })
    else:
        report["warnings"].append("no ruleset_catalogs declared")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a DiceFrame content module.")
    parser.add_argument("module_dir", type=Path, help="Content module directory (plugin.json).")
    parser.add_argument("--json", action="store_true", help="Print the raw JSON report.")
    args = parser.parse_args()

    report = validate_module(args.module_dir.resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = "OK" if report["ok"] else "FAILED"
        print(f"[{status}] {report['module']}")
        for error in report["errors"]:
            print(f"  error: {error}")
        for warning in report["warnings"]:
            print(f"  warning: {warning}")
        for adventure in report["adventures"]:
            print(f"  adventure: {adventure['adventure_id']} ({adventure['format']})")
        for catalog in report["catalogs"]:
            print(f"  catalog: {catalog['directory']} ({catalog['records']} records)")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
