# 02 — LMForge Changes

Target: enable LMForge (running inside the `ubuntu-ml` VM) to serve a vision-language model (VLM) alongside the existing chat / embed / rerank models, all hot-resident in the RTX 5060's 16 GB VRAM, exposed via the existing OpenAI-compatible API on the LAN.

## 0. Where this code lives

LMForge source: `/Users/titasbiswas/projects/ai_focused/lmforge` (Rust workspace, builds to a single `lmforge` binary plus a Tauri UI).

All file paths in this doc are relative to that repo root.

## 1. Current state — what already works

Confirmed by reading the source:

- `EngineAdapter` trait in `src/engine/adapter.rs` — clean three-method interface (`pull_model`, `start`, `stop`)
- Three adapters in `src/engine/adapters/`: `omlx.rs`, `sglang.rs`, `llamacpp.rs`
- `EngineManager` in `src/engine/manager.rs` holds `running_models: HashMap<String, ModelSlot>` — **multi-model hot loading with VRAM-aware LRU eviction is already implemented**
- Model roles: `Chat | Embed | Rerank` (`src/engine/adapter.rs:11`)
- `ModelCapabilities` struct in `src/model/index.rs:27` — `chat`, `embeddings`, `reranking`, `thinking`, `embedding_dims`, `pooling`
- `detect_capabilities()` (`src/model/index.rs:163`) — sniffs HF `config.json` to set caps
- OpenAI handler in `src/server/openai.rs:71` (`chat_completions`) — parses JSON body, role-checks the model, forwards to the selected backend slot
- SGLang adapter `start()` in `src/engine/adapters/sglang.rs:105` — currently passes `--model-path`, `--port`, and (for embed) `--is-embedding` + `--pooling-method`
- Bind address: `127.0.0.1:11430` (must change for LAN access)

## 2. Gaps to close

| Gap | Severity | Where |
|---|---|---|
| No VLM models in catalog | Blocker | `data/catalogs/gguf.json` |
| `ModelCapabilities` has no `vision` field | Blocker | `src/model/index.rs:27` |
| `detect_capabilities()` doesn't recognize VLM model types | Blocker | `src/model/index.rs:163` |
| `ModelRole` enum has no `Vision` variant | Optional / preferred | `src/engine/adapter.rs:11` |
| SGLang adapter doesn't pass `--chat-template` for VLM models | Blocker | `src/engine/adapters/sglang.rs:105` |
| OpenAI handler — verify multimodal `content` array passthrough | Verify | `src/server/openai.rs:71` |
| Daemon binds 127.0.0.1 only | Blocker for LAN | wherever the listener is bound (search for `11430`) |
| No `--gpu-memory-utilization` or `--mem-fraction-static` knob exposed | Important for multi-model packing | `src/engine/adapters/sglang.rs:155` (the "Future parity params" comment) |

## 3. Change plan

### 3.1 Catalog — add VLM entries

File: `data/catalogs/gguf.json`

Add a section after `_comment_chat`, before `_comment_embed`. For VLMs we need both the base model GGUF and the multimodal projector file (`mmproj`). llama.cpp uses both; SGLang uses the original safetensors HF repo, not GGUF, so we may need a parallel entry strategy — see §3.4.

```json
"_comment_vision": "--- Vision-language models (VLM) ---",

"qwen2.5-vl:7b:4bit": {
    "repo": "bartowski/Qwen2.5-VL-7B-Instruct-GGUF",
    "file": "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
    "mmproj": "mmproj-Qwen2.5-VL-7B-Instruct-f16.gguf"
},
"qwen2.5-vl:3b:4bit": {
    "repo": "bartowski/Qwen2.5-VL-3B-Instruct-GGUF",
    "file": "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf",
    "mmproj": "mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf"
},
"minicpm-v:2.6:4bit": {
    "repo": "openbmb/MiniCPM-V-2_6-gguf",
    "file": "ggml-model-Q4_K_M.gguf",
    "mmproj": "mmproj-model-f16.gguf"
}
```

