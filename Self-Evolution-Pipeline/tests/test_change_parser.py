"""Stage 8 helper: ChangeParser 测试 — 验证 .change 文件的解析和生成。

=== 测试范围 ===

  1. parse_change_text() — 从文本解析 .change 格式
  2. parse_change_file() — 从文件解析
  3. load_change_dir() — 批量加载目录
  4. build_change_yaml() — 生成 .change 文件内容
  5. ChangeFile.valid — 必填字段校验

=== .change 文件格式 ===

    # Change 001
    suggestion_id: fix-protocol-agent
    suggestion_type: fix
    priority: high

    summary: 添加文件读取前的存在性检查

    anchor:
      type: heading
      selector: "## 文件读取规范"

    operation: INSERT_SUBSECTION

    new_content: |
      ### 存在性检查
      在调用 Read 工具前，先用 Bash 运行 `test -f <path>` 验证文件存在。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skill_evolution.evolution.change_parser import (
    ChangeFile,
    build_change_yaml,
    load_change_dir,
    parse_change_file,
    parse_change_text,
)


# ═══════════════════════════════════════════════════════════════════════════════
# parse_change_text — 从文本解析
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseChangeText:
    """验证从文本解析 .change 格式的核心功能。"""

    def test_parse_basic_insert_subsection(self):
        """解析标准的 INSERT_SUBSECTION 变更。"""
        text = """\
# Change 001
suggestion_id: fix-protocol-agent
suggestion_type: fix
priority: high

summary: 添加文件读取前的存在性检查

anchor:
  type: heading
  selector: "## 文件读取规范"

operation: INSERT_SUBSECTION

new_content: |
  ### 存在性检查
  在调用 Read 工具前，先用 Bash 运行 `test -f <path>` 验证文件存在。"""

        cf = parse_change_text(text)

        assert cf is not None
        assert cf.change_id == "001"
        assert cf.suggestion_id == "fix-protocol-agent"
        assert cf.suggestion_type == "fix"
        assert cf.priority == "high"
        assert cf.summary == "添加文件读取前的存在性检查"
        assert cf.anchor_type == "heading"
        assert cf.anchor_selector == "## 文件读取规范"
        assert cf.operation == "INSERT_SUBSECTION"
        assert "### 存在性检查" in cf.new_content
        assert "test -f" in cf.new_content

    def test_parse_insert_rule(self):
        """解析 INSERT_RULE 操作。"""
        text = """\
# Change 002
suggestion_id: derived-agent
suggestion_type: derived
priority: medium

summary: 添加超时重试规则

anchor:
  type: heading
  selector: "## 工具使用规范"

operation: INSERT_RULE

new_content: |
  - 文件读取超过 30 秒时自动重试一次"""

        cf = parse_change_text(text)

        assert cf is not None
        assert cf.operation == "INSERT_RULE"
        assert cf.anchor_selector == "## 工具使用规范"
        assert "30 秒" in cf.new_content

    def test_parse_delete_operation(self):
        """解析 DELETE 操作 (不需要 new_content)。"""
        text = """\
# Change 003
suggestion_id: fix-cleanup
suggestion_type: fix
priority: low

summary: 删除过时的调试说明

anchor:
  type: heading
  selector: "## 调试信息"

operation: DELETE"""

        cf = parse_change_text(text)

        assert cf is not None
        assert cf.operation == "DELETE"
        assert cf.new_content == ""
        assert cf.valid

    def test_parse_multiline_new_content(self):
        """解析包含多行内容的 new_content。"""
        text = """\
# Change 004
suggestion_id: fix-multi
suggestion_type: fix
priority: medium

summary: 添加多行规范

anchor:
  type: heading
  selector: "## 编码规范"

operation: INSERT_SUBSECTION

new_content: |
  ### 命名规范
  - 变量名使用 snake_case
  - 类名使用 PascalCase
  - 常量使用 UPPER_SNAKE_CASE

  ### 注释规范
  - 公共函数必须有 docstring
  - 复杂逻辑必须有行内注释"""

        cf = parse_change_text(text)

        assert cf is not None
        assert "snake_case" in cf.new_content
        assert "PascalCase" in cf.new_content
        assert "注释规范" in cf.new_content

    def test_parse_selector_with_single_quotes(self):
        """selector 使用单引号时应该正确去引号。"""
        text = """\
# Change 005
summary: 测试单引号
anchor:
  type: heading
  selector: '## 单引号标题'
operation: INSERT_RULE
new_content: |
  规则内容"""

        cf = parse_change_text(text)

        assert cf is not None
        assert cf.anchor_selector == "## 单引号标题"

    def test_parse_selector_without_quotes(self):
        """selector 没有引号时应该原样使用。"""
        text = """\
