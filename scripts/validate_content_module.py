"""Validate a DiceFrame content module directory or package (LIFE-04, 母方案 §127/§199).

模块作者 / CI 的打包前校验入口：

```bash
python scripts/validate_content_module.py my-module
python scripts/validate_content_module.py my-module-1.0.0.dfplugin --json
```

校验内容（全部复用产品代码路径，不另立第二套规则）：

- 已打包的 `.dfplugin` / `.zip`：按安装时同一套规则解压（大小、文件数、
  路径越界、符号链接、加密条目、顶层目录名与插件 ID 一致）；
- PluginHost manifest 校验（schema / plugin_type / profile 组合 / 权限 /
  contributes / adventure_packages / ruleset_catalogs 声明）；
- 冒险包：AdventureBundleLoader 全量校验（graph / locale / digest / 无可执行内容）；
- ruleset catalog：D&D 契约逐条校验（DNDMOD-00/01，fail closed）。

输出 JSON 校验报告（schema/ref/runtime/license 元数据清单）供 CI 判定；
任何 error 以退出码 1 结束。校验包文件时可用 ``--extract-to`` 把校验通过的
模组目录复制出来（例如投稿托管时推送到仓库）。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.plugin_host.host import PluginHost  # noqa: E402
from src.rulesets.builtin import (  # noqa: E402
    build_default_ruleset_registry,
    default_module_runtime_requirement,
)


def _validation_dependencies() -> Any:
    """Same runtime availability the server judges installs against."""

    from src.webui.services.module_validation import ModuleValidationDependencies

    return ModuleValidationDependencies(
        ruleset_registry=build_default_ruleset_registry(),
        default_runtime_requirement=default_module_runtime_requirement,
    )


def _report_blockers(blockers: tuple[str, ...]) -> list[str]:
    """Map shared-validator blockers onto the CLI report's legacy phrasing."""

    errors: list[str] = []
    for blocker in blockers:
        if blocker.startswith("ruleset_catalog_invalid:"):
            _, directory, detail = blocker.split(":", 2)
            errors.append(f"catalog {directory}: {detail}")
        else:
            errors.append(blocker)
    return errors


MAX_COVER_BYTES = 512 * 1024
_COVER_SUFFIXES = (".webp", ".png", ".jpg", ".jpeg")


def _check_cover(plugin_dir: Path, manifest: dict[str, Any], report: dict[str, Any]) -> None:
    """封面可选；声明了就必须是包内 assets/ 下的图片，并满足体积与尺寸。

    发布侧用 Pillow 解码，导出接口只做字节校验——这里才是能承担解码的地方。
    """

    raw = manifest.get("cover")
    if raw is None:
        return
    relative = str(raw or "").strip().replace("\\", "/")
    if not relative.startswith("assets/") or Path(relative).suffix.lower() not in _COVER_SUFFIXES:
        report["ok"] = False
        report["errors"].append("cover must point to assets/<name>.webp|png|jpg")
        return
    root = plugin_dir.resolve()
    target = (root / relative).resolve()
    if root not in target.parents:
        report["ok"] = False
        report["errors"].append("cover path escapes the package")
        return
    if not target.is_file() or target.is_symlink():
        report["ok"] = False
        report["errors"].append(f"cover file is missing: {relative}")
        return
    size = target.stat().st_size
    if size > MAX_COVER_BYTES:
        report["ok"] = False
        report["errors"].append(f"cover is {size} bytes; keep it under {MAX_COVER_BYTES}")
        return
    try:
        from PIL import Image

        with Image.open(target) as image:
            width, height = image.size
    except Exception as exc:  # noqa: BLE001 - unreadable image is a validation result
        report["ok"] = False
        report["errors"].append(f"cover is not a readable image: {exc}")
        return
    if width > 4096 or height > 4096 or min(width, height) < 360:
        report["ok"] = False
        report["errors"].append(
            f"cover is {width}x{height}; expected at least 360px on the short side and at most 4096px"
        )
        return
    if abs(width / height - 16 / 9) > 0.06:
        report["warnings"].append(f"cover is {width}x{height}; 16:9 (e.g. 1280x720) fits the cards best")


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
    try:
        # Host construction initializes marketplace state. Validation must not
        # create runtime data beside an author's sources, even on rejection.
        with tempfile.TemporaryDirectory(prefix="diceframe-content-validation-") as scratch:
            host = PluginHost(plugins_dir=plugin_dir.parent, data_dir=Path(scratch))
            _, runtime = host._load_runtime(plugin_dir)
    except Exception as exc:  # noqa: BLE001 - 校验器必须收集而非抛出
        report["ok"] = False
        errors.append(f"manifest validation failed: {exc}")
        return report

    _check_cover(plugin_dir, dict(runtime.manifest), report)

    # 2/3) 深度校验走与安装/预览**完全同一套** rules（FIX-01 §3.3），
    #      不再在这里维护第二份 Adventure / catalog 校验逻辑。
    from src.webui.services.module_validation import validate_module_package

    validation = validate_module_package(
        dict(runtime.manifest),
        deps=_validation_dependencies(),
        directory=plugin_dir,
        require_content_pack=True,
    )
    if validation.blockers:
        report["ok"] = False
        errors.extend(_report_blockers(validation.blockers))
    report["adventures"] = [dict(item) for item in validation.adventures]
    report["catalogs"] = [
        {"directory": item["directory"], "records": item["records"]}
        for item in validation.catalogs
    ]
    report["warnings"].extend(validation.warnings)

    if runtime.adventure_packages_root is None:
        report["warnings"].append("no adventure_packages declared")
    if runtime.ruleset_catalogs_root is None:
        report["warnings"].append("no ruleset_catalogs declared")

    return report


_LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt")
_README_FILES = ("README_CN.md", "README.md")


def _manifest_at_archive_root(payload: bytes) -> bool:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return any(name.replace("\\", "/") == "plugin.json" for name in archive.namelist())


def _package_summary(module_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    docs = str(manifest.get("docs") or "").strip()
    readme_candidates = (docs,) if docs else _README_FILES
    return {
        "plugin_id": str(manifest.get("id") or ""),
        "name": str(manifest.get("name") or ""),
        "version": str(manifest.get("version") or ""),
        "plugin_type": str(manifest.get("plugin_type") or ""),
        "has_license": any((module_dir / name).is_file() for name in _LICENSE_FILES),
        "has_readme": any((module_dir / name).is_file() for name in readme_candidates),
    }


def validate_package_archive(archive_path: Path, *, extract_to: Path | None = None) -> dict[str, Any]:
    """Validate a packaged module (``.dfplugin`` / ``.zip``) the way an install unpacks it.

    解压走 ``PluginHost.validate_package``（与安装预览同一套解压限制），再对解出的
    模组目录跑 :func:`validate_module`。``extract_to`` 仅在校验通过时写入，且必须
    是不存在的路径。
    """

    report: dict[str, Any] = {
        "module": str(archive_path),
        "ok": False,
        "errors": [],
        "warnings": [],
        "adventures": [],
        "catalogs": [],
    }
    if extract_to is not None and extract_to.exists():
        report["errors"].append(f"extract target already exists: {extract_to}")
        return report
    try:
        payload = archive_path.read_bytes()
    except OSError as exc:
        report["errors"].append(f"cannot read package: {exc}")
        return report
    report["package"] = {
        "file": archive_path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

    def check(directory: Path, manifest: dict[str, Any]) -> None:
        plugin_id = str(manifest.get("id") or "")
        if directory.name != plugin_id and not _manifest_at_archive_root(payload):
            report["errors"].append("插件包顶层目录名必须与插件 ID 一致")
            return
        with tempfile.TemporaryDirectory(prefix="diceframe-package-validation-") as scratch:
            module_dir = directory
            if directory.name != plugin_id:
                # plugin.json 在压缩包根目录（仓库源码包）：放进与 ID 同名的目录再校验。
                module_dir = Path(scratch) / plugin_id
                shutil.copytree(directory, module_dir)
            result = validate_module(module_dir)
            for key in ("ok", "errors", "warnings", "adventures", "catalogs"):
                report[key] = result[key]
            report["package"].update(_package_summary(module_dir, manifest))
            if result["ok"] and extract_to is not None:
                shutil.copytree(module_dir, extract_to)

    try:
        with tempfile.TemporaryDirectory(prefix="diceframe-package-host-") as scratch:
            host = PluginHost(plugins_dir=Path(scratch) / "plugins", data_dir=Path(scratch) / "data")
            host.validate_package(payload, check)
    except Exception as exc:  # noqa: BLE001 - 校验器必须收集而非抛出
        report["ok"] = False
        report["errors"].append(f"package validation failed: {exc}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a DiceFrame content module.")
    parser.add_argument(
        "module", type=Path,
        help="Content module directory (plugin.json), or a packaged .dfplugin / .zip file.",
    )
    parser.add_argument("--json", action="store_true", help="Print the raw JSON report.")
    parser.add_argument(
        "--extract-to", type=Path, default=None,
        help="Package files only: copy the validated module directory here (must not exist).",
    )
    args = parser.parse_args()

    target = args.module.resolve()
    if target.is_file():
        report = validate_package_archive(
            target, extract_to=args.extract_to.resolve() if args.extract_to else None,
        )
    elif args.extract_to is not None:
        parser.error("--extract-to only applies to a package file")
    else:
        report = validate_module(target)
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
