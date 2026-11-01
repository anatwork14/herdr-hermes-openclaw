# Implementation status

**GitHub publication:** Source was merged into `main` through [PR #1](https://github.com/anatwork14/herdr-hermes-openclaw/pull/1) on 2026-11-01. Six operational issues (#2–#7) track the remaining host-side work. No live server or Discord configuration has been applied.


| Gate | Status | Evidence | Unresolved |
|---|---|---|---|
| P0 offline control-plane | PASS | 114/114 unittest cases passing on target Linux host; GitHub Actions CI quality gate passing; PR #8 merged into `main` via squash commit `e42174d812429fdf3ee99f809968aa23f20ca639`; Issue #2 closed; Opus 5.5 verdict PASS | None (P0 closed) |
| P1 Herdr + agent smoke test | PASS | Live Herdr 0.9.0 two-agent handoff executed in workspace `w5` (`starlight-tempo`); `st_codex` implemented CPU utility `compute_tempo_error` and test `tests/test_p1_tempo_error.py` (commit `a5968492`, 5/5 pytest passing); `st_claude` independently reviewed diff via Herdr and issued PASS verdict; live `working`/`blocked` busy rejection and cross-project access rejection verified | None |
| P2 Memory + shared skills | PASS (offline) | SQLite WAL mode, FTS5 token sanitization, scoped isolation, graph deduplication, and secret redaction verified under 12-thread concurrency | Shared skill discovery live integration |
| P3 Discord/OpenClaw | PARTIAL | OpenClaw Discord gateway running (task-758); real delivery to `#starlight-tempo`, `#federated-learning-iot`, and `#alerts` verified; channel ID allowlist enforced | Live incoming thread dispatch & owner approval adapter |
| P4 Supervisor orchestration | DESIGN | Task checkpoint table/skills defined | Live Hermes/worker tests |
| P5 token efficiency benchmark | NOT STARTED | Evaluation plan | Real agent telemetry |

| Discord layout and credentials | DESIGN/TESTABLE OFFLINE | `herdctl discord-plan`, `docs/DISCORD_DESIGN.md`; no live bot | Create channels and verify gateway / owner allowlist |
| Role memory token budgets | DESIGN/TESTABLE OFFLINE | `herdctl budget`, `docs/TOKEN_POLICY.md`; soft targets | Measure provider and Herdr CLI usage in live tasks |
| Discord thread parent routing | PARTIAL | Local function requires trusted parent metadata; offline tests | Build/validate OpenClaw metadata adapter on server; no unverified parent dispatch |

The implementing agent must replace these placeholder statuses with evidence after verification. Never mark live gates PASS based on offline mocks.

### Security remediation branch (2026-11-01)

Branch `fix/agent-binding-and-memory-isolation` (PR #8, commits `381f20b`, `7efc820`, `988c81c`, `a2005ce`) was squashed and merged into `main` as `e42174d812429fdf3ee99f809968aa23f20ca639` on 2026-11-01 with owner authorization. All 114 tests pass across Python 3.11 and 3.12 CI matrix and local host. Issue #2 closed.

### P1 Live Agent Handoff Evidence (2026-11-01)

- **Target Workspace:** `w5` (`starlight-tempo`), protocol 22, Herdr 0.9.0.
- **Implementer:** `st_codex` (pane `w5:p1`), worktree `.worktrees/starlight-tempo/st_codex`, branch `herdr/st_codex`.
- **Reviewer:** `st_claude` (pane `w5:p2`), worktree `.worktrees/starlight-tempo/st_claude`, branch `herdr/st_claude`.
- **Task:** CPU-only utility function `compute_tempo_error` in `benchmarks/evaluation/smoke_p1.py` with unit tests in `tests/test_p1_tempo_error.py`.
- **Implementer Output:** Created files and committed `a5968492bf66a5720e53ebb9c080e7016be6321f` to `herdr/st_codex`. Pytest verification: 5/5 passing in 0.01s.
- **Reviewer Output:** `st_claude` inspected commit `a5968492` via Herdr prompt/read, evaluated edge cases (`ground_truth_bpm <= 0`, NaN, Inf) and assertions, and returned an independent **PASS** code review.
- **Runtime Defenses & Boundaries Verified Live:**
  - `working` status rejects prompt attempt: `ERROR: Agent is not ready for a new prompt (state='working')`.
  - `blocked` status rejects prompt attempt: `ERROR: Agent is not ready for a new prompt (state='blocked')`.
  - Cross-project access denied: `ERROR: Agent st_codex is not registered under project federated-learning-iot; refusing cross-project control`.
  - Missing `--yes` flag rejected: `ERROR: Prompting live agent requires --yes`.
  - Stale pane and drift detection: validated pre- and post-read.
  - Recovery without session corruption: rejection of unreviewed package install via `esc` cleanly transitioned `st_codex` back to interactive prompt.
- **Task State Checkpoint:** Recorded `p1-smoke-handoff` as `done` in `.state/memory.sqlite3`.
