"""Trusted Discord inbound gateway router and read-only request handlers.

Enforces strict metadata validation (guild allowlist, author allowlist, bot rejection),
authoritative channel/thread ancestry resolution, message replay deduplication,
and typed read-only handlers with hard project scoping boundaries.
No arbitrary shell execution or untrusted prompt mutation is permitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
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
    """Raised on security violation (unapproved guild, author, or bot message)."""
    pass


class GatewayRoutingError(GatewayError):
    """Raised when channel or thread ancestry resolution fails."""
    pass


class IdempotencyError(GatewayError):
    """Raised when duplicate message delivery is detected."""
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


def parse_inbound_message(data: Union[dict, DiscordInboundMessage]) -> DiscordInboundMessage:
    """Normalize raw payload dictionary or object into a typed DiscordInboundMessage."""
    if isinstance(data, DiscordInboundMessage):
        return data
    if not isinstance(data, dict):
        raise GatewaySecurityError("Inbound message payload must be a dict or DiscordInboundMessage")

    raw_author = data.get("author") or {}
    author_id = data.get("author_id") or (raw_author.get("id") if isinstance(raw_author, dict) else None)
    author_is_bot = bool(
        data.get("author_is_bot", False)
        or (raw_author.get("bot", False) if isinstance(raw_author, dict) else False)
    )

    parent_id = data.get("parent_channel_id")
    if parent_id is not None:
        parent_id = str(parent_id).strip() or None

    return DiscordInboundMessage(
        message_id=str(data.get("message_id") or data.get("id") or "").strip(),
        guild_id=str(data.get("guild_id") or "").strip(),
        channel_id=str(data.get("channel_id") or "").strip(),
        author_id=str(author_id or "").strip(),
        content=str(data.get("content") or "").strip(),
        parent_channel_id=parent_id,
        author_is_bot=author_is_bot,
        timestamp=int(data.get("timestamp")) if data.get("timestamp") is not None else None,
    )


class IdempotencyStore:
    """Tracks processed Discord message IDs in SQLite and memory to prevent replay attacks."""

    def __init__(self, cx: Optional[sqlite3.Connection] = None):
        self.cx = cx
        self._seen: Set[str] = set()
        if self.cx is not None:
            self._init_db()

    def _init_db(self) -> None:
        assert self.cx is not None
        with self.cx:
            self.cx.execute("""
                CREATE TABLE IF NOT EXISTS gateway_idempotency (
                    message_id TEXT PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)

    def is_duplicate(self, message_id: str) -> bool:
        if not message_id:
            return False
        if message_id in self._seen:
            return True
        if self.cx is not None:
            try:
                row = self.cx.execute(
                    "SELECT 1 FROM gateway_idempotency WHERE message_id = ?",
                    (message_id,)
                ).fetchone()
                if row:
                    self._seen.add(message_id)
                    return True
            except sqlite3.OperationalError:
                pass
        return False

    def check_and_record(
        self,
        message_id: str,
        guild_id: str,
        channel_id: str,
        author_id: str,
        command: str,
    ) -> None:
        if not message_id:
            raise IdempotencyError("Missing message ID for idempotency check")
        if self.is_duplicate(message_id):
            raise IdempotencyError(f"Duplicate message ID: {message_id} already processed")

        self._seen.add(message_id)
        if self.cx is not None:
            now = int(time.time())
            with self.cx:
                self.cx.execute(
                    "INSERT INTO gateway_idempotency VALUES (?,?,?,?,?,?)",
                    (message_id, guild_id, channel_id, author_id, command, now)
                )


def sanitize_text(text: str) -> str:
    """Redact known secret patterns and truncate excessive strings."""
    if not text:
        return ""
    sanitized = text
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    if len(sanitized) > 4000:
        sanitized = sanitized[:3950] + "\n... [TRUNCATED]"
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

    # Strip Discord bot mentions at the beginning (e.g. <@1556635278846992404> or <@!1556635278846992404>)
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


