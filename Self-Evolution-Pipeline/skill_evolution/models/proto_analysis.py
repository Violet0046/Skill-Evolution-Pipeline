"""ProtoAnalysis: lightweight structured extraction from a single session.

~500 bytes per session, produced by code (not LLM).
Aggregated by EvidenceBuilder into a single text block for the analysis LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json


@dataclass
class ProtoAnalysis:
    """Structured pre-analysis of a single session, extracted by code."""

    session_id: str = ""
    status: str = ""                    # success / retry_success / failed
    task_title: str = ""
    task_description: str = ""
    tool_sequence: str = ""             # "Read→Bash→Read→Bash→Write"
    failure_reason: str = ""
    correction: str = ""
    final_output: str = ""              # last assistant message, truncated
    error_tool_calls: list[str] = field(default_factory=list)  # errored tool calls
    token_usage: int = 0
    duration_seconds: float = 0.0

    # metadata for traceability
    source_file: str = ""               # original JSONL filename
    quality_score: int = 0
    relevance_level: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_brief(self) -> dict:
        """Compact representation for evidence formatting (~300-500B)."""
        d = {
            "sid": self.session_id[:12],
            "status": self.status,
            "task": self.task_title or self.task_description[:80],
            "tools": self.tool_sequence,
            "tokens": self.token_usage,
        }
        if self.failure_reason:
            d["fail"] = self.failure_reason[:100]
        if self.correction:
            d["fix"] = self.correction[:100]
        if self.error_tool_calls:
            d["err_calls"] = self.error_tool_calls[:3]
        return d
