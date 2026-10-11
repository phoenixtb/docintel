"""
Trailing-refusal guard for streamed answers.

Small generation models (qwen3.5:2b) sometimes end a grounded, cited answer
with the prompt's refusal sentence, contradicting what they just said. The
guard holds back any tail of the stream that could be the start of that
sentence. If the stream ends on the sentence after real content, the sentence
is dropped; in every other case the held text is released unchanged, so a
bare refusal still reaches the user.
"""

from ..prompts import REFUSAL_SENTENCE


class TrailingRefusalGuard:
    def __init__(self, sentence: str = REFUSAL_SENTENCE) -> None:
        self._sentence = sentence
        self._held = ""
        self._released_content = False

    def feed(self, text: str) -> str:
        """Return the part of the stream that is safe to emit now."""
        buf = self._held + text
        cut = self._hold_from(buf)
        out, self._held = buf[:cut], buf[cut:]
        if out.strip():
            self._released_content = True
        return out

    def finish(self) -> str:
        """Return whatever is still held, minus a refusal appended to an answer."""
        held, self._held = self._held, ""
        if self._released_content and held.strip() == self._sentence:
            return ""
        return held

    def _hold_from(self, buf: str) -> int:
        """Index where the holdable tail of ``buf`` starts (``len(buf)`` if none).

        The tail is optional whitespace followed by a prefix of the sentence that
        starts at the beginning of ``buf`` or after whitespace.
        """
        first = max(0, len(buf) - len(self._sentence))
        for j in range(first, len(buf)):
            if (j == 0 or buf[j - 1].isspace()) and self._sentence.startswith(buf[j:]):
                return self._whitespace_start(buf, j)
        return self._whitespace_start(buf, len(buf))

    @staticmethod
    def _whitespace_start(buf: str, j: int) -> int:
        while j > 0 and buf[j - 1].isspace():
            j -= 1
        return j
