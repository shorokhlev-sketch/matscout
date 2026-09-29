# CLAUDE.md

Instructions for coding agents working in this repo.

matscout is an MCP server plus an OpenAI agent over Materials Project,
5 more materials databases (COD, NOMAD, Alexandria, JARVIS, odbx, via
OPTIMADE) and 4 literature sources (Crossref, arXiv, OpenAlex,
Wikipedia). One set of 25 typed tools serves three surfaces: MCP over
stdio, MCP over HTTP, and a web playground where gpt-4o calls the same
MCP server over the network.

## Project map

```
matscout/
  tools/*.py          typed tool implementations, one module per domain;
                      the only layer that talks to external APIs
  tools/_client.py    lazy MPRester singleton + SQLite cache, both injectable
  tool_facades.py     flat-kwarg wrappers + ALL_TOOLS (25): the tool registry
  mcp_server.py       FastMCP server; registers every entry of ALL_TOOLS
  agent/runner.py     OpenAI Responses API loop, two phases, one "mcp" tool
  agent/prompts.py    all system prompts (Discovery, Analysis, single-call V1)
  models.py           Pydantic models, extra="forbid"
  config.py           pydantic-settings; reads env and .env from the cwd
  cache.py            SQLite tool-result cache with TTL
  research.py         run snapshots (research.db), BibTeX, mp-id extraction
  provenance.py       snapshot metadata: commit SHA, library versions
  cli.py              `matscout "query"` console entry
  __main__.py         `python -m matscout`: env + deps health check
web/
  app.py              FastAPI: polling API, /r/{id} snapshots, mounts MCP
  static/             single-page UI (index.html holds the EN + RU strings)
tests/
  test_*.py           offline unit tests + live tests (marker `live`)
  eval/               agent eval: queries.yaml cases, runner.py
docs/                 architecture.md, mcp-setup.md, mp-probe.md, screenshots/ (README images, webp)
deploy.sh             single-VPS deploy (systemd + nginx + certbot)
Dockerfile, compose.yaml   local container run
```

## Commands

```bash
uv sync --extra dev                         # install with dev tools
cp .env.example .env                        # set MP_API_KEY, OPENAI_API_KEY

uv run pytest -q -m "not live"              # offline tests, no keys, ~3 s
uv run --env-file .env pytest -q -m live                     # live MP + OpenAI; skipped unless both keys are in the process env
uv run --env-file .env pytest tests/eval/ -v -m live         # agent eval against real APIs (costs tokens)
uv run --env-file .env python -m tests.eval.runner [--json]  # same eval, standalone report

uv run ruff check .                         # lint
uv run ruff format --check matscout tests web   # format check (CI runs this)
uv run mypy matscout web                    # strict mode comes from pyproject.toml

uv run uvicorn web.app:app --reload --port 8011   # web playground, http://localhost:8011 (on 8000 the local /mcp/http/ answers 421)
uv run matscout-mcp                               # MCP server over stdio
uv run matscout "stable Li cathode, low density"  # agent from the terminal
uv run python -m matscout                         # check env + deps
docker compose up --build                         # playground in a container
```

`uv run` does not load `.env`, so the live commands pass `--env-file .env`.

CI (`.github/workflows/ci.yml`) has 2 parallel jobs. lint-and-test runs
ruff check, ruff format --check, mypy and the offline tests on Python
3.11. docker-build builds the image without pushing. Run the same four
checks before you call a change done.

## Architecture

```
tools/*.py -> tool_facades.py (ALL_TOOLS) -> mcp_server.py (FastMCP)
                                               |-> stdio        (matscout-mcp)
                                               |-> /mcp/http/   (Streamable HTTP, mounted in web/app.py)
                                               |-> /mcp/sse/    (SSE, mounted in web/app.py)

Web request:
  browser -> POST /api/query -> agent/runner.py:stream_agent
          -> client.responses.create(tools=[{type: "mcp", server_url: DEFAULT_MCP_SERVER_URL}])
          -> OpenAI calls <domain>/mcp/http/ -> FastMCP -> tools/*.py -> external API
          -> events buffered in memory -> browser polls GET /api/query/{id}?since=N
```

Details and diagrams: `docs/architecture.md`.

## Conventions

- **ALL_TOOLS is the single source of truth.** The MCP server registers
  whatever is in the list. Never register a tool anywhere else, and never
  branch inside a tool on who the caller is.
- **Facades speak flat JSON.** Facade parameters are primitives and lists
  only. The facade builds the strict Pydantic input model where one exists
  (`SearchFilters`) and returns `model_dump(mode="json")` dicts, so every
  surface sees the same wire format.
- **The facade docstring is the tool description.** FastMCP sends it to
  the LLM as-is. First line = what the tool does. Then when to use it,
  units (eV/atom, eV, g/cm^3) and argument ranges.
- **Pydantic at every boundary.** Models in `models.py` use
  `ConfigDict(extra="forbid")`. `tools/` parses external API payloads
  into models or plain dicts before returning them.
