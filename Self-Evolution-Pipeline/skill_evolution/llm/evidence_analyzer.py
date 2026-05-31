"""EvidenceAnalyzer: one LLM call to analyze all evidence and produce ExecutionAnalysis.

Reference: OpenSpace skill_engine_prompts.py pattern.
Single call, structured JSON output.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from skill_evolution.config.prompts import PromptLoader
from skill_evolution.config.settings import LLMConfig
from skill_evolution.models.evolution import EvolutionSuggestion, EvolutionType


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
    """Analyzes evidence set via a single LLM call and returns ExecutionAnalysis."""

    def __init__(self, config: LLMConfig, prompt_loader: PromptLoader | None = None):
        self.config = config
        self._client = None
        self._prompt_loader = prompt_loader

    def _get_client(self):
        if self._client is None:
            if self.config.provider == "anthropic":
                import anthropic
                self._client = anthropic.Anthropic()
            else:
                raise ValueError(f"Unsupported LLM provider: {self.config.provider}")
        return self._client

    def analyze(self, evidence_text: str, skill_name: str, session_count: int) -> ExecutionAnalysis:
        """Send evidence to LLM and parse ExecutionAnalysis from response."""
        system_prompt = self._prompt_loader.load("evidence_analysis_system") if self._prompt_loader else ""
        user_template = self._prompt_loader.load("evidence_analysis_user") if self._prompt_loader else ""

        user_prompt = user_template.format(
            skill_name=skill_name,
            session_count=session_count,
            evidence_text=evidence_text,
        )

        response_text = self._call_llm(user_prompt, system_prompt)
        parsed = self._parse_response(response_text)
        return ExecutionAnalysis(parsed)

    def _call_llm(self, user_prompt: str, system_prompt: str = "") -> str:
        """Call the LLM with retry logic."""
        client = self._get_client()
        last_error = None

        for attempt in range(self.config.max_retries):
            try:
                if self.config.provider == "anthropic":
                    response = client.messages.create(
                        model=self.config.model,
                        max_tokens=self.config.max_tokens,
                        temperature=self.config.temperature,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_prompt}],
                    )
                    return response.content[0].text
                else:
                    raise ValueError(f"Unsupported provider: {self.config.provider}")
            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))

        raise RuntimeError(f"LLM call failed after {self.config.max_retries} attempts: {last_error}")

    def _parse_response(self, response_text: str) -> dict:
        """Extract JSON from LLM response text."""
        # try direct parse first
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # try extracting JSON from markdown code block
        import re
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

        raise ValueError(
            f"Failed to parse ExecutionAnalysis JSON from LLM response.\n"
            f"Response (first 500 chars): {response_text[:500]}"
        )
