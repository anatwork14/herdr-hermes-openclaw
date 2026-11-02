"""Evidence-based, namespace-scoped graph memory with SQLite FTS5.

Memory is written only by explicit add/link commands. No background transcript
capture, no automatic promotion from project to global scope.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections import deque
from pathlib import Path

ALLOWED_TYPES = {"decision", "constraint", "fact", "experiment", "lesson", "task", "preference", "procedure"}
RELATIONS = {"DEPENDS_ON", "BLOCKED_BY", "SUPERSEDES", "USES", "VERIFIED_BY", "RELATED_TO", "PRODUCED_BY"}
SECRET_PATTERNS = [re.compile(p, re.I) for p in [
    r"(?:api[_-]?key|password|secret|token)\s*[:=]\s*\S+",
    r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY(?: BLOCK)?-----",
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,})\b",
]]

class MemoryError(ValueError):
    pass

def connection(db: Path | str) -> sqlite3.Connection:
    path = Path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(path), timeout=15)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA foreign_keys=ON")
    cx.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY, scope TEXT NOT NULL, project TEXT,
          type TEXT NOT NULL, subject TEXT NOT NULL, content TEXT NOT NULL,
          evidence TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
          created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          CHECK (scope IN ('global','project')),
          CHECK ((scope='global' AND project IS NULL) OR (scope='project' AND project IS NOT NULL)),
          CHECK (status IN ('active','superseded','retracted'))
        );
        CREATE TABLE IF NOT EXISTS edges (
          source TEXT NOT NULL REFERENCES memories(id),
          relation TEXT NOT NULL,
          target TEXT NOT NULL REFERENCES memories(id),
          PRIMARY KEY(source,relation,target)
        );
        CREATE TABLE IF NOT EXISTS task_state (
          id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
          status TEXT NOT NULL, agent TEXT, evidence TEXT NOT NULL DEFAULT '',
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bindings (
          name TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
          pane_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
          cwd TEXT NOT NULL, created_at INTEGER NOT NULL
        );
    """)
    try:
        cx.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(subject, content, content='memories', content_rowid='rowid')")
        cx.executescript("""
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memories BEGIN
              INSERT INTO memory_fts(rowid, subject, content) VALUES (new.rowid, new.subject, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memories BEGIN
              INSERT INTO memory_fts(memory_fts,rowid,subject,content) VALUES('delete',old.rowid,old.subject,old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memories BEGIN
              INSERT INTO memory_fts(memory_fts,rowid,subject,content) VALUES('delete',old.rowid,old.subject,old.content);
              INSERT INTO memory_fts(rowid,subject,content) VALUES(new.rowid,new.subject,new.content);
            END;
        """)
    except sqlite3.OperationalError as exc:
        raise MemoryError("SQLite FTS5 support is required") from exc
    cx.commit()
    return cx

def safe_text(s: str) -> str:
    if any(p.search(s) for p in SECRET_PATTERNS):
        raise MemoryError("Potential credential detected; refusing to persist")
    if not 0 < len(s) <= 12000:
        raise MemoryError("Memory content must be 1–12000 characters")
    return s

def add(cx: sqlite3.Connection, *, scope: str, project: str | None, type: str,
        subject: str, content: str, evidence: str, status: str = 'active') -> str:
    if scope not in ('global', 'project') or (scope == 'global') != (project is None):
        raise MemoryError("Scope/project mismatch")
    if type not in ALLOWED_TYPES:
        raise MemoryError("Unknown memory type")
    if not evidence.strip():
        raise MemoryError("Evidence pointer is required: file, commit, or user-confirmed decision")
    if status not in ('active', 'superseded', 'retracted'):
        raise MemoryError("Invalid status")
    safe_text(subject); safe_text(content); safe_text(evidence)
    now = int(time.time())
    mid = 'mem-' + uuid.uuid4().hex[:12]
    with cx:
        cx.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (mid, scope, project, type, subject, content, evidence, status, now, now))
    return mid

def accessible(row: sqlite3.Row, project: str | None, *, global_only=False) -> bool:
    if global_only:
        return row['scope'] == 'global'
    return row['scope'] == 'global' or (project is not None and row['scope'] == 'project' and row['project'] == project)

