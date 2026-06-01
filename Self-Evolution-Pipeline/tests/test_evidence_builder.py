"""Stage 5: EvidenceBuilder 测试 — 验证 ProtoAnalysis[] → evidence text 转换。

=== 输入输出 ===

  输入: list[ProtoAnalysis] (Stage 4 的输出)
  输出: str (格式化的证据文本块, ~10KB for 20 sessions)

=== 输出结构 ===

  evidence_text 包含 4 个部分:
  1. ## 证据集概览 — skill name, 总会话数
  2. ## 执行状态分布 — success/failed/retry_success 的数量和百分比
  3. ## 会话证据明细 — 每个 session 的详细信息
  4. ## 聚合统计 — 总 token、平均 token、总耗时、使用的工具

=== 用途 ===

  evidence_text 作为 user prompt 的一部分发送给 Stage 6 的 LLM，
  LLM 据此分析执行模式并生成 evolution_suggestions。
"""
from __future__ import annotations

import pytest

from skill_evolution.llm.evidence_builder import EvidenceBuilder
from skill_evolution.models.proto_analysis import ProtoAnalysis


# ═══════════════════════════════════════════════════════════════════════════════
# 基本构建
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidenceBuilder:
    """验证证据文本的构建。"""

    def test_build_single_session(self, sample_proto_analysis):
        """单个 ProtoAnalysis 应该生成包含所有 4 个部分的证据文本。"""
        builder = EvidenceBuilder()
        text = builder.build([sample_proto_analysis], skill_name="test-skill")

        # 验证包含所有 4 个部分
        assert "## 证据集概览" in text
        assert "## 执行状态分布" in text
        assert "## 会话证据明细" in text
        assert "## 聚合统计" in text

        # 验证概览信息
        assert "test-skill" in text
        assert "总会话数: 1" in text

        # 验证状态分布
        assert "success: 1" in text

        # 验证会话明细 (session_id 被截断为 12 字符)
        assert "test-session" in text
        assert "Read→Bash→Write" in text

    def test_build_multiple_sessions(self):
        """多个 ProtoAnalysis 应该生成包含所有会话的证据文本。"""
        analyses = []
        for i in range(3):
            pa = ProtoAnalysis(
                session_id=f"session-{i:03d}",
                status="success" if i < 2 else "failed",
                task_title=f"Task {i}",
                task_description=f"Description {i}",
                tool_sequence="Read→Write",
                token_usage=1000 * (i + 1),
                duration_seconds=10.0 * (i + 1),
                message_count=4,
                tool_call_count=2,
                key_tools=["Read", "Write"],
            )
            analyses.append(pa)

        builder = EvidenceBuilder()
        text = builder.build(analyses, skill_name="multi-test")

        # 验证状态分布: 2 success, 1 failed
        assert "success: 2" in text
        assert "failed: 1" in text

        # 验证聚合统计
        assert "总 Token: 6,000" in text  # 1000 + 2000 + 3000

        # 验证所有会话都出现
        for i in range(3):
            assert f"session-{i:03d}" in text

    def test_build_empty(self):
        """空列表应该不崩溃，生成空的证据文本。"""
        builder = EvidenceBuilder()
        text = builder.build([], skill_name="empty")

        assert "总会话数: 0" in text

    def test_build_with_failure_info(self):
        """包含失败信息的 ProtoAnalysis 应该在证据文本中显示。"""
        pa = ProtoAnalysis(
            session_id="fail-session",
            status="failed",
            task_title="Failed task",
            task_description="This failed",
            tool_sequence="Bash",
            failure_reason="File not found",
            correction="Check file path first",
            error_tool_calls=["Bash: ls /nonexistent"],
            token_usage=500,
            message_count=2,
            tool_call_count=1,
            key_tools=["Bash"],
        )

        builder = EvidenceBuilder()
        text = builder.build([pa], skill_name="fail-test")

        assert "失败原因: File not found" in text
        assert "修正建议: Check file path first" in text
        assert "错误调用:" in text
        assert "Bash: ls /nonexistent" in text
