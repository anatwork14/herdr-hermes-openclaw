"""Comprehensive Phase P2 Memory Lifecycle, Isolation, and Shared Skills Test Suite.

Verifies:
a) Global vs project isolation and cross-project negative access tests.
b) Memory lifecycle (active, superseded, retracted) transitions and query exclusion.
c) Graph traversal deduplication, cycle termination, hop clamping [0..3], and retracted root denial.
d) Token budget clamping and compact context assembly.
e) Canonical shared skills validation (all 5 skills have valid SKILL.md frontmatter and instructions).
f) Provenance and evidence enforcement (empty or whitespace-only evidence rejected).
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from herdctl import memory as m
from herdctl import skills as s
from herdctl import discord_design

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_SKILLS_DIR = REPO_ROOT / "skills" / "shared"
CONFIG_DIR = REPO_ROOT / "config"


class BaseP2Test(unittest.TestCase):
    """Base test class providing an isolated temporary SQLite database."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_p2.db"
        self.cx = m.connection(self.db_path)

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def add_mem(self, *, project: str | None = None, scope: str | None = None,
                type: str = "decision", subject: str = "baseline",
                content: str = "content payload", evidence: str = "git:deadbeef0123",
                status: str = "active") -> str:
        resolved_scope = scope or ("project" if project else "global")
        return m.add(
            self.cx,
            scope=resolved_scope,
            project=project,
            type=type,
            subject=subject,
            content=content,
            evidence=evidence,
            status=status,
        )


