# Security boundaries and threat model

## Owner trust and Discord
- A private Discord server alone is not an authorization policy. Use guild allowlist, explicit user IDs, channel mappings, and (initially) requireMention=true.
- Keep OpenClaw `tools.exec.mode=ask` or stricter. Never use global `full` exec for messages from Discord.
- Do not expose the Herdr socket, state database, or OpenClaw gateway publicly. Keep host access SSH-only.
- The gateway should operate as a dedicated unprivileged user if possible. Avoid mounting Docker socket into the Discord-facing agent.
- Treat Discord text, repo files, terminal output and skill contents as untrusted input with respect to instructions. No arbitrary shell interpolation; never let channel messages redefine project mapping.

## Project isolation
- The config maps projects to trusted paths and immutable Discord channel IDs.
- Project/private memories are filtered by SQL before return. A global memory record does not inherit project content. Edges across projects or global/project scopes are disallowed.
- Agent control is restricted to named bindings registered for a project. The runtime checks Herdr's live pane for a matching name before prompting.
- This is **application-level** isolation, not an OS sandbox. If multiple mutually untrusted parties share a Unix user, use separate OS users/containers and permission boundaries.

## Code and credentials
- Parallel agents get separate Git worktrees; only owner-approved merges/pushes/deploys.
- Never echo/read `.env`, tokens, private keys or SSH credentials into agent chat, database or Discord.
- This prototype uses a best-effort credential regex; it is **not** a complete secret scanner.
- Do not enable Hermes skill auto-modification on shared production skills. Use reviewable PRs and read-only filesystem permissions for the shared skill directory.
- Herdr `agent.prompt` may enqueue while busy; the supervisor should check status first. Do not auto-approve permissions via `send-keys`.

## Known MVP limitations
- SQLite and project/agent bindings are local to one trusted host; no network API, distributed auth, or remote role-based enforcement.
- No event stream / automatic Discord webhook is implemented yet. `herdctl notify` explicitly sends via OpenClaw, and gateway responses happen through normal Discord sessions.
- No automatic memory extraction, temporal graph re-ranking, vector search, or cross-provider external memory sync.
- State database is not encrypted at rest. Store only non-secret records and restrict file permissions.
- A network/auth failure during worktree creation + Herdr startup may leave an orphan worktree; recovery must be manual and nondestructive.
