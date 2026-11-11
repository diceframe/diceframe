# -*- coding: utf-8 -*-
"""更新期间「提前提升启动器」的行为测试（跨平台，用 platform 注入 Windows）。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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


def _no_leftovers(root: Path) -> bool:
    # 失败路径不能留下临时文件，也不能留下指向错误内容的备份。
    return not list(root.glob(".DiceFrame.exe.*.tmp"))


def _big_layout(tmp_path: Path) -> tuple[Path, Path, Path, bytes, bytes]:
    data, root, staged = _layout(tmp_path)
    original = os.urandom(64 * 1024)
    fixed = os.urandom(64 * 1024)
    (root / "DiceFrame.exe").write_bytes(original)
    staged.write_bytes(fixed)
    _signal(staged.parent, staged)
    return data, root, staged, original, fixed


def test_a_copy_that_fails_after_writing_part_of_the_file_leaves_the_launcher_intact(tmp_path, monkeypatch):
    """复制写到一半失败（磁盘满、读写错误）：DiceFrame.exe 必须与原文件逐字节一致。"""
    data, root, _staged, original, fixed = _big_layout(tmp_path)
    real_copy = shutil.copyfileobj

    def partial(reader, writer, *args, **kwargs):
        writer.write(reader.read(1024))
        writer.flush()
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copyfileobj", partial)
    assert _promote(data, root) is False
    assert (root / "DiceFrame.exe").read_bytes() == original
    assert not (root / "DiceFrame.exe.old").exists()
    assert _no_leftovers(root)

    # 重试幂等：故障消失后照常提升，之后再调用是 no-op。
    monkeypatch.setattr(shutil, "copyfileobj", real_copy)
    assert _promote(data, root) is True
    assert (root / "DiceFrame.exe").read_bytes() == fixed
    assert (root / "DiceFrame.exe.old").read_bytes() == original
    assert _promote(data, root) is False
    assert _no_leftovers(root)


def test_a_copy_that_silently_differs_from_the_staged_file_is_rejected(tmp_path, monkeypatch):
    data, root, _staged, original, _fixed = _big_layout(tmp_path)

    def truncated(reader, writer, *args, **kwargs):
        writer.write(reader.read(1024))

    monkeypatch.setattr(shutil, "copyfileobj", truncated)
    assert _promote(data, root) is False
    assert (root / "DiceFrame.exe").read_bytes() == original
    assert not (root / "DiceFrame.exe.old").exists()
    assert _no_leftovers(root)


def test_a_failed_switch_that_left_a_partial_launcher_is_restored_from_the_backup(tmp_path, monkeypatch):
    """最后一步 rename 失败且目标位置已经出现残缺文件：必须按原哈希恢复备份。"""
    data, root, _staged, original, _fixed = _big_layout(tmp_path)
    launcher = root / "DiceFrame.exe"
    real_replace = os.replace

    def flaky(source, target):
        if Path(target) == launcher and Path(source).name.endswith(".tmp"):
            launcher.write_bytes(b"MZ" + b"\0" * 100)
            raise OSError("sharing violation")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", flaky)
    assert _promote(data, root) is False
    assert launcher.read_bytes() == original
    assert not (root / "DiceFrame.exe.old").exists()
    assert _no_leftovers(root)


def test_a_backup_that_cannot_be_moved_aside_aborts_before_touching_the_launcher(tmp_path, monkeypatch):
    data, root, _staged, original, _fixed = _big_layout(tmp_path)
    real_replace = os.replace

    def locked(source, target):
        if Path(target).name == "DiceFrame.exe.old":
            raise PermissionError("in use")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", locked)
    assert _promote(data, root) is False
    assert (root / "DiceFrame.exe").read_bytes() == original
    assert _no_leftovers(root)


_SLEEPER_SOURCE = r"""
internal static class Sleeper
{
    private static void Main()
    {
        // MARKER 只为让两份 exe 内容不同。
        System.Console.WriteLine("MARKER");
        System.Threading.Thread.Sleep(60000);
    }
}
"""


def _compile_sleeper(tmp_path: Path, marker: str) -> Path:
    from scripts import build_launcher

    source = tmp_path / f"sleeper-{marker}.cs"
    source.write_text(_SLEEPER_SOURCE.replace("MARKER", marker), encoding="utf-8")
    output = tmp_path / f"sleeper-{marker}.exe"
    subprocess.run(
        [str(build_launcher.find_csc()), "/nologo", "/target:exe", f"/out:{output}", str(source)],
        check=True,
        capture_output=True,
    )
    return output


@pytest.mark.skipif(os.name != "nt", reason="运行中 exe 的改名约束只在 Windows 上存在")
def test_promotes_over_a_running_launcher_on_windows(tmp_path):
    """真实 Windows 语义：运行中的 exe 能改名、不能覆写；提升后原进程继续运行。"""
    data, root, staged = _layout(tmp_path)
    launcher = root / "DiceFrame.exe"
    shutil.copyfile(_compile_sleeper(tmp_path, "old"), launcher)
    shutil.copyfile(_compile_sleeper(tmp_path, "new"), staged)
    _signal(staged.parent, staged)
    original = launcher.read_bytes()

    running = subprocess.Popen([str(launcher)], stdout=subprocess.DEVNULL)
    try:
        # 运行中的 exe 不能就地覆写：这正是要先复制到临时文件再 rename 的原因。
        with pytest.raises(PermissionError):
            launcher.open("r+b").close()

        assert promote_pending_launcher(data, root) is True
        assert running.poll() is None
        assert launcher.read_bytes() == staged.read_bytes()
        assert (root / "DiceFrame.exe.old").read_bytes() == original
        assert _no_leftovers(root)

        # 备份就是正在运行的旧 exe，删不掉：再来一次提升必须原样放弃，不能把
        # 已经提升好的 DiceFrame.exe 弄坏。
        newest = _compile_sleeper(tmp_path, "newest")
        shutil.copyfile(newest, staged)
        assert promote_pending_launcher(data, root) is False
        assert launcher.read_bytes() != original
        assert (root / "DiceFrame.exe.old").read_bytes() == original
        assert _no_leftovers(root)
    finally:
        running.kill()
        running.wait()
