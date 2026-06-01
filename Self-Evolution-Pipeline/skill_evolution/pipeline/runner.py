"""Skill Evolution Pipeline — runner (7-stage orchestration).

Adapted from OpenSpace tool_layer.py / __main__.py patterns:
- Structured logging via Logger facade (replaces print statements)
- try/except/finally with graceful cleanup
- PipelineRecorder for run observability
- Typed error handling with PipelineError
- Multi-skill parallel evolution via asyncio.gather

Usage:
    python -m skill_evolution.pipeline.runner
    python -m skill_evolution.pipeline.runner --stage extract
    python -m skill_evolution.pipeline.runner --project-root /path/to/project
    python -m skill_evolution.pipeline.runner --skill-names "skill-a,skill-b,skill-c"
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from skill_evolution.config.prompts import PromptLoader
from skill_evolution.config.settings import PipelineConfig
from skill_evolution.extraction.session_extractor import SessionExtractor
from skill_evolution.extraction.feedback_extractor import FeedbackExtractor
from skill_evolution.extraction.proto_extractor import ProtoExtractor
from skill_evolution.processing.quality_filter import QualityFilter
from skill_evolution.processing.sampler import DatasetSplitter
from skill_evolution.models.session import CanonicalSession
from skill_evolution.models.proto_analysis import ProtoAnalysis
from skill_evolution.llm.evidence_builder import EvidenceBuilder
from skill_evolution.llm.evidence_analyzer import EvidenceAnalyzer, ExecutionAnalysis
from skill_evolution.llm.skill_evolver import SkillEvolver
from skill_evolution.exceptions import PipelineError, ErrorCode
from skill_evolution.utils.logging import Logger

logger = Logger.get_logger(__name__)


def save_json(data: object, path: Path) -> None:
    """Save data as JSON to a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ── Stage 1 ──────────────────────────────────────────────────────────────────

def run_extraction(config: PipelineConfig) -> list[CanonicalSession]:
    """Extract CanonicalSession from session index + JSONL files."""
    extractor = SessionExtractor(
        max_content_length=config.extraction.max_content_length
    )
    feedback = FeedbackExtractor()
    root = config.paths.get_project_root()

    index_path = config.paths.resolve_session_index(config.skill_name)
    if not index_path.exists():
        raise PipelineError(
            f"Session index not found: {index_path}",
            code=ErrorCode.INDEX_NOT_FOUND,
            path=str(index_path),
        )

    entries = feedback._read_index(str(index_path))
    logger.info(f"Loaded {len(entries)} index entries from {index_path.name}")

    # Deduplicate by session_path
    seen_paths: dict[str, dict] = {}
    for entry in entries:
        sp = entry.get("session_path", "")
        if sp and sp not in seen_paths:
            seen_paths[sp] = entry
    logger.info(f"Unique session paths: {len(seen_paths)}")

    sessions: list[CanonicalSession] = []
    skipped = 0
    for session_path, entry in seen_paths.items():
        p = Path(session_path)
        if not p.is_absolute():
            p = root / p

        if not p.exists():
            logger.debug(f"File not found, skipping: {session_path}")
            skipped += 1
            continue

        logger.info(f"Processing: {p.name}")
        session = extractor.extract_from_file(str(p))
        session.metadata["file_path"] = str(p)

        # Enrich with index metadata
        session.upload_time = entry.get("upload_time", "")
        session.feedback.quality_score = entry.get("relevance_score", 0)
        session.feedback.relevance_level = entry.get("relevance_level", "")
        session.feedback.is_direct_call = entry.get("is_direct_call", False)

        sessions.append(session)
        logger.debug(
            f"session_id={session.session_id[:12]}, "
            f"status={session.execution.status.value}, "
            f"messages={session.execution.total_messages}, "
            f"tools={session.execution.total_tool_calls}"
        )

    if skipped:
        logger.warning(f"Skipped {skipped} entries (file not found)")
    logger.info(f"Extracted {len(sessions)} session(s) from {len(seen_paths)} index entries")

    return sessions


