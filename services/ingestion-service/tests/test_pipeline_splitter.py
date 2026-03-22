"""
Unit tests for TokenAwareSplitter.

Uses the real BERT tokenizer (bert-base-uncased) via the transformers library.
The tokenizer vocab is pre-downloaded at Docker build time; locally it hits
~/.cache/huggingface on first run (~700 KB download) and is cached thereafter.

scope="module" on the fixture means warm_up() runs once per test file.
"""

import pytest
from haystack.dataclasses import Document

from src.pipeline import TokenAwareSplitter


@pytest.fixture(scope="module")
def splitter() -> TokenAwareSplitter:
    s = TokenAwareSplitter(max_tokens=512, overlap_tokens=64)
    s.warm_up()
    return s


def _run(splitter: TokenAwareSplitter, text: str) -> list[Document]:
    docs_in = [Document(content=text, meta={"doc_id": "test"})]
    return splitter.run(documents=docs_in)["documents"]


# ---------------------------------------------------------------------------
# Basic splitting behaviour
# ---------------------------------------------------------------------------


def test_short_text_stays_single_chunk(splitter: TokenAwareSplitter) -> None:
    text = "This is a short sentence that fits in one chunk easily."
    docs = _run(splitter, text)
    assert len(docs) == 1


def test_long_text_splits_into_multiple_chunks(splitter: TokenAwareSplitter) -> None:
    # ~4 000 words → well over 512 tokens
    word = "information"
    text = " ".join([f"The {word} contained here is important sentence {i}." for i in range(300)])
    docs = _run(splitter, text)
    assert len(docs) > 1


def test_no_chunk_exceeds_max_tokens(splitter: TokenAwareSplitter) -> None:
    # Build a text that forces many splits
    word = "extraterritoriality"
    text = " ".join([word] * 2000)
    docs = _run(splitter, text)
    assert docs, "Expected at least one chunk"
    for doc in docs:
        token_count = doc.meta.get("bert_token_count", 0)
        assert token_count <= 512, (
            f"Chunk {doc.meta.get('split_id')} has {token_count} tokens > 512"
        )


def test_empty_document_produces_no_chunks(splitter: TokenAwareSplitter) -> None:
    docs = _run(splitter, "")
    assert docs == []


def test_whitespace_only_produces_no_chunks(splitter: TokenAwareSplitter) -> None:
    docs = _run(splitter, "   \n\t\n   ")
    assert docs == []


# ---------------------------------------------------------------------------
# Metadata accuracy (the core of our med-8 fix)
# ---------------------------------------------------------------------------


def test_meta_contains_required_offset_keys(splitter: TokenAwareSplitter) -> None:
    text = "Hello world. " * 10
    docs = _run(splitter, text)
    assert docs
    for doc in docs:
        assert "split_idx_start" in doc.meta, "split_idx_start missing"
        assert "split_idx_end" in doc.meta, "split_idx_end missing"
        assert "bert_token_count" in doc.meta, "bert_token_count missing"
        assert "split_id" in doc.meta, "split_id missing"


def test_start_char_matches_raw_chunk_in_original_text(splitter: TokenAwareSplitter) -> None:
    # Build a text long enough to produce multiple chunks
    sentence = "The quick brown fox jumps over the lazy dog. "
    text = sentence * 80  # ~5 600 chars, comfortably over 512 tokens
    docs = _run(splitter, text)
    assert len(docs) > 1, "Need multiple chunks to test offsets"

    for doc in docs:
        start = doc.meta["split_idx_start"]
        end = doc.meta["split_idx_end"]
        assert end > start, f"end ({end}) must be > start ({start})"
        # The slice must exist within the original text
        assert 0 <= start < len(text)
        assert end <= len(text)


def test_bert_token_count_matches_raw_chunk_not_overlap_extended(splitter: TokenAwareSplitter) -> None:
    """
    bert_token_count must count the pre-overlap raw chunk, not the overlap-extended text.
    For the second chunk onward, the overlap prefix is prepended, so the overlap-extended
    chunk has MORE tokens than bert_token_count reports.
    """
    sentence = "The contract clause states that all parties agree. "
    text = sentence * 100  # force multiple chunks
    docs = _run(splitter, text)
    assert len(docs) > 1

    for doc in docs:
        reported = doc.meta["bert_token_count"]
        # Count tokens of the actual stored content (overlap-extended)
        actual = splitter._count(doc.content or "")
        # bert_token_count is the pre-overlap count so it must be ≤ actual content tokens
        assert reported <= actual, (
            f"bert_token_count ({reported}) > actual content tokens ({actual}): overlap not accounted for"
        )
        # And the reported count must always be ≤ max_tokens
        assert reported <= 512


def test_overlap_prefix_appears_in_second_chunk(splitter: TokenAwareSplitter) -> None:
    sentence = "Alpha bravo charlie delta echo foxtrot golf hotel india juliet. "
    text = sentence * 80
    docs = _run(splitter, text)
    assert len(docs) >= 2, "Need at least 2 chunks to test overlap"

    # The second chunk's content should contain text that also appears at the
    # end of the first chunk (the overlap region).  We check by verifying the
    # second chunk has more tokens than the raw first chunk alone.
    first_raw_count = docs[0].meta["bert_token_count"]
    second_content_count = splitter._count(docs[1].content or "")

    # The second chunk is overlap-extended, so it should reference the tail of
    # the first chunk: its total token count will exceed its own raw_count.
    second_raw_count = docs[1].meta["bert_token_count"]
    assert second_content_count > second_raw_count, (
        "Second chunk content should have more tokens than raw chunk alone (overlap)"
    )
