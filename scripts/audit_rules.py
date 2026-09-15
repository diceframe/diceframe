"""Audit rule templates for fields whose absence silently degrades gameplay.

Checks every bundled/plugin rule file (after resolving ``extends`` inheritance).
Additional content-pack source directories can be supplied with ``--path``:

- Hard errors (exit 1): invalid JSON, missing ``rule_id``, malformed
  ``special_stats`` entries, or incompatible advantage/assistance capabilities.
  It also verifies that every discovered world template points to an available
  ``default_rule``. These problems make content unusable or ambiguous.
- Warnings (exit 0 unless ``--strict``): ``special_stats`` entries without an
  explicit ``initial`` (the engine initializes them to max -- fine for
  resource pools, catastrophic for progress bars like KPI), and d20 rules
  with a skill budget whose effective skill bonus is always zero (skill
  values then have no mechanical effect).

Warnings are advisory: pure-narrative design is a legitimate choice; the goal
is to make the consequence visible, not to forbid it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.currency import CurrencySystemError, validate_declared_currency_system
from src.rules.rule_system import RuleSystem  # noqa: E402

SCAN_GLOBS = [
    ROOT / "templates" / "rules" / "*.json",
    ROOT / "plugins" / "*" / "content" / "rules" / "*.json",
    ROOT / "data" / "plugin-packages" / "*" / "content" / "rules" / "*.json",
]
WORLD_SCAN_GLOBS = [
    ROOT / "templates" / "worlds" / "*.json",
    ROOT / "plugins" / "*" / "content" / "worlds" / "*.json",
    ROOT / "data" / "plugin-packages" / "*" / "content" / "worlds" / "*.json",
]

# Engine gives these keys dedicated initialization (CoC SAN/Luck).
_ENGINE_INITIALIZED_KEYS = {"sanity", "luck"}
_ADVANTAGE_TYPES = {"", "d20_keep_high_low", "coc_bonus_penalty"}


def _display_path(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _external_rule_files(path: Path) -> set[Path]:
    """Expand a rule JSON, plugin directory, or content-pack collection."""

    path = path.resolve()
    if path.is_file():
        return {path} if path.suffix.lower() == ".json" else set()
    if not path.is_dir():
        return set()

    files: set[Path] = set()
    if (path / "plugin.json").is_file():
        files.update((path / "content" / "rules").glob("*.json"))
    if path.name == "rules":
        files.update(path.glob("*.json"))
    files.update(path.glob("content/rules/*.json"))
    files.update(path.glob("*/content/rules/*.json"))
    return {item.resolve() for item in files if item.is_file()}


def _external_world_files(path: Path) -> set[Path]:
    path = path.resolve()
    if path.is_file():
        return set()
    if not path.is_dir():
        return set()

    files: set[Path] = set()
    if (path / "plugin.json").is_file():
        files.update((path / "content" / "worlds").glob("*.json"))
    if path.name == "worlds":
        files.update(path.glob("*.json"))
    files.update(path.glob("content/worlds/*.json"))
    files.update(path.glob("*/content/worlds/*.json"))
    return {item.resolve() for item in files if item.is_file()}


def iter_rule_files(extra_paths: list[Path] | None = None) -> list[Path]:
    files: set[Path] = set()
    for pattern in SCAN_GLOBS:
        files.update(p for p in Path(ROOT).glob(str(pattern.relative_to(ROOT))) if p.is_file())
    for path in extra_paths or []:
        files.update(_external_rule_files(path))
    return sorted(files)


def iter_world_files(extra_paths: list[Path] | None = None) -> list[Path]:
    files: set[Path] = set()
    for pattern in WORLD_SCAN_GLOBS:
        files.update(p for p in Path(ROOT).glob(str(pattern.relative_to(ROOT))) if p.is_file())
    for path in extra_paths or []:
        files.update(_external_world_files(path))
    return sorted(files)


def audit_file(path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    rel = _display_path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{rel}: JSON 无效: {exc}"], []

    if raw.get("abstract"):
        return [], []
    if not str(raw.get("rule_id") or "").strip():
        return [f"{rel}: 缺少 rule_id"], []

    # 显式声明版本的货币 schema 属于 hard error：加载边界同样 fail-fast，
    # 这里独立报告具体原因（版本非法 / base_unit 缺失 / rate 非法 / id 重复等），
    # 方便内容作者修复；无版本键的旧规则继续按 legacy 兼容，不误伤。
    declared_system = raw.get("currency_system")
    if declared_system is not None:
        try:
            validate_declared_currency_system(declared_system)
        except CurrencySystemError as exc:
            errors.append(f"{rel}: currency_system 非法: {exc}")

    for stat in raw.get("special_stats") or []:
        if not isinstance(stat, dict) or not str(stat.get("key") or "").strip():
            errors.append(f"{rel}: special_stats 存在缺 key 的条目")
            continue
        if "initial" not in stat and stat.get("key") not in _ENGINE_INITIALIZED_KEYS:
            warnings.append(
                f"{rel}: 特殊属性 '{stat.get('key')}' 未写 initial，引擎将默认初始化为满值 "
                f"({stat.get('key')}={stat.get('max', 99)})；进度条型属性请显式写 initial"
            )

    try:
        rule = RuleSystem.load(path)
    except Exception as exc:  # 继承断裂/公式坏等，交给加载报错
        errors.append(f"{rel}: 规则加载失败: {exc}")
        return errors, warnings

    advantage = rule.check_mechanic.get("advantage")
    if advantage is not None and not isinstance(advantage, dict):
        errors.append(f"{rel}: check_mechanic.advantage 必须是对象")
    elif isinstance(advantage, dict):
        advantage_type = str(advantage.get("type") or "")
        assistance = str(advantage.get("assistance_grants") or "")
        if advantage_type not in _ADVANTAGE_TYPES:
            errors.append(f"{rel}: 未知的 advantage.type={advantage_type!r}")
        if advantage_type == "d20_keep_high_low" and rule.dice_system != "d20":
            errors.append(f"{rel}: d20_keep_high_low 只能用于 d20 规则")
        if advantage_type == "coc_bonus_penalty" and rule.dice_system != "d100":
            errors.append(f"{rel}: coc_bonus_penalty 只能用于 d100 规则")
        if "allow_explicit" in advantage and not isinstance(advantage.get("allow_explicit"), bool):
            errors.append(f"{rel}: advantage.allow_explicit 必须是布尔值")
        if assistance not in {"", "advantage", "disadvantage"}:
            errors.append(f"{rel}: advantage.assistance_grants 只能为空、advantage 或 disadvantage")
        if assistance and advantage_type != "d20_keep_high_low":
            errors.append(f"{rel}: 多人协助转换目前只支持 d20_keep_high_low")

    # 技能加值检查只对 d20 规则有意义：d100 技能值本身就是成功率，none 无检定。
    if rule.dice_system != "d20":
        return errors, warnings

    raw_dc_cap = rule.template.get("max_check_dc", 20)
    try:
        configured_dc_cap = int(raw_dc_cap)
    except (TypeError, ValueError):
        errors.append(f"{rel}: max_check_dc 必须是 1..40 的整数")
        configured_dc_cap = 20
    if not 1 <= configured_dc_cap <= 40:
        errors.append(f"{rel}: max_check_dc 必须是 1..40 的整数")

    has_skill_budget = (
        bool(raw.get("skills"))
        or bool(rule.template.get("skill_pools"))
        or rule.skill_point_total > 0
        or rule.max_skill_value > 0
    )
    if (
        has_skill_budget
        and rule.skill_mode != "proficiency"
        and rule.skill_bonus(80) == 0
    ):
        warnings.append(
            f"{rel}: 定义了技能但有效技能加值恒为 0（技能值不影响检定）。"
            f"如需技能生效请配置 skill_value_to_bonus（参考 base_d20: 20->+1, 40->+2, 60->+3, 80->+4），"
            f"纯叙事设计可忽略"
        )
    return errors, warnings


def audit_world_file(path: Path, available_rules: set[str]) -> list[str]:
    rel = _display_path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{rel}: 世界模板 JSON 无效: {exc}"]
    world_id = str(raw.get("world_id") or "").strip()
    if not world_id:
        return [f"{rel}: 世界模板缺少 world_id"]
    default_rule = str(raw.get("default_rule") or "").strip()
    if not default_rule:
        return [f"{rel}: 世界模板 {world_id!r} 缺少 default_rule"]
    if default_rule not in available_rules:
        return [f"{rel}: default_rule={default_rule!r} 没有对应的可用规则"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="警告也视为失败（用于内容包源头仓库的自检）")
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        default=[],
        help="额外审计的规则 JSON、插件目录或内容包集合目录；可重复",
    )
    args = parser.parse_args()

    files = iter_rule_files(args.path)
    world_files = iter_world_files(args.path)
    if not files:
        print("未发现规则文件，无内容可审计", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    all_warnings: list[str] = []
    for path in files:
        errors, warnings = audit_file(path)
        all_errors.extend(errors)
        all_warnings.extend(warnings)

    available_rules: set[str] = set()
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rule_id = str(raw.get("rule_id") or "").strip()
        if rule_id:
            available_rules.add(rule_id)
    for path in world_files:
        all_errors.extend(audit_world_file(path, available_rules))

    for line in all_warnings:
        print(f"[warn] {line}")
    for line in all_errors:
        print(f"[error] {line}", file=sys.stderr)
    print(
        f"规则审计完成: {len(files)} 个规则, {len(world_files)} 个世界模板, "
        f"{len(all_warnings)} 个警告, {len(all_errors)} 个错误"
    )
    if all_errors:
        return 1
    if args.strict and all_warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
