"""
RAG Quality Metrics (Stage 1)
==============================
Stage-split evaluation metrics for the DocIntel RAG pipeline.

Retrieval metrics (computed from sources[], no network calls):
  hit_at_k       — >=1 source filename contains any relevant_docs pattern
  mrr            — 1/rank of first relevant source (0.0 if none)
  context_recall — fraction of relevant_docs patterns matched by >=1 source

Generation metrics (LLM-judge, requires a running LMForge / OpenAI-compatible endpoint):
  faithfulness        — answer claims grounded in provided sources (0.0–1.0)
  answer_relevancy    — answer addresses the question (0.0–1.0)
  abstention_correct  — True iff LLM correctly abstained/answered per expect_abstention

Health:
  reranker_health     — reranker endpoint is alive and returns HTTP 200

Usage:
  from metrics import retrieval_metrics, generation_judge, reranker_health, abstention_correct
"""

import re
import time
from typing import Optional

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
# LLM-judge prompts
# ---------------------------------------------------------------------------

_FAITHFULNESS_PROMPT = """\
You are a strict RAG evaluation judge. Rate the faithfulness of the answer.

Faithfulness: Does the answer ONLY make claims supported by the retrieved chunks?
Do NOT penalize for what is missing — only penalize unsupported claims.
If the answer says "I don't have information / no relevant documents", score 5.

Score 1: Major unsupported claims.
Score 2: Several unsupported claims.
Score 3: Mostly grounded, minor unsupported claims.
Score 4: Nearly all grounded.
Score 5: Fully grounded or a correct abstention.

QUESTION: {question}

RETRIEVED CHUNKS:
{chunks}

ANSWER: {answer}

Respond with ONLY: score: <1-5>
One-sentence justification."""

_RELEVANCY_PROMPT = """\
You are a strict RAG evaluation judge. Rate the answer relevancy.

Answer Relevancy: Does the answer address what was asked?
If the question cannot be answered from context and the answer correctly says so, score 5.

Score 1: Completely off-topic.
Score 2: Tangentially addresses the question.
Score 3: Partially addresses the question.
Score 4: Mostly addresses the question.
Score 5: Directly and completely addresses the question.

QUESTION: {question}

ANSWER: {answer}

Respond with ONLY: score: <1-5>
One-sentence justification."""


def _extract_score(text: str) -> Optional[float]:
    """Extract 'score: N' and normalize to [0, 1]."""
    m = re.search(r"score\s*:\s*([1-5](?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        raw = float(m.group(1))
        return round((raw - 1.0) / 4.0, 3)  # [1,5] → [0,1]
    return None


# ---------------------------------------------------------------------------
# LLM judge (requires LMForge or any OpenAI-compatible /chat/completions)
# ---------------------------------------------------------------------------

def generation_judge(
    question: str,
    answer: str,
    sources: list[dict],
    expect_abstention: bool,
    judge_url: str,
    judge_model: str,
    timeout: float = 45.0,
) -> dict:
    """
    Call an LLM judge for faithfulness, answer_relevancy, and abstention_correct.

    judge_url: base URL of the judge endpoint (e.g. http://localhost:11430/v1).
    judge_model: model ID to use (e.g. qwen3.5:4b).

    Returns faithfulness (float|None), answer_relevancy (float|None),
    abstention_correct (bool), judge_error (str|None).
    abstention_correct is always computed heuristically (no LLM required).
    """
    chunks_text = "\n\n".join(
        f"[{i+1}] {s.get('content', '')[:400]}" for i, s in enumerate(sources[:5])
    ) or "(no retrieved chunks)"

    result: dict = {
        "faithfulness": None,
        "answer_relevancy": None,
        "abstention_correct": abstention_correct(answer, expect_abstention),
        "judge_error": None,
    }

    try:
        chat_url = judge_url.rstrip("/") + "/chat/completions"
        # Disable chain-of-thought thinking to get compact "score: N" responses.
        # LMForge / vLLM qwen3 models support chat_template_kwargs.enable_thinking.
        _extra = {"chat_template_kwargs": {"enable_thinking": False}}

        with httpx.Client(timeout=timeout) as client:
            # Faithfulness
            resp = client.post(
                chat_url,
                json={
                    "model": judge_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _FAITHFULNESS_PROMPT.format(
                                question=question,
                                chunks=chunks_text,
                                answer=answer,
                            ),
                        }
                    ],
                    "max_tokens": 150,
                    "temperature": 0.0,
                    **_extra,
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            result["faithfulness"] = _extract_score(text)

            # Answer relevancy
            resp2 = client.post(
                chat_url,
                json={
                    "model": judge_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _RELEVANCY_PROMPT.format(
                                question=question,
                                answer=answer,
                            ),
                        }
                    ],
                    "max_tokens": 150,
                    "temperature": 0.0,
                    **_extra,
                },
            )
            resp2.raise_for_status()
            text2 = resp2.json()["choices"][0]["message"]["content"]
            result["answer_relevancy"] = _extract_score(text2)

    except httpx.HTTPError as e:
        result["judge_error"] = f"HTTP {e}"
    except Exception as e:
        result["judge_error"] = str(e)

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
