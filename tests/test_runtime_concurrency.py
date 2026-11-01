"""Unit and regression tests for Herdr runtime contract, concurrency, and error propagation.

Covers:
1. agent_status field handling ('idle', 'working', 'blocked', 'done', None, fallback to status/state)
2. Pane rebind and alias collision detection
3. Prompt timeout delivery ambiguity handling (fail-closed, never auto-retry)
4. Pre-read and post-read identity drift detection (atomic validation, output suppression)
5. Malformed/unstructured herdr responses (fail-closed)
6. Subprocess timeout and error code propagation
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from herdctl import config as cfg_module
from herdctl import memory as mem
from herdctl import runtime as rt


class RuntimeTestBase(unittest.TestCase):
    """Base fixture providing initialized SQLite bindings and mock config."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_runtime.db"
        self.cx = mem.connection(self.db_path)
        self.project = "starlight-tempo"
        self.agent_name = "st_codex"
        self.pane_id = "w1:p1"
        self.workspace_id = "w1"
        self.kind = "codex"
        self.cwd = "/tmp/wt/starlight-tempo/st_codex"

        self.cx.execute(
            "INSERT INTO bindings VALUES (?,?,?,?,?,?,?)",
            (
                self.agent_name,
                self.project,
                self.kind,
                self.pane_id,
                self.workspace_id,
                self.cwd,
                0,
            ),
        )
        self.cx.commit()

        self.cfg = {
            "herdr_binary": "herdr",
            "openclaw_binary": "openclaw",
            "worktree_root": "/tmp/wt",
            "projects": {
                self.project: {
                    "repo": "/tmp/repo",
                    "discord_channel_id": "123456789012345678",
                },
                "other-project": {
                    "repo": "/tmp/repo2",
                    "discord_channel_id": "987654321098765432",
                },
            },
        }

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def live_090_response(
        self,
        *,
        status: str | None = "idle",
        pane_id: str | None = None,
        name: str | None = None,
        workspace_id: str | None = None,
        include_agent_status: bool = True,
        legacy_status: str | None = None,
        legacy_state: str | None = None,
    ) -> dict:
        """Construct realistic Herdr 0.9.0 agent get response."""
        agent_dict: dict = {
            "agent": self.kind,
            "name": name if name is not None else self.agent_name,
            "pane_id": pane_id if pane_id is not None else self.pane_id,
            "workspace_id": workspace_id if workspace_id is not None else self.workspace_id,
        }
        if include_agent_status:
            agent_dict["agent_status"] = status
        if legacy_status is not None:
            agent_dict["status"] = legacy_status
        if legacy_state is not None:
            agent_dict["state"] = legacy_state

        return {
            "id": "cli:agent:get",
            "result": {
                "agent": agent_dict,
                "type": "agent_info",
            },
        }


