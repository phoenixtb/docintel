# Lessons

Patterns from corrections — review at session start.

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