class TestProjectIsolationAndNegativeAccess(BaseP2Test):
    """Objective 3a: Global vs project isolation and cross-project negative access tests."""

    def test_cross_project_search_negative_access(self):
        """Agent in 'starlight-tempo' CANNOT retrieve 'federated-learning-iot' memories, and vice-versa."""
        self.add_mem(
            project="starlight-tempo",
            subject="starlight-mtp-config",
            content="MTP batch size 32 for starlight acoustic model",
            evidence="git:starlight123",
        )
        self.add_mem(
            project="federated-learning-iot",
            subject="iot-federated-fbid",
            content="FBID dataset partition scheme across 100 IoT nodes",
            evidence="git:iot123456",
        )
        self.add_mem(
            project=None,
            scope="global",
            subject="global-gpu-safety",
            content="Always verify CUDA memory constraints before training",
            evidence="doc:gpu-runbook.md",
        )

        # 1. Search under starlight-tempo:
        st_hits = m.search(self.cx, "MTP FBID CUDA", project="starlight-tempo")
        st_subjects = {x["subject"] for x in st_hits}
        self.assertIn("starlight-mtp-config", st_subjects)
        self.assertIn("global-gpu-safety", st_subjects)
        self.assertNotIn("iot-federated-fbid", st_subjects, "Cross-project leak: IoT found in starlight-tempo")

        # 2. Search under federated-learning-iot:
        fl_hits = m.search(self.cx, "MTP FBID CUDA", project="federated-learning-iot")
        fl_subjects = {x["subject"] for x in fl_hits}
        self.assertIn("iot-federated-fbid", fl_subjects)
        self.assertIn("global-gpu-safety", fl_subjects)
        self.assertNotIn("starlight-mtp-config", fl_subjects, "Cross-project leak: starlight found in IoT")

    def test_global_scope_discoverable_by_all_projects(self):
        """Global scope memories are discoverable by both projects and global queries."""
        mid = self.add_mem(
            project=None,
            scope="global",
            subject="durable-procedure",
            content="Standard Git worktree handoff protocol",
            evidence="doc:P2-SHARED.md",
        )
        res_st = m.search(self.cx, "handoff protocol", project="starlight-tempo")
        res_fl = m.search(self.cx, "handoff protocol", project="federated-learning-iot")
        res_gl = m.search(self.cx, "handoff protocol", project=None, global_only=True)

        self.assertEqual([x["id"] for x in res_st], [mid])
        self.assertEqual([x["id"] for x in res_fl], [mid])
        self.assertEqual([x["id"] for x in res_gl], [mid])

    def test_global_only_flag_excludes_project_memories(self):
        """When global_only=True, project-private memories are excluded even if project is passed."""
        self.add_mem(project="starlight-tempo", subject="private-starlight", content="token boundary policy")
        self.add_mem(project=None, scope="global", subject="global-boundary", content="token boundary policy")

        hits = m.search(self.cx, "boundary", project="starlight-tempo", global_only=True)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["subject"], "global-boundary")
        self.assertEqual(hits[0]["scope"], "global")

    def test_cross_project_linking_forbidden(self):
        """Memories across different projects cannot be linked."""
        m_st = self.add_mem(project="starlight-tempo", subject="st-1")
        m_fl = self.add_mem(project="federated-learning-iot", subject="fl-1")
        with self.assertRaises(m.MemoryError):
            m.link(self.cx, m_st, "DEPENDS_ON", m_fl, project="starlight-tempo")
        with self.assertRaises(m.MemoryError):
            m.link(self.cx, m_st, "DEPENDS_ON", m_fl, project="federated-learning-iot")

    def test_cross_scope_linking_forbidden(self):
        """Global and project memories cannot be directly linked."""
        m_proj = self.add_mem(project="starlight-tempo", subject="p-1")
        m_glob = self.add_mem(project=None, scope="global", subject="g-1")
        with self.assertRaises(m.MemoryError):
            m.link(self.cx, m_proj, "USES", m_glob, project="starlight-tempo")

    def test_cross_project_graph_root_access_denied(self):
        """Initiating graph traversal on a memory belonging to another project is denied."""
        m_fl = self.add_mem(project="federated-learning-iot", subject="fl-root")
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, m_fl, project="starlight-tempo")

    def test_cross_project_edge_tampering_ignored_by_graph(self):
        """Even if an edge exists directly in SQLite between projects, graph() filters it out."""
        m_st = self.add_mem(project="starlight-tempo", subject="st-head")
        m_fl = self.add_mem(project="federated-learning-iot", subject="fl-leaked")
        self.cx.execute("INSERT INTO edges VALUES (?,?,?)", (m_st, "RELATED_TO", m_fl))
        self.cx.commit()

        g = m.graph(self.cx, m_st, project="starlight-tempo", hops=2)
        node_ids = {n["id"] for n in g["nodes"]}
        self.assertIn(m_st, node_ids)
        self.assertNotIn(m_fl, node_ids, "Graph leaked cross-project node")
        self.assertEqual(g["edges"], [])

    def test_task_checkpoint_project_boundary_enforcement(self):
        """Task checkpoint created in 'starlight-tempo' cannot be reassigned or stolen by 'federated-learning-iot'."""
        m.task_upsert(self.cx, "task-starlight-100", "starlight-tempo", "Run benchmark", "queued")
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx, "task-starlight-100", "federated-learning-iot", "Hijack task", "working")

        row = self.cx.execute("SELECT * FROM task_state WHERE id='task-starlight-100'").fetchone()
        self.assertEqual(row["project"], "starlight-tempo")
        self.assertEqual(row["title"], "Run benchmark")
        self.assertEqual(row["status"], "queued")

    def test_task_checkpoint_safe_text_sanitization(self):
        """Task checkpoints reject secret patterns in task_id, title, agent, or evidence."""
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx, "task-sec-1", "starlight-tempo", "Deploy api_key = AKIA12345678", "queued")
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx, "task-sec-2", "starlight-tempo", "Valid title", "queued",
                          agent="worker-1", evidence="token: sk-1234567890abcdef1234")
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx, "task-sec-3", "starlight-tempo", "Valid title", "queued",
                          agent="api_key = abcde12345")
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx, "api_key = key12345", "starlight-tempo", "Valid title", "queued")


