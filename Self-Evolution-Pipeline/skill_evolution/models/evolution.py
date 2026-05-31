"""Evolution data types — adapted from OpenSpace skill_engine/types.py.

Simplified for our pipeline: no SkillStore, SkillRegistry, or lineage tracking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EvolutionType(str, Enum):
    FIX = "fix"           # Repair broken / outdated skill instructions
    DERIVED = "derived"   # Enhance / specialize an existing skill
    CAPTURED = "captured"  # Capture a novel reusable pattern (unused for now)


class SkillCategory(str, Enum):
    TOOL_GUIDE = "tool_guide"
    WORKFLOW = "workflow"
    REFERENCE = "reference"


@dataclass
class EvolutionSuggestion:
    """One evolution action suggested by the analysis LLM.

    - FIX: exactly 1 target skill (repair in-place)
    - DERIVED: 1+ target skills (enhance or merge)
    - CAPTURED: no target (brand-new skill, unused for now)
    """
    evolution_type: EvolutionType
    direction: str = ""                        # Free-text: what to evolve and why
    target_skill_ids: list[str] = field(default_factory=list)
    category: Optional[SkillCategory] = None
    evidence_sessions: list[str] = field(default_factory=list)  # session_ids that support this

    @property
    def target_skill_id(self) -> str:
        return self.target_skill_ids[0] if self.target_skill_ids else ""

    def to_dict(self) -> dict:
        return {
            "type": self.evolution_type.value,
            "direction": self.direction,
            "target_skills": self.target_skill_ids,
            "category": self.category.value if self.category else None,
            "evidence_sessions": self.evidence_sessions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EvolutionSuggestion:
        cat = None
        if data.get("category"):
            try:
                cat = SkillCategory(data["category"])
            except ValueError:
                pass
        return cls(
            evolution_type=EvolutionType(data["type"]),
            direction=data.get("direction", ""),
            target_skill_ids=data.get("target_skills", []),
            category=cat,
            evidence_sessions=data.get("evidence_sessions", []),
        )
