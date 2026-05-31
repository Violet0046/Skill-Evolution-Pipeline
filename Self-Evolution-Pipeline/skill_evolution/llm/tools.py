"""Tool definitions and handlers for LLM tool-use.

Provides read_session_summary, read_session_messages, read_session_tool_detail.
Used by both EvidenceAnalyzer and SkillEvolver.
"""
from __future__ import annotations

import json
from typing import Any

from skill_evolution.models.session import CanonicalSession, MessageRole


# ── Anthropic tool schemas ───────────────────────────────────────────────────

SESSION_TOOLS: list[dict] = [
    {
        "name": "read_session_summary",
        "description": (
            "Read a compact summary of a session (~2KB). Shows task, execution stats, "
            "feedback, tool call summaries, and output preview. Use this first to "
            "understand what a session did before diving deeper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID (full or first 12 chars)",
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "read_session_messages",
        "description": (
            "Read messages from a session conversation. Returns role, content, "
            "tool calls and tool results. Each message content is truncated to "
            "2000 characters. Use start/end to read a specific range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID (full or first 12 chars)",
                },
                "start": {
                    "type": "integer",
                    "description": "Start message index (0-based, default 0)",
                },
                "end": {
                    "type": "integer",
                    "description": "End message index (exclusive, default=all)",
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "read_session_tool_detail",
        "description": (
            "Read detailed tool calls from a session. Returns tool name, input, "
            "and output for each tool call. Optionally filter by tool name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID (full or first 12 chars)",
                },
                "tool_name": {
                    "type": "string",
                    "description": "Filter by tool name (optional, returns all if omitted)",
                },
            },
            "required": ["session_id"],
        },
    },
]


class SessionToolRegistry:
    """Holds session data and provides tool handlers for LLM tool-use."""

    def __init__(self, sessions: list[CanonicalSession], max_content_length: int = 2000):
        self._sessions: dict[str, CanonicalSession] = {}
        self._max_content_length = max_content_length

        for s in sessions:
            self._sessions[s.session_id] = s
            # Also register by first 12 chars for convenience
            short = s.session_id[:12]
            if short not in self._sessions:
                self._sessions[short] = s

    def _resolve(self, session_id: str) -> CanonicalSession | None:
        return self._sessions.get(session_id)

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_content_length:
            return text
        return text[: self._max_content_length] + f"... [truncated, {len(text)} chars total]"

    def read_session_summary(self, session_id: str) -> str:
        """Return compact session summary as JSON string."""
        session = self._resolve(session_id)
        if not session:
            return json.dumps({"error": f"Session not found: {session_id}"})
        return json.dumps(session.summary(), ensure_ascii=False, indent=2)

    def read_session_messages(self, session_id: str, start: int = 0, end: int | None = None) -> str:
        """Return messages from a session as JSON string."""
        session = self._resolve(session_id)
        if not session:
            return json.dumps({"error": f"Session not found: {session_id}"})

        messages = session.messages[start:end]
        result = []
        for i, msg in enumerate(messages, start):
            entry: dict[str, Any] = {
                "index": i,
                "role": msg.role.value,
                "content": self._truncate(msg.content_text),
            }
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "tool": tc.tool_name,
                        "input": self._truncate(tc.input_summary),
                        "success": tc.success,
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_results:
                entry["tool_results"] = [
                    {
                        "tool_use_id": tr.get("tool_use_id", ""),
                        "content": self._truncate(tr.get("content", "")),
                    }
                    for tr in msg.tool_results
                ]
            result.append(entry)

        return json.dumps(result, ensure_ascii=False, indent=2)

    def read_session_tool_detail(self, session_id: str, tool_name: str | None = None) -> str:
        """Return detailed tool calls from a session."""
        session = self._resolve(session_id)
        if not session:
            return json.dumps({"error": f"Session not found: {session_id}"})

        result = []
        for msg in session.messages:
            if msg.role != MessageRole.ASSISTANT:
                continue
            for tc in msg.tool_calls:
                if tool_name and tc.tool_name != tool_name:
                    continue
                entry = {
                    "tool": tc.tool_name,
                    "input": self._truncate(tc.input_summary),
                    "success": tc.success,
                    "result": self._truncate(tc.result_summary),
                    "duration_ms": tc.duration_ms,
                }
                result.append(entry)

        return json.dumps(result, ensure_ascii=False, indent=2)

    def get_handler(self, tool_name: str):
        """Get handler function for a tool name."""
        return getattr(self, tool_name, None)
