"""Comprehensive concurrency, isolation, and security test suite for Herdctl memory.

Validates:
1. Multi-threaded / multi-connection concurrent writes under PRAGMA journal_mode=WAL.
2. FTS5 injection attacks (quotes, asterisks, AND/OR/NOT operators, non-ascii Unicode punctuation).
3. Strict cross-project isolation across memories, edges, graph traversals, and tasks.
4. Memory status lifecycle (active -> superseded -> retracted) excluding retired memories from search/graph.
5. Limit and budget clamping boundaries [100..12000] for context and [1..50] for search.
6. Secret redaction across all memory types and fields (PKCS#8, RSA, EC, OpenSSH, tokens).
"""
import concurrent.futures
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from herdctl import memory as m


class BaseMemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "memory.db"
        self.cx = m.connection(self.db_path)

    def tearDown(self):
        self.cx.close()
        self.temp_dir.cleanup()

    def add_mem(self, project=None, scope=None, mem_type="decision",
                subject="benchmark", content="baseline verification content",
                evidence="git:feedface1234", status="active"):
        actual_scope = scope or ("project" if project else "global")
        return m.add(
            self.cx,
            scope=actual_scope,
            project=project,
            type=mem_type,
            subject=subject,
            content=content,
            evidence=evidence,
            status=status,
        )


class TestMemoryConcurrency(BaseMemoryTest):
    """Multi-threaded and multi-connection concurrency verification under WAL mode."""

    def test_concurrent_multithreaded_writes_wal(self):
        """12 concurrent worker threads writing memories, tasks, and edges to WAL db."""
        num_threads = 12
        items_per_thread = 15
        errors: list[Exception] = []

        def worker_task(thread_idx: int):
            try:
                # In SQLite, each thread must use its own connection
                thread_cx = m.connection(self.db_path)
                proj = f"proj-{thread_idx % 3}"
                created_ids = []

                for i in range(items_per_thread):
                    mid = m.add(
                        thread_cx,
                        scope="project",
                        project=proj,
                        type="fact",
                        subject=f"worker-{thread_idx}-item-{i}",
                        content=f"Concurrent content entry {i} from thread {thread_idx}",
                        evidence=f"test:th-{thread_idx}-run-{i}",
                    )
                    created_ids.append(mid)

                    # Upsert task state concurrently
                    m.task_upsert(
                        thread_cx,
                        task_id=f"task-t{thread_idx}-i{i}",
                        project=proj,
                        title=f"Task {i} on thread {thread_idx}",
                        status="working" if i % 2 == 0 else "done",
                        agent=f"agent-{thread_idx}",
                        evidence=f"test:task-th-{thread_idx}",
                    )

                # Link adjacent pairs within the same project
                for j in range(len(created_ids) - 1):
                    m.link(
                        thread_cx,
                        source=created_ids[j],
                        relation="DEPENDS_ON",
                        target=created_ids[j + 1],
                        project=proj,
                    )

                thread_cx.close()
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread execution encountered errors: {errors}")

        # Verify integrity and row counts
        integrity = self.cx.execute("PRAGMA integrity_check").fetchall()
        self.assertEqual([dict(r)["integrity_check"] for r in integrity], ["ok"])

        fk_check = self.cx.execute("PRAGMA foreign_key_check").fetchall()
        self.assertEqual(len(fk_check), 0, f"Foreign key violations found: {fk_check}")

        total_memories = self.cx.execute("SELECT count(*) FROM memories").fetchone()[0]
        expected_total = num_threads * items_per_thread
        self.assertEqual(total_memories, expected_total)

        total_tasks = self.cx.execute("SELECT count(*) FROM task_state").fetchone()[0]
        self.assertEqual(total_tasks, expected_total)

        total_edges = self.cx.execute("SELECT count(*) FROM edges").fetchone()[0]
        expected_edges = num_threads * (items_per_thread - 1)
        self.assertEqual(total_edges, expected_edges)

    def test_concurrent_readers_during_writers_wal(self):
        """Readers querying search, graph, and context while writers are actively mutating."""
        stop_event = threading.Event()
        writer_errors: list[Exception] = []
        reader_errors: list[Exception] = []

        # Seed initial memories
        m1 = self.add_mem(project="proj-rw", subject="root-seed", content="initial seed item")
        m2 = self.add_mem(project="proj-rw", subject="child-seed", content="initial child item")
        m.link(self.cx, m1, "DEPENDS_ON", m2, "proj-rw")

        def writer():
            try:
                wcx = m.connection(self.db_path)
                counter = 0
                while not stop_event.is_set() and counter < 30:
                    mid = m.add(
                        wcx,
                        scope="project",
                        project="proj-rw",
                        type="experiment",
                        subject=f"dynamic-{counter}",
                        content=f"dynamic content payload {counter}",
                        evidence="test:dynamic",
                    )
                    m.task_upsert(
                        wcx,
                        task_id=f"rw-task-{counter}",
                        project="proj-rw",
                        title=f"RW dynamic task {counter}",
                        status="queued",
                    )
                    counter += 1
                wcx.close()
            except Exception as e:
                writer_errors.append(e)

        def reader():
            try:
                rcx = m.connection(self.db_path)
                while not stop_event.is_set():
                    res = m.search(rcx, "seed dynamic", project="proj-rw")
                    self.assertIsInstance(res, list)
                    g = m.graph(rcx, m1, project="proj-rw", hops=1)
                    self.assertGreaterEqual(len(g["nodes"]), 1)
                    ctx = m.context(rcx, project="proj-rw", query="seed", budget=200)
                    self.assertIn("records", ctx)
                rcx.close()
            except Exception as e:
                reader_errors.append(e)

        writer_threads = [threading.Thread(target=writer) for _ in range(3)]
        reader_threads = [threading.Thread(target=reader) for _ in range(4)]

        for t in reader_threads + writer_threads:
            t.start()
        for t in writer_threads:
            t.join()

        stop_event.set()
        for t in reader_threads:
            t.join()

        self.assertEqual(writer_errors, [])
        self.assertEqual(reader_errors, [])


