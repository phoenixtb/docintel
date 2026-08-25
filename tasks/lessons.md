# Lessons

Patterns from corrections — review at session start.

## 2026-08-15 — Wrong assumption: "LMForge is Apple-Silicon-only"
- **What happened:** Audited the fresh-Ubuntu bootstrap assuming LMForge = MLX
  = macOS-only and recommended defaulting to Ollama on Linux. LMForge is
  cross-platform (oMLX / SGLang / llama.cpp CUDA / Vulkan / CPU) with catalog
  shortcuts that resolve MLX-on-macOS, GGUF-elsewhere. LMForge IS the intended
  default engine everywhere.
- **Rule:** LMForge facts live at github.com/phoenixtb/lmforge (user's own
  project, sibling repo at ../lmforge). Check its README/catalogs before any
  claim about platform support, model tags, ports, or bind behavior. Key
  gotchas: binds 127.0.0.1 by default (containers on native Linux can't reach
  it without LMFORGE_BIND=0.0.0.0); /v1/rerank is 501 on SGLang; quant-variant
  availability differs between mlx.json and gguf.json catalogs.

## 2026-08-07 — Announced follow-up action never executed
- **What happened:** After a subagent crashed, I committed a small cleanup and
  ended the turn without actually resuming the subagent — while my message said
  the resume was next. The user had to catch it ("I believe the sub agent
  didn't start").
- **Rule:** Before ending a turn, re-read the last paragraph of my own message:
  every promised action ("resuming now", "launching next") must have a
  corresponding tool call in that same turn. A cleanup step in between does not
  count as done.

## 2026-08-07 — Subagent "PING timed out" notifications can be spurious
- **What happened:** Treated a timeout notification as a crash and attempted a
  second resume; the agent was actually still running (resume API rejected it).
- **Rule:** On a subagent error notification, first check the transcript tail
  and repo state for fresh activity. If a resume attempt fails with "currently
  running", the agent is alive — do not interrupt; wait for real completion.
  Design subagent briefs for crash-resilience anyway: commit per sub-item.
