# matscout

MCP server + agent over the [Materials Project](https://next-gen.materialsproject.org/)
database. You describe what you need in plain language — the agent translates
to typed property filters, queries the DB, self-corrects when too few / too
many hits, and explains the trade-offs in the answer.

```
NL query
   ↓
[ agent: gpt-4o ]
   ↓
[ tools: search_materials / get_material / compare_materials / check_stability ]
   ↓
[ mp-api ] ←→ [ SQLite cache ]
   ↓
ranked answer + comparison table
```

Two surfaces over one set of tool functions:
- **MCP server** (FastMCP) — plug into Claude Desktop / Claude Code
- **OpenAI function-calling agent** behind a FastAPI/SSE playground

## Status

Skeleton — under construction. See [docs/architecture.md](docs/architecture.md)
for the planned layout and [CHANGELOG](#changelog) for what's done.

## Stack

- Python 3.10+, [uv](https://docs.astral.sh/uv/) for dep management
- `mp-api` for Materials Project access
- `mcp[cli]` (FastMCP) for MCP server
- `openai` SDK for the agent loop
- `pydantic` for typed I/O, `pydantic-settings` for env config
- `fastapi` + `sse-starlette` for the playground
- `structlog` for logging
- SQLite for tool-result cache

## Setup

```bash
uv sync                     # installs deps into .venv
cp .env.example .env        # then fill MP_API_KEY + OPENAI_API_KEY
uv run python -m matscout   # quick health check
```

## Roadmap

- [x] **01** — skeleton + pyproject + env scaffolding
- [ ] **02** — `probe_mp.py` (verify available fields against live MP)
- [ ] **03** — `models.py` (Pydantic types) + tests
- [ ] **04** — `cache.py` (SQLite, TTL) + tests
- [ ] **05** — `tools/search.py` (+ offline tests)
- [ ] **06** — `tools/get.py`
- [ ] **07** — `tools/compare.py` + `tools/stability.py`
- [ ] **08** — live integration tests against MP
- [ ] **09** — `mcp_server.py` + Claude Code wiring
- [ ] **10** — OpenAI tool-schema adapter
- [ ] **11** — agent loop v1 (no self-correction)
- [ ] **12** — self-correction (relax / tighten)
- [ ] **13** — eval suite (yaml-driven)
- [ ] **14** — FastAPI + SSE playground + static UI
- [ ] **15** — Dockerfile + GitHub Actions CI
- [ ] **16** — deploy to `matscout.prfo.design`

## Accept-cases (must work end-to-end before "v1 done")

1. "Find a stable semiconductor with band gap near 1.5 eV for a solar cell."
2. "A thermoelectric with low density — give me 3 candidates and compare them."
3. "Sodium-based electrode material, sort by stability."

## Next (not in v1, listed for honesty)

- Cache-warming for popular queries
- 3D crystal structure visualization
- Mechanical / magnetic property dimensions
- Multi-criteria ranking with weights (Pareto front)
- Non-OpenAI backends (Anthropic / local LLM) for the agent

## Attribution

Data from [Materials Project](https://next-gen.materialsproject.org/),
licensed [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Cite per their guidelines in any downstream publication.

## License

MIT
