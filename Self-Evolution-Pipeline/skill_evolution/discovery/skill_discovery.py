"""Skill discovery module - automatically find skills from server directory.

Discovers skills from both agents/ and skills/ subdirectories,
each containing a sessions.jsonl file.

Directory structure:
    server_root/
    ├── agents/
    │   ├── 协议分析-agent/
    │   │   └── sessions.jsonl
    │   └── 主控板性能检查单-agent/
    │       └── sessions.jsonl
    └── skills/
        ├── 查询需求信息/
        │   └── sessions.jsonl
        └── 初始化/
            └── sessions.jsonl
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from skill_evolution.utils.logging import Logger

logger = Logger.get_logger(__name__)


@dataclass
class DiscoveredSkill:
    """Represents a discovered skill with its metadata."""
    name: str
    sessions_path: Path
    skill_md_path: Optional[Path] = None
    source_type: str = ""  # "agents" or "skills"
    root_dir: Optional[Path] = None
    source_skill_dir: Optional[Path] = None  # Source directory containing SKILL.md

    @property
    def exists(self) -> bool:
        """Check if the sessions file exists."""
        return self.sessions_path.exists()

    @property
    def has_skill_md(self) -> bool:
        """Check if SKILL.md exists."""
        return self.skill_md_path is not None and self.skill_md_path.exists()


@dataclass
class DiscoveryResult:
    """Result of skill discovery operation."""
    skills: Dict[str, DiscoveredSkill] = field(default_factory=dict)
    total_found: int = 0
    agents_count: int = 0
    skills_count: int = 0
    errors: List[str] = field(default_factory=list)

    def get_skill_names(self) -> List[str]:
        """Return list of discovered skill names."""
        return list(self.skills.keys())

    def filter_by_type(self, source_type: str) -> List[DiscoveredSkill]:
        """Filter skills by source type ('agents' or 'skills')."""
        return [s for s in self.skills.values() if s.source_type == source_type]


def discover_skills(server_root):
    """Discover all skills from server directory.

    Supports dual-path structure:
    - {server_root}/mapping/.../skills/{skill}/sessions.jsonl (mapping directory)
    - {server_root}/source/.../skills/{skill}/SKILL.md (source directory)

    Or single-path structure:
    - {server_root}/skills/{skill}/sessions.jsonl (and SKILL.md nearby)

    Args:
        server_root: Root directory containing agents/ and skills/ subdirectories
                     (e.g., /home/session_skill_mapping/mapping/.../系统方案设计)

    Returns:
        DiscoveryResult with all discovered skills
    """
    result = DiscoveryResult()
    root = Path(server_root)

    if not root.exists():
        result.errors.append(f"Server root does not exist: {server_root}")
        logger.error(f"Server root does not exist: {server_root}")
        return result

    logger.info(f"Discovering skills in: {root}")

    # Determine the mapping root (where sessions.jsonl live)
    # and the source root (where SKILL.md might live)
    mapping_root = root
    source_root = None

    # Try to find source root from mapping root
    # mapping structure: .../session_skill_mapping/mapping/.../系统方案设计
    # source structure:  .../session_skill_mapping/source/.../系统方案设计
    if "/mapping/" in str(root):
        source_candidate = Path(str(root).replace("/mapping/", "/source/"))
        if source_candidate.exists():
            source_root = source_candidate

    # Discover from mapping structure (agents/ and skills/ with sessions.jsonl)
    mapping_agents = mapping_root / "agents"
    mapping_skills = mapping_root / "skills"

    if mapping_agents.exists():
        result.agents_count = _discover_from_directory(mapping_agents, "agents", result, source_root)

    if mapping_skills.exists():
        result.skills_count = _discover_from_directory(mapping_skills, "skills", result, source_root)

    result.total_found = len(result.skills)
    logger.info(f"Discovery complete: {result.total_found} skills found "
                f"(agents: {result.agents_count}, skills: {result.skills_count})")

    return result


def _discover_from_directory(
    base_dir,
    source_type,
    result,
    source_root=None
):
    """Discover skills from a specific directory.

    Args:
        base_dir: Directory to scan (e.g., agents/ or skills/)
        source_type: Type identifier ("agents" or "skills")
        result: DiscoveryResult to update
        source_root: Optional root directory where SKILL.md might exist

    Returns:
        Number of skills discovered
    """
    count = 0

    try:
        for skill_dir in base_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_name = skill_dir.name
            sessions_path = skill_dir / "sessions.jsonl"

            # Skip if no sessions.jsonl
            if not sessions_path.exists():
                logger.debug(f"Skipping {skill_name}: no sessions.jsonl")
                continue

            # Look for SKILL.md in various locations
            # First check in the same directory
            skill_md_path = _find_skill_md(skill_dir, skill_name)
            source_skill_dir = None

            # If not found, try source_root
            if skill_md_path is None and source_root:
                source_skill_path = source_root / skill_name / "SKILL.md"
                if source_skill_path.exists():
                    skill_md_path = source_skill_path
                    source_skill_dir = source_skill_path.parent
                    logger.debug(f"Found SKILL.md in source: {source_skill_path}")

            skill = DiscoveredSkill(
                name=skill_name,
                sessions_path=sessions_path,
                skill_md_path=skill_md_path,
                source_type=source_type,
                root_dir=skill_dir,
                source_skill_dir=source_skill_dir,
            )

            # Handle duplicate names (prefer agents/ over skills/)
            if skill_name in result.skills:
                existing = result.skills[skill_name]
                # Keep existing if it's from agents and new is from skills
                if existing.source_type == "agents" and source_type == "skills":
                    logger.debug(f"Skipping duplicate {skill_name} from {source_type}/ (keeping agents/)")
                    continue
                # Update SKILL.md if found in new location
                elif skill_md_path and not existing.has_skill_md:
                    existing.skill_md_path = skill_md_path
                    existing.source_skill_dir = source_skill_dir
                    logger.debug(f"Updated SKILL.md for {skill_name}")
                    continue

            result.skills[skill_name] = skill
            count += 1
            logger.debug(f"Discovered: [{source_type}] {skill_name}")

    except PermissionError as e:
        result.errors.append(f"Permission denied: {base_dir}")
        logger.error(f"Permission denied: {base_dir}")
    except Exception as e:
        result.errors.append(f"Error scanning {base_dir}: {e}")
        logger.error(f"Error scanning {base_dir}: {e}")

    return count


def _find_skill_md(skill_dir, skill_name):
    """Find SKILL.md in skill directory.

    Searches in order:
    1. {skill_dir}/SKILL.md
    2. {skill_dir}/.claude/skills/{skill_name}/SKILL.md
    3. {skill_dir}/.claude/skills/{skill_dir.name}/SKILL.md
    """
    # Direct SKILL.md
    direct = skill_dir / "SKILL.md"
    if direct.exists():
        return direct

    # In .claude/skills/{skill_name}/
    claude_skills = skill_dir / ".claude" / "skills" / skill_name / "SKILL.md"
    if claude_skills.exists():
        return claude_skills

    # In .claude/skills/{dir_name}/
    claude_dir = skill_dir / ".claude" / "skills" / skill_dir.name / "SKILL.md"
    if claude_dir.exists():
        return claude_dir

    return None


def format_discovery_summary(result: DiscoveryResult, detailed: bool = False) -> str:
    """Format discovery result as a human-readable summary.

    Args:
        result: DiscoveryResult to format
        detailed: If True, show SKILL.md status for each skill
    """
    lines = [
        "=" * 60,
        f"Skill Discovery Summary: {result.total_found} skills found",
        "=" * 60,
    ]

    # Count skills with/without SKILL.md
    with_skill_md = sum(1 for s in result.skills.values() if s.has_skill_md)
    lines.append(f"\nSKILL.md: {with_skill_md}/{result.total_found} found")

    if result.agents_count > 0:
        lines.append(f"\n[agents/] ({result.agents_count} skills)")
        for skill in result.filter_by_type("agents"):
            if detailed:
                md_status = "📄" if skill.has_skill_md else "❌"
                lines.append(f"  {md_status} {skill.name}")
            else:
                status = "✓" if skill.exists else "✗"
                lines.append(f"  {status} {skill.name}")

    if result.skills_count > 0:
        lines.append(f"\n[skills/] ({result.skills_count} skills)")
        for skill in result.filter_by_type("skills"):
            if detailed:
                md_status = "📄" if skill.has_skill_md else "❌"
                lines.append(f"  {md_status} {skill.name}")
            else:
                status = "✓" if skill.exists else "✗"
                lines.append(f"  {status} {skill.name}")

    if result.errors:
        lines.append(f"\n[ERRORS] ({len(result.errors)})")
        for err in result.errors:
            lines.append(f"  ✗ {err}")

    lines.append("=" * 60)
    return "\n".join(lines)