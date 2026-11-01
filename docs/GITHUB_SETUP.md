# GitHub deployment checklist: anatwork14/herdr-hermes-openclaw

**Current scope:** source-controlled offline-tested starter. This repository does **not** automatically provision a Discord guild, install model-provider accounts, start a Herdr server, or establish persistent Linux services. Live operation requires actions on your trusted host.

## 1. Install and validate the source

```bash
git clone https://github.com/anatwork14/herdr-hermes-openclaw.git
cd herdr-hermes-openclaw
bash scripts/bootstrap.sh
bash scripts/selftest.sh
.venv/bin/herdctl discord-plan
.venv/bin/herdctl budget --role supervisor
```

Do not run the Python CLI as root. `selftest.sh` may show missing Herdr/OpenClaw/project paths in its `doctor` output; that is a *not deployed* signal, not a unit-test success.

## 2. Configure project registry

- Set `config/projects.json` (gitignored) to absolute repository directories accessible by the Herdr user. The examples `/srv/repos/...` are *not* discovery results.
- Each project must have its own verified Discord text-channel snowflake. Do not reuse channel IDs.
- Configure `.state` and `.worktrees` on a persistent local disk. Back up SQLite while honoring WAL journal mode.
- `config/projects.json`, `config/discord_layout.json`, and `config/token_policy.json` are private local configuration and must not be committed.

## 3. One OpenClaw bot + Discord topology

Create one Discord bot application and a *private* server with:

```text
CONTROL
  #agent-control   owner commands
  #approvals       owner-only notification/approval coordination
  #alerts          outbound failures + blocked items
PROJECTS
  #starlight-tempo
  #federated-learning-iot
SYSTEM
  #system-status   optional, outbound only
```

Enable only required bot permissions. Never grant Administrator. Set stable guild, owner-user and channel IDs in the local `config/discord_layout.json` and project registry. Keep the Discord bot token in OpenClaw's secret environment, never in Git, SQLite, a skill, a CLI argument, or a Discord post.

**Important:** The reference `integrations/openclaw/openclaw.discord.example.json5` is a fragment, *not* a ready-to-apply production config. Back up the live OpenClaw config, validate config keys against the installed OpenClaw version, and keep execution approvals enforced. The thread-to-project resolver in `herdctl` trusts the `parent_channel_id` supplied by a gateway adapter; the gateway MUST fetch and independently verify true Discord thread ancestry. Do not accept `parent_channel_id` from a user message.

## 4. Validate Herdr-first runtime

On the host install Herdr and agent CLIs from verified upstream sources. Verify compatibility with the installed Herdr version:

```bash
herdr --version
herdr status server
herdr workspace list
herdr agent list
```

Check live Herdr server availability before `herdctl launch`. Authenticate Codex, Claude, Hermes and optionally Antigravity through their official tools. Do not try to run browser-only Antigravity as if it were the supported `agy` CLI until proven compatible on this host.

```bash
.venv/bin/herdctl launch --project starlight-tempo --kind codex --name st_impl --yes
.venv/bin/herdctl read --project starlight-tempo --agent st_impl --lines 30
.venv/bin/herdctl prompt --project starlight-tempo --agent st_impl --text 'Summarize repo state without writing files' --wait --yes
```

The `--yes` guard is a CLI confirmation flag, **not an authorization mechanism**. Expose commands only behind an authenticated, allowlisted gateway and owner approval for costly/destructive actions. Do not allow remote users to pass arbitrary project names or shell commands.

## 5. Shared skills, memory and handoffs

```bash
.venv/bin/python scripts/install_skills.py         # dry-run
.venv/bin/python scripts/install_skills.py --apply
.venv/bin/herdctl memory add --scope project --project starlight-tempo --type decision --subject 'MTP baseline' --content 'Use native MTP as baseline, subject to experiment qualification' --evidence 'Owner-confirmed research decision'
.venv/bin/herdctl memory context --project starlight-tempo 'MTP baseline'
```

Review all symlink targets before applying. Never add private per-project knowledge as `global` scope without owner approval. Record `source` / `evidence` for factual claims.

## 6. Required acceptance gates

1. Offline: all tests pass, source files compile, and no secrets appear in Git.
2. Herdr: launch/read/prompt/detach/reconnect tested against real Codex and Claude in distinct worktrees; blocked input handled safely.
3. Discord: one bot, owner-only inbound, correct channel mapping and trusted thread metadata, outbound alert verified.
4. Memory: evidence-backed global/project isolation; cross-project negative tests; token-budget retrieval checked.
5. Workflow: Hermes plans a task, Codex implements on branch, Claude independently reviews, artifacts and compact summary returned to the matching Discord thread.
6. Resilience: restart/reconnect, server downtime, agent crash, and retry-idempotency documented and tested.

Keep each gate as BLOCKED until verified live. See `docs/IMPLEMENTATION_STATUS.md` and `MASTER_AGENT_PROMPT.md` for the executing agent's detailed work order.
