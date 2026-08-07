"""
RAG Quality Metrics (Stage 1)
==============================
Stage-split evaluation metrics for the DocIntel RAG pipeline.

Retrieval metrics (computed from sources[], no network calls):
  hit_at_k       — >=1 source filename contains any relevant_docs pattern
  mrr            — 1/rank of first relevant source (0.0 if none)
  context_recall — fraction of relevant_docs patterns matched by >=1 source

Generation metrics (LLM-judge — ragas faithfulness + answer_relevancy, G4 —
requires a running LMForge / OpenAI-compatible endpoint):
  faithfulness        — answer claims grounded in provided sources (0.0–1.0)
  answer_relevancy    — answer addresses the question (0.0–1.0)
  abstention_correct  — True iff LLM correctly abstained/answered per expect_abstention
                         (heuristic, no LLM — unchanged by the G4 ragas adoption)

Health:
  reranker_health     — reranker endpoint is alive and returns HTTP 200

Usage:
  from metrics import retrieval_metrics, generation_judge, reranker_health, abstention_correct
"""

import asyncio
import re
import time

import httpx


# ---------------------------------------------------------------------------
# Phrases that indicate the LLM abstained / had no relevant context
# ---------------------------------------------------------------------------
ABSTENTION_PHRASES = [
    "i don't have",
    "i couldn't find",
    "no information",
    "not find relevant",
    "no relevant",
    "cannot find",
    "no documents",
    "not covered",
    "not available",
    "unable to find",
    "don't have that information",
    "cannot provide",
]


# ---------------------------------------------------------------------------
# Retrieval metrics
# ---------------------------------------------------------------------------

def retrieval_metrics(
    sources: list[dict],
    relevant_docs: list[str],
    k: int = 5,
) -> dict:
    """
    Compute hit@k, MRR, and context_recall from a sources list.

    relevant_docs: list of filename substrings (e.g. ["hr_policy"]).
      A source matches if any pattern is a case-insensitive substring of
      source["filename"].

    Returns dict with hit_at_k (bool|None), mrr (float|None),
    context_recall (float|None), k (int).
    None values when relevant_docs is empty (nothing to evaluate against).
    """
    if not relevant_docs:
        return {"hit_at_k": None, "mrr": None, "context_recall": None, "k": k}

    top_k = sources[:k]
    # Match against filename OR domain field (domain covers CUAD/contract titles
    # where the filename is derived from the contract title, not a domain prefix)
    source_targets = [
        s.get("filename", "") + "|" + s.get("domain", "")
        for s in top_k
    ]

    def matches(target: str) -> bool:
        return any(pat.lower() in target.lower() for pat in relevant_docs)

    hit = any(matches(t) for t in source_targets)

    mrr = 0.0
    for rank, target in enumerate(source_targets, start=1):
        if matches(target):
            mrr = 1.0 / rank
            break

    recall_hits = sum(
        1 for pat in relevant_docs
        if any(pat.lower() in t.lower() for t in source_targets)
    )
    context_recall = recall_hits / len(relevant_docs)

    return {
        "hit_at_k": hit,
        "mrr": round(mrr, 3),
        "context_recall": round(context_recall, 3),
        "k": k,
    }


# ---------------------------------------------------------------------------
# Abstention helpers (heuristic, no LLM needed)
# ---------------------------------------------------------------------------

def is_abstention(answer: str) -> bool:
    """Heuristic: does the answer indicate no relevant information was found?"""
    lower = answer.lower()
    return any(phrase in lower for phrase in ABSTENTION_PHRASES)


def abstention_correct(answer: str, expect_abstention: bool) -> bool:
    """Did the model correctly abstain/answer relative to the expectation?"""
    return is_abstention(answer) == expect_abstention


# ---------------------------------------------------------------------------
# Citation coverage (G6 — heuristic, no LLM needed)
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CITATION_MARKER = re.compile(r"\[(\d+)\]")


