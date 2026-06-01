"""Configuration management for the Skill Evolution Pipeline.

Adapted from OpenSpace config/grounding.py pattern:
- Pydantic v2 BaseModel hierarchy with Field validators
- Thread-safe singleton loader with deep merge
- field_validator for custom validation
- ConfigMixin for safe attribute access
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from skill_evolution.config.constants import (
    LOG_LEVELS,
    DEFAULT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    DEFAULT_EVOLUTION_RATIO,
    DEFAULT_TEST_RATIO,
    DEFAULT_MIN_RELEVANCE_SCORE,
)

# Load .env file from pipeline directory (Self-Evolution-Pipeline/)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


class ConfigMixin:
    """Mixin to add utility methods for config access."""

    def get_value(self, key: str, default: Any = None) -> Any:
        """Safely get config value, works with both dict and Pydantic models."""
        if isinstance(self, dict):
            return self.get(key, default)
        return getattr(self, key, default)


class LLMConfig(BaseModel, ConfigMixin):
    """LLM provider configuration.

    Provider-specific config is read from corresponding env vars:
    - OpenAI/MiniMax: OPENAI_API_KEY, OPENAI_API_BASE, OPENAI_MODEL
    - Anthropic: ANTHROPIC_API_KEY, ANTHROPIC_API_BASE, ANTHROPIC_MODEL
    """
    provider: str = Field(os.getenv("LLM_PROVIDER", "openai"), description="LLM provider: anthropic, openai")
    model: str = Field(default="", description="Model identifier (set dynamically based on provider)")
    max_tokens: int = Field(DEFAULT_MAX_TOKENS, ge=256, le=128000, description="Max output tokens")
    temperature: float = Field(DEFAULT_TEMPERATURE, ge=0.0, le=2.0, description="Sampling temperature")
    max_retries: int = Field(DEFAULT_MAX_RETRIES, ge=0, le=10, description="Max retry attempts")
    retry_delay: float = Field(1.0, ge=0.1, le=60.0, description="Base retry delay in seconds")
    timeout: float = Field(DEFAULT_TIMEOUT, ge=1.0, le=600.0, description="Per-request timeout in seconds")
    api_base: Optional[str] = Field(default="", description="API base URL (set dynamically based on provider)")
    api_key: Optional[str] = Field(default="", description="API key (set dynamically based on provider)")

    def __init__(self, **data):
        """Initialize with provider-specific env vars."""
        super().__init__(**data)
        # Override with provider-specific env vars
        if self.provider == "anthropic":
            self.api_key = os.getenv("ANTHROPIC_API_KEY", self.api_key)
            self.api_base = os.getenv("ANTHROPIC_API_BASE", "") or None
            self.model = os.getenv("ANTHROPIC_MODEL", self.model or DEFAULT_MODEL)
        else:
            # Default to openai-compatible
            self.api_key = os.getenv("OPENAI_API_KEY", self.api_key)
            self.api_base = os.getenv("OPENAI_API_BASE", "") or None
            self.model = os.getenv("OPENAI_MODEL", self.model or DEFAULT_MODEL)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        valid = {"anthropic", "openai"}
        if v.lower() not in valid:
            raise ValueError(f"Provider must be one of {valid}, got: {v}")
        return v.lower()


class ExtractionConfig(BaseModel, ConfigMixin):
    """Configuration for the extraction layer."""
    max_content_length: int = Field(500, ge=50, le=5000, description="Max chars for content previews")
    include_tool_results: bool = Field(True, description="Include tool results in extraction")
    include_system_messages: bool = Field(False, description="Include system messages")


class SamplingConfig(BaseModel, ConfigMixin):
    """Configuration for quality filtering and dataset split."""
    min_relevance_score: int = Field(DEFAULT_MIN_RELEVANCE_SCORE, ge=0, le=10, description="Minimum relevance score to include")
    evolution_ratio: float = Field(DEFAULT_EVOLUTION_RATIO, ge=0.1, le=0.95, description="Evolution set ratio")
    test_ratio: float = Field(DEFAULT_TEST_RATIO, ge=0.05, le=0.9, description="Test set ratio")

    @field_validator("test_ratio")
    @classmethod
    def validate_ratios(cls, v: float, info: Any) -> float:
        evolution = info.data.get("evolution_ratio", DEFAULT_EVOLUTION_RATIO)
        if abs(evolution + v - 1.0) > 0.01:
            raise ValueError(f"evolution_ratio ({evolution}) + test_ratio ({v}) must sum to 1.0")
        return v


class EvaluationConfig(BaseModel, ConfigMixin):
    """Configuration for evaluation thresholds."""
    improvement_threshold_approve: float = Field(15.0, ge=0.0, description=">= this % -> APPROVE")
    improvement_threshold_reject: float = Field(-5.0, le=0.0, description="< this % -> REJECT")
    dimensions: Dict[str, float] = Field(
        default_factory=lambda: {
            "rule_compliance": 0.30,
            "output_quality": 0.30,
            "efficiency": 0.20,
            "stability": 0.20,
        },
        description="Evaluation dimension weights (must sum to 1.0)",
    )

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions(cls, v: Dict[str, float]) -> Dict[str, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Dimension weights must sum to 1.0, got {total:.2f}")
        return v


class PathConfig(BaseModel, ConfigMixin):
    """Path configuration — all paths are relative to project_root unless absolute."""
    project_root: str = Field("", description="Project root directory")

    # Input paths
    session_glob: str = Field("agent-*.jsonl", description="Glob pattern for session JSONL files")
    session_index: str = Field("{folder_name}/sessions.jsonl", description="Session index path template")
    skill_search_paths: List[str] = Field(
        default_factory=lambda: [
            "{folder_name}/SKILL.md",
            "{folder_name}/.claude/skills/{folder_name}/SKILL.md",
            "{folder_name}/.claude/skills/{skill_name}/SKILL.md",
        ],
        description="Skill file search paths (tried in order)",
    )
    skill_folder_map: Dict[str, str] = Field(
        default_factory=lambda: {"protocol-agent": "协议分析-agent"},
        description="Map skill_name -> on-disk folder name",
    )

    # Output paths
    output_dir: str = Field("output/runs", description="Run output directory")
    staging_dir: str = Field("output/staging", description="Staging directory for evolved skills")
    prompts_dir: str = Field("prompts", description="Prompt templates directory")

    def _resolve_path(self, path_str: str, base: Optional[Path] = None) -> Path:
        """Resolve a path string: absolute if starts with / or drive letter, else relative to base."""
        p = Path(path_str)
        if p.is_absolute():
            return p
        base = base or self.get_project_root()
        return base / p

    @staticmethod
    def _pipeline_dir() -> Path:
        """Return the pipeline directory (Self-Evolution-Pipeline/)."""
        return Path(__file__).resolve().parent.parent.parent

    def get_project_root(self) -> Path:
        """Get the project root as a Path."""
        if not self.project_root:
            raise ValueError("project_root is not set. Provide via config or --project-root flag.")
        return Path(self.project_root)

    def get_folder_name(self, skill_name: str) -> str:
        """Map skill_name to on-disk folder name."""
        return self.skill_folder_map.get(skill_name, skill_name)

    def resolve_session_files(self) -> list[Path]:
        """Find all session JSONL files matching session_glob under project_root."""
        return sorted(self.get_project_root().glob(self.session_glob))

    def resolve_session_index(self, skill_name: str) -> Path:
        """Resolve the sessions.jsonl index path for a given skill."""
        folder_name = self.get_folder_name(skill_name)
        raw = self.session_index.format(skill_name=skill_name, folder_name=folder_name)
        return self._resolve_path(raw)

    def resolve_skill_dir(self, skill_name: str) -> Optional[Path]:
        """Find the directory containing SKILL.md for the given skill."""
        root = self.get_project_root()
        folder_name = self.get_folder_name(skill_name)
        for template in self.skill_search_paths:
            raw = template.format(skill_name=skill_name, folder_name=folder_name)
            candidate = root / raw
            if candidate.exists():
                return candidate.parent
        return None

    def resolve_skill_content(self, skill_name: str) -> str:
        """Load SKILL.md content for the given skill."""
        skill_dir = self.resolve_skill_dir(skill_name)
        if skill_dir:
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                return skill_file.read_text(encoding="utf-8")
        return f"---\nname: {skill_name}\ndescription: Placeholder\n---\n\n# {skill_name}\n\nSkill content not found."

    def resolve_output_dir(self, run_id: str) -> Path:
        """Resolve the output directory for a specific run."""
        return self._resolve_path(self.output_dir, base=self._pipeline_dir()) / run_id

    def resolve_staging_dir(self, skill_name: str) -> Path:
        """Resolve the staging directory for evolved skills."""
        return self._resolve_path(self.staging_dir, base=self._pipeline_dir()) / skill_name

    def resolve_prompts_dir(self) -> Path:
        """Resolve the prompts directory."""
        return self._resolve_path(self.prompts_dir, base=self._pipeline_dir())


class PipelineConfig(BaseModel, ConfigMixin):
    """Top-level pipeline configuration — Pydantic v2 BaseModel."""
    skill_name: str = Field("protocol-agent", description="Skill name to evolve (single mode)")
    skill_names: List[str] = Field(default_factory=list, description="Skill names to evolve (multi-skill mode)")
    max_concurrent_skills: int = Field(3, ge=1, le=10, description="Max concurrent skill evolutions")
    max_concurrent_suggestions: int = Field(3, ge=1, le=5, description="Max concurrent evolution suggestions per skill")
    llm: LLMConfig = Field(default_factory=LLMConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    paths: PathConfig = Field(default_factory=PathConfig)

    def get_skill_names(self) -> List[str]:
        """Return the list of skills to process. Falls back to [skill_name]."""
        if self.skill_names:
            return self.skill_names
        return [self.skill_name]

    @classmethod
    def from_yaml(cls, path: str) -> PipelineConfig:
        """Load config from YAML file with deep merge."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    def to_yaml(self, path: str) -> None:
        """Save config to YAML file."""
        data = self.model_dump()
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    def model_dump_yaml(self) -> Dict[str, Any]:
        """Export as dict suitable for YAML serialization."""
        return self.model_dump()


