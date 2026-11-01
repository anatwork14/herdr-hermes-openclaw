# Implementation status

**GitHub publication:** Source was merged into `main` through [PR #1](https://github.com/anatwork14/herdr-hermes-openclaw/pull/1) on 2026-11-01. Six operational issues (#2–#7) track the remaining host-side work. No live server or Discord configuration has been applied.


| Gate | Status | Evidence | Unresolved |
|---|---|---|---|
| P0 offline control-plane | PASS (authoring environment) | 24 unittest cases green; Python 3.13; local CLI verified on 2026-11-01 | Re-run on target server |
| P1 Herdr + agent smoke test | BLOCKED | Requires actual Herdr server and authenticated CLIs | Host access |
| P2 Memory + shared skills | PARTIAL | SQLite implementation exists; CLI + skill discovery require verification | Host config |
| P3 Discord/OpenClaw | BLOCKED | Integration instructions provided | Bot token, IDs, gateway |
| P4 Supervisor orchestration | DESIGN | Task checkpoint table/skills | Live Hermes/worker tests |
| P5 token efficiency benchmark | NOT STARTED | Evaluation plan | Real agent telemetry |

| Discord layout and credentials | DESIGN/TESTABLE OFFLINE | `herdctl discord-plan`, `docs/DISCORD_DESIGN.md`; no live bot | Create channels and verify gateway / owner allowlist |
| Role memory token budgets | DESIGN/TESTABLE OFFLINE | `herdctl budget`, `docs/TOKEN_POLICY.md`; soft targets | Measure provider and Herdr CLI usage in live tasks |
| Discord thread parent routing | PARTIAL | Local function requires trusted parent metadata; offline tests | Build/validate OpenClaw metadata adapter on server; no unverified parent dispatch |

The implementing agent must replace these placeholder statuses with evidence after verification. Never mark live gates PASS based on offline mocks.

### Security remediation branch (2026-11-01)

A dedicated branch fixes four reproduced runtime/memory correctness defects and adds adversarial regression tests. The authoring copy passed **41 offline tests**, but this **does not verify real Herdr compatibility** or deployed Discord permissions. See [REMEDIATION_2026-11-01.md](REMEDIATION_2026-11-01.md). Pending: rerun full PR suite, independent Opus review, exact live Herdr API schema, safe owner-approved agent handoff and gateway integration. P1/P3/P4 remain **BLOCKED**.