class TestAgentStatusHandling(RuntimeTestBase):
    """1. agent_status field handling ('idle', 'working', 'blocked', 'done', None)."""

    @patch("herdctl.runtime.herdr")
    def test_agent_status_idle_allows_prompt(self, fake_herdr):
        """Herdr 0.9.0 agent_status='idle' allows new prompt submission."""
        live = self.live_090_response(status="idle")
        fake_herdr.side_effect = [live, {"ok": True}, live]

        res = rt.prompt_agent(
            self.cfg, self.cx, project=self.project, agent=self.agent_name, text="run task", yes=True
        )
        self.assertEqual(res, {"ok": True})
        self.assertEqual(fake_herdr.call_count, 3)
        self.assertEqual(
            fake_herdr.call_args_list[1].args[1],
            ["agent", "prompt", self.pane_id, "run task"],
        )

    @patch("herdctl.runtime.herdr")
    def test_agent_status_done_allows_prompt(self, fake_herdr):
        """Herdr 0.9.0 agent_status='done' allows new prompt submission."""
        live = self.live_090_response(status="done")
        fake_herdr.side_effect = [live, {"ok": True}, live]

        res = rt.prompt_agent(
            self.cfg, self.cx, project=self.project, agent=self.agent_name, text="run task", yes=True
        )
        self.assertEqual(res, {"ok": True})
        self.assertEqual(fake_herdr.call_count, 3)

    @patch("herdctl.runtime.herdr")
    def test_agent_status_working_blocks_prompt(self, fake_herdr):
        """Herdr 0.9.0 agent_status='working' must reject prompt without enqueuing."""
        live = self.live_090_response(status="working")
        fake_herdr.return_value = live

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="run task", yes=True
            )
        self.assertIn("Agent is not ready for a new prompt (state='working')", str(ctx.exception))
        # Exactly 1 call (agent get verification), never calls agent prompt
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_agent_status_blocked_blocks_prompt(self, fake_herdr):
        """Herdr 0.9.0 agent_status='blocked' must reject prompt without enqueuing."""
        live = self.live_090_response(status="blocked")
        fake_herdr.return_value = live

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="run task", yes=True
            )
        self.assertIn("Agent is not ready for a new prompt (state='blocked')", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_agent_status_none_omitted_allows_prompt(self, fake_herdr):
        """When status is omitted entirely, fail-open for backwards compatibility."""
        live = self.live_090_response(include_agent_status=False)
        fake_herdr.side_effect = [live, {"ok": True}, live]

        res = rt.prompt_agent(
            self.cfg, self.cx, project=self.project, agent=self.agent_name, text="run task", yes=True
        )
        self.assertEqual(res, {"ok": True})
        self.assertEqual(fake_herdr.call_count, 3)

    @patch("herdctl.runtime.herdr")
    def test_agent_status_explicit_null_allows_prompt(self, fake_herdr):
        """When agent_status is explicitly None and no fallback is present, allows prompt."""
        live = self.live_090_response(status=None, include_agent_status=True)
        fake_herdr.side_effect = [live, {"ok": True}, live]

        res = rt.prompt_agent(
            self.cfg, self.cx, project=self.project, agent=self.agent_name, text="run task", yes=True
        )
        self.assertEqual(res, {"ok": True})
        self.assertEqual(fake_herdr.call_count, 3)

    @patch("herdctl.runtime.herdr")
    def test_agent_status_explicit_null_falls_back_to_legacy_status(self, fake_herdr):
        """When agent_status is None, falls back to 'status' and blocks if working."""
        live = self.live_090_response(status=None, include_agent_status=True, legacy_status="working")
        fake_herdr.return_value = live

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="run task", yes=True
            )
        self.assertIn("Agent is not ready for a new prompt (state='working')", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_agent_status_omitted_falls_back_to_legacy_state(self, fake_herdr):
        """When agent_status is omitted, falls back to 'state' and blocks if blocked."""
        live = self.live_090_response(include_agent_status=False, legacy_state="blocked")
        fake_herdr.return_value = live

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="run task", yes=True
            )
        self.assertIn("Agent is not ready for a new prompt (state='blocked')", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_agent_status_unknown_state_blocks_prompt(self, fake_herdr):
        """Any unexpected/unknown agent_status (e.g. 'paused', 'crashed') must block prompt."""
        live = self.live_090_response(status="paused")
        fake_herdr.return_value = live

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="run task", yes=True
            )
        self.assertIn("Agent is not ready for a new prompt (state='paused')", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)