# ── Thread-safe singleton config loader (from OpenSpace pattern) ─────────────

_config: Optional[PipelineConfig] = None
_config_lock = threading.RLock()


def _deep_merge_dict(base: dict, update: dict) -> dict:
    """Deep merge two dictionaries, update's values override base's values."""
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: Optional[str] = None) -> PipelineConfig:
    """Load configuration from YAML file.

    Thread-safe singleton: subsequent calls return cached config unless reset.

    Environment variables take precedence over YAML config for sensitive values:
    - OPENAI_API_KEY / ANTHROPIC_API_KEY
    - OPENAI_API_BASE
    - OPENAI_MODEL / ANTHROPIC_MODEL
    - LLM_PROVIDER
    """
    global _config
    with _config_lock:
        if config_path:
            path = Path(config_path)
        else:
            path = PathConfig._pipeline_dir() / "configs" / "default.yaml"

        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                raw_data = yaml.safe_load(f) or {}

            # Only override provider from env (model/api_key/api_base handled in LLMConfig.__init__)
            if "llm" in raw_data and os.getenv("LLM_PROVIDER"):
                raw_data["llm"]["provider"] = os.getenv("LLM_PROVIDER")

            _config = PipelineConfig.model_validate(raw_data)
        else:
            _config = PipelineConfig()

    return _config


def get_config() -> PipelineConfig:
    """Get global config instance. Loads defaults if not yet loaded."""
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                load_config()
    return _config


def reset_config() -> None:
    """Reset config singleton (for testing)."""
    global _config
    with _config_lock:
        _config = None
