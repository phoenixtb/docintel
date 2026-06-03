"""
Per-page routing logic — pure functions, no I/O, fully unit-testable.

Routing decision tree:
  text_density > 0.5 AND bitmap < 0.05  → Path A (digital)
  text_density > 0.3 AND table_hint     → Path B (layout + TableFormer)
  bitmap > 0.5 AND text_density < 0.05  → Path C (Tesseract)
  text_chars == 0 (no embedded text)    → Path C (Tesseract — fully scanned page)
  fallback                              → Path B (layout — safest default)

  After Path C (escalate to VLM if ANY of):
    confidence < 0              (hard failure)
    noise_markers >= 2          (substitution garbage: standalone \, », single 'i'…)
    word_validity_ratio < 0.80  (ratio garbage: many tokens not in English dict)
    confidence < 60             (Tesseract self-reported obvious failure)
    has_table OR has_math       (content-type signals — TableFormer/math need VLM)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExtractionPath(str, Enum):
    DIGITAL = "digital"      # Path A: pypdfium2 text layer
    LAYOUT = "layout"        # Path B: Docling layout + TableFormer, no OCR
    TESSERACT = "tesseract"  # Path C: lightweight CPU OCR
    VLM = "vlm"              # Path E: multimodal VLM via LMForge


@dataclass
class PageProfile:
    """Metrics for a single PDF page, derived from pdf_probe.probe_pages()."""
    page_index: int          # 0-based
    text_chars: int          # embedded text character count
    text_density: float      # text_chars / page_area (normalised [0,1])
    bitmap_ratio: float      # non-white pixel fraction [0,1]
    has_table_hint: bool = False   # heuristic: detected grid lines / separator runs
    has_math_hint: bool = False    # heuristic: detected math-like symbols


def initial_path(profile: PageProfile) -> ExtractionPath:
    """
    Decide the primary extraction path for a page based on its profile.

    No I/O. Returns ExtractionPath enum value.
    """
    td = profile.text_density
    bm = profile.bitmap_ratio
    table = profile.has_table_hint

    if td > 0.5 and bm < 0.05:
        return ExtractionPath.DIGITAL

    if td > 0.3 and table:
        return ExtractionPath.LAYOUT

    if bm > 0.5 and td < 0.05:
        return ExtractionPath.TESSERACT

    # No embedded text at all → page is fully scanned; use Tesseract regardless of bitmap coverage
    if profile.text_chars == 0:
        return ExtractionPath.TESSERACT

    # Mixed or ambiguous — Docling layout is the safest fallback
    return ExtractionPath.LAYOUT


def should_escalate_to_vlm(
    tesseract_conf: float,
    word_validity_ratio: float = -1.0,
    noise_markers: int = 0,
    has_table: bool = False,
    has_math: bool = False,
    conf_threshold: float = 60.0,
    word_ratio_threshold: float = 0.80,
    noise_threshold: int = 2,
) -> tuple[bool, str]:
    """
    Decide whether to escalate a Tesseract result to the VLM.

    Quality-first multi-signal gate. Tesseract's self-reported confidence is
    unreliable on stylised / photographed text (it confidently outputs "Boheane"
    for "Someone"), so we layer two orthogonal content checks on top:

      1. word_validity_ratio — fraction of tokens in English dictionary.
         Catches large-scale garbage; insensitive to small-but-meaningful errors.
      2. noise_markers — count of structural OCR artifacts (standalone backslash,
         bullet glyphs, lone single letters). Catches substitution-style errors
         that don't move the dictionary ratio much.

    Args:
        tesseract_conf:        Average Tesseract word confidence [0, 100].
                               -1 = hard failure → always escalate.
        word_validity_ratio:   Fraction of letter-tokens recognised as English words.
                               -1 = check could not run (skip this signal).
        noise_markers:         Count of strong garbage markers in the OCR output.
        has_table:             Whether the page appears to contain tabular content.
        has_math:              Whether the page appears to contain mathematical notation.
        conf_threshold:        Minimum acceptable Tesseract confidence (default 60).
        word_ratio_threshold:  Minimum acceptable word-validity ratio (default 0.80).
        noise_threshold:       Maximum tolerable noise marker count (default 2).

    Returns (escalate, reason). `reason` is a short token suitable for logs/metrics.
    """
    if tesseract_conf < 0:
        return True, "tesseract_failure"
    if noise_markers >= noise_threshold:
        return True, f"noise_markers({noise_markers})"
    if word_validity_ratio >= 0 and word_validity_ratio < word_ratio_threshold:
        return True, f"low_word_ratio({word_validity_ratio:.2f})"
    if tesseract_conf < conf_threshold:
        return True, f"low_conf({tesseract_conf:.0f})"
    if has_table:
        return True, "table_hint"
    if has_math:
        return True, "math_hint"
    return False, "kept_tesseract"
