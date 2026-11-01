---
name: git-worktree
description: Safely organize parallel agent implementation and review with isolated Git worktrees.
---
# Git worktrees

Each coding agent gets a distinct worktree and branch. The main repository branch remains untouched by agent implementation. `herdctl launch` creates branch `herdr/<agent-name>` with a worktree in the configured worktree root. If the branch or folder already exists, stop and inspect; never delete or overwrite it. Use commits and a reviewable diff for handoff. Merges, force push, branch deletion and worktree removal require explicit owner approval.
