"""pareto_rank — multi-criteria material ranking.

Given a shortlist of mp-ids and a list of criteria, return a Pareto-
sorted ranking. Each criterion specifies a property, a target direction
(maximise / minimise / target-value), and a weight. The agent uses this
to turn "I have 20 candidates and 4 properties" into a defensible
ordering it can present to the user.
"""

from __future__ import annotations

from typing import Any, Literal

from matscout.models import Candidate
from matscout.tools.get import get_material


def pareto_rank(
    material_ids: list[str],
    criteria: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """Rank a candidate set by multiple weighted criteria.

    Args:
        material_ids: mp-ids to rank.
        criteria: list of dicts, each shaped
            ``{"property": str, "direction": "max"|"min"|"near",
               "target": float | None, "weight": float}``.
            Properties must be top-level Candidate fields: ``band_gap``,
            ``density``, ``energy_above_hull``, ``formation_energy_per_atom``.
        limit: how many of the best candidates to return.

    Returns:
        ``{
            "criteria": [...],
            "ranking": [{"material_id", "formula", "score",
                         "is_pareto", "property_values": {...}}, ...],
            "summary": str
        }``

        ``ranking`` is sorted by descending weighted score; ``is_pareto``
        flags points on the Pareto frontier (no other candidate dominates
        them across all criteria).
    """
    if not material_ids:
        return {"criteria": criteria, "ranking": [], "summary": "No material_ids provided."}
    if not criteria:
        raise ValueError("pareto_rank needs at least one criterion")

    # Validate criteria up front so a downstream error doesn't leak
    # confusing stack traces back to the agent.
    valid_props = {"band_gap", "density", "energy_above_hull", "formation_energy_per_atom"}
    valid_dirs = {"max", "min", "near"}
    for c in criteria:
        prop = c.get("property")
        direction = c.get("direction")
        if prop not in valid_props:
            raise ValueError(f"criterion property must be one of {valid_props}, got {prop!r}")
        if direction not in valid_dirs:
            raise ValueError(f"criterion direction must be one of {valid_dirs}, got {direction!r}")
        if direction == "near" and "target" not in c:
            raise ValueError(f"criterion direction='near' requires a 'target' value: {c}")

    # Fetch all candidate property sheets — cached, so this is cheap on
    # repeat runs of the same shortlist.
    rows: list[tuple[Candidate, dict[str, float]]] = []
    for mid in material_ids:
        try:
            sheet = get_material(mid).model_dump(mode="json")
        except Exception:
            continue
        cand = Candidate.model_validate(sheet)
        raw_props: dict[str, Any] = {p: getattr(cand, p, None) for p in valid_props}
        # Drop rows missing data on any required criterion.
        if any(raw_props[c["property"]] is None for c in criteria):
            continue
        props: dict[str, float] = {
            k: float(v) for k, v in raw_props.items() if v is not None
        }
        rows.append((cand, props))

    if not rows:
        return {
            "criteria": criteria,
            "ranking": [],
            "summary": "None of the candidates had data on the requested criteria.",
        }

    # Per-criterion normalised score in [0, 1], 1 = best.
    def score_one(value: float, c: dict[str, Any]) -> float:
        prop = c["property"]
        direction: Literal["max", "min", "near"] = c["direction"]
        vals = [r[1][prop] for r in rows]
        vmin, vmax = min(vals), max(vals)
        rng = vmax - vmin if vmax > vmin else 1.0
        if direction == "max":
            return (value - vmin) / rng
        if direction == "min":
            return (vmax - value) / rng
        # near: 1 at target, falls off linearly with relative distance
        target = float(c["target"])
        dist = abs(value - target) / rng
        return max(0.0, 1.0 - dist)

    scored: list[dict[str, Any]] = []
    for cand, props in rows:
        per_crit: list[tuple[float, float]] = []  # (score, weight)
        for c in criteria:
            val = props.get(c["property"])
            assert val is not None  # filtered out above; appease mypy
            s = score_one(float(val), c)
            per_crit.append((s, float(c.get("weight", 1.0))))
        total_w = sum(w for _, w in per_crit) or 1.0
        weighted = sum(s * w for s, w in per_crit) / total_w
        scored.append(
            {
                "material_id": cand.material_id,
                "formula": cand.formula_pretty,
                "score": round(weighted, 4),
                "per_criterion_scores": {
                    c["property"]: round(s, 4) for c, (s, _) in zip(criteria, per_crit, strict=True)
                },
                "property_values": {p: round(float(v), 4) for p, v in props.items()},
            }
        )

    # Pareto frontier: a row is on the frontier iff no other row is
    # weakly better on every criterion AND strictly better on at least
    # one. We compare per-criterion scores (already direction-aware).
    def dominates(a: dict[str, float], b: dict[str, float]) -> bool:
        weakly_better = all(a[k] >= b[k] for k in a)
        strictly_better = any(a[k] > b[k] for k in a)
        return weakly_better and strictly_better

    for row in scored:
        crit_scores = row["per_criterion_scores"]
        row["is_pareto"] = not any(
            dominates(other["per_criterion_scores"], crit_scores)
            for other in scored
            if other is not row
        )

    scored.sort(key=lambda r: r["score"], reverse=True)
    top = scored[:limit]
    n_pareto = sum(1 for r in top if r["is_pareto"])
    summary = (
        f"Ranked {len(scored)} candidates by {len(criteria)} criteria; "
        f"top {len(top)} returned, {n_pareto} on the Pareto frontier."
    )
    return {"criteria": criteria, "ranking": top, "summary": summary}
