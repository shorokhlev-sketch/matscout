"""FastMCP server exposing matscout's typed tools to any MCP client.

Wrappers are intentionally thin: they translate flat kwargs (which is how
MCP/JSON-schema speaks) into Pydantic input models and convert outputs
back to dicts. The actual logic lives in ``matscout.tools``.

Transports:

- **stdio** (default, what console_script ``matscout-mcp`` does): for
  local Claude Desktop / Code use, where the client spawns the server as
  a subprocess and pipes JSON-RPC over stdin/stdout.
- **SSE / streamable HTTP**: when the FastAPI playground mounts this
  server, MCP becomes reachable as a URL - Claude Desktop ``url`` field
  in mcpServers config can point straight at production without
  installing Python anywhere.

Run locally (stdio):
    matscout-mcp                  # console_script installed by pyproject
    python -m matscout.mcp_server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from matscout.tool_facades import ALL_TOOLS

# Hosts/origins from which the MCP HTTP transports will accept requests.
# Includes localhost (for local dev), the production domain (browser-
# initiated MCP calls via the playground's OpenAI Responses API hop, and
# any external Claude Desktop using `url` mode), and OpenAI's outbound
# range proxy.openai.com that's used when calls come back through their
# MCP tool feature.
_ALLOWED_HOSTS = [
    "127.0.0.1",
    "127.0.0.1:8011",
    "127.0.0.1:8090",
    "localhost",
    "localhost:8011",
    "localhost:8090",
    "matscout.prfo.design",
]

# Origin header is only ever sent by browser-initiated XHR. The
# server-to-server callers we care about (OpenAI Responses API egress,
# Claude Desktop remote MCP) omit it, and the guard passes None
# trivially. We allow our own playground origin in case a browser ever
# needs to talk to /mcp/ directly during local dev.
_ALLOWED_ORIGINS = [
    "http://127.0.0.1:8090",
    "http://localhost:8090",
    "https://matscout.prfo.design",
]

mcp = FastMCP(
    "matscout",
    instructions=(
        "Materials Project research agent. Use these tools to find inorganic "
        "crystalline materials matching property constraints. Workflow: "
        "1) search_materials with reasonable filters → 2) inspect top "
        "candidates with get_material / check_stability → 3) compare_materials "
        "side-by-side. For synthesis questions add get_phase_diagram and "
        "predict_decomposition. For computational hand-off use get_structure "
        "(CIF / POSCAR / XYZ). Energies are in eV/atom, band gaps in eV, "
        "density in g/cm^3. Materials with energy_above_hull > 0.025 eV/atom "
        "are unlikely to be synthesizable as a single phase."
    ),
    # Strip the default route prefixes so we can mount the resulting
    # Starlette app at whatever URL prefix we want (the web playground
    # mounts at /mcp/sse and /mcp/http to keep both transports available).
    sse_path="/",
    message_path="/messages/",
    streamable_http_path="/",
    # Allow Host/Origin headers we expect in production. Without this the
    # FastMCP DNS-rebinding guard rejects any non-localhost host with
    # "Invalid Host header" before our routes ever see the request.
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_ALLOWED_HOSTS,
        allowed_origins=_ALLOWED_ORIGINS,
    ),
)

# Single source of truth: register whatever's in ALL_TOOLS. Keeps the MCP
# surface and the OpenAI-agent surface lock-stepped - when we add or drop
# a tool, both update at once.
for _fn in ALL_TOOLS:
    mcp.tool()(_fn)


def main() -> None:
    """Console entrypoint - runs FastMCP over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