# ── Stage 2 ──────────────────────────────────────────────────────────────────

def run_filtering(
    config: PipelineConfig, sessions: list[CanonicalSession]
) -> dict[str, list[CanonicalSession]]:
    """Quality filtering and classification."""
    qf = QualityFilter(config.sampling)
    groups = qf.filter_and_classify(sessions)
    stats = qf.get_stats(groups)

    logger.info(f"Filter results: input={len(sessions)}, passed={stats['total_passed']}")
    for group_name, count in stats["groups"].items():
        logger.info(f"  {group_name}: {count}")
    discarded = len(sessions) - stats["total_passed"]
    if discarded > 0:
        logger.info(f"  Discarded: {discarded}")

    return groups


# ── Stage 3 ──────────────────────────────────────────────────────────────────

def run_split(
    config: PipelineConfig,
    groups: dict[str, list[CanonicalSession]],
) -> tuple[list[CanonicalSession], list[CanonicalSession]]:
    """Split into evolution and test sets."""
    splitter = DatasetSplitter(config.sampling)
    result = splitter.split(groups)

    logger.info(f"Split: evolution={len(result.evolution_set)}, test={len(result.test_set)}")
    for s in result.evolution_set:
        logger.debug(f"  evolution: {s.session_id[:12]} ({s.execution.status.value})")
    for s in result.test_set:
        logger.debug(f"  test: {s.session_id[:12]} ({s.execution.status.value})")

    return result.evolution_set, result.test_set


# ── Stage 4 ──────────────────────────────────────────────────────────────────

def run_proto_extraction(sessions: list[CanonicalSession]) -> list[ProtoAnalysis]:
    """Extract ProtoAnalysis from sessions (pure code, no LLM)."""
    extractor = ProtoExtractor()
    analyses = [extractor.extract(s) for s in sessions]

    logger.info(f"Extracted {len(analyses)} ProtoAnalyses")
    for pa in analyses:
        logger.debug(f"  {pa.session_id[:12]}: {pa.status}, tools={pa.tool_sequence[:40]}")

    return analyses


# ── Stage 5 ──────────────────────────────────────────────────────────────────

def run_evidence_build(analyses: list[ProtoAnalysis], skill_name: str) -> str:
    """Format ProtoAnalyses into evidence text block (pure code)."""
    builder = EvidenceBuilder()
    evidence_text = builder.build(analyses, skill_name=skill_name)

    logger.info(f"Built evidence text: {len(evidence_text)} chars")
    return evidence_text


# ── Stage 6 ──────────────────────────────────────────────────────────────────

def run_analysis(
    config: PipelineConfig,
    evidence_text: str,
    skill_name: str,
    session_count: int,
    prompt_loader: PromptLoader,
    sessions: Optional[list[CanonicalSession]] = None,
) -> ExecutionAnalysis:
    """LLM call to analyze evidence set -> ExecutionAnalysis."""
    analyzer = EvidenceAnalyzer(config.llm, prompt_loader=prompt_loader, sessions=sessions)
    analysis = analyzer.analyze(evidence_text, skill_name, session_count)

    logger.info(f"LLM analysis complete: {analysis.summary()}")
    return analysis


# ── Stage 7 ──────────────────────────────────────────────────────────────────

def run_evolution(
    config: PipelineConfig,
    analysis: ExecutionAnalysis,
    skill_content: str,
    skill_dir: Optional[Path],
    staging_dir: Path,
    prompt_loader: PromptLoader,
    sessions: Optional[list[CanonicalSession]] = None,
    run_id: str | None = None,
) -> Optional[object]:
    """Process evolution_suggestions serially -> patch files in staging directory."""
    if not analysis.evolution_suggestions:
        logger.info("No evolution suggestions — skipping")
        return None

    evolver = SkillEvolver(
        config.llm,
        prompt_loader=prompt_loader,
        sessions=sessions,
        run_id=run_id,
    )
    run_result = evolver.evolve(
        analysis=analysis,
        skill_content=skill_content,
        skill_dir=skill_dir,
        output_dir=staging_dir,
    )

    logger.info(f"Evolution: {run_result.success_count} ok, {run_result.fail_count} failed")
    for r in run_result.results:
        status = "OK" if r.ok else "FAIL"
        logger.info(f"  [{status}] {r.suggestion.evolution_type.value}: {r.change_summary or r.error}")

    return run_result


