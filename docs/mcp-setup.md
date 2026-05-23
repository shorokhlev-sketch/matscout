# Plugging matscout into Claude Code as an MCP server

matscout exposes its 4 tools (`search_materials`, `get_material`,
`compare_materials`, `check_stability`) over the Model Context Protocol.
Once configured, you can ask Claude Code in plain language:

> *"Find me three stable cubic semiconductors with band gap around 1.5 eV
> and a density below 6 g/cm³."*

— and it will issue the right `search_materials` call, then drill into
candidates with `get_material` and `compare_materials`. No agent code on
matscout's side; Claude Code is the orchestrator.

## 1. One-time setup

Verify the server boots:

```bash
cd ~/matscout
.venv/bin/python -m matscout.mcp_server &
# Should sit there waiting on stdin (MCP uses stdio). Kill it: kill %1
```

## 2. Wire into Claude Code

Two options, depending on your taste.

### Option A — per-project (`.mcp.json` in the directory you launch Claude Code from)

```json
{
  "mcpServers": {
    "matscout": {
      "command": "/Users/prfo/matscout/.venv/bin/python",
      "args": ["-m", "matscout.mcp_server"],
      "env": {
        "MP_API_KEY": "<paste-key-or-use-system-env>",
        "OPENAI_API_KEY": "<not-actually-needed-for-mcp-mode>"
      }
    }
  }
}
```

`OPENAI_API_KEY` is required only because `matscout.config` insists on it
at import time. You can leave it as a dummy string when running the
MCP server (no LLM call goes through this layer — Claude Code is the LLM).

### Option B — globally via CLI

```bash
claude mcp add matscout \
  --command /Users/prfo/matscout/.venv/bin/python \
  --args "-m,matscout.mcp_server" \
  --env MP_API_KEY=<key>
```

Restart Claude Code after wiring. The four tools should show up in the
slash-tools list.

## 3. Smoke-test from Claude

In a Claude Code session, paste:

> List the tools you got from matscout, then call `search_materials` for
> elements `["Cu","O"]` with `band_gap_range=(1.5, 2.5)` and `only_stable=true`,
> limit 5. Summarise what came back.

You should see Claude print four tool names, then issue one tool call,
then receive a list of `mp-` ids back.

## 4. Talking to it like a researcher

After it works, try:

> Find me stable thermoelectric candidates that have low density (under
> 5 g/cm³) and contain Bi or Te. Compare the top three.

Claude will plan, search, possibly broaden filters if it gets zero hits,
fetch full sheets, build a comparison table, and explain the trade-offs
in natural language.

## Troubleshooting

- **No tools appear** → check `claude mcp list` (CLI) — should list `matscout`.
  If missing, the JSON path / command is wrong.
- **Server crashes immediately** → run the command directly in a terminal
  to see the traceback (env var validation errors will print clearly).
- **`MaterialNotFoundError`** when asking by id → the id is real but
  doesn't exist; Claude should react and re-search by composition.
- **Repeated calls hit the API every time** → check `~/matscout/cache/matscout.db`
  exists and the process can write to it.

## Inspecting the cache

```bash
sqlite3 ~/matscout/cache/matscout.db \
  "SELECT tool_name, COUNT(*), SUM(hit_count) FROM tool_cache GROUP BY tool_name;"
```
