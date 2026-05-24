"""Tool surface — pure functions, single source of truth.

Both `mcp_server.py` (FastMCP @tool decorator) and `agent/runner.py`
(OpenAI function calling) import from here. No conditional branching
inside the tools by who's calling — that's the whole architectural point.
"""

from __future__ import annotations

from matscout.tools.compare import compare_materials
from matscout.tools.get import MaterialNotFoundError, get_material
from matscout.tools.search import search_materials
from matscout.tools.stability import check_stability
from matscout.tools.synthesis import (
    get_competing_phases,
    get_phase_diagram,
    predict_decomposition,
)

__all__ = [
    "MaterialNotFoundError",
    "check_stability",
    "compare_materials",
    "get_competing_phases",
    "get_material",
    "get_phase_diagram",
    "predict_decomposition",
    "search_materials",
]