The current catalog entry shape is `{ repo, file }`. We're adding an optional `mmproj` field. The downloader and llama.cpp adapter need to be taught to fetch and pass `mmproj` (see §3.4).

For SGLang we use the original safetensors repos — different catalog or a separate `engine_hint` field per entry. Cleanest minimum-change option: add a sibling catalog file `data/catalogs/sglang.json` and let `EngineAdapterInstance` pick the right catalog by adapter kind.

### 3.2 `ModelCapabilities` — add `vision` field

File: `src/model/index.rs:27`

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelCapabilities {
    pub chat: bool,
    pub embeddings: bool,
    #[serde(default)]
    pub reranking: bool,
    pub thinking: bool,
    #[serde(default)]
    pub embedding_dims: Option<u32>,
    #[serde(default)]
    pub pooling: Option<String>,

    #[serde(default)]
    pub vision: bool,                  // NEW — model accepts image_url content blocks
    #[serde(default)]
    pub mmproj_path: Option<String>,   // NEW — for llama.cpp, path to multimodal projector
}
```

`#[serde(default)]` keeps the index forward-compatible (existing `models.json` files load fine).

### 3.3 `detect_capabilities()` — recognize VLMs

File: `src/model/index.rs:163`

Inside the `config.json` analysis block, add:

```rust
// --- Vision-language model detection ---
let vlm_model_types = [
    "qwen2_vl", "qwen2_5_vl", "llava", "llava_next", "llava_onevision",
    "internvl", "internvl_chat", "minicpmv", "minicpm_v",
    "mllama", "phi3_v", "phi-3-vision",
];
if vlm_model_types.iter().any(|t| model_type.contains(t)) {
    caps.vision = true;
    caps.chat = true;          // VLMs are chat models that also accept images
    caps.embeddings = false;
}

// Architecture-based fallback (some repos don't set model_type cleanly)
let is_vlm_arch = config["architectures"]
    .as_array()
    .map(|archs| archs.iter().any(|a| {
        let s = a.as_str().unwrap_or("").to_lowercase();
        s.contains("forconditionalgeneration") && (
            s.contains("vl") || s.contains("vision") || s.contains("llava")
        )
    }))
    .unwrap_or(false);
if is_vlm_arch {
    caps.vision = true;
    caps.chat = true;
}
```

Also extend the catalog-id heuristic (`model_id_hint`) — if the id contains `:vl:` or `-vl-` or `vision`, set `caps.vision = true`.

### 3.4 SGLang adapter — VLM support

File: `src/engine/adapters/sglang.rs:105`

SGLang serves VLMs natively but needs the right flags. Add VLM detection and pass:

```rust
// After the existing arg-builder, before spawn:

// VLM models need --chat-template and may need --mem-fraction-static tightened
if let Some(caps) = read_capabilities_from_index(model_id, data_dir) {
    if caps.vision {
        // SGLang ships built-in templates for common VLMs. Map model_type -> template name.
        // Reference: https://github.com/sgl-project/sglang/tree/main/python/sglang/lang/chat_template.py
        let template = if model_id.contains("qwen2.5-vl") || model_id.contains("qwen2_5_vl") {
            "qwen2-vl"
        } else if model_id.contains("qwen2-vl") {
            "qwen2-vl"
        } else if model_id.contains("llava-onevision") {
            "llava_onevision"
        } else if model_id.contains("llava") {
            "vicuna_v1.1"
        } else if model_id.contains("minicpm-v") {
            "minicpmv"
        } else {
            "qwen2-vl" // sensible default
        };
        args.push("--chat-template".to_string());
        args.push(template.to_string());

        // VLMs are memory-hungry on activations; cap their slice so other models fit.
        args.push("--mem-fraction-static".to_string());
        args.push("0.45".to_string()); // ~7 GB on a 16 GB card
    }
}

// Always-on: respect a global tuning knob from config (see §3.7)
```

