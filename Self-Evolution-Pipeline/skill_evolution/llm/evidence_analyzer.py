"""EvidenceAnalyzer: LLM with tool-use to analyze evidence and produce ExecutionAnalysis.

Uses LLMWithTools for multi-turn tool-use conversations. The LLM can call
read_session_summary, read_session_messages, read_session_tool_detail to
dive deeper into specific sessions.
"""
from __future__ import annotations

import json
import re

from skill_evolution.config.prompts import PromptLoader
from skill_evolution.config.settings import LLMConfig
from skill_evolution.llm.base import LLMWithTools
from skill_evolution.llm.tools import SESSION_TOOLS, SessionToolRegistry
from skill_evolution.models.evolution import EvolutionSuggestion, EvolutionType
from skill_evolution.models.session import CanonicalSession
from skill_evolution.exceptions import LLMError, ErrorCode
from skill_evolution.utils.logging import Logger

logger = Logger.get_logger(__name__)


class ExecutionAnalysis:
    """Structured result from EvidenceAnalyzer LLM call."""

    def __init__(self, raw_json: dict):
        self.raw = raw_json
        self.skill_name: str = raw_json.get("skill_name", "")
        self.total_sessions: int = raw_json.get("total_sessions", 0)
        self.success_count: int = raw_json.get("success_count", 0)
        self.retry_success_count: int = raw_json.get("retry_success_count", 0)
        self.failed_count: int = raw_json.get("failed_count", 0)
        self.success_rate: float = raw_json.get("success_rate", 0.0)
        self.dominant_patterns: list[dict] = raw_json.get("dominant_patterns", [])
        self.failure_analysis: dict = raw_json.get("failure_analysis", {})
        self.skill_gaps: list[dict] = raw_json.get("skill_gaps", [])
        self.execution_efficiency: dict = raw_json.get("execution_efficiency", {})

        # Parse evolution_suggestions into typed objects
        self.evolution_suggestions: list[EvolutionSuggestion] = [
            EvolutionSuggestion.from_dict(s)
            for s in raw_json.get("evolution_suggestions", [])
        ]

    @property
    def candidate_for_evolution(self) -> bool:
        return len(self.evolution_suggestions) > 0

    def to_dict(self) -> dict:
        return self.raw

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.raw, ensure_ascii=False, indent=indent)

    def summary(self) -> str:
        """Human-readable summary for logging."""
        lines = [
            f"ExecutionAnalysis: {self.skill_name}",
            f"  Sessions: {self.total_sessions} (success={self.success_count}, "
            f"retry={self.retry_success_count}, failed={self.failed_count})",
            f"  Success rate: {self.success_rate:.0%}",
            f"  Patterns: {len(self.dominant_patterns)}",
            f"  Root causes: {len(self.failure_analysis.get('root_causes', []))}",
            f"  Skill gaps: {len(self.skill_gaps)}",
            f"  Evolution suggestions: {len(self.evolution_suggestions)}",
        ]
        for s in self.evolution_suggestions:
            lines.append(f"    [{s.evolution_type.value}] {s.direction[:80]}")
        return "\n".join(lines)


class EvidenceAnalyzer:
    """Analyzes evidence set via LLM with tool-use support."""

    def __init__(
        self,
        config: LLMConfig,
        prompt_loader: PromptLoader | None = None,
        sessions: list[CanonicalSession] | None = None,
    ):
        self.config = config
        self._prompt_loader = prompt_loader
        self._sessions = sessions or []

    def analyze(self, evidence_text: str, skill_name: str, session_count: int) -> ExecutionAnalysis:
        """Send evidence to LLM with tools and parse ExecutionAnalysis from response."""
        system_prompt = self._prompt_loader.load("evidence_analysis_system") if self._prompt_loader else ""
        user_template = self._prompt_loader.load("evidence_analysis_user") if self._prompt_loader else ""

        user_prompt = user_template.format(
            skill_name=skill_name,
            session_count=session_count,
            evidence_text=evidence_text,
        )

        # Set up LLM with tools
        registry = SessionToolRegistry(self._sessions)
        llm = LLMWithTools(self.config, tools=SESSION_TOOLS)

        # Register tool handlers
        for tool_def in SESSION_TOOLS:
            name = tool_def["name"]
            handler = registry.get_handler(name)
            if handler:
                llm.register_tool(name, handler)

        # Run multi-turn conversation
        messages = [{"role": "user", "content": user_prompt}]
        response_text = llm.run_conversation(system=system_prompt, messages=messages)

        parsed = self._parse_response(response_text)
        return ExecutionAnalysis(parsed)

    def _parse_response(self, response_text: str) -> dict:
        """Extract JSON from LLM response text."""
        # try direct parse first
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # try extracting JSON from markdown code block
        json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # try finding first { to last }
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(response_text[start:end])
            except json.JSONDecodeError:
                pass

        raise LLMError(
            f"Failed to parse ExecutionAnalysis JSON from LLM response.",
            code=ErrorCode.LLM_RESPONSE_PARSE,
            response_preview=response_text[:500],
        )
