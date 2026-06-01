"""Stage 1: SessionExtractor 测试 — 验证 JSONL → CanonicalSession 转换。

=== 输入输出 ===

  输入: JSONL 文件路径 (每行一个 JSON 对象)
  输出: CanonicalSession 对象

=== JSONL 格式 ===

  第1行: type=user, 包含:
    - agentId, sessionId, timestamp, promptId
    - message.content: 需求文本 (含 需求ID、需求标题、任务描述)

  后续行: type=assistant, 包含:
    - message.content: [{type: "text", text: "..."}, {type: "tool_use", ...}]
    - message.usage: {input_tokens, output_tokens}

  可选行: type=user + isMeta=true, 包含:
    - skill-format 注入的 SKILL.md 内容

=== 测试策略 ===

  1. 端到端测试: extract_from_file() → 验证最终 CanonicalSession 所有字段
  2. 逐步测试: 单独调用每个私有方法，验证中间结果
     这样如果某一步出错，能精确定位到是哪个方法的问题

=== 逐步测试的数据流 ===

  原始 JSONL 记录 (list[dict])
       ↓  _extract_metadata()
  session.session_id, session.agent_id, session.timestamp, session.metadata
       ↓  _extract_task_input()
  session.task_input (requirement_id, requirement_title, task_description, raw_content)
       ↓  _extract_messages()
  session.messages (list[Message], 每个 Message 有 role, content_text, tool_calls)
       ↓  _compute_execution_trace()
  session.execution (status, total_messages, total_tool_calls, total_token_usage)
       ↓  _determine_status()
  session.execution.status (SUCCESS / FAILED / RETRY_SUCCESS / UNKNOWN)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill_evolution.extraction.session_extractor import SessionExtractor
from skill_evolution.models.session import (
    CanonicalSession, ExecutionStatus, MessageRole, TaskInput,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 基本提取
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionExtractor:
    """验证从 JSONL 文件提取会话的核心功能。"""

    def test_extract_from_file(self, jsonl_file):
        """从 JSONL 文件提取会话，应该正确解析所有字段。

        验证:
        - session_id 来自第1条记录的 sessionId
        - agent_id 来自第1条记录的 agentId
        - task_input.requirement_id 从消息内容中解析
        - messages 列表包含所有非 attachment 记录
        - execution.status 根据消息内容判断
        """
        extractor = SessionExtractor()
        session = extractor.extract_from_file(str(jsonl_file))

        # 元数据
        assert session.session_id == "test-session-001"
        assert session.agent_id == "test-agent"

        # 任务输入
        assert session.task_input.requirement_id == "REQ-001"
        assert session.task_input.requirement_title == "Test"

        # 消息
        assert len(session.messages) == 2
        assert session.messages[0].role == MessageRole.USER
        assert session.messages[1].role == MessageRole.ASSISTANT

    def test_extract_from_file_with_tool_use(self, tmp_dir):
        """包含工具调用的 JSONL 应该正确解析 tool_calls。

        模拟场景: assistant 调用 Read 工具，然后返回结果。
        """
        records = [
            {
                "type": "user",
                "agentId": "agent-1",
                "sessionId": "session-tool",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "需求ID：REQ-T\n需求标题：Tool test\n## 任务：Read file"},
            },
            {
                "type": "assistant",
                "uuid": "uuid-1",
                "timestamp": "2026-01-01T00:00:05Z",
                "message": {
                    "role": "assistant",
                    "model": "test-model",
                    "content": [
                        {"type": "text", "text": "I'll read the file."},
                        {"type": "tool_use", "id": "tu-001", "name": "Read", "input": {"file_path": "/tmp/test.md"}},
                    ],
                    "usage": {"input_tokens": 200, "output_tokens": 100},
                },
            },
            {
                "type": "user",
                "uuid": "uuid-2",
                "timestamp": "2026-01-01T00:00:06Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "tu-001", "content": "File content here"}],
                },
            },
            {
                "type": "assistant",
                "uuid": "uuid-3",
                "timestamp": "2026-01-01T00:00:10Z",
                "message": {
                    "role": "assistant",
                    "model": "test-model",
                    "content": [{"type": "text", "text": "File read complete. 完成"}],
                    "usage": {"input_tokens": 300, "output_tokens": 150},
                },
            },
        ]
        path = tmp_dir / "tool_session.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        extractor = SessionExtractor()
        session = extractor.extract_from_file(str(path))

        # 验证工具调用被正确提取
        assert session.execution.total_tool_calls == 1
        tool_call = session.messages[1].tool_calls[0]
        assert tool_call.tool_name == "Read"
        assert tool_call.tool_use_id == "tu-001"

        # 验证 token 使用量
        assert session.execution.total_token_usage.input_tokens == 500  # 200 + 300
        assert session.execution.total_token_usage.output_tokens == 250  # 100 + 150

    def test_determine_status_success(self, tmp_dir):
        """包含 "完成" 关键词的会话应该被标记为 SUCCESS。"""
        records = [
            {
                "type": "user",
                "agentId": "a",
                "sessionId": "s",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "Do something"},
            },
            {
                "type": "assistant",
                "uuid": "u1",
                "timestamp": "2026-01-01T00:00:05Z",
                "message": {
                    "role": "assistant",
                    "model": "m",
                    "content": [{"type": "text", "text": "任务完成"}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        ]
        path = tmp_dir / "success.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        extractor = SessionExtractor()
        session = extractor.extract_from_file(str(path))
        assert session.execution.status == ExecutionStatus.SUCCESS

    def test_determine_status_failed(self, tmp_dir):
        """包含 "error" 且没有成功关键词的会话应该被标记为 FAILED。"""
        records = [
            {
                "type": "user",
                "agentId": "a",
                "sessionId": "s",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "Do something"},
            },
            {
                "type": "assistant",
                "uuid": "u1",
                "timestamp": "2026-01-01T00:00:05Z",
                "message": {
                    "role": "assistant",
                    "model": "m",
                    "content": [{"type": "text", "text": "Error: file not found"}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        ]
        path = tmp_dir / "failed.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        extractor = SessionExtractor()
        session = extractor.extract_from_file(str(path))
        assert session.execution.status == ExecutionStatus.FAILED

    def test_empty_file(self, tmp_dir):
        """空文件应该返回空的 CanonicalSession。"""
        path = tmp_dir / "empty.jsonl"
        path.write_text("", encoding="utf-8")

        extractor = SessionExtractor()
        session = extractor.extract_from_file(str(path))
        assert session.session_id == ""
        assert len(session.messages) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 逐步验证 — 单独测试每个私有方法
# ═══════════════════════════════════════════════════════════════════════════════

# 测试用的原始 JSONL 记录 (模拟真实数据)
SAMPLE_RECORDS = [
    {
        "type": "user",
        "agentId": "agent-abc",
        "sessionId": "session-xyz-001",
        "timestamp": "2026-06-01T10:00:00Z",
        "promptId": "prompt-123",
        "message": {
            "role": "user",
            "content": "需求ID：REQ-42\n需求标题：协议解析\n需求类型：protocol\n## 任务：解析TCP握手协议",
        },
    },
    {
        "type": "assistant",
        "uuid": "uuid-1",
        "timestamp": "2026-06-01T10:00:05Z",
        "message": {
            "role": "assistant",
            "model": "mimo-v2.5-pro",
            "content": [
                {"type": "text", "text": "我来解析这个协议。"},
                {"type": "tool_use", "id": "tu-001", "name": "Read", "input": {"file_path": "/tmp/protocol.md"}},
            ],
            "usage": {"input_tokens": 500, "output_tokens": 200},
        },
    },
    {
        "type": "user",
        "uuid": "uuid-2",
        "timestamp": "2026-06-01T10:00:06Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-001", "content": "TCP协议内容..."}],
        },
    },
    {
        "type": "assistant",
        "uuid": "uuid-3",
        "timestamp": "2026-06-01T10:00:15Z",
        "message": {
            "role": "assistant",
            "model": "mimo-v2.5-pro",
            "content": [{"type": "text", "text": "解析完成，协议有效。"}],
            "usage": {"input_tokens": 800, "output_tokens": 300},
        },
    },
]


class TestExtractMetadata:
    """单独测试 _extract_metadata — 验证从第1条记录提取元数据。

    输入: SAMPLE_RECORDS (list[dict])
    输出: session 的 session_id, agent_id, timestamp, metadata

    这样如果 session_id 不对，我们知道是 _extract_metadata 的问题，
    而不是被后面的步骤覆盖了。
    """

    def test_metadata_extraction(self):
        """_extract_metadata 应该从第1条记录提取 sessionId, agentId, timestamp。"""
        extractor = SessionExtractor()
        session = CanonicalSession()
        extractor._extract_metadata(session, SAMPLE_RECORDS)

        # 验证: 这些值应该直接来自第1条记录的顶层字段
        assert session.session_id == "session-xyz-001"
        assert session.agent_id == "agent-abc"
        assert session.timestamp == "2026-06-01T10:00:00Z"

    def test_metadata_with_empty_records(self):
        """空记录列表会报 IndexError — extract_from_file 会先检查，这里验证预期行为。"""
        extractor = SessionExtractor()
        session = CanonicalSession()
        with pytest.raises(IndexError):
            extractor._extract_metadata(session, [])


class TestExtractTaskInput:
    """单独测试 _extract_task_input — 验证从消息内容解析任务信息。

    输入: SAMPLE_RECORDS (list[dict])
    输出: session.task_input 的各字段

    解析逻辑:
    - 需求ID: 正则 "需求ID[：:]\\s*(\\S+)" → "REQ-42"
    - 需求标题: 正则 "需求标题[：:]\\s*(.+)" → "协议解析"
    - 需求类型: 正则 "需求类型[：:]\\s*(\\S+)" → "protocol"
    - 任务描述: 正则 "(?:任务|##\\s*任务)[：:]\\s*(.+)" → "解析TCP握手协议"
    """

    def test_task_fields_extraction(self):
        """_extract_task_input 应该从消息内容解析出需求ID、标题、类型、任务。"""
        extractor = SessionExtractor()
        session = CanonicalSession()
        extractor._extract_task_input(session, SAMPLE_RECORDS)

        # 验证: 每个字段都是通过正则从 message.content 解析出来的
        assert session.task_input.requirement_id == "REQ-42"
        assert session.task_input.requirement_title == "协议解析"
        assert session.task_input.requirement_type == "protocol"
        assert session.task_input.task_description == "解析TCP握手协议"
        # raw_content 是完整的原始消息内容
        assert "REQ-42" in session.task_input.raw_content

    def test_task_fields_with_no_requirement(self):
        """没有需求ID的消息，requirement_id 应该为空字符串。"""
        records = [
            {
                "type": "user",
                "agentId": "a",
                "sessionId": "s",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "帮我写个函数"},
            },
        ]
        extractor = SessionExtractor()
        session = CanonicalSession()
        extractor._extract_task_input(session, records)

        # 没有 "需求ID：" 格式的内容，所以解析为空
        assert session.task_input.requirement_id == ""
        assert session.task_input.task_description == "帮我写个函数"


class TestExtractMessages:
    """单独测试 _extract_messages — 验证将 JSON 记录转为 Message 对象。

    输入: SAMPLE_RECORDS (list[dict])
    输出: session.messages (list[Message])

    转换规则:
    - type=user + role=user + content=string → Message(role=USER, content_text=...)
    - type=user + content=[{type: "tool_result"}] → Message(role=TOOL, tool_results=[...])
    - type=assistant + content=[{type: "text"}, {type: "tool_use"}] → Message(role=ASSISTANT, tool_calls=[...])
    """

    def test_messages_extraction(self):
        """_extract_messages 应该将 4 条记录转为 4 个 Message 对象。"""
        extractor = SessionExtractor()
        session = CanonicalSession()
        extractor._extract_messages(session, SAMPLE_RECORDS)

        # 4条记录 → 4个Message (user, assistant, user-with-tool-result, assistant)
        assert len(session.messages) == 4

        # 第1条: user 消息
        msg0 = session.messages[0]
        assert msg0.role == MessageRole.USER
        assert "REQ-42" in msg0.content_text

        # 第2条: assistant 消息，包含1个工具调用
        msg1 = session.messages[1]
        assert msg1.role == MessageRole.ASSISTANT
        assert len(msg1.tool_calls) == 1
        assert msg1.tool_calls[0].tool_name == "Read"
        assert msg1.tool_calls[0].tool_use_id == "tu-001"

        # 第3条: tool result 消息
        msg2 = session.messages[2]
        assert msg2.role == MessageRole.TOOL
        assert len(msg2.tool_results) == 1
        assert msg2.tool_results[0]["tool_use_id"] == "tu-001"

        # 第4条: assistant 最终回复
        msg3 = session.messages[3]
        assert msg3.role == MessageRole.ASSISTANT
        assert "解析完成" in msg3.content_text

    def test_messages_skip_attachment(self):
        """type=attachment 的记录应该被跳过。"""
        records = [
            {"type": "attachment", "data": "some file"},
            {
                "type": "user",
                "agentId": "a",
                "sessionId": "s",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "hello"},
            },
        ]
        extractor = SessionExtractor()
        session = CanonicalSession()
        extractor._extract_messages(session, records)

        # attachment 被跳过，只有1条 user 消息
        assert len(session.messages) == 1


class TestComputeExecutionTrace:
    """单独测试 _compute_execution_trace — 验证统计计算。

    前提: session.messages 已经被 _extract_messages 填充
    输出: session.execution 的统计字段

    计算逻辑:
    - total_messages: 消息数量
    - total_tool_calls: 所有 assistant 消息中的 tool_calls 总数
    - total_token_usage: 所有 assistant 消息的 usage 累加
    - models_used: 去重的模型名列表
    - duration_seconds: 第一条和最后一条消息的时间差
    """

    def test_execution_trace(self):
        """_compute_execution_trace 应该正确统计消息、工具、token、时长。"""
        extractor = SessionExtractor()
        session = CanonicalSession()

        # 先执行前置步骤
        extractor._extract_metadata(session, SAMPLE_RECORDS)
        extractor._extract_messages(session, SAMPLE_RECORDS)

        # 执行被测方法
        extractor._compute_execution_trace(session)

        # 验证统计结果
        assert session.execution.total_messages == 4  # 4条消息
        assert session.execution.total_tool_calls == 1  # 1次工具调用 (Read)
        assert session.execution.total_token_usage.input_tokens == 1300  # 500 + 800
        assert session.execution.total_token_usage.output_tokens == 500  # 200 + 300
        assert "mimo-v2.5-pro" in session.execution.models_used
        # 时长: 10:00:15 - 10:00:00 = 15秒
        assert session.execution.duration_seconds == 15.0