# Change 006
summary: 测试无引号
anchor:
  type: heading
  selector: ## 无引号标题
operation: INSERT_RULE
new_content: |
  规则内容"""

        cf = parse_change_text(text)

        assert cf is not None
        assert cf.anchor_selector == "## 无引号标题"

    def test_parse_returns_none_missing_summary(self):
        """缺少 summary 时应该返回 None。"""
        text = """\
# Change 007
anchor:
  type: heading
  selector: "## 标题"
operation: INSERT_RULE
new_content: |
  内容"""

        cf = parse_change_text(text)
        assert cf is None

    def test_parse_returns_none_missing_anchor(self):
        """缺少 anchor selector 时应该返回 None。"""
        text = """\
# Change 008
summary: 测试
operation: INSERT_RULE
new_content: |
  内容"""

        cf = parse_change_text(text)
        assert cf is None

    def test_parse_returns_none_missing_content_for_insert(self):
        """INSERT 操作缺少 new_content 时应该返回 None。"""
        text = """\
# Change 009
summary: 测试
anchor:
  type: heading
  selector: "## 标题"
operation: INSERT_SUBSECTION"""

        cf = parse_change_text(text)
        assert cf is None

    def test_parse_raw_text_preserved(self):
        """原始文本应该保存在 raw_text 字段中。"""
        text = """\
# Change 010
summary: 测试
anchor:
  type: heading
  selector: "## 标题"
operation: INSERT_RULE
new_content: |
  内容"""

        cf = parse_change_text(text)

        assert cf is not None
        assert cf.raw_text == text

    def test_parse_old_content(self):
        """解析 old_content 字段 (用于 REPLACE 操作)。"""
        text = """\
# Change 011
summary: 替换旧规则
anchor:
  type: heading
  selector: "## 旧规则"
operation: REPLACE
old_content: |
  旧的规则内容
new_content: |
  新的规则内容"""

        cf = parse_change_text(text)

        assert cf is not None
        assert "旧的规则内容" in cf.old_content
        assert "新的规则内容" in cf.new_content


# ═══════════════════════════════════════════════════════════════════════════════
# parse_change_file — 从文件解析
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseChangeFile:
    """验证从文件解析 .change 格式。"""

    def test_parse_from_file(self, tmp_dir):
        """从文件读取并解析 .change 文件。"""
        content = """\
# Change 001
suggestion_id: test-skill
suggestion_type: fix
priority: medium

summary: 测试变更

anchor:
  type: heading
  selector: "## 测试标题"

operation: INSERT_SUBSECTION

