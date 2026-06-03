"""
Path C: Tesseract OCR for bitmap-heavy pages.

Renders the page via pypdfium2, runs pytesseract for:
  - confidence data (legacy signal, kept for hard-failure detection)
  - full text extraction
  - dictionary-word-validity ratio (catches ratio-style garbage)
  - noise marker count (catches substitution-style garbage that ratio misses)

Returns (text, avg_confidence, word_validity_ratio, noise_count).
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Tokens with letters only (ignores numbers, punctuation, weird OCR artifacts).
# Apostrophes are kept inside the token so "Hick's", "it's" count as one token.
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

# Common English contractions / proper-noun affixes that aren't in dict but are valid.
_CONTRACTION_SUFFIXES = {"s", "t", "d", "re", "ve", "ll", "m"}

# Strong OCR garbage markers — these essentially never appear in clean English text.
_NOISE_CHARS = set("\\|~^")
_NOISE_ARTIFACTS = set("»›«‹•►▪◆◇★☆")
_VALID_SINGLE_LETTERS = {"I", "a", "A"}  # "I" pronoun, "a"/"A" article
_TOKEN_STRIP = ".,;:!?\"'()[]{}<>“”‘’"


@lru_cache(maxsize=1)
def _get_spellchecker():
    """Lazily load the bundled English dictionary (one-time cost ~150ms)."""
    try:
        from spellchecker import SpellChecker
        return SpellChecker(language="en", distance=1)
    except Exception as exc:
        logger.warning("pyspellchecker not available — word-validity check disabled: %s", exc)
        return None


def word_validity_ratio(text: str) -> float:
    """
    Fraction of letter-only tokens that are valid English words.

    Returns a value in [0, 1], or -1.0 if the dictionary is unavailable
    or the text has too few tokens to form a meaningful ratio (< 5 tokens).

    Garbled OCR ("Boheane", "yee meus", "\\") scores low because the wrong
    tokens aren't in the dictionary; clean text scores high regardless of
    Tesseract's reported confidence.
    """
    spell = _get_spellchecker()
    if spell is None:
        return -1.0

    tokens = _WORD_RE.findall(text or "")
    # Filter out single-letter junk tokens but keep "I", "a"
    tokens = [t.lower() for t in tokens if len(t) >= 2 or t.lower() in {"i", "a"}]
    if len(tokens) < 5:
        # Too little signal — don't make a quality call from < 5 words
        return -1.0

    # Strip contraction suffixes for the dictionary lookup ("hick's" → "hick")
    bases = []
    for tok in tokens:
        if "'" in tok:
            head, _, tail = tok.partition("'")
            if tail in _CONTRACTION_SUFFIXES:
                bases.append(head)
                continue
        bases.append(tok)

    unknown = spell.unknown(bases)
    valid_count = len(bases) - len(unknown)
    return valid_count / len(bases)


def noise_marker_count(text: str) -> int:
    """
    Count strong OCR garbage markers — patterns that essentially never appear
    in clean English text. Complements word_validity_ratio for substitution-style
    errors where most tokens are valid but the *meaning-carrying* ones are wrong.

    Markers counted (each occurrence = +1):
      - Standalone backslash / pipe / tilde / caret tokens
      - Bullet, arrow, decorative-quote artifact tokens (», ›, •, etc.)
      - Standalone single-letter tokens other than "I" / "a" / "A"
        (real text never has lone "i", "b", "c"... — they're broken tokenisations)

    A count >= 2 is a strong signal that Tesseract output is unreliable
    regardless of its self-reported confidence.
    """
    if not text:
        return 0

    count = 0
    for tok in text.split():
        core = tok.strip(_TOKEN_STRIP)
        if len(core) != 1:
            continue
        if core in _NOISE_CHARS or core in _NOISE_ARTIFACTS:
            count += 1
        elif core.isalpha() and core not in _VALID_SINGLE_LETTERS:
            count += 1
    return count


def extract_tesseract(
    path: Path, page_index: int, dpi: int = 150
) -> tuple[str, float, float, int]:
    """
    OCR a single PDF page (0-based index) with Tesseract.

    Returns (text, avg_confidence, word_validity_ratio, noise_count).
      avg_confidence:       [0, 100]; -1 = hard failure (always escalate).
      word_validity_ratio:  [0, 1]; -1 = check could not run.
      noise_count:          int >= 0; count of strong OCR garbage markers.
    """
    try:
        import pypdfium2 as pdfium
        import pytesseract
        from PIL import Image
    except ImportError as e:
        logger.warning("tesseract dependencies not available: %s", e)
        return "", -1.0, -1.0, 0

    try:
        doc = pdfium.PdfDocument(str(path))
        if page_index >= len(doc):
            doc.close()
            return "", -1.0, -1.0, 0

        page = doc[page_index]
        scale = dpi / 72.0
        bitmap = page.render(scale=scale)
        pil_img: Image.Image = bitmap.to_pil()
        bitmap.close()
        page.close()
        doc.close()
    except Exception as exc:
        logger.warning("page render failed for page %d of %s: %s", page_index, path, exc)
        return "", -1.0, -1.0, 0

    try:
        data = pytesseract.image_to_data(pil_img, output_type=pytesseract.Output.DICT)
        confs = [int(c) for c in data["conf"] if int(c) >= 0]
        avg_conf = sum(confs) / len(confs) if confs else 0.0

        text = pytesseract.image_to_string(pil_img).strip()
        wv_ratio = word_validity_ratio(text)
        noise = noise_marker_count(text)
        return text, avg_conf, wv_ratio, noise
    except Exception as exc:
        logger.warning("tesseract OCR failed for page %d of %s: %s", page_index, path, exc)
        return "", -1.0, -1.0, 0
