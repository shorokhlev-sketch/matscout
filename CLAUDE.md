# matscout — внутренние заметки

MCP server + OpenAI агент над Materials Project. Сделан для вакансии AI-operator чтобы показать MCP + function-calling + typed tools на реальной академической задаче.

## Источник истины

GitHub `shorokhlev-sketch/matscout` (приватный). Перед сессией `git pull`, после правок — `git add . && git commit && git push`.

## Архитектура

Один набор тулов выставлен ДВУМЯ поверхностями:

```
matscout/tools/{search, get, compare, stability, synthesis, literature, structure}.py
    ↓
matscout/tool_facades.py  # flat-kwarg wrappers, ALL_TOOLS=[…]
    ↓
matscout/agent/runner.py  # OpenAI function-calling loop (stream_agent)
matscout/mcp_server.py    # FastMCP wrapper, тот же ALL_TOOLS
    ↓
web/app.py                # FastAPI playground, polling-based
```

**Тулы (12):** search_materials, get_material, compare_materials, check_stability, get_phase_diagram, predict_decomposition, get_competing_phases, get_structure, find_papers, get_papers_about, get_doi_metadata, find_preprints.

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

1. **Polling вместо SSE** — РКН/провайдерский DPI режет long-lived `text/event-stream` независимо от heartbeat. Заменил на POST request_id + GET poll. Подробнее в `web/app.py` docstring.

2. **Snapshot replay через `/r/{id}`** — каждый run сохраняется в `research.db`. URL вида `/r/abc123` восстанавливает trace + answer. Browser back/forward в SPA через `pushState` использует тот же механизм.

3. **Conversation mode** — runner принимает `prior_messages`, web хранит `Conversation` registry с TTL 1ч. Follow-up отвечает с памятью о предыдущих turns.

4. **VPN compatibility** — на VPS включён MSS clamping iptables + `tcp_mtu_probing=2` чтобы OpenVPN TCP/UDP не таймаутил.

## Известные особенности

- mp-api emits deprecation warning о `nelements` — фактический parameter `num_elements`. Не критично.
- Для phase diagrams MP не возвращает элементарные эндпоинты в multi-element chemsys query — приходится отдельно тянуть `chemsys=el` и мерджить. Решено в `tools/synthesis.py`.
- При построении convex hull визуализации полиморфы одинаковой композиции сливаются в одну точку — на UI дедуплицируем в `_phase_diagram_viz`, в tool result LLM получает все.

## Ритуал

В конце сессии: «закоммить и запушь» — выполняю. «Обнови контекст» — обновлю этот файл под текущее состояние.
