"""Trusted Discord inbound gateway router and read-only request handlers.

Enforces strict metadata validation (guild allowlist, author allowlist, bot rejection),
trusted gateway adapter authentication boundary, authoritative channel/thread ancestry resolution,
durable SQLite request idempotency lifecycle (received -> processing -> completed/failed/uncertain),
and typed read-only handlers with hard project scoping boundaries.
No arbitrary shell execution, prompt mutation, or unscoped cross-project access is permitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .config import ConfigError, load_config, project_info, resolve_path
from .discord_design import budget, project_for_channel, _load, _policy_path
from .memory import MemoryError, SECRET_PATTERNS, connection, search
from .runtime import RuntimeError_, herdr

_SNOWFLAKE = re.compile(r"^[0-9]{15,22}$")
ALLOWED_COMMANDS = {"status", "agents", "recall", "tasks", "budget"}
PROHIBITED_COMMANDS = {
    "launch", "prompt", "notify", "set", "add", "retire", "link",
    "bash", "sh", "exec", "eval", "sudo", "cmd", "cat", "rm", "curl", "wget",
    "python", "python3", "perl", "ruby", "node", "git", "kill", "reboot",
}
VALID_ROLES = {"gateway", "supervisor", "implementer", "reviewer", "explorer"}


class GatewayError(Exception):
    """Base exception for Discord gateway errors."""
    pass


class GatewaySecurityError(GatewayError):
    """Raised on security violation (unapproved guild, author, bot message, or untrusted adapter)."""
    pass


class GatewayRoutingError(GatewayError):
    """Raised when channel or thread ancestry resolution fails."""
    pass


class IdempotencyError(GatewayError):
    """Raised when duplicate message delivery, concurrency collision, or storage failure occurs."""
    pass


class CommandError(GatewayError):
    """Raised when command is unrecognized, malformed, or prohibited."""
    pass


@dataclass
class DiscordInboundMessage:
    """Represents a validated inbound message payload from Discord gateway."""
    message_id: str
    guild_id: str
    channel_id: str
    author_id: str
    content: str = ""
    parent_channel_id: Optional[str] = None
    author_is_bot: bool = False
    timestamp: Optional[int] = None
    adapter_token: Optional[str] = None
    is_synthetic: bool = False


def parse_inbound_message(
    data: Union[dict, DiscordInboundMessage],
    is_synthetic: bool = False,
    adapter_token: Optional[str] = None,
) -> DiscordInboundMessage:
    """Normalize raw payload dictionary or object into a typed DiscordInboundMessage.

    In production ingress path (not is_synthetic), transport metadata must be explicitly provided;
    defaults are strictly prohibited.
    """
    if isinstance(data, DiscordInboundMessage):
        if adapter_token and not data.adapter_token:
            data.adapter_token = adapter_token
        if is_synthetic:
            data.is_synthetic = True
        return data
    if not isinstance(data, dict):
        raise GatewaySecurityError("Inbound message payload must be a dict or DiscordInboundMessage")

    syn = bool(is_synthetic or data.get("is_synthetic", False))
    tok = adapter_token or data.get("adapter_token")

    raw_author = data.get("author") or {}
    author_id = data.get("author_id") or (raw_author.get("id") if isinstance(raw_author, dict) else None)
    author_is_bot = bool(
        data.get("author_is_bot", False)
        or (raw_author.get("bot", False) if isinstance(raw_author, dict) else False)
    )

    parent_id = data.get("parent_channel_id")
    if parent_id is not None:
        parent_id = str(parent_id).strip() or None

    msg_id = str(data.get("message_id") or data.get("id") or "").strip()
    guild_id = str(data.get("guild_id") or "").strip()
    channel_id = str(data.get("channel_id") or "").strip()
    author_id_str = str(author_id or "").strip()

    # In production ingress path, no defaults are permitted; missing metadata is rejected
    if not syn:
        if not msg_id:
            raise GatewaySecurityError("Missing required transport metadata: 'message_id'; defaults prohibited in production ingress")
        if not guild_id:
            raise GatewaySecurityError("Missing required transport metadata: 'guild_id'; defaults prohibited in production ingress")
        if not channel_id:
            raise GatewaySecurityError("Missing required transport metadata: 'channel_id'; defaults prohibited in production ingress")
        if not author_id_str:
            raise GatewaySecurityError("Missing required transport metadata: 'author_id'; defaults prohibited in production ingress")
    else:
        # Synthetic test fixtures supply explicit synthetic defaults if omitted
        if not msg_id:
            msg_id = "1566470000000000001"
        if not guild_id:
            guild_id = "1556635278846992404"
        if not author_id_str:
            author_id_str = "734391788718129205"

    return DiscordInboundMessage(
        message_id=msg_id,
        guild_id=guild_id,
        channel_id=channel_id,
        author_id=author_id_str,
        content=str(data.get("content") or "").strip(),
        parent_channel_id=parent_id,
        author_is_bot=author_is_bot,
        timestamp=int(data.get("timestamp")) if data.get("timestamp") is not None else None,
        adapter_token=tok,
        is_synthetic=syn,
    )


class IdempotencyStore:
    """Durable request lifecycle store in SQLite and memory.

    Enforces states: received -> processing -> completed, with failed and uncertain states.
    Prevents concurrent handlers from processing the same request using SQLite immediate transactions.
    Supports crash recovery without automatic retry of ambiguous prompts.
    """

    def __init__(
        self,
        cx: Optional[sqlite3.Connection] = None,
        processing_timeout_seconds: float = 60.0,
    ):
        self.cx = cx
        self.processing_timeout_seconds = processing_timeout_seconds
        self._seen: Set[str] = set()
        if self.cx is not None:
            self._init_db()

    def _init_db(self) -> None:
        assert self.cx is not None
        try:
            with self.cx:
                self.cx.execute("""
                    CREATE TABLE IF NOT EXISTS gateway_idempotency (
                        message_id TEXT PRIMARY KEY,
                        guild_id TEXT NOT NULL,
                        channel_id TEXT NOT NULL,
                        author_id TEXT NOT NULL,
                        scope TEXT NOT NULL DEFAULT 'control',
                        project TEXT DEFAULT NULL,
                        command TEXT NOT NULL,
                        arguments TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'received',
                        response_json TEXT DEFAULT NULL,
                        error TEXT DEFAULT NULL,
                        agent TEXT DEFAULT NULL,
                        is_synthetic INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                """)
                # Handle schema migration if table existed with legacy columns
                cursor = self.cx.execute("PRAGMA table_info(gateway_idempotency)")
                existing_cols = {row[1] for row in cursor.fetchall()}
                migrations = [
                    ("scope", "TEXT NOT NULL DEFAULT 'control'"),
                    ("project", "TEXT DEFAULT NULL"),
                    ("arguments", "TEXT NOT NULL DEFAULT ''"),
                    ("status", "TEXT NOT NULL DEFAULT 'completed'"),
                    ("response_json", "TEXT DEFAULT NULL"),
                    ("error", "TEXT DEFAULT NULL"),
                    ("agent", "TEXT DEFAULT NULL"),
                    ("is_synthetic", "INTEGER NOT NULL DEFAULT 0"),
                    ("updated_at", "INTEGER NOT NULL DEFAULT 0"),
                ]
                for col_name, col_def in migrations:
                    if col_name not in existing_cols:
                        self.cx.execute(f"ALTER TABLE gateway_idempotency ADD COLUMN {col_name} {col_def}")

                self.cx.execute("""
                    CREATE INDEX IF NOT EXISTS idx_gateway_idempotency_proj_agent 
                    ON gateway_idempotency(project, agent, status)
                """)
                self.cx.execute("""
                    CREATE INDEX IF NOT EXISTS idx_gateway_idempotency_status_upd 
                    ON gateway_idempotency(status, updated_at)
                """)
        except sqlite3.Error as exc:
            raise IdempotencyError(f"Database error during idempotency schema initialization: {exc}") from exc

    def is_duplicate(self, message_id: str) -> bool:
        if not message_id:
            return False
        if message_id in self._seen:
            return True
        if self.cx is not None:
            try:
                row = self.cx.execute(
                    "SELECT status FROM gateway_idempotency WHERE message_id = ?",
                    (message_id,)
                ).fetchone()
                if row:
                    status = row[0]
                    if status in ("completed", "processing", "uncertain"):
                        self._seen.add(message_id)
                        return True
            except sqlite3.Error as exc:
                raise IdempotencyError(f"Database error checking duplicate status: {exc}") from exc
        return False

    def check_and_record(
        self,
        message_id: str,
        guild_id: str,
        channel_id: str,
        author_id: str,
        command: str,
        scope: str = "control",
        project: Optional[str] = None,
        arguments: str = "",
        status: str = "completed",
        response_data: Optional[Dict[str, Any]] = None,
        response_text: Optional[str] = None,
        agent: Optional[str] = None,
        is_synthetic: bool = False,
    ) -> None:
        if not message_id:
            raise IdempotencyError("Missing message ID for idempotency check")
        if self.is_duplicate(message_id):
            raise IdempotencyError(f"Duplicate message ID: {message_id} already processed")

        self._seen.add(message_id)
        if self.cx is not None:
            now = int(time.time())
            resp_json = json.dumps({"data": response_data, "text": response_text}, ensure_ascii=False) if response_data is not None else None
            try:
                with self.cx:
                    self.cx.execute(
                        """
                        INSERT INTO gateway_idempotency (
                            message_id, guild_id, channel_id, author_id, scope, project,
                            command, arguments, status, response_json, agent, is_synthetic,
                            created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            message_id, guild_id, channel_id, author_id, scope, project,
                            command, arguments, status, resp_json, agent, 1 if is_synthetic else 0,
                            now, now
                        )
                    )
            except sqlite3.Error as exc:
                raise IdempotencyError(f"Database error recording idempotency for {message_id}: {exc}") from exc

    def reserve_request(
        self,
        message_id: str,
        guild_id: str,
        channel_id: str,
        author_id: str,
        scope: str,
        project: Optional[str],
        command: str,
        arguments: str = "",
        agent: Optional[str] = None,
        is_synthetic: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """Atomically reserve message ID in SQLite before dispatch.

        Enforces lifecycle states: received -> processing -> completed.
        Prevents concurrent handlers from processing the same request.
        """
        if not message_id:
            raise IdempotencyError("Missing message ID for idempotency reservation")

        now = int(time.time())
        t_limit = timeout_seconds if timeout_seconds is not None else self.processing_timeout_seconds

        if self.cx is None:
            if message_id in self._seen:
                raise IdempotencyError(f"Duplicate message ID: {message_id} already processed")
            self._seen.add(message_id)
            return "processing", None

        try:
            self.cx.execute("BEGIN IMMEDIATE")
            row = self.cx.execute(
                "SELECT * FROM gateway_idempotency WHERE message_id = ?",
                (message_id,)
            ).fetchone()

            if row is None:
                self.cx.execute(
                    """
                    INSERT INTO gateway_idempotency (
                        message_id, guild_id, channel_id, author_id, scope, project,
                        command, arguments, status, agent, is_synthetic,
                        created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        message_id, guild_id, channel_id, author_id, scope, project,
                        command, arguments, "processing", agent, 1 if is_synthetic else 0,
                        now, now
                    )
                )
                self.cx.commit()
                self._seen.add(message_id)
                return "processing", None

            record = dict(row)
            status = record.get("status")
            updated_at = record.get("updated_at", record.get("created_at", 0))
            elapsed = now - updated_at

            if status == "completed":
                self.cx.commit()
                self._seen.add(message_id)
                return "completed", record

            if status == "processing":
                if elapsed > t_limit:
                    if command in ALLOWED_COMMANDS:
                        # Crash recovery for read-only handler
                        self.cx.execute(
                            "UPDATE gateway_idempotency SET status = 'processing', updated_at = ? WHERE message_id = ?",
                            (now, message_id)
                        )
                        self.cx.commit()
                        self._seen.add(message_id)
                        return "processing", None
                    else:
                        # Crash recovery for prompt or mutating task: mark uncertain, do NOT auto-retry!
                        self.cx.execute(
                            """
                            UPDATE gateway_idempotency 
                            SET status = 'uncertain', 
                                error = 'Ambiguous timeout during processing; potentially delivered to agent; automatic retry prohibited',
                                updated_at = ? 
                            WHERE message_id = ?
                            """,
                            (now, message_id)
                        )
                        self.cx.commit()
                        raise IdempotencyError(
                            f"Request {message_id} timed out while processing; marked UNCERTAIN; automatic retry prohibited"
                        )
                else:
                    self.cx.rollback()
                    raise IdempotencyError(f"Request {message_id} is currently processing by another worker")

            if status == "uncertain":
                self.cx.commit()
                raise IdempotencyError(f"Request {message_id} is in an UNCERTAIN state; automatic retry prohibited")

            if status == "failed":
                self.cx.commit()
                raise IdempotencyError(f"Request {message_id} previously failed: {record.get('error')}")

            self.cx.commit()
            return "processing", None

        except sqlite3.Error as exc:
            try:
                self.cx.rollback()
            except Exception:
                pass
            raise IdempotencyError(f"Database error during atomic request reservation: {exc}") from exc

    def complete_request(
        self,
        message_id: str,
        response_data: Any,
        response_text: str,
    ) -> None:
        """Mark request as completed and persist serialized response."""
        self._seen.add(message_id)
        if self.cx is not None:
            now = int(time.time())
            resp_json = json.dumps({"data": response_data, "text": response_text}, ensure_ascii=False)
            try:
                with self.cx:
                    self.cx.execute(
                        """
                        UPDATE gateway_idempotency 
                        SET status = 'completed', response_json = ?, updated_at = ?
                        WHERE message_id = ?
                        """,
                        (resp_json, now, message_id)
                    )
            except sqlite3.Error as exc:
                raise IdempotencyError(f"Database error completing request {message_id}: {exc}") from exc

    def fail_request(
        self,
        message_id: str,
        error_msg: str,
        is_uncertain: bool = False,
    ) -> None:
        """Mark request as failed or uncertain."""
        if self.cx is not None:
            now = int(time.time())
            st = "uncertain" if is_uncertain else "failed"
            try:
                with self.cx:
                    self.cx.execute(
                        """
                        UPDATE gateway_idempotency 
                        SET status = ?, error = ?, updated_at = ?
                        WHERE message_id = ?
                        """,
                        (st, error_msg, now, message_id)
                    )
            except sqlite3.Error as exc:
                raise IdempotencyError(f"Database error updating failure state for {message_id}: {exc}") from exc

    def check_agent_busy(
        self,
        project: str,
        agent: str,
        timeout_seconds: Optional[float] = None,
    ) -> Optional[str]:
        """Check if an agent currently has an active 'processing' request.
        Returns active message_id if busy, None if idle.
        """
        if self.cx is None:
            return None
        now = int(time.time())
        t_limit = timeout_seconds if timeout_seconds is not None else self.processing_timeout_seconds
        try:
            row = self.cx.execute(
                """
                SELECT message_id, updated_at FROM gateway_idempotency 
                WHERE project = ? AND agent = ? AND status = 'processing'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (project, agent)
            ).fetchone()
            if row:
                mid, upd = row[0], row[1]
                if (now - upd) <= t_limit:
                    return mid
            return None
        except sqlite3.Error as exc:
            raise IdempotencyError(f"Database error checking agent busy state: {exc}") from exc

    def list_agent_queue(
        self,
        project: str,
        agent: str,
    ) -> List[Dict[str, Any]]:
        """List active and pending requests for a specific agent in FIFO order."""
        if self.cx is None:
            return []
        try:
            rows = self.cx.execute(
                """
                SELECT * FROM gateway_idempotency 
                WHERE project = ? AND agent = ? AND status IN ('received', 'processing')
                ORDER BY created_at ASC
                """,
                (project, agent)
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            raise IdempotencyError(f"Database error listing agent queue: {exc}") from exc

    def get_record(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full idempotency record by message ID."""
        if self.cx is None:
            return None
        try:
            row = self.cx.execute(
                "SELECT * FROM gateway_idempotency WHERE message_id = ?",
                (message_id,)
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as exc:
            raise IdempotencyError(f"Database error reading record {message_id}: {exc}") from exc


def sanitize_text(text: str, max_length: int = 1950) -> str:
    """Redact known secret patterns and truncate excessive strings."""
    if not text:
        return ""
    sanitized = text
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length - 50] + "\n... [TRUNCATED]"
    return sanitized


def sanitize_error(exc: Exception) -> str:
    """Produce safe, redacted error string without raw tracebacks or secret tokens."""
    msg = str(exc)
    return sanitize_text(msg.splitlines()[0] if msg else "Unknown internal gateway error")


def parse_command(raw_content: str) -> Tuple[str, str]:
    """Parse typed Discord message content into (command, arguments).

    Supports bare commands ('status'), leading slashes ('/status'),
    exclamation marks ('!status'), and Discord bot mentions ('<@123...> status').
    """
    if not raw_content or not raw_content.strip():
        raise CommandError("Empty command content; allowed commands: status, agents, recall, tasks, budget")

    # Strip Discord bot mentions at the beginning
    cleaned = re.sub(r"^<@!?\d+>\s*", "", raw_content.strip())
    # Strip leading slash or exclamation mark
    cleaned = re.sub(r"^[!/]", "", cleaned).strip()

    if not cleaned:
        raise CommandError("Empty command; allowed commands: status, agents, recall, tasks, budget")

    parts = cleaned.split(None, 1)
    cmd = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    # Security check: detect prohibited shell or mutation commands
    if cmd in PROHIBITED_COMMANDS:
        raise CommandError(f"Command '{cmd}' is prohibited in read-only gateway")

    # Metacharacter check for injection attempts in command name
    if not re.fullmatch(r"[a-z0-9_-]+", cmd):
        raise CommandError("Invalid command identifier syntax")

    if cmd not in ALLOWED_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        raise CommandError(f"Unrecognized command '{cmd}'. Allowed read-only commands: {allowed}")

    # Argument validation against shell injection and path traversal
    if re.search(r"[;&|`$><]", args):
        raise CommandError("Shell metacharacters are prohibited in command arguments")
    if "../" in args or "..\\" in args:
        raise CommandError("Path traversal sequences are prohibited in command arguments")

    return cmd, args


def validate_metadata(
    layout: dict,
    message: DiscordInboundMessage,
    expected_adapter_token: Optional[str] = None,
) -> None:
    """Validate inbound metadata against trusted transport metadata, adapter boundary, and allowlists."""
    if message.author_is_bot:
        raise GatewaySecurityError("Bot messages are prohibited; refusing to process")

    # Adapter caller verification for production ingress
    if not message.is_synthetic and expected_adapter_token:
        if not message.adapter_token or message.adapter_token != expected_adapter_token:
            raise GatewaySecurityError("Untrusted gateway adapter caller; invalid or missing adapter authentication")

    if not message.message_id or not _SNOWFLAKE.fullmatch(message.message_id):
        raise GatewaySecurityError(f"Invalid or missing Discord message ID: {message.message_id!r}")

    # Validate Guild ID
    if not message.guild_id or not _SNOWFLAKE.fullmatch(message.guild_id):
        raise GatewaySecurityError(f"Invalid Discord guild ID format: {message.guild_id!r}")

    configured_guild = str(layout.get("guild_id") or "").strip()
    if not configured_guild:
        raise GatewaySecurityError("Discord layout missing required guild_id configuration")
    if message.guild_id != configured_guild:
        raise GatewaySecurityError(f"Unapproved guild ID: {message.guild_id}")

    # Validate Author ID against owner allowlist
    if not message.author_id or not _SNOWFLAKE.fullmatch(message.author_id):
        raise GatewaySecurityError(f"Invalid Discord author ID format: {message.author_id!r}")

    owners = {str(uid).strip() for uid in layout.get("owner_user_ids", []) if uid}
    if not owners:
        raise GatewaySecurityError("Discord layout missing owner_user_ids configuration")
    if message.author_id not in owners:
        raise GatewaySecurityError(f"Unapproved author ID: {message.author_id}")


def resolve_channel_ancestry(
    layout: dict,
    cfg: dict,
    channel_id: str,
    parent_channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve Discord channel or thread ancestry into authoritative scope and project.

    Rules:
    - If channel_id == control channel (#agent-control), resolve scope as 'control', project as None.
    - If channel_id matches a project channel, resolve project, via 'project_channel'.
    - If channel is a thread, ONLY resolve project if parent_channel_id matches project channel.
    - Reject forged parent_channel_id, equal parent/child IDs, or unmapped channels.
    """
    if not channel_id or not _SNOWFLAKE.fullmatch(str(channel_id)):
        raise GatewayRoutingError(f"Invalid Discord channel ID: {channel_id!r}")

    cid = str(channel_id)
    pid = str(parent_channel_id).strip() if parent_channel_id is not None else None

    if pid is not None:
        if not _SNOWFLAKE.fullmatch(pid):
            raise GatewayRoutingError(f"Invalid Discord parent channel ID: {pid!r}")
        if pid == cid:
            raise GatewayRoutingError("Equal parent channel ID and channel ID is invalid")

    # 1. Check control channels
    control_channels = layout.get("control_channels", {})
    agent_control = control_channels.get("agent-control", {})
    agent_control_id = str(agent_control.get("channel_id") or "").strip()

    if agent_control_id and cid == agent_control_id:
        if pid is not None:
            raise GatewayRoutingError("Parent ID supplied for a root control channel")
        if not agent_control.get("inbound", True):
            raise GatewayRoutingError("Control channel is not authorized for inbound requests")
        return {
            "scope": "control",
            "project": None,
            "channel_id": cid,
            "parent_channel_id": None,
            "via": "control_channel",
        }

    # Check other restricted control channels (approvals, alerts, system-status)
    for cname, cinfo in control_channels.items():
        if cname == "agent-control":
            continue
        mapped_cid = str(cinfo.get("channel_id") or "").strip()
        if mapped_cid and mapped_cid == cid:
            raise GatewayRoutingError(
                f"Channel {cid} (#{cname}) is outbound-only or restricted; inbound commands prohibited"
            )

    # 2. Check project channels
    projects = cfg.get("projects", {})
    for p_name, p_entry in projects.items():
        p_cid = str(p_entry.get("discord_channel_id") or "").strip()
        if not p_cid:
            continue
        # Direct project channel
        if p_cid == cid:
            if pid is not None:
                raise GatewayRoutingError("Parent ID supplied for an already configured project channel")
            return {
                "scope": "project",
                "project": p_name,
                "channel_id": cid,
                "parent_channel_id": None,
                "via": "project_channel",
            }
        # Trusted thread parent match
        if pid is not None and p_cid == pid:
            return {
                "scope": "project",
                "project": p_name,
                "channel_id": cid,
                "parent_channel_id": pid,
                "via": "trusted_thread_parent",
            }

    # 3. If parent_channel_id was supplied but did not match any project
    if pid is not None:
        raise GatewayRoutingError(
            f"Thread parent {pid} does not match any configured project channel"
        )

    # 4. Unmapped channel ID without parent
    raise GatewayRoutingError(
        f"Channel {cid} is not mapped to any control or project channel"
    )


def handle_status(
    cfg: dict,
    root: Path,
    cx: sqlite3.Connection,
    layout: dict,
    scope: str,
    project: Optional[str],
) -> Dict[str, Any]:
    """Return gateway health, project mappings, and Herdr connection status.
    Per-project status responses do not expose unrelated projects.
    """
    herdr_bin = cfg.get("herdr_binary", "herdr")
    herdr_found = bool(shutil.which(herdr_bin))
    herdr_status: Any = "offline"

    if herdr_found:
        try:
            res = herdr(cfg, ["status", "server"], timeout=5)
            if isinstance(res, dict):
                herdr_status = res.get("status") or res.get("result") or "online"
            else:
                herdr_status = str(res).strip()
        except Exception as exc:
            herdr_status = f"unavailable: {str(exc).splitlines()[0]}"
    else:
        herdr_status = f"binary '{herdr_bin}' not found on PATH"

    projects_summary = {}
    all_projects = cfg.get("projects", {})
    target_projects = {project: all_projects[project]} if (scope == "project" and project in all_projects) else all_projects

    for name, p_info in target_projects.items():
        try:
            repo_path = resolve_path(root, p_info.get("repo", ""))
            repo_exists = (repo_path / ".git").exists() or (repo_path / ".git").is_file()
        except Exception:
            repo_exists = False
        projects_summary[name] = {
            "channel_id": p_info.get("discord_channel_id"),
            "repo_exists": repo_exists,
        }

    return {
        "gateway_health": "ok",
        "scope": scope,
        "project": project,
        "guild_id": layout.get("guild_id"),
        "herdr_status": herdr_status,
        "projects": projects_summary,
    }


def handle_agents(
    cx: sqlite3.Connection,
    scope: str,
    project: Optional[str],
) -> List[Dict[str, Any]]:
    """List registered agents scoped to the resolved project, or all in #agent-control."""
    if scope == "control":
        rows = cx.execute(
            "SELECT name, project, kind, pane_id, workspace_id, cwd, created_at FROM bindings ORDER BY project, name"
        ).fetchall()
    else:
        assert project is not None
        rows = cx.execute(
            "SELECT name, project, kind, pane_id, workspace_id, cwd, created_at FROM bindings WHERE project=? ORDER BY name",
            (project,)
        ).fetchall()
    return [dict(r) for r in rows]


def handle_recall(
    cx: sqlite3.Connection,
    scope: str,
    project: Optional[str],
    query: str,
) -> List[Dict[str, Any]]:
    """Execute scoped memory search for the resolved project without cross-project leakage."""
    q = query.strip()
    if not q:
        raise CommandError("Query required for recall command")
    if len(q) > 500:
        raise CommandError("Query exceeds maximum allowed length of 500 characters")

    # In #agent-control (project is None), search global-only memory
    if scope == "control" or project is None:
        results = search(cx, q, project=None, global_only=True, limit=8)
    else:
        results = search(cx, q, project=project, limit=8)

    # Sanitize each memory record's contents to protect against accidental secret leakage
    sanitized_records = []
    for r in results:
        rec = dict(r)
        rec["subject"] = sanitize_text(rec.get("subject", ""))
        rec["content"] = sanitize_text(rec.get("content", ""))
        rec["evidence"] = sanitize_text(rec.get("evidence", ""))
        sanitized_records.append(rec)
    return sanitized_records


def handle_tasks(
    cx: sqlite3.Connection,
    scope: str,
    project: Optional[str],
) -> List[Dict[str, Any]]:
    """List tasks for the resolved project, or all tasks in #agent-control."""
    if scope == "control" or project is None:
        rows = cx.execute(
            "SELECT id, project, title, status, agent, evidence, updated_at FROM task_state ORDER BY project, updated_at DESC"
        ).fetchall()
    else:
        rows = cx.execute(
            "SELECT id, project, title, status, agent, evidence, updated_at FROM task_state WHERE project=? ORDER BY updated_at DESC",
            (project,)
        ).fetchall()

    tasks = []
    for r in rows:
        t = dict(r)
        t["title"] = sanitize_text(t.get("title", ""))
        t["evidence"] = sanitize_text(t.get("evidence", ""))
        tasks.append(t)
    return tasks


def handle_budget(
    root: Path,
    role: str,
) -> Dict[str, Any]:
    """Return model-context budget guidance for an agent role."""
    r = role.strip().lower()
    if not r:
        roles_list = ", ".join(sorted(VALID_ROLES))
        raise CommandError(f"Role required for budget command. Available roles: {roles_list}")
    if r not in VALID_ROLES:
        roles_list = ", ".join(sorted(VALID_ROLES))
        raise CommandError(f"Unknown role {r!r}; available roles: {roles_list}")

    try:
        return budget(root, r)
    except ConfigError as exc:
        raise CommandError(str(exc)) from exc


def format_discord_response(
    command: str,
    scope: str,
    project: Optional[str],
    data: Any,
) -> str:
    """Format structured handler response into a compact, human-readable Discord message.
    Ensures message is bounded to <= 2000 characters (Discord platform limit).
    """
    scope_tag = f"[{project}]" if project else f"[{scope.upper()}]"
    result = ""

    if command == "status":
        lines = [
            f"**Gateway Status** {scope_tag}",
            f"• Health: `{data.get('gateway_health')}`",
            f"• Guild ID: `{data.get('guild_id')}`",
            f"• Herdr Server: `{data.get('herdr_status')}`",
        ]
        if project:
            pinfo = data.get("projects", {}).get(project, {})
            lines.append(f"• Project `{project}`: Channel: `{pinfo.get('channel_id')}`, Repo Exists: `{pinfo.get('repo_exists')}`")
        else:
            lines.append("• Configured Projects:")
            for pname, pinfo in data.get("projects", {}).items():
                lines.append(f"  - `{pname}` (Channel: `{pinfo.get('channel_id')}`, Repo Exists: `{pinfo.get('repo_exists')}`)")
        result = "\n".join(lines)

    elif command == "agents":
        if not data:
            result = f"{scope_tag} No registered agents found."
        else:
            lines = [f"**Registered Agents** {scope_tag} ({len(data)} total):"]
            for a in data:
                lines.append(f"• `{a.get('name')}` ({a.get('kind')}) in `{a.get('project')}` | Pane: `{a.get('pane_id')}`")
            result = "\n".join(lines)

    elif command == "recall":
        if not data:
            result = f"{scope_tag} No memories matched query."
        else:
            lines = [f"**Memory Recall** {scope_tag} ({len(data)} results):"]
            for m in data:
                lines.append(f"• `[{m.get('id')}]` ({m.get('scope')}/{m.get('type')}) **{m.get('subject')}**: {m.get('content')}")
            result = "\n".join(lines)

    elif command == "tasks":
        if not data:
            result = f"{scope_tag} No tasks found."
        else:
            lines = [f"**Tasks** {scope_tag} ({len(data)} total):"]
            for t in data:
                agent_str = f" [Agent: {t.get('agent')}]" if t.get("agent") else ""
                lines.append(f"• `[{t.get('id')}]` **{t.get('status').upper()}**: {t.get('title')}{agent_str}")
            result = "\n".join(lines)

    elif command == "budget":
        result = (
            f"**Budget Policy: {data.get('role')}**\n"
            f"• Memory Context Limit: ~`{data.get('memory_context_budget_estimate')}` tokens\n"
            f"• Terminal Read Lines: `{data.get('terminal_read_lines')}`\n"
            f"• Recommended Skills: `{', '.join(data.get('recommended_skills', []))}`\n"
            f"• Guidance: _{data.get('guidance')}_"
        )
    else:
        result = sanitize_text(json.dumps(data, indent=2))

    # Strict Discord boundary: 2000 character hard limit
    if len(result) > 1950:
        result = result[:1900] + "\n... [TRUNCATED to 1950 chars due to Discord limit]"
    return result


class DiscordGateway:
    """Trusted Discord Inbound Gateway Router.

    Validates metadata, enforces channel/thread ancestry, prevents message replay,
    and dispatches typed read-only commands within strict project scoping boundaries.
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        cfg: Optional[dict] = None,
        layout: Optional[dict] = None,
        cx: Optional[sqlite3.Connection] = None,
        adapter_token: Optional[str] = None,
        processing_timeout_seconds: float = 60.0,
    ):
        if root is not None:
            self.root = Path(root).resolve()
        else:
            try:
                _, detected_root = load_config()
                self.root = detected_root
            except Exception:
                self.root = Path(__file__).resolve().parents[2]

        if cfg is not None:
            self.cfg = cfg
        else:
            try:
                self.cfg, _ = load_config()
            except Exception:
                self.cfg = {"projects": {}}

        if layout is not None:
            self.layout = layout
        else:
            try:
                self.layout = _load(_policy_path(self.root, "discord_layout"))
            except Exception:
                self.layout = {}

        if cx is not None:
            self.cx = cx
        else:
            state_dir = resolve_path(self.root, self.cfg.get("state_dir", "./.state"))
            db_path = state_dir / "memory.sqlite3"
            self.cx = connection(db_path)

        self.adapter_token = (
            adapter_token
            or os.environ.get("HERDCTL_GATEWAY_TOKEN")
            or self.layout.get("gateway_adapter", {}).get("auth_token")
            or self.layout.get("adapter_token")
        )
        self.idempotency = IdempotencyStore(self.cx, processing_timeout_seconds=processing_timeout_seconds)

    def validate_metadata(self, message: DiscordInboundMessage) -> None:
        """Validate inbound Discord message metadata."""
        validate_metadata(self.layout, message, expected_adapter_token=self.adapter_token)

    def resolve_channel(
        self,
        channel_id: str,
        parent_channel_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve channel or thread ancestry into verified scope and project."""
        return resolve_channel_ancestry(self.layout, self.cfg, channel_id, parent_channel_id)

    def parse_command(self, raw_content: str) -> Tuple[str, str]:
        """Parse raw Discord message text into command and argument strings."""
        return parse_command(raw_content)

    def dispatch(
        self,
        cmd: str,
        args: str,
        scope: str,
        project: Optional[str],
    ) -> Any:
        """Dispatch verified command to its read-only handler."""
        if cmd == "status":
            return handle_status(self.cfg, self.root, self.cx, self.layout, scope, project)
        if cmd == "agents":
            return handle_agents(self.cx, scope, project)
        if cmd == "recall":
            return handle_recall(self.cx, scope, project, args)
        if cmd == "tasks":
            return handle_tasks(self.cx, scope, project)
        if cmd == "budget":
            return handle_budget(self.root, args)
        raise CommandError(f"Unsupported command: {cmd}")

    def handle_message(
        self,
        message: Union[dict, DiscordInboundMessage],
        raise_on_error: bool = False,
        adapter_token: Optional[str] = None,
        is_synthetic: bool = False,
        replay_cached: bool = False,
    ) -> Dict[str, Any]:
        """Process an inbound Discord message end-to-end.

        Validates metadata, checks idempotency, resolves channel ancestry,
        parses command, dispatches to read-only handler, and records idempotency.
        Fails closed on any violation.
        """
        msg: Optional[DiscordInboundMessage] = None
        cmd = ""
        args = ""
        routing: Optional[Dict[str, Any]] = None

        try:
            tok = adapter_token or getattr(message, "adapter_token", None) or (message.get("adapter_token") if isinstance(message, dict) else None)
            syn = is_synthetic or getattr(message, "is_synthetic", False) or (bool(message.get("is_synthetic")) if isinstance(message, dict) else False)
            msg = parse_inbound_message(message, is_synthetic=syn, adapter_token=tok)

            # Step 1: Validate trusted metadata and adapter caller authentication
            self.validate_metadata(msg)

            # Step 2: Authoritative channel and thread ancestry resolution
            routing = self.resolve_channel(msg.channel_id, msg.parent_channel_id)

            # Step 3: Parse typed read-only command
            cmd, args = self.parse_command(msg.content)

            # Step 4: Atomic reservation before dispatch
            reservation_state, cached_record = self.idempotency.reserve_request(
                message_id=msg.message_id,
                guild_id=msg.guild_id,
                channel_id=msg.channel_id,
                author_id=msg.author_id,
                scope=routing["scope"],
                project=routing["project"],
                command=cmd,
                arguments=args,
                is_synthetic=msg.is_synthetic,
            )

            # Duplicate completed request handling
            if reservation_state == "completed":
                if replay_cached and cached_record and cached_record.get("response_json"):
                    cached_obj = json.loads(cached_record["response_json"])
                    return {
                        "ok": True,
                        "message_id": msg.message_id,
                        "scope": routing["scope"],
                        "project": routing["project"],
                        "channel_id": msg.channel_id,
                        "parent_channel_id": msg.parent_channel_id,
                        "command": cmd,
                        "data": cached_obj.get("data"),
                        "text": cached_obj.get("text"),
                        "error": None,
                        "replayed": True,
                    }
                raise IdempotencyError(f"Duplicate message ID: {msg.message_id} already processed")

            # Step 5: Dispatch to scoped handler
            result_data = self.dispatch(cmd, args, routing["scope"], routing["project"])

            text_resp = format_discord_response(cmd, routing["scope"], routing["project"], result_data)

            # Step 6: Mark request as completed in SQLite
            self.idempotency.complete_request(msg.message_id, result_data, text_resp)

            return {
                "ok": True,
                "message_id": msg.message_id,
                "scope": routing["scope"],
                "project": routing["project"],
                "channel_id": msg.channel_id,
                "parent_channel_id": msg.parent_channel_id,
                "command": cmd,
                "data": result_data,
                "text": text_resp,
                "error": None,
            }

        except Exception as exc:
            if msg and msg.message_id and not isinstance(exc, IdempotencyError):
                try:
                    self.idempotency.fail_request(msg.message_id, sanitize_error(exc))
                except Exception:
                    pass

            if raise_on_error:
                raise
            err_msg = sanitize_error(exc)
            return {
                "ok": False,
                "message_id": msg.message_id if msg else None,
                "scope": routing.get("scope") if routing else None,
                "project": routing.get("project") if routing else None,
                "channel_id": msg.channel_id if msg else None,
                "parent_channel_id": msg.parent_channel_id if msg else None,
                "command": cmd or None,
                "data": None,
                "text": f"Error: {err_msg}",
                "error": err_msg,
            }
