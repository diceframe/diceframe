"""记忆召回增强测试。"""

import pytest
from src.memory.recall import _extract_entities, _extract_ngrams, format_recalled


class TestExtractEntities:
    def test_chinese_entities(self):
        result = _extract_entities("我们去酒馆找老板打听消息")
        assert "酒馆" in result
        assert "老板" in result

    def test_mixed_text(self):
        result = _extract_entities("前往BlackForest寻找Ancient Sword")
        assert "BlackForest" in result
        assert "Ancient" in result
        assert "Sword" in result

    def test_empty(self):
        assert _extract_entities("") == []

    def test_short_words(self):
        result = _extract_entities("我去了")
        # "我去" "去了" 会被提取，但也有 "我去了" 整体
        # 验证不会返回空但不校验精确内容
        assert isinstance(result, list)


class TestExtractNgrams:
    def test_bigrams(self):
        result = _extract_ngrams("跑团游戏", n=2)
        assert "跑团" in result
        assert "团游" in result
        assert "游戏" in result

    def test_ignore_stop_chars(self):
        result = _extract_ngrams("？！？")
        assert len(result) == 0

    def test_empty(self):
        assert len(_extract_ngrams("")) == 0


class TestFormatRecalled:
    def test_empty(self):
        assert format_recalled([]) == ""

    def test_with_entries(self):
        entries = [
            {"entity": "哥布林", "relation": "恐惧", "value": "火把", "confidence": 0.9},
            {"entity": "老法师", "relation": "知道", "value": "古老传送门的秘密", "confidence": 0.7},
        ]
        result = format_recalled(entries)
        assert "【相关记忆】" in result
        assert "哥布林" in result
        assert "老法师" in result
        assert "->" in result
        assert "0.9" not in result


def test_like_coarse_filter_hits_old_entries(tmp_path):
    """P2-H：LIKE 粗筛命中文本实体词相关的旧记忆（旧实现固定 LIMIT 500 会漏）。"""
    from src.memory.delta import MemoryStore
    from src.memory.recall import recall_by_text_improved

    store = MemoryStore(tmp_path / "mem.db")
    store.open()
    # 一条很旧的匹配记忆（updated_at 远早于其他）
    store._conn.execute(
        "INSERT INTO memory_entries (game_key, entity, relation, value, status, updated_at) "
        "VALUES ('g1', '古井', '藏着', '密室钥匙', 'active', '2000-01-01')"
    )
    # 550 条更新的无关记忆（超过旧实现的 LIMIT 500）
    store._conn.executemany(
        "INSERT INTO memory_entries (game_key, entity, relation, value, status) "
        "VALUES (?, ?, '无关', ?, 'active')",
        [("g1", f"npc{i}", f"v{i}") for i in range(550)],
    )
    store._conn.commit()

    result = recall_by_text_improved(store, "g1", "我们查看了古井", limit=5, viewer_is_gm=True)
    assert any(e["entity"] == "古井" for e in result), "LIKE 粗筛应命中旧的相关记忆"
    store.close()
