"""Stage 4: ProtoExtractor 测试 — 验证 CanonicalSession → ProtoAnalysis 转换。

=== 输入输出 ===

  输入: CanonicalSession (Stage 1 的输出)
  输出: ProtoAnalysis (~500B 轻量级结构化摘要)

=== 关键逻辑 ===

  1. _build_tool_sequence: 提取工具调用序列，去重连续相同工具
     例: Read→Read→Bash→Write → Read→Bash→Write
  2. _extract_key_tools: 提取去重的工具名列表
  3. _find_error_tool_calls: 找出返回错误的工具调用
  4. status, token_usage, duration 等直接从 session 复制
"""
from __future__ import annotations

import pytest

from skill_evolution.extraction.proto_extractor import ProtoExtractor
from skill_evolution.models.session import (
    CanonicalSession,
    ExecutionStatus,
    ExecutionTrace,
    TaskInput,
    Feedback,
    Message,
    MessageRole,
    ToolCall,
    TokenUsage,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 基本提取
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtoExtractor:
    """验证从 CanonicalSession 提取 ProtoAnalysis。"""

    def test_extract_basic(self, sample_session):
        """从 sample_session 提取 ProtoAnalysis，验证所有字段。

        ProtoAnalysis 是纯代码提取 (不涉及 LLM)，约 500 字节。
        """
        extractor = ProtoExtractor()
        pa = extractor.extract(sample_session)

        assert pa.session_id == "test-session-001"
        assert pa.status == "success"
        assert pa.task_title == "Test requirement"
        assert pa.token_usage == 1500  # 1000 + 500
        assert pa.message_count == 4
        assert pa.quality_score == 8

    def test_tool_sequence_dedup(self):
        """连续相同的工具调用应该被去重。

        例: [Read, Read, Bash, Bash, Write] → "Read→Bash→Write"
        """
        session = CanonicalSession()
        session.session_id = "dedup-test"
        session.task_input = TaskInput(raw_content="test", task_description="test")
        session.execution = ExecutionTrace(
            status=ExecutionStatus.SUCCESS,
            total_messages=2,
            total_tool_calls=5,
            total_token_usage=TokenUsage(input_tokens=100, output_tokens=50),
        )
        session.messages = [
            Message(
                role=MessageRole.ASSISTANT,
                content_text="test",
                tool_calls=[
                    ToolCall(tool_name="Read", tool_use_id="t1", call_index=0),
                    ToolCall(tool_name="Read", tool_use_id="t2", call_index=1),
                    ToolCall(tool_name="Bash", tool_use_id="t3", call_index=2),
                    ToolCall(tool_name="Bash", tool_use_id="t4", call_index=3),
                    ToolCall(tool_name="Write", tool_use_id="t5", call_index=4),
                ],
            ),
            Message(role=MessageRole.ASSISTANT, content_text="done"),
        ]
        session.feedback = Feedback()

        extractor = ProtoExtractor()
        pa = extractor.extract(session)

        # 连续相同工具应该被去重
        assert pa.tool_sequence == "Read→Bash→Write"
        # key_tools 包含所有去重工具
        assert set(pa.key_tools) == {"Read", "Bash", "Write"}

    def test_error_tool_calls(self):
        """工具返回错误时应该被记录到 error_tool_calls。"""
        session = CanonicalSession()
        session.session_id = "error-test"
        session.task_input = TaskInput(raw_content="test", task_description="test")
        session.execution = ExecutionTrace(
            status=ExecutionStatus.FAILED,
            total_messages=3,
            total_tool_calls=1,
            total_token_usage=TokenUsage(input_tokens=100, output_tokens=50),
        )
        session.messages = [
            Message(
                role=MessageRole.ASSISTANT,
                content_text="trying...",
                tool_calls=[
                    ToolCall(
                        tool_name="Bash",
                        tool_use_id="tu-error",
                        call_index=0,
                        input_summary="rm -rf /nonexistent",
                    ),
                ],
            ),
            Message(
                role=MessageRole.TOOL,
                tool_results=[{"tool_use_id": "tu-error", "content": "Error: No such file or directory"}],
            ),
            Message(role=MessageRole.ASSISTANT, content_text="failed"),
        ]
        session.feedback = Feedback()

        extractor = ProtoExtractor()
        pa = extractor.extract(session)

        assert len(pa.error_tool_calls) == 1
        assert "Bash" in pa.error_tool_calls[0]

    def test_empty_session(self):
        """空会话应该返回空的 ProtoAnalysis。"""
        session = CanonicalSession()
        extractor = ProtoExtractor()
        pa = extractor.extract(session)

        assert pa.session_id == ""
        assert pa.status == "unknown"
        assert pa.tool_sequence == ""
        assert pa.token_usage == 0
