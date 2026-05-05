"""
Tests for extractor modules.

- digital / layout: require pypdfium2 / docling — skipped if not available
- tesseract: skipped if pytesseract / tesseract binary not present
- vlm: mocks httpx — always runs
"""

from __future__ import annotations

import asyncio
import unittest.mock as mock
from pathlib import Path

import pytest


# ── digital ──────────────────────────────────────────────────────────────────

class TestDigitalExtractor:
    def test_returns_string(self, tmp_path):
        pytest.importorskip("pypdfium2")
        from src.extractors.digital import extract_digital

        # A non-existent path should return "" without raising
        result = extract_digital(tmp_path / "missing.pdf", page_index=0)
        assert isinstance(result, str)

    def test_out_of_range_page(self, tmp_path):
        pytest.importorskip("pypdfium2")
        from src.extractors.digital import extract_digital

        # pypdfium2 raises on open for non-PDF; function should catch and return ""
        fake = tmp_path / "fake.pdf"
        fake.write_bytes(b"not a pdf")
        result = extract_digital(fake, page_index=999)
        assert result == ""


# ── tesseract ─────────────────────────────────────────────────────────────────

class TestTesseractExtractor:
    def test_missing_file_returns_empty_and_neg_conf(self, tmp_path):
        pytest.importorskip("pytesseract")
        pytest.importorskip("pypdfium2")
        from src.extractors.ocr_tesseract import extract_tesseract

        text, conf, wv, noise = extract_tesseract(tmp_path / "missing.pdf", page_index=0)
        assert text == ""
        assert conf == -1.0
        assert wv == -1.0
        assert noise == 0

    def test_invalid_pdf_returns_empty_and_neg_conf(self, tmp_path):
        pytest.importorskip("pytesseract")
        pytest.importorskip("pypdfium2")
        from src.extractors.ocr_tesseract import extract_tesseract

        fake = tmp_path / "fake.pdf"
        fake.write_bytes(b"not a pdf")
        text, conf, wv, noise = extract_tesseract(fake, page_index=0)
        assert text == ""
        assert conf == -1.0
        assert wv == -1.0
        assert noise == 0


# ── vlm (mocked) ──────────────────────────────────────────────────────────────

class TestVlmExtractor:
    def _make_semaphore(self):
        return asyncio.Semaphore(1)

    @pytest.mark.asyncio
    async def test_success_path(self, tmp_path):
        from src.extractors.vlm import extract_vlm

        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"dummy")

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Extracted text"}}]
        }
        mock_response.raise_for_status = mock.MagicMock()

        with mock.patch("src.extractors.vlm._render_page_png", return_value=b"fakepng"):
            with mock.patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = mock.AsyncMock()
                mock_client.post = mock.AsyncMock(return_value=mock_response)
                mock_client_cls.return_value.__aenter__ = mock.AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = mock.AsyncMock(return_value=False)

                result = await extract_vlm(
                    path=fake_pdf,
                    page_index=0,
                    vlm_url="http://localhost:11430/v1",
                    vlm_model="qwen2.5-vl:3b:4bit",
                    semaphore=self._make_semaphore(),
                )

        assert result == "Extracted text"

    @pytest.mark.asyncio
    async def test_render_failure_returns_empty(self, tmp_path):
        from src.extractors.vlm import extract_vlm

        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"dummy")

        with mock.patch("src.extractors.vlm._render_page_png", return_value=None):
            result = await extract_vlm(
                path=fake_pdf,
                page_index=0,
                vlm_url="http://localhost:11430/v1",
                vlm_model="qwen2.5-vl:3b:4bit",
                semaphore=self._make_semaphore(),
                max_retries=1,
            )

        assert result == ""

    @pytest.mark.asyncio
    async def test_503_triggers_retry(self, tmp_path):
        from src.extractors.vlm import extract_vlm

        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"dummy")

        resp_503 = mock.MagicMock()
        resp_503.status_code = 503
        resp_503.headers = {"Retry-After": "0"}

        resp_200 = mock.MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"choices": [{"message": {"content": "recovered"}}]}
        resp_200.raise_for_status = mock.MagicMock()

        with mock.patch("src.extractors.vlm._render_page_png", return_value=b"fakepng"):
            with mock.patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = mock.AsyncMock()
                mock_client.post = mock.AsyncMock(side_effect=[resp_503, resp_200])
                mock_client_cls.return_value.__aenter__ = mock.AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = mock.AsyncMock(return_value=False)

                result = await extract_vlm(
                    path=fake_pdf,
                    page_index=0,
                    vlm_url="http://localhost:11430/v1",
                    vlm_model="qwen2.5-vl:3b:4bit",
                    semaphore=self._make_semaphore(),
                    max_retries=3,
                )

        assert result == "recovered"
