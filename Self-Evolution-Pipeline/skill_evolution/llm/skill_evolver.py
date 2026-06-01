"""SkillEvolver: processes evolution_suggestions serially or in parallel.

Uses LLMWithTools for multi-turn tool-use conversations. The LLM can call
read_session_summary, read_session_messages, read_session_tool_detail to
examine the evidence sessions before producing edits.

All outputs go to staging/{skill_name}/changes/{run_id}/ directory:
- {id}.change: concise change description (DELETE/ADD/WHERE) for each suggestion
- versions.json: manifest of all changes

This design ensures:
- No in-place modification of original skill
- All suggestions produce changes that can be merged later by merge LLM
- Full audit trail of changes
"""
from __future__ import annotations

import asyncio
import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from skill_evolution.config.prompts import PromptLoader
from skill_evolution.config.settings import LLMConfig
from skill_evolution.config.constants import MAX_CONVERSATION_ROUNDS
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
    apply_search_replace, apply_update_chunks, parse_patch,
    parse_multi_file_full,
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


@dataclass
class ParsedChange:
    """Structured representation of a parsed change from LLM output."""
    change_id: str = ""
    summary: str = ""
    anchor_type: str = "heading"
    anchor_selector: str = ""
    operation: str = "INSERT_SUBSECTION"
    new_content: str = ""
    old_content: str = ""  # optional, for DELETE/REPLACE operations


