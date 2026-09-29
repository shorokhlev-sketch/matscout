"""`python -m matscout` - health check that env + deps are wired."""

from __future__ import annotations

import sys

from matscout.config import get_settings


def main() -> int:
    try:
        s = get_settings()
    except Exception as e:
        print(f"FAIL config: {e}", file=sys.stderr)
        return 1

    print("matscout health check")
    print(
        f"  MP key:            {'set (' + s.mp_api_key[:6] + '...)' if s.mp_api_key else 'MISSING'}"
    )
    print(f"  OpenAI key:        {'set' if s.openai_api_key else 'MISSING'}")
    print(f"  Log format:        {s.log_format}")
    print(f"  Cache TTL (days):  {s.cache_ttl_days}")
    print(f"  Cache db:          {s.cache_db_path}")
    print(f"  Project root:      {s.project_root}")

    try:
        import fastapi  # noqa: F401
        import mcp  # noqa: F401
        import mp_api  # noqa: F401
        import openai  # noqa: F401

        print("  Deps:              mp-api OK, mcp OK, openai OK, fastapi OK")
    except ImportError as e:
        print(f"  Deps:              FAIL {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
