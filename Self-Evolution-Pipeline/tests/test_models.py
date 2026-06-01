"""数据模型测试 — 验证 pipeline 中所有数据结构的正确性。

=== 测试范围 ===

1. CanonicalSession (session.py)
   - 序列化: to_dict(), to_json()
   - 摘要: summary() — 用于 LLM 上下文的压缩表示
   - 输出预览: _get_output_preview() — 提取最后一条 assistant 消息

2. ProtoAnalysis (proto_analysis.py)
   - 序列化: to_dict()
   - 简要表示: to_brief() — 用于 evidence 格式化的 ~300B 表示

3. EvolutionSuggestion (evolution.py)
   - 序列化: to_dict(), from_dict()
   - 属性: target_skill_id (取第一个 target)

4. 其他模型: TokenUsage, ExecutionStatus, MessageRole 等枚举和数据类
"""
from __future__ import annotations

import json

import pytest

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
from skill_evolution.models.evolution import (
    EvolutionSuggestion,
    EvolutionType,
    SkillCategory,
)


# ═══════════════════════════════════════════════════════════════════════════════
# ExecutionStatus 枚举
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutionStatus:
    """验证执行状态枚举的值和类型。"""

    def test_enum_values(self):
        """ExecutionStatus 应该有 4 个值: SUCCESS, FAILED, RETRY_SUCCESS, UNKNOWN。"""
        assert ExecutionStatus.SUCCESS.value == "success"
        assert ExecutionStatus.FAILED.value == "failed"
        assert ExecutionStatus.RETRY_SUCCESS.value == "retry_success"
        assert ExecutionStatus.UNKNOWN.value == "unknown"

    def test_is_string_enum(self):
        """ExecutionStatus 继承自 str，可以直接用于字符串比较。"""
        assert ExecutionStatus.SUCCESS == "success"
        assert isinstance(ExecutionStatus.SUCCESS, str)


# ═══════════════════════════════════════════════════════════════════════════════
# TokenUsage 数据类
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenUsage:
    """验证 Token 使用量计算。"""

    def test_total(self):
        """total 属性应该返回 input + output tokens。"""
        usage = TokenUsage(input_tokens=1000, output_tokens=500)
        assert usage.total == 1500

    def test_defaults(self):
        """默认值应该都是 0。"""
        usage = TokenUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cache_creation_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.total == 0


# ═══════════════════════════════════════════════════════════════════════════════
# CanonicalSession
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalSession:
    """验证会话对象的核心功能。"""

    def test_summary_structure(self, sample_session):
        """summary() 应该返回一个包含所有关键字段的字典。

        summary 是发送给 LLM 的压缩表示 (~2KB)，包含:
        - session_id, agent_id, skill_name
        - task (requirement_id, title, type, description)
        - execution (status, messages, tools, tokens, duration)
        - tool_calls (每个工具调用的摘要)
        - feedback (quality_score, relevance_level 等)
        """
        s = sample_session.summary()
        assert s["session_id"] == "test-session-001"
        assert s["task"]["requirement_id"] == "REQ-001"
        assert s["execution"]["status"] == "success"
        assert s["execution"]["total_tokens"] == 1500  # 1000 + 500
        # tool_calls 来自 execution.tool_call_details (conftest 中未设置)
        # 验证其他关键字段
        assert s["task"]["requirement_id"] == "REQ-001"

    def test_to_json(self, sample_session):
        """to_json() 应该返回合法的 JSON 字符串。"""
        json_str = sample_session.to_json()
        parsed = json.loads(json_str)
        assert parsed["session_id"] == "test-session-001"

    def test_get_output_preview(self, sample_session):
        """_get_output_preview() 应该返回最后一条 assistant 消息的内容。"""
        preview = sample_session._get_output_preview()
        assert "Analysis complete" in preview

    def test_get_output_preview_empty(self):
        """没有消息时，_get_output_preview() 应该返回空字符串。"""
        session = CanonicalSession()
        assert session._get_output_preview() == ""


# ═══════════════════════════════════════════════════════════════════════════════
# ProtoAnalysis
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtoAnalysis:
    """验证 ProtoAnalysis 的序列化和简要表示。"""

    def test_to_brief(self, sample_proto_analysis):
        """to_brief() 应该返回 ~300B 的紧凑表示，用于 evidence 格式化。

        包含: sid, status, task, tools, tokens, msg_count, tc_count, key_tools, session_path
        可选: fail, fix, err_calls (如果存在)

        注意: sid 被截断为 session_id[:12]
        """
        brief = sample_proto_analysis.to_brief()
        assert brief["sid"] == "test-session"  # [:12] 截断
        assert brief["status"] == "success"
        assert brief["tools"] == "Read→Bash→Write"
        assert brief["tokens"] == 1500

    def test_to_dict(self, sample_proto_analysis):
        """to_dict() 应该返回完整的字典表示。"""
        d = sample_proto_analysis.to_dict()
        assert d["session_id"] == "test-session-001"
        assert d["token_usage"] == 1500


# ═══════════════════════════════════════════════════════════════════════════════
# EvolutionSuggestion
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvolutionSuggestion:
    """验证进化建议的序列化和反序列化。"""

    def test_from_dict(self):
        """from_dict() 应该从字典创建 EvolutionSuggestion。"""
        data = {
            "type": "fix",
            "direction": "Add error handling",
            "target_skills": ["skill-1"],
            "category": "tool_guide",
            "evidence_sessions": ["session-1"],
            "evidence_session_paths": ["/tmp/test.jsonl"],
        }
        suggestion = EvolutionSuggestion.from_dict(data)
        assert suggestion.evolution_type == EvolutionType.FIX
        assert suggestion.direction == "Add error handling"
        assert suggestion.target_skill_ids == ["skill-1"]
        assert suggestion.category == SkillCategory.TOOL_GUIDE

    def test_to_dict(self, sample_evolution_suggestion):
        """to_dict() 应该返回可序列化的字典。"""
        d = sample_evolution_suggestion.to_dict()
        assert d["type"] == "fix"
        assert d["direction"] == "Add error handling for missing protocol files"
        assert "test-skill" in d["target_skills"]

    def test_target_skill_id_empty(self):
        """没有 target_skill_ids 时，target_skill_id 应该返回空字符串。"""
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            direction="test",
        )
        assert suggestion.target_skill_id == ""
