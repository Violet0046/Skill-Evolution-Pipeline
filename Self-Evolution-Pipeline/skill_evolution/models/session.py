"""Session data models for the Skill Evolution Pipeline.

Canonical format for session data extracted from Claude Code agent JSONL files.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import json


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RETRY_SUCCESS = "retry_success"  # failed then retried and succeeded
    UNKNOWN = "unknown"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ContentType(str, Enum):
    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ToolCall:
    tool_name: str
    tool_use_id: str
    call_index: int
    input_summary: str = ""  # truncated description of input
    success: bool = True
    result_summary: str = ""  # truncated description of result
    duration_ms: int = 0


@dataclass
class Message:
    role: MessageRole
    content_text: str = ""  # flattened text content
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)  # {tool_use_id, content}
    model: str = ""
    usage: Optional[TokenUsage] = None
    timestamp: str = ""
    uuid: str = ""


@dataclass
class TaskInput:
    """The initial task description that triggered this session."""
    requirement_id: str = ""
    requirement_title: str = ""
    requirement_type: str = ""
    task_description: str = ""
    raw_content: str = ""  # full first user message
    skill_content: str = ""  # the SKILL.md content injected
    working_directory: str = ""


@dataclass
class ExecutionTrace:
    """Aggregated execution metrics from the session."""
    status: ExecutionStatus = ExecutionStatus.UNKNOWN
    total_messages: int = 0
    total_tool_calls: int = 0
    tool_call_details: list[ToolCall] = field(default_factory=list)
    total_token_usage: TokenUsage = field(default_factory=TokenUsage)
    models_used: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class Feedback:
    """Feedback from review/retry context."""
    is_retry: bool = False
    retry_reason: str = ""
    failure_reason: str = ""
    correction_suggestion: str = ""
    quality_score: int = 0  # from sessions.jsonl relevance_score
    relevance_level: str = ""
    is_direct_call: bool = False


@dataclass
class CanonicalSession:
    """Standardized session representation extracted from raw JSONL."""
    session_id: str = ""
    agent_id: str = ""
    skill_name: str = ""
    timestamp: str = ""
    upload_time: str = ""

    task_input: TaskInput = field(default_factory=TaskInput)
    execution: ExecutionTrace = field(default_factory=ExecutionTrace)
    messages: list[Message] = field(default_factory=list)
    feedback: Feedback = field(default_factory=Feedback)

    metadata: dict = field(default_factory=dict)  # session_path, promptId, etc.

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def summary(self) -> dict:
        """Compact summary for pipeline processing (~2KB target)."""
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "skill_name": self.skill_name,
            "timestamp": self.timestamp,
            "task": {
                "requirement_id": self.task_input.requirement_id,
                "requirement_title": self.task_input.requirement_title,
                "requirement_type": self.task_input.requirement_type,
                "task_description": self.task_input.task_description,
            },
            "execution": {
                "status": self.execution.status.value,
                "total_messages": self.execution.total_messages,
                "total_tool_calls": self.execution.total_tool_calls,
                "models_used": self.execution.models_used,
                "total_tokens": self.execution.total_token_usage.total,
                "duration_seconds": self.execution.duration_seconds,
            },
            "tool_calls": [
                {
                    "tool_name": tc.tool_name,
                    "call_index": tc.call_index,
                    "success": tc.success,
                    "input_summary": tc.input_summary[:200],
                    "result_summary": tc.result_summary[:200],
                }
                for tc in self.execution.tool_call_details
            ],
            "feedback": {
                "is_retry": self.feedback.is_retry,
                "retry_reason": self.feedback.retry_reason,
                "failure_reason": self.feedback.failure_reason,
                "correction_suggestion": self.feedback.correction_suggestion,
                "quality_score": self.feedback.quality_score,
                "relevance_level": self.feedback.relevance_level,
                "is_direct_call": self.feedback.is_direct_call,
            },
            "skill_content_preview": self.task_input.skill_content[:500] if self.task_input.skill_content else "",
            "output_preview": self._get_output_preview(),
        }

    def _get_output_preview(self) -> str:
        """Get the last assistant message as output preview."""
        for msg in reversed(self.messages):
            if msg.role == MessageRole.ASSISTANT and msg.content_text:
                return msg.content_text[:500]
        return ""
