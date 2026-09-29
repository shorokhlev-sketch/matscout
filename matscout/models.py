"""Typed I/O models for matscout.

Every tool input/output crosses Pydantic - this is what MCP clients and the
OpenAI agent see (auto-generated JSON schemas), and what the eval suite
asserts against. Keeping these strict and small is the whole point of
"narrow surface".
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Materials Project considers a phase "stable" when it lies on the convex
# hull; "metastable" up to ~25 meV/atom above it (kT at room temperature
# heuristic). Beyond that, synthesis is considered increasingly unlikely.
STABLE_THRESHOLD_EV_ATOM = 0.0
METASTABLE_THRESHOLD_EV_ATOM = 0.025


class Verdict(str, Enum):
    """Human-readable stability verdict derived from energy_above_hull."""

    STABLE = "stable"
    METASTABLE = "metastable"
    UNSTABLE = "unstable"

    @classmethod
    def from_e_above_hull(cls, e: float | None) -> Verdict:
        if e is None:
            return cls.UNSTABLE
        if e <= STABLE_THRESHOLD_EV_ATOM + 1e-9:
            return cls.STABLE
        if e <= METASTABLE_THRESHOLD_EV_ATOM:
            return cls.METASTABLE
        return cls.UNSTABLE

    def explain(self, e: float | None) -> str:
        if e is None:
            return "Stability data unavailable; treat as unstable."
        if self is Verdict.STABLE:
            return f"On convex hull (E above hull = {e * 1000:.0f} meV/atom). Synthesizable phase."
        if self is Verdict.METASTABLE:
            return (
                f"Metastable, {e * 1000:.0f} meV/atom above hull. Likely synthesizable "
                "but may decompose under prolonged equilibrium."
            )
        return (
            f"{e * 1000:.0f} meV/atom above hull; likely not synthesizable as a "
            "single phase; consider as a guideline only."
        )


class CrystalSystem(str, Enum):
    """Bravais crystal-family enum mirroring pymatgen's, but as plain str."""

    TRICLINIC = "Triclinic"
    MONOCLINIC = "Monoclinic"
    ORTHORHOMBIC = "Orthorhombic"
    TETRAGONAL = "Tetragonal"
    TRIGONAL = "Trigonal"
    HEXAGONAL = "Hexagonal"
    CUBIC = "Cubic"
    UNKNOWN = "Unknown"


class Symmetry(BaseModel):
    """Normalized symmetry block - strips the pymatgen-specific wrapping."""

    model_config = ConfigDict(extra="forbid")

    crystal_system: CrystalSystem = CrystalSystem.UNKNOWN
    spacegroup_symbol: str | None = None
    spacegroup_number: int | None = Field(default=None, ge=1, le=230)
    point_group: str | None = None


