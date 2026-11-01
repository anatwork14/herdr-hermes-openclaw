"""Canonical shared skills metadata parsing, validation, and multi-agent discovery.

Supports Codex, Claude Code, Hermes Agent, and OpenClaw discovery formats.
"""
from __future__ import annotations
import re
from pathlib import Path

CANONICAL_SKILLS = (
    "git-worktree",
    "herdr-control",
    "memory-recall",
    "scientific-review",
    "task-handoff",
)

class SkillError(ValueError):
    """Raised when skill metadata or discovery fails validation."""
    pass

def parse_skill_markdown(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter and instruction body from SKILL.md text."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not match:
        raise SkillError("SKILL.md missing valid YAML frontmatter delimiters ('---')")
    frontmatter_raw, body = match.group(1), match.group(2).strip()
    metadata: dict[str, str] = {}
    for line in frontmatter_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        metadata[key.strip()] = val.strip().strip('"\'')

    if "name" not in metadata or not metadata["name"]:
        raise SkillError("Skill frontmatter missing required 'name'")
    if "description" not in metadata or not metadata["description"]:
        raise SkillError("Skill frontmatter missing required 'description'")
    if not body:
        raise SkillError("Skill instructions body cannot be empty")
    return metadata, body

def load_skill(skill_dir: Path | str) -> dict:
    """Load and validate a skill directory containing SKILL.md."""
    path = Path(skill_dir)
    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        raise SkillError(f"Missing SKILL.md in {path}")
    content = skill_md.read_text(encoding="utf-8")
    meta, body = parse_skill_markdown(content)
    if meta["name"] != path.name:
        raise SkillError(f"Skill name {meta['name']!r} does not match directory name {path.name!r}")
    return {
        "name": meta["name"],
        "description": meta["description"],
        "instructions": body,
        "dir": str(path.resolve()),
        "skill_md": str(skill_md.resolve()),
    }

def get_canonical_skills(shared_dir: Path | str | None = None) -> dict[str, dict]:
    """Scan and return all canonical shared skills."""
    if shared_dir is None:
        shared_dir = Path(__file__).resolve().parents[2] / "skills" / "shared"
    path = Path(shared_dir)
    if not path.is_dir():
        raise SkillError(f"Shared skills directory not found: {path}")

    found: dict[str, dict] = {}
    for item in sorted(path.iterdir()):
        if item.is_dir() and (item / "SKILL.md").is_file():
            skill_info = load_skill(item)
            found[skill_info["name"]] = skill_info

    missing = set(CANONICAL_SKILLS) - set(found.keys())
    if missing:
        raise SkillError(f"Missing canonical skills: {sorted(missing)}")
    return found

def discover_codex_skills(agents_dir: Path | str) -> dict[str, dict]:
    """Discover skills available to OpenAI Codex / CLI agents (~/.agents/skills)."""
    p = Path(agents_dir)
    if not p.is_dir():
        return {}
    skills: dict[str, dict] = {}
    for item in sorted(p.iterdir()):
        if item.is_dir() and (item / "SKILL.md").is_file():
            try:
                skills[item.name] = load_skill(item)
            except SkillError:
                continue
    return skills

def discover_claude_skills(claude_dir: Path | str) -> dict[str, dict]:
    """Discover skills available to Claude Code (~/.claude/skills)."""
    p = Path(claude_dir)
    if not p.is_dir():
        return {}
    skills: dict[str, dict] = {}
    for item in sorted(p.iterdir()):
        if item.is_dir() and (item / "SKILL.md").is_file():
            try:
                skills[item.name] = load_skill(item)
            except SkillError:
                continue
    return skills

def discover_hermes_skills(external_dirs: list[Path | str]) -> dict[str, dict]:
    """Discover skills configured via Hermes Agent skills.external_dirs."""
    skills: dict[str, dict] = {}
    for d in external_dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        for item in sorted(p.iterdir()):
            if item.is_dir() and (item / "SKILL.md").is_file():
                try:
                    skills[item.name] = load_skill(item)
                except SkillError:
                    continue
    return skills

def discover_openclaw_skills(extra_dirs: list[Path | str]) -> dict[str, dict]:
    """Discover skills configured via OpenClaw skills.load.extraDirs."""
    return discover_hermes_skills(extra_dirs)

def plan_symlinks(shared_dir: Path | str, target_roots: list[Path | str]) -> list[dict]:
    """Dry-run plan for installing canonical shared skills into agent root directories."""
    shared = Path(shared_dir)
    plan: list[dict] = []
    for root in target_roots:
        dest_root = Path(root)
        for name in sorted(CANONICAL_SKILLS):
            src = shared / name
            if not (src / "SKILL.md").is_file():
                continue
            target = dest_root / name
            exists = target.exists() or target.is_symlink()
            plan.append({
                "skill": name,
                "source": str(src),
                "target": str(target),
                "action": "SKIP" if exists else "LINK",
                "exists": exists,
            })
    return plan

def install_symlinks(shared_dir: Path | str, target_roots: list[Path | str],
                     apply: bool = False) -> list[dict]:
    """Install canonical shared skills as symbolic links without overwriting existing files."""
    plan = plan_symlinks(shared_dir, target_roots)
    results = []
    for item in plan:
        target = Path(item["target"])
        if item["action"] == "LINK" and apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(Path(item["source"]), target_is_directory=True)
            results.append({**item, "applied": True})
        else:
            results.append({**item, "applied": False})
    return results
