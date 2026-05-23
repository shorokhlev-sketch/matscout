"""Convert plain Python tool functions into OpenAI function-call schemas.

The agent layer wants to expose the same tool functions to gpt-4o as the
MCP layer does to Claude — but OpenAI's API speaks JSON Schema for tool
parameters. This module bridges the two without writing schemas by hand:

    - Build a Pydantic model dynamically from the function's signature.
    - Have Pydantic emit the JSON schema for that model.
    - Wrap it in OpenAI's tool envelope.

That way our type hints stay the single source of truth.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

# OpenAI's "strict" mode requires additionalProperties: false at every level
# and every property in "required". We post-process the schema to honour that.


def _signature_to_pydantic_model(fn: Callable[..., Any]) -> type[BaseModel]:
    """Build a Pydantic model that mirrors fn's argument list."""
    sig = inspect.signature(fn)
    fields: dict[str, Any] = {}

    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            # *args / **kwargs don't translate to JSON-schema cleanly.
            continue

        annotation = param.annotation
        if annotation is inspect.Parameter.empty:
            annotation = Any

        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (annotation, Field(default, description=None))

    model: type[BaseModel] = create_model(
        f"_{fn.__name__.title().replace('_', '')}Input",
        __config__=ConfigDict(arbitrary_types_allowed=True, extra="forbid"),
        **fields,
    )
    return model


def _strip_pydantic_chrome(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop Pydantic-only keys that OpenAI's schema validator dislikes."""
    schema = dict(schema)
    schema.pop("title", None)
    if "$defs" in schema:
        schema["$defs"] = {k: _strip_pydantic_chrome(v) for k, v in schema["$defs"].items()}
    for prop in schema.get("properties", {}).values():
        if isinstance(prop, dict):
            prop.pop("title", None)
    return schema


def _short_description(fn: Callable[..., Any]) -> str:
    """First paragraph of the docstring → what gpt-4o reads to pick a tool."""
    doc = inspect.getdoc(fn) or fn.__name__.replace("_", " ")
    return doc.split("\n\n", 1)[0].strip()


def tool_to_openai_spec(fn: Callable[..., Any], *, name: str | None = None) -> dict[str, Any]:
    """Build the OpenAI ``tools=[...]`` entry for one Python function."""
    model = _signature_to_pydantic_model(fn)
    schema = _strip_pydantic_chrome(model.model_json_schema())
    return {
        "type": "function",
        "function": {
            "name": name or fn.__name__,
            "description": _short_description(fn),
            "parameters": schema,
        },
    }


def tools_to_openai_specs(fns: list[Callable[..., Any]]) -> list[dict[str, Any]]:
    """Bulk-build the ``tools=[...]`` list for a Chat Completions request."""
    return [tool_to_openai_spec(fn) for fn in fns]
