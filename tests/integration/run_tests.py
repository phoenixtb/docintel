#!/usr/bin/env python3
"""
DocIntel Integration Test Runner
=================================
Loads query suites from queries.yaml, calls the RAG streaming API for each,
and writes a structured JSON report + human-readable Markdown summary.

Usage:
    python run_tests.py                          # use defaults from queries.yaml
    python run_tests.py --url http://localhost:8000 --rag-path /query/stream
    python run_tests.py --suite "HR Policies"   # run a single suite
    python run_tests.py --out reports/my_run.json
    python run_tests.py --no-cache              # force use_cache=false (default)
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import yaml

SCRIPT_DIR = Path(__file__).parent
DEFAULT_CONFIG = SCRIPT_DIR / "queries.yaml"
REPORTS_DIR = SCRIPT_DIR / "reports"


# =============================================================================
# SSE streaming parser
# =============================================================================

def call_streaming(
    base_url: str,
    rag_path: str,
    question: str,
    document_type: str,
    tenant_id: str,
    use_reranking: bool,
    use_cache: bool,
    timeout: int,
) -> dict:
    """Call the RAG streaming endpoint, collect all SSE events and return a result dict."""
    payload = {
        "question": question,
        "tenant_id": tenant_id,
        "document_type": document_type,
        "use_reranking": use_reranking,
        "use_cache": use_cache,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-Tenant-Id": tenant_id,
    }

    answer = ""
    thinking = ""
    sources = []
    error = None
    t0 = time.time()

    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST",
                f"{base_url}{rag_path}",
                json=payload,
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                buffer = ""
                for raw_chunk in resp.iter_text():
                    buffer += raw_chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        if data.get("thinking_token"):
                            thinking += data["thinking_token"]
                        if data.get("token"):
                            answer += data["token"]
                        if data.get("sources"):
                            sources = data["sources"]
                        if data.get("error"):
                            error = data["error"]
                        if data.get("done"):
                            break
    except Exception as exc:
        error = str(exc)

    latency = round(time.time() - t0, 2)
    return {
        "answer": answer.strip(),
        "thinking_length": len(thinking),
        "sources": sources,
        "source_count": len(sources),
        "latency_seconds": latency,
        "error": error,
    }


# =============================================================================
# Evaluation
# =============================================================================

def evaluate(result: dict, expect_keywords: list[str], min_answer_length: int = 5) -> dict:
    answer = result["answer"].lower()

    # Check keywords in both answer AND source content so that a short but
    # factually correct answer (e.g. "Garman [4]") is not penalised when the
    # keyword appears in the retrieved source chunks.
    source_text = " ".join(
        (s.get("content") or "") for s in result.get("sources", [])
    ).lower()
    combined = answer + " " + source_text

    keyword_hits = [kw for kw in expect_keywords if kw.lower() in combined]
    keyword_miss = [kw for kw in expect_keywords if kw.lower() not in combined]

    passed = (
        result["error"] is None
        and len(result["answer"]) >= min_answer_length
        and result["source_count"] > 0
        and len(keyword_miss) == 0   # all expected keywords found (in answer OR sources)
    )

    return {
        "passed": passed,
        "keyword_hits": keyword_hits,
        "keyword_miss": keyword_miss,
        "keyword_coverage": (
            round(len(keyword_hits) / len(expect_keywords), 2)
            if expect_keywords else 1.0
        ),
        "answer_length": len(result["answer"]),
        "thinking_length": result.get("thinking_length", 0),
    }


# =============================================================================
# Report writers
# =============================================================================

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        f"# DocIntel Integration Test Report",
        f"",
        f"**Run at:** {report['run_at']}  ",
        f"**Base URL:** {report['config']['base_url']}  ",
        f"**Total:** {report['summary']['total']} queries | "
        f"{report['summary']['passed']} passed | "
        f"{report['summary']['failed']} failed  ",
        f"",
    ]

    for suite in report["suites"]:
        suite_pass = sum(1 for q in suite["queries"] if q["eval"]["passed"])
        suite_total = len(suite["queries"])
        lines += [
            f"---",
            f"",
            f"## {suite['name']}  `{suite['document_type']}`  "
            f"({suite_pass}/{suite_total} passed)",
            f"",
        ]
        for q in suite["queries"]:
            ev = q["eval"]
            res = q["result"]
            icon = PASS if ev["passed"] else FAIL
            kw_note = ""
            if q["expect_keywords"]:
                hit_pct = int(ev["keyword_coverage"] * 100)
                kw_note = f" | kw {hit_pct}%"
                if ev["keyword_miss"]:
                    kw_note += f" (missing: {', '.join(ev['keyword_miss'])})"
            lines += [
                f"### {icon} {q['question']}",
                f"",
                f"*{res['latency_seconds']}s · {res['source_count']} sources{kw_note}*",
                f"",
            ]
            if res["error"]:
                lines += [f"> **Error:** {res['error']}", f""]
            elif res["answer"]:
                # Trim to 600 chars for readability
                preview = res["answer"][:600]
                if len(res["answer"]) > 600:
                    preview += "…"
                lines += [f"{preview}", f""]

            if res["sources"]:
                lines.append("**Sources:**")
                for s in res["sources"][:3]:
                    score_pct = int((s.get("score") or 0) * 100)
                    lines.append(
                        f"- [{s.get('ref_id', '?')}] `{s.get('filename', '')}` "
                        f"— {s.get('section', '')} ({score_pct}%)"
                    )
                lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_json(report: dict, path: Path) -> None:
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="DocIntel integration test runner")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to queries.yaml")
    parser.add_argument("--url", help="Override base_url from config")
    parser.add_argument("--rag-path", dest="rag_path", help="Override rag_path from config")
    parser.add_argument("--suite", help="Run only this suite name")
    parser.add_argument("--out", help="Output file prefix (no extension); default: reports/TIMESTAMP")
    parser.add_argument("--no-cache", dest="no_cache", action="store_true", help="Force use_cache=false")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    cfg = yaml.safe_load(config_path.read_text())
    run_cfg = cfg["config"]

    base_url  = args.url or run_cfg["base_url"]
    rag_path  = args.rag_path or run_cfg.get("rag_path", "/api/v1/query/stream")
    tenant_id = run_cfg.get("tenant_id", "default")
    use_cache = not args.no_cache and run_cfg.get("use_cache", False)
    use_reranking = run_cfg.get("use_reranking", True)
    timeout   = run_cfg.get("timeout_seconds", 120)
    min_answer_length = run_cfg.get("min_answer_length", 5)

    suites = cfg["suites"]
    if args.suite:
        suites = [s for s in suites if s["name"] == args.suite]
        if not suites:
            print(f"Suite '{args.suite}' not found.", file=sys.stderr)
            return 1

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_prefix = Path(args.out) if args.out else REPORTS_DIR / timestamp

    report: dict = {
        "run_at": datetime.now().isoformat(),
        "config": {
            "base_url": base_url,
            "rag_path": rag_path,
            "tenant_id": tenant_id,
            "use_cache": use_cache,
            "use_reranking": use_reranking,
        },
        "suites": [],
        "summary": {"total": 0, "passed": 0, "failed": 0},
    }

    print(f"\nDocIntel Integration Tests — {base_url}{rag_path}")
    print(f"{'─' * 70}\n")

    total = passed = 0

    for suite_def in suites:
        suite_name = suite_def["name"]
        doc_type   = suite_def["document_type"]
        print(f"▶  Suite: {suite_name}  ({doc_type})")

        suite_result = {
            "name": suite_name,
            "document_type": doc_type,
            "queries": [],
        }

        for qdef in suite_def["queries"]:
            question = qdef["question"]
            expect_kw = qdef.get("expect_keywords", [])

            print(f"   ⋯  {question[:65]}", end="", flush=True)
            result = call_streaming(
                base_url=base_url,
                rag_path=rag_path,
                question=question,
                document_type=doc_type,
                tenant_id=tenant_id,
                use_reranking=use_reranking,
                use_cache=use_cache,
                timeout=timeout,
            )
            ev = evaluate(result, expect_kw, min_answer_length=min_answer_length)

            icon = PASS if ev["passed"] else FAIL
            kw_info = ""
            if expect_kw:
                kw_info = f" kw={int(ev['keyword_coverage']*100)}%"
            print(f"\r   {icon}  {question[:60]:<60} {result['latency_seconds']:5.1f}s  "
                  f"{result['source_count']} src{kw_info}")

            suite_result["queries"].append({
                "question": question,
                "expect_keywords": expect_kw,
                "result": result,
                "eval": ev,
            })
            total += 1
            if ev["passed"]:
                passed += 1

        report["suites"].append(suite_result)
        print()

    report["summary"] = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
    }

    json_path = Path(str(out_prefix) + ".json")
    md_path   = Path(str(out_prefix) + ".md")
    write_json(report, json_path)
    write_markdown(report, md_path)

    print(f"{'─' * 70}")
    print(f"Results: {passed}/{total} passed")
    print(f"JSON  → {json_path}")
    print(f"MD    → {md_path}\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
