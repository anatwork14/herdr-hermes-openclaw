# Code-review remediation — 2026-11-01

This patch corrects the four reproduced defects from the latest-main review. It does **not** deploy or certify Herdr, Hermes or Discord.

## Patches and evidence

| Issue | Root cause | Corrected contract | Added tests |
|---|---|---|---|
| H1 — Wrong-pane prompt | JSON substring check was not an identity check | Require exact `result.agent.pane_id`, validate reported name and workspace where present; target registered pane | Wrong pane in unrelated text, missing ID, renamed agent, wrong workspace, post-send mismatch |
| H2 — Unverified terminal read | Trusted stale agent alias on read | Verify structured identity before and after the read; use the registered pane target; discard output if identity drifted | Wrong alias, race/reassignment, valid bounded read, cross-project negative |
| M1 — Retired-memory graph leak | Traversal permitted retracted roots and superseded neighbors | Ordinary graph traversal only returns `status='active'` nodes | Retracted root, superseded root, retired neighbors |
| M2 — Empty global-only search | Applied scope filter after FTS rank and LIMIT | Apply global-only SQL predicate before ranking and LIMIT | Twenty more-specific private hits cannot hide a global hit |

Other safe-default changes: reject prompts when a reported lifecycle state is `working`, `blocked`, or `unknown`; include `idle`, `done`, `blocked` when waiting; **never automatically retry ambiguous timeout or stalled prompts**. A Herdr `done`/idle state means *ready*, not verified completion.

## Offline verification

Authoring environment: `PYTHONPATH=src python -m unittest discover -s tests -v` and `python -m compileall -q src tests`. The authoring copy passed **41 tests** (24 baseline + 17 regression tests). The PR contains an additional dedicated suite with **18 regression cases**; rerun the **full branch suite** on the target machine and record its actual result before approving/merging. GitHub/Herdr runtime integration was **not** exercised.

## Release blockers and residual risk

1. **No atomic compare-and-submit:** pre/post identity checks narrow a race but do not replace an authenticated, serialized adapter. Even a pane ID is not a durable agent session ID. After a move/restart/replacement, require operator reconciliation. Use a per-agent dispatch queue and deduplication before exposing execution to Discord.
2. **Live schema unverified:** on target host use `herdr --version`, `herdr api schema --json` and `herdr agent get <known-live-name>` with secret redaction. Ensure the actual fields map to the structured validator, fail closed on mismatch. Do not return to substring checks.
3. **`--yes` is not authorization:** remote requests need a trusted gateway checking owner ID, guild ID, parent thread ancestry, project mapping, operations policy, cost/approval gates and rate limits. This project does not yet implement that adapter.
4. **Permissions and secrets:** project filtering in SQLite is not OS isolation. Restrict Unix users/state DB permissions and never pass credentials through Discord, memory or terminal logs.
5. **Prompt ambiguity:** Herdr's documentation explicitly states timeout or `agent_prompt_stalled` does not prove input was not sent. Inspect live output before any deliberate human retry.
6. **Unverified systems:** no real Codex→Claude handoff, Hermes scheduling, OpenClaw ingress, thread ancestry, restart recovery, or token-cost benchmark has been demonstrated on the target host.
7. **Independent Opus reviewer pending:** review these diffs and inspect negative test fixtures before allowing a production merge.

## Task allocation for the next stage

- **Sonnet 5.5 lead:** integrate this patch, arrange live Herdr CLI contract fixtures, design serialized/authorized Discord dispatch and real recovery.
- **Haiku 5.5 A:** tests for agent move/restart/rebind, mixed CLI JSON responses and permission prompts. Use isolated worktree; report actual test outputs.
- **Haiku 5.5 B:** memory scope/retraction property tests, stale facts, FTS ranking, and concurrency tests; do not modify private global memory.
- **Opus 5.5 independent reviewer:** assess all high-severity findings, adversarial alias swaps, residual TOCTOU, project isolation, API compatibility and test methodology. Produce PASS / PASS WITH CONDITIONS / FAIL / BLOCKED with cited code and evidence.

## References

- [Official Herdr agent automation](https://herdr.dev/tui/docs/agent-automation/)
- [Official Herdr CLI reference](https://herdr.dev/tui/docs/cli-reference/)
- [Official Herdr socket API](https://herdr.dev/tui/docs/socket-api/)
- [Original roadmap](ISSUES.md)

**Do not mark P1/P3/P4 PASS or use this as a public Discord execution bridge based only on offline tests.**
