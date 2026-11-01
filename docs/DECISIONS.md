# Decisions

- Herdr-first, no parallel tmux/ACP control for the same worker.
- OpenClaw is Discord gateway; Hermes is optional supervisor and user-facing conversations stay in OpenClaw.
- Python stdlib and SQLite FTS5 for P0; typed graph edges in the same database; no automatic memory extraction.
- Git worktrees provide conflict avoidance, not a security sandbox.
- Shared SKILL.md files are canonical and reviewed before change.
- All external installation, auth changes and destructive actions are owner-gated.
- Project boundary is derived from trusted Discord channel metadata, not a prompt string.