class TestFTS5InjectionSafety(BaseMemoryTest):
    """FTS5 adversarial injection attacks: quotes, asterisks, operators, unicode punctuation."""

    def setUp(self):
        super().setUp()
        # Seed test documents for FTS searching
        self.add_mem(
            project="proj-fts",
            subject="architecture-design",
            content="System architecture overview for secure distributed compute nodes",
            evidence="doc:arch.md",
        )
        self.add_mem(
            project="proj-fts",
            subject="¿problema-de-conexión?",
            content="Error handling with «guillemets» and inverted punctuation ¡alerta!",
            evidence="doc:i18n.md",
        )
        self.add_mem(
            project="proj-fts",
            subject="cache-eviction",
            content="Cache invalidation policies under LRU and FIFO algorithms",
            evidence="doc:cache.md",
        )

    def test_unbalanced_and_nested_quotes(self):
        """Search query with wild quote patterns must never throw SQLite/FTS syntax error."""
        quote_payloads = [
            '"',
            '""',
            '"""',
            '"""""',
            "'",
            "''",
            "'''",
            '" OR 1=1; --',
            "' OR '1'='1",
            'architecture" AND "design',
            '"""secure"""',
            '"unterminated double quote',
            "'unterminated single quote",
            '""" OR """="',
            '\'"\'"\'"',
        ]
        for query in quote_payloads:
            with self.subTest(query=query):
                results = m.search(self.cx, query, project="proj-fts")
                self.assertIsInstance(results, list)

    def test_asterisks_and_wildcards(self):
        """Asterisks and glob characters must be sanitized and never crash FTS5."""
        wildcard_payloads = [
            "*",
            "**",
            "***",
            "arch*",
            "*arch",
            "*arch*",
            "***secure***",
            "cache * eviction",
            "* OR *",
            "*** AND ***",
        ]
        for query in wildcard_payloads:
            with self.subTest(query=query):
                results = m.search(self.cx, query, project="proj-fts")
                self.assertIsInstance(results, list)

    def test_boolean_and_proximity_operators(self):
        """Raw boolean operators and FTS keywords must be safely sanitized as tokens."""
        operator_payloads = [
            "AND",
            "OR",
            "NOT",
            "AND AND",
            "OR OR",
            "NOT NOT",
            "AND OR NOT",
            "architecture AND",
            "AND architecture",
            "architecture NOT",
            "NOT architecture",
            "architecture AND NOT design",
            "NEAR(architecture, design)",
            "NEAR(architecture, 5)",
            "ONE NEAR/2 TWO",
            "MATCH 'architecture'",
        ]
        for query in operator_payloads:
            with self.subTest(query=query):
                results = m.search(self.cx, query, project="proj-fts")
                self.assertIsInstance(results, list)

    def test_column_filter_and_fts_syntax_injection(self):
        """Column specifiers, rowid tricks, and caret prefix queries must be neutralized."""
        injection_payloads = [
            "subject:architecture",
            "content:secure",
            "rowid:1",
            "memory_fts:architecture",
            "^architecture",
            "^",
            "^^^",
            "{architecture design}",
            "(architecture OR design)",
            "((()))",
            "[architecture]",
        ]
        for query in injection_payloads:
            with self.subTest(query=query):
                results = m.search(self.cx, query, project="proj-fts")
                self.assertIsInstance(results, list)

    def test_unicode_punctuation_and_emojis(self):
        """Non-ascii punctuation, smart quotes, em-dashes, and emojis must be handled safely."""
        unicode_payloads = [
            "“smart quotes”",
            "‘single curly’",
            "«guillemets»",
            "„german quotes“",
            "architecture—overview…",
            "¿problema? ¡alerta!",
            "§123 ¶456 †dagger •bullet",
            "【重要】「メモ」『テスト』",
            "🔥 🚀 🛡️ 🔒",
            "zero\u200bwidth\u200bspace",
            "math: ± × ÷ ∑ √ ∞ ≠ ≤ ≥",
        ]
        for query in unicode_payloads:
            with self.subTest(query=query):
                results = m.search(self.cx, query, project="proj-fts")
                self.assertIsInstance(results, list)

    def test_empty_and_whitespace_and_punctuation_only_queries(self):
        """Empty, pure whitespace, or pure punctuation queries return empty results cleanly."""
        pure_punct_payloads = [
            "",
            "   ",
            "\t\r\n",
            "!@#$%^&*()_+=-~`{}[]|;:,.<>?",
            "—…«»“”‘’¿¡",
            "   ***   \"\"\"   ---   ",
        ]
        for query in pure_punct_payloads:
            with self.subTest(query=query):
                self.assertEqual(m.search(self.cx, query, project="proj-fts"), [])
                # Context should also work cleanly without crashing
                ctx = m.context(self.cx, project="proj-fts", query=query, budget=200)
                self.assertEqual(ctx["records"], 0)
                self.assertEqual(ctx["text"], "")

    def test_grounded_match_verification_with_sanitized_tokens(self):
        """Ensure search still accurately matches records when query contains punctuation."""
        # Query with inverted punctuation should match ¿problema-de-conexión?
        hits = m.search(self.cx, "¿problema? ¡alerta!", project="proj-fts")
        matched_subjects = {h["subject"] for h in hits}
        self.assertIn("¿problema-de-conexión?", matched_subjects)

        # Query with quotes around architecture should match architecture-design
        hits = m.search(self.cx, '"architecture"', project="proj-fts")
        matched_subjects = {h["subject"] for h in hits}
        self.assertIn("architecture-design", matched_subjects)


