import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from scripts import validate_docker_update


def test_docker_runtime_requires_cryptography_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validate_docker_update, "_native_runtime_matches_target", lambda: False,
    )
    package_root = tmp_path / "package"
    site_packages = package_root / "runtime" / "site-packages"
    site_packages.mkdir(parents=True)

    with pytest.raises(ValueError, match="cffi, pycparser, _cffi_backend"):
        validate_docker_update.validate_runtime_dependencies(package_root)


def test_complete_docker_runtime_passes_structural_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validate_docker_update, "_native_runtime_matches_target", lambda: False,
    )
    package_root = tmp_path / "package"
    site_packages = package_root / "runtime" / "site-packages"
    (site_packages / "cffi").mkdir(parents=True)
    (site_packages / "pycparser").mkdir()
    (site_packages / "_cffi_backend.cpython-311-x86_64-linux-gnu.so").touch()

    validate_docker_update.validate_runtime_dependencies(package_root)


@pytest.mark.parametrize("version", ["3.3.0", "v3.3.2", "3.3.0-beta.3"])
def test_manual_docker_publish_only_tags_the_requested_version(tmp_path: Path, version: str) -> None:
    bash = shutil.which("bash")
    if os.name == "nt":
        git = shutil.which("git")
        bash = str(Path(git).resolve().parents[1] / "bin" / "bash.exe") if git else None
    if not bash or not Path(bash).is_file():
        pytest.skip("The Docker workflow runs on Bash")
    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "docker.yml"
    steps = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["ghcr"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "meta")
    output = tmp_path / "github-output"
    subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script],
        env={**os.environ, "GITHUB_REPOSITORY_OWNER": "DiceFrame",
             "GITHUB_OUTPUT": output.as_posix(), "IMAGE_VERSION": version},
        check=True,
    )
    version = version.removeprefix("v")
    assert output.read_text(encoding="utf-8").splitlines() == [
        f"version={version}",
        f"tags=ghcr.io/diceframe/diceframe:{version},docker.io/falconku/diceframe:{version}",
    ]
