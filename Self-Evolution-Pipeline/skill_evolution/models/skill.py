"""Skill version and evolution log models."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import json

from skill_evolution.models.evolution import EvolutionType


class DecisionType(str, Enum):
    APPROVE = "APPROVE"       # improvement >= threshold, auto-merge
    NEED_REVIEW = "NEED_REVIEW"  # improvement in grey zone, human review
    REJECT = "REJECT"         # improvement < 0 or negative


@dataclass
class SkillVersion:
    """Represents a version of a skill definition."""
    skill_name: str = ""
    version: str = "v1.0"
    content: str = ""  # the full SKILL.md content
    created_at: str = ""
    parent_version: Optional[str] = None
    evolution_type: Optional[EvolutionType] = None
    change_summary: str = ""
    is_active: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass
class DimensionScore:
    """A single evaluation dimension score."""
    name: str = ""
    weight: float = 0.0
    old_score: float = 0.0
    new_score: float = 0.0

    @property
    def improvement(self) -> float:
        return self.new_score - self.old_score

    @property
    def improvement_pct(self) -> float:
        if self.old_score == 0:
            return 0.0
        return (self.new_score - self.old_score) / self.old_score * 100


@dataclass
class EvaluationReport:
    """Evaluation report comparing old vs new skill versions."""
    skill_name: str = ""
    old_version: str = ""
    new_version: str = ""
    created_at: str = ""

    test_case_count: int = 0
    old_pass_count: int = 0
    new_pass_count: int = 0

    old_avg_score: float = 0.0
    new_avg_score: float = 0.0

    dimensions: list[DimensionScore] = field(default_factory=list)
    case_comparisons: list[dict] = field(default_factory=list)

    decision: DecisionType = DecisionType.REJECT
    decision_reason: str = ""
    risk_notes: list[str] = field(default_factory=list)

    @property
    def improvement_pct(self) -> float:
        if self.old_avg_score == 0:
            return 0.0
        return (self.new_avg_score - self.old_avg_score) / self.old_avg_score * 100

    def to_markdown(self) -> str:
        lines = [
            f"# Evaluation Report",
            f"",
            f"Generated: {self.created_at}",
            f"Versions: {self.old_version} vs {self.new_version}",
            f"",
            f"## Test Statistics",
            f"",
            f"| Metric | Old | New | Change |",
            f"|--------|-----|-----|--------|",
            f"| Test Cases | {self.test_case_count} | {self.test_case_count} | - |",
            f"| Passed | {self.old_pass_count} | {self.new_pass_count} | {self.new_pass_count - self.old_pass_count:+d} |",
            f"| Pass Rate | {self.old_pass_count/max(self.test_case_count,1)*100:.1f}% | {self.new_pass_count/max(self.test_case_count,1)*100:.1f}% | {(self.new_pass_count-self.old_pass_count)/max(self.test_case_count,1)*100:+.1f}% |",
            f"",
            f"## Overall Score",
            f"",
            f"- Old: {self.old_avg_score:.1f}",
            f"- New: {self.new_avg_score:.1f}",
            f"- Improvement: {self.improvement_pct:+.1f}%",
            f"",
            f"## Dimension Scores",
            f"",
            f"| Dimension | Weight | Old | New | Change |",
            f"|-----------|--------|-----|-----|--------|",
        ]
        for d in self.dimensions:
            lines.append(f"| {d.name} | {d.weight:.0%} | {d.old_score:.0f} | {d.new_score:.0f} | {d.improvement:+.0f} |")
        lines.extend([
            f"",
            f"## Decision",
            f"",
            f"**Decision**: {self.decision.value}",
            f"**Reason**: {self.decision_reason}",
        ])
        if self.risk_notes:
            lines.extend(["", "## Risk Notes"])
            for note in self.risk_notes:
                lines.append(f"- {note}")
        return "\n".join(lines)


@dataclass
class EvolutionLog:
    """Log of a single evolution attempt."""
    skill_name: str = ""
    evolution_type: EvolutionType = EvolutionType.FIX
    old_version: str = ""
    new_version: str = ""
    created_at: str = ""

    evidence_count: int = 0  # number of sessions used as evidence
    test_count: int = 0      # number of test cases

    report: Optional[EvaluationReport] = None
    decision: DecisionType = DecisionType.REJECT

    old_content_snapshot: str = ""  # backup of old skill content
    new_content: str = ""           # the evolved skill content
    diff: str = ""                  # unified diff

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.report:
            d["report"] = asdict(self.report)
        return d