class TestMemoryLifecycle(BaseP2Test):
    """Objective 3b: Memory lifecycle (active, superseded, retracted) transitions and query exclusion."""

    def test_lifecycle_default_active(self):
        """Newly created memory defaults to active status."""
        mid = self.add_mem(project="starlight-tempo", subject="default-active")
        row = self.cx.execute("SELECT status FROM memories WHERE id=?", (mid,)).fetchone()
        self.assertEqual(row["status"], "active")

    def test_lifecycle_creation_with_status(self):
        """Creating memories with explicit superseded or retracted status is supported."""
        m_sup = self.add_mem(project="starlight-tempo", subject="sup-init", status="superseded")
        m_ret = self.add_mem(project="starlight-tempo", subject="ret-init", status="retracted")
        self.assertEqual(self.cx.execute("SELECT status FROM memories WHERE id=?", (m_sup,)).fetchone()["status"], "superseded")
        self.assertEqual(self.cx.execute("SELECT status FROM memories WHERE id=?", (m_ret,)).fetchone()["status"], "retracted")

        with self.assertRaises(m.MemoryError):
            self.add_mem(project="starlight-tempo", status="invalid_status")

    def test_lifecycle_status_transitions(self):
        """Verify set_status and retire transitions across active, superseded, and retracted."""
        mid = self.add_mem(project="starlight-tempo", subject="stateful-node")

        # active -> superseded
        m.retire(self.cx, mid, "superseded", project="starlight-tempo")
        self.assertEqual(self.cx.execute("SELECT status FROM memories WHERE id=?", (mid,)).fetchone()["status"], "superseded")

        # superseded -> active
        m.set_status(self.cx, mid, "active", project="starlight-tempo")
        self.assertEqual(self.cx.execute("SELECT status FROM memories WHERE id=?", (mid,)).fetchone()["status"], "active")

        # active -> retracted
        m.retire(self.cx, mid, "retracted", project="starlight-tempo")
        self.assertEqual(self.cx.execute("SELECT status FROM memories WHERE id=?", (mid,)).fetchone()["status"], "retracted")

        # invalid status raises
        with self.assertRaises(m.MemoryError):
            m.set_status(self.cx, mid, "nonexistent", project="starlight-tempo")

        # unauthorized cross-project transition raises
        with self.assertRaises(m.MemoryError):
            m.set_status(self.cx, mid, "active", project="federated-learning-iot")

    def test_search_excludes_superseded_and_retracted_by_default(self):
        """Search by default excludes superseded and retracted records."""
        m_act = self.add_mem(project="starlight-tempo", subject="item-active", content="queryable lifecycle item")
        m_sup = self.add_mem(project="starlight-tempo", subject="item-superseded", content="queryable lifecycle item", status="superseded")
        m_ret = self.add_mem(project="starlight-tempo", subject="item-retracted", content="queryable lifecycle item", status="retracted")

        hits = m.search(self.cx, "queryable lifecycle", project="starlight-tempo")
        hit_ids = [h["id"] for h in hits]
        self.assertEqual(hit_ids, [m_act])

    def test_search_explicit_status_request(self):
        """Explicitly requesting superseded or retracted memories returns them."""
        m_act = self.add_mem(project="starlight-tempo", subject="item-act", content="lifecycle probe item")
        m_sup = self.add_mem(project="starlight-tempo", subject="item-sup", content="lifecycle probe item", status="superseded")
        m_ret = self.add_mem(project="starlight-tempo", subject="item-ret", content="lifecycle probe item", status="retracted")

        # Explicit superseded
        hits_sup = m.search(self.cx, "lifecycle probe", project="starlight-tempo", status="superseded")
        self.assertEqual([h["id"] for h in hits_sup], [m_sup])

        # Explicit retracted
        hits_ret = m.search(self.cx, "lifecycle probe", project="starlight-tempo", status="retracted")
        self.assertEqual([h["id"] for h in hits_ret], [m_ret])

        # Include retired / all
        hits_all = m.search(self.cx, "lifecycle probe", project="starlight-tempo", include_retired=True)
        self.assertEqual(len(hits_all), 3)

        hits_tuple = m.search(self.cx, "lifecycle probe", project="starlight-tempo", status=("active", "superseded"))
        self.assertEqual({h["id"] for h in hits_tuple}, {m_act, m_sup})

        with self.assertRaises(m.MemoryError):
            m.search(self.cx, "probe", project="starlight-tempo", status="invalid_filter")

    def test_graph_excludes_retired_neighbors_by_default(self):
        """Graph traversal by default excludes superseded and retracted neighbors."""
        root = self.add_mem(project="starlight-tempo", subject="root")
        n_act = self.add_mem(project="starlight-tempo", subject="neighbor-active")
        n_sup = self.add_mem(project="starlight-tempo", subject="neighbor-sup", status="superseded")
        n_ret = self.add_mem(project="starlight-tempo", subject="neighbor-ret", status="retracted")

        m.link(self.cx, root, "DEPENDS_ON", n_act, project="starlight-tempo")
        m.link(self.cx, root, "SUPERSEDES", n_sup, project="starlight-tempo")
        m.link(self.cx, root, "RELATED_TO", n_ret, project="starlight-tempo")

        g = m.graph(self.cx, root, project="starlight-tempo", hops=1)
        node_ids = {n["id"] for n in g["nodes"]}
        self.assertEqual(node_ids, {root, n_act})
        self.assertEqual(len(g["edges"]), 1)
        self.assertEqual(g["edges"][0]["target"], n_act)

    def test_graph_explicit_include_retired(self):
        """When include_retired=True, graph traversal includes retired nodes and edges."""
        root = self.add_mem(project="starlight-tempo", subject="root")
        n_sup = self.add_mem(project="starlight-tempo", subject="neighbor-sup", status="superseded")
        m.link(self.cx, root, "SUPERSEDES", n_sup, project="starlight-tempo")

        g = m.graph(self.cx, root, project="starlight-tempo", hops=1, include_retired=True)
        self.assertEqual({n["id"] for n in g["nodes"]}, {root, n_sup})
        self.assertEqual(len(g["edges"]), 1)


