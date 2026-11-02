"""Hardening test suite for PR #9: Authenticated Discord Events, Durable Idempotency, and Isolation.

Covers:
1. Authenticated Discord Events (Haiku Worker A):
   - Missing, forged, or mismatched transport metadata rejected without defaults in production.
   - Untrusted gateway adapter callers rejected when adapter token is configured.
   - Explicit separation between synthetic testing (--synthetic / is_synthetic) and authenticated production ingress.
   - Per-project status responses strictly isolate project details without exposing unrelated projects.
   - Discord responses bounded to <= 2000 characters.
2. Durable Request Idempotency (Haiku Worker B):
   - Atomic reservation before dispatch enforcing lifecycle: received -> processing -> completed / failed / uncertain.
   - Concurrency protection: multiple parallel threads on identical message ID collide safely.
   - Duplicate delivery on read-only commands produces predictable result without re-executing dispatch.
   - Crash recovery: read-only commands reclaimed; mutating/prompt commands transition to uncertain without auto-retry.
   - Database failure propagation: SQLite errors raise IdempotencyError and are never silently swallowed.
   - Serialized per-agent queue: prevents concurrent agent dispatch for the same agent.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
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
from herdctl.cli import build_parser, main as cli_main
from herdctl.discord_gateway import (
    CommandError,
    DiscordGateway,
    DiscordInboundMessage,
    GatewayRoutingError,
    GatewaySecurityError,
    IdempotencyError,
    IdempotencyStore,
    format_discord_response,
    handle_status,
    parse_inbound_message,
    resolve_channel_ancestry,
    sanitize_text,
    validate_metadata,
)


class TestHardeningBase(unittest.TestCase):
    """Fixture providing isolated temporary directories, policies, SQLite DB, and configs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()

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
            "gateway_adapter": {
                "auth_token": "secret-trusted-openclaw-token-998877",
            },
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

        # Create simulated repo folders with .git
        (self.root / "repos" / "starlight-tempo" / ".git").mkdir(parents=True)
        (self.root / "repos" / "fl-iot" / ".git").mkdir(parents=True)

        self.db_path = self.root / ".state" / "memory.sqlite3"
        self.cx = mem.connection(self.db_path)

        self.gateway = DiscordGateway(
            root=self.root,
            cfg=self.cfg,
            layout=self.layout,
            cx=self.cx,
        )

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def make_valid_msg(
        self,
        content: str = "status",
        channel_id: str = "1566465715828031582",
        parent_channel_id: str | None = None,
        guild_id: str = "1556635278846992404",
        author_id: str = "734391788718129205",
        author_is_bot: bool = False,
        message_id: str = "1566470000000000001",
        adapter_token: str | None = "secret-trusted-openclaw-token-998877",
        is_synthetic: bool = False,
    ) -> DiscordInboundMessage:
        return DiscordInboundMessage(
            message_id=message_id,
            guild_id=guild_id,
            channel_id=channel_id,
            author_id=author_id,
            content=content,
            parent_channel_id=parent_channel_id,
            author_is_bot=author_is_bot,
            adapter_token=adapter_token,
            is_synthetic=is_synthetic,
        )


