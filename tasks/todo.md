# Anchored Iterative Summarization — Holistic Plan

## Design Decisions

### How the UI knows about compression — two moments:

1. **On conversation load** — `context_summary` message in the messages array (role="context_summary")
   renders a persistent visual divider in the chat thread. Survives page refresh, session restore.

2. **Live during streaming** — the initial SSE metadata event includes `context_state`
   so the UI can show a live context budget indicator without a round-trip.

### Single source of truth for the summary
A `context_summary` role message written to `messages` table is both the notification mechanism
AND the stored summary — no duplication. The `session_summary` column on `conversations` is
the working copy the backend uses for LLM prompting (avoids re-querying all messages).

---

## Data Model

### `conversations` table additions
```sql
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS session_summary   TEXT,
  ADD COLUMN IF NOT EXISTS summary_upto_count INTEGER NOT NULL DEFAULT 0;
-- summary_upto_count = total messages (user+assistant) absorbed into last summary
```

### New message role: `"context_summary"`
```json
{
  "role": "context_summary",
  "content": "<the summary text itself>",
  "metadata": {
    "type": "context_compression",
    "compressed_turns": 3,
    "summary_upto_count": 6
  }
}
```
Written to `messages` table when compression runs.
UI renders this as a visual divider, NOT a chat bubble.
Excluded from history passed to LLM.

---

## SSE Stream Changes

### Metadata event (already emitted at stream start — extend it)
```json
{
  "metadata": {
    "query_id": "...",
    "context_state": {
      "has_summary": true,
      "summarized_turns": 3,
      "verbatim_turns": 2
    }
  }
}
```
When `has_summary: false`, `context_state` is omitted entirely (no noise for fresh conversations).

---

## Files to Change

### 1. `config/postgres/init.sql`
- Add `session_summary TEXT` and `summary_upto_count INTEGER DEFAULT 0` to `conversations` CREATE TABLE
- Live DB: run `ALTER TABLE` (in implementation step)

### 2. `services/rag-service/src/config.py`
```python
conversation_summary_threshold: int = 8   # messages before first compression
conversation_verbatim_recent:   int = 4   # always kept verbatim
```

### 3. `services/rag-service/src/db.py`
- Add `session_summary` + `summary_upto_count` to `Conversation` ORM model
- Add `get_conversation_summary_state(conversation_id, tenant_id) -> dict`:
    returns `{session_summary, summary_upto_count, total_message_count}`
    lightweight — no eager load of all messages
- Add `update_conversation_summary(conversation_id, tenant_id, summary, upto_count)`
- `_conv_to_dict()` includes `summary_upto_count` in the output
- `_load_conversation_history()` excludes `context_summary` role messages from history

### 4. `services/rag-service/src/components/summarizer.py` (NEW)
```python
class AnchoredSummarizer:
    """
    Anchored iterative summarizer using a small/fast Ollama model.
    Extends (never replaces) an existing summary with a new span of messages.
    """
    async def compress(
        self,
        existing_summary: str | None,
        new_span: list[dict],          # [{"role": "user"|"assistant", "content": "..."}]
        ollama_url: str,
        model: str,
    ) -> str: ...
```

Prompt (anchored — merges, never rewrites):
```
You are a conversation memory assistant.

Current summary (may be empty):
{existing_summary or "No summary yet."}

New conversation exchanges to incorporate:
{formatted_new_span}

Extend the summary to include these exchanges. Preserve concisely (≤300 words):
- The user's main questions and intent
- Key facts, conclusions, and answers provided
- Documents, topics, or domains referenced
- Important decisions or findings

Return only the updated summary. No preamble or explanation.
```

### 5. `services/rag-service/src/pipelines/query.py`

**`_load_conversation_history()` rewrite:**
```
1. get_conversation_summary_state(conversation_id, tenant_id)
   → {session_summary, summary_upto_count, total_message_count}
2. Load only last VERBATIM_RECENT messages (not all — efficient)
3. Build history:
   - If session_summary: prepend {"role": "system", "content": f"Earlier conversation:\n{session_summary}"}
   - Append last VERBATIM_RECENT user+assistant messages
4. Also return context_state = {has_summary: bool, summarized_turns: int, verbatim_turns: int}
   (returned alongside history for the stream metadata event)
```

**`_persist_conversation()` + new `_maybe_compress_history()` coroutine:**
```
_persist_conversation():
  1. add_message(user) + add_message(assistant)
  2. asyncio.ensure_future(_maybe_compress_history(...))  ← fire-and-forget

_maybe_compress_history(conversation_id, tenant_id, summarizer, settings):
  1. get_conversation_summary_state()
  2. if total_messages <= settings.conversation_summary_threshold: return
  3. evictable_count = total_messages - VERBATIM_RECENT - summary_upto_count
  4. if evictable_count < 2: return   ← nothing meaningful to compress yet
  5. Load messages[summary_upto_count : total - VERBATIM_RECENT]
  6. new_summary = await summarizer.compress(existing_summary, evicted_span)
  7. update_conversation_summary(new_summary, new_upto_count)
  8. Insert context_summary message into messages table
     (role="context_summary", content=new_summary, metadata={...})
```

