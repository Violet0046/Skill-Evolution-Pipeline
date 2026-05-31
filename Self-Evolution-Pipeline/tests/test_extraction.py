"""Tests for extraction modules — SessionExtractor, ProtoExtractor."""
from __future__ import annotations

from pathlib import Path

from skill_evolution.extraction.session_extractor import SessionExtractor
from skill_evolution.extraction.proto_extractor import ProtoExtractor
from skill_evolution.models.session import ExecutionStatus


class TestSessionExtractor:
    def test_extract_from_file(self, jsonl_file: Path):
        extractor = SessionExtractor(max_content_length=200)
        session = extractor.extract_from_file(str(jsonl_file))

        assert session.session_id == "test-session-001"
        assert session.agent_id == "test-agent"
        assert len(session.messages) > 0
        assert session.task_input.raw_content != ""

    def test_determine_status_success(self):
        extractor = SessionExtractor()
        from skill_evolution.models.session import CanonicalSession, Message, MessageRole
        session = CanonicalSession()
        session.task_input.raw_content = "Do something"
        session.messages = [
            Message(role=MessageRole.ASSISTANT, content_text="任务完成，已生成结果"),
        ]
        status = extractor._determine_status(session)
        assert status == ExecutionStatus.SUCCESS

    def test_determine_status_failed(self):
        extractor = SessionExtractor()
        from skill_evolution.models.session import CanonicalSession, Message, MessageRole
        session = CanonicalSession()
        session.task_input.raw_content = "Do something"
        session.messages = [
            Message(role=MessageRole.ASSISTANT, content_text="Error: failed to process"),
        ]
        status = extractor._determine_status(session)
        assert status == ExecutionStatus.FAILED

    def test_empty_file(self, tmp_path: Path):
        empty_file = tmp_path / "empty.jsonl"
        empty_file.write_text("", encoding="utf-8")
        extractor = SessionExtractor()
        session = extractor.extract_from_file(str(empty_file))
        assert session.session_id == ""


class TestProtoExtractor:
    def test_extract(self, sample_session):
        extractor = ProtoExtractor()
        pa = extractor.extract(sample_session)

        assert pa.session_id == "test-session-001"
        assert pa.status == "success"
        assert pa.message_count == 4
        assert pa.tool_call_count == 2
        assert "Read" in pa.key_tools

    def test_tool_sequence_dedup(self):
        from skill_evolution.models.session import CanonicalSession, Message, MessageRole, ToolCall, ExecutionTrace, ExecutionStatus
        session = CanonicalSession()
        session.execution = ExecutionTrace(status=ExecutionStatus.SUCCESS)
        session.messages = [
            Message(
                role=MessageRole.ASSISTANT,
                tool_calls=[
                    ToolCall(tool_name="Read", tool_use_id="1", call_index=0),
                    ToolCall(tool_name="Read", tool_use_id="2", call_index=1),
                    ToolCall(tool_name="Bash", tool_use_id="3", call_index=2),
                ],
            ),
        ]
        extractor = ProtoExtractor()
        seq = extractor._build_tool_sequence(session)
        assert seq == "Read→Bash"
