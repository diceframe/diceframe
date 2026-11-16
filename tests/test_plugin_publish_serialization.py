"""The global recoverable group journal cannot be overwritten by a second writer."""
import asyncio
import json

import pytest

from src.plugin_host.host import PluginHost
from test_plugin_install_crash_recovery import PowerLoss
from test_plugin_requirements import _package_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("second_group", [False, True])
async def test_competing_publish_waits_until_first_group_finishes(tmp_path, monkeypatch, second_group):
    import src.plugin_host.host as host_module

    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    await host.install_group_from_zips([_package_bytes(p, "1") for p in ("pack-a", "pack-b")])
    paused = asyncio.Event()
    release = asyncio.Event()
    original = host_module._rename_dir_with_retry

    async def interrupt_after_first_backup(src, dst, **kwargs):
        await original(src, dst, **kwargs)
        if src.name == "pack-a" and dst.name.endswith(".backup"):
            paused.set()
            await release.wait()
            raise PowerLoss()

    monkeypatch.setattr(host_module, "_rename_dir_with_retry", interrupt_after_first_backup)
    first = asyncio.create_task(host.install_group_from_zips(
        [_package_bytes(p, "2") for p in ("pack-a", "pack-b")], overwrite=True,
    ))
    await asyncio.wait_for(paused.wait(), 2)
    before = json.loads(host._group_install_journal_path.read_text())
    second = asyncio.create_task(
        host.install_group_from_zips([_package_bytes("pack-c", "1")]) if second_group
        else host.install_from_zip(_package_bytes("pack-c", "1"))
    )
    try:
        await asyncio.sleep(0.02)
        assert not second.done()
        assert json.loads(host._group_install_journal_path.read_text()) == before
        assert not (tmp_path / "plugins/pack-c").exists()
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        release.set()
        with pytest.raises(PowerLoss):
            await first
        restarted = PluginHost(tmp_path / "plugins", tmp_path / "data")
        restarted.discover()
        assert restarted.public_detail("pack-a")["version"] == "1"
        assert restarted.public_detail("pack-b")["version"] == "1"
        assert not restarted._group_install_journal_path.exists()
    finally:
        release.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