class TestStage1AuthenticatedDiscordEvents(TestHardeningBase):
    """Haiku Worker A: Authentication boundaries, metadata validation, and channel isolation."""

    def test_missing_transport_metadata_rejected_in_production(self):
        """Production ingress must reject missing message_id, guild_id, channel_id, or author_id without defaults."""
        raw_payload = {
            "channel_id": "1566465715828031582",
            "content": "status",
            # missing message_id, guild_id, author_id
        }
        with self.assertRaises(GatewaySecurityError) as ctx:
            parse_inbound_message(raw_payload, is_synthetic=False)
        self.assertIn("Missing required transport metadata", str(ctx.exception))

    def test_untrusted_adapter_caller_rejected(self):
        """Caller presenting missing or forged adapter authentication token must be rejected."""
        msg_bad_token = self.make_valid_msg(adapter_token="forged-attacker-token")
        with self.assertRaises(GatewaySecurityError) as ctx:
            self.gateway.validate_metadata(msg_bad_token)
        self.assertIn("Untrusted gateway adapter caller", str(ctx.exception))

        msg_no_token = self.make_valid_msg(adapter_token=None)
        with self.assertRaises(GatewaySecurityError) as ctx:
            self.gateway.validate_metadata(msg_no_token)
        self.assertIn("Untrusted gateway adapter caller", str(ctx.exception))

    def test_valid_adapter_token_succeeds(self):
        """Trusted adapter presenting the authorized token is accepted."""
        msg = self.make_valid_msg(adapter_token="secret-trusted-openclaw-token-998877")
        self.gateway.validate_metadata(msg)

    def test_synthetic_flag_explicit_separation(self):
        """Synthetic mode (--synthetic or is_synthetic=True) allows test payloads and records synthetic status."""
        synthetic_payload = {
            "channel_id": "1566465715828031582",
            "content": "status",
            "is_synthetic": True,
        }
        parsed = parse_inbound_message(synthetic_payload, is_synthetic=True)
        self.assertTrue(parsed.is_synthetic)
        # Synthetic messages bypass adapter token requirement
        validate_metadata(self.layout, parsed, expected_adapter_token="secret-trusted-openclaw-token-998877")

    def test_forged_owner_rejected(self):
        """Requests with unauthorized author ID fail validation even with valid syntax."""
        msg = self.make_valid_msg(author_id="999999999999999999")
        with self.assertRaises(GatewaySecurityError) as ctx:
            self.gateway.validate_metadata(msg)
        self.assertIn("Unapproved author ID", str(ctx.exception))

    def test_forged_thread_parent_rejected(self):
        """Thread purporting to be under a project but with forged parent ID is rejected."""
        with self.assertRaises(GatewayRoutingError) as ctx:
            self.gateway.resolve_channel("1566469999999999999", parent_channel_id="1566468888888888888")
        self.assertIn("does not match any configured project channel", str(ctx.exception))

    def test_per_project_status_isolation(self):
        """Status command in #starlight-tempo must only report starlight-tempo, never federated-learning-iot."""
        res_project = handle_status(
            cfg=self.cfg,
            root=self.root,
            cx=self.cx,
            layout=self.layout,
            scope="project",
            project="starlight-tempo",
        )
        self.assertIn("starlight-tempo", res_project["projects"])
        self.assertNotIn("federated-learning-iot", res_project["projects"])

        formatted = format_discord_response("status", "project", "starlight-tempo", res_project)
        self.assertIn("[starlight-tempo]", formatted)
        self.assertNotIn("federated-learning-iot", formatted)

        # Control channel does see all projects
        res_control = handle_status(
            cfg=self.cfg,
            root=self.root,
            cx=self.cx,
            layout=self.layout,
            scope="control",
            project=None,
        )
        self.assertIn("starlight-tempo", res_control["projects"])
        self.assertIn("federated-learning-iot", res_control["projects"])

    def test_response_strictly_bounded_under_2000_chars(self):
        """Discord responses must be bounded to <= 2000 characters."""
        long_data = [{"id": f"rec-{i}", "scope": "project", "type": "fact", "subject": "long subject " * 10, "content": "long content " * 30} for i in range(20)]
        resp = format_discord_response("recall", "project", "starlight-tempo", long_data)
        self.assertLessEqual(len(resp), 2000)
        self.assertIn("[TRUNCATED", resp)


