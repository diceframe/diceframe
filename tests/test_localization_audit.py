"""ADR-0015 §8: the localisation ratchet, and what it actually measures.

The scripts are not a cleanup pass — they freeze today's counts so that new
user-facing CJK literals and new undeclared interface errors cannot slip in
silently. These tests pin the measurement itself (what counts, what is
excluded), because a ratchet that silently counts nothing is worse than none.
"""

from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def localization():
    return _load("audit_localization")


@pytest.fixture(scope="module")
def error_codes():
    return _load("audit_error_codes")


# ------------------------------------------------------- CJK literal counting


def test_docstrings_are_not_runtime_text(tmp_path: Path, localization) -> None:
    path = tmp_path / "sample.py"
    path.write_text(
        textwrap.dedent(
            '''"""模块说明：这段中文不是运行时文案。"""
from __future__ import annotations


def f() -> str:
    """函数说明：也不是。"""
    return "这是运行时文案"


VALUE = "也是"
'''
        ),
        encoding="utf-8",
    )
    assert len(localization.cjk_literals(path)) == 2


def test_ascii_only_files_count_zero(tmp_path: Path, localization) -> None:
    path = tmp_path / "clean.py"
    path.write_text('VALUE = "success"\n', encoding="utf-8")
    assert localization.cjk_literals(path) == []


def test_the_current_counts_never_grow(localization) -> None:
    baseline = localization.load_baseline()
    counts, mechanics = localization.scan()

    grown_packages = {
        name: (baseline["packages"].get(name, 0), count)
        for name, count in counts.items()
        if count > baseline["packages"].get(name, 0)
    }
    assert not grown_packages, f"新增用户可见 CJK 字面量：{grown_packages}"

    grown_mechanics = {
        name: (baseline["mechanics"].get(name, 0), count)
        for name, count in mechanics.items()
        if count > baseline["mechanics"].get(name, 0)
    }
    assert not grown_mechanics, f"机械目录出现新的 CJK 字面量：{grown_mechanics}"


def test_the_baseline_covers_every_mechanics_file_it_should(localization) -> None:
    """A new mechanics file with CJK text cannot hide by having no entry."""

    baseline = localization.load_baseline()
    _counts, mechanics = localization.scan()
    unknown = sorted(name for name in mechanics if name not in baseline["mechanics"])
    assert not unknown, f"机械目录新增文件未登记基线：{unknown}"


# --------------------------------------------------- undeclared error payloads


def test_an_undeclared_error_payload_is_counted(tmp_path: Path, error_codes) -> None:
    path = tmp_path / "sample.py"
    path.write_text(
        textwrap.dedent(
            '''
def a():
    return {"ok": False, "error": t("webui.services.x.y")}


def b():
    return {"ok": False, "error_code": "x_y", "error": t("webui.services.x.y")}


def c():
    return error_payload("webui.services.x.y")


def d():
    return {"ok": False, "code": "STALE_RUN", "error": t("webui.services.x.y")}
'''
        ),
        encoding="utf-8",
    )
    sites = error_codes.undeclared_sites(path)
    # a 与 d：缺 error_code；b 已声明；c 走 error_payload。
    assert len(sites) == 2


def test_the_interface_ratchet_never_grows(error_codes) -> None:
    baseline = json.loads(error_codes.BASELINE_PATH.read_text(encoding="utf-8"))["files"]
    current = {name: len(sites) for name, sites in error_codes.scan().items()}
    grown = {
        name: (baseline.get(name, 0), count)
        for name, count in current.items()
        if count > baseline.get(name, 0)
    }
    assert not grown, f"新增未声明错误码的接口错误：{grown}"


def test_the_interfaces_are_scanned_including_the_orchestrator(error_codes) -> None:
    """`api.py` 直接组装响应体；漏扫它等于给 orchestrator 开一条免检通道。"""

    scanned = set(error_codes.scan())
    assert "src/webui/api.py" in scanned
    assert any(name.startswith("src/webui/services/") for name in scanned)
    assert any(name.startswith("src/webui/routes/") for name in scanned)