class TestPaneRebindAndAliasCollision(RuntimeTestBase):
    """2. Pane rebind and alias collision detection."""

    @patch("herdctl.runtime.herdr")
    def test_pane_rebind_mismatch_rejected_pre_prompt(self, fake_herdr):
        """If live pane differs from registered pane (e.g. agent moved), prompt fails closed."""
        live = self.live_090_response(pane_id="w2:p9")
        fake_herdr.return_value = live

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="hi", yes=True
            )
        self.assertIn("Live Herdr pane differs from registered pane; manual rebind required", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_pane_rebind_mismatch_rejected_pre_read(self, fake_herdr):
        """If live pane differs from registered pane, read fails closed before reading."""
        live = self.live_090_response(pane_id="w2:p9")
        fake_herdr.return_value = live

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.read_agent(self.cfg, self.cx, project=self.project, agent=self.agent_name)
        self.assertIn("Live Herdr pane differs from registered pane; manual rebind required", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_alias_collision_different_agent_name_rejected(self, fake_herdr):
        """If live agent name differs from bound name (alias reassigned), fails closed."""
        live = self.live_090_response(name="imposter_agent")
        fake_herdr.return_value = live

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="hi", yes=True
            )
        self.assertIn("Live Herdr agent name differs from registered name", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_workspace_mismatch_rejected(self, fake_herdr):
        """If live agent reports different workspace, fails closed."""
        live = self.live_090_response(workspace_id="w99")
        fake_herdr.return_value = live

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="hi", yes=True
            )
        self.assertIn("Live Herdr workspace differs from registered workspace", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_cross_project_agent_control_rejected(self, fake_herdr):
        """Refuse control of an agent under another project without consulting herdr."""
        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project="other-project", agent=self.agent_name, text="hi", yes=True
            )
        self.assertIn("refusing cross-project control", str(ctx.exception))
        fake_herdr.assert_not_called()

    @patch("herdctl.runtime.herdr")
    def test_post_prompt_pane_rebind_detected(self, fake_herdr):
        """Detect pane drift immediately post-submit and refuse retry."""
        pre_live = self.live_090_response(pane_id=self.pane_id)
        post_live = self.live_090_response(pane_id="w9:p9")
        fake_herdr.side_effect = [pre_live, {"ok": True}, post_live]

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="task", yes=True
            )
        self.assertIn("prompt may already have been delivered — do not blindly retry", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 3)


class TestPromptDeliveryAmbiguity(RuntimeTestBase):
    """3. Prompt timeout delivery ambiguity handling."""

    @patch("herdctl.runtime.herdr")
    def test_prompt_command_timeout_never_retries(self, fake_herdr):
        """When prompt command times out, fail closed and mark delivery uncertain."""
        pre_live = self.live_090_response()
        fake_herdr.side_effect = [
            pre_live,
            rt.RuntimeError_("Command failed to run (herdr): Command '['herdr', 'agent', 'prompt']' timed out after 35 seconds"),
        ]

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="task", yes=True
            )
        self.assertIn("prompt delivery uncertain — inspect the agent before any retry", str(ctx.exception))
        self.assertIsNotNone(ctx.exception.__cause__)
        # Pre-check + single prompt call, NO auto-retry
        self.assertEqual(fake_herdr.call_count, 2)

    @patch("herdctl.runtime.herdr")
    def test_prompt_wait_mode_timeout_and_flags(self, fake_herdr):
        """Wait mode passes correct flags and handles timeout ambiguity."""
        pre_live = self.live_090_response()
        fake_herdr.side_effect = [
            pre_live,
            rt.RuntimeError_("Command failed to run (herdr): timeout after 130s"),
        ]

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="wait task", yes=True, wait=True
            )
        self.assertIn("prompt delivery uncertain — inspect the agent before any retry", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 2)

        prompt_call = fake_herdr.call_args_list[1]
        self.assertEqual(prompt_call.kwargs.get("timeout"), 130)
        argv = prompt_call.args[1]
        self.assertIn("--wait", argv)
        self.assertIn("--timeout", argv)
        self.assertEqual(
            [argv[i + 1] for i, x in enumerate(argv) if x == "--until"],
            ["idle", "done", "blocked"],
        )

    @patch("herdctl.runtime.herdr")
    def test_prompt_stalled_or_rejected_error_is_delivery_uncertain(self, fake_herdr):
        """Subprocess exit failure or stalled state flags delivery uncertainty."""
        pre_live = self.live_090_response()
        fake_herdr.side_effect = [
            pre_live,
            rt.RuntimeError_("Herdr rejected request: agent_prompt_stalled"),
        ]

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="task", yes=True
            )
        self.assertIn("prompt delivery uncertain — inspect the agent before any retry", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 2)


