"""Actual install fault injection: interrupts bypass normal Exception rollback."""

import hashlib
import json

import pytest

from src.plugin_host.host import PluginHost
from test_plugin_requirements import _package_bytes


class PowerLoss(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("publish_number", [1, 2])
@pytest.mark.parametrize("legacy_journal", [False, True])
async def test_group_rename_before_progress_write_rolls_back_whole_group(
    tmp_path, monkeypatch, overwrite, publish_number, legacy_journal,
):
    import src.plugin_host.host as host_module

    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    if overwrite:
        await host.install_group_from_zips([_package_bytes(p, "1") for p in ("pack-a", "pack-b")])
    original = host_module._rename_dir_with_retry
    published = 0

    async def interrupt_after_rename(src, dst, **kwargs):
        nonlocal published
        await original(src, dst, **kwargs)
        if src.name.endswith(".installing"):
            published += 1
            if published == publish_number:
                raise PowerLoss()

    monkeypatch.setattr(host_module, "_rename_dir_with_retry", interrupt_after_rename)
    with pytest.raises(PowerLoss):
        await host.install_group_from_zips(
            [_package_bytes(p, "2") for p in ("pack-a", "pack-b")], overwrite=overwrite,
        )
    if legacy_journal:
        # Pre-fix writer lost the latest publish progress when rename succeeded.
        journal = json.loads(host._group_install_journal_path.read_text())
        journal["published"] = journal["published"][:-1]
        journal["phase"] = "publishing" if journal["published"] else "backup_moved"
        host._write_group_install_journal(journal)
    for _ in range(2):
        restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
        restarted.discover()
        for plugin_id in ("pack-a", "pack-b"):
            if overwrite:
                assert restarted.public_detail(plugin_id)["version"] == "1"
                assert restarted.public_detail(plugin_id)["status"] == "active"
            else:
                assert not (tmp_path / "plugins" / plugin_id).exists()
        assert not restarted._group_install_journal_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [False, True])
async def test_committed_overwrite_replaces_stale_receipt_on_restart(tmp_path, monkeypatch, group):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    await host.install_from_zip(_package_bytes("pack-a", "1"))
    payload = _package_bytes("pack-a", "2")
    writer = "_write_group_install_journal" if group else "_write_install_journal"
    original = getattr(host, writer)

    def interrupt_after_commit(record, **kwargs):
        original(record, **kwargs)
        if record["phase"] == "published":
            raise PowerLoss()

    monkeypatch.setattr(host, writer, interrupt_after_commit)
    with pytest.raises(PowerLoss):
        if group:
            await host.install_group_from_zips([payload], overwrite=True)
        else:
            await host.install_from_zip(payload, overwrite=True)
    assert host._load_install_receipt("pack-a")["version"] == "1"
    for _ in range(2):
        restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
        restarted.discover()
        detail = restarted.public_detail("pack-a")
        assert detail["version"] == "2"
        assert detail["status"] == "active", detail
        receipt = restarted._load_install_receipt("pack-a")
        manifest = json.loads((tmp_path / "plugins/pack-a/plugin.json").read_text())
        assert receipt["manifest_digest"] == restarted._manifest_digest(manifest)
        assert receipt["artifact_hash"] == hashlib.sha256(payload).hexdigest()
        assert not restarted._install_journal_path.exists()
        assert not restarted._group_install_journal_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [False, True])
async def test_receipt_failure_keeps_journal_for_next_restart(tmp_path, monkeypatch, group):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    writer = "_write_group_install_journal" if group else "_write_install_journal"
    original = getattr(host, writer)

    def interrupt(record, **kwargs):
        original(record, **kwargs)
        if record["phase"] == "published":
            raise PowerLoss()

    monkeypatch.setattr(host, writer, interrupt)
    with pytest.raises(PowerLoss):
        if group:
            await host.install_group_from_zips([_package_bytes("pack-a", "1")])
        else:
            await host.install_from_zip(_package_bytes("pack-a", "1"))
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise OSError("receipt disk unavailable")
        # 恢复现在发生在启动装配（__init__）里，注入失败必须覆盖到构造期。
        patch.setattr(PluginHost, "_save_install_receipt", fail)
        restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
        restarted.discover()
    journal = restarted._group_install_journal_path if group else restarted._install_journal_path
    assert journal.exists()
    restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
    restarted.discover()
    assert restarted._load_install_receipt("pack-a")["version"] == "1"
    assert not journal.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [False, True])
