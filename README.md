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
./scripts/start.sh           # With authentication (Authentik)
./scripts/start.sh --no-auth # Without authentication (dev mode)
```

Starts infrastructure, Authentik (if enabled), and all application services.

### 3. Stop / Cleanup

```bash
./scripts/stop.sh             # Stop containers (preserves state, fast restart)
./scripts/cleanup.sh          # Stop and remove containers
./scripts/cleanup.sh --volumes # Also delete all data (DB, vectors, etc.)
./scripts/cleanup.sh --all    # Delete everything including Ollama models
```

### Credentials

| Service | URL | User | Password |
|---------|-----|------|----------|
| Web UI | http://localhost:3001 | demo-admin | DocIntel@123 |
| Authentik Admin | http://localhost:9090/if/admin/ | akadmin | DocIntel@123 |
| Langfuse | http://localhost:3000 | admin@docintel.local | admin123 |
| MinIO Console | http://localhost:9001 | minioadmin | minioadmin |

**Demo users** (password: `DocIntel@123`):
- `demo-admin` / `demo-user` — tenant: default
- `tenant-user` — tenant: demo

## Developer Guide

### After Code Changes

Services with `build:` directives (web-ui, api-gateway, document-service, rag-service, admin-service) need a rebuild to pick up changes:

```bash
# Rebuild all services and restart
./scripts/start.sh --build

# Rebuild a single service (faster)
docker compose --profile app build web-ui
docker compose --profile app up -d web-ui
```

`stop.sh` / `start.sh` without `--build` reuses existing images — fast restarts, but won't reflect code changes.

### Viewing Logs (Debug Mode)

When queries get stuck or you need to debug:

```bash
# Interactive: pick service or "Debug" (rag + api-gateway)
./scripts/docintel.sh   # → Logs

# Or directly:
./scripts/logs.sh debug           # Query path: rag-service + api-gateway
./scripts/logs.sh rag-service     # RAG pipeline only
docker compose logs -f rag-service
```

### Service Development Shortcuts

```bash
# View logs for a specific service
docker compose logs -f web-ui
docker compose logs -f rag-service

# Rebuild and restart one service
docker compose --profile app build <service> && docker compose --profile app up -d <service>

# Shell into a running container
docker exec -it docintel-rag-service-1 bash
```

### Authentik (Identity Provider)

OAuth2/OIDC configuration is fully automated via blueprint at `config/authentik/blueprints/docintel-setup.yaml`. On `start.sh`, the blueprint auto-provisions:
- OAuth2 provider (public client, PKCE)
- DocIntel application
- Tenant groups and demo users
- Branding (logo, favicon, custom login page)

To re-apply the blueprint manually:
```bash
docker exec docintel-authentik-worker-1 ak apply_blueprint /blueprints/custom/docintel-setup.yaml
```

### GPU Usage (Mac M3 / Apple Silicon)

- **LLM (Ollama)**: Runs natively on host and uses Metal by default. During a query, check Activity Monitor → Ollama; GPU should spike.
- **Embeddings**: Run in the rag-service container (CPU). They don't use GPU.
- If Ollama shows no GPU: ensure `ollama serve` is running natively (not in Docker). Run a query, then `ollama ps` to confirm the model is loaded.

### Key Directories

```
config/
├── authentik/
│   ├── blueprints/    # Authentik provisioning YAML
│   └── media/         # Login page logo + favicon
├── postgres/          # DB init scripts
└── qdrant/            # Collection init scripts

scripts/
├── setup.sh           # One-time: pull images + models
├── start.sh           # Start all services (--build, --no-auth)
├── stop.sh            # Stop containers (preserves state)
├── cleanup.sh         # Remove containers (--volumes, --all)
├── logs.sh            # View logs (debug mode: rag + api-gateway)
├── build.sh           # Interactive build selector
├── docintel.sh        # Interactive CLI (setup, start, stop, logs, etc.)
└── setup-authentik.sh # Verify blueprint applied (called by start.sh)
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
| Web UI | SvelteKit + oidc-client-ts | MIT / Apache 2.0 |
| API Gateway | Kotlin / Spring Cloud Gateway | Apache 2.0 |
| Identity Provider | Authentik | MIT |
| LLM | Qwen3-4B via Ollama | Apache 2.0 |
| Embeddings | nomic-embed-text | Apache 2.0 |
| RAG Framework | Haystack 2.x | Apache 2.0 |
| Vector DB | Qdrant | Apache 2.0 |
| Database | PostgreSQL 18 | PostgreSQL License |
| Cache | Redis | BSD |
| Object Storage | MinIO | AGPL / Commercial |
| Observability | Langfuse + ClickHouse | MIT |

## Service URLs

| Service | URL | Description |
|---------|-----|-------------|
| Web UI | http://localhost:3001 | Chat interface |
| API Gateway | http://localhost:8080 | REST + SSE API |
| Authentik | http://localhost:9090 | Identity provider |
| Langfuse | http://localhost:3000 | Observability UI |
| Qdrant | http://localhost:6333 | Vector DB dashboard |
| MinIO Console | http://localhost:9001 | Object storage UI |
| PostgreSQL | localhost:5432 | Database |
| Redis | localhost:6379 | Cache |

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