class TestReadIdentityDriftDetection(RuntimeTestBase):
    """4. Pre-read and post-read identity drift detection."""

    @patch("herdctl.runtime.herdr")
    def test_pre_read_pane_drift_aborts_without_reading(self, fake_herdr):
        """If pane changed before read, abort without issuing 'agent read'."""
        fake_herdr.return_value = self.live_090_response(pane_id="w8:p8")

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.read_agent(self.cfg, self.cx, project=self.project, agent=self.agent_name)
        self.assertIn("Live Herdr pane differs from registered pane; manual rebind required", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)
        self.assertEqual(fake_herdr.call_args.args[1][:2], ["agent", "get"])

    @patch("herdctl.runtime.herdr")
    def test_pre_read_name_drift_aborts_without_reading(self, fake_herdr):
        """If agent name changed before read, abort without issuing 'agent read'."""
        fake_herdr.return_value = self.live_090_response(name="someone_else")

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.read_agent(self.cfg, self.cx, project=self.project, agent=self.agent_name)
        self.assertIn("Live Herdr agent name differs from registered name", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_pre_read_workspace_drift_aborts_without_reading(self, fake_herdr):
        """If workspace changed before read, abort without issuing 'agent read'."""
        fake_herdr.return_value = self.live_090_response(workspace_id="w99")

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.read_agent(self.cfg, self.cx, project=self.project, agent=self.agent_name)
        self.assertIn("Live Herdr workspace differs from registered workspace", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_post_read_pane_drift_discards_captured_output(self, fake_herdr):
        """If pane drifts during/after read, discard output and fail closed."""
        pre_live = self.live_090_response(pane_id=self.pane_id)
        post_live = self.live_090_response(pane_id="w3:p3")
        fake_herdr.side_effect = [
            pre_live,
            "TOP_SECRET_SESSION_KEY=abcdef123456",
            post_live,
        ]

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.read_agent(self.cfg, self.cx, project=self.project, agent=self.agent_name)
        self.assertIn("Live Herdr pane differs from registered pane; manual rebind required", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 3)

    @patch("herdctl.runtime.herdr")
    def test_post_read_name_drift_discards_captured_output(self, fake_herdr):
        """If agent name drifts during/after read, discard output and fail closed."""
        pre_live = self.live_090_response(name=self.agent_name)
        post_live = self.live_090_response(name="hijacked_agent")
        fake_herdr.side_effect = [
            pre_live,
            "sensitive data",
            post_live,
        ]

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.read_agent(self.cfg, self.cx, project=self.project, agent=self.agent_name)
        self.assertIn("Live Herdr agent name differs from registered name", str(ctx.exception))
        self.assertEqual(fake_herdr.call_count, 3)

    @patch("herdctl.runtime.herdr")
    def test_valid_read_targets_pane_id_and_returns_output(self, fake_herdr):
        """Valid read checks pre and post identity, targets pane_id, and returns content."""
        live = self.live_090_response()
        fake_herdr.side_effect = [live, "safe output", live]

        res = rt.read_agent(self.cfg, self.cx, project=self.project, agent=self.agent_name, lines=50)
        self.assertEqual(
            res,
            {
                "project": self.project,
                "agent": self.agent_name,
                "output": "safe output",
            },
        )
        self.assertEqual(fake_herdr.call_count, 3)
        self.assertEqual(
            fake_herdr.call_args_list[1].args[1],
            ["agent", "read", self.pane_id, "--source", "recent-unwrapped", "--lines", "50"],
        )


class TestMalformedHerdrResponses(RuntimeTestBase):
    """5. Malformed/unstructured herdr responses."""

    @patch("herdctl.runtime.herdr")
    def test_unstructured_string_response_rejected(self, fake_herdr):
        """Non-JSON string response from herdr raises unstructured error."""
        fake_herdr.return_value = "daemon offline or socket closed"

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.verified_live_agent(self.cfg, {"name": self.agent_name, "pane_id": self.pane_id})
        self.assertIn("Unstructured Herdr agent identity; refusing access", str(ctx.exception))

    @patch("herdctl.runtime.herdr")
    def test_unstructured_non_dict_response_rejected(self, fake_herdr):
        """JSON array or primitive response raises unstructured error."""
        for invalid in [["agent", "list"], 12345, True, None]:
            fake_herdr.return_value = invalid
            with self.subTest(invalid=invalid):
                with self.assertRaises(rt.RuntimeError_) as ctx:
                    rt.verified_live_agent(self.cfg, {"name": self.agent_name, "pane_id": self.pane_id})
                self.assertIn("Unstructured Herdr agent identity; refusing access", str(ctx.exception))

    @patch("herdctl.runtime.herdr")
    def test_missing_result_key_rejected(self, fake_herdr):
        """Response dictionary omitting 'result' key is rejected."""
        fake_herdr.return_value = {"id": "cli:agent:get"}

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.verified_live_agent(self.cfg, {"name": self.agent_name, "pane_id": self.pane_id})
        self.assertIn("Herdr agent get omitted result.agent; refusing access", str(ctx.exception))

    @patch("herdctl.runtime.herdr")
    def test_result_not_a_dict_rejected(self, fake_herdr):
        """Response where 'result' is not a dictionary is rejected."""
        for val in ["not-a-dict", 99, [1, 2], None]:
            fake_herdr.return_value = {"id": "cli:agent:get", "result": val}
            with self.subTest(val=val):
                with self.assertRaises(rt.RuntimeError_) as ctx:
                    rt.verified_live_agent(self.cfg, {"name": self.agent_name, "pane_id": self.pane_id})
                self.assertIn("Herdr agent get omitted result.agent; refusing access", str(ctx.exception))

    @patch("herdctl.runtime.herdr")
    def test_missing_agent_key_in_result_rejected(self, fake_herdr):
        """Response where result dict has no 'agent' key is rejected."""
        fake_herdr.return_value = {"id": "cli:agent:get", "result": {"type": "agent_info"}}

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.verified_live_agent(self.cfg, {"name": self.agent_name, "pane_id": self.pane_id})
        self.assertIn("Herdr agent get omitted result.agent; refusing access", str(ctx.exception))

    @patch("herdctl.runtime.herdr")
    def test_agent_not_a_dict_rejected(self, fake_herdr):
        """Response where result['agent'] is a string/int is rejected."""
        fake_herdr.return_value = {"id": "cli:agent:get", "result": {"agent": "st_codex"}}

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.verified_live_agent(self.cfg, {"name": self.agent_name, "pane_id": self.pane_id})
        self.assertIn("Herdr agent get omitted result.agent; refusing access", str(ctx.exception))

    @patch("herdctl.runtime.herdr")
    def test_agent_missing_pane_id_rejected(self, fake_herdr):
        """Response where agent dict has no pane_id is rejected."""
        fake_herdr.return_value = {
            "id": "cli:agent:get",
            "result": {"agent": {"name": self.agent_name, "workspace_id": self.workspace_id}},
        }

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.verified_live_agent(self.cfg, {"name": self.agent_name, "pane_id": self.pane_id})
        self.assertIn("Live Herdr pane differs from registered pane; manual rebind required", str(ctx.exception))

    @patch("herdctl.runtime.command")
    def test_herdr_cli_ok_false_raises_runtime_error(self, fake_command):
        """Herdr response with ok=false raises explicit error."""
        fake_command.return_value = json.dumps({"ok": False, "error": "agent not found"})

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.herdr(self.cfg, ["agent", "get", "unknown"])
        self.assertIn("Herdr rejected request: agent not found", str(ctx.exception))

    def test_nested_helper_error_cases(self):
        """nested() helper raises on missing keys, non-dict intermediaries, and invalid IDs."""
        # Missing intermediate key
        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.nested({"result": {}}, "result", "workspace", "workspace_id")
        self.assertIn("missing result.workspace.workspace_id", str(ctx.exception))

        # Non-dict intermediary
        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.nested({"result": "not_a_dict"}, "result", "workspace")
        self.assertIn("missing result.workspace", str(ctx.exception))

        # Empty string ID
        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.nested({"result": {"pane_id": ""}}, "result", "pane_id")
        self.assertIn("Unexpected Herdr ID: ''", str(ctx.exception))

        # Non-string ID (e.g. integer)
        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.nested({"result": {"pane_id": 123}}, "result", "pane_id")
        self.assertIn("Unexpected Herdr ID: 123", str(ctx.exception))

        # Valid nested string ID
        self.assertEqual(
            rt.nested({"result": {"pane_id": "w1:p1"}}, "result", "pane_id"),
            "w1:p1",
        )


class TestSubprocessTimeoutAndErrorPropagation(RuntimeTestBase):
    """6. Subprocess timeout and error code propagation."""

    @patch("subprocess.run")
    def test_command_timeout_expired_raises_runtime_error_with_cause(self, fake_run):
        """subprocess.TimeoutExpired is translated to RuntimeError_ with chained cause."""
        exc = subprocess.TimeoutExpired(cmd=["herdr", "status"], timeout=35)
        fake_run.side_effect = exc

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.command(["herdr", "status"], timeout=35)
        self.assertIn("Command failed to run (herdr)", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, exc)

    @patch("subprocess.run")
    def test_command_oserror_raises_runtime_error_with_cause(self, fake_run):
        """OSError (e.g. executable not found) is translated to RuntimeError_ with cause."""
        exc = FileNotFoundError(2, "No such file or directory: 'herdr'")
        fake_run.side_effect = exc

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.command(["herdr", "status"])
        self.assertIn("Command failed to run (herdr): [Errno 2] No such file or directory", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, exc)

    @patch("subprocess.run")
    def test_command_nonzero_exit_code_stderr(self, fake_run):
        """Non-zero exit code includes stderr in RuntimeError_."""
        fake_run.return_value = subprocess.CompletedProcess(
            args=["herdr", "agent", "prompt"],
            returncode=1,
            stdout="",
            stderr="herdr-server: socket connection refused",
        )

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.command(["herdr", "agent", "prompt"])
        self.assertEqual(str(ctx.exception), "herdr exited 1: herdr-server: socket connection refused")

    @patch("subprocess.run")
    def test_command_nonzero_exit_code_stdout_fallback(self, fake_run):
        """Non-zero exit code falls back to stdout if stderr is empty."""
        fake_run.return_value = subprocess.CompletedProcess(
            args=["herdr", "version"],
            returncode=2,
            stdout="unknown option --bad",
            stderr="",
        )

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.command(["herdr", "version"])
        self.assertEqual(str(ctx.exception), "herdr exited 2: unknown option --bad")

    @patch("subprocess.run")
    def test_command_stderr_truncation_to_1800_chars(self, fake_run):
        """Stderr longer than 1800 chars is cleanly truncated to the tail [-1800:]."""
        long_prefix = "HEADER_INFO_" * 100  # 1200 chars
        tail_content = "TAIL_ERROR_" * 163 + "12345"  # exactly 1800 chars
        full_stderr = long_prefix + tail_content

        fake_run.return_value = subprocess.CompletedProcess(
            args=["herdr", "dump"],
            returncode=1,
            stdout="",
            stderr=full_stderr,
        )

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.command(["herdr", "dump"])
        err_msg = str(ctx.exception)
        self.assertTrue(err_msg.startswith("herdr exited 1: "))
        self.assertNotIn("HEADER_INFO_", err_msg)
        self.assertIn(tail_content, err_msg)

    @patch("subprocess.run")
    def test_subprocess_timeout_propagates_through_prompt_agent(self, fake_run):
        """End-to-end: subprocess.TimeoutExpired during prompt_agent propagates as delivery uncertain."""
        # Pre-check agent get succeeds
        live_json = json.dumps(self.live_090_response(status="idle"))
        prompt_exc = subprocess.TimeoutExpired(cmd=["herdr", "agent", "prompt"], timeout=35)

        fake_run.side_effect = [
            subprocess.CompletedProcess(args=["herdr", "agent", "get"], returncode=0, stdout=live_json, stderr=""),
            prompt_exc,
        ]

        with self.assertRaises(rt.RuntimeError_) as ctx:
            rt.prompt_agent(
                self.cfg, self.cx, project=self.project, agent=self.agent_name, text="timed out prompt", yes=True
            )
        err = str(ctx.exception)
        self.assertIn("prompt delivery uncertain — inspect the agent before any retry", err)
        self.assertIn("Command failed to run (herdr)", err)


if __name__ == "__main__":
    unittest.main()
