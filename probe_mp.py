"""One-shot probe of the live Materials Project API.

Goal: figure out the *actual* names / shapes / filterable args before we
write tools/search.py against assumptions. Output is human-readable and
also dumped to docs/mp-probe.md for the next step.

Usage:
    uv run python probe_mp.py
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from mp_api.client import MPRester

from matscout.config import get_settings


def dump_available_fields(mpr: MPRester) -> list[str]:
    """All fields a `summary.search(...)` result can carry."""
    fields = list(mpr.materials.summary.available_fields)
    print(f"\n---- available_fields ({len(fields)}) ----")
    for f in sorted(fields):
        print(f"  - {f}")
    return fields


def dump_search_signature(mpr: MPRester) -> str:
    """Which kwargs `search()` itself accepts (the filter surface)."""
    sig = inspect.signature(mpr.materials.summary.search)
    print("\n---- summary.search() signature ----")
    print(f"  {sig}")
    doc = inspect.getdoc(mpr.materials.summary.search) or ""
    print("\n---- summary.search() docstring (truncated) ----")
    print(doc[:2000])
    print("..." if len(doc) > 2000 else "")
    return doc


def probe_sample_search(mpr: MPRester) -> list[dict[str, Any]]:
    """Run one realistic constrained search; show what comes back."""
    print("\n---- sample search: elements=['Si','O'], band_gap (1.0, 2.0) ----")
    fields_to_pull = [
        "material_id",
        "formula_pretty",
        "elements",
        "nelements",
        "symmetry",
        "band_gap",
        "density",
        "energy_above_hull",
        "formation_energy_per_atom",
        "is_stable",
        "is_metal",
        "theoretical",
        "deprecated",
    ]
    docs = mpr.materials.summary.search(
        elements=["Si", "O"],
        band_gap=(1.0, 2.0),
        num_chunks=1,
        chunk_size=5,
        fields=fields_to_pull,
    )
    print(f"  returned {len(docs)} docs")
    rows: list[dict[str, Any]] = []
    for d in docs:
        row = {f: getattr(d, f, None) for f in fields_to_pull}
        # Cast non-JSON-friendly values into something printable
        for k, v in list(row.items()):
            try:
                json.dumps(v)
            except TypeError:
                row[k] = repr(v)
        rows.append(row)
    print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
    return rows


def probe_get_by_id(mpr: MPRester, material_id: str) -> dict[str, Any]:
    """Verify single-id retrieval shape."""
    print(f"\n---- get_by_id({material_id!r}) ----")
    docs = mpr.materials.summary.search(material_ids=[material_id])
    if not docs:
        print("  (no result)")
        return {}
    d = docs[0]
    keys = sorted(d.model_dump().keys()) if hasattr(d, "model_dump") else sorted(vars(d).keys())
    print(f"  fields on returned doc ({len(keys)}):")
    for k in keys:
        print(f"    - {k}")
    return {"id": material_id, "n_fields": len(keys)}


def main() -> int:
    s = get_settings()
    out_md = Path("docs/mp-probe.md")
    out_md.parent.mkdir(exist_ok=True)

    print("matscout MP API probe")
    print(f"  key: {s.mp_api_key[:6]}...  ({len(s.mp_api_key)} chars)")

    with MPRester(s.mp_api_key) as mpr:
        fields = dump_available_fields(mpr)
        doc = dump_search_signature(mpr)
        sample = probe_sample_search(mpr)
        first_id = sample[0]["material_id"] if sample else None
        if first_id:
            probe_get_by_id(mpr, first_id)

    # Persist a markdown summary for the next implementation step
    out_md.write_text(
        "# Materials Project: live probe\n\n"
        "`probe_mp.py` writes this file.\n\n"
        f"## summary.search(): all available output fields ({len(fields)})\n\n"
        + "\n".join(f"- `{f}`" for f in sorted(fields))
        + "\n\n## summary.search() docstring (first 2000 chars)\n\n```\n"
        + doc[:2000]
        + ("\n...\n```\n" if len(doc) > 2000 else "\n```\n")
        + "\n## Sample query: `elements=['Si','O'], band_gap=(1.0,2.0)`\n\n```json\n"
        + json.dumps(sample, indent=2, ensure_ascii=False, default=str)
        + "\n```\n",
        encoding="utf-8",
    )
    print(f"\nOK wrote summary to {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
