"""Regression cases for audited Herdr and graph-memory trust boundaries.

Mocks validate our wrapper logic only; they are NOT live Herdr acceptance tests.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdctl import memory as mem, runtime as rt


class TestAgentBindings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cx = mem.connection(Path(self.tmp.name) / "state.db")
        self.cx.execute(
            "INSERT INTO bindings VALUES (?,?,?,?,?,?,?)",
            ("worker", "project_a", "codex", "w1:p1", "w1", "/tmp/agent", 0),
        )
        self.cx.commit()
        self.cfg = {"herdr_binary": "herdr"}

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def prompt(self, **kwargs):
        return rt.prompt_agent(self.cfg, self.cx, project="project_a",
                               agent="worker", text="inspect", yes=True, **kwargs)

    def read(self):
        return rt.read_agent(self.cfg, self.cx, project="project_a", agent="worker")

    @patch("herdctl.runtime.herdr")
    def test_wrong_pane_in_unrelated_metadata_is_rejected(self, fake):
        fake.return_value = {"result": {"agent": {"pane_id": "w8:p8", "name": "worker"},
                                        "historical": "w1:p1"}}
        with self.assertRaises(rt.RuntimeError_):
            self.prompt()
        self.assertEqual(fake.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_missing_pane_field_is_rejected(self, fake):
        fake.return_value = {"result": {"agent": {"name": "worker"}, "note": "w1:p1"}}
        with self.assertRaises(rt.RuntimeError_):
            self.prompt()
        self.assertEqual(fake.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_same_pane_wrong_name_is_rejected(self, fake):
        fake.return_value = {"result": {"agent": {"pane_id": "w1:p1", "name": "other"}}}
        with self.assertRaises(rt.RuntimeError_):
            self.prompt()
        self.assertEqual(fake.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_wrong_workspace_is_rejected(self, fake):
        fake.return_value = {"result": {"agent": {"pane_id": "w1:p1",
                                                   "name": "worker", "workspace_id": "w7"}}}
        with self.assertRaises(rt.RuntimeError_):
            self.read()
        self.assertEqual(fake.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_blocked_agent_is_not_prompted(self, fake):
        fake.return_value = {"result": {"agent": {"pane_id": "w1:p1", "status": "blocked"}}}
        with self.assertRaises(rt.RuntimeError_):
            self.prompt()
        self.assertEqual(fake.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_working_agent_is_not_prompted(self, fake):
        fake.return_value = {"result": {"agent": {"pane_id": "w1:p1", "status": "working"}}}
        with self.assertRaises(rt.RuntimeError_):
            self.prompt()
        self.assertEqual(fake.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_timeout_never_auto_retries(self, fake):
        fake.side_effect = [
            {"result": {"agent": {"pane_id": "w1:p1", "status": "idle"}}},
            rt.RuntimeError_("timeout"),
        ]
        with self.assertRaisesRegex(rt.RuntimeError_, "delivery uncertain"):
            self.prompt()
        self.assertEqual(fake.call_count, 2)

    @patch("herdctl.runtime.herdr")
    def test_post_submit_mismatch_is_rejected(self, fake):
        fake.side_effect = [
            {"result": {"agent": {"pane_id": "w1:p1", "status": "idle"}}},
            {"ok": True},
            {"result": {"agent": {"pane_id": "w2:p2"}}},
        ]
        with self.assertRaisesRegex(rt.RuntimeError_, "already have been delivered"):
            self.prompt()
        self.assertEqual(fake.call_count, 3)

    @patch("herdctl.runtime.herdr")
    def test_read_rejects_stale_alias_before_output(self, fake):
        fake.return_value = {"result": {"agent": {"pane_id": "w8:p8"}}}
        with self.assertRaises(rt.RuntimeError_):
            self.read()
        self.assertEqual(fake.call_count, 1)

    @patch("herdctl.runtime.herdr")
    def test_read_discards_output_when_identity_changes(self, fake):
        fake.side_effect = [
            {"result": {"agent": {"pane_id": "w1:p1", "name": "worker"}}},
            "sensitive terminal output",
            {"result": {"agent": {"pane_id": "w9:p9", "name": "worker"}}},
        ]
        with self.assertRaises(rt.RuntimeError_):
            self.read()
        self.assertEqual(fake.call_args_list[1].args[1][:3], ["agent", "read", "w1:p1"])

    @patch("herdctl.runtime.herdr")
    def test_valid_read_targets_pane_and_checks_twice(self, fake):
        live = {"result": {"agent": {"pane_id": "w1:p1", "name": "worker"}}}
        fake.side_effect = [live, "safe output", live]
        self.assertEqual(self.read()["output"], "safe output")
        self.assertEqual(fake.call_args_list[1].args[1][:3], ["agent", "read", "w1:p1"])

    @patch("herdctl.runtime.herdr")
    def test_wait_accepts_idle_done_and_blocked(self, fake):
        live = {"result": {"agent": {"pane_id": "w1:p1", "status": "idle"}}}
        fake.side_effect = [live, {"ok": True}, live]
        self.prompt(wait=True)
        argv = fake.call_args_list[1].args[1]
        self.assertEqual(argv[:3], ["agent", "prompt", "w1:p1"])
        self.assertEqual([argv[i+1] for i,x in enumerate(argv) if x == "--until"],
                         ["idle", "done", "blocked"])

    def test_cross_project_binding_rejected_before_herdr(self):
        with patch("herdctl.runtime.herdr") as fake:
            with self.assertRaises(rt.RuntimeError_):
                rt.read_agent(self.cfg, self.cx, project="project_b", agent="worker")
            fake.assert_not_called()


class TestMemoryQueries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cx = mem.connection(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def add(self, subject, project=None, content="evidence"):
        return mem.add(self.cx, scope="project" if project else "global",
                       project=project, type="decision", subject=subject,
                       content=content, evidence="git:deadbeef")

    def test_global_only_filter_before_limit(self):
        self.add("alpha", content="global alpha")
        for i in range(20):
            self.add(f"alpha-{i}", project="project_a", content="alpha alpha alpha alpha")
        records = mem.search(self.cx, "alpha", project="project_a", global_only=True, limit=1)
        self.assertEqual([r["scope"] for r in records], ["global"])

    def test_retracted_graph_root_denied(self):
        node = self.add("old", project="project_a")
        self.cx.execute("UPDATE memories SET status='retracted' WHERE id=?", (node,))
        self.cx.commit()
        with self.assertRaises(mem.MemoryError):
            mem.graph(self.cx, node, project="project_a")

    def test_superseded_graph_root_denied(self):
        node = self.add("old", project="project_a")
        self.cx.execute("UPDATE memories SET status='superseded' WHERE id=?", (node,))
        self.cx.commit()
        with self.assertRaises(mem.MemoryError):
            mem.graph(self.cx, node, project="project_a")

    def test_retired_neighbors_are_not_returned(self):
        root = self.add("current", project="project_a")
        old = self.add("old", project="project_a")
        retracted = self.add("retracted", project="project_a")
        mem.link(self.cx, root, "SUPERSEDES", old, "project_a")
        mem.link(self.cx, root, "RELATED_TO", retracted, "project_a")
        self.cx.execute("UPDATE memories SET status='superseded' WHERE id=?", (old,))
        self.cx.execute("UPDATE memories SET status='retracted' WHERE id=?", (retracted,))
        self.cx.commit()
        graph = mem.graph(self.cx, root, project="project_a")
        self.assertEqual([n["id"] for n in graph["nodes"]], [root])
        self.assertEqual(graph["edges"], [])

    def test_cross_project_memory_still_inaccessible(self):
        self.add("private", project="project_b", content="private key rationale")
        self.assertEqual(mem.search(self.cx, "rationale", project="project_a"), [])


if __name__ == "__main__":
    unittest.main()
