"""Stage 2-3: QualityFilter + DatasetSplitter 测试。

=== Stage 2: QualityFilter ===

  输入: CanonicalSession[] (所有提取的会话)
  输出: dict[str, list[CanonicalSession]] (按状态分组)

  过滤规则:
  1. 必须有 messages (非空)
  2. 必须有 task_input.raw_content (有任务描述)
  3. quality_score >= min_relevance_score (来自 sessions.jsonl)
  4. 按 execution.status 分组: success / failed / retry_success

=== Stage 3: DatasetSplitter ===

  输入: 分组后的 sessions
  输出: SamplingResult(evolution_set, test_set)

  分割逻辑:
  - 按状态分组后，每组独立按 evolution_ratio (70%) 分割
  - 只有1个会话的组 → 全部进 evolution_set
  - 使用固定 seed=42 保证可复现
"""
from __future__ import annotations

import pytest

from skill_evolution.processing.quality_filter import QualityFilter
from skill_evolution.processing.sampler import DatasetSplitter
from skill_evolution.config.settings import SamplingConfig
from skill_evolution.models.session import (
    CanonicalSession,
    ExecutionStatus,
    ExecutionTrace,
    TaskInput,
    Feedback,
    Message,
    MessageRole,
)


# ═══════════════════════════════════════════════════════════════════════════════
# QualityFilter (Stage 2)
# ═══════════════════════════════════════════════════════════════════════════════

class TestQualityFilter:
    """验证质量过滤和分组逻辑。"""

    def test_filter_passes_good_sessions(self, sample_sessions):
        """满足所有条件的会话应该通过过滤并按状态分组。

        sample_sessions 包含 3 个会话: 1 success, 1 failed, 1 retry_success
        min_relevance_score=0 时全部通过。
        """
        config = SamplingConfig(min_relevance_score=0)
        qf = QualityFilter(config)
        groups = qf.filter_and_classify(sample_sessions)

        assert len(groups["success"]) == 1
        assert len(groups["failed"]) == 1
        assert len(groups["retry_success"]) == 1

    def test_filter_rejects_low_score(self, sample_session):
        """quality_score < min_relevance_score 的会话应该被过滤掉。"""
        sample_session.feedback.quality_score = 2

        config = SamplingConfig(min_relevance_score=5)
        qf = QualityFilter(config)
        groups = qf.filter_and_classify([sample_session])

        # 所有组都应该为空
        assert len(groups["success"]) == 0
        assert len(groups["failed"]) == 0
        assert len(groups["retry_success"]) == 0

    def test_filter_rejects_empty_messages(self):
        """没有消息的会话应该被过滤掉。"""
        session = CanonicalSession()
        session.session_id = "empty"
        session.task_input = TaskInput(raw_content="some task")
        session.execution = ExecutionTrace(status=ExecutionStatus.SUCCESS)
        session.feedback = Feedback(quality_score=8)
        session.messages = []  # 空消息

        config = SamplingConfig(min_relevance_score=0)
        qf = QualityFilter(config)
        groups = qf.filter_and_classify([session])

        total = sum(len(v) for v in groups.values())
        assert total == 0

    def test_filter_classifies_failed(self, sample_sessions):
        """执行状态为 FAILED 的会话应该进入 failed 组。"""
        config = SamplingConfig(min_relevance_score=0)
        qf = QualityFilter(config)
        groups = qf.filter_and_classify(sample_sessions)

        failed_ids = [s.session_id for s in groups["failed"]]
        assert "test-session-002" in failed_ids

    def test_get_stats(self, sample_sessions):
        """get_stats() 应该返回正确的统计信息。"""
        config = SamplingConfig(min_relevance_score=0)
        qf = QualityFilter(config)
        groups = qf.filter_and_classify(sample_sessions)
        stats = qf.get_stats(groups)

        assert stats["total_passed"] == 3
        assert stats["groups"]["success"] == 1
        assert stats["groups"]["failed"] == 1
        assert stats["groups"]["retry_success"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# DatasetSplitter (Stage 3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDatasetSplitter:
    """验证数据集分割逻辑。"""

    def test_split_ratio(self, sample_sessions):
        """分割后 evolution_set 应该包含约 70% 的会话。

        sample_sessions 有 3 个会话 (每个状态 1 个)。
        每组只有 1 个会话 → 全部进 evolution_set (因为单会话组不拆分)。
        """
        config = SamplingConfig(min_relevance_score=0, evolution_ratio=0.7, test_ratio=0.3)
        splitter = DatasetSplitter(config)

        # 先过滤
        qf = QualityFilter(config)
        groups = qf.filter_and_classify(sample_sessions)

        result = splitter.split(groups)
        # 每个状态组只有 1 个会话，所以全部进 evolution_set
        assert len(result.evolution_set) == 3
        assert len(result.test_set) == 0

    def test_split_with_multiple_sessions(self):
        """多个会话时应该按比例分割。"""
        sessions = []
        for i in range(10):
            s = CanonicalSession()
            s.session_id = f"session-{i:03d}"
            s.task_input = TaskInput(raw_content=f"Task {i}")
            s.execution = ExecutionTrace(
                status=ExecutionStatus.SUCCESS,
                total_messages=2,
                total_tool_calls=1,
            )
            s.messages = [
                Message(role=MessageRole.USER, content_text=f"Task {i}"),
                Message(role=MessageRole.ASSISTANT, content_text="完成"),
            ]
            s.feedback = Feedback(quality_score=8)
            sessions.append(s)

        config = SamplingConfig(min_relevance_score=0, evolution_ratio=0.7, test_ratio=0.3)
        qf = QualityFilter(config)
        groups = qf.filter_and_classify(sessions)

        splitter = DatasetSplitter(config)
        result = splitter.split(groups)

        # 10 个会话, 70% → 7 evolution, 3 test
        assert len(result.evolution_set) == 7
        assert len(result.test_set) == 3

    def test_split_empty(self):
        """空输入应该返回空结果。"""
        config = SamplingConfig()
        splitter = DatasetSplitter(config)
        result = splitter.split({"success": [], "failed": [], "retry_success": []})

        assert len(result.evolution_set) == 0
        assert len(result.test_set) == 0
