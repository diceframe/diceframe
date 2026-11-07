"""Deterministic narration audit: hidden-lore leaks, protocol leaks, self-repetition.

This module never calls a model. It compares public narration with text the
players must not see yet (GM-only lore entries, hidden facts) and reports
overlapping token runs. It is meant for offline review of saved games and for
regression checks after prompt changes, not for blocking live narration.

Leak rule
---------
Text is split into tokens: every CJK / kana character is one token, every
Latin word or number is one token. A *run* is ``run_size`` consecutive tokens.
A protected entry counts as leaked into a narration when at least
``min_runs`` distinct runs of that entry appear in the narration **and** none
of those runs also appears in the allowed corpus (public lore, character
sheets, earlier public narration). Shared vocabulary such as a street name
that also appears in public lore therefore does not count as a leak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from src.knowledge.visibility import classify_audience
from src.llm.parser import sanitize_narration
from src.llm.protocol import contains_protocol_markup

DEFAULT_RUN_SIZE = 6
DEFAULT_MIN_RUNS = 2
MAX_EVIDENCE_RUNS = 3

_TOKEN_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]|[0-9a-zÀ-ɏЀ-ӿ]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;\n])")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").casefold())


def token_runs(text: str, run_size: int = DEFAULT_RUN_SIZE) -> set[tuple[str, ...]]:
    tokens = tokenize(text)
    if len(tokens) < run_size:
        return set()
    return {tuple(tokens[index:index + run_size]) for index in range(len(tokens) - run_size + 1)}


@dataclass(frozen=True)
class ProtectedText:
    source_id: str
    text: str


@dataclass
class LeakFinding:
    source_id: str
    runs: int
    evidence: list[str] = field(default_factory=list)


def find_protected_leaks(
    public_text: str,
    protected: Iterable[ProtectedText],
    allowed_texts: Iterable[str] = (),
    *,
    run_size: int = DEFAULT_RUN_SIZE,
    min_runs: int = DEFAULT_MIN_RUNS,
) -> list[LeakFinding]:
    """Return protected entries whose own wording shows up in ``public_text``."""
    public_runs = token_runs(public_text, run_size)
    if not public_runs:
        return []
    allowed_runs: set[tuple[str, ...]] = set()
    for text in allowed_texts:
        allowed_runs |= token_runs(text, run_size)
    findings: list[LeakFinding] = []
    for item in protected:
        shared = (token_runs(item.text, run_size) & public_runs) - allowed_runs
        if len(shared) >= min_runs:
            evidence = sorted("".join(run) if _is_cjk_run(run) else " ".join(run) for run in shared)
            findings.append(LeakFinding(item.source_id, len(shared), evidence[:MAX_EVIDENCE_RUNS]))
    return sorted(findings, key=lambda finding: (-finding.runs, finding.source_id))


def _is_cjk_run(run: tuple[str, ...]) -> bool:
    return all(len(token) == 1 and not token.isascii() for token in run)


def split_lore_by_audience(entries: Iterable[dict]) -> tuple[list[ProtectedText], list[str]]:
    """GM-only and character-only entries are protected; public entries are allowed text."""
    protected: list[ProtectedText] = []
    allowed: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("content") or "")
        if not text.strip():
            continue
        if classify_audience(entry) == "public":
            allowed.append(text)
        else:
            source = str(entry.get("uid") or entry.get("id") or entry.get("comment") or f"entry-{index}")
            protected.append(ProtectedText(source, text))
    return protected, allowed


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if len(part.strip()) >= 8]


@dataclass
class RoundAudit:
    round: object
    chars: int
    protocol_leak: bool
    repeated_sentences: int
    leaks: list[LeakFinding]
    sanity_checks: int


def audit_rounds(
    entries: Iterable[dict],
    protected: list[ProtectedText],
    allowed_texts: list[str],
    *,
    run_size: int = DEFAULT_RUN_SIZE,
    min_runs: int = DEFAULT_MIN_RUNS,
) -> list[RoundAudit]:
    """Audit every round's public narration in order.

    Earlier public narration joins the allowed corpus as the game goes on:
    once something has been said in public, repeating it later is not a new
    leak (the first occurrence is the one that is reported).
    """
    results: list[RoundAudit] = []
    seen_public: list[str] = []
    previous_sentences: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("gm_response") or "")
        public = sanitize_narration(raw)
        sentences = _sentences(public)
        repeated = sum(1 for sentence in sentences if sentence in previous_sentences)
        state_changes = [str(item) for item in entry.get("state_changes") or []]
        results.append(RoundAudit(
            round=entry.get("round"),
            chars=len(public),
            protocol_leak=contains_protocol_markup(public),
            repeated_sentences=repeated,
            leaks=find_protected_leaks(
                public, protected, [*allowed_texts, *seen_public], run_size=run_size, min_runs=min_runs,
            ),
            sanity_checks=sum(1 for item in state_changes if item.startswith(("【理智检定】", "[Sanity Check]", "【正気度ロール】"))),
        ))
        seen_public.append(public)
        previous_sentences = set(sentences)
    return results


def summarize(results: list[RoundAudit]) -> dict[str, object]:
    rounds = len(results)
    return {
        "rounds": rounds,
        "average_chars": round(sum(item.chars for item in results) / rounds, 1) if rounds else 0,
        "protocol_leak_rounds": [item.round for item in results if item.protocol_leak],
        "leak_rounds": [
            {"round": item.round, "sources": [finding.source_id for finding in item.leaks]}
            for item in results if item.leaks
        ],
        "repeated_sentences": sum(item.repeated_sentences for item in results),
        "sanity_checks": sum(item.sanity_checks for item in results),
    }
