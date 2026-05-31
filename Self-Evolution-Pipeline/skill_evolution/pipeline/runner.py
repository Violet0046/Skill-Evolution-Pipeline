"""Skill Evolution Pipeline — runner (7-stage orchestration).

Usage:
    python -m skill_evolution.pipeline.runner
    python -m skill_evolution.pipeline.runner --stage extract
    python -m skill_evolution.pipeline.runner --project-root /path/to/project
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

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


def save_json(data: any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Stage 1 ──────────────────────────────────────────────────────────────────

def run_extraction(config: PipelineConfig) -> list[CanonicalSession]:
    """Extract CanonicalSession from session index + JSONL files.

    Flow: sessions.jsonl (index) → resolve session_path → parse JSONL → CanonicalSession
    """
    extractor = SessionExtractor(
        max_content_length=config.extraction.max_content_length
    )
    feedback = FeedbackExtractor()
    root = config.paths.get_project_root()

    # Step 1: Load sessions.jsonl index as primary input
    index_path = config.paths.resolve_session_index(config.skill_name)
    if not index_path.exists():
        print(f"[ERROR] Session index not found: {index_path}")
        return []

    entries = feedback._read_index(str(index_path))
    print(f"  Loaded {len(entries)} index entries from {index_path.name}")

    # Step 2: Deduplicate by session_path (keep first occurrence = highest score)
    seen_paths: dict[str, dict] = {}
    for entry in entries:
        sp = entry.get("session_path", "")
        if sp and sp not in seen_paths:
            seen_paths[sp] = entry
    print(f"  Unique session paths: {len(seen_paths)}")

    # Step 3: For each entry, resolve path and parse JSONL
    sessions: list[CanonicalSession] = []
    skipped = 0
    for session_path, entry in seen_paths.items():
        # Resolve path: absolute or relative to project_root
        p = Path(session_path)
        if not p.is_absolute():
            p = root / p

        if not p.exists():
            print(f"  [SKIP] File not found: {session_path}")
            skipped += 1
            continue

        print(f"  Processing: {p.name}")
        session = extractor.extract_from_file(str(p))
        session.metadata["file_path"] = str(p)

        # Enrich with index metadata
        session.upload_time = entry.get("upload_time", "")
        session.feedback.quality_score = entry.get("relevance_score", 0)
        session.feedback.relevance_level = entry.get("relevance_level", "")
        session.feedback.is_direct_call = entry.get("is_direct_call", False)

        sessions.append(session)
        print(f"    -> session_id={session.session_id}")
        print(f"       status={session.execution.status.value}, "
              f"messages={session.execution.total_messages}, "
              f"tools={session.execution.total_tool_calls}, "
              f"tokens={session.execution.total_token_usage.total}")
        print(f"       quality_score={session.feedback.quality_score}, "
              f"retry={session.feedback.is_retry}")

    if skipped:
        print(f"  [WARN] Skipped {skipped} entries (file not found)")
    print(f"[EXTRACT] Extracted {len(sessions)} session(s) from {len(seen_paths)} index entries")

    return sessions


# ── Stage 2 ──────────────────────────────────────────────────────────────────

def run_filtering(
    config: PipelineConfig, sessions: list[CanonicalSession]
) -> dict[str, list[CanonicalSession]]:
    """Quality filtering and classification."""
    qf = QualityFilter(config.sampling)
    groups = qf.filter_and_classify(sessions)
    stats = qf.get_stats(groups)

    print(f"\n[FILTER] Results:")
    print(f"  Input: {len(sessions)} sessions")
    print(f"  Passed: {stats['total_passed']}")
    for group_name, count in stats["groups"].items():
        print(f"    {group_name}: {count}")
    discarded = len(sessions) - stats["total_passed"]
    if discarded > 0:
        print(f"  Discarded: {discarded}")

    return groups


# ── Stage 3 ──────────────────────────────────────────────────────────────────

def run_split(
    config: PipelineConfig,
    groups: dict[str, list[CanonicalSession]],
) -> tuple[list[CanonicalSession], list[CanonicalSession]]:
    """Split into evolution and test sets."""
    splitter = DatasetSplitter(config.sampling)
    result = splitter.split(groups)

    print(f"\n[SPLIT] Results:")
    print(f"  Evolution set: {len(result.evolution_set)} sessions")
    for s in result.evolution_set:
        print(f"    - {s.session_id[:12]}... ({s.execution.status.value})")
    print(f"  Test set: {len(result.test_set)} sessions")
    for s in result.test_set:
        print(f"    - {s.session_id[:12]}... ({s.execution.status.value})")

    return result.evolution_set, result.test_set


# ── Stage 4 ──────────────────────────────────────────────────────────────────

def run_proto_extraction(
    sessions: list[CanonicalSession],
) -> list[ProtoAnalysis]:
    """Extract ProtoAnalysis from sessions (pure code, no LLM)."""
    extractor = ProtoExtractor()
    analyses = []
    for s in sessions:
        pa = extractor.extract(s)
        analyses.append(pa)

    print(f"\n[PROTO] Extracted {len(analyses)} ProtoAnalyses")
    for pa in analyses:
        print(f"  - {pa.session_id[:12]}: {pa.status}, tools={pa.tool_sequence[:40]}")

    return analyses


# ── Stage 5 ──────────────────────────────────────────────────────────────────

def run_evidence_build(
    analyses: list[ProtoAnalysis],
    skill_name: str,
) -> str:
    """Format ProtoAnalyses into evidence text block (pure code)."""
    builder = EvidenceBuilder()
    evidence_text = builder.build(analyses, skill_name=skill_name)

    print(f"\n[EVIDENCE] Built evidence text: {len(evidence_text)} chars")
    return evidence_text


# ── Stage 6 ──────────────────────────────────────────────────────────────────

def run_analysis(
    config: PipelineConfig,
    evidence_text: str,
    skill_name: str,
    session_count: int,
    prompt_loader: PromptLoader,
    sessions: list[CanonicalSession] | None = None,
) -> ExecutionAnalysis:
    """LLM call to analyze evidence set → ExecutionAnalysis."""
    analyzer = EvidenceAnalyzer(config.llm, prompt_loader=prompt_loader, sessions=sessions)
    analysis = analyzer.analyze(evidence_text, skill_name, session_count)

    print(f"\n[ANALYSIS] LLM analysis complete:")
    print(analysis.summary())

    return analysis


# ── Stage 7 ──────────────────────────────────────────────────────────────────

def run_evolution(
    config: PipelineConfig,
    analysis: ExecutionAnalysis,
    skill_content: str,
    skill_dir: Path | None,
    staging_dir: Path,
    prompt_loader: PromptLoader,
    sessions: list[CanonicalSession] | None = None,
) -> None:
    """Process evolution_suggestions serially → new skill versions."""
    if not analysis.evolution_suggestions:
        print(f"\n[EVOLVE] No evolution suggestions — skipping")
        return

    evolver = SkillEvolver(config.llm, prompt_loader=prompt_loader, sessions=sessions)
    run_result = evolver.evolve(
        analysis=analysis,
        skill_content=skill_content,
        skill_dir=skill_dir,
        output_dir=staging_dir,
    )

    print(f"\n[EVOLVE] Results: {run_result.success_count} ok, {run_result.fail_count} failed")
    for r in run_result.results:
        status = "OK" if r.ok else "FAIL"
        print(f"  [{status}] {r.suggestion.evolution_type.value}: {r.change_summary or r.error}")

    # Save evolution results to run output (not staging)
    return run_result


# ── Pipeline Orchestration ───────────────────────────────────────────────────

def run_pipeline(config: PipelineConfig, stage: str = "all") -> None:
    """Run the full pipeline or a specific stage."""
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    output_dir = config.paths.resolve_output_dir(run_id)
    staging_dir = config.paths.resolve_staging_dir(config.skill_name)

    print(f"{'=' * 60}")
    print(f"Skill Evolution Pipeline")
    print(f"{'=' * 60}")
    print(f"Skill:       {config.skill_name}")
    print(f"Project:     {config.paths.get_project_root()}")
    print(f"Output:      {output_dir}")
    print(f"Staging:     {staging_dir}")
    print(f"Stage:       {stage}")
    print(f"{'=' * 60}")

    # --- Stage 1-3: Extract → Filter → Split ---
    sessions = run_extraction(config)
    if not sessions:
        print("[ERROR] No sessions extracted. Exiting.")
        return

    groups = run_filtering(config, sessions)
    evolution_set, test_set = run_split(config, groups)

    # Build session info for run_meta
    def _session_entry(s):
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

    # --- Stage 4-7: ProtoExtract → EvidenceBuild → Analyze → Evolve ---
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

        # Stage 7: Evolution → staging directory
        skill_content = config.paths.resolve_skill_content(config.skill_name)
        skill_dir = config.paths.resolve_skill_dir(config.skill_name)

        run_result = run_evolution(config, analysis, skill_content, skill_dir, staging_dir, prompt_loader, sessions=evolution_set)

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

        print(f"\n  Analysis output:")
        print(f"    {output_dir / 'evidence_text.md'}")
        print(f"    {output_dir / 'execution_analysis.json'}")
        print(f"    {output_dir / 'evolution_results.json'}")

    print(f"\n  Output files:")
    print(f"    {output_dir / 'run_meta.json'}")

    print(f"\n{'=' * 60}")
    print(f"Pipeline complete.")
    print(f"{'=' * 60}")


def main():
    """CLI entry point."""
    from skill_evolution.pipeline.cli import parse_args, load_dotenv, ensure_importable, get_pipeline_dir

    ensure_importable()
    load_dotenv()

    args = parse_args()

    # Load config
    config_path = args.config or str(get_pipeline_dir() / "configs" / "default.yaml")
    if Path(config_path).exists():
        config = PipelineConfig.from_yaml(config_path)
    else:
        print(f"[WARN] Config not found: {config_path}, using defaults")
        config = PipelineConfig()

    # CLI overrides
    if args.skill:
        config.skill_name = args.skill
    if args.project_root:
        config.paths.project_root = args.project_root
    if args.staging_dir:
        config.paths.staging_dir = args.staging_dir

    # Auto-detect project_root if not set
    if not config.paths.project_root:
        config.paths.project_root = str(get_pipeline_dir().parent)

    run_pipeline(config, stage=args.stage)


if __name__ == "__main__":
    main()
