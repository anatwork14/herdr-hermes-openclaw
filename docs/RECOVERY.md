# Operator recovery

1. Run `herdctl doctor`, `herdctl agents`, `herdr workspace list` and `herdr agent list`; compare registered pane IDs with reality.
2. If a launch failed after creating a worktree, inspect with `git -C /PATH/TO/REPO worktree list` and check `herdr/<agent>` branch. Do not delete worktree automatically; first save any changes, then explicitly review cleanup.
3. If a pane was moved or native session resumed, the binding may refer to a stale pane ID. Do not bypass ownership verification. A reviewed future `rebind` command can update the registry; meanwhile use direct trusted Herdr CLI under manual supervision.
4. If memory looks incorrect, search and inspect evidence, then `herdctl memory retire --id ... --status superseded --yes` in the appropriate scope. Add a corrected memory with a new evidence pointer.
5. Back up the SQLite database with SQLite's online backup API or `sqlite3 .backup` while services run; do not copy only the `.sqlite3` file without considering WAL.
6. Never automatically restart Herdr or OpenClaw in a way that interrupts active sessions without operator approval.