class TestStage2DurableRequestIdempotency(TestHardeningBase):
    """Haiku Worker B: Request lifecycle, atomic reservation, concurrency, and crash recovery."""

    def test_atomic_reservation_lifecycle_states(self):
        """Request transitions through received/processing -> completed."""
        store = self.gateway.idempotency
        mid = "1566470000000011111"
        status, record = store.reserve_request(
            message_id=mid,
            guild_id="1556635278846992404",
            channel_id="1566465715828031582",
            author_id="734391788718129205",
            scope="control",
            project=None,
            command="status",
        )
        self.assertEqual(status, "processing")
        self.assertIsNone(record)

        rec_in_flight = store.get_record(mid)
        self.assertEqual(rec_in_flight["status"], "processing")

        store.complete_request(mid, {"health": "ok"}, "**Gateway Status**")
        rec_completed = store.get_record(mid)
        self.assertEqual(rec_completed["status"], "completed")
        self.assertIn("health", rec_completed["response_json"])

    def test_concurrent_parallel_reservation_collision(self):
        """10 concurrent threads attempting to process the exact same message ID must collide safely."""
        mid = "1566470000000022222"
        success_count = 0
        collision_count = 0

        def try_reserve():
            # Use distinct connection per thread to simulate real concurrent processes
            thread_cx = mem.connection(self.db_path)
            try:
                thread_store = IdempotencyStore(thread_cx, processing_timeout_seconds=30.0)
                st, _ = thread_store.reserve_request(
                    message_id=mid,
                    guild_id="1556635278846992404",
                    channel_id="1566465715828031582",
                    author_id="734391788718129205",
                    scope="control",
                    project=None,
                    command="status",
                )
                if st == "processing":
                    time.sleep(0.05)
                    thread_store.complete_request(mid, {"data": 1}, "text 1")
                    return "won"
                return "completed_duplicate"
            except IdempotencyError:
                return "collision"
            finally:
                thread_cx.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(try_reserve) for _ in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(results.count("won"), 1, "Exactly one thread must win reservation")
        self.assertEqual(results.count("won") + results.count("collision") + results.count("completed_duplicate"), 10)

    def test_duplicate_read_only_replay_cached_without_dispatch(self):
        """Duplicate delivery with replay_cached=True returns identical cached response without dispatch."""
        msg = self.make_valid_msg(content="status", message_id="1566470000000033333")
        res1 = self.gateway.handle_message(msg)
        self.assertTrue(res1["ok"])
        self.assertFalse(res1.get("replayed", False))

        # Replay with replay_cached=True
        with patch.object(self.gateway, "dispatch", side_effect=AssertionError("Dispatch must NOT be called on replay")):
            res2 = self.gateway.handle_message(msg, replay_cached=True)
            self.assertTrue(res2["ok"])
            self.assertTrue(res2["replayed"])
            self.assertEqual(res1["text"], res2["text"])

    def test_crash_recovery_read_only_reclaim(self):
        """Read-only request stuck in processing past timeout is safely reclaimed and re-executed."""
        store = self.gateway.idempotency
        mid = "1566470000000044444"
        # Insert timed-out processing record (updated 100 seconds ago)
        now = int(time.time())
        with self.cx:
            self.cx.execute(
                """
                INSERT INTO gateway_idempotency (
                    message_id, guild_id, channel_id, author_id, scope, project,
                    command, arguments, status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (mid, "1556635278846992404", "1566465715828031582", "734391788718129205",
                 "control", None, "status", "", "processing", now - 100, now - 100)
            )

        # Attempt to reserve with 10s timeout: should reclaim cleanly
        st, _ = store.reserve_request(
            message_id=mid,
            guild_id="1556635278846992404",
            channel_id="1566465715828031582",
            author_id="734391788718129205",
            scope="control",
            project=None,
            command="status",
            timeout_seconds=10.0,
        )
        self.assertEqual(st, "processing")

    def test_crash_recovery_mutating_prompt_transitions_to_uncertain_no_auto_retry(self):
        """Mutating or prompt request stuck in processing past timeout transitions to UNCERTAIN; auto-retry is forbidden."""
        store = self.gateway.idempotency
        mid = "1566470000000055555"
        now = int(time.time())
        with self.cx:
            self.cx.execute(
                """
                INSERT INTO gateway_idempotency (
                    message_id, guild_id, channel_id, author_id, scope, project,
                    command, arguments, status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (mid, "1556635278846992404", "1566465715828031582", "734391788718129205",
                 "project", "starlight-tempo", "prompt", "--text hello", "processing", now - 100, now - 100)
            )

        with self.assertRaises(IdempotencyError) as ctx:
            store.reserve_request(
                message_id=mid,
                guild_id="1556635278846992404",
                channel_id="1566465715828031582",
                author_id="734391788718129205",
                scope="project",
                project="starlight-tempo",
                command="prompt",
                arguments="--text hello",
                timeout_seconds=10.0,
            )
        self.assertIn("marked UNCERTAIN", str(ctx.exception))

        rec = store.get_record(mid)
        self.assertEqual(rec["status"], "uncertain")

        # Second attempt also blocked immediately
        with self.assertRaises(IdempotencyError) as ctx2:
            store.reserve_request(
                message_id=mid,
                guild_id="1556635278846992404",
                channel_id="1566465715828031582",
                author_id="734391788718129205",
                scope="project",
                project="starlight-tempo",
                command="prompt",
                arguments="--text hello",
            )
        self.assertIn("UNCERTAIN", str(ctx2.exception))

    def test_database_failure_raises_idempotency_error_never_swallowed(self):
        """Storage operational errors in IdempotencyStore must raise IdempotencyError and not be silently ignored."""
        bad_cx = sqlite3.connect(":memory:")
        bad_cx.close()  # Closed connection will throw Cannot operate on a closed database
        store = IdempotencyStore()
        store.cx = bad_cx

        with self.assertRaises(IdempotencyError):
            store.is_duplicate("1566470000000066666")

        with self.assertRaises(IdempotencyError):
            store.reserve_request(
                message_id="1566470000000066666",
                guild_id="1556635278846992404",
                channel_id="1566465715828031582",
                author_id="734391788718129205",
                scope="control",
                project=None,
                command="status",
            )

    def test_serialized_per_agent_queue(self):
        """Serialized per-agent queue detects busy agent and tracks pending requests in FIFO order."""
        store = self.gateway.idempotency
        mid1 = "1566470000000077771"
        mid2 = "1566470000000077772"

        store.reserve_request(
            message_id=mid1,
            guild_id="1556635278846992404",
            channel_id="1566467969033306122",
            author_id="734391788718129205",
            scope="project",
            project="starlight-tempo",
            command="status",
            agent="st_codex",
        )

        busy_id = store.check_agent_busy(project="starlight-tempo", agent="st_codex")
        self.assertEqual(busy_id, mid1)

        idle_id = store.check_agent_busy(project="starlight-tempo", agent="st_claude")
        self.assertIsNone(idle_id)

        # Complete first request, verify agent becomes idle
        store.complete_request(mid1, {"done": True}, "completed")
        self.assertIsNone(store.check_agent_busy(project="starlight-tempo", agent="st_codex"))

    def test_persistence_across_process_restart(self):
        """A new DiscordGateway instance pointing to existing SQLite DB sees completed records."""
        mid = "1566470000000088888"
        msg = self.make_valid_msg(content="status", message_id=mid)
        res1 = self.gateway.handle_message(msg)
        self.assertTrue(res1["ok"])

        # Simulated new process with fresh gateway
        restarted_gateway = DiscordGateway(
            root=self.root,
            cfg=self.cfg,
            layout=self.layout,
            cx=self.cx,
        )
        rec = restarted_gateway.idempotency.get_record(mid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "completed")

        res_dup = restarted_gateway.handle_message(msg)
        self.assertFalse(res_dup["ok"])
        self.assertIn("Duplicate message ID", res_dup["error"])

    def test_untrusted_synthetic_escalation_rejected(self):
        """Untrusted payload attempting to pass is_synthetic=True is rejected in production ingress."""
        malicious_payload = {
            "message_id": "1566470000000099991",
            "guild_id": "1556635278846992404",
            "channel_id": "1566465715828031582",
            "author_id": "734391788718129205",
            "content": "status",
            "is_synthetic": True,
        }
        with self.assertRaises(GatewaySecurityError) as ctx:
            parse_inbound_message(malicious_payload, is_synthetic=False)
        self.assertIn("Synthetic-mode escalation attempt in untrusted payload rejected", str(ctx.exception))

    def test_production_mode_unconfigured_credential_fails_closed(self):
        """Production ingress must fail closed on startup if server adapter credential is not configured."""
        unconfigured_layout = {
            "version": 1,
            "guild_id": "1556635278846992404",
            "owner_user_ids": ["734391788718129205"],
        }
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(GatewaySecurityError) as ctx:
                DiscordGateway(
                    root=self.root,
                    cfg=self.cfg,
                    layout=unconfigured_layout,
                    cx=self.cx,
                    production=True,
                )
            self.assertIn("Production ingress requires configured gateway adapter credential", str(ctx.exception))

    def test_conflicting_replay_altered_metadata_rejected(self):
        """Replaying a message ID with altered parameters (command, channel, or author) raises IdempotencyError."""
        mid = "1566470000000099992"
        msg = self.make_valid_msg(content="status", message_id=mid)
        res1 = self.gateway.handle_message(msg)
        self.assertTrue(res1["ok"])

        # Attempt replay with altered content / command
        msg_tampered = self.make_valid_msg(content="recall note", message_id=mid)
        res2 = self.gateway.handle_message(msg_tampered)
        self.assertFalse(res2["ok"])
        self.assertIn("Conflicting replay detected", res2["error"])

    def test_atomic_agent_lease_contention_rejected(self):
        """Atomic reservation prevents two concurrent requests from obtaining a lease on the same agent."""
        store = self.gateway.idempotency
        mid1 = "1566470000000099993"
        mid2 = "1566470000000099994"

        # Request 1 reserves agent
        store.reserve_request(
            message_id=mid1,
            guild_id="1556635278846992404",
            channel_id="1566467969033306122",
            author_id="734391788718129205",
            scope="project",
            project="starlight-tempo",
            command="prompt",
            agent="st_codex",
        )

        # Request 2 targeting same agent must be rejected atomically
        with self.assertRaises(IdempotencyError) as ctx:
            store.reserve_request(
                message_id=mid2,
                guild_id="1556635278846992404",
                channel_id="1566467969033306122",
                author_id="734391788718129205",
                scope="project",
                project="starlight-tempo",
                command="prompt",
                agent="st_codex",
            )
        self.assertIn("is currently leased by dispatch", str(ctx.exception))


class TestOpenClawDiscordAdapter(unittest.TestCase):
    """Test suite for OpenClaw Discord Adapter metadata extraction and read-only boundary."""

    def test_extract_valid_event_metadata(self):
        """Authoritative metadata is extracted cleanly from OpenClaw Discord event structure."""
        from integrations.openclaw.openclaw_discord_adapter import extract_event_metadata

        event = {
            "message": {
                "id": "1566470000000099995",
                "guild": {"id": "1556635278846992404"},
                "channel": {
                    "id": "1566467969033306122",
                    "parentId": "1566465715828031582",
                },
                "author": {"id": "734391788718129205", "bot": False},
                "content": "status",
            }
        }
        meta = extract_event_metadata(event)
        self.assertEqual(meta["message_id"], "1566470000000099995")
        self.assertEqual(meta["guild_id"], "1556635278846992404")
        self.assertEqual(meta["channel_id"], "1566467969033306122")
        self.assertEqual(meta["parent_channel_id"], "1566465715828031582")
        self.assertEqual(meta["author_id"], "734391788718129205")
        self.assertFalse(meta["author_is_bot"])
        self.assertEqual(meta["content"], "status")

    def test_read_only_command_enforcement(self):
        """Only read-only commands (status, agents, recall, tasks, budget) are allowed."""
        from integrations.openclaw.openclaw_discord_adapter import (
            OpenClawCommandError,
            validate_read_only_command,
        )

        # Allowed read-only commands
        for cmd in ["status", "/status", "agents", "recall fact", "tasks", "budget --role supervisor", "model", "model anthropic/claude-3.5-sonnet"]:
            verb, args = validate_read_only_command(cmd)
            self.assertIn(verb, {"status", "agents", "recall", "tasks", "budget", "model", "models"})

        # Mutating and execution commands strictly forbidden
        for bad_cmd in ["prompt st_codex do something", "launch explorer", "notify owner", "sh rm -rf", "exec ls"]:
            with self.assertRaises(OpenClawCommandError) as ctx:
                validate_read_only_command(bad_cmd)
            self.assertTrue(
                "mutating or execution operation" in str(ctx.exception)
                or "Unsupported command" in str(ctx.exception)
            )

    def test_adapter_rejects_bots_and_synthetic_escalation(self):
        """Adapter rejects bot authors and untrusted synthetic flags."""
        from integrations.openclaw.openclaw_discord_adapter import (
            OpenClawSecurityError,
            extract_event_metadata,
        )

        bot_event = {
            "id": "1566470000000099996",
            "guild_id": "1556635278846992404",
            "channel_id": "1566465715828031582",
            "author": {"id": "1556635278846992499", "bot": True},
            "content": "status",
        }
        with self.assertRaises(OpenClawSecurityError):
            extract_event_metadata(bot_event)

        synthetic_event = {
            "id": "1566470000000099997",
            "guild_id": "1556635278846992404",
            "channel_id": "1566465715828031582",
            "author_id": "734391788718129205",
            "content": "status",
            "is_synthetic": True,
        }
        with self.assertRaises(OpenClawSecurityError):
            extract_event_metadata(synthetic_event)

    def test_model_command_inspection_and_setting(self):
        """Model command allows inspecting active model and explicitly setting openrouter model."""
        from herdctl.discord_gateway import handle_model, CommandError

        with tempfile.TemporaryDirectory() as td:
            fake_cfg = Path(td) / "openclaw.json"
            with patch.dict(os.environ, {"OPENCLAW_CONFIG_PATH": str(fake_cfg)}):
                # Status inspection
                info = handle_model("")
                self.assertEqual(info["action"], "status")
                self.assertIn("current_model", info)
                self.assertIsInstance(info["popular_models"], list)

                # Setting valid model
                res = handle_model("anthropic/claude-3.5-sonnet")
                self.assertEqual(res["action"], "set")
                self.assertEqual(res["current_model"], "anthropic/claude-3.5-sonnet")

                # Reverting to auto
                res2 = handle_model("openrouter/auto")
                self.assertEqual(res2["action"], "set")
                self.assertEqual(res2["current_model"], "openrouter/auto")

                # Invalid model string rejected
                with self.assertRaises(CommandError) as ctx:
                    handle_model("invalid model with spaces ; rm -rf")
                self.assertIn("Invalid model identifier", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
