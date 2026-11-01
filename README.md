# Herdr Agent Workspace

**Herdr-first Discord multi-agent starter** for Codex, Claude Code, Hermes and Antigravity CLI, with scoped graph memory and one shared SKILL.md registry.

## What is implemented
- Installable Python CLI (`herdctl`) with **no third-party runtime Python dependencies**.
- Project registry and trusted Discord channel-ID mapping, plus read-only `discord-plan` including safe task-thread parent routing contract.
- Herdr workspace + pane + agent start wrapper, with agent bindings and Git worktree isolation.
- Read/prompt worker commands restricted to the configured project, and explicit `--yes` for mutations.
- SQLite persistent global/project memory; FTS5 search, typed graph edges, evidence pointers, memory retire, bounded context packages and per-project task checkpoints.
- OpenClaw Discord routing/permission template, Discord channel topology, credential inventory, token-budget policy and outbound message adapter.
- Shared skills for Herdr control, memory recall, handoff, scientific review and worktrees.
- Master implementer prompt, deployment/security/recovery runbooks and 24 passing offline tests (authoring environment; run `scripts/selftest.sh` to re-check).

## Not yet implemented/verified
- No live Herdr server or coding CLIs are connected to this package in the authoring environment.
- No Discord credentials, gateway, or server deployment has been configured.
- No native event listener, automated agent scheduling, vector search, knowledge extraction or token-cost benchmark. Hermes supervision is instructions/configuration, not an independently implemented workflow engine.
- This is a **starter/MVP**, not a completed production deployment. Live integration gates are explicit in `docs/IMPLEMENTATION_STATUS.md`.

## Source repository

Canonical project: https://github.com/anatwork14/herdr-hermes-openclaw

```bash
git clone https://github.com/anatwork14/herdr-hermes-openclaw.git
cd herdr-hermes-openclaw
```

See `docs/GITHUB_SETUP.md` for the host-specific live deployment and acceptance checklist.

## Quick start

```bash
bash scripts/bootstrap.sh
# Edit config/projects.json with actual local project repository paths and Discord IDs.
.venv/bin/herdctl doctor
.venv/bin/herdctl discord-plan
.venv/bin/herdctl budget --role supervisor
.venv/bin/python -m unittest discover -s tests -v
```

After Herdr server is installed, authenticated and started:

```bash
.venv/bin/herdctl launch --project starlight-tempo --kind codex --name st_impl --yes
.venv/bin/herdctl read --project starlight-tempo --agent st_impl --lines 50
.venv/bin/herdctl memory context --project starlight-tempo "benchmark baseline"
```

## Directory map

```text
src/herdctl/             Python config, memory, CLI, runtime adapters
tests/                  Offline unit tests
config/                 Project, Discord-layout and role-budget policies (private configs gitignored)
skills/shared/           Canonical reusable SKILL.md files
integrations/openclaw/  Discord routing instructions and JSON5 config fragment
integrations/hermes/    Supervisor and shared skill config examples
docs/                   Architecture, deployment, security, recovery, operations
MASTER_AGENT_PROMPT.md  Paste into Codex/Claude lead implementer on server
```

### Recommended operating model
One private Discord server, one channel per project, one Herdr workspace per project, one Git worktree per concurrent coding agent. Keep Hermes as coordinator and OpenClaw as the only Discord gateway. Use evidence-backed task handoffs rather than dumping terminal transcripts into other agents' prompts.

### Safety
Never commit secrets. A memory DB is not an access-control boundary against hostile OS users. Keep OpenClaw host exec guarded and validate permissions before allowing Discord to dispatch live agents. See `docs/SECURITY.md`.

**First read:** `docs/GITHUB_SETUP.md`, `docs/ISSUES.md`, `docs/DEPLOYMENT.md`, then `MASTER_AGENT_PROMPT.md`.

**Discord and token operations:** `docs/DISCORD_DESIGN.md` and `docs/TOKEN_POLICY.md`. Channel creation, task-thread metadata resolution, provider token accounting and approval workflow must still be validated or implemented on the host; these documents do not claim live integration.
