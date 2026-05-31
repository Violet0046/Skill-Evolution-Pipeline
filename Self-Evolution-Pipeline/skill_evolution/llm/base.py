"""LLMWithTools: shared Anthropic tool-use conversation loop.

Both EvidenceAnalyzer and SkillEvolver use this to enable multi-turn
tool-use conversations with the Anthropic API.
"""
from __future__ import annotations

import time
from typing import Callable

from skill_evolution.config.settings import LLMConfig


class LLMWithTools:
    """Anthropic API tool-use conversation loop."""

    def __init__(self, config: LLMConfig, tools: list[dict] | None = None):
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
                raise ValueError(f"Unsupported LLM provider: {self.config.provider}")
        return self._client

    def register_tool(self, name: str, handler: Callable):
        """Register a tool handler. handler(**kwargs) -> str."""
        self._tool_handlers[name] = handler

    def run_conversation(
        self,
        system: str,
        messages: list[dict],
        max_rounds: int = 10,
    ) -> str:
        """Multi-turn tool-use loop. Returns final text response.

        Args:
            system: system prompt
            messages: initial messages list (will be mutated)
            max_rounds: max tool-use rounds before giving up

        Returns:
            Final text response from the LLM
        """
        client = self._get_client()
        last_error = None

        for round_num in range(max_rounds):
            try:
                kwargs = dict(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    system=system,
                    messages=messages,
                )
                if self.tools:
                    kwargs["tools"] = self.tools

                response = client.messages.create(**kwargs)

                # Collect tool_use blocks
                tool_uses = [b for b in response.content if b.type == "tool_use"]

                if not tool_uses:
                    # No tool calls — extract final text
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
                            result = f"Error executing {tu.name}: {e}"
                    else:
                        result = f"Error: unknown tool '{tu.name}'"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": str(result),
                    })
                messages.append({"role": "user", "content": tool_results})

            except Exception as e:
                last_error = e
                if round_num < max_rounds - 1:
                    time.sleep(self.config.retry_delay * (round_num + 1))

        raise RuntimeError(
            f"Tool-use loop exceeded {max_rounds} rounds. Last error: {last_error}"
        )

    def call_once(self, system: str, user_prompt: str) -> str:
        """Simple single-turn LLM call (no tools). For backward compatibility."""
        client = self._get_client()
        last_error = None

        for attempt in range(self.config.max_retries):
            try:
                response = client.messages.create(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    system=system,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return response.content[0].text
            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))

        raise RuntimeError(f"LLM call failed after {self.config.max_retries} attempts: {last_error}")
