"""Quality filtering for extracted sessions.

Filters sessions based on completeness, relevance, and execution quality.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from skill_evolution.models.session import CanonicalSession, ExecutionStatus
from skill_evolution.config.settings import SamplingConfig
from skill_evolution.utils.logging import Logger

logger = Logger.get_logger(__name__)


@dataclass
class FilterResult:
    """Result of filtering a single session."""
    session_id: str
    passed: bool
    group: str  # "failed", "retry_success", "success", "unknown"
    reason: str = ""


class QualityFilter:
    """Filters and classifies sessions by quality."""

    def __init__(self, config: SamplingConfig):
        self.config = config

    def filter_and_classify(
        self, sessions: list[CanonicalSession]
    ) -> dict[str, list[CanonicalSession]]:
        """Filter sessions and group them by execution status.

        Returns dict with keys: "failed", "retry_success", "success"
        """
        groups: dict[str, list[CanonicalSession]] = {
            "failed": [],
            "retry_success": [],
            "success": [],
        }

        for session in sessions:
            result = self._check_session(session)
            if result.passed:
                groups[result.group].append(session)

        return groups

    def _check_session(self, session: CanonicalSession) -> FilterResult:
        """Check if a session passes quality filters."""
        sid = session.session_id

        # check completeness - must have messages
        if not session.messages:
            return FilterResult(sid, False, "unknown", "No messages")

        # check completeness - must have task input
        if not session.task_input.raw_content:
            return FilterResult(sid, False, "unknown", "No task input")

        # check relevance score
        if session.feedback.quality_score < self.config.min_relevance_score:
            return FilterResult(
                sid, False, "unknown",
                f"Relevance score {session.feedback.quality_score} < {self.config.min_relevance_score}"
            )

        # classify by execution status
        status = session.execution.status
        if status == ExecutionStatus.FAILED:
            return FilterResult(sid, True, "failed")
        elif status == ExecutionStatus.RETRY_SUCCESS:
            return FilterResult(sid, True, "retry_success")
        elif status == ExecutionStatus.SUCCESS:
            return FilterResult(sid, True, "success")
        else:
            # unknown status - still include if has enough content
            if session.execution.total_tool_calls > 0:
                return FilterResult(sid, True, "success", "Unknown status but has tool calls")
            return FilterResult(sid, False, "unknown", "Unknown status, no tool calls")

    def get_stats(self, groups: dict[str, list[CanonicalSession]]) -> dict:
        """Get statistics about filtered groups."""
        total = sum(len(v) for v in groups.values())
        return {
            "total_input": total,
            "total_passed": total,
            "groups": {k: len(v) for k, v in groups.items()},
        }