def citation_coverage(answer: str, num_sources: int) -> float | None:
    """
    Fraction of answer sentences carrying at least one valid [n] citation
    marker (1 <= n <= num_sources, matching the numbered context chunks the
    prompt instructs the model to cite).

    Returns None when the metric is not applicable: empty answer, no sources
    (nothing to cite), or an abstention (abstaining answers must NOT cite).

    Heuristic sentence split (punctuation or newline boundaries); fragments
    shorter than 3 words are ignored so headings, list bullets like "1." and
    stray markers don't distort the denominator. Markers pointing outside the
    source range ([7] with 3 sources) don't count as citations.
    """
    if not answer or num_sources <= 0 or is_abstention(answer):
        return None

    sentences = [
        s.strip() for s in _SENTENCE_SPLIT.split(answer)
        if len(s.split()) >= 3
    ]
    if not sentences:
        return None

    cited = sum(
        1 for s in sentences
        if any(1 <= int(n) <= num_sources for n in _CITATION_MARKER.findall(s))
    )
    return round(cited / len(sentences), 3)


# ---------------------------------------------------------------------------
# LLM judge — ragas faithfulness + answer_relevancy (G4)
# ---------------------------------------------------------------------------
# Replaces the old hand-rolled "score: N" prompts with ragas's tuned metrics:
#   - Faithfulness: decomposes the answer into claims, verifies each against
#     retrieved_contexts (structured-output extraction via `instructor` — no
#     regex score-parsing).
#   - AnswerRelevancy: generates hypothetical questions from the answer and
#     compares their embedding similarity to the original question.
# Both wired to the LMForge judge (OpenAI-compatible /v1) — chat model for
# the LLM, qwen3-embed:0.6b:8bit for AnswerRelevancy's embedding step.
# Non-thinking mode forced via chat_template_kwargs so structured-output
# extraction stays clean (LMForge/vLLM qwen3 models honor this field).
#
# Live-verified against qwen3.5:4b:6bit on canned samples (faithful answer →
# 1.0, unsupported claim → 0.0, on-topic → 0.85, off-topic → 0.33, abstention
# → 0.0) — see tasks/05-2026-gaps-plan.md G4. Required pinning
# langchain-community<0.3.20 in tests/integration/pyproject.toml (ragas 0.4.3
# imports a langchain_community.chat_models.vertexai path removed in newer
# langchain-community releases).
_JUDGE_EMBED_MODEL = "qwen3-embed:0.6b:8bit"

# Cache built (llm, embeddings)-backed metric instances per (judge_url,
# judge_model) — constructing the OpenAI clients + instructor adapter per
# query would be wasteful across a whole suite run.
_JUDGE_METRICS_CACHE: dict[tuple[str, str], tuple] = {}


