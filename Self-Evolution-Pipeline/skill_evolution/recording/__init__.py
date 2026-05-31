"""Recording and observability for pipeline runs.

Adapted from OpenSpace recording/manager.py pattern:
- Singleton PipelineRecorder for run tracking
- JSONL-based run logging
- Stage-level timing and metrics
"""
from skill_evolution.recording.recorder import PipelineRecorder

__all__ = ["PipelineRecorder"]
