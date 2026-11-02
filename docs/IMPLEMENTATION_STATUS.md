# Implementation status

**GitHub publication:** Source was merged into `main` through [PR #1](https://github.com/anatwork14/herdr-hermes-openclaw/pull/1) on 2026-11-01. Six operational issues (#2–#7) track the remaining host-side work. No live server or Discord configuration has been applied.


| Gate | Status | Evidence | Unresolved |
|---|---|---|---|
| P0 offline control-plane | PASS | 114/114 unittest cases passing on target Linux host; GitHub Actions CI quality gate passing; PR #8 merged into `main` via squash commit `e42174d812429fdf3ee99f809968aa23f20ca639`; Issue #2 closed; Opus 5.5 verdict PASS | None (P0 closed) |
| P1 Herdr + agent smoke test | PASS | Live Herdr 0.9.0 two-agent handoff executed in workspace `w5` (`starlight-tempo`); `st_codex` implemented CPU utility `compute_tempo_error` and test `tests/test_p1_tempo_error.py` (commit `a5968492`, 5/5 pytest passing); `st_claude` independently reviewed diff via Herdr and issued PASS verdict; live `working`/`blocked` busy rejection and cross-project access rejection verified; Issue #3 closed; Opus 5.5 verdict PASS | None (P1 closed) |
| P2 Memory + shared skills | PASS | Memory lifecycle (`active`/`superseded`/`retracted`), cross-project negative access, token budget clamping `[100..10000]`, and 5 canonical shared skills discoverable live across installed Codex, Claude, and Hermes; 35 dedicated + live agent tests passing | Issue #4 review gate |
| P3 Discord/OpenClaw | READY FOR OWNER SMOKE TEST | P3-A Hardened: trusted inbound Discord gateway router (`src/herdctl/discord_gateway.py`), adapter auth boundary, guild/author allowlist, thread ancestry resolver, durable SQLite idempotency lifecycle (`received` -> `processing` -> `completed`/`failed`/`uncertain`), atomic immediate reservation, concurrency protection, agent lease fencing with monotonic generations, conflicting replay rejection, per-project status isolation, bounded responses (<=2000 chars), OpenClaw inbound adapter (`integrations/openclaw/openclaw_discord_adapter.py`) restricted to 5 read-only commands (`status`, `agents`, `recall`, `tasks`, `budget`); 215/215 total suite green | P3-B live bounded dispatch pending review & owner approval |
| P4 Supervisor orchestration | DESIGN | Task checkpoint table/skills defined | Live Hermes/worker tests |
| P5 token efficiency benchmark | NOT STARTED | Evaluation plan | Real agent telemetry |

| Discord layout and credentials | READY | `herdctl discord-plan` status `READY_FOR_MANUAL_CONFIGURATION`, 0 missing fields; layout aligned with OpenClaw | None |
| Role memory token budgets | PASS | `herdctl budget` validated against `config/token_policy.example.json`; retrieval budget clamping `[100..10000]` enforced | None |
| Discord thread parent routing | PASS (P3-A) | Trusted gateway resolver in `src/herdctl/discord_gateway.py` validates parent channel metadata against project registry; unmapped/forged threads rejected | Live Discord event testing |

The implementing agent must replace these placeholder statuses with evidence after verification. Never mark live gates PASS based on offline mocks.

### Phase P2 & P3-A Integration Evidence (2026-11-01)

- **Integration Commit:** `feat/p2-p3-integration` (`a24576a`).
- **Full Test Suite:** **192 / 192 tests passing** in 24.711s (`unittest discover -s tests`).
- **Self-Test Script:** `scripts/selftest.sh` reports clean doctor status.
- **P2 Deliverables:**
  - Memory lifecycle state machine (`active`, `superseded`, `retracted`) with status filtering in `search()` and `graph()`.
  - Canonical shared skills (`git-worktree`, `herdr-control`, `memory-recall`, `scientific-review`, `task-handoff`) in `src/herdctl/skills.py` with multi-agent discovery for Codex (`~/.agents/skills`), Claude Code (`~/.claude/skills`), and Hermes Agent (`skills.external_dirs`).
  - Strict negative access: cross-project queries, link creation, and graph traversals between `starlight-tempo` and `federated-learning-iot` strictly denied.
- **P3-A Deliverables:**
  - Trusted inbound gateway router (`src/herdctl/discord_gateway.py`):
    - Guild ID allowlist: matches `1556635278846992404`.
    - Author ID allowlist: matches `734391788718129205`. Bot messages rejected.
    - Authoritative thread ancestry resolver: threads must have valid `parent_channel_id` matching project channel. Forged/missing parents rejected.
    - Idempotency & replay protection: SQLite-backed (`gateway_idempotency` table) + memory index.
    - 5 read-only commands: `status`, `agents`, `recall`, `tasks`, `budget`. Shell commands, metacharacters, and path traversal strictly blocked.

### PR #9 Hardening Evidence — Authentication, Idempotency and Live Integration (2026-11-02)

- **Starting SHA:** `55a3bfd751856e208961b2ea6e8173286ed9ec95`.
- **Full Test Suite:** **208 / 208 tests passing** (`unittest discover -s tests -v`).
- **Self-Test Script:** `scripts/selftest.sh` reports clean compilation, 208 passing tests, and doctor check passing.
- **Stage 1 (Haiku Worker A) Authenticated Discord Events:**
  - Trusted Gateway-Adapter Boundary: `adapter_token` authentication enforced against configured secret/env token (`HERDCTL_GATEWAY_TOKEN`); untrusted adapter callers strictly rejected with `GatewaySecurityError`.
  - Zero Production Ingress Defaults: Production ingress rejects missing `message_id`, `guild_id`, `channel_id`, or `author_id` without fallback defaults.
  - Explicit Synthetic Mode: `--synthetic` CLI flag and `is_synthetic` property demarcate synthetic test fixtures from live transport events.
  - Per-Project Status Isolation: `status` requests in `#starlight-tempo` return strictly scoped status without exposing `#federated-learning-iot` or unrelated projects.
  - Response Boundary: All Discord responses safely formatted and truncated to <= 2000 characters (max 1950 chars + truncation indicator).
- **Stage 2 (Haiku Worker B) Durable Request Idempotency:**
  - Request Lifecycle States: `received` -> `processing` -> `completed`, plus `failed` and `uncertain`.
  - Atomic Reservation: SQLite `BEGIN IMMEDIATE` transaction atomically reserves `message_id` before command dispatch, preventing concurrent handler races.
  - Crash Recovery: Stale requests in `processing` past timeout are recovered cleanly (read-only queries reclaimed; prompt/mutating tasks transition to `uncertain` with automatic retries prohibited).
  - Storage Reliability: SQLite errors during idempotency operations raise `IdempotencyError` and are never silently swallowed.
  - Serialized Per-Agent Queue: `check_agent_busy()` and `list_agent_queue()` verify agent availability before dispatch.
  - Duplicate Delivery Replay: Read-only duplicate requests produce deterministic results without redundant dispatch/model executions.
- **Stage 3 Shared Skills Live Verification:**
  - Canonical Skills: All 5 canonical skills (`git-worktree`, `herdr-control`, `memory-recall`, `scientific-review`, `task-handoff`) in `skills/shared/` verified live.
  - Codex CLI: Verified via `~/.agents/skills` (5/5 discovered).
  - Claude Code: Verified via `~/.claude/skills` (5/5 discovered).
  - Hermes Agent: Configured via `skills.external_dirs: [~/.agents/skills]`, verified live via `hermes skills list` (5/5 enabled as external/local).
  - Preexisting user configurations preserved without unreviewed modification.
- **Installed Runtime Versions:**
  - OpenClaw: 2026.10.9 (`8b18b55`)
  - Herdr: 0.9.0
  - Hermes Agent: v0.22.5 (`2026.9.24`)
  - Codex CLI: 0.163.0
  - Claude Code: 2.1.316
  - Antigravity CLI: 1.4.12

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

### PR #9 Final Security Gate and P3-A Live Readiness (2026-11-02)

- **Total Test Suite:** **215 / 215 tests passing** across all modules.
- **Worker A — Trusted Authentication Boundary:**
  - Expected adapter credential strictly loaded from server-side configuration/environment (`HERDCTL_GATEWAY_TOKEN`, `HERDCTL_GATEWAY_SECRET`, or `layout['gateway_adapter']['auth_token']`). Callers cannot set or override expected secrets.
  - Mandatory in production: `DiscordGateway(..., production=True)` immediately fails closed if server-side credential is missing.
  - Production ingress strictly separates from synthetic testing: production `herdctl discord-gateway` rejects untrusted synthetic escalation (`"is_synthetic": true`); offline fixtures use dedicated `herdctl discord-gateway-test`.
  - Authenticated thread ancestry: thread parent ID derived exclusively from trusted Discord transport metadata (`parent_channel_id` / `parentId`), verified against project mapping.
- **Worker B — Idempotency and Dispatch Safety:**
  - Full request lifecycle binding: binds message ID to `(guild_id, author_id, channel_id, parent_channel_id, project, command, arguments)`.
  - Conflicting replay rejection: any attempt to replay a message ID with altered arguments, commands, or channels is rejected with `IdempotencyError`.
  - Atomic reservation and agent lease acquisition: uses SQLite `BEGIN IMMEDIATE` transaction ensuring only one active dispatch leases an agent.
  - Monotonic lease generation and stale session reconciliation: protects against concurrent dispatch races and agent collision.
  - Conditional state transitions: transitions conditioned on `WHERE message_id = ? AND status = 'processing'`. Validation errors never mutate database records.
- **Sonnet — OpenClaw Inbound Adapter:**
  - Adapter implementation: `integrations/openclaw/openclaw_discord_adapter.py`.
  - Enforces strict read-only boundary: permits ONLY `status`, `agents`, `recall`, `tasks`, and `budget`.
  - Mutating or shell commands (`prompt`, `launch`, `notify`, `exec`, `sh`, `bash`) strictly rejected with `OpenClawCommandError`.
  - Local authenticated execution: calls `herdctl discord-gateway` via authenticated local subprocess boundary using `HERDCTL_GATEWAY_TOKEN`.

