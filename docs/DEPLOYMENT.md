# Deployment runbook (Linux user-space, no sudo required for Python project)

## 0. Prerequisites
Python >=3.10 with `venv`, Git, a persistent shell/SSH connection, and installed/authorized CLIs: Herdr, OpenClaw, Hermes, Codex, Claude Code and (optional) Antigravity CLI. Verify every vendor's installation commands from official websites before running them. This project does NOT silently install third-party programs or request login tokens.

Reference docs:
- https://herdr.dev/tui/docs/install/
- https://herdr.dev/tui/docs/agent-automation/
- https://herdr.dev/tui/docs/integrations/
- https://docs.openclaw.ai/channels/discord/setup
- https://docs.openclaw.ai/tools/skills
- https://hermes-agent.nousresearch.com/docs/user-guide/features/skills

## 1. Prepare code/config
```bash
git clone https://github.com/anatwork14/herdr-hermes-openclaw.git
cd herdr-hermes-openclaw
bash scripts/bootstrap.sh
# edit config/projects.json with real absolute local repository paths
.venv/bin/herdctl doctor
.venv/bin/python -m unittest discover -s tests -v
```
You may choose a different folder; references are relative to the unpacked project. `state_dir` is stored locally as `.state` and is gitignored.

`bootstrap.sh` also copies private `config/discord_layout.json` and `config/token_policy.json` on first use. Edit the former with guild/owner/control channel IDs and consult `docs/DISCORD_DESIGN.md`; keep provider credentials outside both JSON files. Use `.venv/bin/herdctl discord-plan` and `.venv/bin/herdctl budget --role reviewer` for a read-only review before connecting a bot.

If repositories are missing, clone them explicitly after verifying repo ownership and remote URLs. Suggested existing research project examples are STARLIGHT-TEMPO and Federated Learning in IoT; their actual target paths MUST be set by the operator.

## 2. Start Herdr and verify auth
Use `herdr --version`, `herdr api schema --json`, `herdr status server` to validate installed CLI. Start `herdr server` in a supervised terminal/session (it is a long-lived process). In a second SSH session, verify `herdr workspace list` works. For persistent headless operation, configure a user-level service after assessing host permissions/process lifetime; don't assume `systemd --user` or linger is available.

Verify `codex`, `claude`, `hermes`, `agy` (Antigravity CLI's binary/kind may vary) in `PATH` and their individual authentication **interactively**. Run Herdr integrations as appropriate after reviewing modifications:
```bash
herdr integration install codex
herdr integration install claude
herdr integration install hermes
herdr integration install antigravity-cli
```
Only install an integration if its agent is present; Antigravity requires current CLI compatibility verification.

## 3. Install shared skills
```bash
.venv/bin/python scripts/install_skills.py       # dry-run
.venv/bin/python scripts/install_skills.py --apply
```
The script symlinks into `~/.agents/skills`, `~/.claude/skills`, and `~/.gemini/config/skills` only if absent. For Hermes merge the `skills.external_dirs` snippet, for OpenClaw merge `skills.load.extraDirs` as shown in `integrations/`. Confirm skills appear in each tool. Treat shared skills as version-controlled and read-only to unsupervised agents.

## 4. Discord/OpenClaw
- Create a Discord bot in the Discord Developer Portal, enable Message Content Intent, and invite it only to a private server.
- Keep token out of Git. Place it in OpenClaw's supported secrets environment (`~/.openclaw/.env` or a secret manager).
- Get Discord User ID, Guild ID, control Channel IDs and project Channel IDs; configure *real* values in OpenClaw. The JSON5 template is a fragment, not a complete config replacement.
- Configure `groupPolicy=allowlist`, per-guild user allowlist and initially `requireMention=true`. Keep alerts/approvals outbound-only and native exec approvals owner-DM-targeted. Use `tools.exec.mode=ask` or stricter. Review `openclaw doctor` and gateway status after merging config.
- Configure `config/projects.json` with matching Discord channel IDs. Do not depend on names as security identifiers. Use `herdctl project-for-channel <ID>` with the **platform-provided** project channel ID; task threads must supply a separately verified `--parent-channel-id` from trusted Discord metadata. Thread dispatch remains blocked without that metadata adapter, and neither ID may come from user-authored text.
- Place `integrations/openclaw/PROJECT_AGENT.md` in OpenClaw's workspace instructions (merge with existing `AGENTS.md`) so channel messages route to the right `herdctl` project.
- Provide a permitted exec surface to the venv `herdctl` entrypoint under OpenClaw's approval policy, not blanket shell execution.
- Test bot pairing/allowlisting with your own Discord user before enabling any agent dispatch.

## 5. Start one agent and perform handoff
```bash
.venv/bin/herdctl launch --project starlight-tempo --kind codex --name st_impl --yes
.venv/bin/herdctl agents --project starlight-tempo
.venv/bin/herdctl read --project starlight-tempo --agent st_impl --lines 40
.venv/bin/herdctl prompt --project starlight-tempo --agent st_impl --text "Inspect repository; report only. Do not change files." --yes
.venv/bin/herdctl launch --project starlight-tempo --kind claude --name st_review --yes
```
All launches create isolated Git worktrees from `HEAD` in configured worktree root and branches `herdr/<agent>`. To avoid proliferation, create only two workers for the first smoke test; do not run GPU benchmarks.

## 6. Verify scoped memory
```bash
.venv/bin/herdctl memory add --scope project --project starlight-tempo \
  --type decision --subject "baseline" --content "MTP is the selected primary benchmark baseline" \
  --evidence "user-confirmed:2026-10-28"
.venv/bin/herdctl memory context --project starlight-tempo --budget 800 "benchmark baseline"
.venv/bin/herdctl task set --project starlight-tempo --id st-p0 --title "Agent handoff smoke test" \
  --status queued
```
Use an evidence pointer that can be inspected or a clearly labeled explicit user-confirmed decision. Before an agent relies on this memory, compare it to the current repo.

## 7. Send Discord status
```bash
.venv/bin/herdctl notify --project starlight-tempo --text "Herdr P0 smoke test completed" --yes
```
This requires a configured OpenClaw gateway and channel mapping. Outbound send success does NOT prove the inbound Discord-routing skill has been exercised.

## Deployment gates
G0: local tests green; G1: Herdr two-agent observe/send/read live; G2: Discord channel allowlist and inbound routing, outbound send; G3: correct global/project isolation; G4: successful handoff with Git evidence; G5: optional automated monitoring and recovery. Do not mark a gate as passed by tests of mocks alone.
