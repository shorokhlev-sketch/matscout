"""Shared fixtures + live-marker skip logic."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip @pytest.mark.live tests when credentials are not in the env.

    This keeps unit-test runs in CI (no secrets) green while still letting
    `pytest -m live` exercise the real MP / OpenAI APIs locally.
    """
    if os.environ.get("MP_API_KEY") and os.environ.get("OPENAI_API_KEY"):
        return
    skip_live = pytest.mark.skip(reason="MP_API_KEY / OPENAI_API_KEY not in env")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