def _get_judge_metrics(judge_url: str, judge_model: str):
    key = (judge_url, judge_model)
    if key not in _JUDGE_METRICS_CACHE:
        from openai import AsyncOpenAI
        from ragas.embeddings.base import embedding_factory
        from ragas.llms import llm_factory
        from ragas.metrics.collections import AnswerRelevancy, Faithfulness

        chat_client = AsyncOpenAI(base_url=judge_url, api_key="none")
        embed_client = AsyncOpenAI(base_url=judge_url, api_key="none")
        llm = llm_factory(
            judge_model,
            client=chat_client,
            # ragas defaults max_tokens=1024 — too small for Faithfulness'
            # claim decomposition on long answers (contract queries hit "output
            # is incomplete due to a max_tokens length limit" at 1024).
            max_tokens=4096,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        embeddings = embedding_factory("openai", _JUDGE_EMBED_MODEL, client=embed_client)
        _JUDGE_METRICS_CACHE[key] = (
            Faithfulness(llm=llm),
            AnswerRelevancy(llm=llm, embeddings=embeddings),
        )
    return _JUDGE_METRICS_CACHE[key]


def generation_judge(
    question: str,
    answer: str,
    sources: list[dict],
    expect_abstention: bool,
    judge_url: str,
    judge_model: str,
    # Faithfulness decomposes the answer into claims and verifies each — several
    # sequential LLM calls. On-device judges need minutes, not seconds, for long
    # contract-style answers (45s produced blank TimeoutError failures on every
    # CUAD query in the Aug 2026 full-gate run).
    timeout: float = 240.0,
) -> dict:
    """
    Score faithfulness + answer_relevancy via ragas, pointed at the LMForge judge.

    judge_url: base URL of the judge endpoint (e.g. http://localhost:11430/v1).
    judge_model: model ID to use (e.g. qwen3.5:4b:6bit).

    Returns faithfulness (float|None), answer_relevancy (float|None),
    abstention_correct (bool), judge_error (str|None) — same shape as before
    the G4 ragas adoption, so callers (run_tests.py) are unaffected.

    abstention_correct is always computed heuristically (no LLM required —
    unchanged by G4). When the model correctly abstained, faithfulness/
    answer_relevancy are left None rather than judged: a generic claim-
    verification metric has nothing to check in a "no relevant documents"
    response, and ragas scores it 0.0 (verified live) — which would wrongly
    drag down the average for behaving correctly. This mirrors how
    retrieval_metrics() already returns None fields when not applicable.
    """
    correct_abstention = abstention_correct(answer, expect_abstention)
    result: dict = {
        "faithfulness": None,
        "answer_relevancy": None,
        "abstention_correct": correct_abstention,
        "judge_error": None,
    }

    if expect_abstention and correct_abstention:
        return result

    if not answer.strip():
        result["judge_error"] = "empty answer — skipped judge"
        return result

    contexts = [s.get("content", "") for s in sources[:5] if s.get("content")] or [
        "(no retrieved chunks)"
    ]

    async def _score() -> tuple[float | None, float | None]:
        faith_metric, rel_metric = _get_judge_metrics(judge_url, judge_model)
        faith_result = await asyncio.wait_for(
            faith_metric.ascore(
                user_input=question, response=answer, retrieved_contexts=contexts
            ),
            timeout=timeout,
        )
        rel_result = await asyncio.wait_for(
            rel_metric.ascore(user_input=question, response=answer),
            timeout=timeout,
        )
        return faith_result.value, rel_result.value

    try:
        faithfulness_val, relevancy_val = asyncio.run(_score())
        result["faithfulness"] = round(faithfulness_val, 3) if faithfulness_val is not None else None
        result["answer_relevancy"] = round(relevancy_val, 3) if relevancy_val is not None else None
    except Exception as e:
        # Always include the exception type: TimeoutError stringifies to "",
        # which produced undebuggable blank "ragas judge failed:" reports.
        result["judge_error"] = f"ragas judge failed: {type(e).__name__}: {e}"

    return result


# ---------------------------------------------------------------------------
# Reranker health check
# ---------------------------------------------------------------------------

_HEALTH_DOCS = [
    "The company allows employees to work remotely up to three days per week.",
    "Annual leave entitlement is 20 days per calendar year.",
]


def reranker_health(
    reranker_url: str,
    reranker_model: str,
    timeout: float = 8.0,
) -> dict:
    """
    POST two dummy docs to the reranker and verify HTTP 200.
    Returns healthy (bool), status_code (int|None), latency_ms (float), error (str|None).
    """
    url = reranker_url.rstrip("/") + "/rerank"
    t0 = time.time()
    try:
        resp = httpx.post(
            url,
            json={
                "model": reranker_model,
                "query": "work from home policy",
                "documents": _HEALTH_DOCS,
                "top_n": 2,
            },
            timeout=timeout,
        )
        latency_ms = round((time.time() - t0) * 1000, 1)
        healthy = resp.status_code == 200
        return {
            "healthy": healthy,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
            "error": None if healthy else resp.text[:200],
        }
    except Exception as e:
        latency_ms = round((time.time() - t0) * 1000, 1)
        return {
            "healthy": False,
            "status_code": None,
            "latency_ms": latency_ms,
            "error": str(e),
        }
