"""共享测试 fixtures — 为所有测试模块提供标准化的测试数据。

=== Pipeline 数据流 ===

  sessions.jsonl (原始JSONL文件)
       ↓  Stage 1: SessionExtractor.extract_from_file()
  CanonicalSession[] (标准化会话对象)
       ↓  Stage 2: QualityFilter.filter_and_classify()
  dict[str, list[CanonicalSession]] (按状态分组: success/failed/retry_success)
       ↓  Stage 3: DatasetSplitter.split()
  (evolution_set, test_set) (70/30分割)
       ↓  Stage 4: ProtoExtractor.extract()
  ProtoAnalysis[] (轻量级结构化摘要, ~500B/个)
       ↓  Stage 5: EvidenceBuilder.build()
  evidence_text (格式化文本块, ~10KB)
       ↓  Stage 6: EvidenceAnalyzer.analyze() ← LLM调用
  ExecutionAnalysis (含 evolution_suggestions)
       ↓  Stage 7: SkillEvolver.evolve() ← N次LLM调用
  N个 .change 文件 (原子变更)

=== Fixtures 说明 ===

- sample_session: 一个完整的 CanonicalSession，模拟真实会话的所有字段
- sample_sessions: 包含 success/failed/retry_success 三种状态的会话列表
- sample_proto_analysis: 从 sample_session 提取的 ProtoAnalysis
- sample_evolution_suggestion: 一个 FIX 类型的进化建议
- jsonl_file: 写入磁盘的临时 JSONL 文件，用于测试 SessionExtractor
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


# ── 自动执行的 fixture ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_config():
    """每个测试前后重置 config 单例，防止测试间状态污染。"""
    reset_config()
    yield
    reset_config()


# ── 临时目录 ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """提供临时目录，用于测试中的文件输出。"""
    return tmp_path


# ── LLM 配置 ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_llm_config() -> LLMConfig:
    """测试用 LLM 配置 — 不会发起真实 API 调用。

    注意: provider 设为 "openai" 但 api_key 为空，确保不会意外调用真实 API。
    测试中应使用 mock 来替代真实的 LLM 调用。
    """
    return LLMConfig(
        provider="openai",
        model="test-model",
        max_tokens=1024,
        max_retries=1,
        timeout=5.0,
        api_key="",
        api_base=None,
    )


# ── Pipeline 配置 ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_config(tmp_dir: Path) -> PipelineConfig:
    """最小化的 Pipeline 配置，用于测试 pipeline 各阶段。

    - skill_name: "test-skill"
    - min_relevance_score: 0 (不过滤低分)
    - project_root: 临时目录
    """
    return PipelineConfig(
        skill_name="test-skill",
        llm=LLMConfig(model="test-model", max_retries=1, timeout=5.0, api_key="", api_base=None),
        sampling=SamplingConfig(min_relevance_score=0),
        paths=PathConfig(project_root=str(tmp_dir)),
    )


# ── 单个成功会话 ──────────────────────────────────────────────────────────────

@pytest.fixture
def sample_session() -> CanonicalSession:
    """一个完整的成功会话 — 模拟真实的 Claude Code agent 交互。

    场景: 用户要求分析协议 → agent 调用 Read 工具读取文件 → 返回分析结果

    包含:
    - 4条消息: user → assistant(调用Read) → tool(返回结果) → assistant(最终输出)
    - 2次工具调用
    - status=SUCCESS, quality_score=8
    """
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


# ── 多状态会话列表 ────────────────────────────────────────────────────────────

@pytest.fixture
def sample_sessions(sample_session: CanonicalSession) -> list[CanonicalSession]:
    """包含三种状态的会话列表 — 用于测试 Filter 和 Split 阶段。

    - 1个 SUCCESS 会话 (quality_score=8)
    - 1个 FAILED 会话 (quality_score=6)
    - 1个 RETRY_SUCCESS 会话 (quality_score=7)
    """
    # 复制成功会话并修改为失败会话
    failed = CanonicalSession()
    failed.session_id = "test-session-002"
    failed.agent_id = "test-agent"
    failed.skill_name = "test-skill"
    failed.timestamp = "2026-01-01T01:00:00Z"
    failed.task_input = TaskInput(
        requirement_id="REQ-002",
        requirement_title="Failed task",
        task_description="This task failed",
        raw_content="需求ID：REQ-002\n需求标题：Failed task\n## 任务：This task failed",
    )
    failed.execution = ExecutionTrace(
        status=ExecutionStatus.FAILED,
        total_messages=2,
        total_tool_calls=1,
        total_token_usage=TokenUsage(input_tokens=500, output_tokens=200),
        models_used=["test-model"],
        duration_seconds=5.0,
    )
    failed.messages = [
        Message(role=MessageRole.USER, content_text="Do something"),
        Message(role=MessageRole.ASSISTANT, content_text="Error: failed to complete"),
    ]
    failed.feedback = Feedback(quality_score=6, relevance_level="medium")
    failed.metadata = {"file_path": "/tmp/test2.jsonl"}

    # 复制成功会话并修改为重试成功会话
    retry = CanonicalSession()
    retry.session_id = "test-session-003"
    retry.agent_id = "test-agent"
    retry.skill_name = "test-skill"
    retry.timestamp = "2026-01-01T02:00:00Z"
    retry.task_input = TaskInput(
        requirement_id="REQ-003",
        requirement_title="Retry task",
        task_description="This task was retried",
        raw_content="需求ID：REQ-003\n需求标题：Retry task\n## 重试：This task was retried",
    )
    retry.execution = ExecutionTrace(
        status=ExecutionStatus.RETRY_SUCCESS,
        total_messages=6,
        total_tool_calls=3,
        total_token_usage=TokenUsage(input_tokens=1500, output_tokens=800),
        models_used=["test-model"],
        duration_seconds=20.0,
    )
    retry.messages = [
        Message(role=MessageRole.USER, content_text="重试: Do something again"),
        Message(role=MessageRole.ASSISTANT, content_text="Retrying... 完成"),
    ]
    retry.feedback = Feedback(quality_score=7, relevance_level="high", is_retry=True, retry_reason="First attempt failed")
    retry.metadata = {"file_path": "/tmp/test3.jsonl"}

    return [sample_session, failed, retry]


# ── ProtoAnalysis ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_proto_analysis() -> ProtoAnalysis:
    """从 sample_session 提取的 ProtoAnalysis — 用于测试 EvidenceBuilder。

    ProtoAnalysis 是 Stage 4 的输出，约 500 字节，包含:
    - 会话ID、状态、任务信息
    - 工具序列 (Read→Bash→Write)
    - Token 使用量、消息数、工具调用数
    """
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


# ── EvolutionSuggestion ──────────────────────────────────────────────────────

@pytest.fixture
def sample_evolution_suggestion() -> EvolutionSuggestion:
    """一个 FIX 类型的进化建议 — 用于测试 SkillEvolver。

    FIX 类型: 修复现有技能中的问题
    - target_skill_ids: 目标技能 ID
    - evidence_sessions: 支持此建议的会话 ID
    - evidence_session_paths: 对应的文件路径 (用于 tool-use 查看详情)
    """
    return EvolutionSuggestion(
        evolution_type=EvolutionType.FIX,
        direction="Add error handling for missing protocol files",
        target_skill_ids=["test-skill"],
        evidence_sessions=["test-session-001"],
        evidence_session_paths=["/tmp/test.jsonl"],
    )


# ── 临时 JSONL 文件 ──────────────────────────────────────────────────────────

@pytest.fixture
def jsonl_file(tmp_dir: Path) -> Path:
    """创建一个临时 JSONL 会话文件 — 用于测试 SessionExtractor。

    文件格式: 每行一个 JSON 对象，模拟 Claude Code agent 的原始对话记录。
    包含:
    - 第1行: type=user, 包含需求ID和任务描述
    - 第2行: type=assistant, 包含模型回复和 token 使用量
    """
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
                "content": [{"type": "text", "text": "I'll help with that. 完成"}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        },
    ]
    path = tmp_dir / "test_session.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path