- **Types.** `mypy` runs in strict mode with the pydantic plugin. No
  untyped defs, no implicit Optional. Keep `web/` passing too.
- **Style.** ruff rules E, F, I, B, UP, SIM, RUF; line length 100.
  Target Python 3.10 (`requires-python >= 3.10`): no 3.11+ stdlib such as
  `tomllib`. CI and the Docker image run 3.11.
- **Cache every network read.** Tools call `get_cache()` and key the
  entry by tool name + args (sha256 of canonical JSON). TTL comes from
  `MATSCOUT_CACHE_TTL_DAYS` (default 30). Derived tools such as
  `compare_materials` and `check_stability` reuse `get_material` and add
  no cache entries of their own.
- **External HTTP.** Use `httpx` with an explicit timeout and a
  `matscout/<version>` User-Agent. The OpenAlex and Crossref clients take
  it from `polite_user_agent()` in `tools/_client.py`, which adds
  `mailto:` only when `MATSCOUT_CONTACT_EMAIL` is set (polite pool).
  Never hardcode a contact address in the source.
- **Tests.** Offline tests inject fakes through `set_client()` and
  `set_cache()` in `tools/_client.py`; they must pass without keys. Mark
  anything that hits a real API with `@pytest.mark.live`.
- **Prompts** live in `agent/prompts.py`, with 2 exceptions: the
  Analysis hand-off text (`analysis_input` in `agent/runner.py`) and the
  RU `locale_hint` in `web/app.py`. After a prompt change, run the eval
  suite and compare with the previous run.
- **UI copy** exists 3 times in `web/static/index.html`: the EN default
  inside each `data-i18n` element, plus the EN and RU dictionaries.
  Change all 3.

## Adding a tool

1. Implement a typed function in `matscout/tools/<domain>.py`. Read
   through `get_cache()`, write back after a successful fetch. Put
   reusable result shapes in `models.py`.
2. Export it from `matscout/tools/__init__.py` (import + `__all__`).
3. Add a flat-kwarg facade in `matscout/tool_facades.py` with a precise
   docstring, and append it to `ALL_TOOLS` in the matching group. The MCP
   server picks it up on the next start; no other registration.
4. If the web agent should call it, add the name to
   `_DISCOVERY_TOOL_NAMES` (finds candidates) or `_ANALYSIS_TOOL_NAMES`
   (drills into known candidates) in `agent/runner.py`, and name it in the
   matching prompt in `agent/prompts.py`. Snapshot resume (the
   `prior_messages` branch of `stream_agent`, shared with
   `single_phase=True`) allows every tool in `ALL_TOOLS`.
5. Add an offline test with a fake client in `tests/test_tools_offline.py`.
   Add a live test with `@pytest.mark.live` if the upstream API has
   quirks worth pinning.
6. If the UI renders the result in a special way (phase diagram, structure
   chips), update `web/app.py` and `web/static/index.html`.
7. Update the tool count wherever it is written: `README.md`,
   `docs/mcp-setup.md`, `docs/architecture.md`, the MCP modal copy in
   `web/static/index.html` (markup, EN and RU dictionaries), the
   docstrings in `agent/runner.py` (module and `_stream_phase`), the
   comment above the MCP mounts in `web/app.py` (it also gives the 23
   allow-listed tools), and this file.
8. Run ruff, ruff format, mypy and the offline tests.

## Design decisions to keep

1. **The web agent goes through MCP, not in-process function calling.**
   `stream_agent` hands OpenAI one tool, `{type: "mcp", server_url: ...}`.
   OpenAI discovers the tools with `tools/list` and calls our MCP server
   over HTTPS. The MCP server is on the hot path of every web query.
2. **Two phases per query.** Discovery runs with 11 candidate-finding
   tools and `max_tool_calls=10`; Analysis runs with 17 drill-in tools and
   `max_tool_calls=25`, and writes the final answer. A single gpt-4o call
   used to stop after 5-7 tool calls without composing an answer.
3. **Polling, not SSE, between browser and server.** DPI middleboxes on
   some networks cut long-lived `text/event-stream` responses regardless
   of heartbeats. The browser POSTs `/api/query`, gets a `request_id`,
   then polls `GET /api/query/{id}?since=N` every 500 ms. The OpenAI to
   MCP hop is server-to-server and keeps using Streamable HTTP.
4. **Snapshots.** `web/app.py` saves every run to `cache/research.db`.
   `/r/{id}` replays the trace and answer; the SPA uses the same route
   for `pushState` back/forward.
5. **Conversations use `previous_response_id`.** OpenAI keeps the
   context; the server stores only `last_response_id` per conversation.
   Resuming from a snapshot cannot reuse the expired id, so it rebuilds
   the messages into `input` items and runs single-phase.
6. **`OPENAI_API_KEY` is optional in `config.py`.** The MCP entrypoint
   boots with `MP_API_KEY` only. The web app checks for the OpenAI key at
   startup and fails fast.
