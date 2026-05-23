"""Agent layer — wraps the tool functions for OpenAI function-calling."""

from __future__ import annotations

from matscout.agent.openai_adapter import tool_to_openai_spec, tools_to_openai_specs

__all__ = ["tool_to_openai_spec", "tools_to_openai_specs"]