class TestGraphTraversal(BaseP2Test):
    """Objective 3c: Graph traversal deduplication, cycle termination, hop clamping [0..3], and retracted root denial."""

    def test_graph_deduplication_diamond(self):
        """Diamond topology (A->B, A->C, B->D, C->D) returns each node and edge exactly once."""
        a = self.add_mem(project="starlight-tempo", subject="A")
        b = self.add_mem(project="starlight-tempo", subject="B")
        c = self.add_mem(project="starlight-tempo", subject="C")
        d = self.add_mem(project="starlight-tempo", subject="D")

        m.link(self.cx, a, "DEPENDS_ON", b, project="starlight-tempo")
        m.link(self.cx, a, "DEPENDS_ON", c, project="starlight-tempo")
        m.link(self.cx, b, "DEPENDS_ON", d, project="starlight-tempo")
        m.link(self.cx, c, "DEPENDS_ON", d, project="starlight-tempo")

        g = m.graph(self.cx, a, project="starlight-tempo", hops=2)
        node_ids = [n["id"] for n in g["nodes"]]
        self.assertEqual(len(node_ids), 4)
        self.assertEqual(len(set(node_ids)), 4)
        self.assertEqual(len(g["edges"]), 4)

    def test_graph_cycle_termination(self):
        """Cycle (A->B->C->A) terminates cleanly without infinite recursion."""
        a = self.add_mem(project="starlight-tempo", subject="cycle-A")
        b = self.add_mem(project="starlight-tempo", subject="cycle-B")
        c = self.add_mem(project="starlight-tempo", subject="cycle-C")

        m.link(self.cx, a, "RELATED_TO", b, project="starlight-tempo")
        m.link(self.cx, b, "RELATED_TO", c, project="starlight-tempo")
        m.link(self.cx, c, "RELATED_TO", a, project="starlight-tempo")

        g2 = m.graph(self.cx, a, project="starlight-tempo", hops=2)
        self.assertEqual(len(g2["nodes"]), 3)

        g3 = m.graph(self.cx, a, project="starlight-tempo", hops=3)
        self.assertEqual(len(g3["nodes"]), 3)
        self.assertEqual(len(g3["edges"]), 3)

    def test_graph_self_edge_denied(self):
        """Self-loop edge cannot be created."""
        a = self.add_mem(project="starlight-tempo", subject="self-loop")
        with self.assertRaises(m.MemoryError):
            m.link(self.cx, a, "RELATED_TO", a, project="starlight-tempo")

    def test_graph_hop_clamping(self):
        """Hop clamping [0..3]: 0..3 hops allowed, negative or >3 hops raise MemoryError."""
        nodes = [self.add_mem(project="starlight-tempo", subject=f"hop-node-{i}") for i in range(5)]
        for i in range(4):
            m.link(self.cx, nodes[i], "DEPENDS_ON", nodes[i + 1], project="starlight-tempo")

        # hops = 0: root only, 0 edges
        g0 = m.graph(self.cx, nodes[0], project="starlight-tempo", hops=0)
        self.assertEqual(len(g0["nodes"]), 1)
        self.assertEqual(len(g0["edges"]), 0)

        # hops = 1: 2 nodes, 1 edge
        g1 = m.graph(self.cx, nodes[0], project="starlight-tempo", hops=1)
        self.assertEqual(len(g1["nodes"]), 2)
        self.assertEqual(len(g1["edges"]), 1)

        # hops = 2: 3 nodes, 2 edges
        g2 = m.graph(self.cx, nodes[0], project="starlight-tempo", hops=2)
        self.assertEqual(len(g2["nodes"]), 3)
        self.assertEqual(len(g2["edges"]), 2)

        # hops = 3: 4 nodes, 3 edges
        g3 = m.graph(self.cx, nodes[0], project="starlight-tempo", hops=3)
        self.assertEqual(len(g3["nodes"]), 4)
        self.assertEqual(len(g3["edges"]), 3)

        # Out of bounds
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, nodes[0], project="starlight-tempo", hops=-1)
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, nodes[0], project="starlight-tempo", hops=4)
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, nodes[0], project="starlight-tempo", hops=10)

    def test_graph_retracted_root_denied(self):
        """Retracted root is rejected by graph() by default."""
        root = self.add_mem(project="starlight-tempo", subject="retracted-root", status="retracted")
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, root, project="starlight-tempo")

    def test_graph_superseded_root_denied(self):
        """Superseded root is rejected by graph() by default."""
        root = self.add_mem(project="starlight-tempo", subject="superseded-root", status="superseded")
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, root, project="starlight-tempo")


