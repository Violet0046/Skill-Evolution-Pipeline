"""EvidenceBuilder: format N ProtoAnalyses into a text block for the analysis LLM.

Reference: OpenSpace _format_analysis_context() pattern.
One ProtoAnalysis → ~500B, so N=20 → ~10KB text block (well within context window).
"""
from __future__ import annotations

from skill_evolution.models.proto_analysis import ProtoAnalysis


class EvidenceBuilder:
    """Formats a list of ProtoAnalyses into a single text block for LLM analysis."""

    def build(
        self,
        analyses: list[ProtoAnalysis],
        skill_name: str = "",
    ) -> str:
        """Build formatted evidence text from a list of ProtoAnalyses.

        Returns a structured text block with:
        - Header: skill name, session count, status distribution
        - Per-session evidence entries
        - Aggregate statistics
        """
        sections = []
        sections.append(self._build_header(analyses, skill_name))
        sections.append(self._build_status_summary(analyses))
        sections.append(self._build_session_entries(analyses))
        sections.append(self._build_aggregate_stats(analyses))

        return "\n".join(sections)

    def _build_header(self, analyses: list[ProtoAnalysis], skill_name: str) -> str:
        return (
            f"## 证据集概览\n"
            f"- Skill: {skill_name}\n"
            f"- 总会话数: {len(analyses)}\n"
        )

    def _build_status_summary(self, analyses: list[ProtoAnalysis]) -> str:
        counts = {"success": 0, "retry_success": 0, "failed": 0}
        for a in analyses:
            if a.status in counts:
                counts[a.status] += 1
            else:
                counts["failed"] += 1  # unknown treated as failed

        lines = ["## 执行状态分布"]
        for status, count in counts.items():
            if count > 0:
                pct = count / len(analyses) * 100
                lines.append(f"- {status}: {count} ({pct:.0f}%)")
        lines.append("")
        return "\n".join(lines)

    def _build_session_entries(self, analyses: list[ProtoAnalysis]) -> str:
        lines = ["## 会话证据明细", ""]
        for i, a in enumerate(analyses, 1):
            lines.append(f"### Session {i}: {a.session_id[:12]}")
            lines.append(f"- 状态: {a.status}")
            lines.append(f"- 任务: {a.task_title or a.task_description[:80]}")
            lines.append(f"- 工具序列: {a.tool_sequence}")
            lines.append(f"- Token: {a.token_usage}")
            lines.append(f"- 消息数: {a.message_count}, 工具调用数: {a.tool_call_count}")
            lines.append(f"- 主要工具: {', '.join(a.key_tools)}")
            lines.append(f"- Session 路径: {a.session_path}")

            if a.failure_reason:
                lines.append(f"- 失败原因: {a.failure_reason[:150]}")
            if a.correction:
                lines.append(f"- 修正建议: {a.correction[:150]}")
            if a.error_tool_calls:
                lines.append(f"- 错误调用:")
                for err in a.error_tool_calls:
                    lines.append(f"  - {err}")
            if a.final_output:
                lines.append(f"- 最终输出预览: {a.final_output[:200]}")
            lines.append("")
        return "\n".join(lines)

    def _build_aggregate_stats(self, analyses: list[ProtoAnalysis]) -> str:
        total_tokens = sum(a.token_usage for a in analyses)
        avg_tokens = total_tokens // len(analyses) if analyses else 0
        total_duration = sum(a.duration_seconds for a in analyses)
        avg_duration = total_duration / len(analyses) if analyses else 0

        # collect all tool names
        all_tools = set()
        for a in analyses:
            for part in a.tool_sequence.split("→"):
                if part:
                    all_tools.add(part)

        lines = [
            "## 聚合统计",
            f"- 总 Token: {total_tokens:,}",
            f"- 平均 Token/会话: {avg_tokens:,}",
            f"- 总耗时: {total_duration:.0f}s",
            f"- 平均耗时/会话: {avg_duration:.1f}s",
            f"- 使用的工具: {', '.join(sorted(all_tools))}",
            "",
        ]
        return "\n".join(lines)
