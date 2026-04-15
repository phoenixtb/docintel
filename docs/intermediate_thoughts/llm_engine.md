# LLM Engine — Integration Requirements for DocIntel

Goal: replace all `OllamaXxx` Haystack components with `OpenAIChatGenerator` /
`OpenAITextEmbedder`, pointing at a provider-agnostic engine URL.  The engine
(MLX, vLLM, Ollama, llama.cpp, etc.) only needs to honour the contracts below.

---

## 1. API Contracts Required

### 1.1 Chat Completion  —  `POST /v1/chat/completions`

Standard OpenAI chat completions. DocIntel uses both streaming and non-streaming.

**Non-streaming request (summariser, query expansion)**
```json
{
  "model": "qwen3-8b",
  "messages": [{"role": "user", "content": "..."}],
  "temperature": 0.1,
  "max_tokens": 4096
}
```

**Streaming request (user-facing RAG response)**
```json
{
  "model": "qwen3-8b",
  "messages": [...],
  "stream": true,
  "temperature": 0.1,
  "max_tokens": 4096,
  "num_ctx": 16384
}
```

SSE response must emit `data: {"choices":[{"delta":{"content":"..."}}]}` chunks
and terminate with `data: [DONE]`.

**Thinking mode** (models that support extended reasoning)

DocIntel reads thinking content from the stream.  Two accepted formats — the
engine can emit either:

- **Preferred (OpenAI `reasoning_content`)**: delta has a separate
  `reasoning_content` field alongside `content`.
- **Fallback (inline tags)**: thinking wrapped in `<think>…</think>` inside the
  `content` stream.  DocIntel already parses this.

`num_ctx` / `num_predict` are passed as extra body params.  If the engine ignores
unknown params that is fine; if it rejects them we will gate them behind a flag.

---

### 1.2 Text Embeddings  —  `POST /v1/embeddings`

Used by **both** rag-service (query-time) and ingestion-service (index-time).
Both must point at the **same embedding model** to keep vector space consistent.

```json
{
  "model": "nomic-embed-text",
  "input": "string or array of strings"
}
```

Response:
```json
{
  "data": [{"embedding": [0.01, -0.23, ...], "index": 0}],
  "model": "nomic-embed-text"
}
```

Current embedding dimension: **768** (nomic-embed-text).  If you switch models,
`EMBED_DIM` in rag-service config and the Qdrant collection must be recreated
(breaking change — needs a re-index).

---

### 1.3 Model List  —  `GET /v1/models`  *(optional but useful)*

Used by the admin UI to populate the model picker.  Returns at minimum:
```json
{"data": [{"id": "qwen3-8b"}, {"id": "nomic-embed-text"}]}
```

---

## 2. Config Surface in DocIntel (what changes)

| Current env var | Rename to | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `LLM_ENGINE_URL` | Base URL for all `/v1/*` calls |
| `OLLAMA_LLM_MODEL` | `LLM_CHAT_MODEL` | Chat / generation model name |
| `OLLAMA_EMBED_MODEL` | `LLM_EMBED_MODEL` | Embedding model name |
| `OLLAMA_LLM_CTX` | `LLM_CTX` | Context window (standard) |
| `OLLAMA_LLM_THINKING_CTX` | `LLM_THINKING_CTX` | Context window (thinking mode) |
| `OLLAMA_EXPANSION_MODEL` | `LLM_EXPANSION_MODEL` | Query expansion / summariser model |

`LLM_ENGINE_URL` will default to `http://host.docker.internal:11434` so existing
Ollama setups keep working with zero config change.

---

## 3. Haystack Component Swap

| Remove | Replace with |
|---|---|
| `OllamaChatGenerator` | `OpenAIChatGenerator(api_base_url=LLM_ENGINE_URL, model=LLM_CHAT_MODEL)` |
| `OllamaTextEmbedder` | `OpenAITextEmbedder(api_base_url=LLM_ENGINE_URL, model=LLM_EMBED_MODEL)` |
| `OllamaDocumentEmbedder` | `OpenAIDocumentEmbedder(api_base_url=LLM_ENGINE_URL, model=LLM_EMBED_MODEL)` |

`openai-haystack` is already in the Haystack ecosystem (`haystack-ai` pulls it).
No new pip dependency needed.

`api_key` will be set to a dummy value (`"no-key"`) unless the engine actually
requires one (e.g., a cloud endpoint).  Make the engine accept any key or none.

---

## 4. Thinking Mode

DocIntel has a per-tenant thinking mode toggle stored in `admin.tenants.settings`.
The toggle is only shown in the UI when the selected model supports thinking.

The engine needs to expose **which models support thinking** — ideally via a
metadata field in `GET /v1/models`:

```json
{
  "data": [
    {"id": "qwen3-8b",    "capabilities": {"thinking": true}},
    {"id": "qwen3.5-4b",  "capabilities": {"thinking": false}},
    {"id": "nomic-embed", "capabilities": {"thinking": false}}
  ]
}
```

If the engine does not expose capabilities, DocIntel can maintain a local
allowlist (`THINKING_CAPABLE_MODELS=qwen3-8b,deepseek-r1`).

---

## 5. Health / Readiness

DocIntel startup waits for the LLM engine.  Acceptable health endpoints (any one):

- `GET /health` → 200
- `GET /v1/models` → 200

---

## 6. MLX-Specific Notes

- `mlx-lm` server (`mlx_lm.server`) exposes OpenAI-compatible `/v1/chat/completions`
  and `/v1/completions` but **not `/v1/embeddings`** natively.
- Embedding on MLX needs a separate process — options:
  - Run a second `mlx-lm` or `mlx-embeddings` server on a different port.
  - Use a lightweight OpenAI-compatible embedding server (e.g., `infinity-emb`
    which runs on Metal via PyTorch MPS — already in the docker-compose stack).
  - Point `LLM_EMBED_URL` (separate from `LLM_ENGINE_URL`) at the embedding server.
- Consider a thin router/proxy (e.g., LiteLLM) that unifies chat + embed behind
  one `LLM_ENGINE_URL` and translates model names — cleanest for multi-backend.

---

## 7. Future / Out of Scope for Now

- Mem0 self-hosted as a user preference layer (noted in `docs/future_features`).
- Per-tenant model overrides (tenant can choose model from engine's model list).
- Function calling / tool use.
- Vision models.
