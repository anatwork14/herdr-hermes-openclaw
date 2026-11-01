# Implementation status

**GitHub publication:** Source is published on a feature branch for review. No live server or Discord configuration has been applied by ChatGPT. The repository PR and branch status must be verified independently after publishing.


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
