"""Skill Evolution Pipeline — intelligent skill evolution with LLM-driven analysis.

Lazy-loading pattern adapted from OpenSpace __init__.py:
- __getattr__-based deferred imports keep initial package import lightweight
- TYPE_CHECKING guard provides static type hints without runtime cost
- __all__ explicitly declares public API
"""
from __future__ import annotations

from importlib import import_module as _imp
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from skill_evolution.config.settings import PipelineConfig as PipelineConfig
    from skill_evolution.config.settings import LLMConfig as LLMConfig
    from skill_evolution.llm.base import LLMWithTools as LLMWithTools
    from skill_evolution.llm.evidence_analyzer import EvidenceAnalyzer as EvidenceAnalyzer
    from skill_evolution.llm.skill_evolver import SkillEvolver as SkillEvolver
    from skill_evolution.pipeline.runner import run_pipeline as run_pipeline
    from skill_evolution.exceptions import PipelineError as PipelineError

__version__ = "0.2.0"

__all__ = [
    "__version__",
    # Config
    "PipelineConfig",
    "LLMConfig",
    # LLM
    "LLMWithTools",
    "EvidenceAnalyzer",
    "SkillEvolver",
    # Pipeline
    "run_pipeline",
    # Exceptions
    "PipelineError",
]

_attr_to_module: Dict[str, str] = {
    "PipelineConfig": "skill_evolution.config.settings",
    "LLMConfig": "skill_evolution.config.settings",
    "LLMWithTools": "skill_evolution.llm.base",
    "EvidenceAnalyzer": "skill_evolution.llm.evidence_analyzer",
    "SkillEvolver": "skill_evolution.llm.skill_evolver",
    "run_pipeline": "skill_evolution.pipeline.runner",
    "PipelineError": "skill_evolution.exceptions",
}


def __getattr__(name: str) -> Any:
    """Dynamically import sub-modules on first attribute access.

    Keeps initial package import lightweight and avoids raising
    ModuleNotFoundError for optional dependencies until explicitly used.
    """
    if name not in _attr_to_module:
        raise AttributeError(f"module 'skill_evolution' has no attribute '{name}'")

    module_name = _attr_to_module[name]
    module = _imp(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_attr_to_module.keys()))
