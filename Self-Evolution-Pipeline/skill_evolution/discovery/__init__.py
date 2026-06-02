"""Skill discovery module.

Automatically discovers skills from server directory structure.
"""
from skill_evolution.discovery.skill_discovery import (
    DiscoveryResult,
    DiscoveredSkill,
    discover_skills,
    format_discovery_summary,
)

__all__ = [
    "DiscoveryResult",
    "DiscoveredSkill",
    "discover_skills",
    "format_discovery_summary",
]