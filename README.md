# matscout

MCP server + OpenAI agent over the [Materials Project](https://next-gen.materialsproject.org/)
database. You describe what you need in plain language — the agent translates to typed
property filters, queries the DB, self-corrects when too few / too many hits, and explains
the trade-offs in the answer with citations and downloadable inputs for downstream DFT codes.

**Live demo:** [matscout.prfo.design](https://matscout.prfo.design)

```
NL query
   ↓
[ agent: gpt-4o ]
   ↓
[ 12 typed tools — property lookup · synthesis · literature · structure export ]
   ↓
[ mp-api ] ←→ [ SQLite cache + research store ]
   ↓
ranked answer + comparison tables + inline phase diagram + CIF + papers
```

Two surfaces over one set of tool functions:

- **MCP server** (FastMCP) — plug into Claude Desktop / Claude Code
- **OpenAI function-calling agent** behind a FastAPI playground

## What it does

12 typed tools, four groups:

| Group | Tools | What for |
|---|---|---|
| Property lookup | `search_materials`, `get_material`, `compare_materials`, `check_stability` | "find me X with band gap Y" |
| Synthesis context | `get_phase_diagram`, `predict_decomposition`, `get_competing_phases` | "will it survive synthesis?" |
| Literature | `find_papers`, `get_papers_about`, `get_doi_metadata`, `find_preprints` | Semantic Scholar + CrossRef + arXiv |
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
- `fastapi` + polling for the playground (SSE was killed by ISP DPI on long-lived `text/event-stream`)
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

- **One tool list, two surfaces.** `matscout/tool_facades.py` is the source of truth.
  `matscout/mcp_server.py` exposes them via MCP; `matscout/agent/runner.py` wraps them
  for OpenAI function-calling. The web playground and a Claude Desktop / Code session
  see exactly the same tools.

- **Self-correction in the prompt, not the code.** When MP returns 0 hits or 200 hits,
  the agent loop doesn't hard-code "relax / tighten" branches — the system prompt teaches
  gpt-4o how to handle those conditions. Lets us claim "agentic" honestly.

## Attribution

Data from [Materials Project](https://next-gen.materialsproject.org/), licensed
[CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/). Cite per their guidelines
in any downstream publication — matscout's BibTeX export pre-fills the canonical citation.

## License

MIT