def validate_metadata(layout: dict, message: DiscordInboundMessage) -> None:
    """Validate inbound metadata against trusted guild allowlist and owner ID policy."""
    if message.author_is_bot:
        raise GatewaySecurityError("Bot messages are prohibited; refusing to process")

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
    agent_control_id = str(agent_control.get("channel_id") or "1566465715828031582").strip()

    if cid == agent_control_id:
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
    """Return gateway health, project mappings, and Herdr connection status."""
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
    for name, p_info in cfg.get("projects", {}).items():
        try:
            repo_path = resolve_path(root, p_info.get("repo", ""))
            repo_exists = repo_path.is_dir()
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
    """Format structured handler response into a compact, human-readable Discord message."""
    scope_tag = f"[{project}]" if project else f"[{scope.upper()}]"

    if command == "status":
        lines = [
            f"**Gateway Status** {scope_tag}",
            f"• Health: `{data.get('gateway_health')}`",
            f"• Guild ID: `{data.get('guild_id')}`",
            f"• Herdr Server: `{data.get('herdr_status')}`",
            "• Configured Projects:",
        ]
        for pname, pinfo in data.get("projects", {}).items():
            lines.append(f"  - `{pname}` (Channel: `{pinfo.get('channel_id')}`, Repo Exists: `{pinfo.get('repo_exists')}`)")
        return "\n".join(lines)

    if command == "agents":
        if not data:
            return f"{scope_tag} No registered agents found."
        lines = [f"**Registered Agents** {scope_tag} ({len(data)} total):"]
        for a in data:
            lines.append(f"• `{a.get('name')}` ({a.get('kind')}) in `{a.get('project')}` | Pane: `{a.get('pane_id')}`")
        return "\n".join(lines)

    if command == "recall":
        if not data:
            return f"{scope_tag} No memories matched query."
        lines = [f"**Memory Recall** {scope_tag} ({len(data)} results):"]
        for m in data:
            lines.append(f"• `[{m.get('id')}]` ({m.get('scope')}/{m.get('type')}) **{m.get('subject')}**: {m.get('content')}")
        return "\n".join(lines)

    if command == "tasks":
        if not data:
            return f"{scope_tag} No tasks found."
        lines = [f"**Tasks** {scope_tag} ({len(data)} total):"]
        for t in data:
            agent_str = f" [Agent: {t.get('agent')}]" if t.get("agent") else ""
            lines.append(f"• `[{t.get('id')}]` **{t.get('status').upper()}**: {t.get('title')}{agent_str}")
        return "\n".join(lines)

    if command == "budget":
        return (
            f"**Budget Policy: {data.get('role')}**\n"
            f"• Memory Context Limit: ~`{data.get('memory_context_budget_estimate')}` tokens\n"
            f"• Terminal Read Lines: `{data.get('terminal_read_lines')}`\n"
            f"• Recommended Skills: `{', '.join(data.get('recommended_skills', []))}`\n"
            f"• Guidance: _{data.get('guidance')}_"
        )

    return sanitize_text(json.dumps(data, indent=2))


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

        self.idempotency = IdempotencyStore(self.cx)

    def validate_metadata(self, message: DiscordInboundMessage) -> None:
        """Validate inbound Discord message metadata."""
        validate_metadata(self.layout, message)

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
            msg = parse_inbound_message(message)

            # Step 1: Validate trusted metadata (guild, author, bot check)
            self.validate_metadata(msg)

            # Step 2: Idempotency deduplication check
            if self.idempotency.is_duplicate(msg.message_id):
                raise IdempotencyError(f"Duplicate message ID: {msg.message_id} already processed")

            # Step 3: Authoritative channel and thread ancestry resolution
            routing = self.resolve_channel(msg.channel_id, msg.parent_channel_id)

            # Step 4: Parse typed read-only command
            cmd, args = self.parse_command(msg.content)

            # Step 5: Dispatch to scoped handler
            result_data = self.dispatch(cmd, args, routing["scope"], routing["project"])

            # Step 6: Record idempotency upon successful processing
            self.idempotency.check_and_record(
                msg.message_id,
                msg.guild_id,
                msg.channel_id,
                msg.author_id,
                cmd,
            )

            text_resp = format_discord_response(cmd, routing["scope"], routing["project"], result_data)

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
