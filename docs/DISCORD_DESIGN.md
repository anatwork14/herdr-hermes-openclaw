# Discord operating system — Herdr-first

## Chosen interface
One private Discord guild; **one OpenClaw bot account**; **one channel per project**; **threads for individual tasks**; Hermes/Codex/Claude/Antigravity remain Herdr sessions, not Discord bots. OpenClaw is the only Discord inbound gateway. Avoid OpenClaw ACP persistent worker bindings for these same Herdr-managed sessions or agents will have two competing session owners.

### Server topology (start with six channels)

| Category | Channel | Human use | Agent behavior | Access |
|---|---|---|---|---|
| 00 CONTROL | `#agent-control` | Global `status`, `projects`, `help`, `budget`, `health` | Small read-only response or request owner confirmation | owner-only commands |
| 00 CONTROL | `#approvals` | Central owner review / decision summaries | Post **redacted** approval reminders only | owner-only/private |
| 00 CONTROL | `#alerts` | See `blocked`, `failed`, stuck, security warnings | Outbound notifications only, no direct dispatch | owner-only/private |
| 01 PROJECTS | `#starlight-tempo` | Plan, ask, start, review STARLIGHT work | Resolve project from trusted channel ID; delegate via Hermes/Herdr | allowlisted owner initially |
| 01 PROJECTS | `#federated-learning-iot` | Plan, ask, start, review FL/IoT work | Same project-scoped authorization boundary | allowlisted owner initially |
| 02 SYSTEM | `#system-status` (optional) | Read periodic service/usage summary | Outbound aggregates only | owner-only/private |

**Do not** create `#codex`, `#claude`, `#hermes`, or per-pane channels. Those agents can work inside project worktrees and Herdr panes. Add new projects by adding a channel and its trusted numeric ID to `config/projects.json`, not by copying a bot/token.

### Project thread pattern
`ST-042 | native-MTP benchmark qualification` or `FL-017 | dataset gate review`. A task thread is NOT an extra runtime. Herdr owns agent sessions; OpenClaw owns the Discord conversation. Prefer one thread per bounded task/experiment with an explicit terminal status. On completion, Hermes posts a digest and Git evidence; archive the thread rather than retaining the full transcript in the agent's prompt.

**Thread routing security:** Discord threads have distinct IDs. `herdctl project-for-channel <THREAD_ID> --parent-channel-id <PARENT_PROJECT_CHANNEL_ID>` is available, but the parent ID must be obtained by a **trusted metadata adapter** using Discord-sourced metadata. The CLI cannot prove parentage alone. Never trust the user's text `parent_channel_id=...`; if gateway cannot provide verified ancestry, refuse thread dispatch and ask the owner to use the main project channel. OpenClaw thread session inheritance defaults off: do not enable transcript cloning for Herdr workers.

### Suggested message contract

```text
[ST-042] REVIEW READY
Project: starlight-tempo
Owner: Hermes | Implementer: Codex | Reviewer: Claude
Worktree/commit: herdr/st_impl @ <sha>
Result: CPU tests passed; review pending
Memory: 2 decisions updated; evidence linked
Action: Review diff; no deploy/merge performed
```

Send only relevant status transitions: `started` if requested; `review-ready`, `blocked`, `failed`, `approval-needed`, `done`. Avoid verbose intermediate tool calls. Use a deduplication/rate-limit policy once an event listener is implemented (e.g. batch low-severity events per project); **this is a design policy, not implemented behavior** in the starter.

### Suggested task commands (not built-in Discord slash commands yet)
Messages to the bot can express: `status`, `agents`, `start task`, `review`, `memory recall`, `budget`, `abort request`. The gateway must map each to the existing `herdctl` CLI or a validated adapter. **Do not advertise `/task` or `/agent` as registered slash commands until their handlers are implemented and verified.**

### Discord account and app credentials

