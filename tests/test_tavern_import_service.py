from __future__ import annotations

import pytest

from src.webui.services.tavern import (
    TavernImportDependencies,
    TavernImportService,
)


def _service() -> TavernImportService:
    return TavernImportService(TavernImportDependencies(
        lorebook=None,
        get_instance=lambda _key: None,
        parse_game_key=lambda raw: (raw,),
        rebuild_lorebook_index=lambda _world_id: None,
    ))


@pytest.mark.asyncio
async def test_tavern_import_rejects_server_paths_and_missing_uploads():
    service = _service()

    assert await service.import_card(file_path="C:/secret/card.png") == {
        "ok": False,
        "error": "已禁用 file_path 模式（安全风险），请改用 file_data 上传",
        "error_code": "tavern_file_path_disabled",
    }
    assert await service.import_card() == {
        "ok": False,
        "error": "未提供文件",
        "error_code": "tavern_file_missing",
    }


@pytest.mark.asyncio
async def test_tavern_import_rejects_oversized_encoded_upload_before_decode():
    result = await _service().import_card(file_data="A" * 40_000_001)

    assert result == {"ok": False, "error": "文件过大（上限 30MB）", "error_code": "tavern_file_too_large"}
