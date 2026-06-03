# Model Profiles — VLM Support & UI Polish (Chunk B)

> Pick this up after Chunk A (backend) is merged. Chunk A added the `kind` column,
> moved `ModelProfileResolver` to `lib/docintel-common`, wired the resolver into
> `ingestion-service` for VLM sampling, and added `GET /api/v1/admin/active-models`.
> This spec is the UI half — the bit that makes VLM tuning intuitive.

## Background — what's already true after Chunk A

- `admin.model_profiles` has a nullable `kind VARCHAR(16)` column with values
  `chat | vlm | embed | rerank` (null = auto-infer from pattern).
- Backend resolver auto-infers `kind` when null:
  - pattern contains `vl` or `vision` → `vlm`
  - pattern contains `embed` → `embed`
  - pattern contains `rerank` → `rerank`
  - else → `chat`
- `ingestion-service` reads VLM sampling params from the resolver before every
  VLM call (uses tenant `*` as fallback, then platform `*`, then built-in
  defaults for `qwen2.5-vl:*`).
- New endpoint:
  ```
  GET /api/v1/admin/active-models
  → {
      "chat":   { "model": "<env>", "kind": "chat" },
      "vlm":    { "model": "<env>", "kind": "vlm" },
      "embed":  { "model": "<env>", "kind": "embed" },
      "rerank": { "model": "<env-or-null>", "kind": "rerank", "disabled": <bool> }
    }
  ```
  Backed by admin-service reading the `LLM_*` env vars.
- Existing `/api/v1/tenants/{tenantId}/model-profiles` POST/PUT now accepts
  optional `kind` field on the request body.

## What this chunk delivers

A redesigned **Model tab** in `services/web-ui/src/routes/settings/+page.svelte`
with three stacked panels:

```
┌── 🟢 ACTIVE MODELS IN THIS TENANT ──────────────────────────┐
│   💬 Chat (RAG)         qwen3.5:4b:4bit          [ Tune... ] │
│   📷 Vision (OCR)       qwen2.5-vl:3b:4bit       [ Tune... ] │
│   🔤 Embeddings         qwen3-embed:0.6b:8bit    — fixed —   │
│   🎯 Reranker           disabled                              │
└──────────────────────────────────────────────────────────────┘

┌── ⚙️  TENANT DEFAULTS (fallback for all models) ────────────┐
│   [ Standard params grid | Thinking params grid ]            │  (unchanged)
└──────────────────────────────────────────────────────────────┘

┌── 📋 MODEL-SPECIFIC OVERRIDES ──────────────────────────────┐
│   Pattern              Kind    Display Name    Actions       │
│   qwen2.5-vl:*         [VLM]   Vision OCR      Edit/Delete   │
│   [+ Add Override]                                            │
└──────────────────────────────────────────────────────────────┘
```

## Implementation checklist

### 1. Active Models panel (new component, top of Model tab)

- [ ] On Model tab mount, call `GET /api/v1/admin/active-models`.
- [ ] Render four rows: `chat | vlm | embed | rerank` with role icon, label,
      env-configured model id (or "disabled" pill).
- [ ] `Tune…` button shown only for `chat` and `vlm` rows. Embed/rerank ignore
      sampling params, so show `— fixed —` text.
- [ ] Clicking `Tune…`:
  - Looks up an existing override matching the model id exactly in
    `tenantProfiles`. If found, opens **edit** mode for that profile.
  - If not found, opens **create** mode with `modelPattern` and `kind`
    pre-filled and locked (read-only inputs with a small "from Active Models"
    hint).
- [ ] Show a tiny "uses Tenant Defaults" badge when no specific override exists
      for that model (helps user understand inheritance).

### 2. Kind badges in Model-specific Overrides table

- [ ] Add a `Kind` column between `Pattern` and `Display Name`.
- [ ] Use the row's `kind` field; fall back to client-side inference if null
      (mirror backend rules).
- [ ] Badge colors: chat=blue, vlm=purple, embed=green, rerank=amber.

### 3. Per-kind field visibility in the Sampling Override modal

The modal at `+page.svelte:~2047` currently shows Standard + Thinking sections
unconditionally. Make it kind-aware:

- [ ] Add `kind` dropdown at the top of the modal (chat/vlm/embed/rerank,
      defaults to auto-infer from pattern).
- [ ] When `kind === 'chat'` → show Standard + Thinking (current behavior).
- [ ] When `kind === 'vlm'` → show Standard only. Hide Thinking section
      entirely. Add a one-line hint: "VLMs don't support thinking mode."
- [ ] When `kind === 'embed'` or `'rerank'` → hide both Standard and Thinking
      grids. Show a notice: "This model kind doesn't use sampling parameters."
      (Profile can still be saved — Display Name + Notes only — for
      bookkeeping. Or just disable Save with a tooltip.)
- [ ] When opened via `Tune…` from Active Models, lock the `kind` and
      `modelPattern` fields (read-only).

### 4. Tenant Defaults panel — copy tweak only

- [ ] Change subtitle from
      "Sampling parameters applied to all models for this tenant"
      → "Fallback for any model that doesn't have a specific override above.
         Both chat and vision (VLM) models inherit from here."
- [ ] No structural changes.

### 5. End-to-end test plan

- [ ] Click `Tune…` on the VLM row → modal opens with pattern locked to
      `qwen2.5-vl:3b:4bit`, kind=vlm, Thinking section hidden.
- [ ] Set `temperature=0.1`, `frequency_penalty=0.3`, save.
- [ ] Re-upload `sample_image_pdf_2.pdf`. Verify ingestion-service logs show
      the resolved params being applied to the VLM call (add a debug log line
      in Chunk A's resolver wiring if not already there).
- [ ] Confirm extraction no longer enters the FSC repetition loop.
- [ ] Open existing chat-kind override → modal still shows Thinking section.
- [ ] Create a new override with no kind set, pattern `qwen2.5-vl:custom` →
      backend auto-infers kind=vlm; UI badge renders purple.

## Files to touch

- `services/web-ui/src/routes/settings/+page.svelte` — all UI changes
- `services/admin-service/src/main/kotlin/.../AdminController.kt` (or similar)
  — verify `/active-models` endpoint exists from Chunk A
- No backend changes expected in this chunk

## Out of scope

- Editing the env-configured model id from the UI (still env-only).
- Per-document or per-query overrides.
- Showing live sampling params actually used in the last RAG query (could be a
  future "diagnostics" panel).