class TestCrossProjectIsolation(BaseMemoryTest):
    """Strict verification that cross-project queries never return other projects' data."""

    def setUp(self):
        super().setUp()
        # Seed memories in project A, project B, and global scope
        self.a1 = self.add_mem(
            project="proj-alpha",
            subject="secret-algorithm-a",
            content="Confidential machine learning weights for alpha project",
            evidence="git:alpha01",
        )
        self.a2 = self.add_mem(
            project="proj-alpha",
            subject="pipeline-config-a",
            content="Pipeline hyperparameter specifications for alpha",
            evidence="git:alpha02",
        )
        self.b1 = self.add_mem(
            project="proj-beta",
            subject="secret-algorithm-b",
            content="Proprietary quantitative finance signals for beta project",
            evidence="git:beta01",
        )
        self.b2 = self.add_mem(
            project="proj-beta",
            subject="pipeline-config-b",
            content="Pipeline infrastructure configuration for beta",
            evidence="git:beta02",
        )
        self.g1 = self.add_mem(
            project=None,
            scope="global",
            subject="common-ground-rule",
            content="Global coding standard and pipeline validation guideline",
            evidence="doc:standards.md",
        )

    def test_search_never_leaks_other_project_memories(self):
        """Searching from proj-alpha must return only alpha + global, never beta."""
        # Query matching terms common to both projects: 'secret pipeline algorithm'
        results_a = m.search(self.cx, "secret pipeline algorithm", project="proj-alpha")
        subjects_a = {r["subject"] for r in results_a}
        projects_a = {r["project"] for r in results_a if r["scope"] == "project"}

        self.assertIn("secret-algorithm-a", subjects_a)
        self.assertIn("pipeline-config-a", subjects_a)
        self.assertNotIn("secret-algorithm-b", subjects_a)
        self.assertNotIn("pipeline-config-b", subjects_a)
        self.assertTrue(all(p == "proj-alpha" for p in projects_a))

        # Searching from proj-beta must return only beta + global, never alpha
        results_b = m.search(self.cx, "secret pipeline algorithm", project="proj-beta")
        subjects_b = {r["subject"] for r in results_b}
        projects_b = {r["project"] for r in results_b if r["scope"] == "project"}

        self.assertIn("secret-algorithm-b", subjects_b)
        self.assertIn("pipeline-config-b", subjects_b)
        self.assertNotIn("secret-algorithm-a", subjects_b)
        self.assertNotIn("pipeline-config-a", subjects_b)
        self.assertTrue(all(p == "proj-beta" for p in projects_b))

    def test_global_only_search_strict_exclusion(self):
        """global_only=True must exclude all project memories even if query matches perfectly."""
        results = m.search(self.cx, "pipeline", project="proj-alpha", global_only=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["subject"], "common-ground-rule")
        self.assertEqual(results[0]["scope"], "global")
        self.assertIsNone(results[0]["project"])

        # Also when project=None
        results_none = m.search(self.cx, "pipeline", project=None)
        self.assertEqual(len(results_none), 1)
        self.assertEqual(results_none[0]["scope"], "global")

    def test_context_never_leaks_other_project_memories(self):
        """Context assembly for proj-alpha must never leak proj-beta records."""
        ctx_a = m.context(self.cx, project="proj-alpha", query="secret pipeline", budget=1000)
        self.assertIn("secret-algorithm-a", ctx_a["text"])
        self.assertNotIn("secret-algorithm-b", ctx_a["text"])
        self.assertNotIn("proj-beta", ctx_a["text"])

    def test_cross_project_link_forbidden(self):
        """Edges connecting memories from two different projects must raise MemoryError."""
        with self.assertRaises(m.MemoryError):
            m.link(self.cx, self.a1, "DEPENDS_ON", self.b1, project="proj-alpha")

        with self.assertRaises(m.MemoryError):
            m.link(self.cx, self.a1, "DEPENDS_ON", self.b1, project="proj-beta")

        with self.assertRaises(m.MemoryError):
            m.link(self.cx, self.b1, "RELATED_TO", self.a1, project=None)

    def test_cross_scope_link_forbidden(self):
        """Edges between a global memory and a project memory must be forbidden."""
        with self.assertRaises(m.MemoryError):
            m.link(self.cx, self.a1, "DEPENDS_ON", self.g1, project="proj-alpha")

        with self.assertRaises(m.MemoryError):
            m.link(self.cx, self.g1, "DEPENDS_ON", self.a1, project="proj-alpha")

    def test_graph_traversal_cross_project_isolation(self):
        """graph() initiated on another project's memory must be denied."""
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, self.b1, project="proj-alpha")

        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, self.a1, project="proj-beta")

        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, self.a1, project=None)

    def test_tampered_cross_project_edge_ignored_in_graph(self):
        """Even if a rogue edge exists in SQLite linking alpha->beta, graph traversal skips beta."""
        # Force a rogue cross-project edge into the edges table directly
        self.cx.execute("INSERT INTO edges VALUES (?,?,?)", (self.a1, "DEPENDS_ON", self.b1))
        self.cx.commit()

        # Traversing from alpha perspective must ignore the beta memory
        g = m.graph(self.cx, self.a1, project="proj-alpha", hops=2)
        node_ids = {n["id"] for n in g["nodes"]}
        self.assertIn(self.a1, node_ids)
        self.assertNotIn(self.b1, node_ids)
        self.assertEqual(g["edges"], [])

    def test_task_state_cross_project_reassignment_forbidden(self):
        """Tasks owned by proj-alpha cannot be overwritten or reassigned by proj-beta."""
        m.task_upsert(self.cx, "task-100", "proj-alpha", "Alpha task", "queued")

        # Proj-beta attempt to steal/reassign task-100
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx, "task-100", "proj-beta", "Hijacked task", "working")

        # Verify task is unchanged in SQLite
        row = self.cx.execute("SELECT * FROM task_state WHERE id='task-100'").fetchone()
        self.assertEqual(row["project"], "proj-alpha")
        self.assertEqual(row["title"], "Alpha task")
        self.assertEqual(row["status"], "queued")

    def test_graph_hop_boundaries_and_cycles(self):
        """Hops must be clamped to [0..3]; cycles and self-edges must be handled properly."""
        m1 = self.add_mem(project="proj-alpha", subject="cycle-1", content="node 1")
        m2 = self.add_mem(project="proj-alpha", subject="cycle-2", content="node 2")
        m3 = self.add_mem(project="proj-alpha", subject="cycle-3", content="node 3")

        # Hop boundary assertions
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, m1, project="proj-alpha", hops=-1)
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, m1, project="proj-alpha", hops=4)

        # hops = 0 returns only root and 0 edges
        g0 = m.graph(self.cx, m1, project="proj-alpha", hops=0)
        self.assertEqual(len(g0["nodes"]), 1)
        self.assertEqual(g0["edges"], [])

        # Link in a cycle: m1 -> m2 -> m3 -> m1
        m.link(self.cx, m1, "DEPENDS_ON", m2, "proj-alpha")
        m.link(self.cx, m2, "DEPENDS_ON", m3, "proj-alpha")
        m.link(self.cx, m3, "DEPENDS_ON", m1, "proj-alpha")

        # Traversal terminates cleanly without loop; deduplicates seen edges
        g_cycle = m.graph(self.cx, m1, project="proj-alpha", hops=3)
        self.assertEqual(len(g_cycle["nodes"]), 3)
        self.assertEqual(len(g_cycle["edges"]), 3)

        # Self-edge must be rejected
        with self.assertRaises(m.MemoryError):
            m.link(self.cx, m1, "DEPENDS_ON", m1, "proj-alpha")

        # Unsupported relationship rejected
        with self.assertRaises(m.MemoryError):
            m.link(self.cx, m1, "INVALID_RELATION", m2, "proj-alpha")


