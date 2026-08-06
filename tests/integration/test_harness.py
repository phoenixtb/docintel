"""
Unit tests for the integration harness's own logic (run_tests.py / metrics.py).

No live services required — pure function tests against synthetic inputs.
This is what CI (.github/workflows/integration-tests.yml) actually gates:
GitHub-hosted runners have no LMForge, so real embed/rerank/generation
against the seeded corpus cannot run there (see G3 notes in that workflow
and tasks/05-2026-gaps-plan.md). These tests protect the harness's gating
and abstention-scoring logic from silent regressions; the real
retrieval-quality gate (--retrieval-only --gate) is a local/nightly command
against a machine with LMForge running.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import metrics  # noqa: E402
from run_tests import check_gate, evaluate  # noqa: E402


def _result(answer: str = "", source_count: int = 0, error: str | None = None) -> dict:
    return {
        "answer": answer,
        "thinking_length": 0,
        "sources": [{"content": ""}] * source_count,
        "source_count": source_count,
        "latency_seconds": 1.0,
        "error": error,
        "cache_hit": False,
        "reranker_degraded": False,
    }


# ---------------------------------------------------------------------------
# evaluate() — abstention correctness (G1 fix)
# ---------------------------------------------------------------------------

def test_correct_abstention_passes_with_zero_sources():
    """A near-miss query that correctly abstains (0 sources, canned response)
    must PASS — this was the pre-G1 bug (sources_ok required source_count > 0
    even for expect_abstention=True, so every correct abstention 'failed')."""
    result = _result(answer="I couldn't find relevant information in the uploaded documents.", source_count=0)
    ev = evaluate(result, expect_keywords=["relevant"], expect_abstention=True)
    assert ev["passed"] is True
    assert ev["abstention_correct"] is True


def test_incorrect_abstention_fails_even_with_keyword_hit():
    """The model wrongly abstains on an answerable query — must fail overall
    even if expect_keywords happens to match (e.g. via echoed question text)."""
    result = _result(answer="I couldn't find relevant information in the uploaded documents.", source_count=0)
    ev = evaluate(result, expect_keywords=["policy"], expect_abstention=False)
    assert ev["abstention_correct"] is False
    assert ev["passed"] is False


def test_answerable_query_without_sources_fails():
    result = _result(answer="Here is a real answer with content.", source_count=0)
    ev = evaluate(result, expect_keywords=[], expect_abstention=False)
    assert ev["passed"] is False  # sources_ok requires source_count > 0


def test_answerable_query_with_sources_and_keywords_passes():
    result = _result(answer="The work-life balance policy encourages balance.", source_count=2)
    ev = evaluate(result, expect_keywords=["work-life", "balance"], expect_abstention=False)
    assert ev["passed"] is True


# ---------------------------------------------------------------------------
# evaluate() — retrieval_only mode (G3)
# ---------------------------------------------------------------------------

def test_retrieval_only_abstention_from_source_count():
    """No answer text is generated in retrieval-only mode — abstention must be
    judged from source_count, not from text heuristics."""
    result = _result(answer="", source_count=0)
    ev = evaluate(result, expect_keywords=["policy"], expect_abstention=True, retrieval_only=True)
    assert ev["abstention_correct"] is True
    assert ev["passed"] is True


def test_retrieval_only_wrong_abstention_from_source_count():
    result = _result(answer="", source_count=3)
    ev = evaluate(result, expect_keywords=[], expect_abstention=True, retrieval_only=True)
    assert ev["abstention_correct"] is False
    assert ev["passed"] is False


def test_retrieval_only_answerable_with_sources_passes():
    result = _result(answer="", source_count=3)
    ev = evaluate(result, expect_keywords=["ignored"], expect_abstention=False, retrieval_only=True)
    assert ev["passed"] is True  # keyword/answer-length checks are skipped


# ---------------------------------------------------------------------------
# check_gate() — G3 CI gate thresholds
# ---------------------------------------------------------------------------

def _report(quality_summary: dict, suites: list | None = None) -> dict:
    return {"suites": suites or [], "quality_summary": quality_summary}


def test_gate_passes_when_all_thresholds_met():
    report = _report({
        "hit_at_k_avg": "0.95",
        "abstention_correct_rate": "9/10",
        "faithfulness_avg": "0.85",
    })
    gate_cfg = {"hit_at_k_min": 0.9, "abstention_correct_min": 0.9, "faithfulness_min": 0.8}
    assert check_gate(report, gate_cfg) == []


def test_gate_breaches_hit_at_k():
    report = _report({"hit_at_k_avg": "0.5", "abstention_correct_rate": "10/10"})
    breaches = check_gate(report, {"hit_at_k_min": 0.9})
    assert len(breaches) == 1
    assert "hit_at_k_avg" in breaches[0]


def test_gate_breaches_abstention_rate():
    report = _report({"hit_at_k_avg": "1.0", "abstention_correct_rate": "5/10"})
    breaches = check_gate(report, {"abstention_correct_min": 0.9})
    assert len(breaches) == 1
    assert "abstention_correct_rate" in breaches[0]


def test_gate_skips_faithfulness_when_judge_did_not_run():
    """CI runs --retrieval-only (no judge) — faithfulness_avg is None, and the
    faithfulness criterion must be silently skipped, not counted as a breach."""
    report = _report({"hit_at_k_avg": "1.0", "abstention_correct_rate": "10/10", "faithfulness_avg": None})
    breaches = check_gate(report, {"hit_at_k_min": 0.9, "abstention_correct_min": 0.9, "faithfulness_min": 0.8})
    assert breaches == []


def test_gate_breaches_reranker_degraded():
    report = _report(
        {"hit_at_k_avg": "1.0", "abstention_correct_rate": "10/10"},
        suites=[{"queries": [{"question": "q1", "eval": {"reranker_degraded": True}}]}],
    )
    breaches = check_gate(report, {"allow_reranker_degraded": False})
    assert len(breaches) == 1
    assert "reranker_degraded" in breaches[0]


def test_gate_missing_metrics_is_a_breach_not_a_silent_pass():
    """Running --gate without --metrics/--retrieval-only must fail loudly
    (no quality_summary at all), not silently report a passing gate."""
    report = {"suites": []}
    breaches = check_gate(report, {"hit_at_k_min": 0.9, "abstention_correct_min": 0.9})
    assert len(breaches) == 2


def test_gate_retrieval_only_uses_lower_abstention_floor():
    """--retrieval-only judges abstention from source_count alone, which can't
    tell a hard (embedding-adjacent) near-miss from a real miss — so it uses
    abstention_correct_min_retrieval_only instead of the full-mode bar."""
    report = _report({"abstention_correct_rate": "11/14"})  # 0.786
    gate_cfg = {"abstention_correct_min": 0.9, "abstention_correct_min_retrieval_only": 0.75}
    assert check_gate(report, gate_cfg, retrieval_only=True) == []
    assert check_gate(report, gate_cfg, retrieval_only=False) != []


def test_gate_retrieval_only_falls_back_to_full_bar_if_unconfigured():
    report = _report({"abstention_correct_rate": "8/10"})  # 0.8
    gate_cfg = {"abstention_correct_min": 0.9}  # no retrieval-only override configured
    breaches = check_gate(report, gate_cfg, retrieval_only=True)
    assert len(breaches) == 1


# ---------------------------------------------------------------------------
# metrics.py — retrieval_metrics / abstention_correct (unchanged by G4)
# ---------------------------------------------------------------------------

def test_retrieval_metrics_hit_and_mrr():
    sources = [
        {"filename": "technical_0001.txt", "domain": "technical"},
        {"filename": "hr_policy_0002.txt", "domain": "hr_policy"},
    ]
    result = metrics.retrieval_metrics(sources, relevant_docs=["hr_policy"], k=5)
    assert result["hit_at_k"] is True
    assert result["mrr"] == 0.5  # rank 2


def test_retrieval_metrics_no_relevant_docs_returns_none():
    result = metrics.retrieval_metrics([{"filename": "x"}], relevant_docs=[], k=5)
    assert result["hit_at_k"] is None


def test_abstention_correct_heuristic():
    assert metrics.abstention_correct("I couldn't find relevant information.", True) is True
    assert metrics.abstention_correct("The policy allows 20 days of leave.", False) is True
    assert metrics.abstention_correct("I couldn't find relevant information.", False) is False
