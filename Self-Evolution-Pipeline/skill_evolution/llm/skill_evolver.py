"""SkillEvolver: processes evolution_suggestions serially or in parallel.

Uses LLMWithTools for multi-turn tool-use conversations. The LLM can call
read_session_summary, read_session_messages, read_session_tool_detail to
examine the evidence sessions before producing edits.

Supports two modes:
- evolve(): serial processing (default, safe for FIX that modifies in-place)
- evolve_parallel(): concurrent processing via asyncio.gather (for DERIVED suggestions)
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from skill_evolution.config.prompts import PromptLoader
from skill_evolution.config.settings import LLMConfig
from skill_evolution.llm.base import LLMWithTools
from skill_evolution.llm.prompts import EVOLUTION_COMPLETE, EVOLUTION_FAILED
from skill_evolution.llm.tools import SESSION_TOOLS, SessionToolRegistry
from skill_evolution.llm.evidence_analyzer import ExecutionAnalysis
from skill_evolution.models.evolution import EvolutionSuggestion, EvolutionType
from skill_evolution.models.session import CanonicalSession
from skill_evolution.utils.logging import Logger
from skill_evolution.evolution.patch import (
    fix_skill, derive_skill, create_skill,
    extract_change_summary, strip_markdown_fences,
    validate_skill_dir, collect_skill_snapshot, truncate,
    SkillEditResult, PatchType,
)

logger = Logger.get_logger(__name__)


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

    def __init__(
        self,
        config: LLMConfig,
        prompt_loader: PromptLoader | None = None,
        sessions: list[CanonicalSession] | None = None,
    ):
        self.config = config
        self._prompt_loader = prompt_loader
        self._sessions = sessions or []

    def evolve(
        self,
        analysis: ExecutionAnalysis,
        skill_content: str,
        skill_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ) -> EvolutionRunResult:
        """Process all evolution_suggestions from an ExecutionAnalysis."""
        run_result = EvolutionRunResult(analysis=analysis)

        if not analysis.evolution_suggestions:
            logger.info("No evolution suggestions to process")
            return run_result

        logger.info(f"Processing {len(analysis.evolution_suggestions)} suggestion(s)")

        for i, suggestion in enumerate(analysis.evolution_suggestions):
            logger.info(f"Suggestion {i+1}/{len(analysis.evolution_suggestions)}: "
                        f"[{suggestion.evolution_type.value}] {suggestion.direction[:60]}...")

            result = self._process_suggestion(
                suggestion, skill_content, skill_dir, output_dir,
            )
            run_result.results.append(result)

            if result.ok:
                logger.info(f"  OK: {result.change_summary}")
            else:
                logger.warning(f"  FAILED: {result.error}")

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

        # Re-read SKILL.md from disk if available (previous suggestion may have modified it)
        current_content = skill_content
        if skill_dir and skill_dir.is_dir():
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                current_content = skill_file.read_text(encoding="utf-8")

        # Build prompt based on type
        if suggestion.evolution_type == EvolutionType.FIX:
            prompt = self._build_fix_prompt(suggestion, current_content)
        elif suggestion.evolution_type == EvolutionType.DERIVED:
            prompt = self._build_derived_prompt(suggestion, current_content)
        else:
            result.error = f"Unsupported evolution type: {suggestion.evolution_type}"
            return result

        # Build session list for this suggestion (evidence sessions only)
        evidence_sessions = self._resolve_evidence_sessions(suggestion)

        # Call LLM with tools
        try:
            llm_output = self._call_llm_with_tools(prompt, evidence_sessions)
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

    def _resolve_evidence_sessions(self, suggestion: EvolutionSuggestion) -> list[CanonicalSession]:
        """Resolve evidence sessions from paths or IDs."""
        if not self._sessions:
            return []

        # Build lookup by path and ID
        by_path = {}
        by_id = {}
        for s in self._sessions:
            by_id[s.session_id] = s
            by_id[s.session_id[:12]] = s
            path = s.metadata.get("file_path", "")
            if path:
                by_path[path] = s

        resolved = []
        seen = set()

        # First try matching by paths
        for path in suggestion.evidence_session_paths:
            if path in by_path and path not in seen:
                resolved.append(by_path[path])
                seen.add(path)

        # Then try matching by IDs
        for sid in suggestion.evidence_sessions:
            if sid in by_id and sid not in seen:
                resolved.append(by_id[sid])
                seen.add(sid)

        return resolved

    def _call_llm_with_tools(
        self, user_prompt: str, evidence_sessions: list[CanonicalSession]
    ) -> str:
        """Call LLM with tool-use support for examining sessions."""
        system_prompt = self._prompt_loader.load("evolution_system") if self._prompt_loader else ""

        # Use only evidence sessions for tools (not all sessions)
        registry = SessionToolRegistry(evidence_sessions)
        llm = LLMWithTools(self.config, tools=SESSION_TOOLS)

        for tool_def in SESSION_TOOLS:
            name = tool_def["name"]
            handler = registry.get_handler(name)
            if handler:
                llm.register_tool(name, handler)

        messages = [{"role": "user", "content": user_prompt}]
        return llm.run_conversation(system=system_prompt, messages=messages, max_rounds=5)

    def _build_fix_prompt(self, suggestion: EvolutionSuggestion, skill_content: str) -> str:
        """Build the FIX evolution prompt."""
        failure_context = self._format_failure_context(suggestion)
        heading_index = self._extract_headings(skill_content)
        template = self._prompt_loader.load("evolution_fix") if self._prompt_loader else ""
        return template.format(
            current_content=truncate(skill_content, 12000),
            direction=suggestion.direction,
            failure_context=failure_context,
            heading_index=heading_index,
            evolution_complete=EVOLUTION_COMPLETE,
            evolution_failed=EVOLUTION_FAILED,
        )

    def _build_derived_prompt(self, suggestion: EvolutionSuggestion, skill_content: str) -> str:
        """Build the DERIVED evolution prompt."""
        execution_insights = self._format_failure_context(suggestion)
        heading_index = self._extract_headings(skill_content)
        template = self._prompt_loader.load("evolution_derived") if self._prompt_loader else ""
        return template.format(
            parent_content=truncate(skill_content, 12000),
            direction=suggestion.direction,
            execution_insights=execution_insights,
            heading_index=heading_index,
            evolution_complete=EVOLUTION_COMPLETE,
            evolution_failed=EVOLUTION_FAILED,
        )

    @staticmethod
    def _extract_headings(content: str) -> str:
        """Extract markdown headings from content for anchor reference."""
        headings = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                headings.append(stripped)
        if not headings:
            return "(no headings found)"
        return "\n".join(headings)

    def _format_failure_context(self, suggestion: EvolutionSuggestion) -> str:
        """Format evidence sessions into failure context text with paths."""
        if not suggestion.evidence_sessions and not suggestion.evidence_session_paths:
            return "(No specific session evidence available)"

        lines = [f"Evidence from {len(suggestion.evidence_sessions)} session(s):"]
        for i, sid in enumerate(suggestion.evidence_sessions):
            path = suggestion.evidence_session_paths[i] if i < len(suggestion.evidence_session_paths) else ""
            if path:
                lines.append(f"  - Session {sid[:12]} (path: {path})")
            else:
                lines.append(f"  - Session {sid[:12]}")

        lines.append("")
        lines.append("You can use read_session_summary, read_session_messages, "
                      "and read_session_tool_detail tools to examine these sessions in detail.")
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

    async def evolve_parallel(
        self,
        analysis: ExecutionAnalysis,
        skill_content: str,
        skill_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        max_concurrent: int = 3,
    ) -> EvolutionRunResult:
        """Process evolution_suggestions concurrently via asyncio.gather.

        FIX suggestions are serialized (they modify in-place).
        DERIVED/CAPTURED suggestions are processed concurrently.
        """
        run_result = EvolutionRunResult(analysis=analysis)

        if not analysis.evolution_suggestions:
            logger.info("No evolution suggestions to process")
            return run_result

        suggestions = analysis.evolution_suggestions

        # Separate FIX (serial) from DERIVED/CAPTURED (parallel)
        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        parallel_suggestions = [s for s in suggestions if s.evolution_type != EvolutionType.FIX]

        logger.info(
            f"Processing {len(suggestions)} suggestions: "
            f"{len(fix_suggestions)} FIX (serial), {len(parallel_suggestions)} parallel"
        )

        # Process FIX suggestions serially (they modify in-place)
        for suggestion in fix_suggestions:
            logger.info(f"[FIX] {suggestion.direction[:60]}...")
            result = self._process_suggestion(suggestion, skill_content, skill_dir, output_dir)
            run_result.results.append(result)
            if result.ok:
                logger.info(f"  OK: {result.change_summary}")
            else:
                logger.warning(f"  FAILED: {result.error}")

        # Process DERIVED/CAPTURED suggestions concurrently
        if parallel_suggestions:
            sem = asyncio.Semaphore(max_concurrent)

            async def _process_one(s: EvolutionSuggestion) -> EvolutionResult:
                async with sem:
                    # Run in executor to avoid blocking the event loop
                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(
                        None, self._process_suggestion, s, skill_content, skill_dir, output_dir,
                    )

            tasks = [_process_one(s) for s in parallel_suggestions]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for s, r in zip(parallel_suggestions, results):
                if isinstance(r, Exception):
                    err_result = EvolutionResult(suggestion=s, error=str(r))
                    run_result.results.append(err_result)
                    logger.warning(f"  FAILED: {r}")
                else:
                    run_result.results.append(r)
                    if r.ok:
                        logger.info(f"  OK: {r.change_summary}")
                    else:
                        logger.warning(f"  FAILED: {r.error}")

        return run_result
