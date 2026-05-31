"""SkillEvolver: processes evolution_suggestions serially to produce new skill versions.

Reference: OpenSpace evolver.py _evolve_fix() / _evolve_derived() flow.
Simplified: single LLM call per suggestion (no agent loop, no retry for now).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from skill_evolution.config.prompts import PromptLoader
from skill_evolution.config.settings import LLMConfig
from skill_evolution.llm.prompts import EVOLUTION_COMPLETE, EVOLUTION_FAILED
from skill_evolution.llm.evidence_analyzer import ExecutionAnalysis
from skill_evolution.models.evolution import EvolutionSuggestion, EvolutionType
from skill_evolution.evolution.patch import (
    fix_skill, derive_skill, create_skill,
    extract_change_summary, strip_markdown_fences,
    validate_skill_dir, collect_skill_snapshot, truncate,
    SkillEditResult, PatchType,
)


@dataclass
class EvolutionResult:
    """Result of processing one evolution suggestion."""
    suggestion: EvolutionSuggestion
    edit_result: Optional[SkillEditResult] = None
    change_summary: str = ""
    llm_output: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.edit_result is not None and self.edit_result.ok and not self.error


@dataclass
class EvolutionRunResult:
    """Result of processing all suggestions from one analysis."""
    analysis: ExecutionAnalysis
    results: list[EvolutionResult] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)


class SkillEvolver:
    """Processes evolution_suggestions serially, producing new skill versions."""

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

    def evolve(
        self,
        analysis: ExecutionAnalysis,
        skill_content: str,
        skill_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ) -> EvolutionRunResult:
        """Process all evolution_suggestions from an ExecutionAnalysis.

        Args:
            analysis: ExecutionAnalysis with evolution_suggestions
            skill_content: current skill content (SKILL.md text)
            skill_dir: path to skill directory on disk (for fix_skill)
            output_dir: directory to save evolved skills (for derive_skill)

        Returns:
            EvolutionRunResult with per-suggestion results
        """
        run_result = EvolutionRunResult(analysis=analysis)

        if not analysis.evolution_suggestions:
            print("  [EVOLVE] No evolution suggestions to process")
            return run_result

        print(f"\n[EVOLVE] Processing {len(analysis.evolution_suggestions)} suggestion(s)")

        for i, suggestion in enumerate(analysis.evolution_suggestions):
            print(f"\n  Suggestion {i+1}/{len(analysis.evolution_suggestions)}: "
                  f"[{suggestion.evolution_type.value}] {suggestion.direction[:60]}...")

            result = self._process_suggestion(
                suggestion, skill_content, skill_dir, output_dir,
            )
            run_result.results.append(result)

            if result.ok:
                print(f"    OK: {result.change_summary}")
            else:
                print(f"    FAILED: {result.error}")

        return run_result

    def _process_suggestion(
        self,
        suggestion: EvolutionSuggestion,
        skill_content: str,
        skill_dir: Optional[Path],
        output_dir: Optional[Path],
    ) -> EvolutionResult:
        """Process a single evolution suggestion."""
        result = EvolutionResult(suggestion=suggestion)

        # Build prompt based on type
        if suggestion.evolution_type == EvolutionType.FIX:
            prompt = self._build_fix_prompt(suggestion, skill_content)
        elif suggestion.evolution_type == EvolutionType.DERIVED:
            prompt = self._build_derived_prompt(suggestion, skill_content)
        else:
            result.error = f"Unsupported evolution type: {suggestion.evolution_type}"
            return result

        # Call LLM
        try:
            llm_output = self._call_llm(prompt)
        except Exception as e:
            result.error = f"LLM call failed: {e}"
            return result

        # Check for failure signal
        if EVOLUTION_FAILED in llm_output:
            reason = llm_output.split("Reason:")[-1].strip() if "Reason:" in llm_output else "unknown"
            result.error = f"LLM signaled failure: {reason}"
            result.llm_output = llm_output
            return result

        # Strip completion token and markdown fences
        clean_output = llm_output.replace(EVOLUTION_COMPLETE, "").strip()
        clean_output = strip_markdown_fences(clean_output)

        # Extract change summary
        clean_output, change_summary = extract_change_summary(clean_output)
        result.change_summary = change_summary
        result.llm_output = clean_output

        # Apply the edit
        try:
            if suggestion.evolution_type == EvolutionType.FIX:
                edit_result = self._apply_fix(clean_output, skill_dir)
            elif suggestion.evolution_type == EvolutionType.DERIVED:
                edit_result = self._apply_derived(
                    clean_output, skill_dir, output_dir, suggestion,
                )
            else:
                result.error = f"Cannot apply: unsupported type {suggestion.evolution_type}"
                return result

            result.edit_result = edit_result
            if not edit_result.ok:
                result.error = edit_result.error or "Edit failed"

        except Exception as e:
            result.error = f"Apply failed: {e}"

        return result

    def _build_fix_prompt(self, suggestion: EvolutionSuggestion, skill_content: str) -> str:
        """Build the FIX evolution prompt."""
        failure_context = self._format_failure_context(suggestion)
        template = self._prompt_loader.load("evolution_fix") if self._prompt_loader else ""
        return template.format(
            current_content=truncate(skill_content, 12000),
            direction=suggestion.direction,
            failure_context=failure_context,
            evolution_complete=EVOLUTION_COMPLETE,
            evolution_failed=EVOLUTION_FAILED,
        )

    def _build_derived_prompt(self, suggestion: EvolutionSuggestion, skill_content: str) -> str:
        """Build the DERIVED evolution prompt."""
        execution_insights = self._format_failure_context(suggestion)
        template = self._prompt_loader.load("evolution_derived") if self._prompt_loader else ""
        return template.format(
            parent_content=truncate(skill_content, 12000),
            direction=suggestion.direction,
            execution_insights=execution_insights,
            evolution_complete=EVOLUTION_COMPLETE,
            evolution_failed=EVOLUTION_FAILED,
        )

    def _format_failure_context(self, suggestion: EvolutionSuggestion) -> str:
        """Format evidence sessions into failure context text."""
        if not suggestion.evidence_sessions:
            return "(No specific session evidence available)"
        lines = [f"Evidence from {len(suggestion.evidence_sessions)} session(s):"]
        for sid in suggestion.evidence_sessions:
            lines.append(f"  - Session {sid[:12]}")
        return "\n".join(lines)

    def _apply_fix(self, content: str, skill_dir: Optional[Path]) -> SkillEditResult:
        """Apply a FIX edit to the skill directory."""
        if skill_dir is None:
            return SkillEditResult(error="No skill_dir provided for FIX")
        return fix_skill(skill_dir, content)

    def _apply_derived(
        self,
        content: str,
        skill_dir: Optional[Path],
        output_dir: Optional[Path],
        suggestion: EvolutionSuggestion,
    ) -> SkillEditResult:
        """Apply a DERIVED edit — create new skill in output_dir."""
        if output_dir is None:
            return SkillEditResult(error="No output_dir provided for DERIVED")

        # Determine target directory name
        target_name = f"{suggestion.target_skill_id or 'skill'}-enhanced"
        target_dir = output_dir / target_name

        if skill_dir and skill_dir.is_dir():
            return derive_skill(skill_dir, target_dir, content)
        else:
            return create_skill(target_dir, content)

    def _call_llm(self, user_prompt: str) -> str:
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
                        system="You are a skill editor. Output only the requested content, no explanations.",
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
