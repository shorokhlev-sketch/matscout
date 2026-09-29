"""Standalone eval runner - also exposed via the pytest tests below.

Usage:
    uv run python -m tests.eval.runner            # human-readable
    uv run python -m tests.eval.runner --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from matscout.agent.runner import AgentResult, run_agent

QUERIES_FILE = Path(__file__).parent / "queries.yaml"


@dataclass
class AssertionResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    name: str
    query: str
    answer: str
    tools_used: list[str]
    turns: int
    elapsed_seconds: float
    assertions: list[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(a.passed for a in self.assertions)


def _check_assertions(
    case: dict[str, Any], result: AgentResult, elapsed: float
) -> list[AssertionResult]:
    """Run every assertion declared on a case, return per-assertion outcomes."""
    a = case.get("assertions", {})
    out: list[AssertionResult] = []
    answer = result.answer or ""
    tools_used = [ev.name for ev in result.trace if ev.kind == "tool_call" and ev.name]

    if "tools_used_at_least_one_of" in a:
        wanted = set(a["tools_used_at_least_one_of"])
        ok = bool(wanted & set(tools_used))
        out.append(
            AssertionResult(
                "tools_used_at_least_one_of",
                ok,
                f"wanted any of {sorted(wanted)}, got {tools_used}",
            )
        )

    if "tools_used_all_of" in a:
        wanted = set(a["tools_used_all_of"])
        missing = wanted - set(tools_used)
        out.append(
            AssertionResult(
                "tools_used_all_of",
                not missing,
                f"missing: {sorted(missing)}" if missing else "ok",
            )
        )

    if "answer_contains_any" in a:
        needles = a["answer_contains_any"]
        hit = next((n for n in needles if n.lower() in answer.lower()), None)
        out.append(
            AssertionResult(
                "answer_contains_any",
                hit is not None,
                f"matched '{hit}'" if hit else f"none of {needles} found",
            )
        )

    if "answer_contains_all" in a:
        needles = a["answer_contains_all"]
        missing_text = [n for n in needles if n.lower() not in answer.lower()]
        out.append(
            AssertionResult(
                "answer_contains_all",
                not missing_text,
                "ok" if not missing_text else f"missing: {missing_text}",
            )
        )

    if "answer_min_chars" in a:
        min_len = int(a["answer_min_chars"])
        out.append(
            AssertionResult(
                "answer_min_chars",
                len(answer) >= min_len,
                f"len={len(answer)}, min={min_len}",
            )
        )

    if "max_turns" in a:
        cap = int(a["max_turns"])
        out.append(
            AssertionResult(
                "max_turns",
                result.turns <= cap,
                f"turns={result.turns}, cap={cap}",
            )
        )

    return out


def run_case(case: dict[str, Any]) -> CaseResult:
    query = case["query"].strip()
    start = time.monotonic()
    # The tool-call cap is checked as an assertion on the result (see max_turns above).
    result = run_agent(query)
    elapsed = time.monotonic() - start

    return CaseResult(
        name=case["name"],
        query=query,
        answer=result.answer,
        tools_used=[ev.name for ev in result.trace if ev.kind == "tool_call" and ev.name],
        turns=result.turns,
        elapsed_seconds=elapsed,
        assertions=_check_assertions(case, result, elapsed),
    )


def load_cases(path: Path = QUERIES_FILE) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected list of cases, got {type(data).__name__}")
    return data


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--filter", help="run only cases whose name contains this substring")
    args = p.parse_args()

    cases = load_cases()
    if args.filter:
        cases = [c for c in cases if args.filter in c["name"]]

    results: list[CaseResult] = []
    for c in cases:
        if not args.json:
            print(f"\ncase {c['name']}", flush=True)
        r = run_case(c)
        results.append(r)
        if not args.json:
            mark = "PASS" if r.passed else "FAIL"
            print(
                f"  {mark} {r.turns} turn(s), {r.elapsed_seconds:.1f}s, tools: {','.join(r.tools_used)}"
            )
            for a in r.assertions:
                inner = "PASS" if a.passed else "FAIL"
                print(f"    {inner} {a.name}: {a.detail}")

    passed = sum(1 for r in results if r.passed)
    total = len(results)

    if args.json:
        out = {
            "passed": passed,
            "total": total,
            "cases": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "turns": r.turns,
                    "elapsed_seconds": round(r.elapsed_seconds, 2),
                    "tools_used": r.tools_used,
                    "answer": r.answer,
                    "assertions": [
                        {"name": a.name, "passed": a.passed, "detail": a.detail}
                        for a in r.assertions
                    ],
                }
                for r in results
            ],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"\n{passed}/{total} cases passed.")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
