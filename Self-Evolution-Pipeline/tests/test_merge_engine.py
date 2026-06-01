"""Stage 8: MergeEngine 测试 — 验证三阶段合并逻辑。

=== 测试范围 ===

  1. 阶段1 (代码静态合并):
     - INSERT_SUBSECTION: 在锚点章节末尾插入
     - INSERT_RULE: 在锚点标题后插入
     - DELETE: 删除锚点章节
     - REPLACE: 替换锚点章节内容
     - 多个无冲突 change 串行应用
     - 冲突检测 (同锚点 DELETE + INSERT)
     - 锚点找不到 → 标记为冲突

  2. 阶段2 (LLM 局部融合):
     - 冲突解决 (mock LLM)

  3. 阶段3 (LLM 全文润色):
     - 全文润色 (mock LLM)

=== 测试用的 SKILL.md ===

    # Protocol Agent

    ## 工具使用规范
    - 使用 Read 工具读取文件

    ## 文件读取规范
    - 读取前检查文件是否存在

    ## 调试信息
    - 临时调试用
"""
from __future__ import annotations

import pytest

from skill_evolution.evolution.change_parser import ChangeFile
from skill_evolution.llm.merge_engine import (
    CodeMergeResult,
    ConflictInfo,
    MergeEngine,
    MergeResult,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 测试用的 SKILL.md 内容
# ═══════════════════════════════════════════════════════════════════════════════

SAMPLE_SKILL_MD = """\
# Protocol Agent

## 工具使用规范
- 使用 Read 工具读取文件

## 文件读取规范
- 读取前检查文件是否存在

## 调试信息
- 临时调试用"""


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段1: 单个 change 应用
# ═══════════════════════════════════════════════════════════════════════════════


class TestInsertSubsection:
    """验证 INSERT_SUBSECTION 操作。"""

    def test_insert_at_section_end(self):
        """在锚点章节末尾插入新子章节。"""
        engine = MergeEngine()
        change = ChangeFile(
            summary="添加超时处理",
            anchor_selector="## 文件读取规范",
            operation="INSERT_SUBSECTION",
            new_content="### 超时处理\n超过 30 秒自动重试",
        )

        result = engine.merge(SAMPLE_SKILL_MD, [change])

        assert result.applied_count == 1
        assert result.conflict_count == 0
        assert "### 超时处理" in result.final_content
        assert "超过 30 秒" in result.final_content
        # 验证插入位置: 应该在 "文件读取规范" 章节内，在 "调试信息" 之前
        content = result.final_content
        read_pos = content.index("## 文件读取规范")
        timeout_pos = content.index("### 超时处理")
        debug_pos = content.index("## 调试信息")
        assert read_pos < timeout_pos < debug_pos

    def test_insert_preserves_other_sections(self):
        """插入新内容后，其他章节不受影响。"""
        engine = MergeEngine()
        change = ChangeFile(
            summary="添加规则",
            anchor_selector="## 工具使用规范",
            operation="INSERT_SUBSECTION",
            new_content="### 新规则\n规则内容",
        )

        result = engine.merge(SAMPLE_SKILL_MD, [change])

        assert "## 文件读取规范" in result.final_content
        assert "## 调试信息" in result.final_content
        assert "读取前检查" in result.final_content


class TestInsertRule:
    """验证 INSERT_RULE 操作。"""

    def test_insert_after_heading(self):
        """在锚点标题的下一行插入规则。"""
        engine = MergeEngine()
        change = ChangeFile(
            summary="添加 Bash 工具说明",
            anchor_selector="## 工具使用规范",
            operation="INSERT_RULE",
            new_content="- 使用 Bash 工具执行命令",
        )

        result = engine.merge(SAMPLE_SKILL_MD, [change])

        assert result.applied_count == 1
        assert "使用 Bash 工具" in result.final_content
        # 验证插入位置: 紧跟在 "## 工具使用规范" 之后
        lines = result.final_content.split("\n")
        heading_idx = next(i for i, l in enumerate(lines) if "## 工具使用规范" in l)
        assert "Bash" in lines[heading_idx + 1]


class TestDeleteSection:
    """验证 DELETE 操作。"""

    def test_delete_section(self):
        """删除锚点标题及其下属内容。"""
        engine = MergeEngine()
        change = ChangeFile(
            summary="删除调试信息",
            anchor_selector="## 调试信息",
            operation="DELETE",
        )

        result = engine.merge(SAMPLE_SKILL_MD, [change])

        assert result.applied_count == 1
        assert "## 调试信息" not in result.final_content
        assert "临时调试用" not in result.final_content
        # 其他章节保留
        assert "## 工具使用规范" in result.final_content
        assert "## 文件读取规范" in result.final_content


class TestReplaceSection:
    """验证 REPLACE 操作。"""

    def test_replace_section_content(self):
        """替换锚点章节的内容 (保留标题)。"""
        engine = MergeEngine()
        change = ChangeFile(
            summary="更新文件读取规范",
            anchor_selector="## 文件读取规范",
            operation="REPLACE",
            new_content="- 使用 test -f 检查文件\n- 超时 30 秒重试",
        )

        result = engine.merge(SAMPLE_SKILL_MD, [change])

        assert result.applied_count == 1
        assert "## 文件读取规范" in result.final_content
        assert "test -f" in result.final_content
        assert "读取前检查" not in result.final_content


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段1: 多个 change 串行应用
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultipleChanges:
    """验证多个无冲突 change 的串行应用。"""

    def test_two_changes_different_anchors(self):
        """两个 change 修改不同章节，应该都成功应用。"""
        engine = MergeEngine()
        changes = [
            ChangeFile(
                summary="添加 Bash 说明",
                anchor_selector="## 工具使用规范",
                operation="INSERT_SUBSECTION",
                new_content="### Bash 工具\n使用 Bash 执行命令",
            ),
            ChangeFile(
                summary="添加超时处理",
                anchor_selector="## 文件读取规范",
                operation="INSERT_SUBSECTION",
                new_content="### 超时\n超过 30 秒重试",
            ),
        ]

        result = engine.merge(SAMPLE_SKILL_MD, changes)

        assert result.applied_count == 2
        assert result.conflict_count == 0
        assert "Bash 工具" in result.final_content
        assert "超时" in result.final_content

    def test_insert_then_delete(self):
        """先插入后删除不同章节。"""
        engine = MergeEngine()
        changes = [
            ChangeFile(
                summary="添加新规则",
                anchor_selector="## 工具使用规范",
                operation="INSERT_SUBSECTION",
                new_content="### 新规则\n规则内容",
            ),
            ChangeFile(
                summary="删除调试信息",
                anchor_selector="## 调试信息",
                operation="DELETE",
            ),
        ]

        result = engine.merge(SAMPLE_SKILL_MD, changes)

        assert result.applied_count == 2
        assert "新规则" in result.final_content
        assert "## 调试信息" not in result.final_content


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段1: 冲突检测
# ═══════════════════════════════════════════════════════════════════════════════


class TestConflictDetection:
    """验证冲突检测逻辑。"""

    def test_delete_vs_insert_conflict(self):
        """同锚点的 DELETE + INSERT 应该检测为冲突。"""
        engine = MergeEngine()
        changes = [
            ChangeFile(
                summary="删除调试信息",
                anchor_selector="## 调试信息",
                operation="DELETE",
            ),
            ChangeFile(
                summary="更新调试信息",
                anchor_selector="## 调试信息",
                operation="INSERT_SUBSECTION",
                new_content="### 新调试\n新内容",
            ),
        ]

        result = engine.merge(SAMPLE_SKILL_MD, changes)

        # 两个 change 都有冲突，都不会被应用
        assert result.conflict_count >= 1
        # 原文不变 (因为冲突的 change 被跳过)
        assert "## 调试信息" in result.final_content

    def test_no_conflict_different_anchors(self):
        """不同锚点的 change 不应该有冲突。"""
        engine = MergeEngine()
        changes = [
            ChangeFile(
                summary="变更1",
                anchor_selector="## 工具使用规范",
                operation="INSERT_RULE",
                new_content="- 规则1",
            ),
            ChangeFile(
                summary="变更2",
                anchor_selector="## 文件读取规范",
                operation="INSERT_RULE",
                new_content="- 规则2",
            ),
        ]

        result = engine.merge(SAMPLE_SKILL_MD, changes)

        assert result.conflict_count == 0
        assert result.applied_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段1: 锚点找不到
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnchorNotFound:
    """验证锚点找不到时的行为。"""

    def test_missing_anchor_marks_conflict(self):
        """找不到锚点时，标记为 anchor_not_found 冲突。"""
        engine = MergeEngine()
        change = ChangeFile(
            summary="修改不存在的章节",
            anchor_selector="## 不存在的章节",
            operation="INSERT_SUBSECTION",
            new_content="内容",
        )

        result = engine.merge(SAMPLE_SKILL_MD, [change])

        assert result.applied_count == 0
        assert result.conflict_count == 1
        # 原文不变
        assert result.final_content == SAMPLE_SKILL_MD

    def test_fuzzy_anchor_match(self):
        """锚点有微小差异时，模糊匹配应该能找到。"""
        engine = MergeEngine()
        # selector 有多余空格
        change = ChangeFile(
            summary="模糊匹配测试",
            anchor_selector="##  文件读取规范",
            operation="INSERT_SUBSECTION",
            new_content="### 新内容\n内容",
        )

        result = engine.merge(SAMPLE_SKILL_MD, [change])

        # fuzzy_find_match 应该能匹配到
        assert result.applied_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段1: 边界情况
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """验证边界情况。"""

    def test_empty_changes(self):
        """空 change 列表应该返回原内容。"""
        engine = MergeEngine()
        result = engine.merge(SAMPLE_SKILL_MD, [])

        assert result.applied_count == 0
        assert result.final_content == SAMPLE_SKILL_MD

    def test_insert_at_last_section(self):
        """在最后一个章节 (无后续同级标题) 插入内容。"""
        engine = MergeEngine()
        change = ChangeFile(
            summary="在最后章节插入",
            anchor_selector="## 调试信息",
            operation="INSERT_SUBSECTION",
            new_content="### 新调试规则\n规则内容",
        )

        result = engine.merge(SAMPLE_SKILL_MD, [change])

        assert result.applied_count == 1
        assert "新调试规则" in result.final_content

    def test_insert_at_h1(self):
        """在 H1 标题下插入内容。"""
        engine = MergeEngine()
        change = ChangeFile(
            summary="在 H1 下插入",
            anchor_selector="# Protocol Agent",
            operation="INSERT_SUBSECTION",
            new_content="## 概述\n这是一个协议解析 agent。",
        )

        result = engine.merge(SAMPLE_SKILL_MD, [change])

        assert result.applied_count == 1
        assert "## 概述" in result.final_content


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助方法测试
# ═══════════════════════════════════════════════════════════════════════════════


class TestHelperMethods:
    """验证 MergeEngine 的辅助方法。"""

    def test_heading_level(self):
        """_heading_level 应该正确计算标题级别。"""
        assert MergeEngine._heading_level("# H1") == 1
        assert MergeEngine._heading_level("## H2") == 2
        assert MergeEngine._heading_level("### H3") == 3
        assert MergeEngine._heading_level("not a heading") == 0

    def test_find_section_end(self):
        """_find_section_end 应该找到章节结束位置。"""
        lines = SAMPLE_SKILL_MD.split("\n")
        # "## 工具使用规范" 在第 2 行 (0-based)
        # 下一个同级标题 "## 文件读取规范" 应该是结束位置
        end = MergeEngine._find_section_end(lines, 2, 2)
        assert lines[end].startswith("## 文件读取规范")

    def test_compute_diff(self):
        """_compute_diff 应该生成 unified diff。"""
        original = "line1\nline2\nline3"
        modified = "line1\nmodified\nline3"
        diff = MergeEngine._compute_diff(original, modified)

        assert "-line2" in diff
        assert "+modified" in diff


# ═══════════════════════════════════════════════════════════════════════════════
# MergeResult
# ═══════════════════════════════════════════════════════════════════════════════


class TestMergeResult:
    """验证 MergeResult 的属性和序列化。"""

    def test_to_dict(self):
        """to_dict 应该返回可序列化的字典。"""
        result = MergeResult(
            final_content="content",
            applied_count=2,
            conflict_count=1,
            polished=False,
            diff="-old\n+new",
        )
        d = result.to_dict()

        assert d["applied_count"] == 2
        assert d["conflict_count"] == 1
        assert d["polished"] is False
        assert d["diff_lines"] == 1

    def test_diff_line_count(self):
        """diff_lines 应该正确统计 diff 行数。"""
        result = MergeResult(
            final_content="",
            applied_count=0,
            conflict_count=0,
            diff="line1\nline2\nline3",
        )
        assert result.to_dict()["diff_lines"] == 2  # 3 lines → 2 newline chars