def search(cx: sqlite3.Connection, query: str, *, project: str | None, limit: int = 8,
           global_only: bool = False,
           status: str | list[str] | tuple[str, ...] | set[str] | None = 'active',
           include_retired: bool = False) -> list[dict]:
    # FTS tokenization rather than SQL concatenation; no SQL injection, punctuation-safe.
    tokens = re.findall(r"[\w-]+", query, re.UNICODE)
    if not tokens:
        return []
    fts = ' OR '.join('"' + t.replace('"', '') + '"' for t in tokens[:12])

    if include_retired or status in ('all', None):
        status_predicate = "1=1"
        status_params = []
    elif isinstance(status, (list, tuple, set)):
        valid_statuses = [s for s in status if s in ('active', 'superseded', 'retracted')]
        if not valid_statuses:
            return []
        placeholders = ','.join('?' for _ in valid_statuses)
        status_predicate = f"m.status IN ({placeholders})"
        status_params = list(valid_statuses)
    elif status in ('active', 'superseded', 'retracted'):
        status_predicate = "m.status = ?"
        status_params = [status]
    else:
        raise MemoryError(f"Invalid status: {status}")

    clamped_limit = max(1, min(limit, 50))
    # Filter scope in SQL before FTS rank/LIMIT. Python post-filtering can
    # exclude valid global records when project hits fill the top results.
    if global_only or project is None:
        scope_predicate = "m.scope='global'"
        sql = f"""SELECT m.*, bm25(memory_fts) AS rank FROM memory_fts
          JOIN memories m ON m.rowid = memory_fts.rowid
          WHERE memory_fts MATCH ? AND {status_predicate}
          AND {scope_predicate}
          ORDER BY rank LIMIT ?"""
        params = (fts, *status_params, clamped_limit)
    else:
        scope_predicate = "(m.scope='global' OR (m.scope='project' AND m.project=?))"
        sql = f"""SELECT m.*, bm25(memory_fts) AS rank FROM memory_fts
          JOIN memories m ON m.rowid = memory_fts.rowid
          WHERE memory_fts MATCH ? AND {status_predicate}
          AND {scope_predicate}
          ORDER BY rank LIMIT ?"""
        params = (fts, *status_params, project, clamped_limit)
    rows = cx.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

def set_status(cx: sqlite3.Connection, mid: str, status: str, *, project: str | None = None) -> None:
    if status not in ('active', 'superseded', 'retracted'):
        raise MemoryError(f"Invalid status: {status}")
    row = cx.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    if not row or not accessible(row, project):
        raise MemoryError("Memory is not accessible")
    if row['scope'] == 'global' and project is not None:
        raise MemoryError("Global status change requires global-only operator context")
    now = int(time.time())
    with cx:
        cx.execute("UPDATE memories SET status=?, updated_at=? WHERE id=?", (status, now, mid))

def retire(cx: sqlite3.Connection, mid: str, status: str, *, project: str | None = None) -> None:
    if status not in ('superseded', 'retracted'):
        raise MemoryError("Retire status must be 'superseded' or 'retracted'")
    set_status(cx, mid, status, project=project)

def link(cx: sqlite3.Connection, source: str, relation: str, target: str, project: str | None):
    if relation not in RELATIONS:
        raise MemoryError("Unsupported relationship")
    rows = cx.execute("SELECT * FROM memories WHERE id IN (?,?)", (source,target)).fetchall()
    if len(rows) != 2 or not all(accessible(r, project) for r in rows):
        raise MemoryError("Both memories must be visible within the same project boundary")
    if source == target:
        raise MemoryError("Self-edge is not allowed")
    # A global item must never gain a project-private relationship, preventing leakage.
    if any(r['scope'] == 'global' for r in rows) and any(r['scope'] == 'project' for r in rows):
        # Global -> project cross-scope relationships are purposely prohibited in MVP.
        raise MemoryError("Cross-scope edges are prohibited; copy a reference into project memory")
    if rows[0]['scope']=='project' and rows[0]['project'] != rows[1]['project']:
        raise MemoryError("Cross-project edges forbidden")
    with cx:
        cx.execute("INSERT OR IGNORE INTO edges VALUES (?,?,?)", (source,relation,target))

