"""LLMWithTools: shared Anthropic tool-use conversation loop.

Adapted from OpenSpace llm/client.py patterns:
- Categorized error retry (rate limit, connection, overload)
- Exponential backoff per category with configurable delays
- Structured logging throughout via Logger facade
- Custom exception hierarchy (LLMError) for typed error handling
- Tool result truncation with context-preserving markers
"""
from __future__ import annotations

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


class LLMWithTools:
    """Anthropic API tool-use conversation loop with production-grade error handling."""

    def __init__(self, config: LLMConfig, tools: Optional[list[dict]] = None):
        self.config = config
        self.tools = tools or []
        self._tool_handlers: dict[str, Callable] = {}
        self._client = None

    def _get_client(self):
        if self._client is None:
            if self.config.provider == "anthropic":
                import anthropic
                self._client = anthropic.Anthropic()
            else:
                raise LLMError(
                    f"Unsupported LLM provider: {self.config.provider}",
                    code=ErrorCode.LLM_FATAL,
                )
        return self._client

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
        client = self._get_client()

        for round_num in range(max_rounds):
            try:
                kwargs = dict(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    system=system,
                    messages=messages,
                    timeout=self.config.timeout,
                )
                if self.tools:
                    kwargs["tools"] = self.tools

                response = client.messages.create(**kwargs)

                tool_uses = [b for b in response.content if b.type == "tool_use"]

                if not tool_uses:
                    texts = [b.text for b in response.content if hasattr(b, "text") and b.text]
                    return "\n".join(texts)

                # Execute tools and build results
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for tu in tool_uses:
                    handler = self._tool_handlers.get(tu.name)
                    if handler:
                        try:
                            result = handler(**tu.input)
                        except Exception as e:
                            logger.warning(f"Tool '{tu.name}' raised: {e}")
                            result = f"Error executing {tu.name}: {e}"
                    else:
                        result = f"Error: unknown tool '{tu.name}'"

                    result_str = _truncate_tool_result(str(result))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": result_str,
                    })
                messages.append({"role": "user", "content": tool_results})

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
        client = self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                response = client.messages.create(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    system=system,
                    messages=[{"role": "user", "content": user_prompt}],
                    timeout=self.config.timeout,
                )
                return response.content[0].text
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
