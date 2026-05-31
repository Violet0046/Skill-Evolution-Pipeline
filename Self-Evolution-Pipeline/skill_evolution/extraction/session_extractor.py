"""Extract CanonicalSession from Claude Code agent JSONL files.

Parses the raw JSONL conversation format into structured CanonicalSession objects.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from skill_evolution.models.session import (
    CanonicalSession, TaskInput, ExecutionTrace, ExecutionStatus,
    Message, MessageRole, ToolCall, TokenUsage, Feedback,
)


class SessionExtractor:
    """Extracts structured session data from Claude Code JSONL files."""

    def __init__(self, max_content_length: int = 500):
        self.max_content_length = max_content_length

    def extract_from_file(self, jsonl_path: str) -> CanonicalSession:
        """Parse a JSONL file and return a CanonicalSession."""
        records = self._read_jsonl(jsonl_path)
        if not records:
            return CanonicalSession()

        session = CanonicalSession()
        self._extract_metadata(session, records)
        self._extract_task_input(session, records)
        self._extract_messages(session, records)
        self._compute_execution_trace(session)
        self._extract_feedback(session, records)
        return session

    def _read_jsonl(self, path: str) -> list[dict]:
        records = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def _extract_metadata(self, session: CanonicalSession, records: list[dict]) -> None:
        """Extract session-level metadata from records."""
        first = records[0]
        session.agent_id = first.get("agentId", "")
        session.timestamp = first.get("timestamp", "")
        session.session_id = first.get("sessionId", "")

        # find attribution agent
        for r in records:
            agent = r.get("attributionAgent", "")
            if agent:
                session.skill_name = agent
                break

        session.metadata = {
            "session_path": records[0].get("metadata", {}).get("session_path", ""),
            "prompt_id": first.get("promptId", ""),
            "entrypoint": first.get("entrypoint", ""),
            "version": first.get("version", ""),
            "cwd": first.get("cwd", ""),
            "file_path": "",
        }

    def _extract_task_input(self, session: CanonicalSession, records: list[dict]) -> None:
        """Extract the task description from the first user message."""
        task = TaskInput()

        for r in records:
            if r.get("type") != "user":
                continue
            msg = r.get("message", {})
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")

            # handle string content
            if isinstance(content, str):
                task.raw_content = content
                task.working_directory = r.get("cwd", "")
                self._parse_task_fields(task, content)
                break

            # handle array content (first user message with text blocks)
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                if texts:
                    raw = "\n".join(texts)
                    task.raw_content = raw
                    task.working_directory = r.get("cwd", "")
                    self._parse_task_fields(task, raw)
                    break

        # extract skill content from meta messages
        for r in records:
            if r.get("type") != "user" or not r.get("isMeta"):
                continue
            msg = r.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if "skill-format" in text or "Base directory for this skill" in text:
                            task.skill_content = text
                            break

        session.task_input = task

    def _parse_task_fields(self, task: TaskInput, content: str) -> None:
        """Parse structured fields from the task content."""
        # extract requirement ID
        m = re.search(r"需求ID[：:]\s*(\S+)", content)
        if m:
            task.requirement_id = m.group(1)

        # extract requirement title
        m = re.search(r"需求标题[：:]\s*(.+)", content)
        if m:
            task.requirement_title = m.group(1).strip()

        # extract requirement type
        m = re.search(r"需求类型[：:]\s*(\S+)", content)
        if m:
            task.requirement_type = m.group(1)

        # extract task description (first meaningful line or section)
        m = re.search(r"(?:任务|##\s*任务)[：:]\s*(.+)", content)
        if m:
            task.task_description = m.group(1).strip()
        elif "重试" in content:
            task.task_description = "重试任务"
        else:
            # use first non-empty line as description
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    task.task_description = line[:200]
                    break

    def _extract_messages(self, session: CanonicalSession, records: list[dict]) -> None:
        """Extract all conversation messages."""
        messages = []
        call_index = 0

        for r in records:
            msg_type = r.get("type", "")
            message = r.get("message", {})
            role = message.get("role", "")
            record_uuid = r.get("uuid", "")

            if msg_type == "attachment":
                continue

            if msg_type == "user" and role == "user":
                content = message.get("content", "")
                if isinstance(content, str):
                    messages.append(Message(
                        role=MessageRole.USER,
                        content_text=content,
                        timestamp=r.get("timestamp", ""),
                        uuid=record_uuid,
                    ))
                elif isinstance(content, list):
                    # could be tool_results or text blocks
                    tool_results = []
                    texts = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            tool_results.append({
                                "tool_use_id": block.get("tool_use_id", ""),
                                "content": str(block.get("content", ""))[:self.max_content_length],
                            })
                        elif block.get("type") == "text":
                            texts.append(block.get("text", ""))
                    if tool_results:
                        messages.append(Message(
                            role=MessageRole.TOOL,
                            tool_results=tool_results,
                            timestamp=r.get("timestamp", ""),
                            uuid=record_uuid,
                        ))
                    elif texts:
                        messages.append(Message(
                            role=MessageRole.USER,
                            content_text="\n".join(texts),
                            timestamp=r.get("timestamp", ""),
                            uuid=record_uuid,
                        ))

            elif msg_type == "assistant" and role == "assistant":
                content = message.get("content", [])
                text_parts = []
                tool_calls = []

                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_calls.append(ToolCall(
                                tool_name=block.get("name", ""),
                                tool_use_id=block.get("id", ""),
                                call_index=call_index,
                                input_summary=json.dumps(
                                    block.get("input", {}),
                                    ensure_ascii=False
                                )[:self.max_content_length],
                            ))
                            call_index += 1

                usage_data = message.get("usage", {})
                usage = TokenUsage(
                    input_tokens=usage_data.get("input_tokens", 0),
                    output_tokens=usage_data.get("output_tokens", 0),
                    cache_creation_tokens=usage_data.get("cache_creation_input_tokens", 0),
                    cache_read_tokens=usage_data.get("cache_read_input_tokens", 0),
                )

                messages.append(Message(
                    role=MessageRole.ASSISTANT,
                    content_text="\n".join(text_parts),
                    tool_calls=tool_calls,
                    model=message.get("model", ""),
                    usage=usage,
                    timestamp=r.get("timestamp", ""),
                    uuid=r.get("uuid", ""),
                ))

        session.messages = messages

    def _compute_execution_trace(self, session: CanonicalSession) -> None:
        """Compute aggregated execution metrics from messages."""
        trace = ExecutionTrace()
        all_tool_calls = []
        models = set()
        total_usage = TokenUsage()

        for msg in session.messages:
            trace.total_messages += 1

            if msg.role == MessageRole.ASSISTANT:
                if msg.model:
                    models.add(msg.model)
                if msg.usage:
                    total_usage.input_tokens += msg.usage.input_tokens
                    total_usage.output_tokens += msg.usage.output_tokens
                    total_usage.cache_creation_tokens += msg.usage.cache_creation_tokens
                    total_usage.cache_read_tokens += msg.usage.cache_read_tokens

                for tc in msg.tool_calls:
                    all_tool_calls.append(tc)

            # match tool results back to tool calls
            if msg.role == MessageRole.TOOL:
                result_ids = {tr["tool_use_id"] for tr in msg.tool_results}
                for tc in all_tool_calls:
                    if tc.tool_use_id in result_ids and not tc.result_summary:
                        for tr in msg.tool_results:
                            if tr["tool_use_id"] == tc.tool_use_id:
                                tc.result_summary = tr["content"]
                                break

        trace.total_tool_calls = len(all_tool_calls)
        trace.tool_call_details = all_tool_calls
        trace.total_token_usage = total_usage
        trace.models_used = list(models)

        # determine execution status
        trace.status = self._determine_status(session)

        # compute duration
        if session.messages:
            try:
                start = datetime.fromisoformat(
                    session.messages[0].timestamp.replace("Z", "+00:00")
                )
                end = datetime.fromisoformat(
                    session.messages[-1].timestamp.replace("Z", "+00:00")
                )
                trace.duration_seconds = (end - start).total_seconds()
            except (ValueError, IndexError):
                pass

        session.execution = trace

    def _determine_status(self, session: CanonicalSession) -> ExecutionStatus:
        """Determine execution status from message patterns."""
        full_text = " ".join(
            msg.content_text for msg in session.messages if msg.content_text
        )

        # check for retry indicators in task input
        is_retry = "重试" in session.task_input.raw_content

        # check for error indicators
        has_error = any(kw in full_text.lower() for kw in [
            "error", "failed", "失败", "错误", "exception",
        ])

        # check for success indicators
        has_success = any(kw in full_text for kw in [
            "完成", "success", "成功", "已生成", "已输出",
        ])

        if is_retry and has_success:
            return ExecutionStatus.RETRY_SUCCESS
        elif is_retry and has_error:
            return ExecutionStatus.FAILED
        elif has_error and not has_success:
            return ExecutionStatus.FAILED
        elif has_success:
            return ExecutionStatus.SUCCESS
        else:
            return ExecutionStatus.UNKNOWN

    def _extract_feedback(self, session: CanonicalSession, records: list[dict]) -> None:
        """Extract feedback information from retry context."""
        feedback = Feedback()

        for r in records:
            if r.get("type") != "user":
                continue
            msg = r.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = "\n".join(texts)
            if not isinstance(content, str):
                continue

            # detect retry context
            if "重试" in content or "审查反馈" in content:
                feedback.is_retry = True

            # extract failure reason
            m = re.search(r"(?:失败原因|审查失败原因)[：:]\s*(.+)", content)
            if m:
                feedback.failure_reason = m.group(1).strip()

            # extract retry reason
            m = re.search(r"(?:retry_reason|重试原因)[：:]\s*(.+)", content)
            if m:
                feedback.retry_reason = m.group(1).strip()

            # extract correction suggestion
            m = re.search(r"(?:修正建议|correction_suggestion)[：:]\s*(.+)", content)
            if m:
                feedback.correction_suggestion = m.group(1).strip()

        # use sessions.jsonl metadata if available
        feedback.quality_score = session.feedback.quality_score
        feedback.relevance_level = session.feedback.relevance_level
        feedback.is_direct_call = session.feedback.is_direct_call

        session.feedback = feedback

    def extract_with_index(
        self, jsonl_path: str, index_entry: Optional[dict] = None
    ) -> CanonicalSession:
        """Extract session and enrich with sessions.jsonl index metadata."""
        session = self.extract_from_file(jsonl_path)

        if index_entry:
            session.upload_time = index_entry.get("upload_time", "")
            session.feedback.quality_score = index_entry.get("relevance_score", 0)
            session.feedback.relevance_level = index_entry.get("relevance_level", "")
            session.feedback.is_direct_call = index_entry.get("is_direct_call", False)
            session.metadata["session_path"] = index_entry.get("session_path", "")

        return session
