#!/usr/bin/env python3
"""
Abstention Gate Calibration (Stage 3)
======================================
Reads a Stage-1 baseline report JSON and recommends a rag_min_relevance_score
threshold (tau) by analysing the score distributions of answerable vs
abstention queries.

Usage:
    python calibrate_threshold.py tests/integration/reports/<timestamp>.json

Output:
    Score distribution table + recommended tau + .env export command.

How tau is chosen:
    - Collect top-source scores for all queries where expect_abstention=true
      (should be low — near-miss / out-of-domain)
    - Collect top-source scores for all queries where expect_abstention=false
      AND sources were returned (should be higher)
    - Suggested tau = midpoint between:
        max(abstention_scores) and min(answerable_scores)
    - If ranges overlap: tau = percentile(abstention_scores, 90) + small margin
      (conservative — prefer recall over precision at this stage)
"""

import json
import statistics
import sys
from pathlib import Path


def _fmt(v: float | None) -> str:
    return f"{v:.4f}" if v is not None else "N/A"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python calibrate_threshold.py <report.json>")
        sys.exit(1)

    report_path = Path(sys.argv[1])
    if not report_path.exists():
        print(f"File not found: {report_path}", file=sys.stderr)
        sys.exit(1)

    report = json.loads(report_path.read_text())

    abstention_top_scores: list[float] = []   # queries where no answer expected
    answerable_top_scores: list[float] = []   # queries where answer expected

    query_rows: list[dict] = []

    for suite in report.get("suites", []):
        for q in suite.get("queries", []):
            result = q.get("result", {})
            sources = result.get("sources", [])
            top_score = sources[0].get("score") if sources else None
            ev = q.get("eval", {})
            qm = q.get("quality_metrics", {}) or {}
            ret = qm.get("retrieval", {}) if qm else {}

            query_rows.append({
                "question": q.get("question", "")[:60],
                "top_score": top_score,
                "source_count": result.get("source_count", 0),
                # Ground-truth label from queries.yaml (persisted into the report).
                "expect_abstention": q.get("expect_abstention"),
                "abstention_correct": ev.get("abstention_correct"),
                "hit_at_k": ret.get("hit_at_k"),
                "mrr": ret.get("mrr"),
            })

    if not query_rows:
        print("No queries found in report.")
        sys.exit(0)

    # Print score distribution table
    print(f"\n{'─'*88}")
    print(f"{'Question':<60} {'TopScore':>9} {'Src':>4} {'Expect':>8} {'Hit':>4}")
    print(f"{'─'*88}")
    for r in query_rows:
        ts = _fmt(r["top_score"]) if r["top_score"] is not None else "    N/A"
        exp = "ABSTAIN" if r["expect_abstention"] is True else (
            "ANSWER" if r["expect_abstention"] is False else "?")
        hit = "✓" if r["hit_at_k"] is True else ("✗" if r["hit_at_k"] is False else "-")
        print(f"{r['question']:<60} {ts:>9} {r['source_count']:>4} {exp:>8} {hit:>4}")
    print(f"{'─'*88}\n")

    # Group by GROUND-TRUTH expect_abstention (from queries.yaml), not by whether
    # the model happened to abstain. abstention queries should score low; answerable
    # queries score high. For the answerable group we only trust queries with a
    # confirmed retrieval hit (hit_at_k True) when relevant_docs were specified —
    # this filters out corpus-gap queries (expect ANSWER but no matching doc exists),
    # whose low scores would otherwise poison the separation boundary.
    abs_scores = [r["top_score"] for r in query_rows
                  if r["expect_abstention"] is True and r["top_score"] is not None]
    ans_scores = [r["top_score"] for r in query_rows
                  if r["expect_abstention"] is False and r["top_score"] is not None
                  and r["source_count"] > 0
                  and r["hit_at_k"] is not False]  # exclude confirmed retrieval misses

    print(f"Abstention queries — top-source scores (should be low for good calibration):")
    if abs_scores:
        print(f"  n={len(abs_scores)}  min={_fmt(min(abs_scores))}  "
              f"max={_fmt(max(abs_scores))}  "
              f"median={_fmt(statistics.median(abs_scores))}")
    else:
        print("  (none — run with --metrics or add expect_abstention fields to queries.yaml)")

    print(f"\nAnswerable queries — top-source scores (should be high):")
    if ans_scores:
        print(f"  n={len(ans_scores)}  min={_fmt(min(ans_scores))}  "
              f"max={_fmt(max(ans_scores))}  "
              f"median={_fmt(statistics.median(ans_scores))}")
    else:
        print("  (none)")

    if abs_scores and ans_scores:
        max_abs = max(abs_scores)
        min_ans = min(ans_scores)
        print()
        if min_ans > max_abs:
            tau = round((max_abs + min_ans) / 2.0, 3)
            print(f"Clean separation found.  Suggested tau = {tau}")
        else:
            # Overlap — use 90th-percentile of abstention scores + small margin
            abs_sorted = sorted(abs_scores)
            p90_idx = min(int(len(abs_sorted) * 0.9), len(abs_sorted) - 1)
            p90 = abs_sorted[p90_idx]
            tau = round(p90 + 0.02, 3)
            print(f"Score ranges overlap (max_abs={_fmt(max_abs)} >= min_ans={_fmt(min_ans)}).")
            print(f"Using 90th-percentile of abstention scores + 0.02 margin.")
            print(f"Suggested tau = {tau}  (conservative — prefer recall over precision)")

        print(f"\nSet in .env or docker-compose:")
        print(f"  RAG_MIN_RELEVANCE_SCORE={tau}")
        print(f"  RAG_MIN_SCORE_FALLBACK_TOPK=0")
        print(f"\nExport command:")
        print(f"  export RAG_MIN_RELEVANCE_SCORE={tau}")
    else:
        print("\nInsufficient data to recommend tau.")
        print("Re-run with expect_abstention fields set in queries.yaml and --metrics flag.")


if __name__ == "__main__":
    main()
