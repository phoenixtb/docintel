"""
LmforgeReranker against the LMForge rerank contract (docs/contracts/reranker.md):
probability scores pass through; every failure mode degrades with a reason and
keeps retrieval order — never misread or dropped documents.
"""

import httpx
import pytest
from haystack import Document

from src.components import reranker as reranker_module
from src.components.reranker import LmforgeReranker

DOCS = [
    Document(id="a", content="Employees accrue annual leave", score=0.031),
    Document(id="b", content="The fund shall pay the blockchain administrator a fee", score=0.030),
    Document(id="c", content="Franchise royalty of 5% of gross sales", score=0.029),
]


def _respond(monkeypatch, status=200, json=None, exc=None):
    calls = []

    def fake_post(url, json=None, timeout=None, **_):  # noqa: A002 - mirrors httpx.post
        calls.append({"url": url, "json": json, "timeout": timeout})
        if exc is not None:
            raise exc
        return httpx.Response(status, json=json_body, request=httpx.Request("POST", url))

    json_body = json
    monkeypatch.setattr(reranker_module.httpx, "post", fake_post)
    return calls


def test_probability_scores_are_used_as_is_and_sorted(monkeypatch):
    calls = _respond(
        monkeypatch,
        json={
            "score_type": "probability",
            "results": [
                {"index": 0, "relevance_score": 0.00001},
                {"index": 1, "relevance_score": 0.22},
            ],
        },
    )

    out = LmforgeReranker(url="http://lmforge/v1", top_k=2).run(query="q", documents=DOCS)

    assert out["reranker_degraded"] is False and out["degraded_reason"] is None
    assert [(d.id, d.score) for d in out["documents"]] == [("b", 0.22), ("a", 0.00001)]
    assert calls[0]["url"] == "http://lmforge/v1/rerank"
    assert calls[0]["json"]["top_n"] == 2


@pytest.mark.parametrize("score_type", [None, "logit", "sigmoid"])
def test_undeclared_or_other_score_scale_degrades_instead_of_being_misread(monkeypatch, score_type):
    body = {"results": [{"index": 1, "relevance_score": 0.66}]}
    if score_type:
        body["score_type"] = score_type
    _respond(monkeypatch, json=body)

    out = LmforgeReranker(top_k=2).run(query="q", documents=DOCS)

    assert out["reranker_degraded"] is True
    assert out["degraded_reason"] == "unsupported_score_contract"
    assert [d.id for d in out["documents"]] == ["a", "b"]  # retrieval order, capped to top_k


@pytest.mark.parametrize(
    "status, code",
    [(400, "query_too_long"), (400, "input_too_long"), (422, "reranker_unusable")],
)
def test_lmforge_error_codes_degrade_with_that_code(monkeypatch, status, code):
    _respond(
        monkeypatch,
        status=status,
        json={
            "error": {"message": "x", "type": "invalid_request_error", "param": None, "code": code}
        },
    )

    out = LmforgeReranker().run(query="q", documents=DOCS)

    assert out["reranker_degraded"] is True
    assert out["degraded_reason"] == code
    assert [d.score for d in out["documents"]] == [0.031, 0.030, 0.029]  # fused scores kept


def test_unknown_error_reports_the_http_status(monkeypatch):
    _respond(monkeypatch, status=500, json={"error": {"message": "boom", "code": None}})

    assert LmforgeReranker().run(query="q", documents=DOCS)["degraded_reason"] == "http_500"


def test_transport_failure_degrades_as_unavailable(monkeypatch):
    _respond(monkeypatch, exc=httpx.ConnectTimeout("timed out"))

    out = LmforgeReranker().run(query="q", documents=DOCS)

    assert out["reranker_degraded"] is True
    assert out["degraded_reason"] == "unavailable"
    assert len(out["documents"]) == 3


def test_truncated_documents_are_logged_and_still_scored(monkeypatch, caplog):
    _respond(
        monkeypatch,
        json={
            "score_type": "probability",
            "meta": {"truncated_documents": [2]},
            "results": [{"index": 2, "relevance_score": 0.4}],
        },
    )

    with caplog.at_level("INFO"):
        out = LmforgeReranker().run(query="q", documents=DOCS)

    assert out["reranker_degraded"] is False
    assert out["documents"][0].id == "c"
    assert "truncated 1 long document" in caplog.text


def test_no_documents_makes_no_call(monkeypatch):
    calls = _respond(monkeypatch, json={})

    out = LmforgeReranker().run(query="q", documents=[])

    assert out == {"documents": [], "reranker_degraded": False, "degraded_reason": None}
    assert calls == []