# ── Pipeline Orchestration ───────────────────────────────────────────────────

def run_pipeline(config: PipelineConfig, stage: str = "all") -> None:
    """Run the full pipeline or a specific stage."""
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    output_dir = config.paths.resolve_output_dir(run_id)
    staging_dir = config.paths.resolve_staging_dir(config.skill_name)

    logger.info("=" * 60)
    logger.info("Skill Evolution Pipeline")
    logger.info("=" * 60)
    logger.info(f"Skill:       {config.skill_name}")
    logger.info(f"Project:     {config.paths.get_project_root()}")
    logger.info(f"Output:      {output_dir}")
    logger.info(f"Staging:     {staging_dir}")
    logger.info(f"Stage:       {stage}")
    logger.info("=" * 60)

    try:
        _run_pipeline_inner(config, stage, run_id, output_dir, staging_dir)
    except PipelineError:
        raise
    except KeyboardInterrupt:
        logger.warning("Pipeline interrupted by user")
        raise
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        raise PipelineError(
            f"Pipeline failed: {e}",
            code=ErrorCode.PIPELINE_STAGE_FAILED,
            run_id=run_id,
        ) from e
    finally:
        logger.info("=" * 60)
        logger.info("Pipeline complete.")
        logger.info("=" * 60)


def _run_pipeline_inner(
    config: PipelineConfig,
    stage: str,
    run_id: str,
    output_dir: Path,
    staging_dir: Path,
) -> None:
    """Inner pipeline logic separated for clean error handling."""

    # --- Stage 1-3: Extract -> Filter -> Split ---
    sessions = run_extraction(config)
    if not sessions:
        raise PipelineError("No sessions extracted", code=ErrorCode.SESSION_NOT_FOUND)

    groups = run_filtering(config, sessions)
    evolution_set, test_set = run_split(config, groups)

    # Build session info for run_meta
    def _session_entry(s: CanonicalSession) -> dict:
        return {
            "session_id": s.session_id,
            "source_file": Path(s.metadata.get("file_path", "")).name,
            "status": s.execution.status.value,
            "quality_score": s.feedback.quality_score,
        }

    save_json(
        {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "skill_name": config.skill_name,
            "total_extracted": len(sessions),
            "total_filtered": len(evolution_set) + len(test_set),
            "evolution_count": len(evolution_set),
            "test_count": len(test_set),
            "evolution_set": [_session_entry(s) for s in evolution_set],
            "test_set": [_session_entry(s) for s in test_set],
            "config": {
                "min_relevance_score": config.sampling.min_relevance_score,
                "evolution_ratio": config.sampling.evolution_ratio,
            },
        },
        output_dir / "run_meta.json",
    )

    # --- Stage 4-7: ProtoExtract -> EvidenceBuild -> Analyze -> Evolve ---
    if stage in ("all", "analyze"):
        prompt_loader = PromptLoader(config.paths.resolve_prompts_dir())

        analyses = run_proto_extraction(evolution_set)
        evidence_text = run_evidence_build(analyses, config.skill_name)

        # Save evidence as readable markdown
        md_path = output_dir / "evidence_text.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(evidence_text, encoding="utf-8")

        analysis = run_analysis(
            config, evidence_text, config.skill_name, len(analyses), prompt_loader,
            sessions=evolution_set,
        )
        save_json(analysis.to_dict(), output_dir / "execution_analysis.json")

        # Stage 7: Evolution -> staging directory
        skill_content = config.paths.resolve_skill_content(config.skill_name)
        skill_dir = config.paths.resolve_skill_dir(config.skill_name)

        run_result = run_evolution(
            config, analysis, skill_content, skill_dir, staging_dir,
            prompt_loader, sessions=evolution_set, run_id=run_id,
        )

        if run_result:
            save_json(
                {
                    "total_suggestions": len(analysis.evolution_suggestions),
                    "success_count": run_result.success_count,
                    "fail_count": run_result.fail_count,
                    "results": [
                        {
                            "type": r.suggestion.evolution_type.value,
                            "direction": r.suggestion.direction,
                            "change_summary": r.change_summary,
                            "ok": r.ok,
                            "error": r.error,
                            "output_dir": str(r.edit_result.skill_dir) if r.edit_result and r.edit_result.ok else None,
                        }
                        for r in run_result.results
                    ],
                },
                output_dir / "evolution_results.json",
            )

        logger.info(f"Analysis output: {output_dir / 'evidence_text.md'}")
        logger.info(f"Analysis output: {output_dir / 'execution_analysis.json'}")
        logger.info(f"Analysis output: {output_dir / 'evolution_results.json'}")

    logger.info(f"Run meta: {output_dir / 'run_meta.json'}")


