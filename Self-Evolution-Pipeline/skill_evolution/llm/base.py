"""LLMWithTools: shared LLM tool-use conversation loop.

Uses litellm for unified multi-provider support:
- Automatically handles provider-specific message format differences
- Built-in retry with exponential backoff
- Support for 100+ LLM providers (OpenAI, Anthropic, MiniMax, etc.)

Adapted from OpenSpace llm/client.py patterns.
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from skill_evolution.config.settings import LLMConfig
from skill_evolution.config.constants import (
    MAX_TOOL_RESULT_CHARS,
    MAX_CONVERSATION_ROUNDS,
    BACKOFF_RATE_LIMIT,
    BACKOFF_CONNECTION,
    BACKOFF_OVERLOAD,
)
from skill_evolution.exceptions import LLMError, ErrorCode
from skill_evolution.utils.logging import Logger

# Lazy import litellm (it takes ~9s to import)
_litellm = None


def _get_litellm():
    """Lazy import litellm to avoid slow startup."""
    global _litellm
    if _litellm is None:
        import litellm as _lm
        _lm.suppress_debug_info = True
        _lm.set_verbose = False
        # Suppress litellm's own logger to avoid noisy completion/Completed Call lines
        import logging as _logging
        _logging.getLogger("LiteLLM").setLevel(_logging.WARNING)
        _logging.getLogger("httpx").setLevel(_logging.WARNING)
        _litellm = _lm
    return _litellm


logger = Logger.get_logger(__name__)


def _backoff_for_error(error: LLMError, attempt: int) -> float:
    """Return backoff delay in seconds based on error category."""
    code = error.code
    if code == ErrorCode.LLM_RATE_LIMIT:
        delays = BACKOFF_RATE_LIMIT
        return delays[min(attempt, len(delays) - 1)]
    if code == ErrorCode.LLM_CONNECTION:
        delays = BACKOFF_CONNECTION
        return min(delays[min(attempt, len(delays) - 1)], 60)
    if code == ErrorCode.LLM_OVERLOAD:
        delays = BACKOFF_OVERLOAD
        return min(delays[min(attempt, len(delays) - 1)], 60)
    return 0


def _truncate_tool_result(content: str, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Truncate tool result with context-preserving marker."""
    if len(content) <= max_chars:
        return content
    half = max_chars // 2
    return (
        content[:half]
        + f"\n\n[...TRUNCATED: {len(content)} chars total, showing first and last {half}...]\n\n"
        + content[-half:]
    )


def _is_minimax_model(model: str) -> bool:
    """Check if model is a MiniMax model."""
    return isinstance(model, str) and "minimax" in model.lower()


def _merge_consecutive_system_messages(messages: list) -> list:
    """Merge consecutive system messages into one.

    Some providers (e.g. MiniMax) reject requests with multiple consecutive
    system messages (error 2013 "invalid chat setting").
    """
    if not messages:
        return messages
    merged = []
    for msg in messages:
        if (
            merged
            and msg.get("role") == "system"
            and merged[-1].get("role") == "system"
        ):
            merged[-1] = {
                "role": "system",
                "content": merged[-1].get("content", "") + "\n\n" + msg.get("content", ""),
            }
        else:
            merged.append(msg.copy() if isinstance(msg, dict) else msg)
    return merged


def _rewrite_nonleading_system_messages_for_minimax(messages: list) -> list:
    """Rewrite non-leading system messages into internal user notes for MiniMax.

    MiniMax doesn't support non-leading system messages, so we convert them
    to user messages with a special prefix.
    """
    rewritten = []
    rewritten_count = 0

    for msg in messages:
        msg_copy = msg.copy() if isinstance(msg, dict) else {"role": "user", "content": str(msg)}
        if msg_copy.get("role") == "system" and rewritten:
            content = msg_copy.get("content", "")
            if isinstance(content, str):
                msg_copy["content"] = (
                    "[INTERNAL ORCHESTRATION NOTE]\n"
                    "This note was originally injected as a system message by the "
                    "agent runtime. Treat it as workflow guidance, not as a new "
                    "end-user request.\n\n"
                    f"{content}"
                )
            msg_copy["role"] = "user"
            rewritten_count += 1
        rewritten.append(msg_copy)

    if rewritten_count:
        logger.debug(
            "Rewrote %d non-leading system message(s) for MiniMax compatibility",
            rewritten_count,
        )

    return rewritten