class TestMemoryStatusLifecycle(BaseMemoryTest):
    """Memory status lifecycle (active -> superseded -> retracted) exclusion rules."""

    def test_invalid_status_rejected_at_schema_and_api(self):
        """Invalid statuses such as archived, deleted, or random words must be rejected."""
        with self.assertRaises(m.MemoryError):
            self.add_mem(project="proj-life", status="archived")

        with self.assertRaises(m.MemoryError):
            self.add_mem(project="proj-life", status="deleted")

        with self.assertRaises(m.MemoryError):
            self.add_mem(project="proj-life", status="")

        # Direct SQL update with invalid status violates CHECK constraint
        mid = self.add_mem(project="proj-life", status="active")
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("UPDATE memories SET status='invalid_status' WHERE id=?", (mid,))

    def test_active_memory_search_and_graph(self):
        """Active memory is found in search and is valid as graph root and neighbor."""
        m1 = self.add_mem(project="proj-life", subject="active-node-1", content="First active element")
        m2 = self.add_mem(project="proj-life", subject="active-node-2", content="Second active element")
        m.link(self.cx, m1, "DEPENDS_ON", m2, "proj-life")

        hits = m.search(self.cx, "active element", project="proj-life")
        self.assertEqual(len(hits), 2)

        g = m.graph(self.cx, m1, project="proj-life")
        self.assertEqual(len(g["nodes"]), 2)
        self.assertEqual(len(g["edges"]), 1)

    def test_superseded_memory_excluded_from_search_and_graph(self):
        """Superseded memory must be excluded from search, context, and graph."""
        m1 = self.add_mem(project="proj-life", subject="root-active", content="active element")
        m2 = self.add_mem(project="proj-life", subject="target-superseded", content="active element to supersede")
        m.link(self.cx, m1, "SUPERSEDES", m2, "proj-life")

        # Mark m2 superseded
        self.cx.execute("UPDATE memories SET status='superseded' WHERE id=?", (m2,))
        self.cx.commit()

        # Search excludes superseded
        hits = m.search(self.cx, "supersede", project="proj-life")
        self.assertEqual(hits, [])

        # Graph starting at superseded node raises error
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, m2, project="proj-life")

        # Graph starting at active node does not include superseded neighbor or edge
        g = m.graph(self.cx, m1, project="proj-life")
        self.assertEqual([n["id"] for n in g["nodes"]], [m1])
        self.assertEqual(g["edges"], [])

    def test_retracted_memory_excluded_from_search_and_graph(self):
        """Retracted memory must be excluded from search, context, and graph."""
        m1 = self.add_mem(project="proj-life", subject="root-active", content="active element")
        m2 = self.add_mem(project="proj-life", subject="target-retracted", content="retracted element")
        m.link(self.cx, m1, "RELATED_TO", m2, "proj-life")

        # Mark m2 retracted
        self.cx.execute("UPDATE memories SET status='retracted' WHERE id=?", (m2,))
        self.cx.commit()

        # Search excludes retracted
        hits = m.search(self.cx, "retracted", project="proj-life")
        self.assertEqual(hits, [])

        # Graph root on retracted raises error
        with self.assertRaises(m.MemoryError):
            m.graph(self.cx, m2, project="proj-life")

        # Graph starting at active node excludes retracted neighbor
        g = m.graph(self.cx, m1, project="proj-life")
        self.assertEqual([n["id"] for n in g["nodes"]], [m1])
        self.assertEqual(g["edges"], [])

    def test_full_lifecycle_transitions_and_graph_traversal(self):
        """Active -> Superseded -> Retracted -> Active full transition cycle."""
        m1 = self.add_mem(project="proj-life", subject="head", content="lifecycle head element")
        m2 = self.add_mem(project="proj-life", subject="middle", content="lifecycle middle element")
        m3 = self.add_mem(project="proj-life", subject="tail", content="lifecycle tail element")

        m.link(self.cx, m1, "DEPENDS_ON", m2, "proj-life")
        m.link(self.cx, m2, "DEPENDS_ON", m3, "proj-life")

        # 1. Initially all 3 active: traversal returns all 3 nodes and 2 edges
        g1 = m.graph(self.cx, m1, project="proj-life", hops=2)
        self.assertEqual(len(g1["nodes"]), 3)
        self.assertEqual(len(g1["edges"]), 2)

        # 2. Middle becomes superseded: traversal from head stops at head (cannot traverse through middle)
        self.cx.execute("UPDATE memories SET status='superseded' WHERE id=?", (m2,))
        self.cx.commit()
        g2 = m.graph(self.cx, m1, project="proj-life", hops=2)
        self.assertEqual([n["id"] for n in g2["nodes"]], [m1])
        self.assertEqual(g2["edges"], [])

        # 3. Tail becomes retracted, middle restored to active: traversal returns head and middle
        self.cx.execute("UPDATE memories SET status='active' WHERE id=?", (m2,))
        self.cx.commit()
        self.cx.execute("UPDATE memories SET status='retracted' WHERE id=?", (m3,))
        self.cx.commit()
        g3 = m.graph(self.cx, m1, project="proj-life", hops=2)
        self.assertEqual({n["id"] for n in g3["nodes"]}, {m1, m2})
        self.assertEqual(len(g3["edges"]), 1)

        # 4. Tail restored to active: all 3 reachable again
        self.cx.execute("UPDATE memories SET status='active' WHERE id=?", (m3,))
        self.cx.commit()
        g4 = m.graph(self.cx, m1, project="proj-life", hops=2)
        self.assertEqual(len(g4["nodes"]), 3)
        self.assertEqual(len(g4["edges"]), 2)


