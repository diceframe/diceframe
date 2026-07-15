"""世界模板读取 helper。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def world_template_path(worlds_dir: str | Path, world_id: str) -> Path:
    """按 world_id 构造世界模板路径。"""
    return Path(worlds_dir) / f"{world_id}.json"


def load_world_template(worlds_dir: str | Path, world_id: str) -> dict[str, Any] | None:
    """加载世界模板 JSON；不存在时返回 None。"""
    world_id = (world_id or "").strip()
    if not world_id:
        return None
    path = world_template_path(worlds_dir, world_id)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)