- Create ONE Discord application named `ResearchOps`, copy its single **bot token**, store only as `DISCORD_BOT_TOKEN` in the OpenClaw service environment/secure store. It is not a model token.
- Record one guild/server ID, owner User ID, and each channel ID; numeric IDs are identifiers, not passwords.
- One set of model login credentials per provider/CLI: OpenAI for Codex, Anthropic for Claude, a configured provider for Hermes and supported Antigravity auth if enabled. Credentials can be account/profile based; do not assume a separate paid API key is mandatory for each agent or that subscriptions permit all automated uses.
- **No** Discord token for Herdr, Hermes-as-worker, Codex, Claude, Antigravity, project channels, or individual threads.
- If Hermes Discord gateway is enabled later, it needs a second *distinct* Discord application/token, strict bot-to-bot loop prevention, separate allowlists and a compelling reason. Not recommended at MVP.
- Never commit `.env`, tokens or OAuth refresh credentials. Rotate the Discord token after exposure; add a dedicated least-privileged Unix account for the gateway if feasible.

### Permissions and secure defaults

1. Private guild; operator allowlist by **user ID**, not username. `groupPolicy: allowlist`; limit enabled inbound channels to control + project channels.
2. Start `requireMention: true` in every enabled guild channel. For single-owner convenience, allow `false` only in project channels *after* real usage and cost validation.
3. Limit Discord bot permissions to View Channels, Send Messages, Read Message History, Send Messages in Threads, Embed Links. Enable reactions only if useful; never grant Administrator.
4. OpenClaw `tools.exec.mode: ask` or stricter; avoid `full`. Enable native exec approvals routed to **owner DM**; private `#approvals` is an audit/reminder surface, not an automatic approval handler.
5. Do not enable bot-to-bot message processing. Do not broadcast Discord message streams to Hermes and OpenClaw simultaneously.
6. Keep `channels.discord.historyLimit` small (suggested six) to avoid repeatedly charging for unrelated channel history; measure task quality. Thread inheritance from parent should remain disabled.
7. Never permit a Discord message to choose arbitrary `cwd`, Herdr pane ID, repository, memory scope or shell command. Strict project-channel ID registry and explicit owner-gated `herdctl` operations are mandatory.
8. `#alerts`/`#approvals`/`#system-status` can receive outbound posts where explicitly allowed. Do not list them as **inbound** channels just because the bot can send there; verify this behavior live.
9. When `#approvals` or `#alerts` contains sensitive data, set private Discord channel permissions and send only redacted metadata.

### Cost and memory policy

`config/token_policy.example.json` defines initial/followup **retrieved-memory** targets by role; run `herdctl budget --role reviewer` or `--followup`. Budgets do NOT cap a model's total tokens or provider spend. `herdctl memory context ... --budget N` uses approximate counts; model provider and OpenClaw telemetry must be collected for actual cost.

Prefer deterministic routing, then compact supervisor planning, then implementation and review. Never auto-forward full Codex transcript into Claude or vice versa: handoff through task ID, Git commit/diff, test artifact and <= ~1,600 tokens of relevant memories.

### Provisioning order

1. Create private guild and category/channel layout manually.
2. Create one bot in the Discord Developer Portal; grant only required intents/permissions.
3. Set environment secret on the host (never paste it into a bot message). Collect guild/user/channel IDs.
4. Fill `config/projects.json` and `config/discord_layout.json` (copied from examples by bootstrap, gitignored). Run `herdctl discord-plan` to see missing fields. **This tool does not create Discord channels.**
5. Merge and dry-run the OpenClaw JSON5 fragment against installed-version config, with backup. Configure Hermes as a Herdr worker, **not** a second Discord gateway.
6. Test `#agent-control` read-only requests, project mapping, and owner-only denial, then safe Herdr dispatch with owner confirmation.
7. Test task-thread parent metadata routing and native exec-approval DM. If the metadata adapter is not finished, restrict agent commands to parent project channels and use threads for summaries only.
8. Test outbound alerts and negative cross-project cases; only then ease mention gating if desired.

### Official references
- https://docs.openclaw.ai/channels/discord/setup
- https://docs.openclaw.ai/channels/discord/access-control
- https://docs.openclaw.ai/channels/discord/threads-and-sessions
- https://docs.openclaw.ai/channels/discord/rich-messages
- https://docs.openclaw.ai/tools/exec-approvals
- https://docs.openclaw.ai/concepts/usage-tracking
- https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord
