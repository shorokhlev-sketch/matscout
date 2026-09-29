# matscout: multi-stage build with uv for deterministic deps.
# Final image runs the FastAPI playground on :8000.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# uv comes from its official image, which saves a shell-script install.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

# ---- deps layer (cached while pyproject.toml and uv.lock stay the same) ----
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra dev

# ---- source ----
COPY matscout/  ./matscout/
COPY web/       ./web/
COPY README.md  ./

# Install the project itself (entry-point scripts).
RUN uv sync --frozen --extra dev

# ---- runtime ----
EXPOSE 8000
ENV PATH="/app/.venv/bin:$PATH"

# Default: serve the playground. Override CMD to run the MCP server or CLI.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()" \
      || exit 1

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
