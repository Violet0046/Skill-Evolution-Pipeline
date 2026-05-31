"""Shared test fixtures for the Skill Evolution Pipeline.

Adapted from OpenSpace testing patterns:
- reset_config() for test isolation
- Sample data factories
- Temporary directory fixtures
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from skill_evolution.config.settings import (
    PipelineConfig,
    LLMConfig,
    SamplingConfig,
    ExtractionConfig,
    PathConfig,
    reset_config,
)
from skill_evolution.models.session import (
    CanonicalSession,
    ExecutionStatus,
    ExecutionTrace,
    TaskInput,
    Feedback,
    Message,
    MessageRole,
    TokenUsage,
    ToolCall,
)
from skill_evolution.models.proto_analysis import ProtoAnalysis
from skill_evolution.models.evolution import EvolutionSuggestion, EvolutionType


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config singleton before each test."""
    reset_config()
    yield
    reset_config()


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for test outputs."""
    return tmp_path


@pytest.fixture
def sample_llm_config() -> LLMConfig:
    """Minimal LLM config for testing (no real API calls)."""
    return LLMConfig(
        provider="anthropic",
        model="test-model",
        max_tokens=1024,
        max_retries=1,
        timeout=5.0,
    )


@pytest.fixture
def sample_config(tmp_dir: Path) -> PipelineConfig:
    """Minimal pipeline config for testing."""
    return PipelineConfig(
        skill_name="test-skill",
        llm=LLMConfig(model="test-model", max_retries=1, timeout=5.0),
        sampling=SamplingConfig(min_relevance_score=0),
        paths=PathConfig(project_root=str(tmp_dir)),
    )


@pytest.fixture
def sample_session() -> CanonicalSession:
    """A minimal but complete CanonicalSession for testing."""
    session = CanonicalSession()
    session.session_id = "test-session-001"
    session.agent_id = "test-agent"
    session.skill_name = "test-skill"
    session.timestamp = "2026-01-01T00:00:00Z"

    session.task_input = TaskInput(
        requirement_id="REQ-001",
        requirement_title="Test requirement",
        requirement_type="protocol",
        task_description="Analyze the test protocol",
        raw_content="需求ID：REQ-001\n需求标题：Test requirement\n## 任务：Analyze the test protocol",
    )

    session.execution = ExecutionTrace(
        status=ExecutionStatus.SUCCESS,
        total_messages=4,
        total_tool_calls=2,
        total_token_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        models_used=["test-model"],
        duration_seconds=10.0,
    )

    session.messages = [
        Message(role=MessageRole.USER, content_text="Analyze this protocol"),
        Message(
            role=MessageRole.ASSISTANT,
            content_text="I'll analyze the protocol.",
            tool_calls=[
                ToolCall(
                    tool_name="Read",
                    tool_use_id="tu-001",
                    call_index=0,
                    input_summary='{"file_path": "protocol.md"}',
                    success=True,
                ),
            ],
        ),
        Message(
            role=MessageRole.TOOL,
            tool_results=[{"tool_use_id": "tu-001", "content": "Protocol content here"}],
        ),
        Message(role=MessageRole.ASSISTANT, content_text="Analysis complete. Protocol is valid."),
    ]

    session.feedback = Feedback(
        quality_score=8,
        relevance_level="high",
    )

    session.metadata = {"file_path": "/tmp/test.jsonl"}

    return session


@pytest.fixture
def sample_proto_analysis() -> ProtoAnalysis:
    """A minimal ProtoAnalysis for testing."""
    return ProtoAnalysis(
        session_id="test-session-001",
        status="success",
        task_title="Test requirement",
        task_description="Analyze the test protocol",
        tool_sequence="Read→Bash→Write",
        token_usage=1500,
        duration_seconds=10.0,
        message_count=4,
        tool_call_count=2,
        key_tools=["Read", "Bash", "Write"],
        quality_score=8,
        session_path="/tmp/test.jsonl",
    )


@pytest.fixture
def sample_evolution_suggestion() -> EvolutionSuggestion:
    """A minimal EvolutionSuggestion for testing."""
    return EvolutionSuggestion(
        evolution_type=EvolutionType.FIX,
        direction="Add error handling for missing protocol files",
        target_skill_ids=["test-skill"],
        evidence_sessions=["test-session-001"],
        evidence_session_paths=["/tmp/test.jsonl"],
    )


@pytest.fixture
def jsonl_file(tmp_dir: Path) -> Path:
    """Create a sample JSONL session file for testing."""
    records = [
        {
            "type": "user",
            "agentId": "test-agent",
            "sessionId": "test-session-001",
            "timestamp": "2026-01-01T00:00:00Z",
            "promptId": "prompt-001",
            "message": {"role": "user", "content": "需求ID：REQ-001\n需求标题：Test\n## 任务：Do something"},
        },
        {
            "type": "assistant",
            "uuid": "uuid-001",
            "timestamp": "2026-01-01T00:00:05Z",
            "message": {
                "role": "assistant",
                "model": "test-model",
                "content": [{"type": "text", "text": "I'll help with that."}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        },
    ]
    path = tmp_dir / "test_session.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path
