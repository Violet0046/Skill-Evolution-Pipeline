"""Tests for data models — session, evolution, proto_analysis."""
from __future__ import annotations

import json

from skill_evolution.models.session import (
    CanonicalSession,
    ExecutionStatus,
    ExecutionTrace,
    TaskInput,
    Feedback,
    TokenUsage,
    Message,
    MessageRole,
    ToolCall,
)
from skill_evolution.models.evolution import (
    EvolutionType,
    EvolutionSuggestion,
    SkillCategory,
)
from skill_evolution.models.proto_analysis import ProtoAnalysis
from skill_evolution.models.skill import (
    SkillVersion,
    EvaluationReport,
    DecisionType,
    DimensionScore,
)


class TestExecutionStatus:
    def test_enum_values(self):
        assert ExecutionStatus.SUCCESS.value == "success"
        assert ExecutionStatus.FAILED.value == "failed"
        assert ExecutionStatus.RETRY_SUCCESS.value == "retry_success"
        assert ExecutionStatus.UNKNOWN.value == "unknown"


class TestTokenUsage:
    def test_total(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.total == 150

    def test_defaults(self):
        usage = TokenUsage()
        assert usage.total == 0


class TestCanonicalSession:
    def test_summary(self, sample_session):
        summary = sample_session.summary()
        assert summary["session_id"] == "test-session-001"
        assert "task" in summary
        assert "execution" in summary
        assert "tool_calls" in summary
        assert summary["execution"]["status"] == "success"

    def test_to_json(self, sample_session):
        json_str = sample_session.to_json()
        data = json.loads(json_str)
        assert data["session_id"] == "test-session-001"

    def test_get_output_preview(self, sample_session):
        preview = sample_session._get_output_preview()
        assert "Analysis complete" in preview


class TestEvolutionSuggestion:
    def test_from_dict(self):
        data = {
            "type": "fix",
            "direction": "Fix error handling",
            "target_skills": ["skill-1"],
            "evidence_sessions": ["session-1"],
        }
        suggestion = EvolutionSuggestion.from_dict(data)
        assert suggestion.evolution_type == EvolutionType.FIX
        assert suggestion.direction == "Fix error handling"
        assert suggestion.target_skill_id == "skill-1"

    def test_to_dict(self, sample_evolution_suggestion):
        data = sample_evolution_suggestion.to_dict()
        assert data["type"] == "fix"
        assert "direction" in data

    def test_target_skill_id_empty(self):
        suggestion = EvolutionSuggestion(evolution_type=EvolutionType.FIX)
        assert suggestion.target_skill_id == ""


class TestProtoAnalysis:
    def test_to_brief(self, sample_proto_analysis):
        brief = sample_proto_analysis.to_brief()
        assert brief["sid"] == "test-session-"[:12]
        assert brief["status"] == "success"
        assert "tools" in brief


class TestSkillVersion:
    def test_to_dict(self):
        sv = SkillVersion(skill_name="test", version="v1.0", content="# Test")
        data = sv.to_dict()
        assert data["skill_name"] == "test"


class TestEvaluationReport:
    def test_improvement_pct(self):
        report = EvaluationReport(old_avg_score=80.0, new_avg_score=90.0)
        assert report.improvement_pct == 12.5

    def test_improvement_pct_zero_old(self):
        report = EvaluationReport(old_avg_score=0.0, new_avg_score=90.0)
        assert report.improvement_pct == 0.0


class TestDimensionScore:
    def test_improvement(self):
        ds = DimensionScore(name="quality", weight=0.5, old_score=80.0, new_score=90.0)
        assert ds.improvement == 10.0
        assert ds.improvement_pct == 12.5
