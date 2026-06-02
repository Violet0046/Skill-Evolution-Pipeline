"""CLI argument parsing and environment setup.

Adapted from OpenSpace __main__.py patterns:
- argparse with subcommands
- --log-level control
- Graceful shutdown support
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional


# Pipeline directory: Self-Evolution-Pipeline/
_PIPELINE_DIR = Path(__file__).resolve().parent.parent.parent


def load_dotenv(env_path: Optional[Path] = None) -> None:
    """Load .env file (simple implementation, no dependency)."""
    env_path = env_path or _PIPELINE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_importable() -> None:
    """Ensure the pipeline package is importable from project root."""
    if str(_PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(_PIPELINE_DIR))


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments with subcommands."""
    parser = argparse.ArgumentParser(
        description="Skill Evolution Pipeline — intelligent skill evolution with LLM analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  cd Self-Evolution-Pipeline
  python -m skill_evolution.pipeline.runner
  python -m skill_evolution.pipeline.runner --stage extract
  python -m skill_evolution.pipeline.runner --project-root /path/to/project
  python -m skill_evolution.pipeline.runner --log-level DEBUG
        """,
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run subcommand (default)
    run_parser = subparsers.add_parser("run", help="Run the evolution pipeline")
    _add_run_args(run_parser)

    # validate subcommand
    validate_parser = subparsers.add_parser("validate", help="Validate configuration")
    validate_parser.add_argument("--config", "-c", default=None, help="Path to YAML config file")

    # version subcommand
    subparsers.add_parser("version", help="Show version info")

    # Add run args to main parser too (for backward compatibility)
    _add_run_args(parser)

    args = parser.parse_args(argv)

    # Default to "run" if no subcommand
    if args.command is None:
        args.command = "run"

    return args


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    """Add run-specific arguments to a parser."""
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--skill", "-s", default=None,
        help="Skill name to evolve (overrides config)",
    )
    parser.add_argument(
        "--project-root", "-p", default=None,
        help="Project root directory (overrides config)",
    )
    parser.add_argument(
        "--server-root", default=None,
        help="Server root directory for sessions.jsonl (overrides project-root)",
    )
    parser.add_argument(
        "--skill-root", default=None,
        help="Local root directory containing SKILL.md files",
    )
    parser.add_argument(
        "--discover-all", action="store_true",
        help="Discover all skills from server-root and process them",
    )
    parser.add_argument(
        "--staging-dir", default=None,
        help="Staging directory for evolved skills (overrides config)",
    )
    parser.add_argument(
        "--skill-names", default=None,
        help="Comma-separated list of skill names for multi-skill parallel evolution",
    )
    parser.add_argument(
        "--max-concurrent-skills", type=int, default=None,
        help="Max concurrent skill evolutions (default: 3)",
    )
    parser.add_argument(
        "--stage",
        choices=["all", "extract", "filter", "sample", "analyze", "evolve"],
        default="all",
        help="Pipeline stage to run (default: all)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Log level (default: INFO)",
    )


def get_pipeline_dir() -> Path:
    """Return the pipeline directory (Self-Evolution-Pipeline/)."""
    return _PIPELINE_DIR
