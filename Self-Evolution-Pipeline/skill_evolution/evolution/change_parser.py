"""Stage 8 helper — .change file parser and loader.

Parses the YAML-like .change files produced by Stage 7 (SkillEvolver).
Each file describes a single atomic change to a SKILL.md file.

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

=== 与 SkillEvolver 的关系 ===

    SkillEvolver._try_parse_change_format()  ← 解析 LLM 原始输出
    SkillEvolver._build_change_yaml()        ← 生成 .change 文件

    本模块:
    parse_change_file()  ← 解析已写入磁盘的 .change 文件 (更宽容)
    build_change_yaml()  ← 生成 .change 文件 (同 _build_change_yaml)
    load_change_dir()    ← 批量加载目录下所有 .change 文件
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from skill_evolution.utils.logging import Logger

logger = Logger.get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ChangeFile:
    """一个 .change 文件的完整解析结果。"""

    change_id: str = ""              # 变更编号, e.g. "001"
    suggestion_id: str = ""          # 来源建议 ID, e.g. "fix-protocol-agent"
    suggestion_type: str = ""        # "fix" | "derived"
    priority: str = "medium"         # "low" | "medium" | "high" | "critical"
    summary: str = ""                # 变更摘要
    anchor_type: str = "heading"     # 锚点类型 (目前只有 "heading")
    anchor_selector: str = ""        # 锚点标题文本, e.g. "## 文件读取规范"
    operation: str = "INSERT_SUBSECTION"  # "INSERT_SUBSECTION" | "INSERT_RULE" | "DELETE"
    new_content: str = ""            # 要插入的新内容
    old_content: str = ""            # DELETE/REPLACE 时的旧内容
    raw_text: str = ""               # 原始文件内容 (调试用)

    @property
    def valid(self) -> bool:
        """检查必填字段是否完整。"""
        if not self.summary:
            return False
        if not self.anchor_selector:
            return False
        if self.operation != "DELETE" and not self.new_content:
            return False
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# 解析
# ═══════════════════════════════════════════════════════════════════════════════


def parse_change_file(path: Path) -> Optional[ChangeFile]:
    """解析单个 .change 文件。

    使用状态机逐行解析 YAML-like 格式。
    比 SkillEvolver._try_parse_change_format() 更宽容:
    - 不会在遇到 LLM thinking 模式时停止 (文件已经是最终版本)
    - 支持所有字段 (suggestion_id, suggestion_type, priority 等)

    Args:
        path: .change 文件路径

    Returns:
        ChangeFile 对象, 解析失败返回 None
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to read {path}: {e}")
        return None

    return parse_change_text(text)


def parse_change_text(text: str) -> Optional[ChangeFile]:
    """从文本解析 .change 内容。

    与 parse_change_file 相同逻辑, 但接受字符串输入。
    方便测试和从 LLM 输出直接解析。
    """
    result = ChangeFile(raw_text=text)
    lines = text.split("\n")

    state = "init"
    in_content = False       # 是否正在收集多行内容
    content_target = ""      # "new_content" 或 "old_content"
    content_lines: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # ── # Change NNN ──
        if line.startswith("# Change "):
            parts = line.split()
            if len(parts) >= 3:
                result.change_id = parts[2].strip()
            i += 1
            continue

        # ── 正在收集多行内容 ──
        if in_content:
            # 多行内容以 2 空格缩进 (build_change_yaml 的格式)
            # 遇到非缩进行或文件结尾时停止
            if line and not line[0].isspace() and not line.startswith("  "):
                # 遇到新的顶层 key, 停止收集
                in_content = False
                _flush_content(result, content_target, content_lines)
                content_lines = []
                # 不跳过, 重新处理当前行
                continue
            else:
                # 去掉 2 空格缩进
                if line.startswith("  "):
                    content_lines.append(line[2:])
                else:
                    content_lines.append(line)
                i += 1
                continue

        # ── 顶层 key-value ──
        if line.startswith("suggestion_id:"):
            result.suggestion_id = line.split(":", 1)[1].strip()
            state = "suggestion_id"
        elif line.startswith("suggestion_type:"):
            result.suggestion_type = line.split(":", 1)[1].strip()
            state = "suggestion_type"
        elif line.startswith("priority:"):
            result.priority = line.split(":", 1)[1].strip()
            state = "priority"
        elif line.startswith("summary:"):
            result.summary = line.split(":", 1)[1].strip()
            state = "summary"
        elif line.startswith("anchor:"):
            state = "anchor"
        elif line.startswith("  type:") and state == "anchor":
            result.anchor_type = line.split(":", 1)[1].strip()
        elif line.startswith("  selector:") and state == "anchor":
            selector = line.split(":", 1)[1].strip()
            # 去掉引号
            if len(selector) >= 2 and selector[0] in ('"', "'") and selector[-1] == selector[0]:
                selector = selector[1:-1]
            result.anchor_selector = selector
        elif line.startswith("operation:"):
            result.operation = line.split(":", 1)[1].strip()
            state = "operation"
        elif line.startswith("new_content:"):
            state = "new_content"
            content_target = "new_content"
            # 检查是否有 | 后的内联内容
            after_colon = line.split(":", 1)[1].strip()
            if after_colon and after_colon != "|":
                # 内联内容 (少见)
                result.new_content = after_colon
            i += 1
            continue
        elif line.startswith("old_content:"):
            state = "old_content"
            content_target = "old_content"
            i += 1
            continue

        # ── 检查是否开始收集多行内容 ──
        if state in ("new_content", "old_content") and not in_content:
            # 等待第一个非空行开始收集
            if line.strip():
                in_content = True
                content_lines = []
                # 当前行就是内容的第一行
                if line.startswith("  "):
                    content_lines.append(line[2:])
                else:
                    content_lines.append(line)

        i += 1

    # ── 收尾: 文件结尾时仍在收集内容 ──
    if in_content and content_lines:
        _flush_content(result, content_target, content_lines)

    if not result.valid:
        logger.debug(f"Invalid change file: summary={bool(result.summary)}, "
                     f"anchor={bool(result.anchor_selector)}, "
                     f"content={bool(result.new_content)}, op={result.operation}")
        return None

    return result


