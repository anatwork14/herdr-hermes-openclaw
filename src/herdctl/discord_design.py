"""Read-only Discord layout and model-context budget design helpers.

Does not make Discord API calls, mutate OpenClaw, or enforce provider limits.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import ConfigError

_ID = re.compile(r"^[0-9]{15,22}$")


def _load(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot load policy {path}: {exc}") from exc
    if not isinstance(result, dict) or result.get("version") != 1:
        raise ConfigError(f"Invalid version in policy {path}")
    return result


def _policy_path(root: Path, stem: str) -> Path:
    private = root / "config" / f"{stem}.json"
    return private if private.is_file() else root / "config" / f"{stem}.example.json"


def budget(root: Path, role: str, followup: bool = False) -> dict:
    policy = _load(_policy_path(root, "token_policy"))
    roles = policy.get("roles", {})
    if role not in roles:
        raise ConfigError(f"Unknown role {role!r}; available roles: {', '.join(sorted(roles))}")
    value = roles[role]
    field = "followup_memory_tokens" if followup else "initial_memory_tokens"
    context = value.get(field)
    if not isinstance(context, int) or not 0 <= context <= 10000:
        raise ConfigError("Invalid memory token budget")
    return {
        "role": role,
        "stage": "followup" if followup else "initial",
        "memory_context_budget_estimate": context,
        "target_response_tokens_not_enforced": value["target_response_tokens"],
        "terminal_read_lines": value["terminal_read_lines"],
        "recommended_skills": value["skills"],
        "guidance": value["rule"],
        "note": "Memory CLI uses approximate character/token sizing; actual provider token telemetry must be measured separately."
    }


def discord_plan(root: Path, cfg: dict) -> dict:
    layout = _load(_policy_path(root, "discord_layout"))
    used: set[str] = set()
    missing = []

    def safe_id(value, label):
        if value in (None, ""):
            missing.append(label)
            return None
        val = str(value)
        if not _ID.fullmatch(val):
            raise ConfigError(f"Invalid Discord ID for {label}")
        if label.endswith("channel_id"):
            if val in used:
                raise ConfigError(f"Duplicate channel ID in Discord plan: {label}")
            used.add(val)
        return val

    guild = safe_id(layout.get("guild_id"), "guild_id")
    owner_ids = layout.get("owner_user_ids", [])
    if not isinstance(owner_ids, list):
        raise ConfigError("owner_user_ids must be an array")
    owners = [safe_id(v, f"owner_user_ids[{i}]") for i, v in enumerate(owner_ids)]
    if not owners:
        missing.append("owner_user_ids")
    controls = {}
    for name, entry in layout.get("control_channels", {}).items():
        field = f"control_channels.{name}.channel_id"
        resolved_id = safe_id(entry.get("channel_id"), field)
        if entry.get("optional") and resolved_id is None and field in missing:
            missing.remove(field)
        controls[name] = {
            "channel_id": resolved_id,
            "inbound": bool(entry.get("inbound", False)),
            "restricted": bool(entry.get("restricted", True)),
            "require_mention": bool(entry.get("require_mention", True)),
            "optional": bool(entry.get("optional", False))
        }
    projects = {}
    for name, entry in cfg["projects"].items():
        projects[name] = {
            "channel_name": name,
            "channel_id": safe_id(entry.get("discord_channel_id"), f"projects.{name}.discord_channel_id"),
            "require_mention": bool(layout["project_channel_defaults"].get("require_mention", True)),
            "task_threads": True,
            "session_rule": "Project channel and each task thread have distinct chat contexts; Herdr is the worker session owner."
        }
    return {
        "status": "READY_FOR_MANUAL_CONFIGURATION" if not missing else "MISSING_IDS",
        "bot_accounts": 1,
        "bot_credentials_required": ["DISCORD_BOT_TOKEN (store only in OpenClaw secret environment)"],
        "guild_id": guild,
        "owner_user_ids": owners,
        "control_channels": controls,
        "project_channels": projects,
        "missing_fields": missing,
        "permissions": layout["permissions"],
        "no_live_changes": True,
        "note": "A completed plan does not prove OpenClaw permissions, gateway routing, or Discord bot connectivity."
    }


def project_for_channel(cfg: dict, channel_id: str, parent_channel_id: str | None = None) -> dict:
    """Map a channel or thread to its configured parent project.

    Both identifiers MUST be supplied by a trusted Discord gateway adapter;
    this function cannot independently verify Discord thread ancestry.
    """
    if not _ID.fullmatch(str(channel_id)):
        raise ConfigError("Invalid channel/thread ID")
    if parent_channel_id is not None:
        if not _ID.fullmatch(str(parent_channel_id)):
            raise ConfigError("Invalid parent channel ID")
        if str(parent_channel_id) == str(channel_id):
            raise ConfigError("Do not supply a parent ID equal to channel ID")
    for project, entry in cfg["projects"].items():
        mapped = str(entry.get("discord_channel_id", ""))
        if mapped == channel_id:
            if parent_channel_id is not None:
                raise ConfigError("Parent ID supplied for an already configured project channel")
            return {"channel_id": channel_id, "project": project, "via": "project_channel"}
        if parent_channel_id is not None and mapped == parent_channel_id:
            return {"channel_id": channel_id, "parent_channel_id": parent_channel_id,
                    "project": project, "via": "trusted_thread_parent"}
    raise ConfigError("No exactly matching project channel/parent ID")
