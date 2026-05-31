"""Prompt constants and output schema for the Skill Evolution Pipeline.

Prompt templates (system/user messages, evolution templates) are externalized
to .txt files in the prompts/ directory and loaded via PromptLoader.
"""

# ── Sentinel tokens ──────────────────────────────────────────────────────────

EVOLUTION_COMPLETE = "<EVOLUTION_COMPLETE>"
EVOLUTION_FAILED = "<EVOLUTION_FAILED>"


# ── Output schema for structured parsing ─────────────────────────────────────

EXECUTION_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_name": {"type": "string"},
        "total_sessions": {"type": "integer"},
        "success_count": {"type": "integer"},
        "retry_success_count": {"type": "integer"},
        "failed_count": {"type": "integer"},
        "success_rate": {"type": "number"},
        "dominant_patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "frequency": {"type": "integer"},
                    "impact": {"type": "string", "enum": ["high", "medium", "low"]},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["pattern", "frequency", "impact"],
            },
        },
        "failure_analysis": {
            "type": "object",
            "properties": {
                "root_causes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cause": {"type": "string"},
                            "frequency": {"type": "integer"},
                            "affected_sessions": {"type": "array", "items": {"type": "string"}},
                            "category": {
                                "type": "string",
                                "enum": ["tool_misuse", "rule_violation", "logic_error", "missing_step"],
                            },
                        },
                        "required": ["cause", "frequency", "category"],
                    },
                },
                "common_errors": {"type": "array", "items": {"type": "string"}},
            },
        },
        "skill_gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "gap": {"type": "string"},
                    "evidence": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["gap", "priority"],
            },
        },
        "evolution_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["fix", "derived", "captured"]},
                    "direction": {"type": "string"},
                    "target_skills": {"type": "array", "items": {"type": "string"}},
                    "category": {"type": "string", "enum": ["tool_guide", "workflow", "reference"]},
                    "evidence_sessions": {"type": "array", "items": {"type": "string"}},
                    "evidence_session_paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["type", "direction"],
            },
        },
        "execution_efficiency": {
            "type": "object",
            "properties": {
                "avg_tokens": {"type": "integer"},
                "avg_duration_seconds": {"type": "number"},
                "bottleneck_tools": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["skill_name", "total_sessions", "success_rate", "dominant_patterns", "failure_analysis", "evolution_suggestions"],
}