class TestLimitAndBudgetClamping(BaseMemoryTest):
    """Limit clamping [1..50] for search and budget clamping [100..12000] for context."""

    def test_context_budget_boundaries(self):
        """Budget must be clamped strictly within [100..12000]."""
        # Underflow checks
        with self.assertRaises(m.MemoryError):
            m.context(self.cx, project="proj-clamp", query="test", budget=99)
        with self.assertRaises(m.MemoryError):
            m.context(self.cx, project="proj-clamp", query="test", budget=0)
        with self.assertRaises(m.MemoryError):
            m.context(self.cx, project="proj-clamp", query="test", budget=-100)

        # Overflow checks
        with self.assertRaises(m.MemoryError):
            m.context(self.cx, project="proj-clamp", query="test", budget=12001)
        with self.assertRaises(m.MemoryError):
            m.context(self.cx, project="proj-clamp", query="test", budget=50000)

        # Valid boundaries: 100, 101, 11999, 12000
        for valid_b in (100, 101, 11999, 12000):
            with self.subTest(budget=valid_b):
                res = m.context(self.cx, project="proj-clamp", query="test", budget=valid_b)
                self.assertEqual(res["budget_tokens"], valid_b)

    def test_context_budget_character_ceiling(self):
        """Context text length must not exceed budget * 4 characters."""
        for i in range(15):
            self.add_mem(
                project="proj-clamp",
                subject=f"item-{i}",
                content=f"payload-token-data-{i} " + ("x" * 200),
            )
        budget = 120
        ctx = m.context(self.cx, project="proj-clamp", query="payload-token-data", budget=budget)
        self.assertLessEqual(len(ctx["text"]), budget * 4)
        self.assertLessEqual(ctx["estimated_tokens_upper_bound"], budget)
        self.assertGreaterEqual(ctx["records"], 1)

    def test_search_limit_clamping_boundaries(self):
        """Search limit is clamped between 1 and 50."""
        # Insert 60 active records matching 'clamping'
        for i in range(60):
            self.add_mem(
                project="proj-clamp",
                subject=f"clamping-{i:02d}",
                content=f"Clamping test content item number {i:02d}",
            )

        # limit <= 0 clamped to 1
        self.assertEqual(len(m.search(self.cx, "clamping", project="proj-clamp", limit=-10)), 1)
        self.assertEqual(len(m.search(self.cx, "clamping", project="proj-clamp", limit=0)), 1)

        # Exact limits 1..50
        self.assertEqual(len(m.search(self.cx, "clamping", project="proj-clamp", limit=1)), 1)
        self.assertEqual(len(m.search(self.cx, "clamping", project="proj-clamp", limit=5)), 5)
        self.assertEqual(len(m.search(self.cx, "clamping", project="proj-clamp", limit=50)), 50)

        # limit > 50 clamped to 50
        self.assertEqual(len(m.search(self.cx, "clamping", project="proj-clamp", limit=51)), 50)
        self.assertEqual(len(m.search(self.cx, "clamping", project="proj-clamp", limit=100)), 50)
        self.assertEqual(len(m.search(self.cx, "clamping", project="proj-clamp", limit=9999)), 50)

    def test_memory_text_length_boundaries(self):
        """safe_text must enforce [1..12000] length boundaries."""
        # Empty string rejected
        with self.assertRaises(m.MemoryError):
            self.add_mem(project="proj-clamp", content="")

        # 1 character allowed
        self.add_mem(project="proj-clamp", subject="a", content="b", evidence="c")

        # 12000 characters allowed
        large_12k = "x" * 12000
        self.add_mem(project="proj-clamp", subject="12k", content=large_12k)

        # 12001 characters rejected
        too_large = "x" * 12001
        with self.assertRaises(m.MemoryError):
            self.add_mem(project="proj-clamp", subject="12k1", content=too_large)

    def test_scope_and_evidence_boundaries(self):
        """Scope/project mismatch and empty evidence must be rejected."""
        # Global with project
        with self.assertRaises(m.MemoryError):
            m.add(self.cx, scope="global", project="some-proj", type="fact",
                  subject="s", content="c", evidence="e")

        # Project without project
        with self.assertRaises(m.MemoryError):
            m.add(self.cx, scope="project", project=None, type="fact",
                  subject="s", content="c", evidence="e")

        # Invalid scope
        with self.assertRaises(m.MemoryError):
            m.add(self.cx, scope="user", project=None, type="fact",
                  subject="s", content="c", evidence="e")

        # Whitespace-only evidence
        with self.assertRaises(m.MemoryError):
            m.add(self.cx, scope="global", project=None, type="fact",
                  subject="s", content="c", evidence="   \t  \n  ")


