"""Unit tests for page_router — pure logic, no I/O."""

import pytest
from src.page_router import ExtractionPath, PageProfile, initial_path, should_escalate_to_vlm


def _profile(text_density=0.0, bitmap_ratio=0.0, has_table_hint=False, has_math_hint=False):
    return PageProfile(
        page_index=0,
        text_chars=int(text_density * 100),
        text_density=text_density,
        bitmap_ratio=bitmap_ratio,
        has_table_hint=has_table_hint,
        has_math_hint=has_math_hint,
    )


class TestInitialPath:
    def test_digital_high_text_low_bitmap(self):
        p = _profile(text_density=0.8, bitmap_ratio=0.02)
        assert initial_path(p) == ExtractionPath.DIGITAL

    def test_digital_boundary_exact(self):
        p = _profile(text_density=0.51, bitmap_ratio=0.04)
        assert initial_path(p) == ExtractionPath.DIGITAL

    def test_layout_table_hint(self):
        p = _profile(text_density=0.4, bitmap_ratio=0.2, has_table_hint=True)
        assert initial_path(p) == ExtractionPath.LAYOUT

    def test_tesseract_bitmap_heavy(self):
        p = _profile(text_density=0.02, bitmap_ratio=0.8)
        assert initial_path(p) == ExtractionPath.TESSERACT

    def test_layout_fallback_mixed(self):
        # text_chars > 0 → falls through to layout fallback
        p = PageProfile(
            page_index=0, text_chars=20, text_density=0.2, bitmap_ratio=0.3,
        )
        assert initial_path(p) == ExtractionPath.LAYOUT

    def test_tesseract_when_no_embedded_text(self):
        # text_chars == 0 → fully scanned page, route to Tesseract regardless of bitmap
        p = PageProfile(
            page_index=0, text_chars=0, text_density=0.0, bitmap_ratio=0.10,
        )
        assert initial_path(p) == ExtractionPath.TESSERACT

    def test_layout_text_with_table_overrides_bitmap(self):
        p = _profile(text_density=0.35, bitmap_ratio=0.4, has_table_hint=True)
        assert initial_path(p) == ExtractionPath.LAYOUT

    def test_digital_wins_over_table_hint_when_text_very_high(self):
        # text_density > 0.5 is checked first
        p = _profile(text_density=0.9, bitmap_ratio=0.01, has_table_hint=True)
        assert initial_path(p) == ExtractionPath.DIGITAL


class TestShouldEscalateToVlm:
    def test_hard_failure_minus_one(self):
        escalate, reason = should_escalate_to_vlm(tesseract_conf=-1.0)
        assert escalate is True
        assert reason == "tesseract_failure"

    def test_low_word_ratio_escalates_even_with_high_confidence(self):
        escalate, reason = should_escalate_to_vlm(
            tesseract_conf=85.0, word_validity_ratio=0.55, noise_markers=0,
        )
        assert escalate is True
        assert "low_word_ratio" in reason

    def test_noise_markers_escalate_even_with_high_word_ratio(self):
        # Substitution-style failure: most tokens valid (PDF 2 case) but garbage markers present
        escalate, reason = should_escalate_to_vlm(
            tesseract_conf=85.0, word_validity_ratio=0.93, noise_markers=3,
        )
        assert escalate is True
        assert "noise_markers" in reason

    def test_clean_signals_keep_tesseract(self):
        escalate, reason = should_escalate_to_vlm(
            tesseract_conf=70.0, word_validity_ratio=0.96, noise_markers=0,
        )
        assert escalate is False
        assert reason == "kept_tesseract"

    def test_word_ratio_unavailable_falls_back_to_other_signals(self):
        escalate, _ = should_escalate_to_vlm(
            tesseract_conf=80.0, word_validity_ratio=-1.0, noise_markers=0,
        )
        assert escalate is False
        escalate, reason = should_escalate_to_vlm(
            tesseract_conf=40.0, word_validity_ratio=-1.0, noise_markers=0,
        )
        assert escalate is True
        assert "low_conf" in reason

    def test_low_conf_escalates(self):
        escalate, reason = should_escalate_to_vlm(
            tesseract_conf=45.0, word_validity_ratio=0.95, noise_markers=0,
        )
        assert escalate is True
        assert "low_conf" in reason

    def test_table_hint_escalates(self):
        escalate, reason = should_escalate_to_vlm(
            tesseract_conf=85.0, word_validity_ratio=0.95, noise_markers=0, has_table=True,
        )
        assert escalate is True
        assert reason == "table_hint"

    def test_math_hint_escalates(self):
        escalate, reason = should_escalate_to_vlm(
            tesseract_conf=85.0, word_validity_ratio=0.95, noise_markers=0, has_math=True,
        )
        assert escalate is True
        assert reason == "math_hint"

    def test_noise_threshold_boundary(self):
        # 1 marker tolerated (might be a stray bullet in real text)
        escalate, _ = should_escalate_to_vlm(
            tesseract_conf=80.0, word_validity_ratio=0.95, noise_markers=1,
        )
        assert escalate is False
        # 2 markers → escalate
        escalate, reason = should_escalate_to_vlm(
            tesseract_conf=80.0, word_validity_ratio=0.95, noise_markers=2,
        )
        assert escalate is True
        assert "noise_markers" in reason

    def test_word_ratio_threshold_boundary(self):
        escalate, _ = should_escalate_to_vlm(
            tesseract_conf=80.0, word_validity_ratio=0.80, noise_markers=0,
        )
        assert escalate is False
        escalate, _ = should_escalate_to_vlm(
            tesseract_conf=80.0, word_validity_ratio=0.799, noise_markers=0,
        )
        assert escalate is True

    def test_evaluation_order_failure_first(self):
        # tesseract_failure outranks every other signal
        escalate, reason = should_escalate_to_vlm(
            tesseract_conf=-1.0, word_validity_ratio=0.99, noise_markers=0,
        )
        assert escalate is True
        assert reason == "tesseract_failure"