**`_load_conversation_history()` signature change:**
Returns `tuple[list[dict], dict]` — (history, context_state)
Callers updated accordingly.

### 6. `services/rag-service/src/api/main.py`

**`/query/stream` endpoint:**
- `_load_conversation_history()` now returns `(history, context_state)`
- The initial metadata SSE event is extended:
  ```python
  metadata_event = {"metadata": {"query_id": query_id}}
  if context_state.get("has_summary"):
      metadata_event["metadata"]["context_state"] = context_state
  yield f"data: {json.dumps(metadata_event)}\n\n"
  ```
- `AnchoredSummarizer` instantiated once at startup (app.state), injected into query

**`GET /conversations/{id}` endpoint:**
- Response already returns messages array — `context_summary` role messages included
- Also add `summary_upto_count` to the conversation object in response
- UI uses this on load to know if a divider should appear

### 7. `services/web-ui/src/lib/components/ContextSummaryDivider.svelte` (NEW)
```
Visual: horizontal rule with a pill label "Earlier context summarized"
State: collapsed (default) / expanded (shows summary text)
Props: summary: string, compressedTurns: number
```
Design:
```
────────────── ↕ Earlier context summarized (3 turns) ──────────────
              [click to expand/collapse]
              <summary text when expanded>
```

### 8. `services/web-ui/src/routes/chat/+page.svelte`

**Message type extension:**
```typescript
interface Message {
  id: string;
  role: 'user' | 'assistant' | 'context_summary';   // NEW
  content: string;
  thinking?: string;
  sources?: Source[];
  liked?: boolean | null;
  queryId?: string;
  routedDomain?: string;
  metadata?: { type: string; compressed_turns: number; };  // for context_summary
}
```

**Message rendering:**
```svelte
{#each messages as message (message.id)}
  {#if message.role === 'context_summary'}
    <ContextSummaryDivider
      summary={message.content}
      compressedTurns={message.metadata?.compressed_turns ?? 0}
    />
  {:else if message.role === 'user'}
    <!-- existing user bubble -->
  {:else}
    <MessageBubble ... />
  {/if}
{/each}
```

**Context state indicator (in input area):**
```
When contextState.has_summary:
  Shows below textarea: "Context: {verbatim} turns active · {summarized} summarized"
  Subtle, slate-colored, tooltip explains what this means
```

```typescript
let contextState = $state<{ has_summary: boolean; summarized_turns: number; verbatim_turns: number } | null>(null);
```

Updated in:
- Stream parse: `if (data.metadata?.context_state) contextState = data.metadata.context_state`
- Conversation load: derive from `summary_upto_count` in API response
- Reset to `null` on `startNewChat()`

**`loadConversation()` update:**
- Map `context_summary` messages from API response to `Message` objects with `role: "context_summary"`
- Set `contextState` from `summary_upto_count` in response

---

## Compression Trigger Logic (summary)

```
threshold = 8 messages   (4 full turns)
verbatim  = 4 messages   (2 full turns always kept raw)

Example with 10 total messages:
  summary covers: messages[0..5]  (3 turns = compressed_turns: 3)
  verbatim keeps: messages[6..9]  (2 turns)

On load:
  LLM receives: [system: "Earlier: <summary>"] + messages[6..9]
```

---

## Compression Ratio Target
- Input span:  ~3 turns × ~300 tokens each = ~900 tokens
- Summary output: ≤300 words ≈ 400 tokens
- Net saving per compression: ~500 tokens recovered
- Model: qwen3:1.7b (fast, cheap, no impact on main query latency — async)

---

## Graceful Degradation
- Existing conversations: `summary_upto_count = 0`, `session_summary = NULL`
  → behaviour identical to current (no summary, load last 4 messages)
- If summarizer fails: log warning, skip compression, conversation continues normally
- `context_summary` messages filtered out of LLM history — no bleeding

---

## Implementation Order
1. DB migration (ALTER TABLE + init.sql)
2. `db.py` model + functions
3. `config.py` + `summarizer.py`
4. `query.py` — history load + compress trigger
5. `main.py` — metadata event + startup wiring
6. `ContextSummaryDivider.svelte` (new component)
7. `chat/+page.svelte` — type, rendering, context indicator
8. Build + test: create conversation → ask 6+ questions → verify divider appears → verify LLM uses context from summary
