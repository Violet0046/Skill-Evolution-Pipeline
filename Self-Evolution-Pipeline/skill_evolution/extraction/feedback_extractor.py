"""Extract structured feedback from session messages.

Focuses on parsing retry/failure context from user messages that contain
review feedback, correction suggestions, and failure analysis.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from skill_evolution.models.session import CanonicalSession, ExecutionStatus
from skill_evolution.utils.logging import Logger

logger = Logger.get_logger(__name__)


class FeedbackExtractor:
    """Extracts and enriches feedback from session data."""

    def enrich_from_sessions_index(
        self,
        session: CanonicalSession,
        index_path: str,
    ) -> list[CanonicalSession]:
        """Load sessions.jsonl and match entries to enrich session feedback.

        Returns all sessions listed in the index (for the same skill).
        """
        entries = self._read_index(index_path)
        enriched = []
        for entry in entries:
            # each entry represents a session that used this skill
            skill_names = entry.get("mentioned_skills", [])
            # we're interested in sessions where our skill is mentioned
            enriched.append(entry)
        return enriched

    def _read_index(self, path: str) -> list[dict]:
        entries = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def extract_failure_patterns(
        self, sessions: list[CanonicalSession]
    ) -> dict[str, list[str]]:
        """Aggregate failure patterns across multiple sessions."""
        patterns: dict[str, list[str]] = {}

        for session in sessions:
            if session.execution.status == ExecutionStatus.SUCCESS:
                continue

            reason = session.feedback.failure_reason
            if reason:
                patterns.setdefault("failure_reasons", []).append(
                    f"[{session.session_id}] {reason}"
                )

            correction = session.feedback.correction_suggestion
            if correction:
                patterns.setdefault("corrections", []).append(
                    f"[{session.session_id}] {correction}"
                )

            retry = session.feedback.retry_reason
            if retry:
                patterns.setdefault("retry_reasons", []).append(
                    f"[{session.session_id}] {retry}"
                )

        return patterns

    def extract_rule_violations(
        self, sessions: list[CanonicalSession]
    ) -> list[dict]:
        """Extract specific rule violations from sessions."""
        violations = []

        for session in sessions:
            full_text = session.task_input.raw_content

            # pattern: "X超过规则上限(Y)"
            m = re.search(r"(\S+?)\s*超过.*?上限.*?(\d+)", full_text)
            if m:
                violations.append({
                    "session_id": session.session_id,
                    "rule": f"数量限制: {m.group(1)} <= {m.group(2)}",
                    "description": full_text[m.start():m.end() + 50],
                })

            # pattern: constraint violations in task description
            constraint_patterns = [
                (r"最多.*?(\d+)\s*条", "数量限制"),
                (r"不能超过.*?(\d+)", "上限限制"),
                (r"必须.*?(<=|>=|<|>|=)\s*(\d+)", "数值约束"),
            ]
            for pattern, category in constraint_patterns:
                m = re.search(pattern, full_text)
                if m:
                    violations.append({
                        "session_id": session.session_id,
                        "rule": f"{category}: {m.group(0)}",
                        "category": category,
                    })

        return violations
