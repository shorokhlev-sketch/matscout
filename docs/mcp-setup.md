# Using matscout as an MCP server

matscout exposes 25 tools over the Model Context Protocol. The list is
`ALL_TOOLS` in `matscout/tool_facades.py`; `matscout/mcp_server.py`
registers every entry. Groups:

| Group | Tools |
|---|---|
| Property lookup (4) | `search_materials`, `get_material`, `compare_materials`, `check_stability` |
| Synthesis context (4) | `get_phase_diagram`, `predict_decomposition`, `get_competing_phases`, `compute_phase_diagram_strict` |
| Application presets (5) | `find_battery_anode`, `find_battery_cathode`, `find_solar_absorber`, `find_thermoelectric`, `find_transparent_conductor` |
| Property sheets (2) | `get_elastic_properties`, `get_electronic_summary` |
| Ranking (1) | `pareto_rank` |
| Structure export (1) | `get_structure` (CIF / POSCAR / XYZ) |
| JARVIS-DFT (2) | `find_2d_materials`, `get_jarvis_topological` |
| Federated search (1) | `optimade_search` over 6 OPTIMADE providers: MP, COD, NOMAD, Alexandria, JARVIS, odbx |
| Experimental structures (1) | `find_cod_experimental` |
| Literature (4) | `get_doi_metadata`, `find_preprints`, `search_openalex`, `get_wikipedia_summary` |

Once connected, you can ask Claude in plain language:

> Find three stable cubic semiconductors with a band gap near 1.5 eV and
> a density below 6 g/cm³.

Claude issues `search_materials`, then drills into candidates with
`get_material` and `compare_materials`. The MCP client is the
orchestrator; matscout runs no LLM in this mode.

There are two ways to connect: a local stdio subprocess, or the remote
HTTP endpoint of a deployed instance.

## Option A: local stdio server

Needs a Materials Project API key (free, from
https://next-gen.materialsproject.org/api). No OpenAI key: `config.py`
treats `OPENAI_API_KEY` as optional, and only the web playground, the
`matscout` CLI and the live tests need it.

Check that the server boots:

```bash
cd /path/to/matscout
uv sync
MP_API_KEY=<your-key> uv run matscout-mcp
# Waits on stdin (MCP speaks JSON-RPC over stdio). Stop it with Ctrl+C.
```

### Claude Code

```bash
claude mcp add matscout -e MP_API_KEY=<your-key> \
  -- /path/to/matscout/.venv/bin/python -m matscout.mcp_server
```

Or per project, in `.mcp.json` in the directory you start Claude Code
from (the repo's `.gitignore` already excludes `.mcp.json`, since it
holds a key):

```json
{
  "mcpServers": {
    "matscout": {
      "command": "/path/to/matscout/.venv/bin/python",
      "args": ["-m", "matscout.mcp_server"],
      "env": { "MP_API_KEY": "<your-key>" }
    }
  }
}
```

### Claude Desktop

Same `mcpServers` block, in
`~/Library/Application Support/Claude/claude_desktop_config.json` on
macOS. Restart Claude Desktop after editing.

## Option B: remote HTTP endpoint

A deployed instance mounts the same FastMCP server inside the web app:

- `/mcp/http/` - Streamable HTTP (use this one)
- `/mcp/sse/` - SSE, for older clients

No local Python and no API key on the client side: the server holds the
Materials Project key.

```bash
claude mcp add --transport http matscout https://<your-domain>/mcp/http/
```

For Claude Desktop: open Settings > Connectors > Add custom connector
and paste `https://<your-domain>/mcp/http/`. `claude_desktop_config.json`
only launches local stdio servers (`command`, `args`, `env`); a `url`
entry there does not connect. To keep the remote server in that file,
bridge it through `mcp-remote`, which needs Node.js:

```json
{
  "mcpServers": {
    "matscout": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://<your-domain>/mcp/http/"]
    }
  }
}
```

The public demo runs at `https://matscout.prfo.design/mcp/http/`.

If you deploy on your own domain, add it to `_ALLOWED_HOSTS` and
`_ALLOWED_ORIGINS` in `matscout/mcp_server.py`. Otherwise the FastMCP
DNS-rebinding guard rejects the request with `Invalid Host header`.

## Smoke test

In a Claude session, paste:

> List the tools you got from matscout. Then call `search_materials` with
> `elements=["Cu","O"]`, `band_gap_range=[1.5, 2.5]`, `only_stable=true`,
> `limit=5`, and summarize the result.

Expected: 25 tool names, one tool call, a list of `mp-` ids.

## Research-style prompts

> Find stable thermoelectric candidates with density under 5 g/cm³ that
> contain Bi or Te. Compare the top three.

Expected: at least one `search_materials` or `find_thermoelectric` call,
then `compare_materials` on 3 `mp-` ids, and a table with density and
band gap.

## Troubleshooting

- **No tools appear.** Run `claude mcp list`; the output must include `matscout`.
  If it is missing, the command path or the JSON is wrong.
- **Server exits at start.** Run the command in a terminal to see the
  traceback.
- **Every tool call fails with `ValidationError ... MP_API_KEY Field
  required`.** Settings load lazily, so the server boots without the key
  and fails on the first call. Set `MP_API_KEY` in the client's `env`
  block or in `.env` in the server's working directory.
- **`MaterialNotFoundError`.** The id does not exist in Materials
  Project; the client should re-search by composition.
- **Every call hits the API.** Check that `cache/matscout.db` exists in
  the repo root and that the process can write to it.
- **`Invalid Host header` on a remote endpoint.** See the allowlist note
  in Option B.

## Inspecting the cache

```bash
sqlite3 cache/matscout.db \
  "SELECT tool_name, COUNT(*), SUM(hit_count) FROM tool_cache GROUP BY tool_name;"
```
