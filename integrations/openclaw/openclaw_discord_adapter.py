"""OpenClaw Discord Inbound Adapter for herdctl Discord Gateway.

Integrates OpenClaw Discord messaging events with the hardened herdctl Discord Gateway.
Enforces:
1. Strict read-only command boundary: only 'status', 'agents', 'recall', 'tasks', 'budget' allowed.
   Explicitly rejects mutating commands ('prompt', 'launch', 'notify', shell execution).
2. Authoritative transport metadata normalization from OpenClaw Discord event payload.
3. Thread ancestry derived exclusively from authenticated transport metadata (parentId/parent_id).
4. Local authenticated execution boundary using HERDCTL_GATEWAY_TOKEN environment secret.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple

ALLOWED_READ_ONLY_COMMANDS = frozenset({"status", "agents", "recall", "tasks", "budget"})
DISALLOWED_MUTATING_COMMANDS = frozenset({"prompt", "launch", "notify", "exec", "sh", "bash", "eval"})

SNOWFLAKE_RE = re.compile(r"^\d{17,21}$")


class OpenClawAdapterError(Exception):
    """Base error for OpenClaw Discord Adapter."""
    pass


class OpenClawSecurityError(OpenClawAdapterError):
    """Security or authentication violation in adapter."""
    pass


class OpenClawCommandError(OpenClawAdapterError):
    """Unsupported or prohibited command."""
    pass


def extract_event_metadata(event: dict) -> Dict[str, Any]:
    """Extract and validate authoritative Discord transport metadata from OpenClaw event.
    
    Supports both direct Discord event payloads and nested OpenClaw message structures.
    Rejects any unauthenticated or synthesized claims.
    """
    if not isinstance(event, dict):
        raise OpenClawSecurityError("OpenClaw event must be a dictionary")

    # Reject synthetic-mode escalation in inbound adapter
    if event.get("is_synthetic"):
        raise OpenClawSecurityError("Synthetic-mode activation is forbidden in OpenClaw inbound adapter")

    # Normalize nested message structure if wrapped by OpenClaw event schema
    msg = event.get("message") if isinstance(event.get("message"), dict) else event

    # 1. Message ID
    message_id = str(msg.get("id") or msg.get("message_id") or "").strip()
    if not message_id or not SNOWFLAKE_RE.match(message_id):
        raise OpenClawSecurityError(f"Invalid or missing Discord message_id: {message_id!r}")

    # 2. Guild ID
    guild_data = msg.get("guild")
    if isinstance(guild_data, dict):
        guild_id = str(guild_data.get("id") or "").strip()
    else:
        guild_id = str(msg.get("guild_id") or guild_data or "").strip()
    if not guild_id or not SNOWFLAKE_RE.match(guild_id):
        raise OpenClawSecurityError(f"Invalid or missing Discord guild_id: {guild_id!r}")

    # 3. Channel ID
    channel_data = msg.get("channel")
    if isinstance(channel_data, dict):
        channel_id = str(channel_data.get("id") or "").strip()
        raw_parent = channel_data.get("parentId") or channel_data.get("parent_id")
    else:
        channel_id = str(msg.get("channel_id") or channel_data or "").strip()
        raw_parent = msg.get("parent_channel_id") or msg.get("parentId")
    if not channel_id or not SNOWFLAKE_RE.match(channel_id):
        raise OpenClawSecurityError(f"Invalid or missing Discord channel_id: {channel_id!r}")

    # 4. Thread Parent ID (derived strictly from transport metadata)
    parent_channel_id: Optional[str] = None
    if raw_parent:
        p_str = str(raw_parent).strip()
        if p_str and SNOWFLAKE_RE.match(p_str):
            parent_channel_id = p_str
        elif p_str:
            raise OpenClawSecurityError(f"Malformed parent_channel_id: {p_str!r}")

    # 5. Author ID and bot status
    author_data = msg.get("author")
    if isinstance(author_data, dict):
        author_id = str(author_data.get("id") or "").strip()
        author_is_bot = bool(author_data.get("bot", False))
    else:
        author_id = str(msg.get("author_id") or "").strip()
        author_is_bot = bool(msg.get("author_is_bot", False))

    if not author_id or not SNOWFLAKE_RE.match(author_id):
        raise OpenClawSecurityError(f"Invalid or missing Discord author_id: {author_id!r}")

    if author_is_bot:
        raise OpenClawSecurityError("Bot messages are prohibited from driving gateway execution")

    # 6. Content
    content = str(msg.get("content") or "").strip()

    return {
        "message_id": message_id,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "parent_channel_id": parent_channel_id,
        "author_id": author_id,
        "author_is_bot": False,
        "content": content,
    }


def validate_read_only_command(content: str) -> Tuple[str, str]:
    """Parse and enforce that incoming command is strictly in the allowed read-only set.
    
    Rejects prompt, launch, notify, shell commands, and unrecognized verbs.
    """
    clean = content.strip()
    if clean.startswith("/"):
        clean = clean[1:].strip()

    tokens = clean.split(None, 1)
    verb = tokens[0].lower() if tokens else ""
    args = tokens[1].strip() if len(tokens) > 1 else ""

    if not verb:
        raise OpenClawCommandError("Empty command content")

    if verb in DISALLOWED_MUTATING_COMMANDS:
        raise OpenClawCommandError(
            f"Command '{verb}' is a mutating or execution operation and is strictly prohibited in P3-A Discord integration"
        )

    if verb not in ALLOWED_READ_ONLY_COMMANDS:
        raise OpenClawCommandError(
            f"Unsupported command '{verb}'. Allowed read-only commands: {', '.join(sorted(ALLOWED_READ_ONLY_COMMANDS))}"
        )

    return verb, args


def invoke_herdctl_gateway(
    metadata: Dict[str, Any],
    adapter_token: str,
    root_dir: Optional[Path] = None,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Invoke herdctl discord-gateway through local subprocess boundary using configured token.
    
    Ensures:
    - Token is passed via CLI flag or environment, verified server-side.
    - Zero shell interpolation (list-based subprocess args).
    - Hard timeout enforcement.
    """
    if not adapter_token or not adapter_token.strip():
        raise OpenClawSecurityError("Missing adapter authentication credential for herdctl gateway invocation")

    payload = {
        "message_id": metadata["message_id"],
        "guild_id": metadata["guild_id"],
        "channel_id": metadata["channel_id"],
        "parent_channel_id": metadata.get("parent_channel_id"),
        "author_id": metadata["author_id"],
        "author_is_bot": False,
        "content": metadata["content"],
    }

    cmd = [
        sys.executable,
        "-m",
        "herdctl",
        "discord-gateway",
        "--payload",
        json.dumps(payload),
        "--adapter-token",
        adapter_token.strip(),
    ]

    env = dict(os.environ)
    env["HERDCTL_GATEWAY_TOKEN"] = adapter_token.strip()

    cwd = str(root_dir) if root_dir else None

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpenClawAdapterError(f"Gateway execution timed out after {timeout_seconds}s") from exc
    except OSError as exc:
        raise OpenClawAdapterError(f"Failed to execute herdctl gateway subprocess: {exc}") from exc

    if proc.returncode != 0 and not proc.stdout.strip():
        err_msg = proc.stderr.strip() or f"Gateway exited with code {proc.returncode}"
        raise OpenClawAdapterError(f"Gateway execution failed: {err_msg}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise OpenClawAdapterError(
            f"Invalid JSON returned by gateway: {proc.stdout[:200]!r} (stderr: {proc.stderr[:200]!r})"
        ) from exc


def handle_openclaw_message(
    event: dict,
    adapter_token: Optional[str] = None,
    root_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Main entry point for handling inbound OpenClaw Discord message events.
    
    Validates event metadata, checks read-only whitelist, and invokes gateway.
    """
    token = adapter_token or os.environ.get("HERDCTL_GATEWAY_TOKEN") or os.environ.get("HERDCTL_GATEWAY_SECRET")
    if not token or not token.strip():
        raise OpenClawSecurityError("Adapter credential HERDCTL_GATEWAY_TOKEN not configured in server environment")

    metadata = extract_event_metadata(event)
    verb, _ = validate_read_only_command(metadata["content"])

    res = invoke_herdctl_gateway(
        metadata=metadata,
        adapter_token=token.strip(),
        root_dir=root_dir,
    )
    return res


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m integrations.openclaw.openclaw_discord_adapter '<event_json>'", file=sys.stderr)
        sys.exit(1)

    try:
        raw_event = json.loads(sys.argv[1])
        result = handle_openclaw_message(raw_event)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("ok") else 1)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(1)
