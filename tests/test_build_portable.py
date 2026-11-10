import hashlib
import zipfile

import pytest

from scripts import build_portable


def test_verify_sha256_accepts_expected_content(tmp_path):
    payload = b"portable-runtime"
    target = tmp_path / "runtime.zip"
    target.write_bytes(payload)

    build_portable.verify_sha256(target, hashlib.sha256(payload).hexdigest())


def test_verify_sha256_rejects_tampered_content(tmp_path):
    target = tmp_path / "runtime.zip"
    target.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        build_portable.verify_sha256(target, "0" * 64)


def test_download_rechecks_cached_file(tmp_path):
    target = tmp_path / "cached.zip"
    target.write_bytes(b"stale-cache")

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        build_portable.download("https://example.invalid/runtime.zip", target, "0" * 64)


def test_remove_generated_tree_removes_nested_content(tmp_path):
    target = tmp_path / "build" / "nested"
    target.mkdir(parents=True)
    (target / "artifact.txt").write_text("generated", encoding="utf-8")

    build_portable.remove_generated_tree(tmp_path / "build")

    assert not (tmp_path / "build").exists()


def test_cleanup_runtime_drops_agent_skill_docs_but_keeps_package_code(tmp_path):
    # pydantic_ai/.agents/skills/… 是包里路径最深的文件，旧版应用内更新在嵌套安装目录下
    # 解压它会超过 Windows 260 字符；运行时不读，打包时去掉。
    site_packages = tmp_path / "runtime" / "Lib" / "site-packages"
    skill_doc = (
        site_packages / "pydantic_ai" / ".agents" / "skills"
        / "building-pydantic-ai-agents" / "references" / "ARCHITECTURE.md"
    )
    skill_doc.parent.mkdir(parents=True)
    skill_doc.write_text("docs", encoding="utf-8")
    module = site_packages / "pydantic_ai" / "agent.py"
    module.write_text("# code", encoding="utf-8")
    # griffe 的 _internal/agents 是真代码，名字像但不能被误删。
    griffe_agents = site_packages / "griffe" / "_internal" / "agents" / "visitor.py"
    griffe_agents.parent.mkdir(parents=True)
    griffe_agents.write_text("# code", encoding="utf-8")

    build_portable.cleanup_runtime(tmp_path / "runtime")

    assert not (site_packages / "pydantic_ai" / ".agents").exists()
    assert module.is_file()
    assert griffe_agents.is_file()


def test_validate_zip_rejects_paths_too_deep_for_older_updaters(tmp_path):
    archive = tmp_path / "portable.zip"
    deep = build_portable.PACKAGE_DIR_NAME + "/" + "x" * build_portable.MAX_ARCHIVE_PATH
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(deep, "")

    with pytest.raises(RuntimeError, match="too long for older in-app updaters"):
        build_portable.validate_zip(archive)


def test_archive_path_budget_fits_a_nested_downloads_install():
    # 已安装的旧版本用自带更新器解压新包：<安装目录>\data\_updater\extract-v<版本>\<zip 内路径>。
    # 最常见的安装方式是在「下载」里右键「全部解压」，得到两层同名目录（约 100 字符）；
    # zip 内路径守住 MAX_ARCHIVE_PATH，这种安装就能直接在应用内升级。
    install_root = (
        r"C:\Users\Administrator\Downloads"
        r"\DiceFrame-v3.1.2-windows-portable\DiceFrame-v3.1.2-windows-portable"
    )
    old_updater_prefix = r"\data\_updater\extract-v3.10.10" + "\\"
    assert len(install_root) + len(old_updater_prefix) + build_portable.MAX_ARCHIVE_PATH <= 259
    assert len(build_portable.PACKAGE_DIR_NAME) <= len("DiceFrame")
