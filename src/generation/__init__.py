"""AI 生成模块 —— 世界生成和角色生成的共用逻辑，供 plugin.py 和 webui/api.py 复用。"""

from .creator import generate_character, generate_world, parse_json

__all__ = ["generate_world", "generate_character", "parse_json"]
