# -*- coding: utf-8 -*-
"""更新期间「提前提升启动器」的行为测试（跨平台，用 platform 注入 Windows）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.webui.services.launcher_promotion import promote_pending_launcher


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    updater = data / "_updater"
    root = tmp_path / "install"
    updater.mkdir(parents=True)
    root.mkdir()
    (root / "DiceFrame.exe").write_bytes(b"old-launcher")
    return data, root, updater / "launcher-v9.9.9.exe"


def _signal(updater: Path, staged: Path) -> None:
    (updater / "restart_signal.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "expected_version": "9.9.9",
                "candidate_dir": str(updater.parent / "versions" / "v9.9.9"),
                "launcher_path": str(staged),
                "probation_seconds": 60,
            }
        ),
        encoding="utf-8",
    )


def _promote(data: Path, root: Path) -> bool:
    return promote_pending_launcher(data, root, platform="nt")


def test_promotes_the_staged_launcher_while_the_update_is_pending(tmp_path):
    data, root, staged = _layout(tmp_path)
    staged.write_bytes(b"fixed-launcher")
    _signal(staged.parent, staged)

    assert _promote(data, root) is True
    assert (root / "DiceFrame.exe").read_bytes() == b"fixed-launcher"
    assert (root / "DiceFrame.exe.old").read_bytes() == b"old-launcher"
    # 旧启动器收尾还要用 launcher_path，不能删。
    assert staged.is_file()


def test_is_idempotent_when_the_root_launcher_is_already_the_new_one(tmp_path):
    data, root, staged = _layout(tmp_path)
    staged.write_bytes(b"fixed-launcher")
    _signal(staged.parent, staged)

    assert _promote(data, root) is True
    assert _promote(data, root) is False  # 内容一致，不再重命名运行中的 exe
    assert (root / "DiceFrame.exe.old").read_bytes() == b"old-launcher"


@pytest.mark.parametrize(
    "setup",
    [
        "no_signal",
        "no_install_root",
        "posix",
        "outside_updater_dir",
        "wrong_suffix",
        "broken_signal",
        "missing_staged",
    ],
)
def test_no_op_cases_never_touch_the_installed_launcher(tmp_path, setup):
    data, root, staged = _layout(tmp_path)
    staged.write_bytes(b"fixed-launcher")
    launcher = root / "DiceFrame.exe"
    platform = "nt"

    if setup == "no_signal":
        pass
    elif setup == "no_install_root":
        _signal(staged.parent, staged)
        root = ""
    elif setup == "posix":
        _signal(staged.parent, staged)
        platform = "posix"
    elif setup == "outside_updater_dir":
        outside = tmp_path / "elsewhere.exe"
        outside.write_bytes(b"fixed-launcher")
        _signal(staged.parent, outside)
    elif setup == "wrong_suffix":
        staged = staged.with_suffix(".dll")
        staged.write_bytes(b"fixed-launcher")
        _signal(staged.parent, staged)
    elif setup == "broken_signal":
        (staged.parent / "restart_signal.json").write_text("{not json", encoding="utf-8")
    elif setup == "missing_staged":
        _signal(staged.parent, staged)
        staged.unlink()

    assert promote_pending_launcher(data, root, platform=platform) is False
    assert launcher.read_bytes() == b"old-launcher"
    if root:
        assert not (root / "DiceFrame.exe.old").exists()


def test_restores_the_original_launcher_if_the_copy_fails(tmp_path, monkeypatch):
    data, root, staged = _layout(tmp_path)
    staged.write_bytes(b"fixed-launcher")
    _signal(staged.parent, staged)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", fail)

    assert _promote(data, root) is False
    assert (root / "DiceFrame.exe").read_bytes() == b"old-launcher"
    assert not (root / "DiceFrame.exe.old").exists()
