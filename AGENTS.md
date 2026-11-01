# Agent development rules — Herdr Agent Workspace

Read `MASTER_AGENT_PROMPT.md`, `README.md`, `docs/DEPLOYMENT.md`, and `docs/SECURITY.md` before changing implementation. Herdr is primary. OpenClaw is only the Discord gateway; Hermes is supervisor, not another terminal owner.

## Contracts
1. No destructive changes, deployment, persistent service installation, installs from unreviewed scripts, network downloads, GPU experiments, or pushes without owner approval.
2. Do not invent Herdr/OpenClaw/Hermes CLI flags. Verify the installed version's `--help`, schema, and official docs.
3. Do not claim live integrations passed without actual smoke tests on target host.
4. Use project-scoped memory and permissions, never read project B from project A.
5. `SKILL.md` files are canonical and version controlled; no self-modifying shared production skills.
6. Each agent modifies its own Git worktree; reviewers read diffs and test outputs rather than sharing the writer's working tree.
7. Include tests for new behavior, use safe subprocess argument arrays, document unverified assumptions.

## Completion report
Provide verified checks, exact commands, changed files, real project paths, pending credentials/manual steps, and limitations. Keep live access credentials out of logs and commits.

## Discord and token requirements
Read `docs/DISCORD_DESIGN.md`, `docs/TOKEN_POLICY.md`. Never create bot accounts per agent. Use a single OpenClaw Discord bot and Herdr-managed terminal agents. Verify project-channel ID and trusted Discord thread parent before dispatch. `herdctl budget` values are soft context targets, not hard provider quotas. Do not claim the sample OpenClaw fragment, approvals or agent cost ledger are live without real tests.
