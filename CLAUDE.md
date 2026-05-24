# matscout — внутренние заметки

MCP server + OpenAI агент над Materials Project. Сделан для вакансии AI-operator чтобы показать MCP + function-calling + typed tools на реальной академической задаче.

## Источник истины

GitHub `shorokhlev-sketch/matscout` (приватный). Перед сессией `git pull`, после правок — `git add . && git commit && git push`.

## Архитектура

Один набор тулов выставлен через единый MCP-сервер. Веб-плейграунд не дёргает тулы у себя в процессе — gpt-4o ходит через OpenAI Responses API к **нашему же** MCP-серверу по сети. Это значит «MCP server claim» = живой нагруженный path, а не картинка в README.

```
matscout/tools/{search, get, compare, stability, synthesis, literature, structure}.py
    ↓
matscout/tool_facades.py    # flat-kwarg wrappers, ALL_TOOLS = [10 функций]
    ↓
matscout/mcp_server.py      # FastMCP, регистрирует ALL_TOOLS динамически
    ↓                          ↓
   stdio entrypoint          mounted в web/app.py:
   (matscout-mcp)              /mcp/sse/ (SSE)
                               /mcp/http/ (Streamable HTTP, OpenAI-compatible)
                                  ↓
                               Тот же endpoint используется и нашим веб-плейграундом,
                               и любым внешним Claude Desktop через "url" mcpServers.

Маршрут запроса с сайта:
   Browser → web/app.py /api/query
       → matscout/agent/runner.py:stream_agent
       → client.responses.create(tools=[{type:"mcp", server_url:".../mcp/http/"}])
       → OpenAI egress ──HTTPS──→ matscout.prfo.design/mcp/http/
       → FastMCP dispatch → matscout/tools/*.py → mp-api → MP REST
       → результат обратно OpenAI → text streaming → polling в браузер
```

**Тулы (10):** search_materials, get_material, compare_materials, check_stability, get_phase_diagram, predict_decomposition, get_competing_phases, get_structure, get_doi_metadata, find_preprints.

(find_papers / get_papers_about под Semantic Scholar сняты из `ALL_TOOLS` — S2 anonymous лимит-рейтит с одного IP. Функции остались в `tools/literature.py` для callers с S2 API ключом.)

**Полировка:** Pydantic models с `extra="forbid"`, SQLite кэш с TTL, mypy --strict, ruff, pytest, GitHub Actions CI.

## Локальный запуск

```bash
cd ~/matscout
uv sync
cp .env.example .env       # MP_API_KEY + OPENAI_API_KEY
uv run uvicorn web.app:app --reload --port 8000
# → http://localhost:8000
```

Тесты + проверки:

```bash
uv run pytest -q -m 'not live'
uv run mypy matscout web
uv run ruff check matscout web
```

## Production

- **Хост:** Beget VPS Moscow `45.153.190.88` (вход `ssh beget` через ControlMaster в `~/.ssh/config`)
- **systemd:** `prfo-matscout.service` → `uvicorn web.app:app --host 127.0.0.1 --port 8011`
- **nginx:** `matscout.prfo.design` → proxy на 8011, `Cache-Control: no-store` на `/` и `/r/{id}`
- **Workdir на VPS:** `/opt/prfo/matscout/`
- **Cache:** `/opt/prfo/matscout/cache/{matscout.db,research.db}` — sqlite

## Deploy

```bash
cd ~/matscout
git rev-parse --short=10 HEAD > VERSION
rsync -az \
  --include='matscout/' --include='matscout/**' \
  --include='web/' --include='web/**' \
  --include='pyproject.toml' --include='uv.lock' \
  --include='VERSION' \
  --exclude='*' \
  ./ beget:/opt/prfo/matscout/
ssh beget 'systemctl restart prfo-matscout.service'
sleep 14   # uvicorn cold start: pymatgen + matplotlib imports
```

## Ключевые архитектурные решения

1. **Полный маршрут через MCP, не через function calling.** Ранее `stream_agent` собирал JSON-схемы из ALL_TOOLS и отдавал их в `chat.completions.create(tools=[…])`, дёргая функции у себя в процессе. Сейчас он отдаёт OpenAI один tool `{type:"mcp", server_url:"matscout.prfo.design/mcp/http/"}` и дальше OpenAI сам ходит по сети к нашему MCP-серверу (тот же uvicorn, FastMCP Streamable-HTTP transport). Эффект: claim «MCP server» работает не на словах, а на каждый запрос с сайта.

2. **Polling вместо SSE между браузером и origin** — РКН/провайдерский DPI режет long-lived `text/event-stream` независимо от heartbeat. Заменил на POST request_id + GET poll. Подробнее в `web/app.py` docstring. (OpenAI ↔ наш MCP-сервер живут server-to-server, DPI там не мешает.)

3. **Snapshot replay через `/r/{id}`** — каждый run сохраняется в `research.db`. URL вида `/r/abc123` восстанавливает trace + answer. Browser back/forward в SPA через `pushState` использует тот же механизм.

4. **Conversation mode через `previous_response_id`** — раньше я гонял prior_messages в каждом ходе. С Responses API OpenAI хранит контекст на своей стороне, мы кладём только `last_response_id` в Conversation и передаём его в следующем turn. Snapshot resume — fallback: реконструированные messages идут в `input` items (response_id из старого snapshot уже expired).

5. **`openai_api_key` опциональный** — config.py не валидирует его как required, чтобы MCP stdio entrypoint бутился с одним `MP_API_KEY`. Web слой fail-fast'ит при старте если ключа нет.

6. **VPN compatibility** — на VPS включён MSS clamping iptables + `tcp_mtu_probing=2` чтобы OpenVPN TCP/UDP не таймаутил.

## Известные особенности

- mp-api emits deprecation warning о `nelements` — фактический parameter `num_elements`. Не критично.
- Для phase diagrams MP не возвращает элементарные эндпоинты в multi-element chemsys query — приходится отдельно тянуть `chemsys=el` и мерджить. Решено в `tools/synthesis.py`.
- При построении convex hull визуализации полиморфы одинаковой композиции сливаются в одну точку — на UI дедуплицируем в `_phase_diagram_viz`, в tool result LLM получает все.
- FastMCP DNS-rebinding guard рубит запросы с non-localhost Host header'ом. Поэтому в `mcp_server.py` явно указан `TransportSecuritySettings(allowed_hosts=[…matscout.prfo.design…])`. Если поднимаешь сервер на другом домене — добавь его в allowlist.
- В trace на `tool_call` events args приходят пустыми (`get_material({})`). Это потому что `response.mcp_call_arguments.delta` стрим идёт параллельно с `response.output_item.added`; реальные args появляются в `tool_result` event. Косметика, не баг.
- `previous_response_id` у OpenAI имеет retention window. Если conversation пролежала больше чем этот срок, follow-up через id даст 404 — следует fallback на свежий старт.

## Ритуал

В конце сессии: «закоммить и запушь» — выполняю. «Обнови контекст» — обновлю этот файл под текущее состояние.
