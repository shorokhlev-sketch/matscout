"""Tool surface — pure functions, single source of truth.

Both `mcp_server.py` (FastMCP @tool decorator) and `agent/runner.py`
(OpenAI function calling) import from here. No conditional branching
inside the tools by who's calling — that's the whole architectural point.
"""

from __future__ import annotations

from matscout.tools.applications import (
    find_battery_anode,
    find_battery_cathode,
    find_solar_absorber,
    find_thermoelectric,
    find_transparent_conductor,
)
from matscout.tools.compare import compare_materials
from matscout.tools.get import MaterialNotFoundError, get_material
from matscout.tools.jarvis import find_2d_materials, get_jarvis_topological
from matscout.tools.literature import (
    find_papers,
    find_preprints,
    get_doi_metadata,
    get_papers_about,
)
from matscout.tools.phase_diagram_strict import compute_phase_diagram_strict
from matscout.tools.properties import get_elastic_properties, get_electronic_summary
from matscout.tools.ranking import pareto_rank
from matscout.tools.search import search_materials
from matscout.tools.stability import check_stability
from matscout.tools.structure import get_structure
from matscout.tools.synthesis import (
    get_competing_phases,
    get_phase_diagram,
    predict_decomposition,
)

__all__ = [
    "MaterialNotFoundError",
    "check_stability",
    "compare_materials",
    "compute_phase_diagram_strict",
    "find_2d_materials",
    "find_battery_anode",
    "find_battery_cathode",
    "find_papers",
    "find_preprints",
    "find_solar_absorber",
    "find_thermoelectric",
    "find_transparent_conductor",
    "get_competing_phases",
    "get_doi_metadata",
    "get_elastic_properties",
    "get_electronic_summary",
    "get_jarvis_topological",
    "get_material",
    "get_papers_about",
    "get_phase_diagram",
    "get_structure",
    "pareto_rank",
    "predict_decomposition",
    "search_materials",
]
