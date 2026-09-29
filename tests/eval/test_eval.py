"""Pytest wrapper - each YAML case becomes its own test, auto-skipped without keys."""

from __future__ import annotations

from typing import Any

import pytest

from tests.eval.runner import load_cases, run_case

pytestmark = pytest.mark.live


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["name"])
def test_eval_case(case: dict[str, Any]) -> None:
    r = run_case(case)
    failed = [a for a in r.assertions if not a.passed]
    detail = "\n  ".join(f"{a.name}: {a.detail}" for a in failed) if failed else ""
    assert not failed, (
        f"eval '{r.name}' failed:\n  {detail}\n"
        f"  turns: {r.turns}, tools: {r.tools_used}\n"
        f"  answer (first 400 chars): {r.answer[:400]!r}"
    )
