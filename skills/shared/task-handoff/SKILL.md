---
name: task-handoff
description: Handoff implementation and review tasks across Herdr agents using Git artifacts instead of transcript dumps.
---
# Cross-agent task handoff

A task has one current owner and a project-specific ID. For concurrent code edits, each worker uses its own Git worktree. Handoff contains: goal, source project, active branch, commit/diff, test command, test verdict, known limitations, next requested action, related memory IDs.

Record checkpoint:
`herdctl task set --project <project> --id <task_id> --title "..." --status review --agent <reviewer> --evidence "git:<sha>"`

Reviewer must read actual diff and source. If blocked, set `blocked` and send a concise question to Discord. Never mark `done` merely from an agent's terminal state. Avoid repeatedly polling large terminal output; request succinct checkpoints.
