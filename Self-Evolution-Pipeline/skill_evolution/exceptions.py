"""Unified exception hierarchy for the Skill Evolution Pipeline.

Adapted from OpenSpace grounding/core/exceptions.py pattern:
- Typed error codes (str Enum)
- Retryable flag for automatic retry decisions
- Structured context dict for logging/metrics
- to_dict() for JSON serialization
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Any


class ErrorCode(str, Enum):
    """Typed error codes for pipeline operations."""
    # Generic
    UNKNOWN = "UNKNOWN"
    CONFIG_INVALID = "CONFIG_INVALID"

    # Extraction
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_PARSE_FAILED = "SESSION_PARSE_FAILED"
    INDEX_NOT_FOUND = "INDEX_NOT_FOUND"

    # LLM
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"
    LLM_CONNECTION = "LLM_CONNECTION"
    LLM_OVERLOAD = "LLM_OVERLOAD"
    LLM_FATAL = "LLM_FATAL"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_RESPONSE_PARSE = "LLM_RESPONSE_PARSE"

    # Evolution
    PATCH_FAILED = "PATCH_FAILED"
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    EVOLUTION_FAILED = "EVOLUTION_FAILED"

    # Pipeline
    PIPELINE_STAGE_FAILED = "PIPELINE_STAGE_FAILED"
    PIPELINE_CANCELLED = "PIPELINE_CANCELLED"


class PipelineError(Exception):
    """Base exception for the Skill Evolution Pipeline.

    Args:
        message: Human-readable error message.
        code: Typed error code from ErrorCode enum.
        retryable: Whether the caller may retry automatically.
        **context: Extra key-value pairs for structured logging.
    """

    __slots__ = ("message", "code", "retryable", "context")

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.UNKNOWN,
        retryable: bool = False,
        **context: Any,
    ):
        super().__init__(f"[{code}] {message}")
        self.message = message
        self.code = code
        self.retryable = retryable
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        """Serialize for structured logging / JSON output."""
        return {
            "code": self.code.value if isinstance(self.code, ErrorCode) else self.code,
            "message": self.message,
            "retryable": self.retryable,
            "context": self.context,
        }

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def __repr__(self) -> str:
        return f"PipelineError(code={self.code}, msg={self.message!r})"


class ConfigError(PipelineError):
    """Configuration-related errors."""
    def __init__(self, message: str, **context: Any):
        super().__init__(message, code=ErrorCode.CONFIG_INVALID, **context)


class SessionError(PipelineError):
    """Session extraction/parsing errors."""
    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.SESSION_PARSE_FAILED, **context: Any):
        super().__init__(message, code=code, **context)


class LLMError(PipelineError):
    """LLM API errors with automatic category classification."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.LLM_FATAL,
        retryable: bool = False,
        **context: Any,
    ):
        super().__init__(message, code=code, retryable=retryable, **context)

    @classmethod
    def from_exception(cls, error: Exception, attempt: int = 0) -> LLMError:
        """Classify an API error into a typed LLMError."""
        error_str = str(error).lower()

        if any(kw in error_str for kw in ["rate limit", "rate_limit", "too many requests", "429"]):
            return cls(
                str(error), code=ErrorCode.LLM_RATE_LIMIT, retryable=True, attempt=attempt,
            )
        if any(kw in error_str for kw in ["overloaded", "500", "502", "503", "504", "overloaded_error"]):
            return cls(
                str(error), code=ErrorCode.LLM_OVERLOAD, retryable=True, attempt=attempt,
            )
        if any(kw in error_str for kw in [
            "cannot connect", "connection refused", "connection reset",
            "timeout", "timed out", "network unreachable",
        ]):
            return cls(
                str(error), code=ErrorCode.LLM_CONNECTION, retryable=True, attempt=attempt,
            )
        return cls(str(error), code=ErrorCode.LLM_FATAL, retryable=False, attempt=attempt)


class EvolutionError(PipelineError):
    """Skill evolution errors (patching, derivation)."""
    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.EVOLUTION_FAILED, **context: Any):
        super().__init__(message, code=code, **context)


class PatchError(PipelineError):
    """Patch application errors."""
    def __init__(self, message: str, **context: Any):
        super().__init__(message, code=ErrorCode.PATCH_FAILED, **context)