def _normalize_messages_for_model(messages: list, model: str) -> list:
    """Normalize message history for provider-specific requirements."""
    if not _is_minimax_model(model):
        return messages

    minimized = _merge_consecutive_system_messages(messages)
    return _rewrite_nonleading_system_messages_for_minimax(minimized)


def _normalize_model_for_litellm(model: str, api_base: str = "") -> str:
    """Normalize model name for litellm.

    litellm requires provider prefix (e.g. 'openai/gpt-4') unless using a
    standard provider. For custom API endpoints, we add 'openai/' prefix.
    """
    if not model:
        return model

    # If model already has a provider prefix, return as-is
    if "/" in model:
        return model

    # If using a custom API base (not a standard provider), prefix with openai
    if api_base:
        return f"openai/{model}"

    return model


def _convert_tools_for_litellm(tools: list) -> list:
    """Convert Anthropic-style tools to OpenAI function format for litellm."""
    converted = []
    for tool in tools:
        # Anthropic format: {"name": "...", "description": "...", "input_schema": {...}}
        # OpenAI/litellm format: {"type": "function", "function": {...}}
        func_def = {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
        }
        if "input_schema" in tool:
            func_def["parameters"] = tool["input_schema"]
        elif "parameters" in tool:
            func_def["parameters"] = tool["parameters"]
        else:
            func_def["parameters"] = {"type": "object", "properties": {}}

        converted.append({
            "type": "function",
            "function": func_def
        })
    return converted


