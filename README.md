# DocIntel - Enterprise Document Intelligence

A production-grade document Q&A system demonstrating enterprise RAG patterns.

## Features

- **Multi-tenant document Q&A** with role-based access control
- **Hybrid search** (dense + sparse + reranking)
- **Domain-aware routing** (HR, Technical, Contracts)
- **Semantic caching** for performance
- **Full observability** via Langfuse
- **Local-first** - runs entirely on Docker, no API keys required

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Clients                                  │
│        Web UI (SvelteKit)  │  Mobile  │  CLI  │  3rd Party       │
└─────────────────────────────────────────────────────────────────┘
                              │ REST + SSE (streaming)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        API Gateway                               │
│                     (Kotlin/Spring Boot)                         │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│   Document    │    │     RAG       │    │    Admin      │
│   Service     │    │   Service     │    │   Service     │
│ (Kotlin)      │    │ (Python)      │    │ (Kotlin)      │
└───────────────┘    └───────────────┘    └───────────────┘
        │                     │                     │
        ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│  PostgreSQL  │  Qdrant  │  Redis  │  MinIO  │  Ollama  │ Langfuse│
└─────────────────────────────────────────────────────────────────┘
```

**API-First Design:** The backend exposes REST + SSE endpoints that any client can consume (web, mobile, CLI, integrations).

## Quick Start

### Prerequisites

- **Docker Desktop** (with 8GB+ RAM allocated)
- **Ollama** (native installation) - https://ollama.ai/download
  - macOS: `brew install ollama` or download the app
  - Linux: `curl -fsSL https://ollama.ai/install.sh | sh`

### 1. First-Time Setup (run once)

```bash
cd docintel
./scripts/setup.sh
```

This will:
1. Check that Ollama is installed and running
2. Start infrastructure services (Qdrant, PostgreSQL, Redis, MinIO, Langfuse)
3. Initialize Qdrant collections
4. Pull required models (~8-10GB)

### 2. Start the Application

```bash
./scripts/start-app.sh
```

This will:
1. Verify Ollama and Docker are running
2. Start infrastructure and app services
3. Open the Web UI in your browser

**Langfuse Credentials:**
- Email: `admin@docintel.local`
- Password: `admin123`

### Cleanup

chmod +x /Users/titasbiswas/projects/ai_focused/docintel/scripts/cleanup.sh

```bash
# Stop containers (preserve data)
./scripts/cleanup.sh

# Stop and remove volumes (delete all data)
./scripts/cleanup.sh --volumes

# Remove everything including Ollama models
./scripts/cleanup.sh --all
```

## Project Structure

```
docintel/
├── docker-compose.yml          # All services orchestrated
├── .env.example                # Environment template
├── services/
│   ├── api-gateway/            # Kotlin/Spring Boot - API routing
│   ├── document-service/       # Kotlin/Spring Boot - Doc management
│   ├── rag-service/            # Python/FastAPI/Haystack - RAG pipeline
│   ├── admin-service/          # Kotlin/Spring Boot - Admin operations
│   └── web-ui/                 # SvelteKit - Chat interface
├── config/                     # Service configurations
├── scripts/                    # Utility scripts
├── notebooks/                  # Jupyter notebooks
└── docs/                       # Documentation
```

## Technology Stack

| Component | Technology | License |
|-----------|------------|---------|
| Web UI | SvelteKit + @ai-sdk/svelte | MIT |
| LLM | Qwen3-4B via Ollama | Apache 2.0 |
| Embeddings | nomic-embed-text | Apache 2.0 |
| RAG Framework | Haystack 2.x | Apache 2.0 |
| Vector DB | Qdrant | Apache 2.0 |
| Chunking | Chonkie | MIT |
| LLM Abstraction | LiteLLM | MIT |
| Observability | Langfuse | MIT |

## Service URLs

| Service | URL | Description |
|---------|-----|-------------|
| Web UI | http://localhost:3001 | Chat interface |
| API Gateway | http://localhost:8080 | REST + SSE API |
| Langfuse | http://localhost:3000 | Observability UI |
| Qdrant | http://localhost:6333 | Vector DB dashboard |
| MinIO | http://localhost:9001 | Object storage UI |

## Hardware Requirements

| Setup | RAM | Notes |
|-------|-----|-------|
| M1 MacBook Air 16GB | 16GB | Primary development target |
| NVIDIA GPU | 16GB+ | Faster inference |
| Minimum | 16GB | 4+ CPU cores |

## Documentation

- [Project Specification](docs/part2b-project-spec.md)
- [API Documentation](docs/api-docs.md)
- [Architecture Guide](docs/architecture.md)

## Part of AI for Architects Series

This project is Part 2B of the "AI for Architects" tutorial series:
- Part 2A: Production RAG Concepts
- **Part 2B: Enterprise Document Intelligence (this project)**

## Sample queries for the sample datasets
Am I doing something wrong?
HR Policy:
- How many days of annual leave am I entitled to?
- What is the maternity leave policy?
- How do I request time off for a family emergency?
- What happens to my unused vacation days at year end?

Technical:
- How do I authenticate API requests?
- What are the rate limits for the API?
- How do I upload a document using the API?

Contracts:
- What happens to my data if the contract is terminated?
- What is the liability cap in the agreement?
- How can I terminate the service agreement?

## License

MIT
