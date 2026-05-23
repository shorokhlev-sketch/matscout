"""FastMCP-server exposing the 4 tools to any MCP client (Claude Code etc.).

Wrappers are intentionally thin: they translate flat kwargs (which is how
MCP/JSON-schema speaks) into our Pydantic input model and convert
Pydantic outputs back to dicts. The actual logic lives in ``matscout.tools``.

Run:
    matscout-mcp                # console_script installed by pyproject
    python -m matscout.mcp_server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from matscout.tool_facades import (
    check_stability,
    compare_materials,
    get_material,
    search_materials,
)

mcp = FastMCP(
    "matscout",
    instructions=(
        "Materials Project search agent. Use these tools to find inorganic "
        "crystalline materials matching property constraints. Workflow: "
        "1) search_materials with reasonable filters → 2) inspect top "
        "candidates with get_material / check_stability → 3) compare_materials "
        "side-by-side. Energies are in eV/atom, band gaps in eV, density in "
        "g/cm^3. Materials with energy_above_hull > 0.025 eV/atom are "
        "unlikely to be synthesizable as a single phase."
    ),
)


# Register the four facades as MCP tools. Docstrings on the facades become
# the tool descriptions visible to MCP clients.
mcp.tool()(search_materials)
mcp.tool()(get_material)
mcp.tool()(compare_materials)
mcp.tool()(check_stability)


def main() -> None:
    """Console entrypoint — runs FastMCP over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
