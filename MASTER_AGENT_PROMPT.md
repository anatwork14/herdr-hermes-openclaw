# MASTER IMPLEMENTATION PROMPT — Herdr-first Multi-Agent Workspace

**Paste this entire document into your lead coding agent (prefer Codex or Claude Code) after the repository is on your Linux host.**

## Your role
Act as Senior AI Systems Engineer, DevOps Implementer and Scientific Software Reviewer. You are the lead implementer; appoint a separate independent reviewer *through Herdr* after runtime prerequisites pass. Your mission: complete, verify, and document a minimal production-oriented Herdr-first Discord multi-agent system with global/project graph memory and shared skills. Do not replace Herdr with tmux, Docker-based agent managers, or a bespoke orchestration framework.

## User intent (non-negotiable)
1. Discord is the user interface. Each real project corresponds to one Discord channel. Project-specific work stays in that channel.
2. Multiple coding agents (Codex, Claude, Antigravity CLI, Hermes) may run simultaneously. Herdr owns sessions/workspaces/panes and supports cross-agent observation and controlled prompt submission.
3. Hermes is a supervisor, not a second runtime. OpenClaw is the Discord gateway, not the manager of an independently duplicated worker session.
4. Persistent global memory, project-scoped graph memory and temporary task checkpoints must be useful to *all* agents, with evidence and isolation.
5. Shared skills are canonical SKILL.md files, with project-local overrides, progressive disclosure and review-gated edits.
6. Minimize token cost and complexity. Start SQLite FTS5 + typed graph edges; only investigate vector memory/Graphiti after measurement.
7. Primary initial projects (when repositories exist locally): STARLIGHT-TEMPO and federated-learning-in-iot. Do not assume machine paths, API credentials or approval to clone/download.

## Start with preflight, not mutation
Read README.md, AGENTS.md, docs/DEPLOYMENT.md, docs/SECURITY.md, docs/DISCORD_DESIGN.md, docs/TOKEN_POLICY.md, docs/ARCHITECTURE.md, source and tests. Inspect Linux distro, current user capabilities, PATH, installed Herdr CLI version/schema, Herdr session status, OpenClaw config shape, Hermes config, Codex/Claude/Antigravity CLI presence/auth, disk, process lifetime and repository locations. Record verified results, unknowns and risks in `docs/IMPLEMENTATION_STATUS.md` with timestamps. Never display secrets or access tokens. Do not rely on stale commands in external blog posts.

