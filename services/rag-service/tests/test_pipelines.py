"""
Pipeline tests — verify generation kwargs parity between streaming and non-streaming paths.

These are unit tests: they build the pipeline with a mocked Settings object and
inspect the LLM component's configuration without connecting to any live service.
"""

import os
import pytest
from unittest.mock import MagicMock, patch


def _mock_settings(
    llm_temperature: float = 0.1,
    llm_max_tokens: int = 1024,
    llm_frequency_penalty: float = 0.3,
    qdrant_url: str = "http://localhost:6333",
    llm_model: str = "test-model",
    llm_chat_url: str = "http://localhost:11434/v1",
    llm_api_key: str = "none",
    opa_url: str = "http://localhost:8181",
    reranker_url: str = "http://localhost:7997",
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
    rag_reranker_top_k: int = 10,
    rag_retriever_top_k: int = 50,
    rag_default_top_k: int = 5,
    rag_min_relevance_score: float = 0.0,
    rag_min_score_fallback_topk: int = 0,
    rag_use_hybrid_search: bool = True,
    qdrant_embedding_dim: int = 768,
    qdrant_quantization: bool = False,
    use_cache: bool = False,
    rag_use_domain_routing: bool = False,
    llm_embed_model: str = "nomic-embed-text",
    llm_embed_url: str = "http://localhost:11434/v1",
    llm_embed_dim: int = 768,
) -> MagicMock:
    s = MagicMock()
    s.llm_temperature = llm_temperature
    s.llm_max_tokens = llm_max_tokens
    s.llm_frequency_penalty = llm_frequency_penalty
    s.qdrant_url = qdrant_url
    s.llm_model = llm_model
    s.llm_chat_url = llm_chat_url
    s.llm_api_key = llm_api_key
    s.opa_url = opa_url
    s.reranker_url = reranker_url
    s.reranker_model = reranker_model
    s.rag_reranker_top_k = rag_reranker_top_k
    s.rag_retriever_top_k = rag_retriever_top_k
    s.rag_default_top_k = rag_default_top_k
    s.rag_min_relevance_score = rag_min_relevance_score
    s.rag_min_score_fallback_topk = rag_min_score_fallback_topk
    s.rag_use_hybrid_search = rag_use_hybrid_search
    s.qdrant_embedding_dim = qdrant_embedding_dim
    s.qdrant_quantization = qdrant_quantization
    s.use_cache = use_cache
    s.rag_use_domain_routing = rag_use_domain_routing
    s.llm_embed_model = llm_embed_model
    s.llm_embed_url = llm_embed_url
    s.llm_embed_dim = llm_embed_dim
    return s


@pytest.mark.unit
class TestPipelineGenerationKwargs:
    """Verify generation kwargs on the non-streaming pipeline LLM component."""

    def _get_llm_generation_kwargs(self, settings: MagicMock) -> dict:
        """Build the pipeline and extract the LLM component's generation_kwargs."""
        from src.pipelines.query import build_query_pipeline

        with patch("src.pipelines.query.SecureRetriever"), \
             patch("src.pipelines.query.OpaChunkValidator"), \
             patch("src.pipelines.query.InfinityReranker"), \
             patch("src.pipelines.query.PromptBuilder"), \
             patch("src.pipelines.query.AsyncPipeline") as MockPipeline:

            # Capture the kwarg used when OpenAIChatGenerator is added
            added_components = {}

            def mock_add_component(name, component):
                added_components[name] = component

            instance = MockPipeline.return_value
            instance.add_component.side_effect = mock_add_component
            instance.connect = MagicMock()

            build_query_pipeline(settings)

            llm = added_components.get("llm")
            return getattr(llm, "generation_kwargs", {})

    def test_non_streaming_includes_frequency_penalty_when_nonzero(self):
        """Pipeline LLM has frequency_penalty when settings.llm_frequency_penalty > 0."""
        settings = _mock_settings(llm_frequency_penalty=0.3)
        kwargs = self._get_llm_generation_kwargs(settings)
        assert "frequency_penalty" in kwargs
        assert kwargs["frequency_penalty"] == pytest.approx(0.3)

    def test_non_streaming_excludes_frequency_penalty_when_zero(self):
        """Pipeline LLM omits frequency_penalty when settings value is 0."""
        settings = _mock_settings(llm_frequency_penalty=0.0)
        kwargs = self._get_llm_generation_kwargs(settings)
        assert "frequency_penalty" not in kwargs

    def test_non_streaming_includes_max_tokens(self):
        """Pipeline LLM always has max_tokens from settings."""
        settings = _mock_settings(llm_max_tokens=2048)
        kwargs = self._get_llm_generation_kwargs(settings)
        assert kwargs.get("max_tokens") == 2048

    def test_non_streaming_includes_temperature(self):
        """Pipeline LLM always has temperature from settings."""
        settings = _mock_settings(llm_temperature=0.05)
        kwargs = self._get_llm_generation_kwargs(settings)
        assert kwargs.get("temperature") == pytest.approx(0.05)

    def test_streaming_and_pipeline_kwargs_match_for_shared_keys(self):
        """
        The keys present in both streaming and non-streaming generation_kwargs
        must have equal values for the same settings.
        """
        from src.components.llm_adapter import build_streaming_generator

        settings = _mock_settings(llm_frequency_penalty=0.3, llm_max_tokens=1024, llm_temperature=0.1)

        # Non-streaming kwargs (from pipeline)
        pipeline_kwargs = self._get_llm_generation_kwargs(settings)

        # Streaming kwargs (from build_streaming_generator — no think mode)
        streaming_gen = build_streaming_generator(
            model=settings.llm_model,
            chat_url=settings.llm_chat_url,
            api_key=settings.llm_api_key,
            streaming_callback=lambda c: None,
            think=None,
            num_ctx=None,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            frequency_penalty=settings.llm_frequency_penalty,
        )
        streaming_kwargs = streaming_gen.generation_kwargs

        for key in ("temperature", "max_tokens", "frequency_penalty"):
            if key in pipeline_kwargs and key in streaming_kwargs:
                assert pipeline_kwargs[key] == pytest.approx(streaming_kwargs[key]), (
                    f"Mismatch for {key}: pipeline={pipeline_kwargs[key]}, "
                    f"streaming={streaming_kwargs[key]}"
                )