# ---- Tool I/O models ----
class SearchFilters(BaseModel):
    """Input shape of search_materials. Validated at the tool boundary."""

    model_config = ConfigDict(extra="forbid")

    elements: list[str] | None = Field(
        default=None, description="Required elements, e.g. ['Si', 'O']."
    )
    exclude_elements: list[str] | None = Field(
        default=None, description="Elements that must NOT be present."
    )
    formula: str | None = Field(
        default=None,
        description="Pretty formula ('Fe2O3') or anonymous template ('ABO3', 'Si*').",
    )
    band_gap_range: tuple[float, float] | None = Field(
        default=None, description="(min, max) band gap in eV."
    )
    density_range: tuple[float, float] | None = Field(
        default=None, description="(min, max) density in g/cm^3."
    )
    max_energy_above_hull: float | None = Field(
        default=None,
        ge=0.0,
        description="Upper bound on E above hull in eV/atom. Use 0.025 for metastable cutoff.",
    )
    only_stable: bool = Field(
        default=False,
        description="Equivalent to is_stable=True at the MP layer (on the convex hull).",
    )
    num_elements: int | tuple[int, int] | None = Field(
        default=None, description="Exact element count or (min, max) range."
    )
    is_metal: bool | None = Field(
        default=None, description="If set, restrict to metals (True) / non-metals (False)."
    )
    is_gap_direct: bool | None = Field(
        default=None, description="If set and True, only direct band gaps."
    )
    limit: int = Field(default=50, ge=1, le=500, description="Max candidates to return.")

    @field_validator("elements", "exclude_elements")
    @classmethod
    def _strip_and_normalize(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [e.strip().title() for e in v if e.strip()]

    @field_validator("band_gap_range", "density_range")
    @classmethod
    def _range_must_be_ordered(cls, v: tuple[float, float] | None) -> tuple[float, float] | None:
        if v is None:
            return None
        lo, hi = v
        if lo > hi:
            raise ValueError(f"range lower bound ({lo}) > upper bound ({hi})")
        return v


class Candidate(BaseModel):
    """Compact row returned in search results. Keep small - these come in lists."""

    model_config = ConfigDict(extra="forbid")

    material_id: str = Field(..., description="Materials Project id, e.g. 'mp-149'.")
    formula_pretty: str
    elements: list[str]
    nelements: int = Field(..., ge=1)
    band_gap: float | None = Field(default=None, description="eV. None for metals (sometimes).")
    density: float | None = Field(default=None, description="g/cm^3")
    energy_above_hull: float | None = Field(default=None, description="eV/atom")
    formation_energy_per_atom: float | None = Field(default=None, description="eV/atom")
    is_stable: bool | None = None
    is_metal: bool | None = None
    symmetry: Symmetry | None = None

    @property
    def verdict(self) -> Verdict:
        return Verdict.from_e_above_hull(self.energy_above_hull)


class Material(BaseModel):
    """Full per-material sheet - superset of Candidate. Returned by get_material."""

    model_config = ConfigDict(extra="forbid")

    material_id: str
    formula_pretty: str
    formula_anonymous: str | None = None
    chemsys: str | None = None
    elements: list[str]
    nelements: int = Field(..., ge=1)
    nsites: int | None = None
    volume: float | None = None
    density: float | None = None
    density_atomic: float | None = None

    symmetry: Symmetry | None = None

    band_gap: float | None = None
    is_gap_direct: bool | None = None
    is_metal: bool | None = None
    is_magnetic: bool | None = None

    energy_above_hull: float | None = None
    formation_energy_per_atom: float | None = None
    uncorrected_energy_per_atom: float | None = None
    is_stable: bool | None = None

    # Mechanical / optical / magnetic - populated when MP has the data.
    bulk_modulus: dict[str, float] | None = None  # {voigt, reuss, vrh}
    shear_modulus: dict[str, float] | None = None
    refractive_index: float | None = Field(default=None, description="n at zero frequency.")
    total_magnetization: float | None = None

    theoretical: bool = False
    deprecated: bool = False

    @property
    def verdict(self) -> Verdict:
        return Verdict.from_e_above_hull(self.energy_above_hull)


class ComparisonRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    formula_pretty: str
    values: dict[str, Any] = Field(
        default_factory=dict,
        description="Property name → value (already coerced to JSON-safe primitive).",
    )


class ComparisonTable(BaseModel):
    """Bag returned by compare_materials - ready to render in a UI <table>."""

    model_config = ConfigDict(extra="forbid")

    properties: list[str]
    rows: list[ComparisonRow]


class StabilityReport(BaseModel):
    """Return shape of check_stability."""

    model_config = ConfigDict(extra="forbid")

    material_id: str
    formula_pretty: str | None = None
    energy_above_hull: float | None = None
    formation_energy_per_atom: float | None = None
    is_stable: bool | None = None
    verdict: Verdict
    explanation: str


# ---- Literature / paper-search models ----
class Paper(BaseModel):
    """Compact bibliographic record. Designed to fit both Semantic Scholar
    and arXiv-derived rows behind the same shape."""

    model_config = ConfigDict(extra="forbid")

    paper_id: str = Field(
        ..., description="Source-prefixed id: 's2:abc123', 'arxiv:2401.12345', 'doi:10.x/y'."
    )
    source: Literal["semantic-scholar", "arxiv", "crossref"]
    title: str
    authors: list[str] = Field(default_factory=list, description="Short author list, name strings.")
    year: int | None = None
    venue: str | None = Field(default=None, description="Journal or preprint server.")
    doi: str | None = None
    url: str | None = None
    abstract: str | None = None
    citation_count: int | None = None
    open_access_url: str | None = Field(
        default=None,
        description="Direct PDF link when known (arXiv preprint, OA copy, etc.).",
    )


class PaperList(BaseModel):
    """Return shape of literature-search tools."""

    model_config = ConfigDict(extra="forbid")

    query: str
    source: Literal["semantic-scholar", "arxiv", "crossref"]
    count: int
    papers: list[Paper]