class TestSecretRedactionComprehensive(BaseMemoryTest):
    """Secret redaction across all memory types and fields (PKCS#8, RSA, EC, OpenSSH, tokens)."""

    SECRET_PAYLOADS = [
        # PKCS#8 unencrypted
        ("pkcs8_unencrypted", "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC...\n-----END PRIVATE KEY-----"),
        # PKCS#8 encrypted
        ("pkcs8_encrypted", "-----BEGIN ENCRYPTED PRIVATE KEY-----\nMIICojAcBgoqhkiG9w0BDAEDMA4EC...\n-----END ENCRYPTED PRIVATE KEY-----"),
        # RSA (PKCS#1)
        ("rsa", "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0r1b...\n-----END RSA PRIVATE KEY-----"),
        # EC (SEC1)
        ("ec", "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEI...\n-----END EC PRIVATE KEY-----"),
        # OpenSSH
        ("openssh", "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAA...\n-----END OPENSSH PRIVATE KEY-----"),
        # DSA
        ("dsa", "-----BEGIN DSA PRIVATE KEY-----\nMIIBvAIBAAKCAQEA0r1b...\n-----END DSA PRIVATE KEY-----"),
        # PGP
        ("pgp", "-----BEGIN PGP PRIVATE KEY BLOCK-----\nVersion: BCPG\n...\n-----END PGP PRIVATE KEY BLOCK-----"),
        # API Keys
        ("api_key_colon", "api_key: abc123456789xyz"),
        ("api_key_equal", "api-key = secret_api_key_value"),
        ("apikey_assign", "apikey=AKIAIOSFODNN7EXAMPLE"),
        # Passwords
        ("password_colon", "password: CorrectHorseBatteryStaple"),
        ("password_equal", "password = SuperSecretPass123!"),
        # Secrets
        ("secret_colon", "secret: top_secret_vault_token"),
        ("secret_equal", "secret = highly_sensitive_key"),
        # Tokens
        ("token_colon", "token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"),
        ("token_equal", "token = bearer_session_auth_abc"),
        ("openai_sk_token", "sk-proj-1234567890abcdef1234567890"),
        ("github_pat_token", "ghp_1234567890abcdefghijklmnopqrstuvwxyz"),
    ]

    def test_all_private_key_formats_rejected(self):
        """Every private key format (PKCS#8, RSA, EC, OpenSSH, PGP) must be rejected."""
        for label, payload in self.SECRET_PAYLOADS:
            if "PRIVATE KEY" in payload:
                with self.subTest(label=label):
                    with self.assertRaises(m.MemoryError):
                        self.add_mem(project="proj-sec", content=payload)

    def test_token_and_api_key_formats_rejected(self):
        """Tokens, API keys, passwords, and secrets must be rejected."""
        for label, payload in self.SECRET_PAYLOADS:
            if "PRIVATE KEY" not in payload:
                with self.subTest(label=label):
                    with self.assertRaises(m.MemoryError):
                        self.add_mem(project="proj-sec", content=payload)

    def test_secrets_rejected_across_all_memory_types(self):
        """Verify credential refusal across all 8 ALLOWED_TYPES."""
        test_payload = "api_key = leak_test_12345"
        for mem_type in m.ALLOWED_TYPES:
            with self.subTest(mem_type=mem_type):
                with self.assertRaises(m.MemoryError):
                    self.add_mem(project="proj-sec", mem_type=mem_type, content=test_payload)

    def test_secrets_rejected_in_all_memory_fields(self):
        """Verify secret rejection in subject, content, and evidence fields of add()."""
        pkcs8 = "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"

        # Leak in subject
        with self.assertRaises(m.MemoryError):
            self.add_mem(project="proj-sec", subject=pkcs8, content="safe content", evidence="safe:evidence")

        # Leak in content
        with self.assertRaises(m.MemoryError):
            self.add_mem(project="proj-sec", subject="safe subject", content=pkcs8, evidence="safe:evidence")

        # Leak in evidence
        with self.assertRaises(m.MemoryError):
            self.add_mem(project="proj-sec", subject="safe subject", content="safe content", evidence=pkcs8)

    def test_secrets_rejected_in_task_state_fields(self):
        """Verify secret rejection in title, agent, and evidence fields of task_upsert()."""
        token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"

        # Leak in title
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx, "sec-task-1", "proj-sec", title=token, status="queued")

        # Leak in agent
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx, "sec-task-2", "proj-sec", title="Safe title",
                          status="queued", agent=token)

        # Leak in evidence
        with self.assertRaises(m.MemoryError):
            m.task_upsert(self.cx, "sec-task-3", "proj-sec", title="Safe title",
                          status="queued", evidence=token)


if __name__ == "__main__":
    unittest.main()