Helper `read_capabilities_from_index(model_id, data_dir)` already exists in spirit — see how `read_pooling_from_config` is wired at line 150. Either factor it out or call `ModelIndex::load(data_dir).get(model_id)`.

### 3.5 Llama.cpp adapter — VLM via `--mmproj` (fallback / Windows path)

File: `src/engine/adapters/llamacpp.rs`

llama.cpp's `llama-server` supports vision models with:

```
llama-server -m base-model.gguf --mmproj mmproj-model.gguf --port 8080
```

In the adapter `start()`:
- If `caps.vision == true`, look up `mmproj_path` (set during `pull_model` from the catalog `mmproj` field).
- Append `--mmproj <path>` to the args.
- Set `--ctx-size` to ~8192 (VLMs need bigger context for image tokens).

In `pull_model()`:
- After downloading the main `file`, also download the `mmproj` file from the same repo.
- Save mmproj path into `ModelEntry.capabilities.mmproj_path`.

This gives you a llama.cpp VLM path that works on **any** hardware including Windows + the iGPU (llama.cpp has Vulkan and SYCL backends for Intel GPUs).

### 3.6 OpenAI handler — verify multimodal passthrough

File: `src/server/openai.rs:71`

The current handler parses JSON, role-checks, then forwards via `proxy::stream_or_buffer(...)` (read line ~150-200 to confirm). The OpenAI spec for vision is:

```json
{
  "model": "qwen2.5-vl:7b:4bit",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "What is in this image?"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KG..."}}
    ]
  }]
}
```

Two things to verify in `chat_completions`:

1. **`content` array isn't flattened/coerced** anywhere — should pass through `body_value` unchanged.
2. **`check_model_role(..., require_chat: true, ...)`** still passes when `caps.vision == true && caps.chat == true`. Should be fine since the chat check just looks at `entry.capabilities.chat`. **Add** an explicit `require_vision` check helper for clarity:

```rust
fn check_vision_capability(
    index: &ModelIndex,
    model_id: &str,
    body: &serde_json::Value,
) -> Result<(), Response<Body>> {
    // Walk messages[].content[] for any image_url block
    let has_image = body["messages"].as_array()
        .map(|msgs| msgs.iter().any(|m| {
            m["content"].as_array()
                .map(|parts| parts.iter().any(|p| p["type"] == "image_url"))
                .unwrap_or(false)
        }))
        .unwrap_or(false);
    if !has_image { return Ok(()); }

    let entry = match index.get(model_id) {
        Some(e) => e,
        None => return Ok(()),
    };
    if !entry.capabilities.vision {
        let body = format!(
            r#"{{"error":{{"message":"Model '{}' does not support image input. Use a vision model like 'qwen2.5-vl:7b:4bit'.","type":"invalid_request_error"}}}}"#,
            model_id
        );
        return Err(Response::builder()
            .status(StatusCode::BAD_REQUEST)
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(body))
            .unwrap());
    }
    Ok(())
}
```

Wire it after the existing `check_model_role` call.

### 3.7 Bind address + GPU memory tuning — config knobs

LMForge needs two new config fields (likely in `src/config/`):

```toml
# ~/.config/lmforge/config.toml
[server]
bind = "0.0.0.0:11430"   # was "127.0.0.1:11430"

[engine.sglang]
gpu_memory_utilization = 0.85    # passed as --mem-fraction-static globally
chunked_prefill_size = 8192      # batched prefill chunk size
default_mem_fraction_chat = 0.40 # per-slot for chat models
default_mem_fraction_vlm = 0.45  # per-slot for VLM
default_mem_fraction_embed = 0.10
```

In the SGLang adapter `start()`, read these from `EngineConfig` and append to args.

### 3.8 New CLI helper

`lmforge pull` needs to handle the `mmproj` field (download two files, register both). Add a `--vision` flag for clarity:

