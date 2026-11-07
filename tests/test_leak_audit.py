"""Deterministic narration audit: hidden-lore leak detection and round metrics."""

from __future__ import annotations

import json

from scripts.audit_narration import main as audit_main
from src.knowledge.leak_audit import (
    ProtectedText,
    audit_rounds,
    find_protected_leaks,
    split_lore_by_audience,
    summarize,
    tokenize,
)

SECRET = ProtectedText("secret-1", "老宅地下室里藏着教团的祭坛，管家每晚都会下去点燃黑色蜡烛。")


def test_tokenize_splits_cjk_characters_and_latin_words():
    assert tokenize("Arkham 的旧书店") == ["arkham", "的", "旧", "书", "店"]


def test_verbatim_secret_in_public_narration_is_reported():
    narration = "你推开门，听见管家低声说：地下室里藏着教团的祭坛，每晚都要点燃黑色蜡烛。"
    findings = find_protected_leaks(narration, [SECRET])
    assert [finding.source_id for finding in findings] == ["secret-1"]
    assert findings[0].runs >= 2


def test_a_single_shared_phrase_is_not_a_leak():
    narration = "地下室里藏着一箱旧报纸，别的什么都没有。"
    assert find_protected_leaks(narration, [SECRET]) == []


def test_wording_that_is_also_public_does_not_count():
    narration = "镇上人都说老宅地下室里藏着教团的祭坛，可谁也没见过。"
    allowed = ["镇上流传：老宅地下室里藏着教团的祭坛。"]
    assert find_protected_leaks(narration, [SECRET], allowed) == []


def test_english_text_uses_word_runs():
    secret = ProtectedText("en", "The butler lights black candles at the cult altar under the old house every night")
    narration = "Later you learn the butler lights black candles at the cult altar under the old house."
    assert [finding.source_id for finding in find_protected_leaks(narration, [secret])] == ["en"]


def test_lore_is_split_by_audience():
    protected, allowed = split_lore_by_audience([
        {"uid": "a", "content": "GM secret", "visible_to": []},
        {"uid": "b", "content": "Only Alice knows", "visible_to": ["alice"]},
        {"uid": "c", "content": "Everyone knows", "visible_to": ["public"]},
        {"uid": "d", "content": "   "},
    ])
    assert [item.source_id for item in protected] == ["a", "b"]
    assert allowed == ["Everyone knows"]


def test_audit_reports_first_leak_only_and_counts_repetition_and_sanity_checks():
    rounds = [
        {"round": 1, "gm_response": "管家说地下室里藏着教团的祭坛，每晚都要点燃黑色蜡烛。雨一直下个不停，窗外一片漆黑。"},
        {"round": 2, "gm_response": "你又想起地下室里藏着教团的祭坛，每晚都要点燃黑色蜡烛。雨一直下个不停，窗外一片漆黑。",
         "state_changes": ["【理智检定】调查员：d100=80 / 理智 50 → 失败，损失 4（0/1d6）"]},
    ]
    results = audit_rounds(rounds, [SECRET], [])
    assert [bool(item.leaks) for item in results] == [True, False]
    assert results[1].repeated_sentences == 1
    summary = summarize(results)
    assert summary["leak_rounds"] == [{"round": 1, "sources": ["secret-1"]}]
    assert summary["sanity_checks"] == 1


def test_script_reads_a_save_and_a_lorebook(tmp_path, capsys):
    save = tmp_path / "state.json"
    save.write_text(json.dumps({"log": [
        {"round": 1, "gm_response": "管家说地下室里藏着教团的祭坛，每晚都要点燃黑色蜡烛。"},
    ]}, ensure_ascii=False), encoding="utf-8")
    lore = tmp_path / "lore.json"
    lore.write_text(json.dumps({"entries": {"0": {"uid": "secret-1", "content": SECRET.text, "visible_to": []}}},
                               ensure_ascii=False), encoding="utf-8")
    assert audit_main(["--save", str(save), "--lorebook", str(lore), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["leak_rounds"] == [{"round": 1, "sources": ["secret-1"]}]
    assert report["protected_entries"] == 1

def test_overlapping_windows_are_one_span_not_two() -> None:
    """7 个相同词元在 run_size=6 下产生两个重叠窗口，只能算一段证据。"""

    repeated = "甲" * 7
    protected = [ProtectedText("gm-note", repeated)]
    assert find_protected_leaks(repeated, protected, run_size=6, min_runs=2) == []


def test_a_long_contiguous_overlap_still_counts_as_a_leak() -> None:
    """一整段长文被照抄（>1 个 run_size）仍要报，不是把功能一刀切掉。"""

    phrase = "祭坛下方有一条通往地下的暗门"
    protected = [ProtectedText("gm-note", phrase)]
    findings = find_protected_leaks(phrase, protected, run_size=6, min_runs=2)
    assert [finding.source_id for finding in findings] == ["gm-note"]
    assert findings[0].runs >= 2


def test_two_separated_matches_report_two_spans() -> None:
    phrase = "祭坛下方有一条通往地下的暗门"
    narration = f"{phrase}。冒险者四处打量，什么也没发现。{phrase}。"
    protected = [ProtectedText("gm-note", phrase)]
    findings = find_protected_leaks(narration, protected, run_size=6, min_runs=2)
    assert len(findings) == 1
    assert findings[0].runs >= 4


def test_allowed_text_does_not_count_as_evidence_even_when_repeated() -> None:
    phrase = "祭坛下方有一条通往地下的暗门"
    narration = f"{phrase}。{phrase}。"
    protected = [ProtectedText("gm-note", phrase)]
    findings = find_protected_leaks(narration, protected, allowed_texts=[phrase], run_size=6, min_runs=2)
    assert findings == []
