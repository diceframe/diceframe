"""Install journal persistence and crash recovery for the plugin host.

The mixin owns the two on-disk journals and their recovery passes; the host
owns the publish lock these run under and the install transaction that calls
them."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any


class PluginHostInstallJournalMixin:
    """Install journal persistence and crash recovery for the plugin host."""

    __slots__ = ()

    if TYPE_CHECKING:
        data_dir: Any
        logger: Any
        _atomic_json: Any
        def _backfill_install_receipt(
            self,
            plugin_id: str,
            receipt: dict[str, Any] | None,
            target: Path,
        ) -> None:
            ...
        _ensure_inside: Any
        plugins_dir: Any

    @property
    def _install_journal_path(self) -> Path:
        return self.data_dir / "_plugin_install_journal.json"

    def _read_install_journal(self) -> list[dict[str, Any]]:
        path = self._install_journal_path
        if not path.exists():
            return []
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.logger.error("插件安装 journal 无法读取，保留现场等待人工处理: %s", path)
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict) and str(item.get("plugin_id") or "")]

    def _write_install_journal(self, record: dict[str, Any], *, append: bool = False) -> None:
        records = self._read_install_journal() if append else self._read_install_journal()
        plugin_id = str(record.get("plugin_id") or "")
        records = [item for item in records if str(item.get("plugin_id") or "") != plugin_id]
        records.append(dict(record))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_json(self._install_journal_path, records)

    def _remove_install_journal(self, plugin_id: str) -> None:
        records = [item for item in self._read_install_journal()
                   if str(item.get("plugin_id") or "") != str(plugin_id or "")]
        path = self._install_journal_path
        if records:
            self._atomic_json(path, records)
        elif path.exists():
            try:
                path.unlink()
            except OSError:
                self.logger.warning("清理插件安装 journal 失败: %s", path, exc_info=True)

    def _recover_install_journal(self) -> None:
        records = self._read_install_journal()
        if not records:
            return
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        failed_records: list[dict[str, Any]] = []
        for record in records:
            plugin_id = str(record.get("plugin_id") or "")
            try:
                target = Path(str(record.get("target") or "")).resolve()
                staging = Path(str(record.get("staging") or "")).resolve()
                backup = Path(str(record.get("backup") or "")).resolve()
                self._ensure_inside(self.plugins_dir, target)
                self._ensure_inside(self.plugins_dir, staging)
                self._ensure_inside(self.plugins_dir, backup)
                # A still-staged candidate has not been published. The valid
                # target can be the old package (crash before moving backup).
                if (
                    target.is_dir() and not backup.exists()
                    and (staging.exists() or record.get("phase") == "prepared")
                ):
                    if staging.exists():
                        shutil.rmtree(staging)
                    continue
                # A valid target with consumed staging is the committed side.
                # Persist its receipt before discarding recovery evidence.
                if target.is_dir() and (target / "plugin.json").is_file():
                    self._backfill_install_receipt(plugin_id, record, target)
                    if staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
                    if backup.exists():
                        shutil.rmtree(backup, ignore_errors=True)
                    continue
                # Otherwise restore the previous package if the process died
                # after moving it aside. Never promote an uncommitted staging
                # directory into a live plugin.
                if backup.exists() and not target.exists():
                    _rename = target
                    backup.rename(_rename)
                elif target.exists() and not (target / "plugin.json").is_file():
                    shutil.rmtree(target, ignore_errors=True)
                    if backup.exists():
                        backup.rename(target)
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
            except Exception:
                failed_records.append(record)
                self.logger.error("插件安装 journal 恢复失败，已保留现场: %s", plugin_id, exc_info=True)
        if failed_records:
            self._atomic_json(self._install_journal_path, failed_records)
            return
        try:
            self._install_journal_path.unlink()
        except OSError:
            self.logger.warning("清理插件安装 journal 失败: %s", self._install_journal_path, exc_info=True)

    @property
    def _group_install_journal_path(self) -> Path:
        return self.data_dir / "_plugin_group_install_journal.json"

    def _write_group_install_journal(self, record: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_json(self._group_install_journal_path, record)

    def _recover_group_install_journal(self) -> None:
        """Roll back an incomplete multi-package publish before discovery."""

        path = self._group_install_journal_path
        if not path.exists():
            return
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            entries = record.get("entries") if isinstance(record, dict) else None
            phase = str(record.get("phase") or "") if isinstance(record, dict) else ""
            published = {
                str(item) for item in (record.get("published") or [])
            } if isinstance(record, dict) else set()
            if not isinstance(entries, list):
                raise ValueError("group journal entries are invalid")
            receipts_by_id: dict[str, dict[str, Any]] = {}
            journal_receipts = record.get("receipts")
            if isinstance(journal_receipts, list):
                for journal_receipt in journal_receipts:
                    if isinstance(journal_receipt, dict):
                        receipts_by_id[str(journal_receipt.get("plugin_id") or "")] = journal_receipt
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("group journal entry is invalid")
                target = Path(str(entry.get("target") or "")).resolve()
                staging = Path(str(entry.get("staging") or "")).resolve()
                backup = Path(str(entry.get("backup") or "")).resolve()
                self._ensure_inside(self.plugins_dir, target)
                self._ensure_inside(self.plugins_dir, staging)
                self._ensure_inside(self.plugins_dir, backup)
                if phase == "published":
                    self._backfill_install_receipt(
                        str(entry.get("plugin_id") or ""),
                        receipts_by_id.get(str(entry.get("plugin_id") or "")),
                        target,
                    )
                    if staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
                    if backup.exists():
                        shutil.rmtree(backup, ignore_errors=True)
                    continue
                # Old journals recorded publish progress AFTER rename. A
                # consumed staging directory also proves a publish attempt once
                # all backups moved, even if the id is absent from `published`.
                # Persist the rollback plan before mutation so another crash
                # after restoring backup cannot mistake the old target for a
                # candidate and delete it on the next recovery.
                rollback = entry.get("rollback")
                if not isinstance(rollback, dict):
                    rollback = {
                        "discard_target": (
                            str(entry.get("plugin_id") or "") in published
                            or (phase in {"backup_moved", "publishing"} and not staging.exists())
                        ),
                        "restore_backup": backup.exists(),
                    }
                    entry["rollback"] = rollback
                    self._write_group_install_journal(record)
                if backup.exists():
                    if target.exists():
                        shutil.rmtree(target)
                    backup.rename(target)
                elif rollback["discard_target"] and not rollback["restore_backup"] and target.exists():
                    shutil.rmtree(target)
                if staging.exists():
                    shutil.rmtree(staging)
        except Exception:
            self.logger.error("插件组安装 journal 恢复失败，保留现场等待人工处理", exc_info=True)
            return
        try:
            path.unlink()
        except OSError:
            self.logger.warning("清理插件组安装 journal 失败: %s", path, exc_info=True)
