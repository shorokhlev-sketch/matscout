"""check_stability - verdict + human explanation for a single material."""

from __future__ import annotations

from matscout.models import StabilityReport, Verdict
from matscout.tools.get import get_material


def check_stability(material_id: str) -> StabilityReport:
    """Return a stability verdict + explanation for ``material_id``.

    Verdict is derived from ``energy_above_hull`` using thresholds in
    :class:`matscout.models.Verdict`. The explanation is written for a
    materials-engineer reader, not for the LLM (it sits next to the verdict
    in UIs / agent answers).
    """
    m = get_material(material_id)
    e = m.energy_above_hull
    verdict = Verdict.from_e_above_hull(e)
    return StabilityReport(
        material_id=m.material_id,
        formula_pretty=m.formula_pretty,
        energy_above_hull=e,
        formation_energy_per_atom=m.formation_energy_per_atom,
        is_stable=m.is_stable,
        verdict=verdict,
        explanation=verdict.explain(e),
    )