class SkillEvolver:
    """Processes evolution_suggestions serially, producing patch files.

    All patches are output to staging/{skill_name}/patches/{run_id}/ directory.
    This ensures no in-place modification of the original skill.
    """

    def __init__(
        self,
        config: LLMConfig,
        prompt_loader: PromptLoader | None = None,
        sessions: list[CanonicalSession] | None = None,
        run_id: str | None = None,
    ):
        self.config = config
        self._prompt_loader = prompt_loader
        self._sessions = sessions or []
        self._run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self._changes_dir: Path | None = None
        self._base_skill_content: str = ""

    def evolve(
        self,
        analysis: ExecutionAnalysis,
        skill_content: str,
        skill_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ) -> EvolutionRunResult:
        """Process all evolution_suggestions from an ExecutionAnalysis.

        All patches are written to output_dir/patches/{run_id}/ directory.
        A versions.json manifest is created at the end.
        """
        run_result = EvolutionRunResult(analysis=analysis)

        if not analysis.evolution_suggestions:
            logger.info("No evolution suggestions to process")
            return run_result

        # Set up changes directory
        self._base_skill_content = skill_content
        if output_dir:
            self._changes_dir = output_dir / "changes" / self._run_id
            self._changes_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Changes directory: {self._changes_dir}")

        logger.info(f"Processing {len(analysis.evolution_suggestions)} suggestion(s)")

        for i, suggestion in enumerate(analysis.evolution_suggestions):
            patch_id = f"{i+1:03d}"
            logger.info(f"Suggestion {i+1}/{len(analysis.evolution_suggestions)}: "
                        f"[{suggestion.evolution_type.value}] {suggestion.direction[:60]}...")

            result = self._process_suggestion(
                suggestion, skill_content, skill_dir, output_dir, patch_id,
            )
            run_result.results.append(result)

            if result.ok:
                logger.info(f"  OK: {result.change_summary}")
            else:
                logger.warning(f"  FAILED: {result.error}")

        # Generate versions.json
        if self._changes_dir:
            self._generate_versions_json(run_result, skill_dir)

        return run_result

    def _generate_versions_json(
        self,
        run_result: EvolutionRunResult,
        skill_dir: Optional[Path],
    ) -> None:
        """Generate versions.json manifest of all patches."""
        versions = {
            "run_id": self._run_id,
            "base_skill": str(skill_dir / "SKILL.md") if skill_dir else None,
            "execution_analysis_summary": {
                "total_sessions": run_result.analysis.total_sessions,
                "success_rate": run_result.analysis.success_rate,
                "suggestion_count": len(run_result.analysis.evolution_suggestions),
            },
            "patches": [],
        }

        for i, r in enumerate(run_result.results):
            patch_id = f"{i+1:03d}"
            versions["patches"].append({
                "id": patch_id,
                "type": r.suggestion.evolution_type.value,
                "direction": r.suggestion.direction,
                "change_summary": r.change_summary,
                "change_file": f"{patch_id}.change",
                "status": "ok" if r.ok else "failed",
                "error": r.error if not r.ok else None,
                "evidence_sessions": r.suggestion.evidence_sessions,
                "llm_output": r.llm_output[:500] if r.llm_output else None,  # Truncate for manifest
            })

        versions_path = self._changes_dir / "versions.json"
        with open(versions_path, "w", encoding="utf-8") as f:
            json.dump(versions, f, ensure_ascii=False, indent=2)
        logger.info(f"Generated versions.json: {versions_path}")

    def _process_suggestion(
        self,
        suggestion: EvolutionSuggestion,
        skill_content: str,
        skill_dir: Optional[Path],
        output_dir: Optional[Path],
        patch_id: str = "001",
    ) -> EvolutionResult:
        """Process a single evolution suggestion and output a diff file."""
        result = EvolutionResult(suggestion=suggestion)

        # Use the original base content (not modified by previous suggestions)
        current_content = self._base_skill_content
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

        # Apply the edit (now outputs diff files, not in-place modification)
        try:
            if suggestion.evolution_type == EvolutionType.FIX:
                edit_result = self._apply_fix(clean_output, current_content, patch_id, suggestion)
            elif suggestion.evolution_type == EvolutionType.DERIVED:
                edit_result = self._apply_derived(
                    clean_output, current_content, patch_id, suggestion,
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

        # Inject round limit info so LLM can self-manage its exploration budget
        round_limit_hint = (
            f"\n\n## Round Budget\n"
            f"You have a maximum of {MAX_CONVERSATION_ROUNDS} rounds total (including this one). "
            f"Each tool call costs 1 round. Your final text response also costs 1 round. "
            f"Plan accordingly: if you use too many tool calls, you won't have a round left to produce output. "
            f"Recommended: use 0-3 tool calls, then produce your edit immediately."
        )
        system_prompt = system_prompt + round_limit_hint

        # Use only evidence sessions for tools (not all sessions)
        registry = SessionToolRegistry(evidence_sessions)
        llm = LLMWithTools(self.config, tools=SESSION_TOOLS)

        for tool_def in SESSION_TOOLS:
            name = tool_def["name"]
            handler = registry.get_handler(name)
            if handler:
                llm.register_tool(name, handler)

        messages = [{"role": "user", "content": user_prompt}]
        return llm.run_conversation(system=system_prompt, messages=messages, max_rounds=MAX_CONVERSATION_ROUNDS)

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

    def _apply_fix(
        self,
        llm_output: str,
        base_content: str,
        patch_id: str,
        suggestion: EvolutionSuggestion,
    ) -> SkillEditResult:
        """Generate a .change file for FIX suggestion.

        Outputs:
        - {patch_id}.change: structured change description (YAML format)
        - {patch_id}.raw: raw LLM output for debugging
        """
        if not self._changes_dir:
            return SkillEditResult(error="No changes_dir configured")

        # Always save raw LLM output for debugging
        raw_file = self._changes_dir / f"{patch_id}.raw"
        with open(raw_file, "w", encoding="utf-8") as f:
            f.write(llm_output)

        # Parse the structured change format
        parsed = self._try_parse_change_format(llm_output)

        if parsed is None:
            logger.warning(f"Failed to parse change format, saved raw to {raw_file}")
            return SkillEditResult(error="Failed to parse LLM change output (saved raw)")

        # Build suggestion_id from target_skill_id and direction hash
        suggestion_id = f"fix-{suggestion.target_skill_id}" if suggestion.target_skill_id else f"fix-{patch_id}"

        # Extract change_id from patch_id (e.g., "001" from "001")
        change_id = patch_id.zfill(3)

        # Build final YAML
        change_yaml = self._build_change_yaml(
            parsed=parsed,
            suggestion_id=suggestion_id,
            suggestion_type="fix",
            priority="high",  # TODO: derive from analysis
            change_id=change_id,
        )

        # Write .change file
        change_file = self._changes_dir / f"{patch_id}.change"
        with open(change_file, "w", encoding="utf-8") as f:
            f.write(change_yaml)

        logger.info(f"Wrote change: {change_file}")

        return SkillEditResult(
            skill_dir=self._changes_dir,
            content_diff=change_yaml,
        )

    def _apply_derived(
        self,
        llm_output: str,
        base_content: str,
        patch_id: str,
        suggestion: EvolutionSuggestion,
    ) -> SkillEditResult:
        """Generate a .change file for DERIVED suggestion.

        Outputs:
        - {patch_id}.change: structured change description (YAML format)
        - {patch_id}.raw: raw LLM output for debugging
        """
        if not self._changes_dir:
            return SkillEditResult(error="No changes_dir configured")

        # Always save raw LLM output for debugging
        raw_file = self._changes_dir / f"{patch_id}.raw"
        with open(raw_file, "w", encoding="utf-8") as f:
            f.write(llm_output)

        # Parse the structured change format
        parsed = self._try_parse_change_format(llm_output)

        if parsed is None:
            logger.warning(f"Failed to parse change format, saved raw to {raw_file}")
            return SkillEditResult(error="Failed to parse LLM change output (saved raw)")

        # Build suggestion_id from target_skill_id and direction hash
        suggestion_id = f"derived-{suggestion.target_skill_id}" if suggestion.target_skill_id else f"derived-{patch_id}"

        # Extract change_id from patch_id (e.g., "001" from "001")
        change_id = patch_id.zfill(3)

        # Build final YAML
        change_yaml = self._build_change_yaml(
            parsed=parsed,
            suggestion_id=suggestion_id,
            suggestion_type="derived",
            priority="medium",  # TODO: derive from analysis
            change_id=change_id,
        )

        # Write .change file
        change_file = self._changes_dir / f"{patch_id}.change"
        with open(change_file, "w", encoding="utf-8") as f:
            f.write(change_yaml)

        logger.info(f"Wrote change: {change_file}")

        return SkillEditResult(
            skill_dir=self._changes_dir,
            content_diff=change_yaml,
        )

    def _try_parse_evolution_output(self, llm_output: str, base_content: str) -> str | None:
        """Try to parse LLM output into complete skill content.

        Handles multiple formats:
        1. FULL format: Complete new file content (starts with ---)
        2. FILES format: *** Begin Files ... *** End Files (multi-file)
        3. PATCH format: *** Begin Patch / *** Update File / @@ anchor / - / +
        4. DIFF format: SEARCH/REPLACE blocks that get applied to base

        Returns the updated content, or None if parsing fails.
        """
        # Strip markdown fences if present
        clean = strip_markdown_fences(llm_output)

        # Check if it's a FULL format (complete file content, starts with ---)
        if clean.strip().startswith("---"):
            logger.info("Detected FULL format (--- header)")
            return clean.strip()

        # Try FILES format (*** Begin Files ... *** End Files)
        if "*** Begin Files" in clean:
            logger.info("Detected FILES format (*** Begin Files)")
            try:
                files = parse_multi_file_full(clean)
                if "SKILL.md" in files:
                    logger.info(f"Successfully parsed FILES format, extracted SKILL.md ({len(files.get('SKILL.md', ''))} chars)")
                    return files["SKILL.md"]
                elif files:
                    # If no SKILL.md but other files exist, use first one
                    first_key = next(iter(files))
                    logger.info(f"FILES format: no SKILL.md, using first file: {first_key}")
                    return files[first_key]
                else:
                    logger.warning("*** Begin Files format found but no files extracted")
            except Exception as e:
                logger.warning(f"Failed to parse FILES format: {e}")

        # Try PATCH format (*** Begin Patch)
        patch_failed = False
        if "*** Begin Patch" in clean:
            try:
                result = self._apply_patch_format(clean, base_content)
                if result:
                    logger.info("Successfully applied PATCH format")
                    return result
            except Exception as e:
                logger.warning(f"Failed to apply PATCH format: {e}")
                patch_failed = True

        # If PATCH failed, try to extract a complete file content from LLM output as fallback
        # This handles cases where LLM outputs PATCH format with placeholder issues
        if patch_failed:
            fallback = self._try_extract_complete_skill_from_patch(clean)
            if fallback:
                logger.info("PATCH failed but extracted complete skill content from output")
                return fallback

        # Try SEARCH/REPLACE blocks
        try:
            updated, num_applied, error = apply_search_replace(clean, base_content)
            if error:
                logger.warning(f"Failed to apply search/replace: {error}")
                return None
            if num_applied == 0:
                logger.warning("No SEARCH/REPLACE blocks found in LLM output")
                return None
            logger.info(f"Applied {num_applied} SEARCH/REPLACE block(s)")
            return updated
        except Exception as e:
            logger.warning(f"Error applying search/replace: {e}")
            return None

    def _try_extract_complete_skill_from_patch(self, patch_text: str) -> str | None:
        """Try to extract a complete SKILL.md from PATCH format output as fallback.

        This handles cases where LLM outputs a PATCH format but with placeholder
        issues like <unchanged context line> that prevent proper patch application.
        In such cases, we try to find and extract the complete skill content.
        """
        import re

        # Look for *** File: SKILL.md followed by complete content
        file_pattern = re.compile(
            r"\*\*\*\s*File:\s*SKILL\.md\s*\n(.*?)(?=\n\s*\*\*\*|\n\*\*\* End|\Z)",
            re.DOTALL
        )
        match = file_pattern.search(patch_text)
        if match:
            content = match.group(1).strip()
            # Verify it looks like a valid skill (starts with ---)
            if content.startswith("---"):
                return content

        # Also look for content that starts with --- anywhere in the patch
        # This handles cases where LLM includes complete file content inline
        lines = patch_text.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == "---":
                # Found start of frontmatter, try to extract complete content
                potential = "\n".join(lines[i:])
                # Check if it looks complete (has matching closing ---)
                if potential.count("---") >= 2:
                    return potential

        return None

    def _try_parse_change_format(self, llm_output: str) -> ParsedChange | None:
        """Parse the new structured YAML-like change format.

        Expected format:
            # Change 001
            summary: <description>

            anchor:
              type: heading
              selector: "<exact heading text>"

            operation: INSERT_SUBSECTION

            new_content: |
              <content>

        Returns ParsedChange object or None if parsing fails.
        """
        clean = strip_markdown_fences(llm_output)
        lines = clean.split("\n")

        result = ParsedChange()
        in_new_content = False
        new_content_lines = []
        state = "init"  # init -> summary -> anchor -> operation -> content
        change_count = 0  # Track how many change blocks we've seen

        # Patterns that indicate LLM thinking/retry - stop parsing when seen
        stop_patterns = [
            "Wait, I need to",
            "Let me re-output",
            "Let me reconsider",
            "Actually, let me",
        ]

        i = 0
        while i < len(lines):
            line = lines[i].rstrip()

            # Skip completion tokens
            if line.strip() in (EVOLUTION_COMPLETE, EVOLUTION_FAILED):
                i += 1
                continue

            # Check for stop patterns (LLM thinking/retry)
            should_stop = False
            for pattern in stop_patterns:
                if pattern in line:
                    logger.info(f"Detected LLM thinking pattern '{pattern}', stopping parse")
                    should_stop = True
                    break
            if should_stop:
                break

            # Parse change_id from comment (only count the first change block)
            if line.startswith("# Change "):
                change_count += 1
                if change_count > 1:
                    # We've already parsed one change, stop here
                    logger.info("Found second change block, stopping")
                    break
                parts = line.split()
                if len(parts) >= 3:
                    result.change_id = parts[2].strip()
                i += 1
                continue

            # Parse key-value pairs
            if line.startswith("summary:"):
                result.summary = line.split(":", 1)[1].strip()
                state = "summary"
            elif line.startswith("anchor:"):
                state = "anchor"
            elif line.startswith("  type:"):
                if state == "anchor":
                    result.anchor_type = line.split(":", 1)[1].strip()
            elif line.startswith("  selector:"):
                if state == "anchor":
                    # Extract content within quotes or use as-is
                    selector = line.split(":", 1)[1].strip()
                    if selector.startswith('"') and selector.endswith('"'):
                        selector = selector[1:-1]
                    elif selector.startswith("'") and selector.endswith("'"):
                        selector = selector[1:-1]
                    result.anchor_selector = selector
            elif line.startswith("operation:"):
                result.operation = line.split(":", 1)[1].strip()
                state = "operation"
            elif line.startswith("new_content:"):
                state = "new_content"
                # Check for inline content after |
                content_part = line.split(":", 1)[1].strip()
                if content_part.startswith("|"):
                    # Multi-line content follows
                    pass
                i += 1
                continue
            elif line.startswith("old_content:"):
                state = "old_content"
                i += 1
                continue
            elif in_new_content:
                # Continue collecting multi-line content
                # Stop at empty line followed by non-indented content (end of block)
                if not line.strip():
                    # Empty line in content - check if next line is continuation
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].rstrip()
                        if next_line and not next_line[0].isspace():
                            # Next line is not indented, this is end of content block
                            in_new_content = False
                            result.new_content = "\n".join(new_content_lines).rstrip()
                            continue
                    new_content_lines.append(line)
                elif line.startswith("#") or (line.strip() and not line[0].isspace()):
                    # Hit next section, stop collecting
                    in_new_content = False
                    result.new_content = "\n".join(new_content_lines).rstrip()
                    # Don't skip, reprocess this line
                    continue
                else:
                    new_content_lines.append(line)
            elif state == "new_content":
                # Start collecting multi-line content
                if line.strip():
                    in_new_content = True
                    new_content_lines.append(line)
            else:
                # Check if we're starting multi-line content
                if state == "new_content" and (line.startswith(" ") or line.startswith("\t")):
                    in_new_content = True
                    new_content_lines.append(line.lstrip())

            i += 1

        # Handle content that extends to end of file
        if in_new_content and new_content_lines:
            result.new_content = "\n".join(new_content_lines).rstrip()

        # Validate required fields
        if not result.summary:
            logger.warning("No summary found in change format")
            return None
        if not result.anchor_selector:
            logger.warning("No anchor.selector found in change format")
            return None
        if not result.new_content and result.operation != "DELETE":
            logger.warning("No new_content found in change format")
            return None

        return result

    def _build_change_yaml(
        self,
        parsed: ParsedChange,
        suggestion_id: str,
        suggestion_type: str,
        priority: str = "medium",
        change_id: str = "001",
    ) -> str:
        """Build the final YAML change file content.

        Args:
            parsed: ParsedChange from LLM output
            suggestion_id: Unique identifier for this suggestion
            suggestion_type: "fix" or "derived"
            priority: "low" | "medium" | "high" | "critical"
            change_id: Numeric ID for this change (e.g., "001")

        Returns:
            YAML-formatted change file content
        """
        lines = [
            f"# Change {change_id}",
            f"suggestion_id: {suggestion_id}",
            f"suggestion_type: {suggestion_type}",
            f"priority: {priority}",
            "",
            "summary: " + parsed.summary,
            "",
            "anchor:",
            f"  type: {parsed.anchor_type}",
            f"  selector: \"{parsed.anchor_selector}\"",
            "",
            f"operation: {parsed.operation}",
        ]

        if parsed.new_content:
            lines.extend(["", "new_content: |"])
            for content_line in parsed.new_content.split("\n"):
                lines.append(f"  {content_line}")

        if parsed.old_content:
            lines.extend(["", "old_content: |"])
            for content_line in parsed.old_content.split("\n"):
                lines.append(f"  {content_line}")

        return "\n".join(lines)

    def _apply_patch_format(self, patch_text: str, base_content: str) -> str | None:
        """Apply *** Begin Patch format to base content.

        This format uses:
        *** Begin Patch
        *** Update File: SKILL.md
        @@ <anchor line>
        -<line to remove>
        +<line to add>
        *** End Patch

        If anchor cannot be found exactly, tries fuzzy matching.
        """
        # Check if this is a single-file patch for SKILL.md
        if "*** Update File: SKILL.md" not in patch_text:
            logger.warning("PATCH format: only SKILL.md updates supported")
            return None

        try:
            parsed = parse_patch(patch_text)
            if not parsed.hunks:
                logger.warning("No hunks found in PATCH format")
                return None

            # Find the SKILL.md hunk
            skill_hunk = None
            for hunk in parsed.hunks:
                if hunk.path == "SKILL.md" and hunk.type == "update":
                    skill_hunk = hunk
                    break

            if not skill_hunk:
                logger.warning("No SKILL.md update hunk found in PATCH")
                return None

            # Try to apply the update chunks
            try:
                updated = apply_update_chunks("SKILL.md", base_content, skill_hunk.chunks)
                return updated
            except Exception as e:
                # If anchor matching fails, try to fix the anchor with fuzzy matching
                error_msg = str(e)
                if "Cannot locate anchor" in error_msg or "Cannot find expected lines" in error_msg:
                    logger.warning(f"Anchor match failed, trying fuzzy matching: {e}")
                    updated = self._apply_patch_with_fuzzy_anchor(patch_text, base_content)
                    if updated:
                        logger.info("Fuzzy anchor matching succeeded")
                        return updated
                return None

        except Exception as e:
            logger.warning(f"Error parsing PATCH format: {e}")
            return None

    def _apply_patch_with_fuzzy_anchor(self, patch_text: str, base_content: str) -> str | None:
        """Apply PATCH format with fuzzy anchor matching.

        When exact anchor matching fails, finds the most similar line
        in the base content and patches using that as the anchor.
        """
        import difflib

        # Parse the patch to get anchor information
        lines = patch_text.split("\n")
        anchor_line = None
        for i, line in enumerate(lines):
            if line.startswith("@@"):
                # Extract the anchor line after @@
                anchor_line = line[2:].strip()
                break

        if not anchor_line:
            return None

        # Find similar lines in the base content
        base_lines = base_content.split("\n")
        best_match = None
        best_ratio = 0
        best_idx = -1

        for i, line in enumerate(base_lines):
            # Only consider heading lines or significant content lines
            if line.strip().startswith("#") or len(line.strip()) > 10:
                ratio = difflib.SequenceMatcher(None, anchor_line.strip(), line.strip()).ratio()
                if ratio > best_ratio and ratio > 0.6:  # At least 60% similar
                    best_ratio = ratio
                    best_match = line.strip()
                    best_idx = i

        if best_idx == -1:
            logger.warning(f"No similar anchor found for '{anchor_line}'")
            return None

        logger.info(f"Fuzzy matched anchor: '{anchor_line}' -> '{best_match}' (line {best_idx+1}, ratio {best_ratio:.2f})")

        # Build a new patch with the corrected anchor
        fixed_lines = []
        for line in lines:
            if line.startswith("@@"):
                fixed_lines.append("@@ " + best_match)
            else:
                fixed_lines.append(line)

        fixed_patch = "\n".join(fixed_lines)

        try:
            parsed = parse_patch(fixed_patch)
            if parsed.hunks:
                for hunk in parsed.hunks:
                    if hunk.path == "SKILL.md" and hunk.type == "update":
                        return apply_update_chunks("SKILL.md", base_content, hunk.chunks)
        except Exception as e:
            logger.warning(f"Failed to apply patch with fuzzy anchor: {e}")
            return None

        return None

    def _generate_diff(self, old_content: str, new_content: str) -> str:
        """Generate a unified diff between old and new content."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff_lines = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="a/SKILL.md",
            tofile="b/SKILL.md",
            n=3,
        )
        return "".join(diff_lines)

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
