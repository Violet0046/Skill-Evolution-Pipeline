"""Tests for processing modules — QualityFilter, DatasetSplitter."""
from __future__ import annotations

from skill_evolution.processing.quality_filter import QualityFilter
from skill_evolution.processing.sampler import DatasetSplitter
from skill_evolution.config.settings import SamplingConfig
from skill_evolution.models.session import ExecutionStatus


class TestQualityFilter:
    def test_filter_passes_good_sessions(self, sample_session):
        config = SamplingConfig(min_relevance_score=5)
        qf = QualityFilter(config)
        groups = qf.filter_and_classify([sample_session])
        total = sum(len(v) for v in groups.values())
        assert total == 1
        assert len(groups["success"]) == 1

    def test_filter_rejects_low_score(self, sample_session):
        config = SamplingConfig(min_relevance_score=9)
        qf = QualityFilter(config)
        sample_session.feedback.quality_score = 3
        groups = qf.filter_and_classify([sample_session])
        total = sum(len(v) for v in groups.values())
        assert total == 0

    def test_filter_classifies_failed(self, sample_session):
        config = SamplingConfig(min_relevance_score=0)
        qf = QualityFilter(config)
        sample_session.execution.status = ExecutionStatus.FAILED
        groups = qf.filter_and_classify([sample_session])
        assert len(groups["failed"]) == 1


class TestDatasetSplitter:
    def test_split_ratio(self, sample_session):
        config = SamplingConfig(evolution_ratio=0.70, test_ratio=0.30)
        splitter = DatasetSplitter(config)

        # Create multiple sessions with different statuses
        from skill_evolution.models.session import CanonicalSession, ExecutionTrace
        sessions = []
        for i in range(10):
            s = CanonicalSession()
            s.session_id = f"session-{i:03d}"
            s.execution = ExecutionTrace(status=ExecutionStatus.SUCCESS)
            sessions.append(s)

        groups = {"success": sessions, "failed": [], "retry_success": []}
        result = splitter.split(groups)

        assert result.total_count == 10
        assert len(result.evolution_set) > 0
        assert len(result.test_set) > 0

    def test_split_empty(self):
        config = SamplingConfig()
        splitter = DatasetSplitter(config)
        result = splitter.split({"success": [], "failed": [], "retry_success": []})
        assert result.total_count == 0
