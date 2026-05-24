"""Typed runtime configuration — pulled from environment (.env) on import."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime config in one typed place. Pulled from env / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="",
    )

    mp_api_key: str = Field(..., alias="MP_API_KEY")
    # OpenAI is only used by the web playground's gpt-4o agent. The MCP server
    # itself reasons via the connected client (Claude Desktop / Code), so it
    # must boot and run tools without an OpenAI key. We make the field
    # optional here; the web layer asserts presence at startup explicitly.
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    log_format: Literal["dev", "json"] = Field(default="dev", alias="MATSCOUT_LOG_FORMAT")
    cache_ttl_days: int = Field(default=30, alias="MATSCOUT_CACHE_TTL_DAYS", ge=1, le=365)

    # Paths — derived, not from env
    project_root: Path = Path(__file__).resolve().parents[1]

    @property
    def cache_dir(self) -> Path:
        d = self.project_root / "cache"
        d.mkdir(exist_ok=True)
        return d

    @property
    def cache_db_path(self) -> Path:
        return self.cache_dir / "matscout.db"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Lazy singleton — avoids import-time failure when env not yet set."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
