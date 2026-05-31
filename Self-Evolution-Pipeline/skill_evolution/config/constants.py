"""Constants for the Skill Evolution Pipeline.

Adapted from OpenSpace config/constants.py pattern.
"""
from pathlib import Path

# Config file names
CONFIG_DEFAULT = "default.yaml"
CONFIG_DEV = "dev.yaml"

# Log levels
LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Project root directory (Self-Evolution-Pipeline/)
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Default LLM settings
DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 120.0

# Pipeline defaults
DEFAULT_EVOLUTION_RATIO = 0.70
DEFAULT_TEST_RATIO = 0.30
DEFAULT_MIN_RELEVANCE_SCORE = 4

# LLM error backoff (seconds)
BACKOFF_RATE_LIMIT = [60, 90, 120]
BACKOFF_CONNECTION = [10, 20, 40]
BACKOFF_OVERLOAD = [5, 10, 20]

# Tool result limits
MAX_TOOL_RESULT_CHARS = 4000
MAX_CONVERSATION_ROUNDS = 10

__all__ = [
    "CONFIG_DEFAULT",
    "CONFIG_DEV",
    "LOG_LEVELS",
    "PROJECT_ROOT",
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "DEFAULT_EVOLUTION_RATIO",
    "DEFAULT_TEST_RATIO",
    "DEFAULT_MIN_RELEVANCE_SCORE",
    "BACKOFF_RATE_LIMIT",
    "BACKOFF_CONNECTION",
    "BACKOFF_OVERLOAD",
    "MAX_TOOL_RESULT_CHARS",
    "MAX_CONVERSATION_ROUNDS",
]