class TestTokenBudgetAndContextAssembly(BaseP2Test):
    """Objective 3d: Token budget clamping and compact context assembly."""

    def test_clamp_budget_helper(self):
        """clamp_budget clamps integers to [100..10000]."""
        self.assertEqual(m.clamp_budget(0), 100)
        self.assertEqual(m.clamp_budget(-50), 100)
        self.assertEqual(m.clamp_budget(99), 100)
        self.assertEqual(m.clamp_budget(100), 100)
        self.assertEqual(m.clamp_budget(450), 450)
        self.assertEqual(m.clamp_budget(1600), 1600)
        self.assertEqual(m.clamp_budget(10000), 10000)
        self.assertEqual(m.clamp_budget(10001), 10000)
        self.assertEqual(m.clamp_budget(50000), 10000)

    def test_context_budget_clamping_flag(self):
        """context() with clamp=True clamps out-of-range budgets rather than raising."""
        self.add_mem(project="starlight-tempo", subject="item-1", content="simple content for context")

        # Without clamp: underflow / overflow raises MemoryError
        with self.assertRaises(m.MemoryError):
            m.context(self.cx, project="starlight-tempo", query="content", budget=50, clamp=False)
        with self.assertRaises(m.MemoryError):
            m.context(self.cx, project="starlight-tempo", query="content", budget=20000, clamp=False)

        # With clamp: underflow clamps to 100, overflow clamps to 10000
        res_low = m.context(self.cx, project="starlight-tempo", query="content", budget=50, clamp=True)
        self.assertEqual(res_low["budget_tokens"], 100)

        res_high = m.context(self.cx, project="starlight-tempo", query="content", budget=20000, clamp=True)
        self.assertEqual(res_high["budget_tokens"], 10000)

    def test_compact_context_assembly_and_budget_bounds(self):
        """Context assembly respects character bound (budget * 4) and formats evidence pointers."""
        for i in range(12):
            self.add_mem(
                project="starlight-tempo",
                subject=f"config-{i:02d}",
                content=f"Context assembly data chunk number {i:02d} " + ("c" * 200),
                evidence=f"git:commit{i:02d}",
            )

        budget = 150
        max_chars = budget * 4
        res = m.context(self.cx, project="starlight-tempo", query="assembly", budget=budget)
        self.assertLessEqual(len(res["text"]), max_chars)
        self.assertGreaterEqual(res["records"], 1)
        self.assertLessEqual(res["estimated_tokens_upper_bound"], budget)

        # Check line structure
        lines = res["text"].splitlines()
        self.assertEqual(len(lines), res["records"])
        for line in lines:
            self.assertTrue(line.startswith("[mem-"))
            self.assertIn("(project/decision)", line)
            self.assertIn("[evidence: git:commit", line)

    def test_initial_vs_followup_retrieval_budgets(self):
        """Test initial vs follow-up retrieval budgets from token_policy across roles."""
        policy_path = CONFIG_DIR / "token_policy.example.json"
        self.assertTrue(policy_path.is_file())
        policy = json.loads(policy_path.read_text(encoding="utf-8"))

        # Populate database with multiple records
        for i in range(20):
            self.add_mem(
                project="starlight-tempo",
                subject=f"procedure-{i:02d}",
                content=f"Procedure step {i:02d}: run verification and record evidence " + ("z" * 150),
                evidence=f"git:sha{i:02d}",
            )

        roles_to_test = ["supervisor", "implementer", "reviewer", "explorer"]
        for role in roles_to_test:
            with self.subTest(role=role):
                initial_b = discord_design.budget(REPO_ROOT, role, followup=False)
                followup_b = discord_design.budget(REPO_ROOT, role, followup=True)

                init_tokens = initial_b["memory_context_budget_estimate"]
                foll_tokens = followup_b["memory_context_budget_estimate"]

                # Both budgets within [100..10000]
                self.assertGreaterEqual(init_tokens, 100)
                self.assertLessEqual(init_tokens, 10000)
                self.assertGreaterEqual(foll_tokens, 100)
                self.assertLessEqual(foll_tokens, 10000)

                # Initial budget strictly greater than follow-up budget
                self.assertGreater(init_tokens, foll_tokens)

                # Context retrieved with initial budget yields equal or more records than follow-up
                ctx_init = m.context(self.cx, project="starlight-tempo", query="procedure", budget=init_tokens)
                ctx_foll = m.context(self.cx, project="starlight-tempo", query="procedure", budget=foll_tokens)
                self.assertGreaterEqual(ctx_init["records"], ctx_foll["records"])
                self.assertGreaterEqual(len(ctx_init["text"]), len(ctx_foll["text"]))


