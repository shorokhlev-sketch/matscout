# matscout

A live MCP server backed by the [Materials Project](https://next-gen.materialsproject.org/)
database, plus a playground that reasons through it. You describe what you need in plain
language — gpt-4o discovers our 10 typed tools via MCP, calls them over HTTP, self-corrects
when too few / too many hits, and explains the trade-offs in the answer with citations and
downloadable inputs for downstream DFT codes.

**Live demo:** [matscout.prfo.design](https://matscout.prfo.design)
**Live MCP endpoint:** `https://matscout.prfo.design/mcp/http/`

```
NL query
   ↓
Browser → FastAPI playground
   ↓
OpenAI Responses API   tools=[{ type:"mcp", server_url:".../mcp/http/" }]
   ↓
OpenAI egress  ──HTTPS──→  matscout MCP server  (FastMCP Streamable-HTTP)
                              ↓
                            10 typed tools (property · synthesis · literature · structure)
                              ↓
                            mp-api ←→ SQLite cache + research store
                              ↓
                            tool result back through MCP
   ↓
gpt-4o synthesises an answer (markdown + inline SVG phase diagram + CIF chips + 3D viewer)
   ↓
permanent /r/{id} URL with BibTeX export
```

The MCP server is exposed by the same uvicorn process the playground runs on — so the
"MCP" claim is not architectural: it is the literal load-bearing piece. Every browser
query produces real HTTP traffic between OpenAI's egress and our MCP endpoint. Two
transports for client compatibility:

- **`/mcp/http/`** — Streamable HTTP (modern, what OpenAI Responses API + Claude Desktop's
  `url` field use)
- **`/mcp/sse/`** — legacy SSE (older `mcp-cli` versions)
- **stdio** — local subprocess entrypoint `matscout-mcp` for offline use

Same 10 tools on every surface.

## What it does

10 typed tools, four groups:

| Group | Tools | What for |
|---|---|---|
| Property lookup | `search_materials`, `get_material`, `compare_materials`, `check_stability` | "find me X with band gap Y" |
| Synthesis context | `get_phase_diagram`, `predict_decomposition`, `get_competing_phases` | "will it survive synthesis?" |
| Literature | `get_doi_metadata`, `find_preprints` | CrossRef + arXiv (rate-limit-stable, no API key) |
| Computational interop | `get_structure` (CIF / POSCAR / XYZ) | hand off to VASP / Quantum ESPRESSO |

The agent decomposes a question like *"find a stable semiconductor with band gap ~1.5 eV
for a solar cell, show recent preprints, give me a CIF"* into the right sequence of calls,
relaxes the filters if it gets 0 hits, and returns a ranked shortlist with trade-offs
plus inline phase-diagram visualizations and 3D crystal viewers.

## Stack

- Python 3.10+, [uv](https://docs.astral.sh/uv/) for dependency management
- `mp-api` for Materials Project access
- `mcp[cli]` (FastMCP) for the MCP server
- `openai` SDK for the function-calling agent loop
- `pydantic` (typed I/O), `pydantic-settings` (env config)
- `openai` SDK 2.38+ Responses API with the new `tools=[{type: "mcp", ...}]` shape
- `fastapi` + polling for the browser↔origin path (SSE was killed by ISP DPI on long-lived `text/event-stream`; OpenAI↔MCP server-to-server traffic is fine)
- SQLite (WAL mode) for the tool-result cache **and** the persistent research store
- `pymatgen` for CIF / POSCAR / XYZ export
- mypy --strict, ruff, pytest, GitHub Actions CI, Docker

## Setup

```bash
uv sync                     # installs deps into .venv
cp .env.example .env        # then fill MP_API_KEY + OPENAI_API_KEY
uv run python -m matscout   # quick health check
uv run uvicorn web.app:app --port 8000 --reload
# → http://localhost:8000
```

Tests + checks:

```bash
uv run pytest -q -m 'not live'
uv run mypy matscout web
uv run ruff check matscout web
```

## Features

- **Permanent URLs.** Every run gets a `/r/{id}` snapshot — paste it into a notebook
  footnote, share it, come back to it in six months.
- **BibTeX export.** Each research session can be downloaded as a `.bib` file with the
  canonical Jain 2013 Materials Project citation plus a provenance block (matscout commit,
  mp-api version, snapshot timestamp).
- **Conversation mode.** Follow-up questions remember the prior tool calls and answers —
  ask "find me X" then "what about its band gap?" and the agent knows what X is.
- **Browser back/forward** walks through past runs.
- **3D crystal viewer.** Click `◊ 3D` next to any mp-id to load a ball-and-stick viewer
  (3Dmol.js, lazy-loaded) with the unit cell.
- **Inline phase diagrams.** Binary chemsys queries draw a proper convex-hull SVG plot.
- **EN / RU UI** — UI and the agent's final answer switch together.

## Architecture decisions worth knowing

- **Polling over SSE.** Long-lived `text/event-stream` was getting ripped by RU-ISP DPI
  regardless of heartbeat frequency. Replaced with `POST /api/query → request_id` + short
  `GET /api/query/{id}?since=N` polls. Each round-trip looks like a normal REST call to
  the network middleboxes.

- **One tool list, every surface.** `matscout/tool_facades.ALL_TOOLS` is the source of
  truth. `matscout/mcp_server.py` registers each function dynamically from that list;
  `matscout/agent/runner.py` doesn't ship its own JSON schemas at all — it hands OpenAI
  a single `{type: "mcp", server_url: "https://matscout.prfo.design/mcp/http/"}` tool and
  lets the model discover the same 10 functions via MCP `tools/list`. Add a tool to
  `ALL_TOOLS` and it appears for stdio MCP clients, HTTP MCP clients, and the playground
  agent simultaneously — no parallel registration.

- **Verifiable MCP.** Because the playground reasons through the hosted MCP server
  (not a synthetic in-process loop), every browser query produces real HTTP traffic
  between OpenAI's egress and `matscout.prfo.design/mcp/http/`. The "MCP server" claim
  is exercised by every visitor click, not just a code-search artefact.

- **Self-correction in the prompt, not the code.** When MP returns 0 hits or 200 hits,
  the agent loop doesn't hard-code "relax / tighten" branches — the system prompt teaches
  gpt-4o how to handle those conditions. Lets us claim "agentic" honestly.

## Security posture (read me)

The MCP endpoint at `/mcp/http/` is **publicly callable, no auth**. By
design — OpenAI's Responses API and any remote-mode Claude Desktop hit it
without bearer tokens. That means anyone on the internet who finds the URL
can call the 10 tools and consume our Materials Project API quota.

Mitigations in place:

- **DNS-rebinding guard** via FastMCP's `TransportSecuritySettings` — only
  requests with `Host: matscout.prfo.design` (or localhost in dev) pass.
- **Per-IP rate limit at nginx** — 30 req/min, burst 10. Defeats casual
  scrapers; legitimate Responses-API + Claude Desktop usage stays well
  under it.
- **Tools are read-only** — they query MP, CrossRef, arXiv. No state
  mutation, no shell-out, no eval. The blast radius of abuse is quota
  burn, not compromise.

For a production deployment you'd add a shared-secret header (the
Responses API MCP tool config supports custom headers) and put nginx
proxy_request_body limits in front. For a portfolio piece the current
posture is honest enough.

## Attribution

Data from [Materials Project](https://next-gen.materialsproject.org/), licensed
[CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/). Cite per their guidelines
in any downstream publication — matscout's BibTeX export pre-fills the canonical citation.

## License

MIT
