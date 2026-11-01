"""Configuration and project boundary validation."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path

SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,50}$")
AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
KINDS = {"codex", "claude", "agy", "hermes", "gemini"}

class ConfigError(ValueError):
    pass

def load_config(path: str | Path | None = None) -> tuple[dict, Path]:
    p = Path(path or os.environ.get("HERDCTL_CONFIG", "config/projects.json")).expanduser().resolve()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not load {p}: {exc}") from exc
    if not isinstance(data.get("projects"), dict):
        raise ConfigError("Config must define a projects object")
    used_channels = set()
    for slug, project in data["projects"].items():
        validate_slug(slug)
        if not isinstance(project, dict) or not project.get("repo"):
            raise ConfigError(f"Project {slug} requires repo path")
        if project.get("discord_channel_id"):
            channel = str(project["discord_channel_id"])
            if not channel.isdigit():
                raise ConfigError(f"Invalid Discord channel ID in {slug}")
            if channel in used_channels:
                raise ConfigError(f"Duplicate Discord channel ID: {channel}")
            used_channels.add(channel)
    data.setdefault("state_dir", "./.state")
    data.setdefault("worktree_root", "./.worktrees")
    data.setdefault("default_memory_budget", 1600)
    data.setdefault("herdr_binary", "herdr")
    data.setdefault("openclaw_binary", "openclaw")
    return data, p.parent.parent if p.parent.name == "config" else p.parent

def validate_slug(slug: str) -> str:
    if not SLUG.fullmatch(slug):
        raise ConfigError(f"Invalid project ID: {slug}")
    return slug

def validate_agent(name: str) -> str:
    if not AGENT.fullmatch(name):
        raise ConfigError(f"Invalid agent ID: {name}")
    return name

def project_info(cfg: dict, project: str) -> dict:
    validate_slug(project)
    if project not in cfg["projects"]:
        raise ConfigError(f"Unknown project: {project}")
    return cfg["projects"][project]

def resolve_path(root: Path, value: str | Path) -> Path:
    p = Path(value).expanduser()
    return (p if p.is_absolute() else root / p).resolve()
