"""Windows 安装程序（NSIS）的构建：载荷取自便携版 zip，版本号与 makensis 参数。"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from scripts import build_installer


def _portable_zip(path: Path, top: str = "DiceFrame", *, files: dict[str, bytes] | None = None) -> Path:
    contents = files if files is not None else {
        "DiceFrame.exe": b"launcher",
        "app/web_server.py": b"# server",
        "python/python.exe": b"python",
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in contents.items():
            archive.writestr(f"{top}/{name}", data)
    return path


@pytest.mark.parametrize(
    ("version", "expected"),
    [("3.1.3", "3.1.3"), ("v3.1.3", "3.1.3"), ("3.2.0-beta.1", "3.2.0"), ("10.20.30+build.4", "10.20.30")],
)
def test_numeric_version_drops_prerelease_suffix(version, expected):
    # Windows 文件版本字段只接受 x.y.z.w，预发布后缀会让 makensis 直接报错。
    assert build_installer.numeric_version(version) == expected


def test_numeric_version_rejects_garbage():
    with pytest.raises(ValueError):
        build_installer.numeric_version("latest")


def test_setup_name_matches_release_asset_naming():
    # release.yml 按这个名字校验与上传；它不能被应用内更新器的便携版/源码包规则误选。
    assert build_installer.setup_name("v3.2.0-beta.1") == "DiceFrame-v3.2.0-beta.1-windows-setup.exe"


def test_extract_payload_returns_the_single_top_level_directory(tmp_path):
    archive = _portable_zip(tmp_path / "portable.zip", top="DiceFrame-v3.1.3-windows-portable")

    payload = build_installer.extract_payload(archive, tmp_path / "work")

    assert payload == tmp_path / "work" / "DiceFrame-v3.1.3-windows-portable"
    assert (payload / "python" / "python.exe").is_file()


def test_extract_payload_rejects_an_incomplete_package(tmp_path):
    archive = _portable_zip(
        tmp_path / "portable.zip",
        files={"DiceFrame.exe": b"launcher", "app/web_server.py": b"# server"},
    )

    with pytest.raises(RuntimeError, match="python/python.exe"):
        build_installer.extract_payload(archive, tmp_path / "work")


def test_extract_payload_rejects_unsafe_or_ambiguous_layouts(tmp_path):
    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("DiceFrame/../outside.txt", b"x")
    with pytest.raises(RuntimeError, match="Unsafe path"):
        build_installer.extract_payload(unsafe, tmp_path / "work-unsafe")
    assert not (tmp_path / "outside.txt").exists()

    two_tops = tmp_path / "two.zip"
    with zipfile.ZipFile(two_tops, "w") as archive:
        archive.writestr("A/DiceFrame.exe", b"x")
        archive.writestr("B/DiceFrame.exe", b"x")
    with pytest.raises(RuntimeError, match="exactly one top-level directory"):
        build_installer.extract_payload(two_tops, tmp_path / "work-two")


def test_makensis_command_passes_display_and_numeric_versions(tmp_path):
    command = build_installer.makensis_command(
        Path("makensis.exe"),
        version="3.2.0-beta.1",
        payload=tmp_path / "payload",
        icon=tmp_path / "DiceFrame.ico",
        output=tmp_path / "setup.exe",
    )

    assert "-DVERSION=3.2.0-beta.1" in command
    assert "-DNUMERIC_VERSION=3.2.0" in command
    assert f"-DPAYLOAD={tmp_path / 'payload'}" in command
    assert command[-1] == str(build_installer.NSIS_SCRIPT)


def test_find_makensis_honours_the_environment_override(tmp_path, monkeypatch):
    fake = tmp_path / "makensis.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("MAKENSIS", str(fake))
    assert build_installer.find_makensis() == fake

    monkeypatch.setenv("MAKENSIS", str(tmp_path / "missing.exe"))
    with pytest.raises(RuntimeError, match="missing file"):
        build_installer.find_makensis()


def _available_makensis() -> Path | None:
    try:
        return build_installer.find_makensis()
    except RuntimeError:
        return None


@pytest.mark.skipif(
    os.name != "nt" or _available_makensis() is None,
    reason="需要 Windows 与 NSIS（makensis）",
)
def test_builds_a_real_installer_from_a_portable_zip(tmp_path):
    archive = _portable_zip(tmp_path / "DiceFrame-v3.1.3-windows-portable.zip")

    output = build_installer.build_installer(archive, tmp_path / "out", "3.1.3")

    assert output.name == "DiceFrame-v3.1.3-windows-setup.exe"
    assert output.stat().st_size > 0


def test_in_app_updater_never_picks_the_installer():
    # 安装版用应用内更新时下载的是便携版 zip；Release 里多了 setup.exe 也不能被误选。
    from src.webui.services import updater

    setup = build_installer.setup_name("3.1.3")
    latest = {
        "assets": [
            {"name": setup, "download_url": "https://example.com/setup", "size": 1},
            {"name": "DiceFrame-v3.1.3-windows-portable.zip", "download_url": "https://example.com/p", "size": 1},
            {"name": "DiceFrame-v3.1.3-windows.zip", "download_url": "https://example.com/s", "size": 1},
        ]
    }
    for kind, expected in (
        ("portable", "DiceFrame-v3.1.3-windows-portable.zip"),
        ("source", "DiceFrame-v3.1.3-windows.zip"),
    ):
        selected = updater.select_asset(latest, kind)
        assert selected is not None
        assert selected[0]["name"] == expected