# ── Multi-Skill Parallel Pipeline ────────────────────────────────────────────

async def run_pipeline_multi(
    config: PipelineConfig,
    skill_names: list[str],
    stage: str = "all",
) -> dict[str, object]:
    """Run the evolution pipeline for multiple skills concurrently.

    Each skill runs its own full pipeline (Stage 1-7) independently.
    Results are gathered via asyncio.gather with a concurrency limit.

    Args:
        config: Pipeline configuration (shared across all skills)
        skill_names: List of skill names to evolve in parallel
        stage: Pipeline stage to run

    Returns:
        Dict mapping skill_name -> EvolutionRunResult (or None on failure)
    """
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    logger.info("=" * 60)
    logger.info(f"Multi-Skill Pipeline: {len(skill_names)} skills")
    logger.info(f"Skills: {', '.join(skill_names)}")
    logger.info(f"Concurrency: {config.max_concurrent_skills}")
    logger.info("=" * 60)

    sem = asyncio.Semaphore(config.max_concurrent_skills)
    results: dict[str, object] = {}

    async def _run_one(skill_name: str) -> tuple[str, object]:
        """Run pipeline for a single skill with semaphore control."""
        async with sem:
            logger.info(f"[{skill_name}] Starting pipeline...")
            # Create a per-skill config copy
            skill_config = config.model_copy(update={"skill_name": skill_name})
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, _run_skill_pipeline, skill_config, run_id, stage,
                )
                logger.info(f"[{skill_name}] Pipeline complete")
                return skill_name, result
            except Exception as e:
                logger.error(f"[{skill_name}] Pipeline failed: {e}")
                return skill_name, None

    # Run all skills concurrently
    tasks = [_run_one(name) for name in skill_names]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for item in completed:
        if isinstance(item, Exception):
            logger.error(f"Skill task failed with exception: {item}")
        elif isinstance(item, tuple):
            name, result = item
            results[name] = result

    # Summary
    logger.info("=" * 60)
    logger.info(f"Multi-Skill Pipeline Complete: {len(results)}/{len(skill_names)} skills processed")
    for name, result in results.items():
        if result is None:
            logger.info(f"  {name}: FAILED")
        else:
            logger.info(f"  {name}: OK")
    logger.info("=" * 60)

    return results


