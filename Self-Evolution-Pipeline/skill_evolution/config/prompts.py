"""Prompt template loader — reads .txt files from the prompts/ directory."""
from __future__ import annotations

from pathlib import Path


class PromptLoader:
    """Load prompt templates from disk, with built-in fallback."""

    def __init__(self, prompts_dir: Path):
        self._dir = prompts_dir

    def load(self, name: str) -> str:
        """Load a prompt template by name (without .txt extension).

        Returns the file content if found, otherwise raises FileNotFoundError.
        """
        path = self._dir / f"{name}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Prompt template not found: {path}")
