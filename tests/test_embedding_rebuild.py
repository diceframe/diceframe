from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.webui.routes.embedding_models import _run_rebuild


def test_rebuild_worker_reports_stalled_embedding_batch():
    class Store:
        embedding_client = object()

        async def embed_all_pending(self):
            return 0

        def embedding_status(self):
            return {"pending": 3}

    app = {"subsystems": SimpleNamespace(memory_store=Store())}
    asyncio.run(_run_rebuild(app))
    assert app["_embedding_backfill_error"]


def test_rebuild_worker_finishes_when_no_pending_entries_remain():
    class Store:
        embedding_client = object()

        async def embed_all_pending(self):
            return 0

        def embedding_status(self):
            return {"pending": 0}

    app = {"subsystems": SimpleNamespace(memory_store=Store())}
    asyncio.run(_run_rebuild(app))
    assert "_embedding_backfill_error" not in app
