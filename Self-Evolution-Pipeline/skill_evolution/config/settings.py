"""Configuration management for the Skill Evolution Pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class LLMConfig:
    provider: str = "anthropic"  # anthropic, openai
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.0
    max_retries: int = 3
    retry_delay: float = 1.0


@dataclass
class ExtractionConfig:
    """Configuration for the extraction layer."""
    max_content_length: int = 500  # max chars for content previews
    include_tool_results: bool = True
    include_system_messages: bool = False


@dataclass
class SamplingConfig:
    """Configuration for quality filtering and dataset split."""
    min_relevance_score: int = 4

    # evolution/test split ratio
    evolution_ratio: float = 0.70
    test_ratio: float = 0.30


@dataclass
class EvaluationConfig:
    """Configuration for evaluation."""
    improvement_threshold_approve: float = 15.0  # >= 15% -> APPROVE
    improvement_threshold_reject: float = -5.0   # < -5% -> REJECT
    # between -5% and 15% -> NEED_REVIEW

    dimensions: dict = field(default_factory=lambda: {
        "rule_compliance": 0.30,
        "output_quality": 0.30,
        "efficiency": 0.20,
        "stability": 0.20,
    })


@dataclass
class PathConfig:
    """Path configuration — all paths are relative to project_root unless absolute."""
    project_root: str = ""

    # Input paths
    session_glob: str = "agent-*.jsonl"
    session_index: str = "{folder_name}/sessions.jsonl"
    skill_search_paths: list[str] = field(default_factory=lambda: [
        "{folder_name}/SKILL.md",
        "{folder_name}/.claude/skills/{folder_name}/SKILL.md",
        "{folder_name}/.claude/skills/{skill_name}/SKILL.md",
    ])
    skill_folder_map: dict = field(default_factory=lambda: {
        "protocol-agent": "协议分析-agent",
    })

    # Output paths
    output_dir: str = "output/runs"
    staging_dir: str = "output/staging"
    prompts_dir: str = "prompts"

    def _resolve_path(self, path_str: str, base: Path | None = None) -> Path:
        """Resolve a path string: absolute if starts with / or drive letter, else relative to base."""
        p = Path(path_str)
        if p.is_absolute():
            return p
        base = base or self.get_project_root()
        return base / p

    @staticmethod
    def _pipeline_dir() -> Path:
        """Return the pipeline directory (skill-evolution/)."""
        return Path(__file__).resolve().parent.parent.parent

    def get_project_root(self) -> Path:
        """Get the project root as a Path. Must be set before use."""
        if not self.project_root:
            raise ValueError("project_root is not set. Provide via config or --project-root flag.")
        return Path(self.project_root)

    def get_folder_name(self, skill_name: str) -> str:
        """Map skill_name to on-disk folder name via skill_folder_map."""
        return self.skill_folder_map.get(skill_name, skill_name)

    def resolve_session_files(self) -> list[Path]:
        """Find all session JSONL files matching session_glob under project_root."""
        root = self.get_project_root()
        return sorted(root.glob(self.session_glob))

    def resolve_session_index(self, skill_name: str) -> Path:
        """Resolve the sessions.jsonl index path for a given skill."""
        folder_name = self.get_folder_name(skill_name)
        raw = self.session_index.format(skill_name=skill_name, folder_name=folder_name)
        return self._resolve_path(raw)

    def resolve_skill_dir(self, skill_name: str) -> Path | None:
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
        """Load SKILL.md content for the given skill. Returns placeholder if not found."""
        skill_dir = self.resolve_skill_dir(skill_name)
        if skill_dir:
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                return skill_file.read_text(encoding="utf-8")
        return f"---\nname: {skill_name}\ndescription: Placeholder\n---\n\n# {skill_name}\n\nSkill content not found."

    def resolve_output_dir(self, run_id: str) -> Path:
        """Resolve the output directory for a specific run (relative to pipeline dir)."""
        return self._resolve_path(self.output_dir, base=self._pipeline_dir()) / run_id

    def resolve_staging_dir(self, skill_name: str) -> Path:
        """Resolve the staging directory for evolved skills (relative to pipeline dir)."""
        return self._resolve_path(self.staging_dir, base=self._pipeline_dir()) / skill_name

    def resolve_prompts_dir(self) -> Path:
        """Resolve the prompts directory (relative to pipeline dir)."""
        return self._resolve_path(self.prompts_dir, base=self._pipeline_dir())


@dataclass
class PipelineConfig:
    """Top-level pipeline configuration."""
    skill_name: str = "protocol-agent"
    llm: LLMConfig = field(default_factory=LLMConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    paths: PathConfig = field(default_factory=PathConfig)

    @classmethod
    def from_yaml(cls, path: str) -> PipelineConfig:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config = cls()
        if "skill_name" in data:
            config.skill_name = data["skill_name"]

        if "llm" in data:
            for k, v in data["llm"].items():
                if hasattr(config.llm, k):
                    setattr(config.llm, k, v)

        if "extraction" in data:
            for k, v in data["extraction"].items():
                if hasattr(config.extraction, k):
                    setattr(config.extraction, k, v)

        if "sampling" in data:
            for k, v in data["sampling"].items():
                if hasattr(config.sampling, k):
                    setattr(config.sampling, k, v)

        if "evaluation" in data:
            for k, v in data["evaluation"].items():
                if hasattr(config.evaluation, k):
                    setattr(config.evaluation, k, v)

        if "paths" in data:
            for k, v in data["paths"].items():
                if hasattr(config.paths, k):
                    setattr(config.paths, k, v)

        return config

    def to_yaml(self, path: str) -> None:
        import dataclasses
        data = {
            "skill_name": self.skill_name,
            "llm": dataclasses.asdict(self.llm),
            "extraction": dataclasses.asdict(self.extraction),
            "sampling": dataclasses.asdict(self.sampling),
            "evaluation": dataclasses.asdict(self.evaluation),
            "paths": dataclasses.asdict(self.paths),
        }
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
