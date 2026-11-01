# Implementation status

**GitHub publication:** Source was merged into `main` through [PR #1](https://github.com/anatwork14/herdr-hermes-openclaw/pull/1) on 2026-11-01. Six operational issues (#2–#7) track the remaining host-side work. No live server or Discord configuration has been applied.


| Gate | Status | Evidence | Unresolved |
|---|---|---|---|
| P0 offline control-plane | PASS | 114/114 unittest cases passing on target Linux host (Python 3.12.14); GitHub Actions CI quality gate passing (Python 3.11 & 3.12 matrix); comprehensive concurrency, FTS5 injection, secret redaction, and Herdr 0.9.0 schema regression suites green; Opus 5.5 verdict PASS | Awaiting owner approval to merge PR #8 |
| P1 Herdr + agent smoke test | PARTIAL | Herdr 0.9.0 daemon running (`/mnt/ssd/genos2/.config/herdr/herdr.sock`); `st_codex` and `st_claude` workspaces created; offline contract verified; live prompt/read handoff offline tested | Owner-approved live execution tests |
| P2 Memory + shared skills | PASS (offline) | SQLite WAL mode, FTS5 token sanitization, scoped isolation, graph deduplication, and secret redaction verified under 12-thread concurrency | Shared skill discovery live integration |
| P3 Discord/OpenClaw | PARTIAL | OpenClaw Discord gateway running (task-758); real delivery to `#starlight-tempo`, `#federated-learning-iot`, and `#alerts` verified; channel ID allowlist enforced | Live incoming thread dispatch & owner approval adapter |
| P4 Supervisor orchestration | DESIGN | Task checkpoint table/skills defined | Live Hermes/worker tests |
| P5 token efficiency benchmark | NOT STARTED | Evaluation plan | Real agent telemetry |

| Discord layout and credentials | DESIGN/TESTABLE OFFLINE | `herdctl discord-plan`, `docs/DISCORD_DESIGN.md`; no live bot | Create channels and verify gateway / owner allowlist |
| Role memory token budgets | DESIGN/TESTABLE OFFLINE | `herdctl budget`, `docs/TOKEN_POLICY.md`; soft targets | Measure provider and Herdr CLI usage in live tasks |
| Discord thread parent routing | PARTIAL | Local function requires trusted parent metadata; offline tests | Build/validate OpenClaw metadata adapter on server; no unverified parent dispatch |

The implementing agent must replace these placeholder statuses with evidence after verification. Never mark live gates PASS based on offline mocks.

### Security remediation branch (2026-11-01)

Branch `fix/agent-binding-and-memory-isolation` (PR #8, commits `381f20b`, `7efc820`, `988c81c`, `a2005ce`) fixes four reproduced runtime/memory defects (H1, H2, M1, M2), resolves the live Herdr 0.9.0 `agent_status` schema mismatch, hardens PKCS#8/PGP/DSA secret redaction across all task/memory fields, deduplicates graph BFS traversals with hop clamping `[0..3]`, adds GitHub Actions CI quality gate (`.github/workflows/test.yml`), and adds two concurrency/regression suites (`tests/test_memory_concurrency.py` and `tests/test_runtime_concurrency.py`). All **114 tests pass offline on target host** in 17.175s (`scripts/selftest.sh` verified clean) and all GitHub Actions CI checks pass. **Opus 5.5 Independent Review verdict: PASS** (re-review required: NO). Ready for owner merge approval into `main`.
