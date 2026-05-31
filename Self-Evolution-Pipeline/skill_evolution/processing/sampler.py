"""Split filtered sessions into evolution and test sets.

No sampling or discarding — all quality-filtered sessions are kept.
Only splits into evolution (70%) and test (30%) by status group.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from skill_evolution.models.session import CanonicalSession
from skill_evolution.config.settings import SamplingConfig


@dataclass
class SamplingResult:
    """Result of the split process."""
    evolution_set: list[CanonicalSession]
    test_set: list[CanonicalSession]

    @property
    def total_count(self) -> int:
        return len(self.evolution_set) + len(self.test_set)

    def summary(self) -> dict:
        return {
            "evolution_count": len(self.evolution_set),
            "test_count": len(self.test_set),
            "total": self.total_count,
        }


class DatasetSplitter:
    """Splits filtered sessions into evolution and test sets.

    All sessions are kept — no discarding. The split is done
    by execution status to ensure both sets cover all scenarios.
    """

    def __init__(self, config: SamplingConfig, seed: int = 42):
        self.config = config
        self.seed = seed

    def split(
        self, groups: dict[str, list[CanonicalSession]]
    ) -> SamplingResult:
        """Split all filtered sessions into evolution and test sets.

        Args:
            groups: Dict with keys "failed", "retry_success", "success"
                    mapping to lists of CanonicalSession.

        Returns:
            SamplingResult with evolution_set and test_set.
        """
        rng = random.Random(self.seed)

        # merge all groups (keep everything)
        all_sessions: list[CanonicalSession] = []
        for group_sessions in groups.values():
            all_sessions.extend(group_sessions)

        if not all_sessions:
            return SamplingResult(evolution_set=[], test_set=[])

        # split by status group to ensure coverage
        by_status: dict[str, list[CanonicalSession]] = {}
        for s in all_sessions:
            status = s.execution.status.value
            by_status.setdefault(status, []).append(s)

        evolution = []
        test = []

        for status_group in by_status.values():
            if len(status_group) == 1:
                # only 1 session in this group -> goes to evolution
                evolution.extend(status_group)
            else:
                rng.shuffle(status_group)
                split_idx = max(1, int(len(status_group) * self.config.evolution_ratio))
                split_idx = min(split_idx, len(status_group) - 1)
                evolution.extend(status_group[:split_idx])
                test.extend(status_group[split_idx:])

        return SamplingResult(evolution_set=evolution, test_set=test)
