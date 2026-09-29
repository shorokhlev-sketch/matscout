"""Tool surface - pure functions, single source of truth.

`tool_facades.py` wraps these functions into flat-kwarg facades and
lists the exposed ones in ``ALL_TOOLS``; `mcp_server.py` registers that
list. The OpenAI agent reaches the same functions over MCP. No conditional
branching inside the tools by who's calling - that's the whole
architectural point.
"""

from __future__ import annotations

from matscout.tools.applications import (
    find_battery_anode,
    find_battery_cathode,
    find_solar_absorber,
    find_thermoelectric,
    find_transparent_conductor,
)
from matscout.tools.cod import find_cod_experimental
from matscout.tools.compare import compare_materials
from matscout.tools.get import MaterialNotFoundError, get_material
from matscout.tools.jarvis import find_2d_materials, get_jarvis_topological
from matscout.tools.literature import (
    find_papers,
    find_preprints,
    get_doi_metadata,
    get_papers_about,
)
from matscout.tools.openalex import search_openalex
from matscout.tools.optimade import optimade_search
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
from matscout.tools.wikipedia import get_wikipedia_summary

__all__ = [
    "MaterialNotFoundError",
    "check_stability",
    "compare_materials",
    "compute_phase_diagram_strict",
    "find_2d_materials",
    "find_battery_anode",
    "find_battery_cathode",
    "find_cod_experimental",
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
    "get_wikipedia_summary",
    "optimade_search",
    "pareto_rank",
    "predict_decomposition",
    "search_materials",
    "search_openalex",
]
