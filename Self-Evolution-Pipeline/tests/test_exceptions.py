"""Tests for the exception hierarchy."""
from __future__ import annotations

from skill_evolution.exceptions import (
    PipelineError,
    ConfigError,
    SessionError,
    LLMError,
    EvolutionError,
    PatchError,
    ErrorCode,
)


class TestPipelineError:
    def test_basic(self):
        err = PipelineError("something broke")
        assert err.code == ErrorCode.UNKNOWN
        assert err.retryable is False
        assert "something broke" in str(err)

    def test_with_code(self):
        err = PipelineError("not found", code=ErrorCode.SESSION_NOT_FOUND)
        assert err.code == ErrorCode.SESSION_NOT_FOUND

    def test_to_dict(self):
        err = PipelineError("test", code=ErrorCode.CONFIG_INVALID, retryable=True, key="val")
        d = err.to_dict()
        assert d["code"] == "CONFIG_INVALID"
        assert d["retryable"] is True
        assert d["context"]["key"] == "val"

    def test_repr(self):
        err = PipelineError("test", code=ErrorCode.LLM_FATAL)
        assert "LLM_FATAL" in repr(err)


class TestConfigError:
    def test_code(self):
        err = ConfigError("bad config")
        assert err.code == ErrorCode.CONFIG_INVALID


class TestSessionError:
    def test_default_code(self):
        err = SessionError("parse failed")
        assert err.code == ErrorCode.SESSION_PARSE_FAILED


class TestLLMError:
    def test_rate_limit_classification(self):
        err = LLMError.from_exception(Exception("rate_limit exceeded"))
        assert err.code == ErrorCode.LLM_RATE_LIMIT
        assert err.retryable is True

    def test_overload_classification(self):
        err = LLMError.from_exception(Exception("503 Service Unavailable"))
        assert err.code == ErrorCode.LLM_OVERLOAD
        assert err.retryable is True

    def test_connection_classification(self):
        err = LLMError.from_exception(Exception("Connection refused"))
        assert err.code == ErrorCode.LLM_CONNECTION
        assert err.retryable is True

    def test_fatal_classification(self):
        err = LLMError.from_exception(Exception("unknown error"))
        assert err.code == ErrorCode.LLM_FATAL
        assert err.retryable is False


class TestEvolutionError:
    def test_code(self):
        err = EvolutionError("evolution failed")
        assert err.code == ErrorCode.EVOLUTION_FAILED


class TestPatchError:
    def test_code(self):
        err = PatchError("patch failed")
        assert err.code == ErrorCode.PATCH_FAILED
