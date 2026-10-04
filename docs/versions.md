# Pinned versions

The versions the repository builds and runs with today. Changing one is its own PR, except where a
PR introduces the dependency. Python images install exactly what each service's `uv.lock` pins
([ADR-0002](adr/0002-python-images-from-lockfiles.md)), apart from the hardware-specific torch
family. The web UI pins through `package-lock.json`. This table lists the anchors.

## Languages and build

| Item | Version | Where |
|---|---|---|
| Java (toolchain, runtime images) | 21 (`eclipse-temurin:21-*-alpine`) | `services/*/build.gradle.kts`, Dockerfiles |
| Kotlin | 1.9.25 | `services/*/build.gradle.kts` |
| Gradle wrapper | 8.11.1 | `services/*/gradle/wrapper/gradle-wrapper.properties` |
| Python | ≥ 3.11 (images `python:3.11-slim`); ingestion-service < 3.13 | `pyproject.toml`, Dockerfiles |
| Node (web UI build) | 22 (`node:22-alpine`) | `services/web-ui/Dockerfile` |
| uv | 0.12.22 | Python Dockerfiles (`ghcr.io/astral-sh/uv:0.12.22`), CI `python-locks` |
| torch (images) | 2.11.0 (`TORCH_VERSION`, CPU by default) | Python Dockerfiles; locks pin 2.12.1, which is not used in images |

Gradle 8.11.1 does not start on JDK 25 or newer: run `./gradlew` with a JDK 21 `JAVA_HOME`.

## Frameworks and libraries

| Item | Version | Where |
|---|---|---|
| Spring Boot | 3.4.1 | Kotlin services |
| springdoc-openapi | 2.8.9 | document-service, admin-service |
| AWS SDK for Java v2 (BOM) | 2.55.11 | document-service, admin-service — `s3`, `apache-client` |
| Testcontainers (Java) | 1.21.4 | document-service, admin-service tests (first line with Docker Engine 29 API support) |
| MockK / springmockk | 1.13.13 / 4.0.2 | Kotlin tests |
| boto3 | ≥ 1.43.108 (lock: 1.43.108) | `docintel-common[s3]` |
| testcontainers (Python) | ≥ 4.15.0 | `docintel-common[dev]` |
| SvelteKit / Svelte | ^2 / ^5 | `services/web-ui/package.json` |

## Container images (compose and tests)

| Image | Tag | Used by |
|---|---|---|
| `versity/versitygw` | v1.8.0 | `object-store` service and object-store integration tests, kept equal by `scripts/check-object-store-pin.sh` |
| `curlimages/curl` | 8.22.0 | `object-store-init` |
| `debian` | 13-slim | `scripts/backup.sh` (GNU tar with xattrs) |
| `postgres` | 18.1-trixie (compose), 15-alpine (Kotlin tests) | |
| `redis` | 7.4.0-alpine | compose, document-service tests |
| `qdrant/qdrant` | v1.16.3 | compose |
| `clickhouse/clickhouse-server` | 25.1.3.23 | compose |
| `ghcr.io/zitadel/zitadel`, `zitadel-login` | v4.12.1 | compose |
| `langfuse/langfuse` / `langfuse-worker` | 3 / pinned digest | compose |
| `openpolicyagent/opa` | 0.70.0-static | compose |
| `traefik` | v3.6.8 | compose |
| `prom/prometheus` / `grafana/grafana` | v3.2.1 / 11.4.0 | compose |
