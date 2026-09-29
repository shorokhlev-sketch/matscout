"""Structure export - CIF / POSCAR / XYZ for downstream DFT work.

These tools take an mp-id, fetch the canonical structure from MP, and
return it as a text blob in whatever format the user's calculator
wants (VASP → POSCAR, Quantum ESPRESSO / GPAW → CIF, scratch
visualizations → XYZ).

pymatgen does all the heavy lifting. We just need to ask MPRester for
the structure object and call the right writer.
"""

from __future__ import annotations

from typing import Any, Literal

from matscout.tools._client import get_cache, get_client

SupportedFormat = Literal["cif", "poscar", "xyz"]
_FORMATS: tuple[SupportedFormat, ...] = ("cif", "poscar", "xyz")


def _fetch_structure(material_id: str) -> Any:
    """Pull the pymatgen Structure for an mp-id (with our own cache)."""
    client = get_client()
    # MPRester has materials.summary.search; the canonical structure for an
    # id comes from the 'structure' field. We request only that field to
    # keep the response small.
    docs = client.materials.summary.search(
        material_ids=[material_id],
        fields=["material_id", "structure", "formula_pretty"],
    )
    if not docs:
        raise ValueError(f"no structure on file for material_id={material_id!r}")
    return docs[0]


def get_structure(
    material_id: str,
    *,
    fmt: SupportedFormat = "cif",
) -> dict[str, Any]:
    """Return the canonical structure of ``material_id`` as a text blob.

    Args:
        material_id: MP id (e.g. ``"mp-149"``).
        fmt: one of ``"cif"`` (default - Quantum ESPRESSO / GPAW / OVITO),
             ``"poscar"`` (VASP), ``"xyz"`` (visualization, loses periodicity).

    Returns:
        ``{
            "material_id": "mp-149",
            "formula_pretty": "Si",
            "format": "cif",
            "filename_suggestion": "mp-149-Si.cif",
            "content": "<file contents>",
            "n_sites": 2,
            "lattice": {"a": ..., "b": ..., "c": ..., "alpha": ..., ...},
            "spacegroup": {"symbol": ..., "number": ...}
        }``
    """
    if fmt not in _FORMATS:
        raise ValueError(f"fmt must be one of {_FORMATS}, got {fmt!r}")

    cache_args = {"material_id": material_id, "fmt": fmt}
    cache = get_cache()
    cached = cache.get("get_structure", cache_args)
    if cached is not None:
        return cached

    doc = _fetch_structure(material_id)
    structure = getattr(doc, "structure", None)
    if structure is None:
        raise ValueError(f"MP returned no structure object for {material_id!r}")

    # pymatgen.Structure has .to() which switches on format.
    # POSCAR is what VASP calls a CONTCAR-style POSCAR file; pymatgen's
    # `to("poscar")` writes that. CIF and XYZ ditto.
    content = structure.to(fmt=fmt)

    # Convenience metadata so the LLM can tell the user what they got.
    lattice = structure.lattice
    spg = structure.get_space_group_info()  # returns (symbol, number)
    formula = str(getattr(doc, "formula_pretty", "") or structure.composition.reduced_formula)

    result = {
        "material_id": material_id,
        "formula_pretty": formula,
        "format": fmt,
        "filename_suggestion": f"{material_id}-{formula}.{fmt}",
        "content": content,
        "n_sites": len(structure),
        "lattice": {
            "a": float(lattice.a),
            "b": float(lattice.b),
            "c": float(lattice.c),
            "alpha": float(lattice.alpha),
            "beta": float(lattice.beta),
            "gamma": float(lattice.gamma),
            "volume": float(lattice.volume),
        },
        "spacegroup": {"symbol": spg[0], "number": int(spg[1])},
    }
    cache.put("get_structure", cache_args, result)
    return result
