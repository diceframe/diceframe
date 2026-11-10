"""Build the Windows installer (NSIS) from a portable package.

安装程序装的就是便携版本身（DiceFrame.exe + app/ + python/），按当前用户安装到
%LOCALAPPDATA%/Programs/DiceFrame，不需要管理员权限；脚本见 scripts/installer/。
从打好的便携版 zip 取载荷，保证安装版与便携版是同一套文件。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

try:
    from . import build_launcher, build_release
except ImportError:  # pragma: no cover - script execution
    import build_launcher
    import build_release


ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
INSTALLER_DIR = ROOT / "scripts" / "installer"
NSIS_SCRIPT = INSTALLER_DIR / "DiceFrame.nsi"
ICON_PATH = DIST_DIR / "_launcher" / "DiceFrame.ico"
CANDIDATE_MAKENSIS = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "NSIS" / "makensis.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NSIS" / "makensis.exe",
]
PAYLOAD_REQUIRED = ("DiceFrame.exe", "app/web_server.py", "python/python.exe")


def find_makensis() -> Path:
    configured = os.environ.get("MAKENSIS", "").strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
        raise RuntimeError(f"MAKENSIS points to a missing file: {configured}")
    found = shutil.which("makensis")
    if found:
        return Path(found)
    for candidate in CANDIDATE_MAKENSIS:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "Cannot find makensis.exe. Install NSIS 3 (https://nsis.sourceforge.io/) "
        "or set MAKENSIS to its path."
    )


def numeric_version(version: str) -> str:
    """The x.y.z part of a release version: Windows file-version fields reject pre-release suffixes."""
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        raise ValueError(f"Unsupported version for the installer: {version}")
    return ".".join(match.groups())


def setup_name(version: str) -> str:
    return f"DiceFrame-v{version.strip().lstrip('vV')}-windows-setup.exe"


def extract_payload(portable_zip: Path, workdir: Path) -> Path:
    """Unpack the portable zip and return its single top-level directory (the install payload)."""
    with zipfile.ZipFile(portable_zip) as archive:
        names = archive.namelist()
        for name in names:
            parts = PurePosixPath(name.replace("\\", "/")).parts
            if not parts or name.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]:
                raise RuntimeError(f"Unsafe path in portable zip: {name}")
        tops = {PurePosixPath(name.replace("\\", "/")).parts[0] for name in names}
        if len(tops) != 1:
            raise RuntimeError("Portable zip must contain exactly one top-level directory")
        archive.extractall(workdir)
    payload = workdir / next(iter(tops))
    missing = [required for required in PAYLOAD_REQUIRED if not (payload / required).is_file()]
    if missing:
        raise RuntimeError("Portable zip is missing " + ", ".join(missing))
    return payload


def makensis_command(
    makensis: Path, *, version: str, payload: Path, icon: Path, output: Path
) -> list[str]:
    return [
        str(makensis),
        "-V2",
        "-INPUTCHARSET",
        "UTF8",
        f"-DVERSION={version}",
        f"-DNUMERIC_VERSION={numeric_version(version)}",
        f"-DPAYLOAD={payload}",
        f"-DICON={icon}",
        f"-DOUTFILE={output}",
        str(NSIS_SCRIPT),
    ]


def build_installer(portable_zip: Path, output_dir: Path, version: str) -> Path:
    version = version.strip().lstrip("vV")
    if not portable_zip.is_file():
        raise RuntimeError(f"Cannot find portable package: {portable_zip}")
    makensis = find_makensis()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / setup_name(version)
    build_launcher.write_ico(ICON_PATH)
    with tempfile.TemporaryDirectory(prefix="df-installer-") as workdir:
        payload = extract_payload(portable_zip, Path(workdir))
        output.unlink(missing_ok=True)
        command = makensis_command(
            makensis, version=version, payload=payload, icon=ICON_PATH, output=output
        )
        print("> " + " ".join(command), flush=True)
        subprocess.run(command, cwd=str(INSTALLER_DIR), check=True)
    if not output.is_file():
        raise RuntimeError(f"makensis did not produce {output}")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the DiceFrame Windows installer (NSIS).")
    parser.add_argument("--version", default=build_release.app_version(), help="Release version, default: src/version.py")
    parser.add_argument("--portable-zip", type=Path, default=None, help="Portable package to install, default: dist/DiceFrame-v<version>-windows-portable.zip")
    parser.add_argument("--output-dir", type=Path, default=DIST_DIR, help="Directory for the generated setup exe")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    version = args.version.strip().lstrip("vV")
    portable_zip = args.portable_zip or DIST_DIR / f"DiceFrame-v{version}-windows-portable.zip"
    output = build_installer(portable_zip.resolve(), args.output_dir.resolve(), version)
    print(f"\nInstaller created: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