def graph(cx: sqlite3.Connection, start: str, *, project: str | None, hops: int = 2,
          status: str | list[str] | tuple[str, ...] | set[str] | None = 'active',
          include_retired: bool = False) -> dict:
    if hops < 0 or hops > 3:
        raise MemoryError("Traversal limited to 0–3 hops")
    if include_retired or status in ('all', None):
        allowed_statuses = {'active', 'superseded', 'retracted'}
    elif isinstance(status, (list, tuple, set)):
        allowed_statuses = set(status) & {'active', 'superseded', 'retracted'}
        if not allowed_statuses:
            raise MemoryError("No valid statuses specified")
    elif status in ('active', 'superseded', 'retracted'):
        allowed_statuses = {status}
    else:
        raise MemoryError(f"Invalid status: {status}")

    root = cx.execute("SELECT * FROM memories WHERE id=?", (start,)).fetchone()
    if not root or not accessible(root, project) or root['status'] not in allowed_statuses:
        raise MemoryError("Memory is not accessible or active")
    visited, q, edges = {start: dict(root)}, deque([(start, 0)]), []
    seen_edges = set()
    while q:
        node, depth = q.popleft()
        if depth >= hops:
            continue
        for edge in cx.execute("SELECT * FROM edges WHERE source=? OR target=?", (node, node)).fetchall():
            nxt = edge['target'] if edge['source'] == node else edge['source']
            row = cx.execute("SELECT * FROM memories WHERE id=?", (nxt,)).fetchone()
            if not row or not accessible(row, project) or row['status'] not in allowed_statuses:
                continue
            edge_key = (edge['source'], edge['relation'], edge['target'])
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append(dict(edge))
            if nxt not in visited:
                visited[nxt] = dict(row)
                q.append((nxt, depth + 1))
    return {'nodes': list(visited.values()), 'edges': edges}

def clamp_budget(budget: int, min_budget: int = 100, max_budget: int = 10000) -> int:
    return max(min_budget, min(budget, max_budget))

def context(cx: sqlite3.Connection, *, project: str, query: str, budget: int = 1600,
            limit: int = 20, clamp: bool = False) -> dict:
    if clamp:
        budget = clamp_budget(budget, 100, 10000)
    elif budget < 100 or budget > 12000:
        raise MemoryError("Context budget must be 100–12000 tokens")
    results = search(cx, query, project=project, limit=limit)
    # Approximate token bound; deliberately conservatively allocate 4 chars/token.
    max_chars = budget * 4
    lines = []
    used = 0
    for r in results:
        line = f"[{r['id']}] ({r['scope']}/{r['type']}) {r['subject']}: {r['content']} [evidence: {r['evidence']}]"
        if used + len(line) + 1 > max_chars:
            continue
        lines.append(line)
        used += len(line) + 1
    return {'text': '\n'.join(lines), 'records': len(lines), 'estimated_tokens_upper_bound': (used + 3) // 4,
            'budget_tokens': budget, 'query': query, 'project': project}

def task_upsert(cx: sqlite3.Connection, task_id: str, project: str, title: str, status: str,
                agent: str = '', evidence: str = '') -> None:
    if status not in {'queued', 'working', 'blocked', 'review', 'done', 'failed'}:
        raise MemoryError('Invalid task status')
    safe_text(task_id)
    safe_text(project)
    safe_text(title)
    if agent:
        safe_text(agent)
    if evidence:
        safe_text(evidence)
    with cx:
        cx.execute("""INSERT INTO task_state(id,project,title,status,agent,evidence,updated_at)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
          title=excluded.title,status=excluded.status,agent=excluded.agent,
          evidence=excluded.evidence,updated_at=excluded.updated_at
          WHERE task_state.project=excluded.project""", (task_id, project, title, status, agent, evidence, int(time.time())))
        row = cx.execute("SELECT project FROM task_state WHERE id=?", (task_id,)).fetchone()
        if not row or row['project'] != project:
            raise MemoryError('Task already belongs to another project')
