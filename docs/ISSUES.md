# GitHub execution roadmap

This repository contains an offline-tested **starter**, not a production deployment. The following issues are the operational execution queue for a coding agent with access to the intended Linux server.

| Phase | Issue | Exit evidence |
|---|---|---|
| P0 | [#2 — Host preflight](https://github.com/anatwork14/herdr-hermes-openclaw/issues/2) | Local tests, installed CLI versions and environment report |
| P1 | [#3 — Herdr cross-agent runtime](https://github.com/anatwork14/herdr-hermes-openclaw/issues/3) | Real Codex and Claude in separate worktrees, handoff and recovery test |
| P2 | [#4 — Scoped memory and shared skills](https://github.com/anatwork14/herdr-hermes-openclaw/issues/4) | Evidence retrieval, isolation tests, skill discovery |
| P3 | [#5 — OpenClaw + Discord](https://github.com/anatwork14/herdr-hermes-openclaw/issues/5) | One allowlisted bot, real inbound/outbound messages, authenticated channel/thread mapping |
| P4 | [#6 — Hermes orchestration](https://github.com/anatwork14/herdr-hermes-openclaw/issues/6) | Task graph, real implementation and independent review, compact Discord summary |
| P5 | [#7 — Token and security benchmarks](https://github.com/anatwork14/herdr-hermes-openclaw/issues/7) | Reproducible token/cost and task-success comparisons, negative authorization tests |

## Agent execution rules

1. Start from [MASTER_AGENT_PROMPT.md](../MASTER_AGENT_PROMPT.md) and [docs/GITHUB_SETUP.md](GITHUB_SETUP.md).
2. Work from a feature branch/worktree and link every PR to its issue. Do not mark a live gate PASS without logs/test evidence.
3. Run independent review for security-sensitive gateway or cross-project code changes.
4. Do not write credentials to Git, Discord messages, or SQLite.
5. Do not create or configure a Discord bot, install new paid dependencies, push/merge production code, or run paid/GPU workloads without explicit owner authorization.
6. Keep [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) current as evidence is gathered.

**Recommended first task:** #2, using the read-only `scripts/preflight.sh` and offline tests.