def _run_skill_pipeline(
    config: PipelineConfig,
    run_id: str,
    stage: str,
) -> object:
    """Run the full pipeline for a single skill (called from thread executor)."""
    output_dir = config.paths.resolve_output_dir(f"{run_id}/{config.skill_name}")
    staging_dir = config.paths.resolve_staging_dir(config.skill_name)

    # Stage 1-3
    sessions = run_extraction(config)
    if not sessions:
        logger.warning(f"[{config.skill_name}] No sessions extracted, skipping")
        return None

    groups = run_filtering(config, sessions)
    evolution_set, test_set = run_split(config, groups)

    # Save run meta
    save_json(
        {
            "run_id": run_id,
            "skill_name": config.skill_name,
            "timestamp": datetime.now().isoformat(),
            "total_extracted": len(sessions),
            "evolution_count": len(evolution_set),
            "test_count": len(test_set),
        },
        output_dir / "run_meta.json",
    )

    # Stage 4-7
    if stage in ("all", "analyze"):
        prompt_loader = PromptLoader(config.paths.resolve_prompts_dir())

        analyses = run_proto_extraction(evolution_set)
        evidence_text = run_evidence_build(analyses, config.skill_name)

        md_path = output_dir / "evidence_text.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(evidence_text, encoding="utf-8")

        analysis = run_analysis(
            config, evidence_text, config.skill_name, len(analyses), prompt_loader,
            sessions=evolution_set,
        )
        save_json(analysis.to_dict(), output_dir / "execution_analysis.json")

        skill_content = config.paths.resolve_skill_content(config.skill_name)
        skill_dir = config.paths.resolve_skill_dir(config.skill_name)

        run_result = run_evolution(
            config, analysis, skill_content, skill_dir, staging_dir,
            prompt_loader, sessions=evolution_set, run_id=run_id,
        )

        if run_result:
            save_json(
                {
                    "total_suggestions": len(analysis.evolution_suggestions),
                    "success_count": run_result.success_count,
                    "fail_count": run_result.fail_count,
                },
                output_dir / "evolution_results.json",
            )
        return run_result

    return None


def main() -> None:
    """CLI entry point with graceful shutdown and multi-skill support."""
    from skill_evolution.pipeline.cli import parse_args, load_dotenv, ensure_importable, get_pipeline_dir

    ensure_importable()
    load_dotenv()

    args = parse_args()

    # Handle subcommands
    if args.command == "version":
        from skill_evolution import __version__
        print(f"skill-evolution-pipeline v{__version__}")
        return
    if args.command == "validate":
        config_path = args.config or str(get_pipeline_dir() / "configs" / "default.yaml")
        try:
            config = PipelineConfig.from_yaml(config_path)
            print(f"Configuration valid: {config_path}")
            print(f"  Skills: {config.get_skill_names()}")
            print(f"  Model: {config.llm.model}")
        except Exception as e:
            print(f"Configuration invalid: {e}")
            sys.exit(1)
        return

    # Load config
    config_path = args.config or str(get_pipeline_dir() / "configs" / "default.yaml")
    if Path(config_path).exists():
        config = PipelineConfig.from_yaml(config_path)
    else:
        logger.warning(f"Config not found: {config_path}, using defaults")
        config = PipelineConfig()

    # CLI overrides
    if args.skill:
        config.skill_name = args.skill
    if args.project_root:
        config.paths.project_root = args.project_root
    if args.staging_dir:
        config.paths.staging_dir = args.staging_dir
    if hasattr(args, "skill_names") and args.skill_names:
        config.skill_names = [s.strip() for s in args.skill_names.split(",")]
    if hasattr(args, "max_concurrent_skills") and args.max_concurrent_skills:
        config.max_concurrent_skills = args.max_concurrent_skills

    # Set log level from CLI
    if hasattr(args, "log_level") and args.log_level:
        Logger.set_level(args.log_level)

    # Auto-detect project_root if not set
    if not config.paths.project_root:
        config.paths.project_root = str(get_pipeline_dir().parent)

    # Determine mode: multi-skill or single-skill
    skill_names = config.get_skill_names()

    try:
        if len(skill_names) > 1:
            # Multi-skill parallel mode
            logger.info(f"Multi-skill mode: {len(skill_names)} skills")
            results = asyncio.run(run_pipeline_multi(config, skill_names, stage=args.stage))
            # Check for failures
            failures = [name for name, result in results.items() if result is None]
            if failures:
                logger.error(f"Failed skills: {', '.join(failures)}")
                sys.exit(1)
        else:
            # Single-skill mode
            run_pipeline(config, stage=args.stage)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        sys.exit(130)
    except PipelineError as e:
        logger.error(f"Pipeline error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