class TestCanonicalSharedSkills(unittest.TestCase):
    """Objective 3e: Canonical shared skills validation and agent discovery."""

    def test_canonical_skills_files_exist(self):
        """All 5 canonical skills exist with SKILL.md."""
        expected = ["git-worktree", "herdr-control", "memory-recall", "scientific-review", "task-handoff"]
        self.assertEqual(list(s.CANONICAL_SKILLS), expected)
        canonical = s.get_canonical_skills(SHARED_SKILLS_DIR)
        self.assertEqual(sorted(canonical.keys()), sorted(expected))

    def test_canonical_skills_frontmatter_and_instructions(self):
        """Each canonical skill has valid name, description, and substantive instructions."""
        canonical = s.get_canonical_skills(SHARED_SKILLS_DIR)
        for name, meta in canonical.items():
            with self.subTest(skill=name):
                self.assertEqual(meta["name"], name)
                self.assertIsInstance(meta["description"], str)
                self.assertGreater(len(meta["description"]), 20)
                self.assertTrue(meta["description"][0].isupper())
                self.assertIn("# ", meta["instructions"])
                self.assertGreater(len(meta["instructions"]), 100)

    def test_skill_instructions_key_content(self):
        """Canonical skills contain required operational rules and commands."""
        canonical = s.get_canonical_skills(SHARED_SKILLS_DIR)

        # git-worktree: isolation, worktree, approval
        gw = canonical["git-worktree"]["instructions"]
        self.assertIn("worktree", gw.lower())
        self.assertIn("owner approval", gw.lower())

        # herdr-control: herdctl wrapper, approval
        hc = canonical["herdr-control"]["instructions"]
        self.assertIn("herdctl", hc)
        self.assertIn("owner approval", hc.lower())

        # memory-recall: context, budget, evidence, status
        mr = canonical["memory-recall"]["instructions"]
        self.assertIn("herdctl memory context", mr)
        self.assertIn("evidence", mr.lower())
        self.assertIn("superseded", mr.lower())

        # scientific-review: leakage, baselines, reproducibility
        sr = canonical["scientific-review"]["instructions"]
        self.assertIn("leakage", sr.lower())
        self.assertIn("reproducibility", sr.lower())

        # task-handoff: task set, checkpoint, reviewer
        th = canonical["task-handoff"]["instructions"]
        self.assertIn("herdctl task set", th)
        self.assertIn("evidence", th.lower())

    def test_skill_markdown_parser_edge_cases(self):
        """Parser correctly validates frontmatter and rejects malformed formats."""
        # Missing frontmatter delimiter
        with self.assertRaises(s.SkillError):
            s.parse_skill_markdown("# Just a header without frontmatter")

        # Missing name
        with self.assertRaises(s.SkillError):
            s.parse_skill_markdown("---\ndescription: some skill\n---\n# Instructions")

        # Missing description
        with self.assertRaises(s.SkillError):
            s.parse_skill_markdown("---\nname: valid-name\n---\n# Instructions")

        # Empty body
        with self.assertRaises(s.SkillError):
            s.parse_skill_markdown("---\nname: my-skill\ndescription: my desc\n---\n")

    def test_agent_skill_discovery_mechanisms(self):
        """Simulate skill discovery across Codex, Claude Code, and Hermes Agent."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            agents_dir = tmp_root / ".agents" / "skills"
            claude_dir = tmp_root / ".claude" / "skills"

            # Install symlinks via plan and apply
            plan = s.plan_symlinks(SHARED_SKILLS_DIR, [agents_dir, claude_dir])
            self.assertEqual(len(plan), 10)  # 5 skills * 2 roots
            for p in plan:
                self.assertEqual(p["action"], "LINK")
                self.assertFalse(p["exists"])

            results = s.install_symlinks(SHARED_SKILLS_DIR, [agents_dir, claude_dir], apply=True)
            self.assertEqual(len(results), 10)
            for r in results:
                self.assertTrue(r["applied"])

            # 1. Codex discovery (~/.agents/skills)
            codex_skills = s.discover_codex_skills(agents_dir)
            self.assertEqual(sorted(codex_skills.keys()), sorted(s.CANONICAL_SKILLS))

            # 2. Claude Code discovery (~/.claude/skills)
            claude_skills = s.discover_claude_skills(claude_dir)
            self.assertEqual(sorted(claude_skills.keys()), sorted(s.CANONICAL_SKILLS))

            # 3. Hermes Agent discovery (skills.external_dirs)
            hermes_skills = s.discover_hermes_skills([SHARED_SKILLS_DIR])
            self.assertEqual(sorted(hermes_skills.keys()), sorted(s.CANONICAL_SKILLS))

            # 4. OpenClaw discovery (skills.load.extraDirs)
            openclaw_skills = s.discover_openclaw_skills([SHARED_SKILLS_DIR])
            self.assertEqual(sorted(openclaw_skills.keys()), sorted(s.CANONICAL_SKILLS))

            # 5. Idempotent re-run does NOT overwrite existing links
            second_run = s.install_symlinks(SHARED_SKILLS_DIR, [agents_dir, claude_dir], apply=True)
            for r in second_run:
                self.assertEqual(r["action"], "SKIP")
                self.assertFalse(r["applied"])


class TestProvenanceAndEvidenceEnforcement(BaseP2Test):
    """Objective 3f: Provenance and evidence enforcement."""

    def test_empty_and_whitespace_evidence_rejected(self):
        """Empty string or whitespace-only evidence pointers are rejected."""
        with self.assertRaises(m.MemoryError):
            self.add_mem(evidence="")
        with self.assertRaises(m.MemoryError):
            self.add_mem(evidence="   ")
        with self.assertRaises(m.MemoryError):
            self.add_mem(evidence="\t")
        with self.assertRaises(m.MemoryError):
            self.add_mem(evidence="\n\r  \n")

    def test_valid_evidence_pointers_accepted(self):
        """Valid evidence pointers (git SHA, file URI, ADR decision doc) are accepted."""
        m1 = self.add_mem(evidence="git:7a8b9c0d1e2f")
        m2 = self.add_mem(evidence="file:///repo/src/herdctl/memory.py#L42-L60")
        m3 = self.add_mem(evidence="decision:ADR-002-sqlite-wal-concurrency")

        row1 = self.cx.execute("SELECT evidence FROM memories WHERE id=?", (m1,)).fetchone()
        self.assertEqual(row1["evidence"], "git:7a8b9c0d1e2f")
        row2 = self.cx.execute("SELECT evidence FROM memories WHERE id=?", (m2,)).fetchone()
        self.assertEqual(row2["evidence"], "file:///repo/src/herdctl/memory.py#L42-L60")
        row3 = self.cx.execute("SELECT evidence FROM memories WHERE id=?", (m3,)).fetchone()
        self.assertEqual(row3["evidence"], "decision:ADR-002-sqlite-wal-concurrency")

    def test_credentials_in_evidence_rejected(self):
        """Evidence containing credentials or API keys is rejected by safe_text."""
        with self.assertRaises(m.MemoryError):
            self.add_mem(evidence="api_key = AKIA1234567890EXAMPLE")
        with self.assertRaises(m.MemoryError):
            self.add_mem(evidence="ghp_123456789012345678901234567890")
        with self.assertRaises(m.MemoryError):
            self.add_mem(evidence="sk-1234567890abcdef12345678")

    def test_evidence_length_limits(self):
        """Evidence length must be between 1 and 12000 characters."""
        with self.assertRaises(m.MemoryError):
            self.add_mem(evidence="x" * 12001)

        valid_long = "x" * 12000
        mid = self.add_mem(evidence=valid_long)
        row = self.cx.execute("SELECT evidence FROM memories WHERE id=?", (mid,)).fetchone()
        self.assertEqual(len(row["evidence"]), 12000)


if __name__ == "__main__":
    unittest.main()
