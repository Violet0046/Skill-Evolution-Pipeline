"""ProtoExtractor: CanonicalSession → ProtoAnalysis (pure code, no LLM).

Produces a lightweight (~500B) structured summary from an already-parsed session.
"""
from __future__ import annotations

from skill_evolution.models.session import CanonicalSession, MessageRole
from skill_evolution.models.proto_analysis import ProtoAnalysis
from skill_evolution.utils.logging import Logger

logger = Logger.get_logger(__name__)


class ProtoExtractor:
    """Extracts ProtoAnalysis from CanonicalSession."""

    def __init__(self, max_output_length: int = 300, max_error_summary: int = 150):
        self.max_output_length = max_output_length
        self.max_error_summary = max_error_summary

    def extract(self, session: CanonicalSession) -> ProtoAnalysis:
        """Convert a CanonicalSession into a ProtoAnalysis."""
        pa = ProtoAnalysis()

        pa.session_id = session.session_id
        pa.status = session.execution.status.value
        pa.token_usage = session.execution.total_token_usage.total
        pa.duration_seconds = session.execution.duration_seconds

        # task info
        pa.task_title = session.task_input.requirement_title
        pa.task_description = session.task_input.task_description

        # tool sequence
        pa.tool_sequence = self._build_tool_sequence(session)

        # failure / correction from feedback
        pa.failure_reason = session.feedback.failure_reason or session.feedback.retry_reason
        pa.correction = session.feedback.correction_suggestion

        # final output
        pa.final_output = session._get_output_preview()[:self.max_output_length]

        # errored tool calls
        pa.error_tool_calls = self._find_error_tool_calls(session)

        # metadata
        pa.source_file = session.metadata.get("file_path", "")
        pa.session_path = session.metadata.get("file_path", "")
        pa.quality_score = session.feedback.quality_score
        pa.relevance_level = session.feedback.relevance_level

        # richer session metadata
        pa.message_count = len(session.messages)
        pa.tool_call_count = session.execution.total_tool_calls
        pa.key_tools = self._extract_key_tools(session)

        return pa

    def _build_tool_sequence(self, session: CanonicalSession) -> str:
        """Build a compact tool call sequence like 'Read→Bash→Write'."""
        names = []
        for msg in session.messages:
            if msg.role == MessageRole.ASSISTANT:
                for tc in msg.tool_calls:
                    names.append(tc.tool_name)

        if not names:
            return ""

        # deduplicate consecutive calls: Read→Read→Bash → Read→Bash
        compact = [names[0]]
        for name in names[1:]:
            if name != compact[-1]:
                compact.append(name)

        return "→".join(compact)

    def _extract_key_tools(self, session: CanonicalSession) -> list[str]:
        """Extract deduplicated tool names used in this session."""
        seen = set()
        tools = []
        for msg in session.messages:
            if msg.role == MessageRole.ASSISTANT:
                for tc in msg.tool_calls:
                    if tc.tool_name not in seen:
                        seen.add(tc.tool_name)
                        tools.append(tc.tool_name)
        return tools

    def _find_error_tool_calls(self, session: CanonicalSession) -> list[str]:
        """Find tool calls that returned errors."""
        errors = []
        for msg in session.messages:
            if msg.role == MessageRole.TOOL:
                for tr in msg.tool_results:
                    content = tr.get("content", "")
                    if any(kw in content.lower() for kw in ["error", "exception", "traceback", "failed"]):
                        # find matching tool call
                        tool_id = tr.get("tool_use_id", "")
                        for tc_msg in session.messages:
                            if tc_msg.role == MessageRole.ASSISTANT:
                                for tc in tc_msg.tool_calls:
                                    if tc.tool_use_id == tool_id:
                                        summary = f"{tc.tool_name}: {tc.input_summary[:self.max_error_summary]}"
                                        if summary not in errors:
                                            errors.append(summary)
                                        break
        return errors[:5]  # cap at 5
