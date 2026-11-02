"""Regression for repeated full-book scans in activation trace setup."""

from src.lorebook.matcher import KeywordMatcher


def test_constant_membership_is_scanned_once_per_activation(monkeypatch):
    matcher = KeywordMatcher()
    matcher.build([
        {"id": "constant", "is_constant": True},
        {"id": "direct", "keywords": ["door"]},
        {"id": "sticky"},
    ])
    original = matcher._get_constant_ids
    scans = 0

    def counted_constants():
        nonlocal scans
        scans += 1
        return original()

    monkeypatch.setattr(matcher, "_get_constant_ids", counted_constants)
    hits = matcher.match_with_recursive(
        "door", timed_state={"sticky": {"sticky_remaining": 2}},
    )
    assert {row["id"] for row in hits} == {"constant", "direct", "sticky"}
    assert {eid: row["channel"] for eid, row in matcher.last_decisions.items()} == {
        "constant": "constant", "direct": "keyword", "sticky": "sticky",
    }
    assert scans == 1
