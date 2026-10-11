"""TrailingRefusalGuard: drop a refusal appended to an answer, keep everything else."""

import re

import pytest

from src.pipelines.answer_guard import TrailingRefusalGuard
from src.prompts import REFUSAL_SENTENCE

ANSWER = "The fund pays the Blockchain Administrator 0.20% of average net assets [1]."


def _chunkings(text: str) -> list[list[str]]:
    """The same text split the ways a model's tokens might split it."""
    return [list(text), re.findall(r"\S+|\s+", text), [text]]


def _run(chunks: list[str]) -> str:
    guard = TrailingRefusalGuard()
    return "".join(guard.feed(c) for c in chunks) + guard.finish()


@pytest.mark.unit
@pytest.mark.parametrize("chunks", _chunkings(f"{ANSWER}\n\n{REFUSAL_SENTENCE}"))
def test_refusal_appended_to_an_answer_is_dropped(chunks):
    assert _run(chunks).strip() == ANSWER


@pytest.mark.unit
@pytest.mark.parametrize("chunks", _chunkings(REFUSAL_SENTENCE))
def test_bare_refusal_is_kept(chunks):
    assert _run(chunks) == REFUSAL_SENTENCE


@pytest.mark.unit
@pytest.mark.parametrize("chunks", _chunkings(f"{REFUSAL_SENTENCE} However, chunk [2] lists the fee."))
def test_refusal_followed_by_more_text_is_kept(chunks):
    assert _run(chunks) == f"{REFUSAL_SENTENCE} However, chunk [2] lists the fee."


@pytest.mark.unit
@pytest.mark.parametrize("chunks", _chunkings(f"{ANSWER} I don't have the exact date, see [2]."))
def test_text_that_only_starts_like_the_refusal_is_kept(chunks):
    assert _run(chunks) == f"{ANSWER} I don't have the exact date, see [2]."


@pytest.mark.unit
@pytest.mark.parametrize("chunks", _chunkings(ANSWER))
def test_ordinary_answer_passes_through_unchanged(chunks):
    assert _run(chunks) == ANSWER


@pytest.mark.unit
def test_only_the_possible_start_of_the_refusal_is_held_back():
    guard = TrailingRefusalGuard()
    assert guard.feed(ANSWER + " I don") == ANSWER
    assert guard.feed("'t know who signed it.") == " I don't know who signed it."
    assert guard.finish() == ""
