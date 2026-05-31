"""CLI argument parsing and environment setup."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# Pipeline directory: skill-evolution/
_PIPELINE_DIR = Path(__file__).resolve().parent.parent.parent


def load_dotenv(env_path: Path | None = None) -> None:
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Skill Evolution Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  cd Self-Evolution-Pipeline
  python -m skill_evolution.pipeline.runner
  python -m skill_evolution.pipeline.runner --stage extract
  python -m skill_evolution.pipeline.runner --project-root /path/to/project
        """,
    )
    parser.add_argument("--config", "-c", default=None,
                        help="Path to YAML config file")
    parser.add_argument("--skill", "-s", default=None,
                        help="Skill name to evolve (overrides config)")
    parser.add_argument("--project-root", "-p", default=None,
                        help="Project root directory (overrides config)")
    parser.add_argument("--staging-dir", default=None,
                        help="Staging directory for evolved skills (overrides config)")
    parser.add_argument(
        "--stage",
        choices=["all", "extract", "filter", "sample", "analyze", "evolve"],
        default="all",
        help="Pipeline stage to run (default: all)",
    )
    return parser.parse_args(argv)


def get_pipeline_dir() -> Path:
    """Return the pipeline directory (skill-evolution/)."""
    return _PIPELINE_DIR
