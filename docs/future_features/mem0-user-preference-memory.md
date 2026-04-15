# Mem0 Self-Hosted User Preference Memory Layer

## What
Add cross-session user preference memory using self-hosted Mem0 (open-source).
Distinct from conversation history — this captures *who the user is and what they care about*
across all their conversations, not just within one session.

## Why
Current system: each new conversation starts cold, no knowledge of user preferences.
With Mem0: "this user always asks about liability clauses", "prefers concise bullet answers",
"works in healthcare compliance" — surfaces automatically without the user re-stating context.

## How it fits DocIntel
- Mem0 open-source can use **Ollama** for memory extraction (no external API key)
- Storage: **Qdrant** (already deployed) — separate collection `user_memory`
- Haystack 2.25+ has `Mem0MemoryStore` in `haystack-experimental`
- Inject retrieved user memories as a system message prefix on each query, per `user_id`

## Mem0's memory cycle (ADD / UPDATE / DELETE / NOOP)
1. After each Q&A, pass the exchange to Mem0
2. Mem0 uses LLM to extract candidate facts
3. Compares against existing memories for that user via vector similarity
4. Decides: add new fact, update/enrich existing, delete contradicted one, or no-op
5. On next query: retrieve top-k relevant memories → inject as context

## Benchmark reference
- 26% accuracy improvement over full-context (LOCOMO benchmark)
- 90% token savings vs full-context approach
- 91% lower latency

## Implementation sketch
- New Qdrant collection: `user_memory` (per user_id, tenant-scoped)
- `mem0ai` Python package, configured with Ollama + Qdrant
- Call `mem0.add(messages, user_id=user_id)` async after each conversation persist
- Call `mem0.search(query, user_id=user_id)` before building the RAG prompt
- Inject results as `{"role": "system", "content": "What I know about you: ..."}`
- Respect tenant isolation: user_id = `{tenant_id}:{user_id}`

## Second phase: Mem0 for document-level preference
Track which document types / domains the user frequently queries,
surface proactively ("You often ask about contracts — want me to search there first?")

## Dependencies
- `mem0ai` (open-source, pip)
- `haystack-experimental` (already likely in dev)
- No new infrastructure — uses existing Ollama + Qdrant

## Related
- Anchored iterative summarization (within-session context) — implemented separately
- Analytics service — could feed engagement signals into Mem0 memory decisions