def _flush_content(result: ChangeFile, target: str, lines: list[str]) -> None:
    """将收集到的多行内容写入对应字段。"""
    content = "\n".join(lines).rstrip()
    content = _strip_trailing_fences(content)
    if target == "new_content":
        result.new_content = content
    elif target == "old_content":
        result.old_content = content


def _strip_trailing_fences(content: str) -> str:
    """去掉内容尾部多余的 markdown code fence (```)。

    Stage 7 生成的 .change 文件有时会在 new_content 末尾
    带上 ```，这是 LLM 输出的残留，需要清理。
    """
    lines = content.split("\n")
    # 从尾部开始，删除只包含 ``` (可能有空白) 的行
    while lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines).rstrip()


# ═══════════════════════════════════════════════════════════════════════════════
# 目录加载
# ═══════════════════════════════════════════════════════════════════════════════


def load_change_dir(changes_dir: Path) -> list[ChangeFile]:
    """加载目录下所有 .change 文件, 按 change_id 排序。

    Args:
        changes_dir: 包含 .change 文件的目录

    Returns:
        解析成功的 ChangeFile 列表, 按 change_id 数值排序
    """
    if not changes_dir.is_dir():
        logger.warning(f"Changes directory not found: {changes_dir}")
        return []

    changes: list[ChangeFile] = []
    for path in sorted(changes_dir.glob("*.change")):
        cf = parse_change_file(path)
        if cf is not None:
            changes.append(cf)
        else:
            logger.warning(f"Failed to parse: {path.name}")

    # 按 change_id 数值排序 (如果都是数字的话)
    def _sort_key(cf: ChangeFile) -> int:
        try:
            return int(cf.change_id)
        except (ValueError, TypeError):
            return 999

    changes.sort(key=_sort_key)
    logger.info(f"Loaded {len(changes)} change files from {changes_dir}")
    return changes


# ═══════════════════════════════════════════════════════════════════════════════
# 生成
# ═══════════════════════════════════════════════════════════════════════════════


def build_change_yaml(
    change_id: str,
    summary: str,
    anchor_selector: str,
    operation: str,
    new_content: str,
    *,
    suggestion_id: str = "",
    suggestion_type: str = "",
    priority: str = "medium",
    anchor_type: str = "heading",
    old_content: str = "",
) -> str:
    """生成 .change 文件的 YAML 内容。

    与 SkillEvolver._build_change_yaml() 功能相同,
    但作为独立函数, 不依赖 ParsedChange 对象。

    Args:
        change_id: 变更编号, e.g. "001"
        summary: 变更摘要
        anchor_selector: 锚点标题文本
        operation: "INSERT_SUBSECTION" | "INSERT_RULE" | "DELETE"
        new_content: 要插入的新内容
        suggestion_id: 来源建议 ID
        suggestion_type: "fix" | "derived"
        priority: "low" | "medium" | "high" | "critical"
        anchor_type: 锚点类型
        old_content: DELETE/REPLACE 时的旧内容

    Returns:
        YAML 格式的字符串
    """
    lines = [
        f"# Change {change_id}",
        f"suggestion_id: {suggestion_id}",
        f"suggestion_type: {suggestion_type}",
        f"priority: {priority}",
        "",
        "summary: " + summary,
        "",
        "anchor:",
        f"  type: {anchor_type}",
        f'  selector: "{anchor_selector}"',
        "",
        f"operation: {operation}",
    ]

    if new_content:
        lines.extend(["", "new_content: |"])
        for content_line in new_content.split("\n"):
            lines.append(f"  {content_line}")

    if old_content:
        lines.extend(["", "old_content: |"])
        for content_line in old_content.split("\n"):
            lines.append(f"  {content_line}")

    return "\n".join(lines)