class TestWordValidityRatio:
    """Quality-gate helper — uses bundled English dictionary."""

    def test_clean_english_text_high_ratio(self):
        from src.extractors.ocr_tesseract import word_validity_ratio
        text = (
            "Someone has set fire to Mr Hick's cottage, but who could it be? "
            "The Find-Outers have their very first case to solve."
        )
        ratio = word_validity_ratio(text)
        if ratio >= 0:
            assert ratio > 0.80, f"clean text should score high, got {ratio:.2f}"

    def test_garbled_ocr_low_ratio(self):
        from src.extractors.ocr_tesseract import word_validity_ratio
        text = "Boheane yee meus xqzpv blarn frob nxtq plok wibble zorf"
        ratio = word_validity_ratio(text)
        if ratio >= 0:
            assert ratio < 0.40, f"garbage tokens should score low, got {ratio:.2f}"

    def test_too_few_tokens_returns_minus_one(self):
        from src.extractors.ocr_tesseract import word_validity_ratio
        assert word_validity_ratio("Hi there.") == -1.0
        assert word_validity_ratio("") == -1.0


class TestNoiseMarkerCount:
    """Structural OCR garbage detector — pure regex, no dependencies."""

    def test_clean_text_no_markers(self):
        from src.extractors.ocr_tesseract import noise_marker_count
        text = (
            "ATX BACK-CONNECT MOTHERBOARD SUPPORT. The case offers broad motherboard "
            "support, including standard ATX, M-ATX, and Mini-ITX form factors."
        )
        assert noise_marker_count(text) == 0

    def test_pdf2_actual_extraction_flagged(self):
        from src.extractors.ocr_tesseract import noise_marker_count
        # The actual stored Tesseract output for PDF 2 (book cover photo)
        text = (
            "‘We Whenever there's a mystery,\n"
            "\\ yee meus are on the case!\n"
            "Boheane has set fire to Mr Hick's cottage, but who\n"
            "could it be? The Find-Outers have their very first\n"
            "» > case to solve = and a list of suspects to investigate.\n"
            "But i it' 's Not easy finding clues with local policeman\n"
            "i Mr Goon getting in the way ..."
        )
        # Expected markers: \, », two standalone "i"s = >= 4
        assert noise_marker_count(text) >= 3

    def test_standalone_backslash(self):
        from src.extractors.ocr_tesseract import noise_marker_count
        assert noise_marker_count("hello \\ world") == 1

    def test_standalone_lowercase_i_counted(self):
        from src.extractors.ocr_tesseract import noise_marker_count
        # standalone lowercase "i" between words is broken tokenization
        assert noise_marker_count("policeman i Mr Goon") == 1

    def test_uppercase_I_not_counted(self):
        from src.extractors.ocr_tesseract import noise_marker_count
        assert noise_marker_count("I think therefore I am") == 0

    def test_lowercase_a_not_counted(self):
        from src.extractors.ocr_tesseract import noise_marker_count
        assert noise_marker_count("This is a test of a sentence") == 0

    def test_bullet_arrow_artifacts(self):
        from src.extractors.ocr_tesseract import noise_marker_count
        assert noise_marker_count("hello » world › foo") == 2
