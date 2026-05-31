"""Pipeline run recorder — tracks stage execution, timing, and metrics.

Adapted from OpenSpace RecordingManager pattern:
- Singleton global instance
- JSONL-based delta logging
- Stage-level timing with start/end events
- Automatic metric aggregation
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from skill_evolution.utils.logging import Logger

logger = Logger.get_logger(__name__)


class PipelineRecorder:
    """Records pipeline run events to JSONL for observability.

    Usage:
        recorder = PipelineRecorder(output_dir=Path("output/runs/run_xxx"))
        recorder.start_run(skill_name="protocol-agent", config={...})
        with recorder.stage("extraction") as s:
            sessions = run_extraction(config)
            s.set_result(session_count=len(sessions))
        recorder.end_run(success=True)
    """

    _global_instance: Optional[PipelineRecorder] = None

    def __init__(self, output_dir: Path, enabled: bool = True):
        self.output_dir = output_dir
        self.enabled = enabled
        self._events: List[Dict[str, Any]] = []
        self._run_start: Optional[float] = None
        self._run_meta: Dict[str, Any] = {}

        PipelineRecorder._global_instance = self

    @classmethod
    def get_instance(cls) -> Optional[PipelineRecorder]:
        """Get the global recorder instance."""
        return cls._global_instance

    def start_run(self, skill_name: str, config: Optional[Dict[str, Any]] = None) -> None:
        """Record run start."""
        if not self.enabled:
            return
        self._run_start = time.time()
        self._run_meta = {
            "skill_name": skill_name,
            "config": config or {},
        }
        self._emit("run_start", {
            "skill_name": skill_name,
            "timestamp": datetime.now().isoformat(),
        })

    def end_run(self, success: bool, error: Optional[str] = None) -> None:
        """Record run completion."""
        if not self.enabled:
            return
        duration = time.time() - self._run_start if self._run_start else 0
        self._emit("run_end", {
            "success": success,
            "error": error,
            "duration_seconds": round(duration, 2),
            "timestamp": datetime.now().isoformat(),
        })
        self._flush()

    def stage(self, name: str) -> StageContext:
        """Context manager for tracking a pipeline stage."""
        return StageContext(self, name)

    def record_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Record a generic event."""
        if not self.enabled:
            return
        self._emit(event_type, data)

    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Append an event to the in-memory log."""
        event = {
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            **data,
        }
        self._events.append(event)
        logger.debug(f"[RECORDER] {event_type}: {json.dumps(data, ensure_ascii=False, default=str)[:200]}")

    def _flush(self) -> None:
        """Write all events to JSONL file."""
        if not self._events:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.output_dir / "pipeline_events.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            for event in self._events:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        logger.debug(f"[RECORDER] Flushed {len(self._events)} events to {log_path}")
        self._events.clear()


class StageContext:
    """Context manager for tracking a pipeline stage's execution."""

    def __init__(self, recorder: PipelineRecorder, name: str):
        self.recorder = recorder
        self.name = name
        self._start: Optional[float] = None
        self._result: Dict[str, Any] = {}

    def __enter__(self) -> StageContext:
        self._start = time.time()
        self.recorder._emit("stage_start", {"stage": self.name})
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        duration = time.time() - self._start if self._start else 0
        self.recorder._emit("stage_end", {
            "stage": self.name,
            "duration_seconds": round(duration, 2),
            "success": exc_type is None,
            "error": str(exc_val) if exc_val else None,
            **self._result,
        })

    def set_result(self, **kwargs: Any) -> None:
        """Attach result metrics to the stage event."""
        self._result.update(kwargs)