```bash
lmforge pull qwen2.5-vl:7b:4bit              # downloads .gguf + mmproj + registers vision=true
lmforge models list --capability vision       # filters to VLMs
```

## 4. Build & install on the Ubuntu VM

LMForge ships pre-built binaries for x86_64 Linux. Two paths:

### 4.1 Use upstream binary (no source changes needed yet)

Just to validate the existing daemon works on your hardware:

```bash
# Inside ubuntu-ml VM
curl -fsSL https://github.com/phoenixtb/lmforge/releases/latest/download/install-core.sh | bash
lmforge init                  # detects RTX 5060, installs SGLang
lmforge service install
lmforge service start
lmforge pull qwen3:8b:4bit
curl http://127.0.0.1:11430/v1/models
```

### 4.2 Build with our changes (once §3 is implemented)

```bash
# Inside ubuntu-ml VM (or cross-compile from your Mac)
apt install -y build-essential pkg-config libssl-dev curl git
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
. "$HOME/.cargo/env"

git clone https://github.com/phoenixtb/lmforge.git    # or your fork
cd lmforge
cargo build --release
sudo install -m 755 target/release/lmforge /usr/local/bin/lmforge
lmforge service install
lmforge service start
```

Service unit lives at `~/.config/systemd/user/lmforge.service` and is auto-generated by `lmforge service install`.

Edit `~/.config/lmforge/config.toml` to set `bind = "0.0.0.0:11430"`. Restart:

```bash
systemctl --user restart lmforge
```

Open the firewall (already covered in Doc 1 §10.3).

## 5. VRAM allocation plan for RTX 5060 16 GB

Target: keep these three models hot 24×7 on `ubuntu-ml`.

| Model | Slot type | Q4 size | SGLang `--mem-fraction-static` | Purpose |
|---|---|---|---|---|
| `qwen2.5-vl:7b:4bit` | Vision | ~5.0 GB | 0.45 (~7.2 GB) | Docling OCR for bitmap pages |
| `qwen3:8b:4bit` | Chat | ~4.8 GB | 0.40 (~6.4 GB) | RAG generation |
| `nomic-embed-text:v1.5` | Embed | ~0.3 GB | 0.05 (~0.8 GB) | Document & query embeddings |
| Reserved for KV cache headroom | — | — | ~1.6 GB | Avoid OOM on large contexts |
| **Total** | — | **~10.1 GB hot** | **0.90 budget** | 16 GB card |

LMForge's `EngineManager` will keep all three resident; if a 4th model is requested it evicts LRU. Embed models almost never get evicted (they're tiny and hot-path).

If quality on Q4 VLM isn't enough, fall back to:
- `qwen2.5-vl:3b:4bit` (smaller, faster) — frees ~3 GB
- Or run VLM at Q5/Q6 by editing the catalog file path

## 6. Test plan

Inside the Ubuntu VM after install:

```bash
# 1. Daemon reachable from LAN
curl http://ubuntu-ml.lan:11430/v1/models   # from your Mac

# 2. Embeddings
curl http://ubuntu-ml.lan:11430/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"nomic-embed-text:v1.5","input":"hello world"}'

# 3. Chat
curl http://ubuntu-ml.lan:11430/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b:4bit","messages":[{"role":"user","content":"Say hi"}]}'

# 4. VLM with image
IMG_B64=$(base64 -w0 sample.png)
curl http://ubuntu-ml.lan:11430/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d "{
    \"model\":\"qwen2.5-vl:7b:4bit\",
    \"messages\":[{
      \"role\":\"user\",
      \"content\":[
        {\"type\":\"text\",\"text\":\"Extract all text from this page.\"},
        {\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/png;base64,${IMG_B64}\"}}
      ]
    }],
    \"max_tokens\":2048
  }"

# 5. Multi-model concurrency: hit chat + embed + VLM in parallel
# (use ab / hey / a small Python script — measure latency, no degradation expected
#  beyond per-model GPU contention)

# 6. VRAM check
nvidia-smi    # should show all three model processes resident
```

