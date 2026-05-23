"""Command-line entry: ``matscout "natural language query"``.

Prints the agent's intermediate tool calls to stderr (so you can see
what's happening) and the final markdown answer to stdout (so it pipes
cleanly into less / pbcopy / wherever).
"""

from __future__ import annotations

import argparse
import json
import sys

from matscout.agent.runner import stream_agent


def main() -> int:
    p = argparse.ArgumentParser(prog="matscout", description="NL queries → Materials Project")
    p.add_argument("query", help="Plain-English description of the material you want.")
    p.add_argument("--model", default="gpt-4o", help="OpenAI model id (default: gpt-4o)")
    p.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress the tool-call trace on stderr; only print the final answer.",
    )
    args = p.parse_args()

    for ev in stream_agent(args.query, model=args.model):
        if ev.kind == "tool_call":
            if not args.quiet:
                preview = json.dumps(ev.args, ensure_ascii=False)[:160]
                print(f"  → {ev.name}({preview})", file=sys.stderr, flush=True)
        elif ev.kind == "tool_result":
            if not args.quiet:
                if isinstance(ev.result, list):
                    print(f"    ← {len(ev.result)} item(s)", file=sys.stderr, flush=True)
                else:
                    print("    ← (result)", file=sys.stderr, flush=True)
        elif ev.kind == "tool_error":
            print(f"    ✗ error from {ev.name}: {ev.error}", file=sys.stderr, flush=True)
        elif ev.kind == "final":
            print(ev.content or "")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
