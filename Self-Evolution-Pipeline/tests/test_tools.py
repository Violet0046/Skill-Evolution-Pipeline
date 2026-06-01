"""LLM Tools 测试 — 验证 SessionToolRegistry 的工具处理函数。

=== 工具列表 ===

  1. read_session_summary(session_id)
     → 返回会话的紧凑摘要 (~2KB JSON)
     → LLM 首先调用此工具了解会话概况

  2. read_session_messages(session_id, start?, end?)
     → 返回会话的消息列表 (每条截断到 2000 字符)
     → LLM 用此工具查看详细的对话内容

  3. read_session_tool_detail(session_id, tool_name?)
     → 返回工具调用的详细信息 (输入、输出、耗时)
     → LLM 用此工具分析具体的工具使用情况

=== 工具注册 ===

  SessionToolRegistry 持有 session 数据，通过 get_handler(name) 返回处理函数。
  LLMWithTools 注册这些处理函数后，在 tool-use 对话循环中调用。
"""
from __future__ import annotations

import json

import pytest

from skill_evolution.llm.tools import SESSION_TOOLS, SessionToolRegistry
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
# SESSION_TOOLS 定义
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionToolsDefinition:
    """验证工具定义的结构。"""

    def test_tools_count(self):
        """应该定义 3 个工具。"""
        assert len(SESSION_TOOLS) == 3

    def test_tool_names(self):
        """工具名称应该是 read_session_summary, read_session_messages, read_session_tool_detail。"""
        names = {t["name"] for t in SESSION_TOOLS}
        assert names == {"read_session_summary", "read_session_messages", "read_session_tool_detail"}

    def test_tool_schema_has_required_fields(self):
        """每个工具定义应该有 name, description, input_schema。"""
        for tool in SESSION_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "session_id" in tool["input_schema"]["properties"]


# ═══════════════════════════════════════════════════════════════════════════════
# SessionToolRegistry
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionToolRegistry:
    """验证工具处理函数的正确性。"""

    def test_read_session_summary(self, sample_session):
        """read_session_summary 应该返回会话的 JSON 摘要。"""
        registry = SessionToolRegistry([sample_session])
        result = registry.read_session_summary("test-session-001")
        data = json.loads(result)

        assert data["session_id"] == "test-session-001"
        assert data["execution"]["status"] == "success"
        assert "task" in data
        assert "tool_calls" in data

    def test_read_session_summary_short_id(self, sample_session):
        """read_session_summary 应该支持 12 字符的短 ID。

        SessionToolRegistry 注册 session_id[:12] 作为快捷键。
        "test-session-001"[:12] = "test-session"
        """
        registry = SessionToolRegistry([sample_session])
        short_id = sample_session.session_id[:12]  # "test-session"
        result = registry.read_session_summary(short_id)
        data = json.loads(result)

        # 短 ID 能找到会话，返回的 summary 中 session_id 是完整的
        assert data["session_id"] == "test-session-001"

    def test_read_session_summary_not_found(self, sample_session):
        """找不到会话时应该返回错误信息。"""
        registry = SessionToolRegistry([sample_session])
        result = registry.read_session_summary("nonexistent")
        data = json.loads(result)

        assert "error" in data

    def test_read_session_messages(self, sample_session):
        """read_session_messages 应该返回消息列表。"""
        registry = SessionToolRegistry([sample_session])
        result = registry.read_session_messages("test-session-001")
        data = json.loads(result)

        assert len(data) == 4  # sample_session 有 4 条消息
        assert data[0]["role"] == "user"
        assert data[1]["role"] == "assistant"
        assert data[1]["tool_calls"][0]["tool"] == "Read"

    def test_read_session_messages_with_range(self, sample_session):
        """read_session_messages 应该支持 start/end 范围。"""
        registry = SessionToolRegistry([sample_session])
        result = registry.read_session_messages("test-session-001", start=1, end=3)
        data = json.loads(result)

        assert len(data) == 2  # index 1 和 2
        assert data[0]["index"] == 1

    def test_read_session_tool_detail(self, sample_session):
        """read_session_tool_detail 应该返回工具调用详情。"""
        registry = SessionToolRegistry([sample_session])
        result = registry.read_session_tool_detail("test-session-001")
        data = json.loads(result)

        assert len(data) == 1  # sample_session 有 1 个工具调用
        assert data[0]["tool"] == "Read"

    def test_read_session_tool_detail_filtered(self, sample_session):
        """read_session_tool_detail 应该支持按工具名过滤。"""
        registry = SessionToolRegistry([sample_session])
        result = registry.read_session_tool_detail("test-session-001", tool_name="Read")
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["tool"] == "Read"

        # 过滤不存在的工具应该返回空
        result = registry.read_session_tool_detail("test-session-001", tool_name="Write")
        data = json.loads(result)
        assert len(data) == 0

    def test_get_handler(self, sample_session):
        """get_handler() 应该返回对应的处理函数。"""
        registry = SessionToolRegistry([sample_session])

        handler = registry.get_handler("read_session_summary")
        assert handler is not None
        assert callable(handler)

        handler = registry.get_handler("nonexistent_tool")
        assert handler is None