## 7. Upstream / fork strategy

Three options:

1. **Fork at `github.com/<you>/lmforge`** — keep the changes private, build from source on the Ubuntu VM. Lowest friction.
2. **Open a PR upstream** — VLM support is generally useful; the maintainer is likely to accept clean changes. Slower but you stay on official releases.
3. **Maintain a patch series** — keep upstream binary for the unchanged path, apply a small patch + recompile only the parts you need. Highest maintenance burden, not recommended.

Recommended: option 1 first (validate it all works), then option 2 once the design is proven.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| SGLang VLM template mismatch breaks output | Add an integration test in `tests/` that loads a known image + verifies a known string in the response. Easy to detect upstream regressions. |
| GGUF VLM quality is significantly worse than safetensors | Add a `--engine sglang` override in the catalog so you can force safetensors path for VLM. |
| `--mem-fraction-static` sums >1.0 → OOM at model load | Validate sum at config load time; refuse to start with a clear error. |
| LMForge daemon crash kills all 3 model subprocesses | Existing systemd service `Restart=on-failure` handles it. Verify cold-start time (~30 s for 3 models) is acceptable. |
| `0.0.0.0` bind opens to LAN | Firewall whitelist in Doc 1 §10.3. Optionally add `auth_token` config knob to LMForge later. |
| Windows VM can't share LMForge install | Build a Windows binary too (`cargo build --release --target x86_64-pc-windows-gnu`). Test catalog parses on Windows path conventions. |

## 9. Acceptance criteria

- [ ] LMForge daemon binds `0.0.0.0:11430`, reachable from `your-mac.lan`
- [ ] `lmforge pull qwen2.5-vl:7b:4bit` downloads and registers with `vision=true`
- [ ] `nvidia-smi` shows VLM + chat + embed processes resident
- [ ] Chat completion with `image_url` block returns text describing the image
- [ ] `lmforge status` shows 3 active model slots
- [ ] No regression on existing chat/embed/rerank flows
- [ ] systemd auto-restarts the daemon on crash; models reload within 60 s

---

Done with Doc 2. Doc 3 covers the corresponding DocIntel changes to consume this stack.

---

## Incident note — 2026-08-25: llama-server ran on CPU RAM, host OOM (aist1-ubuntu)

Take this to the LMForge repo. All paths relative to LMForge root.

### What happened
During DocIntel bulk indexing on aist1-ubuntu (RTX 5060 Ti 16 GB, sm_120,
driver 595.84, 15 GB RAM), `llama-server` served embeddings from **CPU RAM**
(~7 GB anon RSS, ~1.6 s/batch), not VRAM. Memory pressure killed Chrome/Cursor
(systemd-oomd), then the kernel OOM-killed llama-server twice
(21:59:58 / 22:00:42; systemd restarted lmforge each time). `nvidia-smi`
showed no llama-server on the GPU. All DocIntel containers stayed healthy.

