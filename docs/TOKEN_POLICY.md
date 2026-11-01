# Token architecture — credentials vs usage budgets

**Two meanings of token:** (A) Discord bot / model-provider credentials; (B) input/output/context usage measured in LLM tokens. Never mix these. `docs/DISCORD_DESIGN.md` contains credential inventory and permissions. This document describes (B).

## Proposed budget-aware operation

| Role | Typical runtime | Initial retrieved memory target | Follow-up memory target | Suggested short reply target | Purpose |
|---|---|---:|---:|---:|---|
| Gateway | OpenClaw | **0** | **0** | 250 | deterministic routing / status |
| Supervisor | Hermes | **1,600** | **450** | 800 | task DAG and handoffs |
| Implementer | Codex | **1,200** | **350** | 1,200 | code and tests |
| Reviewer | Claude | **1,600** | **400** | 900 | evidence-based code/scientific review |
| Explorer | Antigravity | **1,100** | **300** | 1,000 | bounded exploration |

These are *approximate target sizes for selected memory/handoff context and desired summaries*, not hard model context-window limits and not estimates of total consumption. Actual model calls include system prompts, tool schema, file reads, tool outputs, planning tokens, output, and vendor caching. They can be far larger. The current app only computes a rough character-based memory budget and displays soft targets. Agent-specific output caps are NOT configured/enforced here.

## Efficient flow

1. Gateway resolves project from trusted Discord metadata without reading memory; if the request is simple status, use `herdctl` directly.
2. Supervisor loads global rules and project decisions (at most initial budget); creates a bounded task/checkpoint with acceptance criteria.
3. Implementer receives task ID, constrained objective, necessary memory links and repository path; source code is read locally as needed, not fully injected.
4. Reviewer receives only relevant commit SHA/diff, tests, constraints and up to reviewer memory budget. The implementer's prior conversation is NOT replayed.
5. Later turns use delta memory (~300–450 tokens), source pointers and short progress checkpoints. Do not inject the same facts repeatedly.
6. On conclusion, persist only validated durable decisions and outcomes with evidence; archive task thread; avoid storing every intermediate token.

## Actual accounting

For each task, collect total provider-reported **input, output, cache-read/cache-write**, memory extraction tokens (if any), and error/retry cost. Also record elapsed time, pass/fail, memory recall correctness, cross-project leakage and number of user-facing notifications. OpenClaw offers `/usage tokens`, `/usage full`, `/context detail`, `openclaw status --usage` and `/usage cost`, but Herdr-managed external CLIs have separate provider authentication and usage attribution: **do not assume OpenClaw reports their tokens**. Track missing telemetry as unknown, not zero.

Test four memory strategies on identical tasks: none, full-history, FTS5, FTS5+edges. The relevant metric is total spend/calls and task accuracy, not just the length of a single retrieved snippet. A >=40% input-context reduction is an aspirational benchmark against full-history, NOT proven performance.

## Quick inspection

```bash
herdctl budget --role gateway
herdctl budget --role supervisor
herdctl budget --role implementer --followup
herdctl budget --role reviewer
herdctl memory context --project starlight-tempo --budget 1200 "benchmark gate"
```

Keep model budgets per *role*, not per Discord channel; new projects inherit the same defaults. Raise budgets only for measured retrieval failures. Use access-control rules, not token budgets, for security boundaries.