async def test_receipt_repair_refuses_target_manifest_not_in_journal(tmp_path, monkeypatch, group):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    await host.install_from_zip(_package_bytes("pack-a", "1"))
    old_receipt = host._load_install_receipt("pack-a")
    writer = "_write_group_install_journal" if group else "_write_install_journal"
    original = getattr(host, writer)

    def interrupt(record, **kwargs):
        original(record, **kwargs)
        if record["phase"] == "published":
            raise PowerLoss()

    monkeypatch.setattr(host, writer, interrupt)
    with pytest.raises(PowerLoss):
        if group:
            await host.install_group_from_zips([_package_bytes("pack-a", "2")], overwrite=True)
        else:
            await host.install_from_zip(_package_bytes("pack-a", "2"), overwrite=True)
    target = tmp_path / "plugins/pack-a/plugin.json"
    expected = target.read_text()
    changed = json.loads(expected)
    changed["description"] = "user modified after crash"
    target.write_text(json.dumps(changed))
    # 恢复在启动装配（__init__）执行：manifest 冲突让本次恢复拒绝补写并保留 journal。
    restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
    restarted.discover()
    assert restarted._load_install_receipt("pack-a") == old_receipt
    assert json.loads(target.read_text()) == changed
    journal = restarted._group_install_journal_path if group else restarted._install_journal_path
    assert journal.exists()
    # Removing the conflict lets the same retained journal finish on restart.
    target.write_text(expected)
    restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
    restarted.discover()
    assert restarted.public_detail("pack-a")["status"] == "active"
    assert restarted._load_install_receipt("pack-a")["version"] == "2"
    assert not journal.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prepared", "staged"])
async def test_unpublished_overwrite_keeps_previous_package_and_receipt(tmp_path, monkeypatch, phase):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    await host.install_from_zip(_package_bytes("pack-a", "1"))
    old_receipt = host._load_install_receipt("pack-a")
    original = host._write_install_journal

    def interrupt(record, **kwargs):
        original(record, **kwargs)
        if record["phase"] == phase:
            raise PowerLoss()

    monkeypatch.setattr(host, "_write_install_journal", interrupt)
    with pytest.raises(PowerLoss):
        await host.install_from_zip(_package_bytes("pack-a", "2"), overwrite=True)
    restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
    restarted.discover()
    assert restarted.public_detail("pack-a")["version"] == "1"
    assert restarted.public_detail("pack-a")["status"] == "active"
    assert restarted._load_install_receipt("pack-a") == old_receipt
    assert not restarted._install_journal_path.exists()


@pytest.mark.asyncio
async def test_group_rollback_interrupted_after_backup_restore_is_repeatable(tmp_path, monkeypatch):
    from pathlib import Path

    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    await host.install_group_from_zips([_package_bytes(p, "1") for p in ("pack-a", "pack-b")])
    original = host._write_group_install_journal

    def interrupt(record):
        if record["phase"] == "published":
            raise PowerLoss()
        original(record)

    monkeypatch.setattr(host, "_write_group_install_journal", interrupt)
    with pytest.raises(PowerLoss):
        await host.install_group_from_zips(
            [_package_bytes(p, "2") for p in ("pack-a", "pack-b")], overwrite=True,
        )
    original_rename = Path.rename

    def interrupt_restore(path, target):
        result = original_rename(path, target)
        if path.name.endswith(".backup"):
            raise PowerLoss()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", interrupt_restore)
        with pytest.raises(PowerLoss):
            PluginHost(tmp_path / "plugins", tmp_path / "data").discover()
    restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
    restarted.discover()
    for plugin_id in ("pack-a", "pack-b"):
        assert restarted.public_detail(plugin_id)["version"] == "1"
        assert restarted.public_detail(plugin_id)["status"] == "active"
    assert not restarted._group_install_journal_path.exists()


@pytest.mark.asyncio
async def test_concurrent_rescan_waits_instead_of_rolling_back_inflight_group_install(
    tmp_path, monkeypatch,
):
    """组安装中途让出事件循环时，并发 rescan 必须排队等发布锁，不能当场回滚在途 journal。"""
    import asyncio

    import src.plugin_host.host as host_module

    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    await host.install_group_from_zips([_package_bytes(p, "1") for p in ("pack-a", "pack-b")])
    original = host_module._rename_dir_with_retry
    hook = {"armed": False}
    holder: dict[str, object] = {}

    async def insert_rescan_during_install(src, dst, **kwargs):
        await original(src, dst, **kwargs)
        if hook["armed"] and src.name == "pack-a" and dst.name.endswith(".backup"):
            hook["armed"] = False
            # 在途事务正持锁：并发 rescan 只能在锁上排队（fail-open 场景旧实现会在此
            # 无锁插入 discover → 把 live journal 当场回滚，破坏备份/目标目录）。
            holder["rescan"] = asyncio.create_task(host.rescan())
            for _ in range(3):
                await asyncio.sleep(0)
            assert not holder["rescan"].done()
            assert host._group_install_journal_path.exists()
        return None

    monkeypatch.setattr(host_module, "_rename_dir_with_retry", insert_rescan_during_install)
    hook["armed"] = True
    await host.install_group_from_zips(
        [_package_bytes(p, "2") for p in ("pack-a", "pack-b")], overwrite=True,
    )
    # 安装事务完整提交：两个插件都是新版本且 active，没有发生任何回滚。
    discovered = await holder["rescan"]
    by_id = {item["id"]: item for item in discovered}
    for plugin_id in ("pack-a", "pack-b"):
        assert by_id[plugin_id]["version"] == "2"
        assert by_id[plugin_id]["status"] == "active"
        assert host.public_detail(plugin_id)["version"] == "2"
    assert not host._group_install_journal_path.exists()
    assert not list((tmp_path / "plugins").glob(".pack-*.group-*"))
