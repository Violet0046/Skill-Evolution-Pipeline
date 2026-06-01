"""Stage 8 — Merge Engine: 将 .change 文件应用到 SKILL.md。

=== 三阶段策略 ===

阶段1: 代码静态合并 (全自动, 确定性)
  .change 文件 → 解析 → 锚点匹配 (fuzzy_find_match) → 直接应用
  ↓ 如果锚点找不到或有冲突
阶段2: LLM 局部融合 (仅处理冲突)
  冲突章节 + 冲突 change 描述 → LLM → 融合后内容
  ↓ 全部合并完成后
阶段3: LLM 全文润色 (可选)
  合并后 SKILL.md → LLM → 排版优化、去重、风格统一

=== 依赖 ===

- change_parser.ChangeFile — .change 文件数据结构
- patch.fuzzy_find_match — 6 层模糊锚点匹配
- LLMWithTools — 阶段 2/3 的 LLM 调用
- PromptLoader — 加载 merge prompt 模板
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from skill_evolution.evolution.change_parser import ChangeFile
from skill_evolution.evolution.patch import fuzzy_find_match
from skill_evolution.utils.logging import Logger

logger = Logger.get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ConflictInfo:
    """冲突描述。"""
    anchor_selector: str
    conflicting_changes: list[ChangeFile]
    conflict_type: str       # "delete_vs_insert" | "multiple_inserts" | "anchor_not_found"
    context: str = ""        # 锚点周围的上下文 (供 LLM 参考)


@dataclass
class CodeMergeResult:
    """阶段1 (代码静态合并) 的结果。"""
    applied: list[ChangeFile] = field(default_factory=list)
    conflicts: list[ConflictInfo] = field(default_factory=list)
    merged_content: str = ""


@dataclass
class MergeResult:
    """完整合并结果。"""
    final_content: str
    applied_count: int
    conflict_count: int
    polished: bool = False
    diff: str = ""

    def to_dict(self) -> dict:
        return {
            "applied_count": self.applied_count,
            "conflict_count": self.conflict_count,
            "polished": self.polished,
            "diff_lines": self.diff.count("\n"),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 合并引擎
# ═══════════════════════════════════════════════════════════════════════════════


class MergeEngine:
    """三阶段合并引擎: 代码优先 + LLM 兜底。

    用法:
        engine = MergeEngine(llm_config, prompt_loader)
        result = engine.merge(skill_content, changes)
    """

    def __init__(
        self,
        llm_config=None,
        prompt_loader=None,
        *,
        enable_polish: bool = False,
    ):
        """
        Args:
            llm_config: LLMConfig 实例 (阶段 2/3 需要)
            prompt_loader: PromptLoader 实例 (阶段 2/3 需要)
            enable_polish: 是否启用阶段 3 全文润色
        """
        self._llm_config = llm_config
        self._prompt_loader = prompt_loader
        self._enable_polish = enable_polish

    # ── 主入口 ────────────────────────────────────────────────────────────────

    def merge(
        self,
        skill_content: str,
        changes: list[ChangeFile],
    ) -> MergeResult:
        """执行完整合并流程。

        Args:
            skill_content: 当前 SKILL.md 内容
            changes: .change 文件列表

        Returns:
            MergeResult 包含最终内容和统计信息
        """
        if not changes:
            logger.info("No changes to merge")
            return MergeResult(
                final_content=skill_content,
                applied_count=0,
                conflict_count=0,
            )

        logger.info(f"Merge: {len(changes)} change(s) to apply")

        # 阶段1: 代码静态合并
        code_result = self._apply_changes_code(skill_content, changes)
        logger.info(f"Phase 1: {len(code_result.applied)} applied, "
                    f"{len(code_result.conflicts)} conflict(s)")

        merged = code_result.merged_content

        # 阶段2: LLM 解决冲突
        if code_result.conflicts:
            if self._llm_config and self._prompt_loader:
                merged = self._resolve_conflicts_with_llm(merged, code_result.conflicts)
                logger.info("Phase 2: conflicts resolved via LLM")
            else:
                logger.warning(f"Phase 2 skipped: {len(code_result.conflicts)} conflict(s) "
                               f"unresolved (no LLM config)")

        # 阶段3: 全文润色 (可选)
        polished = False
        if self._enable_polish and self._llm_config and self._prompt_loader:
            merged = self._polish_with_llm(merged)
            polished = True
            logger.info("Phase 3: polish complete")

        # 生成 diff
        diff = self._compute_diff(skill_content, merged)

        return MergeResult(
            final_content=merged,
            applied_count=len(code_result.applied),
            conflict_count=len(code_result.conflicts),
            polished=polished,
            diff=diff,
        )

    # ── 阶段1: 代码静态合并 ──────────────────────────────────────────────────

    def _apply_changes_code(
        self,
        skill_content: str,
        changes: list[ChangeFile],
    ) -> CodeMergeResult:
        """尝试用代码直接应用所有 change。

        逐个应用 change，每个成功的 change 会更新 merged_content，
        后续 change 在更新后的内容上继续应用。

        失败的 change (锚点找不到) 被标记为冲突。
        """
        result = CodeMergeResult(merged_content=skill_content)

        # 先检测 change 之间的冲突
        conflicts = self._detect_conflicts(changes)
        conflict_anchors = {c.anchor_selector for c in conflicts}

        for change in changes:
            # 如果这个 change 的锚点有冲突，跳过 (交给 LLM)
            if change.anchor_selector in conflict_anchors:
                continue

            new_content = self._apply_single_change(result.merged_content, change)
            if new_content is not None:
                result.merged_content = new_content
                result.applied.append(change)
                logger.debug(f"Applied: {change.summary[:50]}")
            else:
                # 锚点找不到
                ctx = self._extract_context(result.merged_content, change.anchor_selector)
                conflicts.append(ConflictInfo(
                    anchor_selector=change.anchor_selector,
                    conflicting_changes=[change],
                    conflict_type="anchor_not_found",
                    context=ctx,
                ))
                logger.debug(f"Anchor not found: {change.anchor_selector}")

        result.conflicts = conflicts
        return result

    def _find_anchor_line(self, content: str, selector: str) -> int:
        """在 SKILL.md 中找锚点标题的行号。

        使用 patch.py 的 fuzzy_find_match 进行 6 层模糊匹配。
        返回行号 (0-based)，找不到返回 -1。
        """
        matched_text, char_pos = fuzzy_find_match(content, selector)
        if char_pos == -1:
            return -1
        # 将字符位置转为行号
        return content[:char_pos].count("\n")

    def _apply_single_change(
        self,
        content: str,
        change: ChangeFile,
    ) -> Optional[str]:
        """应用单个 change 到内容。

        Returns:
            修改后的内容, 锚点找不到返回 None
        """
        anchor_line = self._find_anchor_line(content, change.anchor_selector)
        if anchor_line == -1:
            return None

        lines = content.split("\n")

        if change.operation == "INSERT_SUBSECTION":
            return self._insert_subsection(lines, anchor_line, change.new_content)
        elif change.operation == "INSERT_RULE":
            return self._insert_rule(lines, anchor_line, change.new_content)
        elif change.operation == "DELETE":
            return self._delete_section(lines, anchor_line, change.anchor_selector)
        elif change.operation == "REPLACE":
            return self._replace_section(lines, anchor_line, change)
        else:
            logger.warning(f"Unknown operation: {change.operation}")
            return None

    def _insert_subsection(
        self,
        lines: list[str],
        anchor_line: int,
        new_content: str,
    ) -> str:
        """INSERT_SUBSECTION: 在锚点标题的章节末尾插入新内容。

        找到锚点标题的下一个同级或更高级标题，在其前面插入。
        """
        anchor_level = self._heading_level(lines[anchor_line])
        insert_pos = self._find_section_end(lines, anchor_line, anchor_level)

        new_lines = new_content.split("\n")
        # 在 insert_pos 前插入，前后各加一个空行
        lines[insert_pos:insert_pos] = [""] + new_lines + [""]
        return "\n".join(lines)

    def _insert_rule(
        self,
        lines: list[str],
        anchor_line: int,
        new_content: str,
    ) -> str:
        """INSERT_RULE: 在锚点标题的下一行插入新内容。"""
        new_lines = new_content.split("\n")
        insert_pos = anchor_line + 1
        lines[insert_pos:insert_pos] = new_lines
        return "\n".join(lines)

    def _delete_section(
        self,
        lines: list[str],
        anchor_line: int,
        anchor_selector: str,
    ) -> str:
        """DELETE: 删除锚点标题及其下属内容 (到下一个同级标题)。"""
        anchor_level = self._heading_level(lines[anchor_line])
        end_pos = self._find_section_end(lines, anchor_line, anchor_level)
        # 删除 anchor_line 到 end_pos (不含 end_pos)
        del lines[anchor_line:end_pos]
        return "\n".join(lines)

    def _replace_section(
        self,
        lines: list[str],
        anchor_line: int,
        change: ChangeFile,
    ) -> str:
        """REPLACE: 替换锚点章节内容。"""
        anchor_level = self._heading_level(lines[anchor_line])
        end_pos = self._find_section_end(lines, anchor_line, anchor_level)
        # 替换 anchor_line+1 到 end_pos
        new_lines = change.new_content.split("\n")
        lines[anchor_line + 1:end_pos] = new_lines
        return "\n".join(lines)

    # ── 标题工具方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _heading_level(line: str) -> int:
        """计算 Markdown 标题级别 (number of #)。"""
        stripped = line.lstrip()
        if stripped.startswith("#"):
            return len(stripped) - len(stripped.lstrip("#"))
        return 0

    @staticmethod
    def _find_section_end(
        lines: list[str],
        start_line: int,
        level: int,
    ) -> int:
        """找到 start_line 所属章节的结束位置。

        从 start_line+1 开始扫描，遇到同级或更高级标题时停止。
        返回结束位置的行号 (该行是下一个标题或文件末尾)。
        """
        for i in range(start_line + 1, len(lines)):
            line = lines[i].lstrip()
            if line.startswith("#"):
                next_level = len(line) - len(line.lstrip("#"))
                if next_level <= level:
                    return i
        return len(lines)

    # ── 冲突检测 ──────────────────────────────────────────────────────────────

    def _detect_conflicts(
        self,
        changes: list[ChangeFile],
    ) -> list[ConflictInfo]:
        """检测多个 change 之间的冲突。

        冲突类型:
        1. 同锚点 + DELETE + INSERT → 语义冲突
        2. 同锚点 + 多个 INSERT → 需要排序 (不算冲突, 按序插入)
        3. 锚点找不到 → 阶段1 处理
        """
        # 按锚点分组
        by_anchor: dict[str, list[ChangeFile]] = {}
        for c in changes:
            by_anchor.setdefault(c.anchor_selector, []).append(c)

        conflicts: list[ConflictInfo] = []
        for anchor, group in by_anchor.items():
            if len(group) < 2:
                continue

            has_delete = any(c.operation == "DELETE" for c in group)
            has_insert = any(c.operation != "DELETE" for c in group)

            if has_delete and has_insert:
                conflicts.append(ConflictInfo(
                    anchor_selector=anchor,
                    conflicting_changes=group,
                    conflict_type="delete_vs_insert",
                ))

        return conflicts

    # ── 上下文提取 (供 LLM 参考) ──────────────────────────────────────────────

    def _extract_context(
        self,
        content: str,
        anchor_selector: str,
        context_lines: int = 5,
    ) -> str:
        """提取锚点附近的上下文 (用于 LLM 冲突解决)。"""
        lines = content.split("\n")
        # 尝试模糊匹配
        matched, pos = fuzzy_find_match(content, anchor_selector)
        if pos != -1:
            line_idx = content[:pos].count("\n")
        else:
            # 找不到时，用关键词搜索
            keywords = anchor_selector.replace("#", "").strip().split()
            for i, line in enumerate(lines):
                if any(kw in line for kw in keywords):
                    line_idx = i
                    break
            else:
                return f"[锚点 '{anchor_selector}' 未找到]"

        start = max(0, line_idx - context_lines)
        end = min(len(lines), line_idx + context_lines + 1)
        ctx_lines = lines[start:end]
        return "\n".join(ctx_lines)

    # ── 阶段2: LLM 局部融合 ──────────────────────────────────────────────────

    def _resolve_conflicts_with_llm(
        self,
        skill_content: str,
        conflicts: list[ConflictInfo],
    ) -> str:
        """用 LLM 解决冲突。

        只把冲突涉及的局部上下文 + 冲突的 change 描述喂给 LLM。
        输入规模小 (~几百字)，不会幻觉。
        """
        if not self._llm_config or not self._prompt_loader:
            return skill_content

        try:
            system_prompt = self._prompt_loader.load("merge_resolve_system")
            user_template = self._prompt_loader.load("merge_resolve_user")
        except FileNotFoundError as e:
            logger.warning(f"Merge prompt not found: {e}")
            return skill_content

        from skill_evolution.llm.base import LLMWithTools

        llm = LLMWithTools(self._llm_config)
        merged = skill_content

        for conflict in conflicts:
            change_descs = "\n".join(
                f"- [{c.operation}] {c.summary}\n  new_content: {c.new_content[:200]}"
                for c in conflict.conflicting_changes
            )

            user_prompt = user_template.format(
                anchor_heading=conflict.anchor_selector,
                current_section=conflict.context,
                change_descriptions=change_descs,
            )

            try:
                resolved = llm.call_once(system_prompt, user_prompt)
                # 将 LLM 返回的内容替换到对应位置
                merged = self._apply_llm_resolution(
                    merged, conflict.anchor_selector, resolved,
                )
            except Exception as e:
                logger.warning(f"LLM conflict resolution failed for "
                               f"'{conflict.anchor_selector}': {e}")

        return merged

    def _apply_llm_resolution(
        self,
        content: str,
        anchor_selector: str,
        resolved_content: str,
    ) -> str:
        """将 LLM 解决后的内容替换到锚点章节。"""
        anchor_line = self._find_anchor_line(content, anchor_selector)
        if anchor_line == -1:
            return content

        lines = content.split("\n")
        level = self._heading_level(lines[anchor_line])
        end_pos = self._find_section_end(lines, anchor_line, level)

        new_lines = resolved_content.strip().split("\n")
        lines[anchor_line + 1:end_pos] = new_lines
        return "\n".join(lines)

    # ── 阶段3: LLM 全文润色 ──────────────────────────────────────────────────

    def _polish_with_llm(self, merged_content: str) -> str:
        """可选的全文润色。

        优化排版、语法、错别字，不改变核心规则。
        """
        if not self._llm_config or not self._prompt_loader:
            return merged_content

        try:
            system_prompt = self._prompt_loader.load("merge_polish_system")
            user_template = self._prompt_loader.load("merge_polish_user")
        except FileNotFoundError as e:
            logger.warning(f"Polish prompt not found: {e}")
            return merged_content

        from skill_evolution.llm.base import LLMWithTools

        llm = LLMWithTools(self._llm_config)
        user_prompt = user_template.format(skill_content=merged_content)

        try:
            polished = llm.call_once(system_prompt, user_prompt)
            return polished
        except Exception as e:
            logger.warning(f"LLM polish failed: {e}")
            return merged_content

    # ── Diff ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_diff(original: str, modified: str) -> str:
        """生成 unified diff。"""
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)
        diff = difflib.unified_diff(
            original_lines, modified_lines,
            fromfile="SKILL.md (original)",
            tofile="SKILL.md (merged)",
            lineterm="",
        )
        return "\n".join(diff)