7. **One uvicorn worker.** Runs and conversations live in in-process
   dicts. Scaling out needs shared state (Redis or similar) first.

## Known quirks

- The eval cases need both API keys and spend OpenAI tokens; CI skips
  them and the repo stores no eval results.
- `stream_agent` accepts `model` but does not pass it to `_stream_phase`.
  Every call uses `DEFAULT_MODEL` (gpt-4o); `matscout --model` has no
  effect.
- `optimade_search` knows 6 providers (`_PROVIDERS` in
  `tools/optimade.py`): mp, cod, nomad, alexandria, jarvis, odbx. Any
  other id raises `ValueError`. We dropped aflow (500), mcloud (404)
  and mpdd (timeout).
- `DEFAULT_MCP_SERVER_URL` in `agent/runner.py` points at the production
  endpoint, because OpenAI must reach the MCP server over the public
  internet. A local web run therefore executes the *deployed* tools. To
  test local tool changes end to end, expose the local server through a
  tunnel, add its host to `_ALLOWED_HOSTS`, and point
  `DEFAULT_MCP_SERVER_URL` at the tunnel (the web app and CLI have no
  flag for it; `stream_agent(..., mcp_server_url=...)` works from
  Python), or test through stdio MCP / offline tests.
- FastMCP's DNS-rebinding guard rejects unknown `Host` headers with
  `Invalid Host header`. A new domain must go into `_ALLOWED_HOSTS` and
  `_ALLOWED_ORIGINS` in `mcp_server.py`.
- mp-api emits a deprecation warning about `nelements`; the real
  parameter is `num_elements`. Harmless.
- MP does not return elemental endpoints for a multi-element `chemsys`
  query. `tools/synthesis.py` fetches `chemsys=<el>` per element and
  merges.
- Polymorphs with the same composition collapse to one point on a hull
  plot. `_phase_diagram_viz` in `web/app.py` dedupes for the UI only; the
  LLM still gets every entry.
- `previous_response_id` has a retention window on OpenAI's side. With
  an expired id, the Discovery call fails and a `tool_error` appears in
  the trace. Analysis then runs without `previous_response_id`, so the
  answer ignores earlier turns, and the conversation continues from the
  new response id. Nothing retries the failed call.
- Semantic Scholar tools (`find_papers`, `get_papers_about`) are out of
  `ALL_TOOLS`: the anonymous tier rate-limits per IP. The functions stay
  in `tools/literature.py` for callers with an S2 API key.
- JARVIS direct tools (`find_2d_materials`, `get_jarvis_topological`) are
  in `ALL_TOOLS` but not in the web agent's allow-lists: NIST's static
  endpoints returned 502 in May 2026 and the agent kept calling them.
  Both tools now go through JARVIS OPTIMADE and add nothing over
  `optimade_search(providers=["jarvis"])`.
- uvicorn cold start takes about 14 s (pymatgen and matplotlib imports).
  Wait before probing `/healthz` after a restart.

## Deploy

Production is one Linux VPS: uvicorn under systemd on `127.0.0.1:8011`,
nginx in front, TLS from certbot, SQLite files in `<REMOTE_DIR>/cache/`
(`matscout.db` = tool cache, `research.db` = snapshots).

- Full provisioning: `./deploy.sh`. It reads `VPS_HOST`, `VPS_USER`,
  `SSH_KEY`, `CERTBOT_EMAIL`, `DOMAIN` (required) and `REMOTE_DIR`,
  `SERVICE_NAME`, `SERVICE_PORT` (optional) from the environment or from
  a gitignored `.env.deploy`. API keys and the optional
  `MATSCOUT_CONTACT_EMAIL` come from `.env` and go into the systemd
  unit. nginx and certbot must already be installed on the host.
- Code-only update: write the commit SHA to `VERSION`
  (`git rev-parse --short=10 HEAD > VERSION`), rsync `matscout/`, `web/`,
  `pyproject.toml`, `uv.lock` and `VERSION` to `REMOTE_DIR`, restart the
  service, wait about 14 s, probe `https://<DOMAIN>/healthz`. Run
  `uv sync --no-dev` on the server if dependencies changed.
- `VERSION` is a deploy artifact (gitignored). `provenance.py` reads
  `MATSCOUT_COMMIT`, then `VERSION`, then `git rev-parse`.
- The production nginx vhost has hand-added settings that `deploy.sh`
  does not write: `Cache-Control: no-store` on `/` and `/r/{id}`, a rate
  limit on `/mcp/` (30 requests/min per IP, burst 10), a static info page
  for browsers that open `/mcp/`, and a 3600 s read timeout on `/mcp/`.
  Step 4 rewrites the vhost from scratch, so back it up before a full
  provisioning run and re-apply these settings after it.
- Never commit `.env`, `.env.deploy`, `.mcp.json` or `cache/`.

## Session workflow

- Start: `git pull`.
- Commit and push only when asked.
- When asked to "update context", rewrite this file to match the code
  as it is now (tool counts, allow-lists, commands).
