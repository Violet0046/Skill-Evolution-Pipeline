"""异常层级测试 — 验证错误分类和自动重试判断。

=== 异常层级 ===

  PipelineError (基类)
    ├── ConfigError        → ErrorCode.CONFIG_INVALID
    ├── SessionError       → ErrorCode.SESSION_PARSE_FAILED
    ├── LLMError           → 自动分类: RATE_LIMIT/CONNECTION/OVERLOAD/FATAL
    ├── EvolutionError     → ErrorCode.EVOLUTION_FAILED
    └── PatchError         → ErrorCode.PATCH_FAILED

=== LLM 错误自动分类 ===

  LLMError.from_exception() 根据错误信息中的关键词自动分类:
  - "rate limit" / "429"       → LLM_RATE_LIMIT (retryable=True)
  - "overloaded" / "500"       → LLM_OVERLOAD (retryable=True)
  - "connection" / "timeout"   → LLM_CONNECTION (retryable=True)
  - 其他                        → LLM_FATAL (retryable=False)
"""
from __future__ import annotations

import pytest

from skill_evolution.exceptions import (
    PipelineError,
    ConfigError,
    SessionError,
    LLMError,
    EvolutionError,
    PatchError,
    ErrorCode,
)


# ═══════════════════════════════════════════════════════════════════════════════
# PipelineError 基类
# ═══════════════════════════════════════════════════════════════════════════════

class TestPipelineError:
    """验证基础异常类的功能。"""

    def test_basic(self):
        """PipelineError 应该包含 message 和默认 code。"""
        err = PipelineError("something broke")
        assert err.message == "something broke"
        assert err.code == ErrorCode.UNKNOWN

    def test_with_code(self):
        """可以指定 ErrorCode。"""
        err = PipelineError("not found", code=ErrorCode.SESSION_NOT_FOUND)
        assert err.code == ErrorCode.SESSION_NOT_FOUND

    def test_to_dict(self):
        """to_dict() 序列化用于结构化日志。"""
        err = PipelineError("test", code=ErrorCode.LLM_RATE_LIMIT, retryable=True, attempt=2)
        d = err.to_dict()
        assert d["code"] == "LLM_RATE_LIMIT"
        assert d["retryable"] is True
        assert d["context"]["attempt"] == 2

    def test_str(self):
        """str() 应该包含 code 和 message。"""
        err = PipelineError("test msg", code=ErrorCode.CONFIG_INVALID)
        assert "CONFIG_INVALID" in str(err)
        assert "test msg" in str(err)


# ═══════════════════════════════════════════════════════════════════════════════
# 子类异常
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubErrors:
    """验证各子类异常的默认 code。"""

    def test_config_error(self):
        assert ConfigError("bad config").code == ErrorCode.CONFIG_INVALID

    def test_session_error(self):
        assert SessionError("parse failed").code == ErrorCode.SESSION_PARSE_FAILED

    def test_evolution_error(self):
        assert EvolutionError("evolve failed").code == ErrorCode.EVOLUTION_FAILED

    def test_patch_error(self):
        assert PatchError("patch failed").code == ErrorCode.PATCH_FAILED


# ═══════════════════════════════════════════════════════════════════════════════
# LLMError 自动分类
# ═══════════════════════════════════════════════════════════════════════════════

class TestLLMErrorClassification:
    """验证 LLMError.from_exception() 的自动错误分类。

    这个分类决定了 base.py 中的重试策略:
    - retryable=True  → 会自动重试 (带退避)
    - retryable=False → 直接抛出异常
    """

    def test_rate_limit_classification(self):
        """包含 "rate limit" 或 "429" 的错误应该分类为 RATE_LIMIT。"""
        err = LLMError.from_exception(Exception("Rate limit exceeded"))
        assert err.code == ErrorCode.LLM_RATE_LIMIT
        assert err.retryable is True

    def test_overload_classification(self):
        """包含 "overloaded" 或 "500" 的错误应该分类为 OVERLOAD。"""
        err = LLMError.from_exception(Exception("Server overloaded (503)"))
        assert err.code == ErrorCode.LLM_OVERLOAD
        assert err.retryable is True

    def test_connection_classification(self):
        """包含 "connection" 或 "timeout" 的错误应该分类为 CONNECTION。"""
        err = LLMError.from_exception(Exception("Connection refused"))
        assert err.code == ErrorCode.LLM_CONNECTION
        assert err.retryable is True

    def test_fatal_classification(self):
        """无法分类的错误应该标记为 FATAL，不可重试。"""
        err = LLMError.from_exception(Exception("Unknown API error"))
        assert err.code == ErrorCode.LLM_FATAL
        assert err.retryable is False