### Attribution
Not DocIntel (its setup delegates engine/hardware selection to `lmforge init`
by design, and that's correct). Not the variant choice either: cuda12 variant
b9351 is built with CUDA 12.8.1 and archs `86;89;90;120-real;120-virtual`
(`scripts/llamacpp-cuda/variants.conf:6`) — sm_120 kernels are present.
The defect is in LMForge's runtime GPU planning / its silent degradation.

### Prime suspects (triage on the box first)
The spawn log line (`src/engine/adapters/llamacpp.rs:189`) records `ngl=`:
- If `ngl=0` → planning bug: `plan_runtime` (llamacpp.rs:670) sets ngl=0 when
  `free_vram_gb - 1.0 <= 0`, and `resolve_profile_with_vram` (llamacpp.rs:755)
  **silently** falls back to a No-GPU profile when the live probe fails.
  Likely env asymmetry: the systemd user unit sets only `Environment=PATH=…`
  (`src/cli/service.rs:227-247`) — nvidia-smi/NVML may be unavailable in the
  service env while `lmforge doctor` (interactive shell) sees the GPU fine.
- If `ngl=99` + `ggml_cuda_init` failure in engine logs → spawn-env bug
  (bundled libcudart/cublas not on the child's LD_LIBRARY_PATH).

Triage commands: engine logs under `~/.lmforge/` for `ngl=` and
`ggml_cuda_init`; while a model is loaded, `readlink /proc/$(pgrep -f
llama-server)/exe` (confirm it's `engines/llamacpp/variants/cuda12/…`);
compare `systemd-run --user --pty nvidia-smi` vs interactive.

### Defects to fix (ranked)
1. **Silent CPU degradation.** Probe failure → No-GPU profile → ngl=0 with no
   surfacing. Must WARN in logs, flag in `/lf/status` + `doctor`
   (e.g. `gpu_offload: none (probe failed)`), ideally refuse/confirm when
   model_size > share of free host RAM.
2. **Probe robustness under systemd.** Prefer cached `hardware.json`
   (written by init, correct) over live-probe-failure degradation; make the
   unit env sufficient for NVML/nvidia-smi.
3. **No RAM admission control on the CPU path.** A ~7 GB CPU load was
   admitted on a 15 GB box running a full docker stack → host-wide OOM. The
   VRAM-aware admission (adapter.rs plan) should gate host RAM too.
4. **Version skew (minor).** Registry pins llamacpp b9861 (engines.toml:116,
   bumped for Windows Blackwell fixes) but Linux variant tarballs are still
   b9351; doctor prints "version=b9861" while executing the b9351 variant —
   misleading, and the Blackwell runtime fixes may be relevant on Linux too.
   Rebuild Linux variants at b9861 or document the skew.
5. **Optional:** on sm_120, consider defaulting init to cuda13 once it's no
   longer `opt_in_only` (torch backend already has `compute_cap_needs_cu130`).

### Correction to earlier advice
The 2026-08-25 suggestion to install the cuda13 variant + env override was a
workaround guess, not the root cause — cuda12 has sm_120 kernels. Harmless if
already applied, but the fixes above are the real ones.

### RESOLVED — 2026-09-04: true root cause found via on-box triage (supersedes "Prime suspects" above)
Both prime suspects were wrong. On-box log triage + live test proved GPU
planning and execution were always fine: every run planned ngl=99, spawned the
cuda12 binary, llama-server detected CUDA0 (~14 GB free, sm_120 kernels
present), and a live embed request today sits at 4.7 GB VRAM / 0.6 GB RSS.

Actual root cause: upstream llama.cpp (bundled b9351) enables a **host-RAM
prompt cache by default at 8192 MiB** (ggml-org/llama.cpp PR #16391), and its
idle-slot save path has no task-type guard, so an embed server writes ~56 MiB
of KV state per prompt into host RAM — write-only, never read back (upstream
issue #26293); the cap isn't even enforced on Linux due to overcommit
(#22629). LMForge passed `--cache-ram` only for chat, assuming the default was
off. During bulk indexing the cache grew until the kernel OOM-killed
llama-server (spawn 16:29:07 UTC → kill 16:29:58 = 21:59:58 IST; respawn →
kill again; third spawn survived once Chrome/Cursor were already dead).

Fixed in LMForge (2026-09-04): embed/rerank now always pass `--cache-ram 0`;
plus hardening — cached hardware-identity owner (no more scattered live
probes), free-VRAM probes return Option with ledger fallback instead of silent
CPU degradation, host-RAM admission gate for partial-ngl loads, ngl surfaced
in /lf/status, doctor probe-failure check, and engine list/status now report
the installed variant's real build tag (b9351) instead of the registry pin
(b9861). The systemd unit env and cuda13 items were disproven/unnecessary.