new_content: |
  ### 新增内容
  这是测试内容。"""

        path = tmp_dir / "001.change"
        path.write_text(content, encoding="utf-8")

        cf = parse_change_file(path)

        assert cf is not None
        assert cf.change_id == "001"
        assert cf.summary == "测试变更"
        assert cf.anchor_selector == "## 测试标题"

    def test_parse_nonexistent_file(self, tmp_dir):
        """不存在的文件应该返回 None。"""
        path = tmp_dir / "nonexistent.change"
        cf = parse_change_file(path)
        assert cf is None


# ═══════════════════════════════════════════════════════════════════════════════
# load_change_dir — 批量加载
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadChangeDir:
    """验证批量加载 .change 文件目录。"""

    def test_load_multiple_files(self, tmp_dir):
        """加载目录下多个 .change 文件, 按 change_id 排序。"""
        dir_path = tmp_dir / "changes"
        dir_path.mkdir()

        for i, summary in enumerate(["第一个变更", "第二个变更", "第三个变更"], 1):
            content = build_change_yaml(
                change_id=f"{i:03d}",
                summary=summary,
                anchor_selector=f"## 标题{i}",
                operation="INSERT_RULE",
                new_content=f"内容{i}",
            )
            (dir_path / f"{i:03d}.change").write_text(content, encoding="utf-8")

        changes = load_change_dir(dir_path)

        assert len(changes) == 3
        assert changes[0].summary == "第一个变更"
        assert changes[1].summary == "第二个变更"
        assert changes[2].summary == "第三个变更"

    def test_load_empty_dir(self, tmp_dir):
        """空目录应该返回空列表。"""
        dir_path = tmp_dir / "empty"
        dir_path.mkdir()

        changes = load_change_dir(dir_path)
        assert changes == []

    def test_load_nonexistent_dir(self, tmp_dir):
        """不存在的目录应该返回空列表。"""
        dir_path = tmp_dir / "nonexistent"
        changes = load_change_dir(dir_path)
        assert changes == []

    def test_load_ignores_non_change_files(self, tmp_dir):
        """应该忽略非 .change 文件。"""
        dir_path = tmp_dir / "mixed"
        dir_path.mkdir()

        # 一个 .change 文件
        content = build_change_yaml(
            change_id="001", summary="变更", anchor_selector="## 标题",
            operation="INSERT_RULE", new_content="内容",
        )
        (dir_path / "001.change").write_text(content, encoding="utf-8")

        # 一些非 .change 文件
        (dir_path / "versions.json").write_text("{}", encoding="utf-8")
        (dir_path / "001.raw").write_text("raw output", encoding="utf-8")
        (dir_path / "readme.txt").write_text("readme", encoding="utf-8")

        changes = load_change_dir(dir_path)
        assert len(changes) == 1

    def test_load_skips_invalid_files(self, tmp_dir):
        """应该跳过解析失败的文件。"""
        dir_path = tmp_dir / "partial"
        dir_path.mkdir()

        # 有效的
        content = build_change_yaml(
            change_id="001", summary="有效变更", anchor_selector="## 标题",
            operation="INSERT_RULE", new_content="内容",
        )
        (dir_path / "001.change").write_text(content, encoding="utf-8")

        # 无效的 (缺少 summary)
        (dir_path / "002.change").write_text("# Change 002\noperation: DELETE\n", encoding="utf-8")

        changes = load_change_dir(dir_path)
        assert len(changes) == 1
        assert changes[0].summary == "有效变更"


# ═══════════════════════════════════════════════════════════════════════════════
# build_change_yaml — 生成
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildChangeYaml:
    """验证 .change 文件内容生成。"""

    def test_roundtrip(self):
        """生成的 YAML 应该能被 parse_change_text 正确解析 (往返测试)。"""
        yaml_str = build_change_yaml(
            change_id="042",
            summary="添加超时处理",
            anchor_selector="## 文件读取规范",
            operation="INSERT_SUBSECTION",
            new_content="### 超时处理\n超过 30 秒自动重试",
            suggestion_id="fix-protocol",
            suggestion_type="fix",
            priority="high",
        )

        cf = parse_change_text(yaml_str)

        assert cf is not None
        assert cf.change_id == "042"
        assert cf.summary == "添加超时处理"
        assert cf.anchor_selector == "## 文件读取规范"
        assert cf.operation == "INSERT_SUBSECTION"
        assert "超时处理" in cf.new_content
        assert cf.suggestion_id == "fix-protocol"
        assert cf.suggestion_type == "fix"
        assert cf.priority == "high"

    def test_build_delete(self):
        """生成 DELETE 操作的 YAML。"""
        yaml_str = build_change_yaml(
            change_id="001",
            summary="删除过时内容",
            anchor_selector="## 旧规则",
            operation="DELETE",
            new_content="",
        )

        cf = parse_change_text(yaml_str)

        assert cf is not None
        assert cf.operation == "DELETE"
        assert cf.new_content == ""

    def test_build_with_old_content(self):
        """生成包含 old_content 的 YAML。"""
        yaml_str = build_change_yaml(
            change_id="001",
            summary="替换规则",
            anchor_selector="## 规则",
            operation="REPLACE",
            new_content="新规则内容",
            old_content="旧规则内容",
        )

        cf = parse_change_text(yaml_str)

        assert cf is not None
        assert "新规则内容" in cf.new_content
        assert "旧规则内容" in cf.old_content


# ═══════════════════════════════════════════════════════════════════════════════
# ChangeFile.valid — 校验
# ═══════════════════════════════════════════════════════════════════════════════


class TestChangeFileValid:
    """验证 ChangeFile 的必填字段校验。"""

    def test_valid_insert(self):
        """INSERT 操作需要 summary, anchor_selector, new_content。"""
        cf = ChangeFile(
            summary="test",
            anchor_selector="## heading",
            operation="INSERT_SUBSECTION",
            new_content="content",
        )
        assert cf.valid

    def test_valid_delete(self):
        """DELETE 操作不需要 new_content。"""
        cf = ChangeFile(
            summary="test",
            anchor_selector="## heading",
            operation="DELETE",
        )
        assert cf.valid

    def test_invalid_missing_summary(self):
        cf = ChangeFile(anchor_selector="## h", new_content="c")
        assert not cf.valid

    def test_invalid_missing_anchor(self):
        cf = ChangeFile(summary="s", new_content="c")
        assert not cf.valid

    def test_invalid_insert_missing_content(self):
        cf = ChangeFile(summary="s", anchor_selector="## h", operation="INSERT_SUBSECTION")
        assert not cf.valid
