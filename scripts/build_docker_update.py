"""Build the managed-Docker application update artifact for Linux AMD64."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:
    from . import build_release
except ImportError:
    import build_release

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.docker_launcher.contracts import (  # noqa: E402
    LAUNCHER_SCHEMA,
    PLATFORM,
    PYTHON_ABI,
    RUNTIME_API,
    file_sha256,
)

DIST_DIR = ROOT / "dist"
BUILD_ROOT = DIST_DIR / "_docker_update_build"
LOCK_FILE = ROOT / "requirements-docker-amd64.lock"


def install_runtime(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "pip", "install",
        "--require-hashes", "--no-deps", "--only-binary=:all:",
        "--platform", "manylinux_2_17_x86_64",
        "--python-version", "3.11", "--implementation", "cp", "--abi", "cp311",
        "--target", str(target), "-r", str(LOCK_FILE),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    for directory in sorted(
        (path for path in target.rglob("*") if path.is_dir() and path.name in {"test", "tests", "__pycache__"}),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        shutil.rmtree(directory, ignore_errors=True)
    for compiled in target.rglob("*.py[co]"):
        compiled.unlink(missing_ok=True)


def write_manifest(package_dir: Path, version: str, commit: str) -> None:
    payload = {
        "schema": 1,
        "version": version,
        "platform": PLATFORM,
        "python_abi": PYTHON_ABI,
        "launcher_schema_min": LAUNCHER_SCHEMA,
        "runtime_api": RUNTIME_API,
        "data_rollback_safe": True,
        "entrypoint": "app/web_server.py",
        "site_packages": "runtime/site-packages",
        "health_path": "/api/system/update/health",
        "probation_seconds": 90,
        "commit": commit,
    }
    (package_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def build_package(
    version: str,
    output_dir: Path,
    commit: str = "",
    *,
    build_frontend_assets: bool = True,
) -> Path:
    version = version.strip().lstrip("vV")
    package_name = f"DiceFrame-v{version}-docker-update-linux-amd64"
    package_dir = BUILD_ROOT / package_name
    if BUILD_ROOT.exists():
        shutil.rmtree(BUILD_ROOT)
    package_dir.mkdir(parents=True)

    app_dir = package_dir / "app"
    build_release.prepare_runtime_app_tree(app_dir)
    if build_frontend_assets:
        build_release.build_frontend(app_dir)
    else:
        prebuilt = ROOT / "static-v2"
        if not (prebuilt / "index.html").is_file():
            raise RuntimeError("--skip-frontend requires a prebuilt static-v2/index.html")
        shutil.copytree(prebuilt, app_dir / "static-v2", dirs_exist_ok=True)
    shutil.rmtree(app_dir / "frontend-v2", ignore_errors=True)
    install_runtime(package_dir / "runtime" / "site-packages")
    write_manifest(package_dir, version, commit)

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{package_name}.zip"
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package_dir.parent).as_posix())

    from scripts.validate_docker_update import validate_archive

    validate_archive(output, expected_version=version)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="utf-8",
    )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", default=os.getenv("DICEFRAME_BUILD_VERSION") or build_release.app_version(),
    )
    parser.add_argument("--output-dir", type=Path, default=DIST_DIR)
    parser.add_argument("--commit", default="")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # --allow-dirty must short-circuit: git_dirty() shells out to git, which is
    # absent from the python:3.11-slim build stage (and .dockerignore strips .git),
    # so evaluating it first makes every in-image build die on FileNotFoundError.
    if not args.allow_dirty and build_release.git_dirty():
        print("Working tree is dirty; commit first or use --allow-dirty.", file=sys.stderr)
        return 2
    output = build_package(
        args.version, args.output_dir.resolve(), args.commit,
        build_frontend_assets=not args.skip_frontend,
    )
    print(f"Docker update package created: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
