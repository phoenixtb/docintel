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
./scripts/start.sh           # Start with authentication (Zitadel)
./scripts/start.sh --build   # Rebuild images before starting
```

Starts infrastructure, Zitadel, and all application services. Authentication is always required — it is the mechanism for tenant identification.

### 3. Stop / Cleanup

```bash
./scripts/stop.sh              # Stop containers (preserves state, fast restart)
./scripts/cleanup.sh           # Stop and remove containers
./scripts/cleanup.sh --data    # Also wipe all named data volumes (DB, vectors, cache, etc.) — preserves Docker images and Ollama models
./scripts/cleanup.sh --volumes # Same as --data
./scripts/cleanup.sh --all     # Delete everything including Ollama models
```

### Credentials

| Service | URL | User | Password |
|---------|-----|------|----------|
| Web UI | http://localhost:3001 | demo-admin | DocIntel@123 |
| Zitadel Admin | http://localhost:9090 | akadmin | DocIntel@123 |
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
docker compose build web-ui
docker compose up -d web-ui
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
docker compose build <service> && docker compose up -d <service>

# Shell into a running container
docker exec -it docintel-rag-service-1 bash
```

### Zitadel (Identity Provider)

OAuth2/OIDC configuration is fully automated via blueprint at `config/zitadel/blueprints/docintel-setup.yaml`. On `start.sh`, the blueprint auto-provisions:
- OAuth2 provider (public client, PKCE)
- DocIntel application
- Tenant groups and demo users
- Branding (logo, favicon, custom login page)

To re-apply the blueprint manually:
```bash
docker exec docintel-zitadel-worker-1 ak apply_blueprint /blueprints/custom/docintel-setup.yaml
```

### GPU Usage (Mac M3 / Apple Silicon)

- **LLM (Ollama)**: Runs natively on host and uses Metal by default. During a query, check Activity Monitor → Ollama; GPU should spike.
- **Embeddings**: Run in the rag-service container (CPU). They don't use GPU.
- If Ollama shows no GPU: ensure `ollama serve` is running natively (not in Docker). Run a query, then `ollama ps` to confirm the model is loaded.

### Key Directories

```
config/
├── zitadel/
│   ├── blueprints/    # Zitadel provisioning YAML
│   └── media/         # Login page logo + favicon
├── defaults.env       # Model defaults — single source of truth (scripts + tests)
├── opa/policies/      # OPA authorization policies
├── postgres/          # DB init scripts
└── qdrant/            # Collection init scripts

scripts/
├── setup.sh           # One-time: pull images + models
├── start.sh           # Start all services (--build)
├── stop.sh            # Stop containers (preserves state)
├── cleanup.sh         # Remove containers (--data, --volumes, --all)
├── logs.sh            # View logs (debug mode: rag + api-gateway)
├── build.sh           # Interactive build selector
├── docintel.sh        # Interactive CLI (setup, start, stop, logs, etc.)
└── setup-zitadel.sh # Verify blueprint applied (called by start.sh)
```

## Configuration

### Model Defaults — `config/defaults.env`

All model names are defined once in `config/defaults.env` and referenced everywhere else:

```bash
DEFAULT_LLM_MODEL=qwen3.5:4b      # Chat generation / streaming
DEFAULT_FALLBACK_MODEL=phi3:mini   # Fallback if preferred model unavailable
DEFAULT_EMBED_MODEL=nomic-embed-text  # Dense vector embeddings
```

- **Scripts** (`setup.sh`, `start.sh`, `cleanup.sh`) source this file automatically.
- **Tests** (`tests/conftest.py`, `test_components.py`) parse it at import time.
- **RAG service** reads `OLLAMA_LLM_MODEL` from Docker Compose (which defaults to `DEFAULT_LLM_MODEL`).

To change the default model globally, edit `config/defaults.env` and restart the stack.

### Dynamic Model Selection (per-tenant)

`tenant_admin` and `platform_admin` can change the active LLM from the UI without restarting. The resolution order at query time is:

| Priority | Source | Who controls |
|----------|--------|-------------|
| 1 (highest) | `platform_settings.llm_model` (PostgreSQL) | `platform_admin` |
| 2 | `tenants.settings->>'llm_model'` (PostgreSQL) | `tenant_admin` |
| 3 (fallback) | `OLLAMA_LLM_MODEL` env var | `config/defaults.env` |

**Tenant admin:** Settings → Model tab → select from available Ollama models → confirm cache invalidation warning.

**Platform admin:** Admin → Model tab → global override or "Tenant Choice" → confirm cache invalidation for all tenants.

When "Tenant Choice" is active (platform model = null), each tenant uses its own preference. When the platform admin reverts from a specific model back to "Tenant Choice", all tenant caches are cleared and each tenant falls back to its own stored preference (or the env default if none is set).

The resolved model is cached in-process for 60 seconds per tenant to avoid a database round-trip on every query.

### Environment Variables

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_LLM_MODEL` | `qwen3.5:4b` | Default LLM (overridden by DB at runtime) |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama host |
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
| Identity Provider | Zitadel | MIT |
| LLM | Qwen3.5-4B via Ollama (configurable per-tenant) | Apache 2.0 |
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
| Zitadel | http://localhost:9090 | Identity provider |
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

## Demo Queries

All 20 queries below are validated against the sample datasets (20/20 pass).
Load sample data first: **Documents → Load Sample Data** (or `./scripts/seed-data.sh`).



### HR Policies (`hr_policy`)

| Query | Expected topics |
|-------|----------------|
| What is the work from home policy? | acknowledgement, periodic review |
| How many days of annual leave am I entitled to? | entitlement, leave days |
| What is the process for requesting parental leave? | parental leave, request procedure |
| How is employee performance evaluated during the probation period? | evaluation, probation |
| What are the consequences of not complying with the code of conduct? | disciplinary action |
| What should I do if I experience workplace harassment? | reporting, policy |
| How do I submit a complaint or grievance? | grievance procedure |

### Technical (`technical`)

| Query | Expected topics |
|-------|----------------|
| How do I troubleshoot a system that keeps crashing? | diagnostics, crash analysis |
| What are the steps to recover data from a failed hard drive? | data recovery |
| How do I configure network settings on a remote server? | network configuration |
| What are best practices for securing a database? | access control, encryption |
| How do I set up a VPN connection? | VPN, network tunneling |
| What is the recommended way to back up critical data? | backup strategies, storage |

### Contracts (`contracts`) — real CUAD commercial contracts

| Query | Expected topics |
|-------|----------------|
| What are the termination clauses in these contracts? | notice period, termination conditions |
| Who owns intellectual property developed under this agreement? | IP ownership, assignment |
| What is the limitation of liability in this contract? | liability cap, damages exclusions |
| Does this agreement include a non-compete or non-solicitation clause? | restraint of trade |
| What are the indemnification obligations of each party? | indemnify, hold harmless |
| What are the payment terms and conditions? | invoicing, due dates |
| Under what circumstances can the contract be renewed or extended? | renewal, extension term |

### Integration Tests

A reusable test suite lives in `tests/integration/`:

```bash
cd tests/integration

# Run all suites — direct rag-service (faster, no auth needed)
python run_tests.py --url http://localhost:8000 --rag-path /query/stream

# Or via api-gateway (tests the full stack)
python run_tests.py

# Run a single suite
python run_tests.py --url http://localhost:8000 --rag-path /query/stream --suite "Technical"

# Output: ttests/integration/reports/YYYYMMDD_HHMMSS.{json,md}
```

Add new query suites or tweak pass criteria in `tests/integration/queries.yaml`.

## License

MIT