def _serialize_response_field(value):
    """Convert provider response fields into plain Python containers."""
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return [_serialize_response_field(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_response_field(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_response_field(item) for key, item in value.items()}
    return value


class LLMWithTools:
    """Multi-provider LLM tool-use conversation loop using litellm."""

    def __init__(self, config: LLMConfig, tools: Optional[list[dict]] = None):
        self.config = config
        self.tools = tools or []
        self._tool_handlers: dict[str, Callable] = {}
        self._litellm_kwargs: dict = {}

        # Build litellm kwargs from config
        self._build_litellm_kwargs()

    def _build_litellm_kwargs(self) -> None:
        """Build litellm kwargs from LLMConfig."""
        if self.config.api_key:
            self._litellm_kwargs["api_key"] = self.config.api_key
        if self.config.api_base:
            self._litellm_kwargs["api_base"] = self.config.api_base

        # Normalize model name for litellm
        self._normalized_model = _normalize_model_for_litellm(
            self.config.model,
            self.config.api_base or ""
        )

    def register_tool(self, name: str, handler: Callable) -> None:
        """Register a tool handler. handler(**kwargs) -> str."""
        self._tool_handlers[name] = handler

    def run_conversation(
        self,
        system: str,
        messages: list[dict],
        max_rounds: int = MAX_CONVERSATION_ROUNDS,
    ) -> str:
        """Multi-turn tool-use loop. Returns final text response.

        Termination:
        - LLM produces text without tool_use -> returns that text
        - max_rounds exhausted -> raises LLMError
        """
        # Normalize model-specific message formats
        normalized_messages = _normalize_messages_for_model(messages, self._normalized_model)

        # Build litellm completion kwargs (messages added per round)
        kwargs = {
            "model": self._normalized_model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            **self._litellm_kwargs,
        }

        # Add tools if present
        llm_tools = None
        if self.tools:
            llm_tools = _convert_tools_for_litellm(self.tools)
            kwargs["tools"] = llm_tools
            kwargs["tool_choice"] = "auto"

        # Build initial messages with system prompt
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(normalized_messages)

        for round_num in range(max_rounds):
            try:
                # Normalize messages for this round
                round_messages = _normalize_messages_for_model(all_messages, self._normalized_model)

                response = _get_litellm().completion(
                    **kwargs,
                    messages=round_messages,
                )

                if not response.choices:
                    raise LLMError("LLM response has no choices", code=ErrorCode.LLM_FATAL)

                response_message = response.choices[0].message
                tool_calls = getattr(response_message, "tool_calls", None)

                if not tool_calls:
                    # No tool calls - return the text response
                    return response_message.content or ""

                # Execute tool calls
                all_messages.append({
                    "role": "assistant",
                    "content": response_message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in tool_calls
                    ]
                })

                for tc in tool_calls:
                    tool_name = tc.function.name
                    handler = self._tool_handlers.get(tool_name)

                    # Try to parse arguments
                    args = {}
                    try:
                        if isinstance(tc.function.arguments, str):
                            args = json.loads(tc.function.arguments or "{}")
                        else:
                            args = tc.function.arguments or {}
                    except json.JSONDecodeError:
                        args = {}

                    if handler:
                        try:
                            result = handler(**args)
                        except Exception as e:
                            logger.warning(f"Tool '{tool_name}' raised: {e}")
                            result = f"Error executing {tool_name}: {e}"
                    else:
                        result = f"Error: unknown tool '{tool_name}'"

                    result_str = _truncate_tool_result(str(result))
                    all_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })

            except LLMError:
                raise
            except Exception as e:
                llm_error = LLMError.from_exception(e, attempt=round_num)
                logger.warning(
                    f"LLM call failed (round {round_num + 1}/{max_rounds}): "
                    f"[{llm_error.code}] {e}"
                )
                if round_num < max_rounds - 1 and llm_error.retryable:
                    delay = _backoff_for_error(llm_error, round_num)
                    logger.info(f"Retrying in {delay:.0f}s...")
                    time.sleep(delay)
                    continue
                raise LLMError(
                    f"Tool-use loop failed at round {round_num + 1}: {e}",
                    code=llm_error.code,
                    round=round_num + 1,
                ) from e

        raise LLMError(
            f"Tool-use loop exceeded {max_rounds} rounds without final response.",
            code=ErrorCode.LLM_FATAL,
            max_rounds=max_rounds,
        )

    def call_once(self, system: str, user_prompt: str) -> str:
        """Simple single-turn LLM call (no tools) with categorized retry."""
        last_error: Optional[Exception] = None

        # Build messages
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_prompt})

        # Normalize for model
        messages = _normalize_messages_for_model(messages, self._normalized_model)

        kwargs = {
            "model": self._normalized_model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "messages": messages,
            **self._litellm_kwargs,
        }

        for attempt in range(self.config.max_retries):
            try:
                response = _get_litellm().completion(**kwargs)
                if response.choices:
                    return response.choices[0].message.content or ""
                raise LLMError("No choices in response", code=ErrorCode.LLM_FATAL)
            except Exception as e:
                last_error = e
                llm_error = LLMError.from_exception(e, attempt=attempt)
                logger.warning(
                    f"LLM call_once failed (attempt {attempt + 1}/{self.config.max_retries}): "
                    f"[{llm_error.code}] {e}"
                )
                if attempt < self.config.max_retries - 1:
                    if not llm_error.retryable:
                        raise llm_error
                    delay = _backoff_for_error(llm_error, attempt)
                    logger.info(f"Retrying in {delay:.0f}s...")
                    time.sleep(delay)

        raise LLMError(
            f"LLM call_once failed after {self.config.max_retries} attempts: {last_error}",
            code=ErrorCode.LLM_FATAL,
            attempts=self.config.max_retries,
        )