## P0 — Local engine (no network and no GPU)
- Install Python project in venv using `bash scripts/bootstrap.sh` (if dependencies already available; don't automatically use a remote installer).
- Run unittest suite and add any missing isolation/security coverage.
- Check SQLite schema, FTS5, token-budget context, evidence requirements, typed edges, and bad-credential guard.
- Ensure code is simple and resilient: no shell interpolation, no guessed API fields, bounded subprocess timeouts, no silent destructive cleanup.
- If defects are found, fix and add regression tests. Stage commits locally in own worktree.
- Gate P0: documented local tests pass.

## P1 — Herdr runtime
- Use the installed Herdr's `herdr api schema --json`, `herdr --help`, `herdr agent start --help`, `herdr agent prompt --help` to check contracts.
- Launch `herdr server` headlessly only with operator approval and a safe process supervisor. Do not restart/kill running sessions.
- Install Herdr agent integrations when appropriate after reviewing config diffs; do not destroy preexisting hooks.
- Verify create workspace response `.result.root_pane.pane_id`; build topology explicitly, then `agent start <name> --kind ... --pane <pane_id>`.
- Launch ONE Codex worker for a no-change inspection and ONE Claude reviewer into isolated worktrees. If binaries/auth aren't available, leave P1 blocked with actionable steps; never fake success.
- Verify send/read/wait and that blocked permission prompts are not bypassed. A session's `done` must not be treated as verified task completion.
- Validate restart/recovery without interrupting user tasks. Protect against stale pane bindings and aliases.
- Gate P1: live Herdr-to-Codex and Herdr-to-Claude handoff, recorded output and real Git evidence.

## P2 — Memory + skills
- Populate ONLY explicitly verified durable memories with source IDs. Demonstrate global read in both projects and strict denial of another project's private memory.
- Test relationship traversal, superseding a decision and task checkpoint retrieval.
- Use `herdctl memory context` with a per-task budget and compare with naive full-history loading.
- Install shared skill symlinks using dry-run first. Merge Hermes `skills.external_dirs` and OpenClaw `skills.load.extraDirs` non-destructively. Verify native skill discovery by Codex, Claude, Hermes, and optional Antigravity.
- Permissions: canonical shared skill source is review-gated; agents may propose changes in a branch, not update production without approval.
- Gate P2: all available agents can retrieve the same appropriate memory/skills; cross-project tests remain green.

## P3 — Discord/OpenClaw and budget-aware channel structure
- Apply `docs/DISCORD_DESIGN.md` topology: ONE OpenClaw bot token, control channels (`#agent-control`, owner-only `#approvals`, outbound `#alerts`), one project channel for each project, and task threads; `#system-status` optional. Do not mint Discord bot tokens per agent/worktree.
- Fill gitignored `config/discord_layout.json` and `config/projects.json` with verified Discord IDs; run `herdctl discord-plan`. Keep all provider credentials outside JSON and Git.
- Run `herdctl budget --role supervisor` etc. Use bounded memory summaries on initial/follow-up requests. Do not interpret these targets as provider-side hard spending limits; collect real telemetry after deployment.
- Incoming thread IDs require the trusted parent channel ID; implement and verify a platform-metadata bridge in OpenClaw before allowing thread-origin Herdr dispatch. Never accept parent channel IDs inferred from LLM prompts, user text or thread titles. If unavailable, keep worker commands in parent project channels and thread replies read-only.
- Validate OpenClaw `historyLimit`, channel/role allowlists, owner DM native exec approvals, and inbound exclusion for outbound-only alert channels. Avoid extra bots and bot-message loops.

- Configure OpenClaw's Discord bot strictly per current official documentation. Ask operator for missing Discord bot token, guild ID, user ID and channel IDs; do not invent them or print tokens. Store secrets only in supported env/secret references.
- Require a Discord user/guild allowlist and project-channel mapping, initially `requireMention=true`, `tools.exec.mode=ask` or stricter.
- Merge the OpenClaw project router instructions into the correct agent workspace, avoiding overwrite of unrelated rules. OpenClaw must route trusted channel metadata, not user-controlled project names.
- Demonstrate in a REAL Discord channel: read-only status, memory recall, safe Herdr dispatch with explicit approval, result notification. Ensure project B channel cannot control project A worker.
- Gate P3: actual inbound and outbound Discord operations, allowlist rejection, safe permissions.

## P4 — Task coordination
- In each project, communicate one concise status digest per meaningful task transition (`blocked`, `review-ready`, `failed`, `approval-needed`, `done`) instead of broadcasting raw terminal transcripts.
- Keep channel and thread transcripts distinct from Herdr sessions and graph memory. Never duplicate one worker as both persistent ACP and Herdr-controlled.

- Implement small DAG/checkpoint workflow only if necessary, using existing task table. No additional Redis, Neo4j, task queue or hosted database by default.
- Hermes should decompose a task, hand implementation to Codex, hand diff review to Claude, and generate compact summary. Persist evidence IDs and status.
- Add an optional bounded event notifier from Herdr socket API if live status notifications are required; include rate limiting, idempotency and no raw log streaming. Do not make the system dependent on a polling daemon by default.
- Never auto-merge, push, deploy, change OpenClaw auth, delete Git resources, or run GPU workloads without explicit owner permission.
- Gate P4: one end-to-end project task with traceable Git/test/review evidence.

## P5 — Performance/security benchmark
- Build a small evaluation set (at least 20 questions spanning project decisions, global conventions, conflicting outdated facts, and cross-project denial).
- Compare (A) no memory, (B) naive concatenated memory, (C) scoped FTS5, (D) scoped FTS5 + relationship retrieval. Use equal tasks/model/settings and track input+output+memory-extraction tokens, latency, recall correctness, task completion, false recall and leakage.
- Target aspirational >=40% context-token reduction vs B without lower task success. Treat results as measured, not assumed. Do not introduce Graphiti until it provides meaningful improvement over C/D after cost accounting.
- Gate P5: reproducible evidence and honest limitations, not just a graph drawing.

## Autonomous execution boundaries
You may freely: read files/docs, inspect config without printing secrets, edit code in your own worktree, write tests, create local documentation, execute safe CPU-only local tests, produce patches and proposed config. Before doing any of the following, stop and request owner approval: downloading/installing external executables, activating remote services, changing Discord/OpenClaw auth, overwriting existing configs, creating new agents that incur model costs, cloning private repositories, pushing/merging, running any GPU or expensive benchmark, deleting worktrees/branches or accessing other projects outside their authorization boundary. If a live test needs credentials, provide a narrow checklist, skip that gate and continue all unblocked offline steps. Never claim blocked work complete.

## Required output
Keep `docs/IMPLEMENTATION_STATUS.md` continuously updated with a table containing phase, status PASS/BLOCKED/FAIL, evidence, limitations, next action. Keep `docs/DECISIONS.md` and a short `docs/OPERATIONS.md`. At the end provide: (1) exact current machine/path/environment state, (2) successful commands and outputs with secrets scrubbed, (3) what was tested vs simulated, (4) remaining approvals/credential prerequisites, (5) how to start/stop safely, (6) Git commit IDs and unmerged branches, (7) token benchmark results only if actually measured. Prioritize working P0–P3 over speculative extensions. Communicate progress via Discord once live routing is verified; before that, use the console.

## Official sources to re-check at execution time
- https://herdr.dev/tui/docs/agent-automation/
- https://herdr.dev/tui/docs/cli-reference/
- https://herdr.dev/tui/docs/integrations/
- https://docs.openclaw.ai/channels/discord/setup
- https://docs.openclaw.ai/tools/skills
- https://docs.openclaw.ai/tools/exec-approvals
- https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers

START NOW: inspect repository, run P0 tests, report verified preflight, then advance through every unblocked phase without asking unnecessary questions. Respect permission gates above.
